"""Fiado sin trabas y autorización a distancia (DD-94).

Primero lo que tiene que andar SIN pedir a nadie (el cliente que la dueña
ya habilitó), después el circuito completo del pedido a distancia —correo,
enlace, PIN, bandeja, la venta que lo usa— y por último los casos que lo
tienen que frenar: ticket cambiado, PIN mal puesto, vencido, autoaprobarse.
"""
import json
import uuid
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from fidelizacion import credito
from fidelizacion.models import Cliente, MovimientoCuenta
from fidelizacion.tests.test_credito import Base
from negocio import mensual
from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria
from ventas import a_distancia, caja
from ventas.models import MovimientoCaja, SolicitudAutorizacion, Venta


class Pantalla(Base):

    def setUp(self):
        super().setUp()
        self.duenia = f.titular()
        # Los avisos salen en otro hilo; en las pruebas, en el mismo.
        en_linea = mock.patch('ventas.a_distancia.en_segundo_plano', side_effect=lambda fn, *a: fn(*a))
        self.en_segundo_plano = en_linea.start()
        self.addCleanup(en_linea.stop)

    def entrar(self, usuario):
        f.entrar(self.client, usuario)
        self.client.post(reverse('ventas:elegir_caja'), {'caja': self.turno.id_caja_registradora_id})

    def pedir(self, nombre, cuerpo, *args):
        return self.client.post(reverse(f'ventas:{nombre}', args=args), json.dumps(cuerpo),
                                content_type='application/json').json()

    def cuerpo(self, cantidad='1', **extra):
        base = {'token': uuid.uuid4().hex, 'cliente': self.elsa.pk, 'beneficios': [],
                'items': [{'producto': self.alfajor.pk, 'cantidad': cantidad}],
                'pagos': [{'medio': self.cc.pk, 'monto': '', 'recibido': ''}]}
        base.update(extra)
        return base

    def pedir_a_distancia(self, cuerpo=None):
        """La caja pide; devuelve (respuesta, pin, enlace) leyendo el correo."""
        cuerpo = cuerpo or self.cuerpo()
        with self.captureOnCommitCallbacks(execute=True):
            r = self.pedir('pedir_autorizacion', cuerpo)
        self.assertTrue(r['ok'], r)
        # Le llega a cada persona que puede aprobar; el de la titular es éste.
        correo = next(m for m in reversed(mail.outbox) if self.duenia.correo in m.to)
        pin = correo.subject.rsplit('PIN ', 1)[1]
        enlace = [linea for linea in correo.body.splitlines() if '/ventas/autorizar/' in linea][0].strip()
        return r, pin, enlace[enlace.index('/ventas/'):]


# ===========================================================================
#  1. Lo que la dueña deja configurado: la caja no pide nada
# ===========================================================================
class PreautorizadoTest(Pantalla):

    def test_habilitado_al_dia_y_dentro_del_tope_se_cobra_sin_clave(self):
        credito.cambiar_estado(self.elsa, 'HABILITADO', self.duenia, limite=D('50000'), plazo=15,
                               nota='Cobra el día 10')
        self.entrar(self.cajera)
        cotizado = self.pedir('cotizar', self.cuerpo('3'))
        self.assertTrue(cotizado['ok'], cotizado)
        self.assertFalse(cotizado['pide_autorizacion'])
        self.assertEqual(cotizado['cliente']['credito']['plazo'], 15)
        cobro = self.pedir('cobrar', self.cuerpo('3'))
        self.assertTrue(cobro['ok'], cobro)
        fiado = MovimientoCuenta.objects.get(tipo_movimiento='FIADO')
        self.assertEqual(fiado.fecha_vencimiento, timezone.localdate() + timedelta(days=15))
        self.assertFalse(MovimientoCaja.objects.filter(id_venta_id=cobro['venta']).exists())

    def test_pasarse_del_tope_si_pide_autorizacion(self):
        credito.cambiar_estado(self.elsa, 'HABILITADO', self.duenia, limite=D('2000'))
        self.entrar(self.cajera)
        r = self.pedir('cotizar', self.cuerpo('3'))
        self.assertFalse(r['ok'])
        self.assertIn('su tope es', r['error'])

    def test_el_plazo_se_valida(self):
        for malo in (0, 400, 'diez'):
            with self.assertRaises(Exception):
                credito.cambiar_estado(self.elsa, 'HABILITADO', self.duenia, plazo=malo)

    def test_la_pantalla_de_la_cuenta_guarda_el_plazo(self):
        self.entrar(self.duenia)
        url = reverse('fidelizacion:cuenta', args=[self.elsa.pk])
        token = self.client.get(url).context['token']
        self.client.post(url, {'accion': 'estado', 'estado': 'HABILITADO', 'limite': '50.000',
                               'nota': 'Jubilada', 'plazo': '20', 'token_operacion': token})
        self.elsa.refresh_from_db()
        self.assertEqual((self.elsa.estado_credito, self.elsa.limite_credito, self.elsa.plazo_credito_dias),
                         ('HABILITADO', D('50000'), 20))


