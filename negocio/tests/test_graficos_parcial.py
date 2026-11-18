"""El comando del Parcial N° 2 (DD-102): cuatro PNG desde la base, con los
mismos datos que la pantalla de Reportes."""
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal as D
from io import StringIO
from pathlib import Path

import pandas as pd
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from negocio import graficos
from negocio.management.commands.generar_graficos_parcial import (
    ARCHIVOS,
    estadisticas,
    operaciones_por_dia_y_hora,
    resumen_estadistico,
    serie_diaria,
    vendido_por_rubro,
)
from nucleo.tests import fabrica as f
from ventas import servicios as ventas

FIRMA_PNG = b'\x89PNG\r\n\x1a\n'


class ConCarpetaTemporal(TestCase):

    def setUp(self):
        self.carpeta = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.carpeta, ignore_errors=True)

    def correr(self, *argumentos):
        salida, errores = StringIO(), StringIO()
        call_command('generar_graficos_parcial', '--salida', str(self.carpeta), *argumentos,
                     stdout=salida, stderr=errores)
        return salida.getvalue(), errores.getvalue()


class ComandoConVentasTest(ConCarpetaTemporal):

    @classmethod
    def setUpTestData(cls):
        cls.cajera = f.usuario('40700001', 'Operador')
        prov = f.proveedor()
        cls.alfajor = f.producto('Alfajor', prov=prov, precio='1000')
        cls.cafe = f.producto('Café en lata', prov=prov, precio='2500', rubro_nombre='Cafetería')
        f.comprar(cls.cajera, prov, (cls.alfajor, 20, '600'), (cls.cafe, 10, '1500'))
        turno = f.abrir_turno(cls.cajera)
        for producto, cantidad, medio in ((cls.alfajor, 2, 'Efectivo'), (cls.cafe, 1, 'QR'),
                                          (cls.alfajor, 1, 'Tarjeta de débito')):
            ventas.registrar(turno, cls.cajera, [ventas.Item(producto, D(cantidad))],
                             [ventas.Pago(f.medio(medio))])

    def test_genera_los_cuatro_png_y_dice_donde_quedaron(self):
        salida, errores = self.correr('--dias', '30')
        for nombre in ARCHIVOS.values():
            ruta = self.carpeta / nombre
            self.assertTrue(ruta.exists(), nombre)
            self.assertEqual(ruta.read_bytes()[:8], FIRMA_PNG, nombre)
            self.assertGreater(ruta.stat().st_size, 5_000, nombre)
            self.assertIn(f'PNG creado: {ruta}', salida)
        self.assertIn('Listo: 4 de 4 gráficos', salida)
        self.assertEqual(errores, '')
        self.assertIn('Resumen estadístico (numpy):', salida)
        self.assertIn('Total vendido (np.sum): $ 5.500,00 en 30 días (1 con ventas)', salida)
        self.assertIn('Tickets: 3 · ticket promedio $ 1.833,33', salida)

    def test_una_franja_fija_de_horas(self):
        salida, _ = self.correr('--dias', '1', '--horas', '0-23')
        self.assertIn('Listo: 4 de 4 gráficos', salida)
        with self.assertRaisesMessage(CommandError, 'como 8-22'):
            self.correr('--horas', 'mañana')
        with self.assertRaisesMessage(CommandError, 'de 0 a 23'):
            self.correr('--horas', '22-8')

    def test_los_datos_son_los_mismos_que_la_pantalla(self):
        hoy = timezone.localdate()
        rubros = vendido_por_rubro(hoy, hoy).set_index('Rubro')['Vendido']
        self.assertEqual(rubros.to_dict(), {'Minisuper': 3000.0, 'Cafetería': 2500.0})
        operaciones = operaciones_por_dia_y_hora(hoy, hoy)
        self.assertEqual(int(operaciones['tickets'].sum()), 3)
        self.assertEqual(set(operaciones['dia']), {hoy.weekday()})

    def test_el_periodo_anterior_sin_ventas_se_marca_como_nuevo(self):
        hoy = timezone.localdate()
        ahora = vendido_por_rubro(hoy, hoy)
        antes = vendido_por_rubro(hoy - timedelta(days=1), hoy - timedelta(days=1))
        self.assertTrue(antes.empty)
        marco = ahora.merge(antes, on='Rubro', how='outer', suffixes=(' ahora', ' antes')).fillna(0.0)
        self.assertEqual(list(marco['Vendido antes']), [0.0, 0.0])


