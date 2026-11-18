"""Cuerpos JSON rotos o con tipos equivocados (DD-103): ningún endpoint de la
web, de /api/servicio/ ni de /api/movil/ puede contestar con un 500.

Cada endpoint recibe un cuerpo que no es un objeto (123, "texto", [1, 2],
null, JSON roto, bytes que no son UTF-8) y objetos con cada campo conocido
en el tipo equivocado (número, lista, booleano, objeto, texto). Lo único
aceptable es una respuesta controlada: 4xx con el error en castellano, o
2xx si el campo no se usa.
"""
import json

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from api_movil.autenticacion import emitir_token
from nucleo.tests import fabrica as f

TOKEN = 'servicio-de-prueba-' + 'x' * 40

NO_SON_OBJETOS = ('123', '"texto"', '[1, 2]', 'null', 'true', '{mal armado', b'\xff\xfe')
CAMPOS = ('url', 'tipo', 'nombre', 'detalle', 'pagos', 'renglones', 'lotes', 'vence', 'numero',
          'autoriza', 'pin', 'items', 'producto', 'cantidad', 'marca', 'cliente', 'modo', 'remota',
          'contenido', 'qr', 'codigo', 'rubro', 'proveedor', 'orden', 'monto', 'medio', 'motivo',
          'usuario', 'clave', 'costo', 'precio', 'descuento', 'puntos', 'observacion', 'destino',
          'origen', 'cuotas', 'recibido', 'telefono', 'dni', 'apellido', 'fecha_nacimiento',
          'precio_venta', 'controla', 'lote', 'stock_minimo', 'imagen')
TIPOS_MALOS = (123, [1, 'a'], True, {'a': 1}, 'texto', -1, 1e308)
ANIDADOS = (
    {'items': [{'producto': 'abc', 'cantidad': [1]}]},
    {'items': ['texto']},
    {'items': [{'producto': 1, 'cantidad': '1'}], 'pagos': [{'medio': 'abc', 'monto': {}}]},
    {'items': [{'producto': 1, 'cantidad': '1'}], 'pagos': ['texto']},
    {'autoriza': 'texto'},
    {'autoriza': {'usuario': 1, 'clave': [1]}},
    {'renglones': [{'producto': 'abc', 'cantidad': {}, 'lotes': 'x'}]},
    {'renglones': [{'producto': 1, 'cantidad': '1', 'lotes': [{'vence': 123, 'cantidad': []}]}]},
    {'lotes': [{'vence': True, 'cantidad': 'x'}]},
    {'producto': 7790001234567, 'cantidad': '1'},
    {'producto': '²', 'cantidad': '1'},
    {'renglones': [1, 'x', None]},
    {'renglones': [{'descripcion': 123, 'codigo_barras': ['x'], 'cantidad': 1e308, 'costo_unitario': {},
                    'lotes': 'x', 'vencimiento': 20261231, 'marca_detectada': 5}],
     'cuit_proveedor': 30712345671, 'nombre_proveedor': ['Norte'], 'tipo_comprobante': 1, 'fecha': 1,
     'vence': [], 'punto_venta': 1.5, 'numero': {'a': 1}},
    {'renglones': [{'descripcion': 'Alfajor', 'cantidad': '1', 'costo_unitario': '1e999999'}]},
)


def cuerpos():
    for cuerpo in NO_SON_OBJETOS:
        yield cuerpo
    for campo in CAMPOS:
        for valor in TIPOS_MALOS:
            yield json.dumps({campo: valor})
    for objeto in ANIDADOS:
        yield json.dumps(objeto)


