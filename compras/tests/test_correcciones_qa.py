"""Correcciones de la prueba integral del 04/11/2026 (Compras).

    AcreedorTest       un pago sin "a quién" es un aviso, no un error 500
    DiferenciasTest    la recepción con faltante no da la orden por completa
    FotosTest          las operaciones de compras dejan antes y después
"""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from compras.models import Egreso, OrdenCompra, TipoEgreso
from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria

D = Decimal


class Base(TestCase):

    def setUp(self):
        self.admin = f.usuario('40000003', 'Administrador', nombre='Ana')
        self.prov = f.proveedor()
        self.gaseosa = f.producto('Gaseosa', prov=self.prov)
        self.agua = f.producto('Agua', prov=self.prov)
        self.tipo = TipoEgreso.objects.get(nombre_tipo_egreso='Gastos varios')
        f.entrar(self.client, self.admin)

    def token(self, nombre):
        return self.client.get(reverse(nombre)).context['token']

    def asiento(self, operacion):
        return LogAuditoria.objects.filter(operacion_realizada=operacion).latest('pk')


# ===========================================================================
class AcreedorTest(Base):

    def registrar(self, **datos):
        return self.client.post(reverse('compras:registrar_egreso'), {
            'token_operacion': self.token('compras:egresos'), 'tipo': self.tipo.pk,
            'monto': '700', 'concepto': 'Fotocopias', **datos}, follow=True)

    def test_sin_proveedor_ni_destinatario_avisa(self):
        for modo in ('ahora', 'anotar'):
            with self.subTest(modo=modo):
                respuesta = self.registrar(
                    modo=modo, medio=f.medio('Transferencia').pk,
                    vence=f.en_dias(3).isoformat())
                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(respuesta, 'Indicá a quién se le paga')
        self.assertFalse(Egreso.objects.exists())

    def test_proveedor_y_destinatario_a_la_vez_avisa(self):
        respuesta = self.registrar(modo='anotar', proveedor=self.prov.pk,
                                   destinatario='Otro', vence=f.en_dias(3).isoformat())
        self.assertContains(respuesta, 'no a los dos')
        self.assertFalse(Egreso.objects.exists())

    def test_el_gasto_de_caja_sin_destinatario_tambien(self):
        f.abrir_turno(self.admin, f.caja_minisuper())
        self.client.post(reverse('ventas:elegir_caja'), {'caja': f.caja_minisuper().pk})
        respuesta = self.registrar(modo='caja')
        self.assertContains(respuesta, 'Indicá a quién se le paga')
        self.assertFalse(Egreso.objects.exists())


