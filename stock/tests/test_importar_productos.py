"""Importar productos desde Excel, con planillas como las que arma una persona."""
import io
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from nucleo.tests import fabrica
from stock.models import Producto


def xlsx(filas, nombre='lista.xlsx'):
    libro = Workbook()
    for fila in filas:
        libro.active.append(fila)
    salida = io.BytesIO()
    libro.save(salida)
    return SimpleUploadedFile(nombre, salida.getvalue())


class ImportarProductosTest(TestCase):

    def setUp(self):
        fabrica.entrar(self.client, fabrica.usuario('30111240', 'Administrador'))
        self.prov = fabrica.proveedor()
        self.url = reverse('importar', args=['productos'])

    def subir(self, archivo):
        return self.client.post(self.url, {'archivo': archivo}, follow=True)

    def confirmar(self):
        return self.client.post(reverse('importar_confirmar', args=['productos']), follow=True)

    def test_una_planilla_desprolija_entra_bien(self):
        """Columnas en otro orden, mayúsculas, una columna de más, una fila
        vacía, precios como número, con coma decimal y con el signo $."""
        p = self.prov.nombre_proveedor
        pantalla = self.subir(xlsx([
            ['PRECIO DE VENTA', 'nombre', 'MARCA', 'Rubro', 'costo', 'Proveedor',
             'Código de Barras', 'Unidad', 'Contenido', 'Fraccionable', 'Controla Vencimiento', 'Notas'],
            [1200, 'Alfajor de maicena', 'Havanna', 'Minisuper', 700, p, 7790895000997, '', '', '', '', 'x'],
            [1500.5, 'Galletitas surtidas', 'Bagley', 'Minisuper', 900.25, p, '', '', '', 'No', 'no', ''],
            ['1.200,50', 'Chocolate con leche', 'Arcor', 'minisuper', '800,75', p, '', 'G', 100, '', '', ''],
            ['$ 2.300', 'Caramelos masticables', 'Arcor', 'Minisuper', '$1.000', p, '', '', '', '', '', ''],
            [None] * 12,
            [8500, 'Queso cremoso', 'La Paulina', 'Minisuper', 6000, self.prov.cuit_proveedor, '', 'KG', 1, 'Sí', 'SI', ''],
        ]))
        self.assertEqual(pantalla.context['errores'], 0, [r.errores for r in pantalla.context['resultados']])
        self.assertEqual(pantalla.context['altas'], 5)
        self.confirmar()
        precios = dict(Producto.objects.filter(id_proveedor=self.prov)
                       .values_list('nombre_producto', 'precio_venta'))
        self.assertEqual(precios['Alfajor de maicena'], D('1200'))
        self.assertEqual(precios['Galletitas surtidas'], D('1500.50'))
        self.assertEqual(precios['Chocolate con leche'], D('1200.50'))
        self.assertEqual(precios['Caramelos masticables'], D('2300'))
        alfajor = Producto.objects.get(nombre_producto='Alfajor de maicena')
        self.assertEqual(alfajor.codigo_barras, '7790895000997')
        queso = Producto.objects.get(nombre_producto='Queso cremoso')
        self.assertTrue(queso.fraccionable and queso.controla_vencimiento)

    def test_con_un_titulo_arriba_de_los_encabezados(self):
        pantalla = self.subir(xlsx([
            ['Lista de precios de noviembre'], [],
            ['Nombre', 'Marca', 'Rubro', 'Precio de venta', 'Proveedor'],
            ['Alfajor', 'Havanna', 'Minisuper', 1200, self.prov.nombre_proveedor]]))
        self.assertEqual(pantalla.context['altas'], 1)

    def test_un_error_no_importa_nada_y_dice_en_que_fila(self):
        p = self.prov.nombre_proveedor
        pantalla = self.subir(xlsx([
            ['Nombre', 'Marca', 'Rubro', 'Precio de venta', 'Proveedor'],
            ['Alfajor', 'Havanna', 'Minisuper', 1200, p],
            ['Turrón', 'Arcor', 'Rubro Que No Existe', 900, p],
            ['Alfajor', 'Havanna', 'Minisuper', 1200, p],
            ['Chicle', 'Beldent', 'Minisuper', -50, p]]))
        self.assertEqual(pantalla.context['errores'], 3)
        self.assertContains(pantalla, 'Está repetido')
        self.confirmar()
        self.assertFalse(Producto.objects.filter(nombre_producto='Alfajor').exists())

    def test_avisa_si_el_precio_queda_bajo_el_costo(self):
        pantalla = self.subir(xlsx([
            ['Nombre', 'Marca', 'Rubro', 'Precio de venta', 'Costo', 'Proveedor'],
            ['Chupetín', 'Arcor', 'Minisuper', 300, 500, self.prov.nombre_proveedor]]))
        self.assertEqual(pantalla.context['errores'], 0)
        self.assertContains(pantalla, 'por debajo del costo')

    def test_el_ejemplo_de_la_planilla_modelo_es_valido(self):
        modelo = self.client.get(reverse('importar_modelo', args=['productos']))
        pantalla = self.subir(SimpleUploadedFile('modelo.xlsx', modelo.content))
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['resultados']])

    def test_archivos_que_no_sirven_se_explican(self):
        for archivo, mensaje in (
                (xlsx([['Producto', 'Precio'], ['Alfajor', 1200]]), 'faltan columnas obligatorias'),
                (xlsx([['Nombre', 'Marca', 'Rubro']]), 'no tiene filas con datos'),
                (SimpleUploadedFile('lista.xls', b'viejo'), '.xlsx (Excel) o .csv'),
                (SimpleUploadedFile('lista.xlsx', b'no es un excel'), 'No se pudo abrir')):
            with self.subTest(mensaje=mensaje):
                self.assertContains(self.subir(archivo), mensaje)
