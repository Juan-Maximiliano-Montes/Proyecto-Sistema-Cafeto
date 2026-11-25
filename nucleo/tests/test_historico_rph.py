"""DB-RPH-01: el pase a histórico de la auditoría y la deriva de esquema.

1. ``pasar_a_historico``: la familia de la auditoría elige lo que archiva en
   tablas temporales (``tmp_log``, ``tmp_intentos``) y verifica lo copiado
   ANTES de borrar, igual que las demás familias.
2. ``auditar_consistencia``: compara cada tabla con histórico contra su
   ``hist_*`` y avisa cuando una migración tocó una y no la otra.
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from nucleo.management.commands import auditar_consistencia
from nucleo.management.commands.pasar_a_historico import FAMILIA_AUDITORIA, FAMILIAS
from seguridad.models import IntentoAcceso, LogAuditoria


def contar(tabla, donde='TRUE', parametros=()):
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT COUNT(*) FROM {tabla} WHERE {donde}', list(parametros))
        return cursor.fetchone()[0]


class AuditoriaConTablasTemporalesTest(TestCase):

    def setUp(self):
        hace = timezone.now() - timedelta(days=800)
        self.log_viejo = LogAuditoria.objects.create(
            operacion_realizada='ALTA DE RUBRO', modulo_afectado='STOCK', fecha_hora=hace)
        self.log_nuevo = LogAuditoria.objects.create(
            operacion_realizada='ALTA DE MARCA', modulo_afectado='STOCK')
        self.intento_viejo = IntentoAcceso.objects.create(
            nombre_usuario_ingresado='nadie', ip_origen='10.0.0.1', fecha_hora=hace,
            motivo_rechazo='USUARIO_INEXISTENTE')
        self.intento_nuevo = IntentoAcceso.objects.create(
            nombre_usuario_ingresado='nadie', ip_origen='10.0.0.1',
            motivo_rechazo='USUARIO_INEXISTENTE')

    def archivar(self):
        call_command('pasar_a_historico', '--ejecutar', stdout=StringIO())

    def test_la_familia_elige_con_tablas_temporales_y_verifica_antes_de_borrar(self):
        creadas = ' '.join(FAMILIA_AUDITORIA['seleccion'])
        for tabla in ('tmp_sesiones', 'tmp_log', 'tmp_intentos'):
            self.assertIn(f'CREATE TEMP TABLE {tabla} ON COMMIT DROP', creadas)
        etiquetas = [etiqueta for etiqueta, _ in FAMILIA_AUDITORIA['verificacion']]
        self.assertEqual(etiquetas, ['log_auditoria', 'sesiones', 'intentos_acceso'])
        # Lo que se copia y lo que se borra sale de las listas, no de un
        # corte de fecha repetido: el mismo conjunto en las dos mitades.
        for sentencia in [s for _, s in FAMILIA_AUDITORIA['copias']] + FAMILIA_AUDITORIA['borrados']:
            self.assertNotIn('fecha_hora <', sentencia)
        self.assertIs(FAMILIAS[-1], FAMILIA_AUDITORIA)

    def test_archiva_lo_viejo_y_deja_lo_reciente(self):
        self.archivar()
        self.assertFalse(LogAuditoria.objects.filter(pk=self.log_viejo.pk).exists())
        self.assertFalse(IntentoAcceso.objects.filter(pk=self.intento_viejo.pk).exists())
        self.assertTrue(LogAuditoria.objects.filter(pk=self.log_nuevo.pk).exists())
        self.assertTrue(IntentoAcceso.objects.filter(pk=self.intento_nuevo.pk).exists())
        self.assertEqual(contar('hist_log_auditoria', 'id_auditoria = %s', [self.log_viejo.pk]), 1)
        self.assertEqual(contar('hist_intentos_acceso', 'id_intento = %s', [self.intento_viejo.pk]), 1)

    def test_si_lo_copiado_no_cuadra_no_se_borra_nada(self):
        # Una verificación que siempre da una fila de más: como si la copia
        # se hubiera perdido una. El pase aborta y la transacción deshace.
        trampa = [('log_auditoria', 'SELECT COUNT(*) + 1 FROM tmp_log')]
        with mock.patch.dict(FAMILIA_AUDITORIA, {'verificacion': trampa}):
            with self.assertRaisesMessage(RuntimeError, 'No se borró nada'):
                self.archivar()
        self.assertTrue(LogAuditoria.objects.filter(pk=self.log_viejo.pk).exists())
        self.assertTrue(IntentoAcceso.objects.filter(pk=self.intento_viejo.pk).exists())
        self.assertEqual(contar('hist_log_auditoria', 'id_auditoria = %s', [self.log_viejo.pk]), 0)

    def test_cada_tabla_se_verifica_contra_su_propia_lista(self):
        trampa = [('intentos_acceso', 'SELECT COUNT(*) + 1 FROM tmp_intentos')]
        with mock.patch.dict(FAMILIA_AUDITORIA, {'verificacion': trampa}):
            with self.assertRaisesMessage(RuntimeError, 'intentos_acceso'):
                self.archivar()
        self.assertTrue(IntentoAcceso.objects.filter(pk=self.intento_viejo.pk).exists())

    def test_una_verificacion_suelta_sigue_valiendo(self):
        # Las demás familias declaran UNA verificación como tupla.
        with mock.patch.dict(FAMILIA_AUDITORIA, {'verificacion': ('log_auditoria', 'SELECT COUNT(*) FROM tmp_log')}):
            self.archivar()
        self.assertFalse(LogAuditoria.objects.filter(pk=self.log_viejo.pk).exists())


class DerivaDeEsquemaTest(TestCase):

    def correr(self):
        salida = StringIO()
        call_command('auditar_consistencia', stdout=salida)
        return salida.getvalue()

    def sql(self, sentencia):
        with connection.cursor() as cursor:
            cursor.execute(sentencia)

    def test_sin_deriva_cada_historico_coincide_con_su_tabla_viva(self):
        self.assertEqual(list(auditar_consistencia.derivas_de_esquema()), [])
        self.assertIn('cada hist_* coincide con su tabla viva', self.correr())

    def test_se_revisan_todas_las_tablas_con_historico(self):
        with connection.cursor() as cursor:
            cursor.execute(auditar_consistencia.TABLAS_CON_HISTORICO_VIGENTES)
            revisadas = {fila[0] for fila in cursor.fetchall()}
        for tabla in ('ventas', 'detalles_venta', 'cambios', 'movimientos_stock', 'log_auditoria',
                      'movimientos_cuenta', 'solicitudes_autorizacion', 'facturas_recibidas'):
            self.assertIn(f'hist_{tabla}', revisadas)

    def test_a_la_tabla_viva_le_agregaron_una_columna_y_al_historico_no(self):
        self.sql('ALTER TABLE cambios ADD COLUMN columna_nueva integer')
        derivas = list(auditar_consistencia.derivas_de_esquema())
        self.assertEqual([(d.tabla, d.historico) for d in derivas], [('cambios', 'hist_cambios')])
        self.assertIn('le falta la columna "columna_nueva"', derivas[0].motivo)
        self.assertIn('DERIVAS DE ESQUEMA', self.correr())

    def test_el_historico_conserva_una_columna_que_la_viva_perdio(self):
        self.sql('ALTER TABLE hist_ordenes_compra ADD COLUMN sobrante text')
        (deriva,) = auditar_consistencia.derivas_de_esquema()
        self.assertIn('tiene la columna "sobrante" que ya no existe en ordenes_compra', deriva.motivo)

    def test_el_tipo_de_una_columna_cambio_en_una_sola_de_las_dos(self):
        self.sql('ALTER TABLE hist_cambios ALTER COLUMN detalle_motivo TYPE varchar(200)')
        (deriva,) = auditar_consistencia.derivas_de_esquema()
        self.assertEqual(deriva.tabla, 'cambios')
        self.assertIn('"detalle_motivo" es varchar(120) en cambios y varchar(200) en hist_cambios', deriva.motivo)

    def test_al_historico_le_falta_la_marca_de_archivo(self):
        self.sql('ALTER TABLE hist_egresos DROP COLUMN fecha_archivo')
        (deriva,) = auditar_consistencia.derivas_de_esquema()
        self.assertIn('no tiene la columna "fecha_archivo"', deriva.motivo)

    def test_la_deriva_se_informa_pero_no_se_da_de_baja(self):
        self.sql('ALTER TABLE hist_cambios ALTER COLUMN detalle_motivo TYPE text')
        salida = StringIO()
        call_command('auditar_consistencia', '--dar-de-baja', stdout=salida)
        texto = salida.getvalue()
        self.assertIn('hace falta una migración', texto)
        self.assertNotIn('dados de baja', texto)