# ===========================================================================
#  2. Casos borde contables
# ===========================================================================
class ContabilidadTest(Pantalla):

    def test_pago_mixto_efectivo_y_cuenta_corriente(self):
        self.habilitar()
        f.comprar(self.admin, self.prov, (self.alfajor, 20, '600'))
        efectivo = f.efectivo()
        self.entrar(self.cajera)
        cobro = self.pedir('cobrar', self.cuerpo(
            '12', pagos=[{'medio': efectivo.pk, 'monto': '7000', 'recibido': '7000'},
                         {'medio': self.cc.pk, 'monto': '', 'recibido': ''}]))
        self.assertTrue(cobro['ok'], cobro)
        self.assertEqual(cobro['total'], '12000.00')
        fiado = MovimientoCuenta.objects.get(tipo_movimiento='FIADO')
        self.assertEqual(fiado.monto, D('5000'))
        caja_venta = MovimientoCaja.objects.get(id_venta_id=cobro['venta'])
        self.assertEqual(caja_venta.monto, D('7000'))

    def test_cobro_unificado_salda_de_la_mas_vieja_a_la_mas_nueva(self):
        self.habilitar()
        hoy = timezone.localdate()
        viejo = self.fiar('2', vence=hoy + timedelta(days=5))       # 2000
        nuevo = self.fiar('3', vence=hoy + timedelta(days=20))      # 3000
        resultado = credito.cobrar(self.elsa, D('2500'), f.efectivo(), self.cajera, turno=self.turno)
        self.assertEqual(resultado['compras_saldadas'], 1)
        saldos = dict(MovimientoCuenta.objects.filter(tipo_movimiento='FIADO')
                      .values_list('id_venta', 'saldo_pendiente'))
        self.assertEqual(saldos[viejo.pk], D('0'))
        self.assertEqual(saldos[nuevo.pk], D('2500'))

    def test_el_cobro_de_la_deuda_no_vuelve_a_ser_venta(self):
        self.habilitar()
        self.fiar('5')                                               # 5000
        antes = mensual.del_mes(timezone.localdate().year, timezone.localdate().month)
        credito.cobrar(self.elsa, D('5000'), f.efectivo(), self.cajera, turno=self.turno,
                       interes_porcentaje=D('10'))
        despues = mensual.del_mes(timezone.localdate().year, timezone.localdate().month)
        self.assertEqual(despues['bruta']['facturado'], antes['bruta']['facturado'])
        self.assertEqual(despues['bruta']['ganancia'], antes['bruta']['ganancia'])
        self.assertEqual(despues['total_entro'] - antes['total_entro'], D('5500'))
        self.assertEqual(despues['interes'], D('500'))
        self.assertEqual(despues['resultado'] - antes['resultado'], D('500'))

    def test_incobrable_es_quebranto_del_mes(self):
        self.habilitar()
        self.fiar('8')
        credito.declarar_incobrable(self.elsa, 'Se mudó y no contesta', self.duenia)
        r = mensual.del_mes(timezone.localdate().year, timezone.localdate().month)
        self.assertEqual(r['incobrable'], D('8000'))
        self.entrar(self.duenia)
        pantalla = self.client.get(reverse('negocio:resumen_del_mes'))
        self.assertContains(pantalla, 'Pérdidas por incobrabilidad')

    def test_el_cajon_separa_ventas_de_cobros_de_cuenta(self):
        self.habilitar()
        self.fiar('2')
        efectivo = f.efectivo()
        f.comprar(self.admin, self.prov, (self.alfajor, 5, '600'))
        from ventas import servicios as ventas
        ventas.registrar(self.turno, self.cajera, [ventas.Item(self.alfajor, D('1'))],
                         [ventas.Pago(efectivo, recibido=D('1000'))])
        credito.cobrar(self.elsa, D('2000'), efectivo, self.cajera, turno=self.turno)
        filas = dict(caja.resumen(self.turno))
        self.assertEqual(filas['Ventas en efectivo del turno'], D('1000'))
        self.assertEqual(filas['Cobros de cuentas corrientes en efectivo'], D('2000'))
        self.entrar(self.admin)
        ficha = self.client.get(reverse('ventas:ficha_turno', args=[self.turno.pk]))
        self.assertContains(ficha, 'Cobros de cuentas corrientes')


