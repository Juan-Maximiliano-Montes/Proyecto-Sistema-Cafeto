"""Retención legal y pasaje a histórico por estado terminal (DD-99).

Lo que se prueba es que el proceso respete las dos mitades de la regla:
se archiva sólo lo que terminó (cuenta saldada, pedido resuelto, diferencia
revisada) y lo archivado queda inalterable diez años.
"""
from datetime import timedelta
from decimal import Decimal as D
from io import StringIO

from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from fidelizacion import credito
from fidelizacion.models import MovimientoCuenta
from fidelizacion.tests.test_credito import Base as BaseCredito
from nucleo.tests import fabrica as f
from ventas import caja
from ventas.models import SolicitudAutorizacion, TurnoCaja, Venta


def contar(tabla, donde='TRUE', parametros=()):
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT COUNT(*) FROM {tabla} WHERE {donde}', list(parametros))
        return cursor.fetchone()[0]


class Base(BaseCredito):

    def envejecer(self, dias=800):
        hace = timezone.now() - timedelta(days=dias)
        TurnoCaja.objects.filter(pk=self.turno.pk).update(fecha_apertura=hace, fecha_cierre=hace)
        return hace

    def cerrar(self):
        caja.cerrar(self.turno, self.cajera, self.caja())

    def archivar(self, *extra):
        call_command('pasar_a_historico', '--ejecutar', *extra, stdout=StringIO())


class CuentaCorrienteTest(Base):

    def test_una_venta_fiada_impaga_no_se_archiva(self):
        self.habilitar()
        venta = self.fiar()
        self.cerrar()
        self.envejecer()
        self.archivar()
        self.assertTrue(Venta.objects.filter(pk=venta.pk).exists(),
                        'una deuda viva no puede salir del libro')
        self.assertTrue(TurnoCaja.objects.filter(pk=self.turno.pk).exists())

    def test_saldada_se_archiva_con_su_cuenta_corriente(self):
        self.habilitar()
        venta = self.fiar()
        credito.cobrar(self.elsa, D('2000'), f.efectivo(), self.cajera, turno=self.turno)
        self.cerrar()
        self.envejecer()
        movimientos = list(MovimientoCuenta.objects.values_list('pk', flat=True))
        self.assertEqual(len(movimientos), 2)
        self.archivar()
        self.assertFalse(Venta.objects.filter(pk=venta.pk).exists())
        self.assertFalse(MovimientoCuenta.objects.exists())
        self.assertEqual(contar('hist_movimientos_cuenta'), 2)
        self.assertEqual(contar('hist_ventas', 'id_venta = %s', [venta.pk]), 1)
        # La cuenta de Elsa sigue en cero: lo archivado no se vuelve a cobrar.
        self.assertEqual(credito.saldo(self.elsa), D('0'))

    def test_un_turno_con_diferencia_sin_revisar_no_se_archiva(self):
        # Faltan $ 50.000 (fuera de tolerancia): queda para que la titular
        # la revise (DD-97).
        caja.cerrar(self.turno, self.cajera, self.caja() - D('50000') if self.caja() > D('50000') else D('0'),
                    a_revisar=True, observacion='Faltante grande, lo revisa la titular mañana')
        self.turno.refresh_from_db()
        self.assertTrue(self.turno.requiere_revision)
        self.envejecer()
        self.archivar()
        self.assertTrue(TurnoCaja.objects.filter(pk=self.turno.pk).exists())


class SolicitudesTest(Base):

    def solicitud(self, estado, dias):
        cuando = timezone.now() - timedelta(days=dias)
        resuelta = estado != 'PENDIENTE'
        return SolicitudAutorizacion.objects.create(
            id_usuario_solicitante=self.cajera, id_turno_caja=self.turno,
            motivo_solicitud='Fiarle por primera vez', monto_solicitud=D('2000'), pedido={},
            huella_operacion='a' * 64, token_hash=f'{estado}{dias}'.ljust(64, 'b'), pin_hash='c' * 64,
            estado_solicitud=estado, fecha_solicitud=cuando,
            fecha_vencimiento=cuando + timedelta(minutes=10),
            fecha_resolucion=cuando + timedelta(minutes=1) if resuelta else None)

    def test_resueltas_y_vencidas_viejas_se_archivan_las_recientes_no(self):
        vieja = self.solicitud('RECHAZADA', 400)
        olvidada = self.solicitud('PENDIENTE', 400)
        reciente = self.solicitud('ANULADA', 30)
        self.archivar()
        quedan = set(SolicitudAutorizacion.objects.values_list('pk', flat=True))
        self.assertEqual(quedan, {reciente.pk})
        self.assertEqual(contar('hist_solicitudes_autorizacion', 'id_solicitud IN (%s, %s)',
                                [vieja.pk, olvidada.pk]), 2)

    def test_el_turno_con_un_pedido_sin_archivar_espera(self):
        self.cerrar()
        self.envejecer()
        self.solicitud('ANULADA', 30)
        self.archivar()
        self.assertTrue(TurnoCaja.objects.filter(pk=self.turno.pk).exists())


