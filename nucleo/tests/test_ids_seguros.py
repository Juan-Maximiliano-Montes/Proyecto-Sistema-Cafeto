"""IDs que llegan de la URL, de un formulario o de un JSON (DD-103): ningún
valor raro (texto, decimal, negativo, «²», un código de barras de 13
dígitos, un número de 20 cifras) puede terminar en un 500."""
from decimal import Decimal as D

from django.db import DataError
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from nucleo.errores import mensaje_de
from nucleo.tests import fabrica as f
from nucleo.validaciones import MAXIMO_ID, id_valido
from ventas import servicios as ventas

RAROS = ('abc', '1.5', '-1', '0', '²', '١٢', '7790001234567', '99999999999999999999', ' ', '1e3', '0x10')


class IdValidoTest(SimpleTestCase):

    def test_acepta_sólo_enteros_positivos_del_integer_de_la_base(self):
        self.assertEqual(id_valido('42'), 42)
        self.assertEqual(id_valido(' 42 '), 42)
        self.assertEqual(id_valido(7), 7)
        self.assertEqual(id_valido(str(MAXIMO_ID)), MAXIMO_ID)
        self.assertEqual(MAXIMO_ID, 2_147_483_647)
        for valor in RAROS + (None, '', True, False, 1.0, [1], {'a': 1}, MAXIMO_ID + 1, -5, 0):
            self.assertIsNone(id_valido(valor), repr(valor))

    def test_un_codigo_de_barras_no_es_un_numero_de_venta(self):
        self.assertIsNone(id_valido('7790001234567'))
        self.assertIsNone(id_valido(7790001234567))


class ErroresDeLaBaseTest(SimpleTestCase):

    def test_data_error_y_overflow_tienen_mensaje_y_no_son_500(self):
        for error in (DataError('integer out of range'), OverflowError('Python int too large')):
            texto, estado = mensaje_de(error)
            self.assertEqual(estado, 400)
            self.assertIn('demasiado grande', texto)
            self.assertNotIn('integer', texto)


@override_settings(DEBUG=False)
class SinQuinientosTest(TestCase):
    """Los vectores que encontró el sondeo de todas las rutas, y los que
    nombró la auditoría, con cada valor raro."""

    @classmethod
    def setUpTestData(cls):
        cls.duenia = f.titular()
        prov = f.proveedor()
        cls.alfajor = f.producto('Alfajor', prov=prov)
        f.comprar(cls.duenia, prov, (cls.alfajor, 10, '600'))
        cls.turno = f.abrir_turno(cls.duenia, f.caja_minisuper())
        ventas.registrar(cls.turno, cls.duenia, [ventas.Item(cls.alfajor, D('1'))], [ventas.Pago(f.efectivo())])

    def setUp(self):
        self.client = Client(raise_request_exception=False)
        f.entrar(self.client, self.duenia)
        self.client.post(reverse('ventas:elegir_caja'), {'caja': f.caja_minisuper().pk})

    GET = (
        ('stock:productos_lista', ('marca', 'rubro', 'proveedor', 'tipo')),
        ('stock:productos_exportar', ('marca', 'rubro', 'proveedor')),
        ('fidelizacion:clientes_lista', ('categoria',)),
        ('geografia:localidades_lista', ('provincia',)),
        ('compras:proveedores_lista', ('tipo', 'condicion')),
        ('ventas:ventas', ('numero', 'caja', 'vendedor', 'medio')),
        ('ventas:cambios', ('venta', 'comprobante')),
        ('ventas:caja', ('vale',)),
        ('ventas:turnos', ('caja',)),
        ('compras:egresos', ('compra', 'vale')),
        ('compras:recepcion', ('orden', 'factura')),
        ('compras:compras', ('proveedor',)),
        ('stock:lotes_de_producto', ('producto',)),
        ('stock:precios', ('marca', 'rubro', 'proveedor')),
        ('stock:buscar_productos', ('page',)),
        ('ventas:catalogo', ('pagina',)),
        ('seguridad:lista', ('perfil',)),
        ('negocio:archivo', ('anio',)),
        ('negocio:archivo_exportar', ('anio',)),
        ('stock:productos_parecidos', ('excluir',)),
    )
    POST = (
        ('ventas:elegir_caja', ('caja',)),
        ('ventas:autorizaciones', ('solicitud',)),
        ('stock:producciones', ('producto',)),
        ('compras:recepcion', ('proveedor', 'orden')),
        ('compras:nueva_compra', ('proveedor',)),
        ('compras:nueva_orden', ('proveedor',)),
        ('compras:registrar_egreso', ('tipo', 'proveedor', 'compra', 'medio')),
        ('ventas:cambios', ('venta', 'medio', 'entrega_producto_0')),
    )

    def test_ningun_parametro_raro_da_500(self):
        rotos = []
        for nombre, claves in self.GET:
            for clave in claves:
                for valor in RAROS:
                    respuesta = self.client.get(reverse(nombre), {clave: valor})
                    if respuesta.status_code >= 500:
                        rotos.append(f'GET {nombre} {clave}={valor!r} → {respuesta.status_code}')
        for nombre, claves in self.POST:
            for clave in claves:
                for valor in RAROS:
                    respuesta = self.client.post(reverse(nombre), {clave: valor})
                    if respuesta.status_code >= 500:
                        rotos.append(f'POST {nombre} {clave}={valor!r} → {respuesta.status_code}')
        self.assertEqual(rotos, [], '\n'.join(rotos))

    def test_un_filtro_que_no_se_ofrece_se_ignora(self):
        todos = self.client.get(reverse('stock:productos_lista'), {'busqueda': 'Alfajor'})
        raro = self.client.get(reverse('stock:productos_lista'), {'busqueda': 'Alfajor', 'marca': 'abc'})
        self.assertEqual(raro.status_code, 200)
        aplicado = {filtro.clave: valor for filtro, _opciones, valor in raro.context['filtros']}
        self.assertEqual(aplicado['marca'], '')
        self.assertEqual(raro.context['total'], todos.context['total'])
        self.assertGreater(todos.context['total'], 0)

    def test_lotes_de_un_producto_que_no_es_un_numero(self):
        respuesta = self.client.get(reverse('stock:lotes_de_producto'), {'producto': 'abc'},
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['lotes'], [])
        respuesta = self.client.get(reverse('stock:lotes_de_producto'), {'producto': '2147483647'})
        self.assertEqual(respuesta.status_code, 404)
        respuesta = self.client.get(reverse('stock:lotes_de_producto'), {'producto': self.alfajor.pk})
        self.assertEqual(respuesta.status_code, 200)

    def test_el_n_de_venta_con_un_codigo_de_barras_no_encuentra_nada(self):
        respuesta = self.client.get(reverse('ventas:ventas'), {'numero': '7790001234567'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['cantidad'], 0)

    def test_volver_de_la_foto_no_sale_del_sitio(self):
        for volver in ('https://otro-sitio.example/', '//otro-sitio.example/', 'abc', '7790001234567'):
            respuesta = self.client.post(reverse('stock:producto_foto', args=[self.alfajor.pk]),
                                         {'volver': volver, 'quitar': '1'})
            self.assertEqual(respuesta.status_code, 302, volver)
            self.assertEqual(respuesta['Location'], reverse('stock:productos_lista'), volver)
        respuesta = self.client.post(reverse('stock:producto_foto', args=[self.alfajor.pk]),
                                     {'volver': '/stock/productos/?q=x', 'quitar': '1'})
        self.assertEqual(respuesta['Location'], '/stock/productos/?q=x')
