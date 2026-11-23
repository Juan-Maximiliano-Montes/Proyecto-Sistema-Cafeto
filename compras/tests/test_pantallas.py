"""Las pantallas de Compras: recepción, órdenes y pagos."""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from compras.models import Compra, Egreso, OrdenCompra, TipoEgreso
from nucleo.tests import fabrica as f

D = Decimal


class Base(TestCase):

    def setUp(self):
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.prov = f.proveedor()
        self.gaseosa = f.producto('Gaseosa', prov=self.prov)
        self.yogur = f.producto('Yogur', prov=self.prov, controla_vencimiento=True)
        f.entrar(self.client, self.encargada)

    def token(self, nombre):
        return self.client.get(reverse(nombre)).context['token']


class RecepcionTest(Base):

    def recibir(self, renglones, **extra):
        datos = {'token_operacion': self.token('compras:recepcion'), 'proveedor': self.prov.pk,
                 'tipo_comprobante': 'FACTURA_A', 'punto_venta': '1', 'numero': '123',
                 'renglones': json.dumps(renglones), **extra}
        return self.client.post(reverse('compras:recepcion'), datos)

    def test_entra_el_stock_con_sus_lotes(self):
        self.recibir([
            {'producto': self.gaseosa.pk, 'cantidad': '12', 'costo': '700'},
            {'producto': self.yogur.pk, 'cantidad': '6', 'costo': '400,50',
             'lotes': [{'vence': f.en_dias(10).isoformat(), 'cantidad': ''}]}])
        compra = Compra.objects.get()
        self.assertEqual(compra.total_compra, D('10803'))
        self.yogur.refresh_from_db()
        self.assertEqual(self.yogur.stock_actual, 6)
        self.assertEqual(self.yogur.lotes.get().cantidad_inicial, 6)

    def test_un_error_no_pierde_lo_cargado(self):
        renglones = [{'producto': self.yogur.pk, 'cantidad': '6', 'costo': '400', 'lotes': []}]
        respuesta = self.recibir(renglones)
        self.assertIn('lote', respuesta.context['error'])
        self.assertEqual(json.loads(respuesta.context['renglones_json']), renglones)
        self.assertFalse(Compra.objects.exists())

    def test_la_misma_factura_no_entra_dos_veces(self):
        renglon = [{'producto': self.gaseosa.pk, 'cantidad': '1', 'costo': '700'}]
        self.recibir(renglon)
        respuesta = self.recibir(renglon)
        self.assertIn('ya está cargada', respuesta.context['error'])
        self.assertEqual(Compra.objects.count(), 1)


class CompraInformalTest(Base):
    """El ticket de la verdulería y la bolsa de carbón del kiosco.

    Esos papeles traen un número suelto y nada más: no tienen punto de
    venta impreso (eso es de los comprobantes de ARCA). La base igual
    exige ``punto_venta >= 1`` (``ck_compras_comprobante``), así que la
    carga quedaba trabada en un campo que no existe en el papel. Para esos
    tipos se asume el 1, que es lo que se venía cargando a mano.
    """

    def recibir(self, tipo, punto='', numero='4321'):
        return self.client.post(reverse('compras:recepcion'), {
            'token_operacion': self.token('compras:recepcion'),
            'proveedor': self.prov.pk, 'tipo_comprobante': tipo,
            'punto_venta': punto, 'numero': numero,
            'renglones': json.dumps([
                {'producto': self.gaseosa.pk, 'cantidad': '3', 'costo': '700'}])})

    def test_un_ticket_sin_punto_de_venta_entra_con_el_1(self):
        # Un número distinto por tipo: en Compras la baja es lógica
        # (RN-8.1.1), así que entre casos no se borra nada, y el
        # comprobante es único por proveedor + tipo + punto + número.
        for numero, tipo in enumerate(('TICKET', 'OTRO', 'REMITO'), start=4321):
            with self.subTest(tipo=tipo):
                respuesta = self.recibir(tipo, numero=str(numero))
                self.assertEqual(respuesta.status_code, 302,
                                 getattr(respuesta, 'context', None) and respuesta.context.get('error'))
                compra = Compra.objects.get(tipo_comprobante=tipo)
                self.assertEqual(compra.punto_venta, 1)
                self.assertEqual(compra.numero_formateado, f'0001-{numero:08d}')

    def test_una_factura_de_ARCA_sigue_pidiendo_el_punto_de_venta(self):
        """Ahí el punto de venta ESTÁ impreso y es parte de la identidad
        del comprobante: no se inventa."""
        respuesta = self.recibir('FACTURA_A')
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('punto de venta', respuesta.context['error'])
        self.assertFalse(Compra.objects.exists())

    def test_el_punto_de_venta_que_se_escribe_se_respeta(self):
        self.recibir('TICKET', punto='7')
        self.assertEqual(Compra.objects.get().punto_venta, 7)

    def test_la_pantalla_le_dice_al_navegador_para_qué_tipos_es(self):
        html = self.client.get(reverse('compras:recepcion')).content.decode()
        control = html[html.index('id="punto"') - 250:html.index('id="punto"') + 400]
        self.assertIn('data-punto-por-defecto="1"', control)
        self.assertIn('data-tipos-sin-punto="TICKET,OTRO,REMITO"', control)


