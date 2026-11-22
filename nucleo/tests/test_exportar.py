"""Excel y PDF exportados: la fila de totales, el conteo de registros, los
metadatos sin repetir, el filtro de la planilla y los anchos y alineaciones."""
import io
import re
from datetime import date, timedelta
from decimal import Decimal as D
from unittest import mock

from django.test import TestCase
from openpyxl import load_workbook

from nucleo import contexto
from nucleo.exportar import ColumnaExportable as C
from nucleo.exportar import a_csv, a_excel, a_pdf
from nucleo.tests import fabrica as f

COLUMNAS = [C('Día', 'fecha'), C('Tickets', 'numero', conteo=True),
            C('Vendido', 'dinero'), C('Margen %', 'numero')]
INICIO = date(2026, 11, 1)
FILAS = [[INICIO + timedelta(days=n), 10, D('37204.29'), D('31.5')] for n in range(14)]
TOTALES = ['Total', 140, D('520860.00'), D('31.5')]


class ExcelTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.usuario = f.titular()

    def hoja(self, **extra):
        with mock.patch.object(contexto, 'usuario_actual', return_value=self.usuario):
            respuesta = a_excel('Ventas por día', COLUMNAS, FILAS, 'Del 01/11/2026 al 14/11/2026',
                                totales=TOTALES, **extra)
        return load_workbook(io.BytesIO(respuesta.content)).active

    def test_el_subtitulo_dice_una_sola_vez_cuando_y_quien_y_cuenta_sin_el_total(self):
        subtitulo = self.hoja()['A3'].value
        self.assertEqual(subtitulo.count('Generado el'), 1)
        self.assertEqual(len(re.findall(r'\d{2}/\d{2}/\d{4} \d{2}:\d{2}', subtitulo)), 1)
        self.assertNotIn('a las', subtitulo)
        self.assertIn(f'por {self.usuario.apellido}, {self.usuario.nombre} ({self.usuario.nombre_usuario})',
                      subtitulo)
        self.assertTrue(subtitulo.endswith(' · 14 registros'))

    def test_el_filtro_de_la_planilla_deja_afuera_la_fila_de_totales(self):
        hoja = self.hoja()
        self.assertEqual(hoja.auto_filter.ref, 'A5:D19')
        self.assertEqual(hoja['A19'].value.date(), INICIO + timedelta(days=13))
        self.assertEqual(hoja['A20'].value, 'Total')

    def test_la_fila_de_totales_es_texto_en_negrita_con_linea_arriba_y_fondo(self):
        hoja = self.hoja()
        total = hoja['A20']
        self.assertEqual(total.number_format, '@')
        for celda in hoja[20]:
            self.assertTrue(celda.font.bold)
            self.assertEqual(celda.border.top.style, 'medium')
            self.assertEqual(celda.fill.fgColor.rgb[-6:], 'FDF3D8')
        self.assertEqual(hoja['C20'].value, D('520860.00'))
        self.assertEqual(hoja['C20'].number_format, '"$" #,##0.00')
        self.assertEqual(hoja['A6'].number_format, 'DD/MM/YYYY')
        self.assertEqual(hoja['B6'].number_format, '#,##0')
        self.assertEqual(hoja['D6'].number_format, '#,##0.###')

    def test_los_anchos_alcanzan_para_los_importes(self):
        hoja = self.hoja()
        self.assertGreaterEqual(hoja.column_dimensions['C'].width, len('$ 520.860,00') + 4)
        self.assertGreater(hoja.column_dimensions['C'].width, 12)
        self.assertGreaterEqual(hoja.column_dimensions['A'].width, len('DD/MM/YYYY'))

    def test_texto_a_la_izquierda_conteos_al_centro_y_numeros_a_la_derecha(self):
        hoja = self.hoja()
        for fila in (5, 6, 19):
            self.assertEqual([hoja.cell(row=fila, column=n).alignment.horizontal for n in range(1, 5)],
                             ['left', 'center', 'right', 'right'])
        self.assertEqual(hoja['A20'].alignment.horizontal, 'left')

    def test_sin_registros_no_hay_fila_de_totales(self):
        with mock.patch.object(contexto, 'usuario_actual', return_value=self.usuario):
            respuesta = a_excel('Ventas por día', COLUMNAS, [], totales=TOTALES)
        hoja = load_workbook(io.BytesIO(respuesta.content)).active
        self.assertIsNone(hoja['A6'].value)
        self.assertTrue(hoja['A3'].value.endswith(' · 0 registros'))

    def test_el_tope_cuenta_solo_lo_que_se_escribio(self):
        hoja = self.hoja(tope=5)
        self.assertEqual(hoja.auto_filter.ref, 'A5:D10')
        self.assertEqual(hoja['A11'].value, 'Total')
        self.assertTrue(hoja['A3'].value.endswith(' · 5 registros'))


class PdfTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.usuario = f.titular()

    def html(self):
        capturado = {}

        class Falso:
            def __init__(self, string):
                capturado['html'] = string

            def write_pdf(self):
                return b'%PDF-falso'

        with mock.patch('weasyprint.HTML', Falso), \
                mock.patch.object(contexto, 'usuario_actual', return_value=self.usuario):
            a_pdf('Ventas por día', COLUMNAS, FILAS, 'Del 01/11/2026 al 14/11/2026', totales=TOTALES)
        return capturado['html']

    def test_la_fila_de_totales_dice_total_en_negrita_con_linea_arriba(self):
        html = self.html()
        fila = re.search(r'<tr class="total">(.*?)</tr>', html, re.S).group(1)
        celdas = re.findall(r'<td[^>]*>(.*?)</td>', fila, re.S)
        self.assertEqual(celdas[0], 'Total')
        self.assertEqual(celdas[1], '140')
        self.assertEqual(celdas[2], '<span class="dinero">$&nbsp;520.860,00</span>')
        self.assertEqual(celdas[3], '31,5')
        self.assertIn('tr.total td { font-weight: 600; background: #FDF3D8; border-top: 1.2pt solid #161616;', html)

    def test_cuenta_los_registros_sin_el_total_y_no_repite_la_fecha(self):
        html = self.html()
        bajada = re.search(r'<div class="bajada">(.*?)</div>', html, re.S).group(1)
        bajada = ' '.join(bajada.split())
        self.assertIn('· 14 registros', bajada)
        self.assertEqual(bajada.count('Generado el'), 1)
        self.assertEqual(len(re.findall(r'\d{2}:\d{2}', bajada)), 1)
        self.assertNotIn('Exportó', bajada)
        self.assertIn(f'por {self.usuario.apellido}, {self.usuario.nombre}', bajada)

    def test_los_conteos_van_al_centro_y_los_importes_a_la_derecha(self):
        html = self.html()
        self.assertIn('<th>Día</th><th class="centro">Tickets</th><th class="num">Vendido</th>'
                      '<th class="num">Margen %</th>', html)
        primera = re.search(r'<tbody>\s*<tr>(.*?)</tr>', html, re.S).group(1)
        self.assertEqual(re.findall(r'<td( class="\w+")?>', primera),
                         ['', ' class="centro"', ' class="num"', ' class="num"'])

    def test_el_pdf_real_se_arma(self):
        with mock.patch.object(contexto, 'usuario_actual', return_value=self.usuario):
            respuesta = a_pdf('Ventas por día', COLUMNAS, FILAS, totales=TOTALES)
        self.assertTrue(respuesta.content.startswith(b'%PDF'))


class CsvTest(TestCase):

    def test_el_total_va_al_final_con_su_etiqueta(self):
        respuesta = a_csv('Ventas por día', COLUMNAS, FILAS, TOTALES)
        texto = b''.join(respuesta.streaming_content).decode('utf-8-sig')
        renglones = texto.strip().split('\r\n')
        self.assertEqual(len(renglones), 16)
        self.assertEqual(renglones[-1], 'Total;140;520860,00;31,5')