# ===========================================================================
#  3. Autorización a distancia
# ===========================================================================
@override_settings(CAFETO_N8N_WEBHOOK_AUTORIZACION='')
class ADistanciaTest(Pantalla):

    def test_circuito_completo_por_el_enlace(self):
        self.entrar(self.cajera)
        r, pin, enlace = self.pedir_a_distancia()
        self.assertEqual(r['estado'], 'PENDIENTE')
        self.assertIn('por primera vez', r['motivo'])
        # Le llega a la titular y a quien tiene el permiso (la encargada).
        self.assertEqual({m.to[0] for m in mail.outbox}, {self.duenia.correo, self.admin.correo})
        self.assertNotIn(pin, json.dumps(r))                 # la caja no ve el PIN
        solicitud = SolicitudAutorizacion.objects.get()
        self.assertNotIn(pin, solicitud.pin_hash)
        # Abrir el enlace (lo hace también el antivirus del correo) no aprueba.
        anonimo = self.client_class()
        pagina = anonimo.get(enlace)
        self.assertContains(pagina, 'Aprobar')
        self.assertEqual(SolicitudAutorizacion.objects.get().estado_solicitud, 'PENDIENTE')
        anonimo.post(enlace, {'accion': 'aprobar'})
        solicitud.refresh_from_db()
        self.assertEqual((solicitud.estado_solicitud, solicitud.via_aprobacion, solicitud.id_usuario_autorizante),
                         ('APROBADA', 'ENLACE', self.duenia))
        estado = self.client.get(reverse('ventas:estado_autorizacion', args=[solicitud.pk])).json()
        self.assertEqual(estado['estado'], 'APROBADA')
        # La caja cobra el mismo ticket con la aprobación.
        cuerpo = self.cuerpo(remota=solicitud.pk)
        self.assertTrue(self.pedir('cotizar', cuerpo)['ok'])
        cobro = self.pedir('cobrar', cuerpo)
        self.assertTrue(cobro['ok'], cobro)
        solicitud.refresh_from_db()
        self.assertEqual((solicitud.estado_solicitud, solicitud.id_venta_id), ('USADA', cobro['venta']))
        self.assertEqual(Cliente.objects.get(pk=self.elsa.pk).estado_credito, 'HABILITADO')
        asiento = LogAuditoria.objects.filter(operacion_realizada='ALTA DE VENTA').latest('pk')
        self.assertIn('a distancia', asiento.detalle_operacion)
        self.assertTrue(LogAuditoria.objects.filter(operacion_realizada='APROBACION DE AUTORIZACION A DISTANCIA',
                                                    id_usuario=self.duenia).exists())
        # Una aprobación se usa una sola vez.
        otra = self.pedir('cobrar', self.cuerpo(remota=solicitud.pk))
        self.assertFalse(otra['ok'])

    def test_el_pin_aprueba_y_cinco_errores_lo_anulan(self):
        self.entrar(self.cajera)
        r, pin, _ = self.pedir_a_distancia()
        malo = '0000' if pin != '0000' else '1111'
        respuesta = self.pedir('pin_autorizacion', {'pin': malo}, r['id'])
        self.assertFalse(respuesta['ok'])
        self.assertIn('Te quedan 4', respuesta['error'])
        bien = self.pedir('pin_autorizacion', {'pin': pin}, r['id'])
        self.assertTrue(bien['ok'], bien)
        self.assertEqual(bien['estado'], 'APROBADA')
        self.assertEqual(SolicitudAutorizacion.objects.get().via_aprobacion, 'PIN')

        r2, pin2, _ = self.pedir_a_distancia(self.cuerpo('2'))
        malo = '0000' if pin2 != '0000' else '1111'
        for _ in range(5):
            ultimo = self.pedir('pin_autorizacion', {'pin': malo}, r2['id'])
        self.assertIn('se anuló', ultimo['error'])
        tarde = self.pedir('pin_autorizacion', {'pin': pin2}, r2['id'])
        self.assertFalse(tarde['ok'])
        self.assertEqual(SolicitudAutorizacion.objects.get(pk=r2['id']).estado_solicitud, 'ANULADA')

    def test_si_el_ticket_cambia_la_aprobacion_no_sirve(self):
        self.entrar(self.cajera)
        r, pin, _ = self.pedir_a_distancia()
        self.pedir('pin_autorizacion', {'pin': pin}, r['id'])
        otro = self.pedir('cobrar', self.cuerpo('2', remota=r['id']))
        self.assertFalse(otro['ok'])
        self.assertIn('El ticket cambió', otro['error'])
        self.assertFalse(Venta.objects.exists())

    def test_vencida_no_se_aprueba_ni_se_usa(self):
        self.entrar(self.cajera)
        r, pin, enlace = self.pedir_a_distancia()
        SolicitudAutorizacion.objects.filter(pk=r['id']).update(
            fecha_solicitud=timezone.now() - timedelta(minutes=30),
            fecha_vencimiento=timezone.now() - timedelta(minutes=20))
        pagina = self.client_class().post(enlace, {'accion': 'aprobar'})
        self.assertContains(pagina, 'venció')
        self.assertEqual(SolicitudAutorizacion.objects.get().estado_solicitud, 'ANULADA')
        self.assertFalse(self.pedir('pin_autorizacion', {'pin': pin}, r['id'])['ok'])

    def test_bandeja_otra_persona_aprueba_y_nadie_se_aprueba_a_si_mismo(self):
        self.entrar(self.cajera)
        r, _pin, _ = self.pedir_a_distancia()
        # La cajera no tiene la bandeja.
        self.assertEqual(self.client.get(reverse('ventas:autorizaciones')).status_code, 302)
        self.entrar(self.admin)
        bandeja = self.client.get(reverse('ventas:autorizaciones'))
        self.assertContains(bandeja, 'por primera vez')
        self.assertContains(bandeja, '1 pedido de autorización esperando')
        self.client.post(reverse('ventas:autorizaciones'), {'solicitud': r['id'], 'accion': 'aprobar'})
        solicitud = SolicitudAutorizacion.objects.get()
        self.assertEqual((solicitud.estado_solicitud, solicitud.via_aprobacion, solicitud.id_usuario_autorizante),
                         ('APROBADA', 'BANDEJA', self.admin))
        with self.assertRaises(Exception):
            a_distancia.resolver(solicitud, self.cajera, True, 'BANDEJA', lambda u: None)

    def test_rechazo_desde_el_enlace(self):
        self.entrar(self.cajera)
        r, _pin, enlace = self.pedir_a_distancia()
        self.client_class().post(enlace, {'accion': 'rechazar'})
        estado = self.client.get(reverse('ventas:estado_autorizacion', args=[r['id']])).json()
        self.assertEqual(estado['estado'], 'RECHAZADA')
        self.assertFalse(self.pedir('cobrar', self.cuerpo(remota=r['id']))['ok'])

    def test_lo_que_no_necesita_firma_no_se_pide(self):
        self.habilitar()
        self.entrar(self.cajera)
        r = self.pedir('pedir_autorizacion', self.cuerpo())
        self.assertFalse(r['ok'])
        self.assertIn('no necesita autorización', r['error'])

    def test_enlace_falso_y_pedido_ajeno(self):
        self.assertEqual(self.client_class().get(reverse('ventas:autorizar_a_distancia', args=['x' * 40])).status_code, 404)
        self.entrar(self.cajera)
        r, _pin, _ = self.pedir_a_distancia()
        otra = f.usuario('40100009', 'Operador', nombre='Otra')
        self.entrar(otra)
        self.assertEqual(self.client.get(reverse('ventas:estado_autorizacion', args=[r['id']])).status_code, 404)

    @override_settings(CAFETO_N8N_WEBHOOK_AUTORIZACION='http://n8n:5678/webhook/cafeto-autorizacion')
    def test_avisa_a_n8n_con_el_pin_y_el_enlace(self):
        self.entrar(self.cajera)
        with mock.patch('ventas.a_distancia._webhook') as webhook:
            r, pin, enlace = self.pedir_a_distancia()
        url, datos = webhook.call_args.args
        self.assertEqual(url, 'http://n8n:5678/webhook/cafeto-autorizacion')
        self.assertEqual(datos['pin'], pin)
        self.assertTrue(datos['enlace'].endswith(enlace))
        self.assertIn('WhatsApp', r['avisos'])


