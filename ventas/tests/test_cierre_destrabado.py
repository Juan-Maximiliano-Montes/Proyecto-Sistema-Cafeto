"""El turno de Carla que nadie podía cerrar (DD-101).

Carla abrió ayer y se fue sin cerrar; hoy falta $ 12.000 en el cajón. Otro
cajero tiene que poder cerrarlo con cualquiera de las tres formas de
aprobar la diferencia (al lado, a distancia o pendiente de revisión) y
cambiar hasta el final qué se hace con el turno: lo único fijo, una vez
vista la diferencia, es lo contado.
"""
from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from nucleo.tests import fabrica as f
from ventas import a_distancia, caja
from ventas import servicios as ventas
from ventas.models import EstadoSolicitud, SolicitudAutorizacion, TurnoCaja, ViaAprobacion
from ventas.tests.test_relevo_y_comprobantes import Base

MOTIVO = 'Faltan 12 mil: Carla se fue sin cerrar ayer'


class Escenario(Base):
    """Ana hace de Carla (abrió ayer); Beto hace de Ariel (llega hoy)."""

    def setUp(self):
        super().setUp()
        self.turno = self.abrir(self.ana)
        ventas.registrar(self.turno, self.ana, [ventas.Item(self.alfajor, D('15'))],
                         [ventas.Pago(f.efectivo())])
        TurnoCaja.objects.filter(pk=self.turno.pk).update(
            fecha_apertura=timezone.now() - timedelta(days=1))
        self.esperado = caja.saldo(self.turno)
        self.contado = self.esperado - D('12000')
        self.entrar(self.beto)
        self.declarar(str(self.contado), modo='RELEVO')   # eligió mal: queda en relevo
        self.revelar()

    def cerrado(self):
        self.turno.refresh_from_db()
        self.assertFalse(self.turno.abierto, 'el turno de Carla sigue abierto')
        self.assertEqual(self.turno.diferencia_caja, D('-12000'))
        self.assertEqual(self.turno.id_usuario_cierre, self.beto)
        return self.turno


class TurnoDeCarlaTest(Escenario):

    # --- las tres formas de aprobar la diferencia --------------------------
    def test_supervisora_presente_y_cambio_a_cerrar(self):
        respuesta = self.confirmar(modo='CIERRE', observacion=MOTIVO, forma='ACA',
                                   autoriza_usuario='40200003', autoriza_clave=f.CLAVE)
        self.assertEqual(respuesta.status_code, 302, respuesta.content[:300])
        turno = self.cerrado()
        self.assertEqual(turno.tipo_cierre, 'SUPERVISADO')
        self.assertEqual(turno.id_usuario_supervisor, self.lucre)
        self.assertFalse(turno.requiere_revision)
        self.assertIsNone(turno.siguiente)

    def test_a_distancia_y_seguir_yo(self):
        pedido = self.client.post(reverse('ventas:pedir_autorizacion_cierre'), {'modo': 'AUSENCIA'},
                                  content_type='application/json').json()
        solicitud = SolicitudAutorizacion.objects.get(pk=pedido['id'])
        a_distancia.resolver(solicitud, f.titular(), True, ViaAprobacion.PIN, a_distancia.control_de(solicitud))
        respuesta = self.confirmar(modo='AUSENCIA', observacion=MOTIVO, forma='DISTANCIA', remota=solicitud.pk)
        self.assertEqual(respuesta.status_code, 302, respuesta.content[:300])
        turno = self.cerrado()
        self.assertEqual(turno.tipo_cierre, 'SUPERVISADO')
        self.assertTrue(turno.relevo_en_ausencia)
        self.assertEqual(turno.siguiente.id_usuario_apertura, self.beto)
        self.assertEqual(turno.siguiente.monto_inicial, self.contado)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado_solicitud, EstadoSolicitud.USADA)

    def pendiente(self, modo, **extra):
        respuesta = self.confirmar(modo=modo, observacion=MOTIVO, forma='REVISAR', **extra)
        self.assertEqual(respuesta.status_code, 302, respuesta.content[:400])
        turno = self.cerrado()
        self.assertTrue(turno.requiere_revision)
        self.assertIsNone(turno.id_usuario_supervisor)
        self.assertEqual(turno.relevo_en_ausencia, modo != 'CIERRE')
        return turno

    def test_pendiente_de_revision_cerrando(self):
        self.assertIsNone(self.pendiente('CIERRE').siguiente)

    def test_pendiente_de_revision_y_sigo_yo(self):
        self.assertEqual(self.pendiente('AUSENCIA').siguiente.id_usuario_apertura, self.beto)

    def test_pendiente_de_revision_y_sigue_un_compañero(self):
        turno = self.pendiente('RELEVO', entrante_usuario='40200003', entrante_clave=f.CLAVE)
        self.assertEqual(turno.siguiente.id_usuario_apertura, self.lucre)

    # --- el cajón lo sigue quien está acá ----------------------------------
    def test_en_relevo_si_sigue_quien_esta_aca_es_cerrar_y_abrir_el_suyo(self):
        respuesta = self.confirmar(modo='RELEVO', observacion=MOTIVO, forma='REVISAR',
                                   entrante_usuario='40200002')     # Beto mismo, sin clave
        self.assertEqual(respuesta.status_code, 302, respuesta.content[:400])
        turno = self.cerrado()
        self.assertTrue(turno.relevo_en_ausencia)
        self.assertEqual(turno.siguiente.id_usuario_apertura, self.beto)

    def test_si_el_turno_es_propio_el_relevo_consigo_mismo_explica_que_elegir(self):
        nuevo = caja.abrir(f.caja_minisuper(), self.beto, D('1000'))
        self.client.post(reverse('ventas:elegir_caja'), {'caja': nuevo.id_caja_registradora_id})
        self.declarar('1000', modo='RELEVO')
        respuesta = self.confirmar(modo='RELEVO', entrante_usuario='40200002', entrante_clave=f.CLAVE)
        self.assertContains(respuesta, 'quien recibe el cajón tiene que ser el compañero que entra')
        self.assertContains(respuesta, 'Cerrar la caja')

    # --- lo cargado no se pierde ------------------------------------------
    def test_un_error_no_borra_lo_cargado(self):
        respuesta = self.confirmar(modo='AUSENCIA', observacion=MOTIVO, forma='ACA',
                                   autoriza_usuario='40200003', autoriza_clave='equivocada')
        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.content.decode()
        self.assertIn(f'value="{MOTIVO}"', html)
        self.assertIn('value="40200003"', html)
        self.assertRegex(html, r'id="modoAusencia" value="AUSENCIA" checked')
        self.assertRegex(html, r'value="ACA"[^>]*checked|checked[^>]*value="ACA"')
        self.assertTrue(TurnoCaja.objects.get(pk=self.turno.pk).abierto)


