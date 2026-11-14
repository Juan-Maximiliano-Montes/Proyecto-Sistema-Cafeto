"""Las pantallas de Stock: alertas, conteo ciego, mermas y producción."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from nucleo.tests import fabrica as f
from stock.models import AjusteStock, EstadoAjuste

D = Decimal


class Base(TestCase):

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.prov = f.proveedor()
        self.yogur = f.producto('Yogur', prov=self.prov, controla_vencimiento=True,
                                stock_minimo=D('5'))
        f.comprar(self.encargada, self.prov, (self.yogur, 4, '500', [(f.en_dias(2), 4)]))


class AlertasTest(Base):

    def test_bajo_minimo_y_por_vencer(self):
        f.entrar(self.client, self.operador)
        faltantes = self.client.get(reverse('stock:alertas'))
        self.assertContains(faltantes, 'Yogur')
        vencimientos = self.client.get(reverse('stock:alertas'), {'ver': 'vencimientos'})
        self.assertContains(vencimientos, '2 días')

    def test_las_etiquetas_salen_en_pdf(self):
        f.entrar(self.client, self.operador)
        respuesta = self.client.get(reverse('stock:etiquetas_oferta'))
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')

    def test_lotes_de_un_producto(self):
        f.entrar(self.client, self.operador)
        datos = self.client.get(reverse('stock:lotes_de_producto'),
                                {'producto': self.yogur.pk}).json()
        self.assertTrue(datos['controla'])
        self.assertEqual(len(datos['lotes']), 1)


class ConteoCiegoTest(Base):

    def test_quien_cuenta_no_ve_el_teorico(self):
        f.entrar(self.client, self.operador)
        self.client.post(reverse('stock:conteo'), {'accion': 'empezar'})
        lote = self.yogur.lotes.get()
        self.client.post(reverse('stock:conteo'), {
            'accion': 'cargar', 'producto': self.yogur.pk, 'lote': lote.pk,
            'contada': '3'})
        self.client.post(reverse('stock:conteo'), {'accion': 'enviar'})
        conteo = AjusteStock.objects.get()
        self.assertEqual(conteo.estado_ajuste, EstadoAjuste.PENDIENTE)
        ficha = self.client.get(reverse('stock:ajuste', args=[conteo.pk]))
        self.assertFalse(ficha.context['ve_diferencias'])
        self.assertNotContains(ficha, '>Sistema<')

        # La encargada ve la diferencia y aprueba: recién ahí cambia el stock.
        self.client.logout()
        f.entrar(self.client, self.encargada)
        ficha = self.client.get(reverse('stock:ajuste', args=[conteo.pk]))
        self.assertContains(ficha, '>Sistema<')
        self.client.post(reverse('stock:resolver', args=[conteo.pk]), {
            'decision': 'aprobar', 'token_operacion': ficha.context['token']})
        self.yogur.refresh_from_db()
        self.assertEqual(self.yogur.stock_actual, D('3'))

    def test_el_conteo_acepta_coma_decimal_en_lo_que_se_pesa(self):
        queso = f.producto('Queso', prov=self.prov, fraccionable=True, unidad='KG')
        f.entrar(self.client, self.operador)
        self.client.post(reverse('stock:conteo'), {'accion': 'empezar'})
        self.client.post(reverse('stock:conteo'), {
            'accion': 'cargar', 'producto': queso.pk, 'contada': '1,250'})
        self.assertEqual(AjusteStock.objects.get().detalles.get().cantidad_contada, D('1.250'))


class MermaTest(Base):

    def test_la_merma_espera_autorizacion(self):
        f.entrar(self.client, self.operador)
        token = self.client.get(reverse('stock:merma')).context['token']
        self.client.post(reverse('stock:merma'), {
            'token_operacion': token, 'motivo': 'ROTURA',
            'producto_0': self.yogur.pk, 'cantidad_0': '1'})
        merma = AjusteStock.objects.get()
        self.assertEqual(merma.estado_ajuste, EstadoAjuste.PENDIENTE)
        self.yogur.refresh_from_db()
        self.assertEqual(self.yogur.stock_actual, D('4'))

    def test_un_doble_envio_carga_una_sola_merma(self):
        # "Aprobarla ya" es de la dueña: la que carga y aprueba en un paso
        # es la titular (DD-67, DD-80).
        f.entrar(self.client, f.titular())
        token = self.client.get(reverse('stock:merma')).context['token']
        datos = {'token_operacion': token, 'motivo': 'ROTURA', 'aprobar_ya': '1',
                 'producto_0': self.yogur.pk, 'cantidad_0': '1'}
        self.client.post(reverse('stock:merma'), datos)
        self.client.post(reverse('stock:merma'), datos)
        self.assertEqual(AjusteStock.objects.count(), 1)
        self.yogur.refresh_from_db()
        self.assertEqual(self.yogur.stock_actual, D('3'))

    def test_un_error_conserva_lo_cargado(self):
        f.entrar(self.client, self.operador)
        token = self.client.get(reverse('stock:merma')).context['token']
        respuesta = self.client.post(reverse('stock:merma'), {
            'token_operacion': token, 'motivo': 'OTRO',
            'producto_0': self.yogur.pk, 'cantidad_0': '1'})
        self.assertIn('detalle', respuesta.context['error'].lower())
        self.assertEqual(respuesta.context['elegidos'][0]['producto'], self.yogur)


class PantallasTest(Base):

    def test_cargan(self):
        f.entrar(self.client, self.encargada)
        for nombre in ('alertas', 'ajustes', 'conteo', 'merma', 'traspaso'):
            with self.subTest(pantalla=nombre):
                self.assertEqual(self.client.get(reverse(f'stock:{nombre}')).status_code, 200)

    def test_produccion_pide_su_permiso(self):
        f.entrar(self.client, self.operador)
        self.assertEqual(self.client.get(reverse('stock:producciones')).status_code, 200)
