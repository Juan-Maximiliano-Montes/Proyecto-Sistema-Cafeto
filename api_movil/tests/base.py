"""Lo que comparten las pruebas de la API: usuarios, token, pedidos y catálogo.

Las rutas se escriben enteras (``/api/movil/...``) a propósito: son el
contrato con la app, que las tiene escritas así. Si alguien las cambia,
las pruebas tienen que fallar.
"""
import json
from decimal import Decimal

from django.test import TestCase

from api_movil.autenticacion import emitir_token
from nucleo.tests import fabrica as f
from seguridad.models import Perfil
from stock.models import Rubro

D = Decimal
RAIZ = '/api/movil'


class BaseApi(TestCase):

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.admin = f.usuario('40000003', 'Administrador', nombre='Ana')

    def usuario_sin_permisos(self):
        """Alguien del equipo cuyo perfil no tiene ningún permiso."""
        Perfil.objects.create(nombre='Cocina', descripcion='Sin permisos',
                              tope_descuento_porcentaje=D('0'))
        return f.usuario('40000009', 'Cocina', nombre='Rita')

    def pedir(self, metodo, ruta, datos=None, como=None, token=None,
              **encabezados):
        """``self.pedir('post', '/marcas/', {'nombre': 'Arcor'}, como=self.admin)``.

        ``como`` emite el token de ese usuario; ``token`` usa uno dado. Un
        ``str`` en ``datos`` viaja tal cual (para probar cuerpos rotos).
        """
        if como is not None:
            token = emitir_token(como)
        if token is not None:
            encabezados['HTTP_AUTHORIZATION'] = f'Token {token}'
        url = RAIZ + ruta
        if metodo == 'get':
            return self.client.get(url, datos or {}, **encabezados)
        cuerpo = datos if isinstance(datos, str) else json.dumps(
            datos if datos is not None else {})
        return getattr(self.client, metodo)(
            url, cuerpo, content_type='application/json', **encabezados)


class BaseCatalogo(BaseApi):
    """Un local chico: algo de cada tipo, con stock y lotes de verdad.

    El stock entra por compras (no se escribe a mano), como en el resto de
    las pruebas del sistema. El rubro Minisuper rebaja 20 % desde 5 días
    antes de vencer.
    """

    def setUp(self):
        super().setUp()
        self.prov = f.proveedor()
        self.cafeteria = f.rubro('Cafetería')
        self.minisuper = f.rubro('Minisuper')
        Rubro.objects.filter(pk=self.minisuper.pk).update(
            dias_rebaja_vencimiento=5, porcentaje_rebaja_vencimiento=D('20'))

        self.gaseosa = f.producto(
            'Gaseosa cola', prov=self.prov, marca_nombre='Coca-Cola',
            contenido='500', unidad='ML', codigo_barras='7790895000782')
        self.alfajor = f.producto('Alfajor', prov=self.prov,
                                  marca_nombre='Arcor', precio='900')
        self.yogur = f.producto('Yogur frutilla', prov=self.prov,
                                controla_vencimiento=True)
        self.grano = f.producto('Café en grano', 'INSUMO', prov=self.prov,
                                rubro_nombre='Cafetería', costo='20000',
                                contenido='1', unidad='KG')
        self.cafe = f.receta(
            f.producto('Café con leche', 'ELABORADO', rubro_nombre='Cafetería',
                       precio='2500'),
            (self.grano, '0.018'))
        self.combo = f.receta(
            f.producto('Desayuno Cafeto', 'COMBO', rubro_nombre='Cafetería',
                       precio='4000'),
            (self.cafe, '1'), (self.alfajor, '2'))

        f.comprar(self.encargada, self.prov,
                  (self.gaseosa, 10, '600'),
                  (self.alfajor, 20, '400'),
                  (self.grano, 5, '20000'),
                  # Un lote dentro de la rebaja (3 días) y otro lejos (30).
                  (self.yogur, 10, '500', [(f.en_dias(3), 4),
                                           (f.en_dias(30), 6)]))

    def catalogo(self, seccion, **parametros):
        """Los productos publicados de la sección, por id."""
        respuesta = self.pedir('get', '/catalogo/productos/',
                               {'seccion': seccion, **parametros})
        self.assertEqual(respuesta.status_code, 200)
        return {p['id']: p for p in respuesta.json()['resultados']}
