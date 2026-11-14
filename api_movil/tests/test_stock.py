"""Lo del equipo: consultar un producto y las alertas de stock y vencimiento.

    ConsultaTest    por código exacto (el escáner) o por texto, insumos
                    incluidos, con su stock, su próximo vencimiento y sus
                    alertas
    AlertasTest     las cuatro alertas, en orden de gravedad, filtradas por
                    tipo, y las mismas que muestra la pestaña Alertas de la web
"""
from api_movil.alertas import DE_STOCK, DE_VENCIMIENTO
from api_movil.tests.base import D, BaseCatalogo
from negocio.models import ParametrosNegocio
from nucleo.tests import fabrica as f
from stock import servicios
from stock.models import Lote, Producto


# ===========================================================================
class ConsultaTest(BaseCatalogo):

    def consultar(self, **parametros):
        respuesta = self.pedir('get', '/stock/productos/', parametros, como=self.operador)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.json()['resultados']

    def test_por_codigo_de_barras_exacto(self):
        self.assertEqual([p['id'] for p in self.consultar(codigo='7790895000782')],
                         [self.gaseosa.pk])
        self.assertEqual(self.consultar(codigo='779089500078'), [])

    def test_por_texto_tambien_busca_el_codigo(self):
        self.assertEqual([p['id'] for p in self.consultar(q='77908950')],
                         [self.gaseosa.pk])

    def test_incluye_insumos_con_su_stock(self):
        grano = self.consultar(q='grano')[0]
        self.assertEqual((grano['id'], grano['tipo']), (self.grano.pk, 'INSUMO'))
        self.assertEqual(grano['stock_actual'], 5)
        # Un insumo se usa por peso: la app acepta 0,018 kg.
        self.assertEqual((grano['unidad'], grano['cantidad_entera']), ('KG', False))
        self.assertIsNone(grano['seccion'])
        self.assertIsNone(grano['precio_oferta'])

    def test_lo_que_no_tiene_stock_propio_va_sin_stock(self):
        cafe = self.consultar(q='cafe con leche')[0]
        self.assertEqual(cafe['id'], self.cafe.pk)
        self.assertIsNone(cafe['stock_actual'])
        self.assertIsNone(cafe['stock_minimo'])
        self.assertIsNone(cafe['proximo_vencimiento'])
        self.assertEqual(cafe['alertas'], [])

    def test_proximo_vencimiento_y_alertas_del_producto(self):
        Producto.objects.filter(pk=self.yogur.pk).update(stock_minimo=D('20'))
        Lote.objects.filter(id_producto=self.yogur, fecha_vencimiento=f.en_dias(3)).update(
            fecha_vencimiento=f.en_dias(-1))
        yogur = self.consultar(q='yogur')[0]
        self.assertEqual(yogur['codigo_barras'], None)
        self.assertEqual((yogur['stock_actual'], yogur['stock_minimo']), (10, 20))
        self.assertTrue(yogur['controla_vencimiento'])
        # El vencido no sale: el próximo es el de 30 días.
        self.assertEqual(yogur['proximo_vencimiento'], {
            'fecha': f.en_dias(30).isoformat(), 'dias': 30, 'cantidad': 6, 'lote': 'L2'})
        self.assertEqual(yogur['alertas'], ['VENCIDO', 'STOCK_BAJO'])

    def test_de_a_20(self):
        for n in range(25):
            f.producto(f'Galletita {n:02d}', prov=self.prov, precio='500')
        primera = self.pedir('get', '/stock/productos/', {'q': 'galletita'},
                             como=self.operador).json()
        segunda = self.pedir('get', '/stock/productos/', {'q': 'galletita', 'pagina': 2},
                             como=self.operador).json()
        self.assertEqual((len(primera['resultados']), primera['hay_mas']), (20, True))
        self.assertEqual((len(segunda['resultados']), segunda['hay_mas']), (5, False))

    def test_pide_ver_productos(self):
        respuesta = self.pedir('get', '/stock/productos/', como=self.usuario_sin_permisos())
        self.assertEqual(respuesta.status_code, 403)