class EstadisticasTest(TestCase):
    """DD-103: el resumen sale de numpy (np.sum, np.mean, np.std,
    np.percentile) sobre lo vendido por día, con cero los días sin venta."""

    def test_los_numeros_son_los_de_numpy(self):
        import numpy as np
        valores = [0.0, 1000.0, 2000.0, 3000.0, 4000.0]
        datos = estadisticas(valores)
        self.assertEqual(datos['total'], 10000.0)
        self.assertEqual(datos['media'], 2000.0)
        self.assertAlmostEqual(datos['desvio'], float(np.std(valores, ddof=1)))
        self.assertEqual((datos['p25'], datos['p50'], datos['p75']), (1000.0, 2000.0, 3000.0))
        self.assertAlmostEqual(datos['p90'], 3600.0)
        self.assertAlmostEqual(datos['tendencia'], 1000.0)
        self.assertIsNone(estadisticas([]))

    def test_los_dias_sin_ventas_cuentan_como_cero(self):
        hoy = timezone.localdate()
        dias = pd.DataFrame({'Día': [hoy], 'Vendido': [900.0]})
        serie = serie_diaria(dias, hoy - timedelta(days=2), hoy)
        self.assertEqual(list(serie.values), [0.0, 0.0, 900.0])
        lineas = '\n'.join(resumen_estadistico(serie.values, [400.0, 500.0]))
        self.assertIn('Media por día (np.mean): $ 300,00', lineas)
        self.assertIn('3 días (1 con ventas)', lineas)
        self.assertIn('Percentiles por día (np.percentile)', lineas)
        self.assertIn('Desvío estándar por día (np.std): $ 519,62 (coeficiente de variación 173,2 %)', lineas)
        self.assertIn('ticket promedio $ 450,00', lineas)

    def test_la_evolucion_dibuja_la_media_y_la_tendencia(self):
        hoy = timezone.localdate()
        desde = hoy - timedelta(days=29)
        serie = pd.Series([float(100 * i) for i in range(30)], index=pd.date_range(desde, hoy))
        svg = graficos.evolucion(serie, desde, hoy, 'Evolución', forma='linea', estadisticas=True)
        self.assertIn('Media por día', svg)
        self.assertIn('Tendencia: +$ 100 por día', svg)
        self.assertNotIn('Media por día', graficos.evolucion(serie, desde, hoy, 'Evolución'))


class ComandoSinDatosTest(ConCarpetaTemporal):

    def test_sin_ventas_no_inventa_graficos(self):
        with self.assertRaisesMessage(CommandError, 'No hay datos para graficar'):
            self.correr('--dias', '7')
        self.assertEqual(list(self.carpeta.glob('*.png')), [])

    def test_fechas_mal_escritas_o_al_reves(self):
        with self.assertRaisesMessage(CommandError, 'AAAA-MM-DD'):
            self.correr('--desde', '31/10/2026')
        with self.assertRaisesMessage(CommandError, 'no puede ser posterior'):
            self.correr('--desde', '2026-11-10', '--hasta', '2026-11-01')


class GraficosNuevosTest(TestCase):

    def test_el_mapa_de_calor_completa_las_horas_sin_movimiento(self):
        marco = pd.DataFrame({'dia': [0, 0, 4], 'hora': [9, 12, 12], 'tickets': [3, 1, 5]})
        svg = graficos.mapa_de_calor(marco, 'Tickets por día y hora')
        self.assertIn('aria-label="Tickets por día y hora"', svg)
        for dia in graficos.DIAS_DE_LA_SEMANA:
            self.assertIn(dia, svg)
        self.assertIsNone(graficos.mapa_de_calor(marco.iloc[0:0], 'vacío'))

    def test_las_barras_comparativas_escriben_la_variacion(self):
        marco = pd.DataFrame({'Rubro': ['Cafetería', 'Minisuper'], 'Ahora': [1500.0, 800.0],
                              'Antes': [1000.0, 0.0]})
        svg = graficos.comparar_periodos(marco, 'Rubro', 'Ahora', 'Antes', 'Comparación')
        self.assertIn('(+50%)', svg)
        self.assertIn('(nuevo)', svg)

    def test_en_modo_png_devuelve_la_ruta(self):
        carpeta = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, carpeta, ignore_errors=True)
        marco = pd.DataFrame({'Medio': ['Efectivo', 'QR'], 'Total': [700.0, 300.0]})
        with graficos.a_png(carpeta / 'dona.png'):
            ruta = graficos.participacion(marco, 'Medio', 'Total', 'Dona', colores=graficos.PAPEL)
        self.assertEqual(ruta, carpeta / 'dona.png')
        self.assertEqual(ruta.read_bytes()[:8], FIRMA_PNG)
        self.assertTrue(graficos.participacion(marco, 'Medio', 'Total', 'Dona').startswith('<svg'))

    def test_dos_marcas_del_eje_nunca_dicen_lo_mismo(self):
        marcas = [graficos._pesos_cortos(v) for v in (1000, 1500, 2000, 2500, 3000, 3600)]
        self.assertEqual(len(set(marcas)), len(marcas))
        self.assertEqual(graficos._pesos_cortos(3600), '$ 3,6 mil')
        self.assertEqual(graficos._pesos_cortos(1_250_000), '$ 1,2 M')
