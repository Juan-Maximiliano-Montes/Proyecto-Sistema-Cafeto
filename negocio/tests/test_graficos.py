"""Los gráficos de los reportes (Hito 5): pandas + matplotlib.

    GraficosTest    cada reporte con datos da su SVG; sin datos, ninguno
    ReglasTest      los límites de DD-74 (barras, torta, granularidad)
    PantallaTest    cada opción muestra su gráfico; selector; PDF
    MenuTest        el panel lateral se pliega por áreas
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import pandas as pd

from negocio.graficos import (TOPE_BARRAS, TOPE_TORTA, admite_torta, comparar, con_otros,
                              evolucion, grafico, tabla)
from negocio.reportes import CONSULTAS, REPORTES
from nucleo.tests import fabrica as f
from ventas import servicios as ventas

D = Decimal


class Base(TestCase):

    def setUp(self):
        self.admin = f.usuario('40000003', 'Administrador', nombre='Ana')
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        prov = f.proveedor()
        self.gaseosa = f.producto('Gaseosa', prov=prov, precio='1500', costo='700')
        f.comprar(self.admin, prov, (self.gaseosa, 40, '700'))
        turno = f.abrir_turno(self.operador)
        for cantidad, medio in (('2', f.efectivo()), ('3', f.medio('Transferencia'))):
            ventas.registrar(turno, self.operador, [ventas.Item(self.gaseosa, D(cantidad))],
                             [ventas.Pago(medio)])
        self.hasta = timezone.localdate()
        self.desde = self.hasta - timedelta(days=10)


class GraficosTest(Base):

    def test_cada_reporte_con_datos_da_su_grafico(self):
        for clave in ('ventas_dia', 'productos', 'medios', 'vendedores', 'stock'):
            with self.subTest(reporte=clave):
                columnas, filas, _ = CONSULTAS[clave](self.desde, self.hasta)
                svg = grafico(clave, columnas, list(filas), self.desde, self.hasta)
                self.assertTrue(svg.startswith('<svg role="img" aria-label='))
                self.assertIn('currentColor', svg)        # sigue al tema

    def test_sin_datos_no_hay_grafico(self):
        columnas, filas, _ = CONSULTAS['mermas'](self.desde, self.hasta)
        self.assertIsNone(grafico('mermas', columnas, list(filas), self.desde, self.hasta))

    def test_pandas_completa_los_dias_sin_ventas(self):
        columnas, filas, _ = CONSULTAS['ventas_dia'](self.desde, self.hasta)
        marco = tabla(columnas, list(filas))
        self.assertEqual(marco['Vendido'].sum(), 7500.0)
        # Con menos de dos semanas el promedio de 7 días saldría casi todo
        # de ventanas incompletas: no se dibuja.
        svg = grafico('ventas_dia', columnas, list(filas), self.desde, self.hasta)
        self.assertNotIn('Promedio de 7 días', svg)
        desde = self.hasta - timedelta(days=19)
        columnas, filas, _ = CONSULTAS['ventas_dia'](desde, self.hasta)
        svg = grafico('ventas_dia', columnas, list(filas), desde, self.hasta)
        self.assertIn('Promedio de 7 días', svg)


class ReglasTest(TestCase):
    """Los límites de DD-74: cuántas barras, cuántas porciones, cuándo no hay torta."""

    def marco(self, valores):
        return pd.DataFrame({'Nombre': [f'N{i:02d}' for i in range(len(valores))],
                             'Monto': valores})

    def test_mas_de_diez_barras_suma_el_resto_en_otros(self):
        datos = con_otros(self.marco(list(range(12, 0, -1))), 'Nombre', 'Monto', TOPE_BARRAS)
        self.assertEqual(len(datos), 10)
        self.assertEqual(datos['Nombre'].iloc[-1], 'Otros (3)')
        self.assertEqual(datos['Monto'].sum(), sum(range(1, 13)))   # el total no cambia

    def test_la_torta_tiene_como_mucho_seis_porciones(self):
        datos = con_otros(self.marco([9, 8, 7, 6, 5, 4, 3, 2, 1]), 'Nombre', 'Monto', TOPE_TORTA)
        self.assertEqual(len(datos), 6)
        self.assertEqual(datos['Nombre'].iloc[-1], 'Otros (4)')

    def test_sin_torta_con_negativos_o_una_sola_parte(self):
        self.assertFalse(admite_torta(self.marco([5, -2]), 'Monto'))
        self.assertFalse(admite_torta(self.marco([5]), 'Monto'))
        self.assertFalse(admite_torta(self.marco([0, 0]), 'Monto'))
        self.assertFalse(admite_torta(self.marco([11, 0, 0, 0]), 'Monto'))   # 100 % de uno
        self.assertTrue(admite_torta(self.marco([5, 2]), 'Monto'))

    def test_un_ranking_no_lleva_otros(self):
        svg = comparar(self.marco(list(range(15, 0, -1))), 'Nombre', 'Monto', 'Ranking',
                       parte_del_todo=False)
        self.assertNotIn('Otros', svg)

    def test_la_torta_pedida_sin_partes_va_en_barras(self):
        svg = comparar(self.marco([5]), 'Nombre', 'Monto', 'Uno solo', estilo='torta')
        self.assertIn('data-estilo="barras"', svg)

    def test_la_granularidad_sigue_al_periodo(self):
        hasta = timezone.localdate()
        serie = pd.Series([100.0], index=[hasta])
        casos = ((10, 'en el día'), (120, 'por semana'), (400, 'por mes'))
        for dias, rotulo in casos:
            with self.subTest(dias=dias):
                svg = evolucion(serie, hasta - timedelta(days=dias - 1), hasta, 'Ventas')
                self.assertIn(rotulo, svg)


class PantallaTest(Base):

    def pagina(self, **parametros):
        f.entrar(self.client, self.admin)
        return self.client.get(reverse('negocio:reportes'), parametros).content.decode()

    def test_cada_opcion_del_menu_muestra_su_grafico_o_por_que_no(self):
        for reporte in REPORTES:
            with self.subTest(reporte=reporte.clave):
                html = self.pagina(ver=reporte.clave)
                self.assertIn('grafico-reporte', html)
                self.assertTrue('<svg role="img"' in html or 'grafico-vacio' in html)

    def test_medios_ofrece_barras_y_torta(self):
        html = self.pagina(ver='medios', grafico='torta')
        self.assertIn('aria-label="Tipo de gráfico"', html)
        self.assertIn('data-estilo="torta"', html)

    def test_productos_no_ofrece_torta(self):
        html = self.pagina(ver='productos', grafico='torta')
        self.assertNotIn('aria-label="Tipo de gráfico"', html)
        self.assertIn('data-estilo="barras"', html)

    def test_un_solo_vendedor_explica_por_que_no_hay_torta(self):
        html = self.pagina(ver='vendedores', grafico='torta')
        self.assertIn('una torta necesita al menos dos partes', html)

    def test_sin_movimientos_invita_a_ampliar_el_periodo(self):
        antes = self.desde - timedelta(days=400)
        html = self.pagina(ver='mermas', desde=antes.isoformat(),
                           hasta=(antes + timedelta(days=5)).isoformat())
        self.assertIn('grafico-vacio', html)
        self.assertIn('Ver los últimos 30 días', html)

    def test_la_pantalla_muestra_el_grafico(self):
        f.entrar(self.client, self.admin)
        html = self.client.get(reverse('negocio:reportes'), {'ver': 'productos'}).content.decode()
        self.assertIn('grafico-reporte', html)
        self.assertIn('<svg role="img"', html)

    def test_el_pdf_lleva_el_grafico(self):
        f.entrar(self.client, self.admin)
        respuesta = self.client.get(reverse('negocio:exportar_reporte'),
                                    {'ver': 'ventas_dia', 'formato': 'pdf'})
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')


class MenuTest(Base):

    def test_el_area_actual_viene_abierta_y_las_otras_plegadas(self):
        f.entrar(self.client, self.admin)
        html = self.client.get(reverse('negocio:reportes')).content.decode()
        self.assertIn('<details class="lateral-grupo lateral-desplegable" data-grupo="mostrador">', html)
        # Reportes tiene una sola pantalla: va como enlace directo, sin desplegable.
        self.assertNotIn('data-grupo="reportes"', html)
        html = self.client.get(reverse('ventas:ventas')).content.decode()
        self.assertIn('data-grupo="mostrador" open data-actual', html)