class HistoricoInalterableTest(Base):

    def test_lo_archivado_no_se_modifica_ni_se_borra(self):
        self.cerrar()
        self.envejecer()
        self.archivar()
        self.assertEqual(contar('hist_turnos_caja', 'id_turno_caja = %s', [self.turno.pk]), 1)
        for sentencia in ('UPDATE hist_turnos_caja SET monto_inicial = 0',
                          'DELETE FROM hist_turnos_caja',
                          'TRUNCATE hist_turnos_caja'):
            with self.subTest(sentencia=sentencia), self.assertRaises(DatabaseError):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(sentencia)
        self.assertEqual(contar('hist_turnos_caja', 'id_turno_caja = %s', [self.turno.pk]), 1)


class PanelArchivoTest(Base):

    def test_la_dueña_ve_cuantos_comprobantes_hay_por_año_y_exporta(self):
        venta = self.fiar(autorizante=self.admin)
        credito.cobrar(self.elsa, D('2000'), f.efectivo(), self.cajera, turno=self.turno)
        self.cerrar()
        hace = self.envejecer()
        Venta.objects.filter(pk=venta.pk).update(fecha_venta=hace)
        self.archivar()
        f.entrar(self.client, self.admin)
        respuesta = self.client.get(reverse('negocio:archivo'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, '10 años')
        fila = next(a for a in respuesta.context['anios'] if a['anio'] == hace.year)
        self.assertEqual(fila['ventas']['archivadas'], 1)
        dia = timezone.localtime(hace).date()
        csv = self.client.get(reverse('negocio:archivo_exportar'),
                              {'libro': 'ventas', 'desde': dia.isoformat(),
                               'hasta': dia.isoformat(), 'formato': 'csv'})
        self.assertEqual(csv.status_code, 200)
        contenido = b''.join(csv.streaming_content if csv.streaming else [csv.content]).decode('utf-8-sig')
        self.assertIn(str(venta.pk), contenido)
        self.assertIn('Archivada', contenido)

    def test_la_cajera_no_entra(self):
        f.entrar(self.client, self.cajera)
        respuesta = self.client.get(reverse('negocio:archivo'))
        self.assertNotEqual(respuesta.status_code, 200)

    def test_un_pago_sin_hacer_cuenta_por_su_vencimiento(self):
        from compras.models import Egreso, TipoEgreso

        vence = timezone.localdate()
        Egreso.objects.create(id_tipo_egreso=TipoEgreso.objects.get(nombre_tipo_egreso='Servicios'),
                              destinatario='EDESA', monto=D('50000'), concepto='Factura de luz',
                              fecha_vencimiento=vence, estado_pago='PENDIENTE_PAGO')
        f.entrar(self.client, self.admin)
        respuesta = self.client.get(reverse('negocio:archivo'), {'libro': 'egresos', 'anio': vence.year})
        self.assertEqual(respuesta.status_code, 200)
        fila = next(a for a in respuesta.context['anios'] if a['anio'] == vence.year)
        self.assertEqual(fila['egresos']['vigentes'], 1)


class FiltroPorAnioTest(Base):

    def test_el_año_elegido_viaja_en_el_formulario_y_en_las_descargas(self):
        f.entrar(self.client, self.admin)
        anio = timezone.localdate().year
        pantalla = self.client.get(reverse('negocio:archivo'), {'libro': 'compras', 'anio': anio})
        html = pantalla.content.decode()
        self.assertIn(f'value="{anio}-01-01"', html)
        self.assertRegex(html, rf'<option value="{anio}" selected>')
        # Las descargas son botones del mismo formulario: mandan lo elegido ahora.
        self.assertIn('formaction="/negocio/archivo-historico/exportar/" name="formato" value="csv"', html)
        exportado = self.client.get(reverse('negocio:archivo_exportar'),
                                    {'libro': 'ventas', 'anio': anio, 'formato': 'csv'})
        self.assertEqual(exportado.status_code, 200)
        self.assertIn('archivo-ventas', exportado['Content-Disposition'])