# ===========================================================================
class AlertasTest(BaseCatalogo):
    """Una de cada: el queso venció, el agua no tiene, el yogur vence en 3
    días (y con la rebaja del rubro va a ofertas) y la gaseosa está baja."""

    def setUp(self):
        super().setUp()
        self.agua = f.producto('Agua mineral', prov=self.prov, stock_minimo=D('5'))
        Producto.objects.filter(pk=self.gaseosa.pk).update(stock_minimo=D('15'))
        self.queso = f.producto('Queso cremoso', prov=self.prov, precio='9000',
                                controla_vencimiento=True, fraccionable=True,
                                contenido='1', unidad='KG')
        f.comprar(self.encargada, self.prov,
                  (self.queso, '2.5', '6000', [(f.en_dias(10), '2.5')]))
        Lote.objects.filter(id_producto=self.queso).update(fecha_vencimiento=f.en_dias(-1))

    def alertas(self, como=None, **parametros):
        respuesta = self.pedir('get', '/stock/alertas/', parametros,
                               como=como or self.operador)
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.json()
        self.assertFalse(datos['hay_mas'])          # la app las pide enteras
        return datos['resultados']

    def test_todas_en_orden_de_gravedad(self):
        alertas = self.alertas(tipo='TODAS')
        self.assertEqual([(a['tipo'], a['producto']['id']) for a in alertas], [
            ('VENCIDO', self.queso.pk), ('SIN_STOCK', self.agua.pk),
            ('POR_VENCER', self.yogur.pk), ('STOCK_BAJO', self.gaseosa.pk)])
        self.assertEqual(len({a['id'] for a in alertas}), 4)

    def test_lo_que_dice_cada_una(self):
        vencido, sin_stock, por_vencer, bajo = self.alertas()
        self.assertEqual((vencido['dias'], vencido['fecha']), (-1, f.en_dias(-1).isoformat()))
        self.assertEqual(vencido['producto'], {
            'id': self.queso.pk, 'nombre': 'Queso cremoso', 'presentacion': 'Por kg',
            'unidad': 'KG', 'cantidad_entera': False, 'controla_vencimiento': True,
            'stock_actual': 2.5})
        # Con el lote, la app abre «Cargar merma» ya completa.
        lote = self.queso.lotes.get()
        self.assertEqual(vencido['lote'], {'id': lote.pk, 'numero': 'L1', 'cantidad': 2.5})
        self.assertEqual(vencido['id'], f'VENCIDO-{self.queso.pk}-{lote.pk}')
        self.assertIn('El lote L1 venció con 2,5 KG en góndola', vencido['detalle'])
        self.assertEqual(sin_stock['detalle'], 'No queda stock. El mínimo es 5 unidad(es).')
        self.assertIsNone(sin_stock['dias'])
        self.assertIsNone(sin_stock['lote'])
        self.assertEqual(por_vencer['dias'], 3)
        self.assertIn('vence en 3 días', por_vencer['detalle'])
        self.assertIn('20% de rebaja', por_vencer['detalle'])
        self.assertEqual(bajo['detalle'], 'Quedan 10 unidad(es); el mínimo es 15 unidad(es).')

    def test_un_insumo_por_debajo_de_cero_queda_a_revisar(self):
        # La receta descontó café antes de que se cargara la compra. En el
        # local hay: no se muestra «-0,46», que se lee como «no hay», sino
        # cuánto se usó de más, y la app lo marca «a revisar».
        Producto.objects.filter(pk=self.grano.pk).update(
            stock_actual=D('-0.46'), stock_minimo=D('1'))
        alerta = next(a for a in self.alertas(tipo='STOCK')
                      if a['producto']['id'] == self.grano.pk)
        self.assertEqual(alerta['tipo'], 'SIN_STOCK')
        self.assertEqual(alerta['producto']['stock_actual'], -0.46)
        self.assertTrue(alerta['detalle'].startswith(
            'Stock a revisar: se usó 0,46 KG más de lo cargado. '
            'Falta cargar la compra o contarlo.'), alerta['detalle'])
        self.assertNotIn('-0', alerta['detalle'])

    def test_por_tipo(self):
        self.assertEqual({a['tipo'] for a in self.alertas(tipo='STOCK')},
                         {'SIN_STOCK', 'STOCK_BAJO'})
        self.assertEqual({a['tipo'] for a in self.alertas(tipo='VENCIMIENTO')},
                         {'VENCIDO', 'POR_VENCER'})
        self.assertEqual(len(self.alertas()), 4)      # sin tipo: todas

    def test_un_tipo_desconocido_es_400(self):
        respuesta = self.pedir('get', '/stock/alertas/', {'tipo': 'OTRAS'}, como=self.operador)
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json(), {'detail': 'Elegí Todas, Stock o Vencimiento.'})

    def test_son_las_de_la_pestania_alertas_de_la_web(self):
        alertas = self.alertas()
        dias = ParametrosNegocio.vigentes().dias_aviso_vencimiento
        self.assertEqual({a['producto']['id'] for a in alertas if a['tipo'] in DE_STOCK},
                         {p.pk for p in servicios.bajo_minimo()})
        self.assertEqual({a['id'].rsplit('-', 1)[1] for a in alertas
                          if a['tipo'] in DE_VENCIMIENTO},
                         {str(l.pk) for l in servicios.proximos_a_vencer(dias)})

    def test_los_dias_de_aviso_salen_de_los_parametros_del_negocio(self):
        parametros = ParametrosNegocio.vigentes()
        parametros.dias_aviso_vencimiento = 2
        parametros.save()
        # El yogur vence en 3 días: con 2 de aviso, todavía no es alerta.
        self.assertEqual([a['tipo'] for a in self.alertas(tipo='VENCIMIENTO')], ['VENCIDO'])

    def test_pide_ver_stock(self):
        respuesta = self.pedir('get', '/stock/alertas/', como=self.usuario_sin_permisos())
        self.assertEqual(respuesta.status_code, 403)