# ===========================================================================
class DiferenciasTest(Base):

    def orden_enviada(self, *renglones):
        self.client.post(reverse('compras:nueva_orden'), {
            'token_operacion': self.token('compras:nueva_orden'), 'proveedor': self.prov.pk,
            'renglones': json.dumps([{'producto': p.pk, 'cantidad': str(c), 'costo': '650'}
                                     for p, c in renglones])})
        orden = OrdenCompra.objects.latest('pk')
        self.client.post(reverse('compras:estado_orden', args=[orden.pk]), {'accion': 'enviar'})
        return orden

    def recibir(self, orden, numero, *renglones):
        return self.client.post(reverse('compras:recepcion') + f'?orden={orden.pk}', {
            'token_operacion': self.token('compras:recepcion'), 'proveedor': self.prov.pk,
            'orden': orden.pk, 'tipo_comprobante': 'FACTURA_A', 'punto_venta': '1',
            'numero': str(numero),
            'renglones': json.dumps([{'producto': p.pk, 'cantidad': str(c), 'costo': '650'}
                                     for p, c in renglones])}, follow=True)

    def test_completa_queda_recepcionada(self):
        orden = self.orden_enviada((self.gaseosa, 24))
        self.recibir(orden, 1, (self.gaseosa, 24))
        orden.refresh_from_db()
        self.assertEqual(orden.estado_orden, 'RECEPCIONADA')

    def test_con_faltante_queda_con_diferencias_y_lo_avisa(self):
        orden = self.orden_enviada((self.gaseosa, 24), (self.agua, 6))
        respuesta = self.recibir(orden, 1, (self.gaseosa, 20), (self.agua, 6))
        orden.refresh_from_db()
        self.assertEqual(orden.estado_orden, 'CON_DIFERENCIAS')
        self.assertContains(respuesta, 'faltan 4')
        fila = next(x for x in orden.diferencias() if x['producto'] == self.gaseosa)
        self.assertEqual(fila['diferencia'], D('-4'))
        # La planilla de la segunda entrega trae sólo lo que falta.
        planilla = self.client.get(reverse('compras:recepcion'), {'orden': orden.pk})
        precargado = json.loads(planilla.context['renglones_json'])
        self.assertEqual([(r['producto'], r['cantidad']) for r in precargado],
                         [(self.gaseosa.pk, '4')])

    def test_llega_el_resto_y_se_completa(self):
        orden = self.orden_enviada((self.gaseosa, 24))
        self.recibir(orden, 1, (self.gaseosa, 20))
        self.recibir(orden, 2, (self.gaseosa, 4))
        orden.refresh_from_db()
        self.assertEqual(orden.estado_orden, 'RECEPCIONADA')
        self.gaseosa.refresh_from_db()
        self.assertEqual(self.gaseosa.stock_actual, 24)

    def test_lo_no_pedido_tambien_es_diferencia(self):
        orden = self.orden_enviada((self.gaseosa, 24))
        self.recibir(orden, 1, (self.gaseosa, 24), (self.agua, 2))
        orden.refresh_from_db()
        self.assertEqual(orden.estado_orden, 'CON_DIFERENCIAS')

    def test_se_cierra_con_lo_acordado(self):
        orden = self.orden_enviada((self.gaseosa, 24))
        self.recibir(orden, 1, (self.gaseosa, 20))
        url = reverse('compras:estado_orden', args=[orden.pk])
        self.client.post(url, {'accion': 'cerrar_diferencias', 'observacion': ''})
        orden.refresh_from_db()
        self.assertEqual(orden.estado_orden, 'CON_DIFERENCIAS')   # sin explicación, no
        self.client.post(url, {'accion': 'cerrar_diferencias',
                               'observacion': 'Manda nota de crédito por 4'})
        orden.refresh_from_db()
        self.assertEqual(orden.estado_orden, 'RECEPCIONADA')
        asiento = self.asiento('CIERRE CON DIFERENCIAS DE ORDEN DE COMPRA')
        self.assertEqual(asiento.registro_anterior['estado_orden'], 'CON_DIFERENCIAS')
        self.assertEqual(asiento.registro_nuevo['estado_orden'], 'RECEPCIONADA')
        self.assertIn('nota de crédito', asiento.detalle_operacion)


# ===========================================================================
class FotosTest(Base):

    def test_alta_y_pago_de_un_egreso(self):
        self.client.post(reverse('compras:registrar_egreso'), {
            'token_operacion': self.token('compras:egresos'), 'modo': 'anotar',
            'tipo': self.tipo.pk, 'monto': '15000', 'concepto': 'Luz',
            'destinatario': 'EDESA', 'vence': f.en_dias(5).isoformat()})
        alta = self.asiento('ALTA DE EGRESO')
        self.assertIsNone(alta.registro_anterior)
        self.assertEqual(alta.registro_nuevo['estado_pago'], 'PENDIENTE_PAGO')
        egreso = Egreso.objects.get()
        self.client.post(reverse('compras:pagar_egreso', args=[egreso.pk]), {
            'token_operacion': self.token('compras:egresos'),
            'medio': f.medio('Transferencia').pk})
        pago = self.asiento('PAGO DE EGRESO')
        self.assertEqual(pago.registro_anterior['estado_pago'], 'PENDIENTE_PAGO')
        self.assertEqual(pago.registro_nuevo['estado_pago'], 'PENDIENTE_ACREDITACION')

    def test_la_orden_y_su_envio(self):
        DiferenciasTest.orden_enviada(self, (self.gaseosa, 24))
        alta = self.asiento('ALTA DE ORDEN DE COMPRA')
        self.assertEqual(len(alta.registro_nuevo['renglones']), 1)
        envio = self.asiento('ENVIO DE ORDEN DE COMPRA')
        self.assertEqual((envio.registro_anterior['estado_orden'],
                          envio.registro_nuevo['estado_orden']), ('PENDIENTE', 'EMITIDA'))
