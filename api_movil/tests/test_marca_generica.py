"""La app no muestra una marca que no es una marca (auditoría 2026-11).

Hay dos marcas reservadas: «Sin marca» (lo que se vende suelto o se elabora
en el local) y «Genérica» (la que le pone el alta simple y el importador a
todo lo que todavía no tiene marca cargada). Ninguna de las dos es una
marca de verdad.

El mostrador web oculta «Genérica» (``ventas/views/mostrador.py``); la app
tenía escrito a mano 'Sin marca' y nada más, así que a los cientos de
artículos que entran de la planilla de Fácil Virtual les mostraba
«Genérica» arriba del nombre, como si fuera la marca del producto.
"""
from api_movil.tests.base import BaseCatalogo
from nucleo.tests import fabrica as f
from stock.models import MARCA_GENERICA


class MarcaGenericaTest(BaseCatalogo):

    def ficha(self, producto, seccion='MINISUPER'):
        return self.catalogo(seccion)[producto.pk]

    def test_generica_viaja_como_sin_marca(self):
        importado = f.producto('GASEOSA 2.25L', prov=self.prov, precio='3500',
                               marca_nombre=MARCA_GENERICA)
        self.assertIsNone(self.ficha(importado)['marca'])

    def test_sin_marca_sigue_viajando_como_sin_marca(self):
        suelto = f.producto('Yerba suelta', prov=self.prov, precio='8000',
                            marca_nombre='Sin marca')
        self.assertIsNone(self.ficha(suelto)['marca'])

    def test_una_marca_de_verdad_sigue_viajando(self):
        self.assertEqual(self.ficha(self.alfajor)['marca']['nombre'], 'Arcor')

    def test_el_nombre_sale_del_modelo_y_no_de_una_copia(self):
        """Si alguien renombra la marca reservada, la app lo sigue."""
        from api_movil import catalogo
        from nucleo.validaciones import clave_normalizada

        self.assertIn(clave_normalizada(MARCA_GENERICA), catalogo.MARCAS_SIN_MARCA)
        self.assertIn(clave_normalizada('Sin marca'), catalogo.MARCAS_SIN_MARCA)

    def test_tampoco_aparece_en_la_busqueda_como_marca(self):
        f.producto('GASEOSA 2.25L', prov=self.prov, precio='3500',
                   marca_nombre=MARCA_GENERICA)
        encontrados = self.catalogo('MINISUPER', q='generica')
        self.assertEqual(
            [p['nombre'] for p in encontrados.values() if p['marca']], [],
            'buscar por la marca reservada no puede traerla como marca')
