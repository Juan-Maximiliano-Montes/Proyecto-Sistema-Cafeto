"""Dos agujeros del cierre de caja (auditoría 2026-11).

    FormularioInvalidoTest   el token de idempotencia se consumía ANTES de
                             validar el formulario: una observación más
                             larga de lo que entra dejaba el turno abierto
                             y sin forma de cerrarlo
    RetomarPedidoTest        recargar la pantalla (F5, la tablet que se
                             bloquea) perdía el pedido de autorización a
                             distancia: al volver a pedirlo se anulaba el
                             anterior y el PIN ya dictado dejaba de servir
    TicketParaQuienMiraTest  quien ve las ventas tiene que poder abrir el
                             ticket aunque no venda
"""
from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from nucleo import idempotencia
from nucleo.tests import fabrica as f
from ventas import a_distancia, caja
from ventas import servicios as ventas
from ventas.models import EstadoSolicitud, SolicitudAutorizacion, TurnoCaja
from ventas.tests.test_relevo_y_comprobantes import Base

MOTIVO = 'Faltan 12 mil: se fue sin cerrar'


class ConDiferencia(Base):
    """Un turno de ayer al que le falta plata: pide que alguien apruebe."""

    def setUp(self):
        super().setUp()
        self.turno = self.abrir(self.ana)
        ventas.registrar(self.turno, self.ana, [ventas.Item(self.alfajor, D('15'))],
                         [ventas.Pago(f.efectivo())])
        TurnoCaja.objects.filter(pk=self.turno.pk).update(
            fecha_apertura=timezone.now() - timedelta(days=1))
        self.contado = caja.saldo(self.turno) - D('12000')
        self.entrar(self.beto)
        self.declarar(str(self.contado))
        self.revelar()

    def token_vigente(self):
        return self.client.get(reverse('ventas:cierre')).context['form'].initial['token_operacion']


# ===========================================================================
class FormularioInvalidoTest(ConDiferencia):

    def test_una_observacion_demasiado_larga_no_cierra_ni_quema_el_token(self):
        token = self.token_vigente()
        respuesta = self.client.post(reverse('ventas:cierre'), {
            'token_operacion': token, 'modo': 'CIERRE', 'forma': 'REVISAR',
            'observacion': 'x' * 300})           # la columna acepta 120
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, 'ventas/cierre.html')
        self.turno.refresh_from_db()
        self.assertTrue(self.turno.abierto, 'no se puede cerrar con el formulario mal')
        # Lo importante: el token NO se gastó, así que el segundo intento vale.
        self.assertIn(token, respuesta.wsgi_request.session.get(idempotencia.CLAVE, []))

    def test_corregida_la_observacion_cierra_con_el_mismo_token(self):
        token = self.token_vigente()
        self.client.post(reverse('ventas:cierre'), {
            'token_operacion': token, 'modo': 'CIERRE', 'forma': 'REVISAR',
            'observacion': 'x' * 300})
        respuesta = self.client.post(reverse('ventas:cierre'), {
            'token_operacion': token, 'modo': 'CIERRE', 'forma': 'REVISAR',
            'observacion': MOTIVO})
        self.assertEqual(respuesta.status_code, 302, respuesta.content[:400])
        self.turno.refresh_from_db()
        self.assertFalse(self.turno.abierto)
        self.assertTrue(self.turno.requiere_revision)

    def test_el_error_se_ve_y_no_se_pierde_lo_cargado(self):
        respuesta = self.client.post(reverse('ventas:cierre'), {
            'token_operacion': self.token_vigente(), 'modo': 'CIERRE',
            'forma': 'REVISAR', 'observacion': 'x' * 300})
        self.assertTrue(respuesta.context['form'].errors)
        self.assertContains(respuesta, 'x' * 300)

    def test_un_modo_que_no_corresponde_tampoco_cierra(self):
        token = self.token_vigente()
        respuesta = self.client.post(reverse('ventas:cierre'), {
            'token_operacion': token, 'modo': 'INVENTADO', 'forma': 'REVISAR',
            'observacion': MOTIVO})
        self.assertEqual(respuesta.status_code, 200)
        self.turno.refresh_from_db()
        self.assertTrue(self.turno.abierto)
        self.assertIn(token, respuesta.wsgi_request.session.get(idempotencia.CLAVE, []))