# ===========================================================================
#  El enlace abierto en un navegador con una sesión (DD-104)
# ===========================================================================
class SesionEnElNavegadorTest(Pantalla):

    def test_el_enlace_de_la_titular_lleva_su_numero_y_el_token_suelto_no_sirve(self):
        self.entrar(self.cajera)
        _r, _pin, enlace = self.pedir_a_distancia()
        self.assertIn(f'.{self.duenia.pk}.', enlace)
        suelto = enlace.split('.')[0] + '/'
        self.assertEqual(self.client_class().get(suelto).status_code, 404)
        pagina = self.client_class().get(enlace)
        self.assertContains(pagina, f'¿Autorizás, {self.duenia.nombre} {self.duenia.apellido}?')
        self.assertContains(pagina, 'Contestás como')

    def test_con_la_sesion_de_otra_persona_pide_la_clave_o_el_pin(self):
        self.entrar(self.cajera)
        r, pin, enlace = self.pedir_a_distancia()
        # La cajera abre en SU navegador el enlace de la titular.
        pagina = self.client.get(enlace)
        self.assertContains(pagina, 'está abierta la sesión de')
        self.assertContains(pagina, f'{self.cajera.nombre} {self.cajera.apellido}')
        self.assertContains(pagina, 'name="confirmacion"')
        sin_nada = self.client.post(enlace, {'accion': 'aprobar'})
        self.assertContains(sin_nada, 'poné tu clave o el PIN')
        malo = '0000' if pin != '0000' else '1111'
        mal = self.client.post(enlace, {'accion': 'aprobar', 'confirmacion': malo})
        self.assertContains(mal, 'Te quedan 4')
        self.assertEqual(SolicitudAutorizacion.objects.get().estado_solicitud, 'PENDIENTE')
        bien = self.client.post(enlace, {'accion': 'aprobar', 'confirmacion': pin})
        self.assertContains(bien, 'Listo, autorizado.')
        self.assertContains(bien, 'puede cobrar el ticket')
        solicitud = SolicitudAutorizacion.objects.get()
        self.assertEqual((solicitud.estado_solicitud, solicitud.id_usuario_autorizante, solicitud.via_aprobacion),
                         ('APROBADA', self.duenia, 'ENLACE'))
        # La sesión de la cajera sigue: la caja no se traba.
        estado = self.client.get(reverse('ventas:estado_autorizacion', args=[r['id']]))
        self.assertEqual(estado.status_code, 200)

    def test_con_la_clave_de_quien_aprueba_y_sin_tocar_la_sesion_abierta(self):
        from seguridad.models import IntentoAcceso, Sesion
        self.entrar(self.cajera)
        self.pedir_a_distancia()
        correo = next(m for m in reversed(mail.outbox) if self.admin.correo in m.to)
        enlace = [linea for linea in correo.body.splitlines() if '/ventas/autorizar/' in linea][0].strip()
        enlace = enlace[enlace.index('/ventas/'):]
        mal = self.client.post(enlace, {'accion': 'aprobar', 'confirmacion': 'no-es-la-clave'})
        self.assertContains(mal, 'La clave no es correcta')
        self.assertTrue(IntentoAcceso.objects.filter(id_usuario=self.admin, motivo_rechazo='CLAVE_INCORRECTA').exists())
        self.client.post(enlace, {'accion': 'rechazar', 'confirmacion': f.CLAVE})
        solicitud = SolicitudAutorizacion.objects.get()
        self.assertEqual(solicitud.estado_solicitud, 'RECHAZADA')
        self.assertTrue(Sesion.objects.filter(id_usuario=self.cajera, activa=True).exists())

    def test_cinco_errores_anulan_el_pedido(self):
        self.entrar(self.cajera)
        _r, pin, enlace = self.pedir_a_distancia()
        malo = '0000' if pin != '0000' else '1111'
        for _ in range(5):
            ultimo = self.client.post(enlace, {'accion': 'aprobar', 'confirmacion': malo})
        self.assertContains(ultimo, 'se anuló')
        self.assertEqual(SolicitudAutorizacion.objects.get().estado_solicitud, 'ANULADA')
        tarde = self.client.post(enlace, {'accion': 'aprobar', 'confirmacion': pin})
        self.assertEqual(SolicitudAutorizacion.objects.get().estado_solicitud, 'ANULADA')
        self.assertNotContains(tarde, 'Listo, autorizado.')

    def test_con_la_sesion_de_quien_aprueba_no_pide_nada_ni_la_cierra(self):
        from seguridad.models import Sesion
        self.entrar(self.cajera)
        _r, _pin, enlace = self.pedir_a_distancia()
        suya = self.client_class()
        f.entrar(suya, self.duenia)
        pagina = suya.get(enlace)
        self.assertNotContains(pagina, 'name="confirmacion"')
        suya.post(enlace, {'accion': 'aprobar'})
        self.assertEqual(SolicitudAutorizacion.objects.get().estado_solicitud, 'APROBADA')
        self.assertTrue(Sesion.objects.filter(id_usuario=self.duenia, activa=True).exists())
        self.assertEqual(suya.get(reverse('ventas:autorizaciones')).status_code, 200)

    def test_lo_que_sigue_depende_de_lo_que_se_pidio(self):
        self.entrar(self.cajera)
        r, _pin, _enlace = self.pedir_a_distancia()
        solicitud = SolicitudAutorizacion.objects.get(pk=r['id'])
        self.assertEqual(a_distancia.que_sigue(solicitud), 'cobrar el ticket')
        solicitud.pedido = dict(solicitud.pedido, tipo=a_distancia.AJUSTE)
        self.assertEqual(a_distancia.que_sigue(solicitud), 'cargar el stock del producto')
        solicitud.pedido = dict(solicitud.pedido, tipo=a_distancia.CIERRE)
        self.assertEqual(a_distancia.que_sigue(solicitud), 'terminar el cierre de la caja')
        self.entrar(self.admin)
        respuesta = self.client.post(reverse('ventas:autorizaciones'), {'solicitud': r['id'], 'accion': 'aprobar'},
                                     follow=True)
        self.assertContains(respuesta, 'Aprobado: la caja ya puede cobrar el ticket.')


