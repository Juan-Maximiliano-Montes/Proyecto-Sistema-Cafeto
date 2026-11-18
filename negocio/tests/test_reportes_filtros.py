"""Filtro de dominio de los reportes (DD-103): un rubro o una caja, además
del período, en los reportes donde el dato lo permite; el filtro elegido se
ve en la pantalla y en la descripción del PDF y del Excel exportados."""
import io
from decimal import Decimal as D
from unittest import mock

from django.http import HttpResponse
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from negocio import reportes
from nucleo.tests import fabrica as f
from ventas import servicios as ventas


class FiltrosDeReportesTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.duenia = f.titular()
        prov = f.proveedor()
        cls.alfajor = f.producto('Alfajor', prov=prov, precio='1000')
        cls.cafe = f.producto('Café en lata', prov=prov, precio='2500', rubro_nombre='Cafetería')
        f.comprar(cls.duenia, prov, (cls.alfajor, 20, '600'), (cls.cafe, 10, '1500'))
        minisuper = f.abrir_turno(cls.duenia, f.caja_minisuper())
        cafeteria = f.abrir_turno(cls.duenia, f.caja_cafeteria())
        ventas.registrar(minisuper, cls.duenia, [ventas.Item(cls.alfajor, D('2'))], [ventas.Pago(f.efectivo())])
        ventas.registrar(cafeteria, cls.duenia, [ventas.Item(cls.cafe, D('1')), ventas.Item(cls.alfajor, D('1'))],
                         [ventas.Pago(f.medio('QR'))])
        cls.hoy = timezone.localdate()

    def setUp(self):
        self.client = Client()
        f.entrar(self.client, self.duenia)

    def pedir(self, **parametros):
        datos = {'desde': self.hoy.isoformat(), 'hasta': self.hoy.isoformat(), **parametros}
        return self.client.get(reverse('negocio:reportes'), datos)

    def vendido(self, respuesta):
        return {fila[0]: fila[2] for fila in respuesta.context['filas']}

    # ------------------------------------------------------------------
    def test_las_consultas_filtran_por_rubro_y_por_caja(self):
        hoy = self.hoy
        _c, filas, _t = reportes.productos(hoy, hoy, rubro=f.rubro('Cafetería').pk)
        self.assertEqual([fila[0] for fila in filas], ['Sin marca · Café en lata · 1 UNIDAD'])
        _c, filas, _t = reportes.productos(hoy, hoy, caja=f.caja_minisuper().pk)
        self.assertEqual([(fila[0], fila[1]) for fila in filas], [('Sin marca · Alfajor · 1 UNIDAD', D('2'))])
        _c, filas, totales = reportes.ventas_por_dia(hoy, hoy, caja=f.caja_cafeteria().pk)
        self.assertEqual(totales[2], D('3500'))
        _c, filas, _t = reportes.medios(hoy, hoy, caja=f.caja_minisuper().pk)
        self.assertEqual([fila[0] for fila in filas], ['Efectivo'])
        _c, filas, _t = reportes.stock_valorizado(rubro=f.rubro('Minisuper').pk)
        self.assertEqual([fila[0] for fila in filas], ['Minisuper'])

    def test_la_pantalla_aplica_el_filtro_y_lo_muestra(self):
        respuesta = self.pedir(ver='productos', rubro=f.rubro('Cafetería').pk)
        self.assertEqual(set(self.vendido(respuesta)), {'Sin marca · Café en lata · 1 UNIDAD'})
        self.assertContains(respuesta, 'Rubro: Cafetería')
        self.assertContains(respuesta, '<select name="rubro" class="form-select">', html=False)
        self.assertContains(respuesta, '<select name="caja" class="form-select">', html=False)
        # Las pestañas de los demás reportes conservan el filtro elegido.
        self.assertContains(respuesta, f'rubro={f.rubro("Cafetería").pk}')

    def test_sólo_ofrece_los_filtros_que_el_reporte_admite(self):
        respuesta = self.pedir(ver='medios')
        self.assertContains(respuesta, 'name="caja"')
        self.assertNotContains(respuesta, 'name="rubro"')
        respuesta = self.pedir(ver='ranking')
        self.assertNotContains(respuesta, 'name="caja"')
        self.assertNotContains(respuesta, 'name="rubro"')

    def test_un_filtro_que_no_aplica_o_no_existe_no_cambia_nada(self):
        todos = self.vendido(self.pedir(ver='medios'))
        for valor in ('abc', '-1', '999999', '7790001234567', '²'):
            respuesta = self.pedir(ver='medios', caja=valor, rubro=valor)
            self.assertEqual(respuesta.status_code, 200, valor)
            self.assertEqual(self.vendido(respuesta), todos, valor)
            self.assertEqual(respuesta.context['filtros_aplicados'], '', valor)
        # El rubro no aplica a medios de pago: se ignora.
        respuesta = self.pedir(ver='medios', rubro=f.rubro('Cafetería').pk)
        self.assertEqual(self.vendido(respuesta), todos)

    def test_el_stock_valorizado_tiene_el_filtro_aunque_no_tenga_periodo(self):
        respuesta = self.client.get(reverse('negocio:reportes'), {'ver': 'stock', 'rubro': f.rubro('Cafetería').pk})
        self.assertEqual(set(self.vendido(respuesta)), {'Cafetería'})
        self.assertContains(respuesta, 'name="rubro"')
        self.assertNotContains(respuesta, 'type="date"')

    def test_el_pdf_dice_los_filtros_quien_y_cuando(self):
        with mock.patch('negocio.views.reportes.exportar') as exportar:
            exportar.return_value = HttpResponse('pdf')
            self.client.get(reverse('negocio:exportar_reporte'), {
                'ver': 'productos', 'formato': 'pdf', 'desde': self.hoy.isoformat(),
                'hasta': self.hoy.isoformat(), 'rubro': f.rubro('Cafetería').pk,
                'caja': f.caja_cafeteria().pk})
        argumentos = exportar.call_args.args
        formato, _titulo, _columnas, filas, descripcion = argumentos[:5]
        self.assertEqual(formato, 'pdf')
        self.assertIn(f'Del {self.hoy:%d/%m/%Y} al {self.hoy:%d/%m/%Y}', descripcion)
        self.assertIn('Filtros: Rubro: Cafetería · Caja: Caja Cafetería', descripcion)
        self.assertIn(f'Exportó {self.duenia.apellido}, {self.duenia.nombre} ({self.duenia.nombre_usuario})',
                      descripcion)
        self.assertRegex(descripcion, r'el \d{2}/\d{2}/\d{4} a las \d{2}:\d{2}$')
        self.assertEqual([fila[0] for fila in filas][:1], ['Sin marca · Café en lata · 1 UNIDAD'])

    def test_el_excel_lleva_la_misma_descripcion(self):
        from openpyxl import load_workbook
        respuesta = self.client.get(reverse('negocio:exportar_reporte'), {
            'ver': 'medios', 'formato': 'xlsx', 'desde': self.hoy.isoformat(),
            'hasta': self.hoy.isoformat(), 'caja': f.caja_minisuper().pk})
        hoja = load_workbook(io.BytesIO(b''.join(respuesta.streaming_content)
                                        if respuesta.streaming else respuesta.content)).active
        self.assertIn('Filtros: Caja: Caja Minisuper', hoja['A3'].value)
        self.assertIn('Exportó', hoja['A3'].value)
        self.assertEqual(hoja['A6'].value, 'Efectivo')