class Fuzz:

    def sin_500(self, cliente, metodo, ruta, **cabeceras):
        rotas = []
        for cuerpo in cuerpos():
            respuesta = getattr(cliente, metodo)(ruta, cuerpo, content_type='application/json', **cabeceras)
            if respuesta.status_code >= 500 and respuesta.status_code != 503:
                rotas.append(f'{metodo.upper()} {ruta} {cuerpo!r:.80} → {respuesta.status_code}')
        return rotas

    def exigir_400_a_lo_que_no_es_objeto(self, cliente, metodo, ruta, **cabeceras):
        for cuerpo in NO_SON_OBJETOS:
            respuesta = getattr(cliente, metodo)(ruta, cuerpo, content_type='application/json', **cabeceras)
            self.assertEqual(respuesta.status_code, 400, f'{ruta} {cuerpo!r}')
            datos = respuesta.json()
            self.assertFalse(datos.get('ok', False))
            texto = datos.get('error') or datos.get('detail') or ''
            self.assertTrue(texto and 'Traceback' not in texto, f'{ruta} {cuerpo!r}: {datos}')


class WebTest(Fuzz, TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.duenia = f.titular()
        prov = f.proveedor()
        cls.alfajor = f.producto('Alfajor', prov=prov)
        f.comprar(cls.duenia, prov, (cls.alfajor, 10, '600'))
        cls.turno = f.abrir_turno(cls.duenia, f.caja_minisuper())

    def setUp(self):
        self.client = Client(raise_request_exception=False, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        f.entrar(self.client, self.duenia)
        self.client.post(reverse('ventas:elegir_caja'), {'caja': f.caja_minisuper().pk})

    RUTAS = ('ventas:cotizar', 'ventas:cobrar', 'ventas:ajuste_express', 'ventas:alta_cliente',
             'ventas:pedir_autorizacion', 'ventas:pedir_autorizacion_cierre',
             'compras:alta_proveedor_factura', 'compras:alta_producto_factura')

    def test_ninguna_ruta_web_da_500(self):
        rotas = []
        for nombre in self.RUTAS:
            rotas += self.sin_500(self.client, 'post', reverse(nombre))
        rotas += self.sin_500(self.client, 'post', reverse('ventas:pin_autorizacion', args=[1]))
        self.assertEqual(rotas, [], '\n'.join(rotas))

    def test_lo_que_no_es_un_objeto_es_400(self):
        for nombre in ('ventas:cotizar', 'ventas:cobrar', 'ventas:ajuste_express', 'ventas:alta_cliente',
                       'ventas:pedir_autorizacion', 'compras:alta_producto_factura'):
            self.exigir_400_a_lo_que_no_es_objeto(self.client, 'post', reverse(nombre))

    def test_renglones_de_formulario_rotos(self):
        for nombre in ('compras:nueva_orden', 'compras:nueva_compra'):
            for renglones in ('123', '"texto"', '{"a": 1}', '[1, 2]', '[{"producto": "abc"}]',
                              '[{"producto": 1, "cantidad": [1], "lotes": "x"}]', '{mal'):
                respuesta = self.client.post(reverse(nombre), {'renglones': renglones, 'proveedor': '1'})
                self.assertLess(respuesta.status_code, 500, f'{nombre} {renglones}')


@override_settings(CAFETO_N8N_TOKEN=TOKEN)
class ApiServicioTest(Fuzz, TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.producto = f.producto('Alfajor', prov=f.proveedor())

    def setUp(self):
        self.client = Client(raise_request_exception=False)
        self.cabeceras = {'HTTP_AUTHORIZATION': f'Bearer {TOKEN}'}

    def rutas(self):
        return ('/api/servicio/productos/%d/foto/' % self.producto.pk,
                '/api/servicio/productos/%d/enriquecer/' % self.producto.pk,
                '/api/servicio/compras/pre-cargar/', '/api/servicio/compras/leer-factura/')

    def test_ninguna_ruta_del_servicio_da_500(self):
        rotas = []
        for ruta in self.rutas():
            rotas += self.sin_500(self.client, 'post', ruta, **self.cabeceras)
        self.assertEqual(rotas, [], '\n'.join(rotas))

    def test_lo_que_no_es_un_objeto_es_400(self):
        for ruta in self.rutas():
            self.exigir_400_a_lo_que_no_es_objeto(self.client, 'post', ruta, **self.cabeceras)

    def test_una_factura_con_renglones_que_no_son_objetos_no_entra_a_la_bandeja(self):
        from compras.models import FacturaRecibida
        respuesta = self.client.post('/api/servicio/compras/pre-cargar/', json.dumps({'renglones': [1, 'x', None]}),
                                     content_type='application/json', **self.cabeceras)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('Cada renglón tiene que ser un objeto', respuesta.json()['detail'])
        self.assertFalse(FacturaRecibida.objects.exists())

    def test_la_url_de_la_precarga_es_absoluta_aunque_no_haya_url_publica(self):
        cuerpo = {'renglones': [{'descripcion': 'Alfajor', 'cantidad': '2', 'costo_unitario': '500'}]}
        with self.settings(CAFETO_URL_PUBLICA=''):
            respuesta = self.client.post('/api/servicio/compras/pre-cargar/', json.dumps(cuerpo),
                                         content_type='application/json', **self.cabeceras)
        self.assertTrue(respuesta.json()['url'].startswith('http://testserver/compras/'))
        with self.settings(CAFETO_URL_PUBLICA='https://cafeto.example/'):
            cuerpo['numero'] = '2'
            respuesta = self.client.post('/api/servicio/compras/pre-cargar/', json.dumps(
                {**cuerpo, 'renglones': [{'descripcion': 'Otro', 'cantidad': '1', 'costo_unitario': '9'}]}),
                content_type='application/json', **self.cabeceras)
        self.assertTrue(respuesta.json()['url'].startswith('https://cafeto.example/compras/'))


class ApiMovilTest(Fuzz, TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.admin = f.usuario('40000003', 'Administrador', nombre='Ana')
        cls.producto = f.producto('Alfajor', prov=f.proveedor())

    def setUp(self):
        self.client = Client(raise_request_exception=False)
        self.cabeceras = {'HTTP_AUTHORIZATION': f'Token {emitir_token(self.admin)}'}

    RUTAS = ('/api/movil/sesion/', '/api/movil/marcas/', '/api/movil/rubros/', '/api/movil/stock/ofertas/',
             '/api/movil/compras/ordenes/', '/api/movil/compras/recepciones/', '/api/movil/stock/conteo/',
             '/api/movil/stock/conteo/renglones/', '/api/movil/stock/conteo/enviar/',
             '/api/movil/stock/mermas/', '/api/movil/stock/traspasos/', '/api/movil/stock/ajustes/',
             '/api/movil/stock/producciones/', '/api/movil/clientes/vincular/')

    def test_ninguna_ruta_movil_da_500(self):
        rotas = []
        for ruta in self.RUTAS:
            rotas += self.sin_500(self.client, 'post', ruta, **self.cabeceras)
        self.assertEqual(rotas, [], '\n'.join(rotas))


class DelPedidoTest(SimpleTestCase):
    """Un formulario común se lee como sus campos; un cuerpo JSON que no es
    un objeto es un pedido mal armado."""

    def test_formulario_y_json(self):
        from django.test import RequestFactory

        from nucleo import json_seguro
        fabrica = RequestFactory()
        self.assertEqual(json_seguro.del_pedido(fabrica.post('/x/', {'modo': 'ACA'})), {'modo': 'ACA'})
        self.assertEqual(json_seguro.del_pedido(fabrica.post('/x/')), {})
        self.assertEqual(json_seguro.del_pedido(
            fabrica.post('/x/', '{"modo": "ACA"}', content_type='application/json')), {'modo': 'ACA'})
        for crudo in ('123', '[1]', '"x"', '{roto'):
            with self.assertRaises(json_seguro.PedidoMalArmado):
                json_seguro.del_pedido(fabrica.post('/x/', crudo, content_type='application/json'))