class OrdenesTest(Base):

    def test_de_la_orden_a_la_recepcion(self):
        self.client.post(reverse('compras:nueva_orden'), {
            'token_operacion': self.token('compras:nueva_orden'), 'proveedor': self.prov.pk,
            'renglones': json.dumps([{'producto': self.gaseosa.pk, 'cantidad': '24', 'costo': '650'}])})
        orden = OrdenCompra.objects.get()
        self.assertEqual(orden.total_orden, D('15600'))
        self.client.post(reverse('compras:estado_orden', args=[orden.pk]), {'accion': 'enviar'})
        respuesta = self.client.get(reverse('compras:recepcion'), {'orden': orden.pk})
        precargados = json.loads(respuesta.context['renglones_json'])
        self.assertEqual(precargados[0]['producto'], self.gaseosa.pk)
        pdf = self.client.get(reverse('compras:orden_pdf', args=[orden.pk]))
        self.assertEqual(pdf['Content-Type'], 'application/pdf')


class EgresosTest(Base):

    def test_anotar_y_pagar_una_obligacion(self):
        tipo = TipoEgreso.objects.get(nombre_tipo_egreso='Gastos varios')
        token = self.token('compras:egresos')
        self.client.post(reverse('compras:registrar_egreso'), {
            'token_operacion': token, 'modo': 'anotar', 'tipo': tipo.pk,
            'monto': '15.000', 'concepto': 'Luz', 'destinatario': 'EDESA',
            'vence': f.en_dias(5).isoformat()})
        egreso = Egreso.objects.get()
        self.assertEqual(egreso.monto, D('15000'))
        self.assertEqual(egreso.estado_pago, 'PENDIENTE_PAGO')
        self.client.post(reverse('compras:pagar_egreso', args=[egreso.pk]), {
            'token_operacion': self.token('compras:egresos'), 'medio': f.medio('Transferencia').pk})
        egreso.refresh_from_db()
        self.assertEqual(egreso.estado_pago, 'PENDIENTE_ACREDITACION')
        self.client.post(reverse('compras:conciliar_egreso', args=[egreso.pk]), {
            'resultado': 'acreditado', 'referencia': 'OP-1'})
        egreso.refresh_from_db()
        self.assertEqual(egreso.estado_pago, 'ACREDITADO')

    def test_el_gasto_de_caja_sin_turno_se_rechaza(self):
        tipo = TipoEgreso.objects.get(nombre_tipo_egreso='Gastos varios')
        respuesta = self.client.post(reverse('compras:registrar_egreso'), {
            'token_operacion': self.token('compras:egresos'), 'modo': 'caja', 'tipo': tipo.pk,
            'monto': '1500', 'concepto': 'Hielo'}, follow=True)
        self.assertContains(respuesta, 'cajón')
        self.assertFalse(Egreso.objects.exists())

    def test_pantallas_cargan(self):
        for nombre in ('compras:compras', 'compras:recepcion', 'compras:ordenes',
                       'compras:nueva_orden', 'compras:egresos'):
            with self.subTest(pantalla=nombre):
                self.assertEqual(self.client.get(reverse(nombre)).status_code, 200)
