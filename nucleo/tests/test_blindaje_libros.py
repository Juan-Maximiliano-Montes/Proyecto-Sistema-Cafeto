"""DB-TRG-01: el TRUNCATE de los libros, la cuenta corriente y la vista.

Se prueba la base directamente, sin pasar por los modelos: es el caso que
la regla de la aplicación no cubre (alguien con ``psql``).
"""
import importlib
from decimal import Decimal as D

from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from fidelizacion import credito
from fidelizacion.models import MovimientoCuenta
from fidelizacion.tests.test_credito import Base as BaseCredito
from nucleo.tests import fabrica as f

LIBROS = importlib.import_module('nucleo.migrations.0003_libros_inalterables').LIBROS


def ejecutar(sentencia, parametros=(), antes=()):
    """Corre ``sentencia`` en su propia transacción (un savepoint)."""
    with transaction.atomic(), connection.cursor() as cursor:
        for previa in antes:
            cursor.execute(previa)
        cursor.execute(sentencia, list(parametros))
        return cursor.rowcount


class TruncateDeLosLibrosTest(TestCase):

    def test_ningun_libro_vivo_se_vacia(self):
        for tabla in (*LIBROS, 'movimientos_cuenta'):
            with self.subTest(tabla=tabla):
                with self.assertRaisesMessage(DatabaseError, 'no se vacía con TRUNCATE'):
                    ejecutar(f'TRUNCATE {tabla} CASCADE')

    def test_tampoco_en_cascada_desde_un_padre(self):
        # ``ventas`` no es un libro, pero sus renglones sí: el CASCADE los alcanza.
        with self.assertRaisesMessage(DatabaseError, 'no se vacía con TRUNCATE'):
            ejecutar('TRUNCATE ventas CASCADE')

    def test_todos_los_libros_tienen_su_trigger(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE NOT t.tgisinternal AND t.tgname LIKE 'tr\\_%\\_sin\\_vaciar' "
                "AND c.relname NOT LIKE 'hist\\_%'")
            con_trigger = {fila[0] for fila in cursor.fetchall()}
        self.assertEqual(con_trigger, {*LIBROS, 'movimientos_cuenta'})

    def test_la_depuracion_explicita_si_puede(self):
        # La misma llave que el archivo histórico (DD-99): sólo una
        # depuración deliberada, y sólo durante su transacción.
        ejecutar('TRUNCATE intentos_acceso',
                 antes=["SET LOCAL cafeto.depuracion_historica = 'on'"])
        # SET LOCAL dura hasta el fin de la transacción (acá, la del test):
        # se apaga a mano, como hace pasar_a_historico.
        ejecutar("SET LOCAL cafeto.depuracion_historica = 'off'")
        with self.assertRaises(DatabaseError):
            ejecutar('TRUNCATE intentos_acceso')


class CuentaCorrienteInalterableTest(BaseCredito):
    """La cuenta corriente: de un fiado sólo cambian su saldo y la marca de
    puntos; nada se borra salvo en el pase a histórico."""

    def setUp(self):
        super().setUp()
        self.habilitar()
        self.venta = self.fiar()
        credito.cobrar(self.elsa, D('500'), f.efectivo(), self.cajera, turno=self.turno)
        self.fiado = MovimientoCuenta.objects.get(tipo_movimiento='FIADO')
        self.cobro = MovimientoCuenta.objects.get(tipo_movimiento='COBRO')

    def test_el_fiado_achica_su_saldo_y_marca_los_puntos(self):
        # Es exactamente lo que hace el servicio de crédito: sigue andando.
        self.fiado.refresh_from_db()
        self.assertEqual(self.fiado.saldo_pendiente, D('1500'))
        ejecutar('UPDATE movimientos_cuenta SET saldo_pendiente = 0, puntos_acreditados = true '
                 'WHERE id_movimiento_cuenta = %s', [self.fiado.pk])
        self.fiado.refresh_from_db()
        self.assertEqual((self.fiado.saldo_pendiente, self.fiado.puntos_acreditados), (0, True))

    def test_del_fiado_no_cambia_nada_mas(self):
        for columna, valor in (('monto', '9999'), ('motivo', "'x'"), ('fecha_vencimiento', "'2999-01-01'"),
                               ('id_cliente', '0'), ('tipo_movimiento', "'COBRO'")):
            with self.subTest(columna=columna), self.assertRaisesMessage(
                    DatabaseError, 'sólo cambian el saldo pendiente y los puntos acreditados'):
                ejecutar(f'UPDATE movimientos_cuenta SET {columna} = {valor} '
                         'WHERE id_movimiento_cuenta = %s', [self.fiado.pk])

    def test_un_movimiento_que_no_es_fiado_no_se_toca(self):
        for columna, valor in (('motivo', "'otro'"), ('monto', '1')):
            with self.subTest(columna=columna), self.assertRaisesMessage(DatabaseError, 'libro de solo agregado'):
                ejecutar(f'UPDATE movimientos_cuenta SET {columna} = {valor} '
                         'WHERE id_movimiento_cuenta = %s', [self.cobro.pk])

    def test_no_se_borra_por_sql(self):
        for movimiento in (self.cobro, self.fiado):
            with self.subTest(tipo=movimiento.tipo_movimiento), self.assertRaisesMessage(
                    DatabaseError, 'no se borra, se contra-asienta'):
                ejecutar('DELETE FROM movimientos_cuenta WHERE id_movimiento_cuenta = %s', [movimiento.pk])
        self.assertEqual(MovimientoCuenta.objects.count(), 2)

    def test_el_pase_a_historico_si_borra_con_la_marca_en_on_o_1(self):
        self.assertEqual(
            ejecutar('DELETE FROM movimientos_cuenta WHERE id_movimiento_cuenta = %s', [self.cobro.pk],
                     antes=["SET LOCAL cafeto.pasaje_historico = 'on'"]), 1)
        # Con '1' también (el cobro ya se fue: se borra el fiado).
        self.assertEqual(
            ejecutar('DELETE FROM movimientos_cuenta WHERE id_movimiento_cuenta = %s', [self.fiado.pk],
                     antes=["SET LOCAL cafeto.pasaje_historico = '1'"]), 1)
        self.assertFalse(MovimientoCuenta.objects.exists())

    def test_cualquier_otro_valor_de_la_marca_no_habilita(self):
        for marca in ('off', '0', 'true', ''):
            with self.subTest(marca=marca), self.assertRaises(DatabaseError):
                ejecutar('DELETE FROM movimientos_cuenta WHERE id_movimiento_cuenta = %s', [self.cobro.pk],
                         antes=[f"SET LOCAL cafeto.pasaje_historico = '{marca}'"])


class VistaDeVentasCompletaTest(BaseCredito):

    def test_la_vista_trae_el_estado_de_la_venta_viva_y_de_la_archivada(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT column_name FROM information_schema.columns "
                           "WHERE table_name = 'v_ventas_completo' ORDER BY ordinal_position")
            columnas = [fila[0] for fila in cursor.fetchall()]
            self.assertEqual(columnas[-1], 'estado_venta')
            venta = self.fiar(autorizante=self.admin)
            cursor.execute('SELECT estado_venta FROM v_ventas_completo WHERE id_venta = %s', [venta.pk])
            self.assertEqual(cursor.fetchone()[0], 'COMPLETADA')