class AprobacionUnaSolaVezTest(Escenario):
    """Una aprobación de la titular no se pide ni se valida dos veces."""

    def aprobar(self):
        pedido = self.client.post(reverse('ventas:pedir_autorizacion_cierre')).json()
        solicitud = SolicitudAutorizacion.objects.get(pk=pedido['id'])
        a_distancia.resolver(solicitud, f.titular(), True, ViaAprobacion.PIN, a_distancia.control_de(solicitud))
        return solicitud

    def test_pedir_de_nuevo_devuelve_la_misma_aprobacion(self):
        solicitud = self.aprobar()
        otra = self.client.post(reverse('ventas:pedir_autorizacion_cierre')).json()
        self.assertEqual(otra['id'], solicitud.pk)
        self.assertEqual(otra['estado'], 'APROBADA')
        self.assertEqual(SolicitudAutorizacion.objects.count(), 1)

    def test_otro_compañero_en_la_misma_caja_usa_la_aprobacion(self):
        solicitud = self.aprobar()
        facu = f.usuario('40200009', 'Operador', nombre='Facundo', apellido='Díaz')
        self.entrar(facu)
        pantalla = self.client.get(reverse('ventas:cierre'))
        self.assertContains(pantalla, 'ya aprobó esta diferencia a distancia')
        self.assertEqual(self.client.get(reverse('ventas:estado_autorizacion', args=[solicitud.pk])).status_code, 200)
        respuesta = self.confirmar(modo='AUSENCIA', observacion=MOTIVO, forma='DISTANCIA', remota=solicitud.pk)
        self.assertEqual(respuesta.status_code, 302, respuesta.content[:400])
        self.turno.refresh_from_db()
        self.assertEqual(self.turno.id_usuario_cierre, facu)
        self.assertEqual(self.turno.id_usuario_supervisor, f.titular())

    def test_el_pin_de_algo_ya_aprobado_no_se_vuelve_a_validar(self):
        solicitud = self.aprobar()
        respuesta = self.client.post(reverse('ventas:pin_autorizacion', args=[solicitud.pk]),
                                     {'pin': '0000'}, content_type='application/json').json()
        self.assertTrue(respuesta['ok'])
        self.assertEqual(respuesta['estado'], 'APROBADA')
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.intentos_pin, 0)

    def test_aprobada_y_no_usada_se_anula_sin_trabar_el_cierre(self):
        solicitud = self.aprobar()
        respuesta = self.confirmar(modo='CIERRE', observacion=MOTIVO, forma='REVISAR')
        self.assertEqual(respuesta.status_code, 302, respuesta.content[:400])
        self.cerrado()
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado_solicitud, EstadoSolicitud.ANULADA)
        self.assertEqual(solicitud.via_aprobacion, ViaAprobacion.PIN)   # la historia queda