# ===========================================================================
class RetomarPedidoTest(ConDiferencia):

    def pedir(self):
        pedido = self.client.post(reverse('ventas:pedir_autorizacion_cierre'),
                                  {'modo': 'CIERRE'}, content_type='application/json').json()
        self.assertTrue(pedido['ok'], pedido)
        return SolicitudAutorizacion.objects.get(pk=pedido['id'])

    def test_al_recargar_la_pantalla_el_pedido_sigue_ahi(self):
        solicitud = self.pedir()
        pantalla = self.client.get(reverse('ventas:cierre'))
        self.assertEqual(pantalla.context['pendiente_remota'], solicitud)
        self.assertGreater(pantalla.context['pendiente_restan'], 0)
        self.assertEqual(pantalla.context['form'].initial['forma'], 'DISTANCIA')
        self.assertContains(pantalla, f'id="remotaPendiente" data-id="{solicitud.pk}"')

    def test_el_pedido_no_se_anula_por_recargar(self):
        solicitud = self.pedir()
        self.client.get(reverse('ventas:cierre'))
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado_solicitud, EstadoSolicitud.PENDIENTE)

    def test_uno_ya_aprobado_manda_sobre_el_pendiente(self):
        from ventas.models import ViaAprobacion

        solicitud = self.pedir()
        a_distancia.resolver(solicitud, f.titular(), True, ViaAprobacion.PIN,
                             a_distancia.control_de(solicitud))
        pantalla = self.client.get(reverse('ventas:cierre'))
        self.assertEqual(pantalla.context['aprobada'], solicitud)
        self.assertIsNone(pantalla.context['pendiente_remota'])

    def test_uno_vencido_no_se_retoma(self):
        solicitud = self.pedir()
        # El vencimiento va DESPUÉS del pedido (ck_solautoriz_valores): se
        # mueven los dos al pasado, como en test_autorizacion_a_distancia.
        SolicitudAutorizacion.objects.filter(pk=solicitud.pk).update(
            fecha_solicitud=timezone.now() - timedelta(minutes=30),
            fecha_vencimiento=timezone.now() - timedelta(minutes=20))
        pantalla = self.client.get(reverse('ventas:cierre'))
        self.assertIsNone(pantalla.context['pendiente_remota'])

    def test_sin_pedido_no_hay_nada_que_retomar(self):
        pantalla = self.client.get(reverse('ventas:cierre'))
        self.assertIsNone(pantalla.context['pendiente_remota'])
        self.assertNotContains(pantalla, 'id="remotaPendiente"')


# ===========================================================================
class TicketParaQuienMiraTest(Base):
    """``ticket`` pedía 'vender', pero adentro la propia vista controla
    'ver_ventas' para el ticket de otra persona: quien supervisa se comía
    un «acceso denegado» con el permiso de ver ventas puesto. Es el mismo
    criterio que ``comprobante_cambio``."""

    def setUp(self):
        super().setUp()
        turno = self.abrir(self.ana)
        self.venta = ventas.registrar(
            turno, self.ana, [ventas.Item(self.alfajor, D('1'))],
            [ventas.Pago(f.efectivo())])[0]

    def url(self):
        return reverse('ventas:ticket', args=[self.venta.pk])

    def test_quien_ve_las_ventas_abre_el_ticket_sin_vender(self):
        from seguridad.tests.test_auditoria_permisos import perfil_con

        mirona = f.usuario('40200009', 'Operador', nombre='Sol')
        mirona.id_perfil = perfil_con('Supervisión', 'ver_ventas')
        mirona.save()
        f.entrar(self.client, mirona)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_quien_vende_abre_el_suyo(self):
        f.entrar(self.client, self.ana)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_sin_ninguno_de_los_dos_no_entra(self):
        from seguridad.tests.test_auditoria_permisos import perfil_con

        ajena = f.usuario('40200010', 'Operador', nombre='Pia')
        ajena.id_perfil = perfil_con('Depósito', 'ver_stock')
        ajena.save()
        f.entrar(self.client, ajena)
        self.assertNotEqual(self.client.get(self.url()).status_code, 200)