# ===========================================================================
#  Delegación: aprueba la encargada con SU enlace o SU PIN; y el ajuste de
#  stock desde el mostrador también se puede pedir a distancia.
# ===========================================================================
class DelegacionTest(Pantalla):

    def correo_de(self, usuario):
        correo = next(m for m in reversed(mail.outbox) if usuario.correo in m.to)
        enlace = [linea for linea in correo.body.splitlines() if '/ventas/autorizar/' in linea][0].strip()
        return correo.subject.rsplit('PIN ', 1)[1], enlace[enlace.index('/ventas/'):]

    def test_la_encargada_aprueba_con_su_enlace(self):
        self.entrar(self.cajera)
        self.pedir_a_distancia()
        _pin, enlace = self.correo_de(self.admin)
        self.client_class().post(enlace, {'accion': 'aprobar'})
        solicitud = SolicitudAutorizacion.objects.get()
        self.assertEqual((solicitud.estado_solicitud, solicitud.id_usuario_autorizante), ('APROBADA', self.admin))

    def test_un_enlace_con_la_firma_tocada_no_sirve(self):
        self.entrar(self.cajera)
        self.pedir_a_distancia()
        _pin, enlace = self.correo_de(self.admin)
        sin_barra = enlace.rstrip('/')
        trucho = sin_barra[:-2] + ('00' if not sin_barra.endswith('00') else '11') + '/'
        self.assertEqual(self.client_class().get(trucho).status_code, 404)
        otro = enlace.replace(f'.{self.admin.pk}.', f'.{self.duenia.pk}.')
        self.assertEqual(self.client_class().post(otro, {'accion': 'aprobar'}).status_code, 404)
        self.assertEqual(SolicitudAutorizacion.objects.get().estado_solicitud, 'PENDIENTE')

    def test_el_pin_de_la_encargada_aprueba_en_su_nombre(self):
        self.entrar(self.cajera)
        r, _pin, _enlace = self.pedir_a_distancia()
        su_pin, _ = self.correo_de(self.admin)
        respuesta = self.pedir('pin_autorizacion', {'pin': su_pin}, r['id'])
        self.assertTrue(respuesta['ok'], respuesta)
        self.assertEqual(SolicitudAutorizacion.objects.get().id_usuario_autorizante, self.admin)

    def test_a_quien_no_tiene_el_permiso_no_le_llega(self):
        f.usuario('40100009', 'Operador', nombre='Otra')
        self.entrar(self.cajera)
        self.pedir_a_distancia()
        self.assertNotIn('40100009@cafeto.test', {m.to[0] for m in mail.outbox})

    def test_con_un_conteo_abierto_el_ajuste_no_se_pide_ni_se_hace(self):
        """``uq_un_conteo_abierto`` deja UN conteo abierto a la vez.

        Antes el pedido salía igual: a la titular le llegaba el WhatsApp,
        lo aprobaba, y al ejecutarlo el ajuste fallaba con «ya hay un
        conteo abierto». La cajera quedaba esperando una autorización que
        ya estaba dada y que no servía para nada. Ahora se mira ANTES de
        pedir, en el mismo control que usa el ajuste directo.
        """
        from stock import servicios as stock
        from stock.models import MotivoAjuste

        yerba = f.producto('Yerba', prov=self.prov, precio='3000', costo='2000')
        conteo = stock.iniciar_conteo(self.admin, MotivoAjuste.DIFERENCIA_CONTEO)
        self.entrar(self.cajera)
        pedido = self.pedir('pedir_autorizacion',
                            {'tipo': 'ajuste', 'producto': yerba.pk, 'cantidad': '4'})
        self.assertFalse(pedido['ok'], pedido)
        self.assertIn('conteo de stock abierto', pedido['error'])
        self.assertFalse(SolicitudAutorizacion.objects.exists(),
                         'no se pide una autorización que no se va a poder usar')
        # El ajuste directo (sin pedir nada) lo rechaza por lo mismo.
        directo = self.pedir('ajuste_express', {'producto': yerba.pk, 'cantidad': '4'})
        self.assertFalse(directo['ok'])
        self.assertIn('conteo de stock abierto', directo['error'])
        # Resuelto el conteo, el pedido sale normalmente. Un conteo se
        # envía con al menos un renglón cargado (es lo que se contó).
        stock.cargar_conteo(conteo, self.alfajor, D('50'))
        stock.enviar_conteo(conteo)
        # Lo aprueba otra persona: quien carga un conteo no lo aprueba.
        stock.aprobar(conteo, self.duenia, 'Listo')
        self.entrar(self.cajera)
        with self.captureOnCommitCallbacks(execute=True):
            otra_vez = self.pedir('pedir_autorizacion',
                                  {'tipo': 'ajuste', 'producto': yerba.pk, 'cantidad': '4'})
        self.assertTrue(otra_vez['ok'], otra_vez)

    def test_el_ajuste_desde_el_mostrador_se_pide_a_distancia(self):
        yerba = f.producto('Yerba', prov=self.prov, precio='3000', costo='2000')
        self.entrar(self.cajera)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.pedir('pedir_autorizacion', {'tipo': 'ajuste', 'producto': yerba.pk, 'cantidad': '4'})
        self.assertTrue(r['ok'], r)
        solicitud = SolicitudAutorizacion.objects.get()
        self.assertIn('Yerba', solicitud.motivo_solicitud)
        # Sin aprobación, el ajuste no pasa.
        sin = self.pedir('ajuste_express', {'producto': yerba.pk, 'cantidad': '4', 'remota': solicitud.pk})
        self.assertFalse(sin['ok'])
        # La encargada lo aprueba desde la bandeja.
        f.entrar(self.client, self.admin)
        self.client.post(reverse('ventas:autorizaciones'), {'solicitud': solicitud.pk, 'accion': 'aprobar'})
        self.entrar(self.cajera)
        # Otra cantidad no sirve: lo aprobado fue «4».
        otra = self.pedir('ajuste_express', {'producto': yerba.pk, 'cantidad': '9', 'remota': solicitud.pk})
        self.assertFalse(otra['ok'])
        hecho = self.pedir('ajuste_express', {'producto': yerba.pk, 'cantidad': '4', 'remota': solicitud.pk})
        self.assertTrue(hecho['ok'], hecho)
        yerba.refresh_from_db()
        solicitud.refresh_from_db()
        self.assertEqual(yerba.stock_actual, D('4'))
        self.assertEqual((solicitud.estado_solicitud, solicitud.id_venta_id, solicitud.id_usuario_autorizante),
                         ('USADA', None, self.admin))
