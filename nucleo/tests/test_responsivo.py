"""Diseño en celular y tablet, en un navegador real (DD-106).

El ingreso arriba y a la vista, los atajos del Inicio en filas completas,
el botón «Exportar» de Reportes dentro de la pantalla y ninguna pantalla
principal con desplazamiento horizontal ni errores en la consola.

    python manage.py test nucleo.tests.test_responsivo --tag=navegador
"""
import os
import unittest

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import tag
from django.urls import reverse

from nucleo.tests.test_navegador import _armar_datos

try:
    from playwright.sync_api import sync_playwright
except ImportError:              # pragma: no cover - fuera del entorno de pruebas
    sync_playwright = None

CELULAR = {'width': 390, 'height': 844}
TABLET = {'width': 768, 'height': 1024}
ESCRITORIO = {'width': 1280, 'height': 900}
PANTALLAS = ('home', 'negocio:reportes', 'ventas:ventas', 'compras:egresos', 'compras:compras',
             'stock:productos_lista', 'ventas:caja')
DESBORDE = 'document.documentElement.scrollWidth - window.innerWidth'


@tag('navegador')
class ResponsivoTest(StaticLiveServerTestCase):
    serialized_rollback = True

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest('Playwright no está instalado.')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
        super().setUpClass()

    def setUp(self):
        self.usuarios, self.clave = _armar_datos()
        self.admin = self.usuarios['Administrador']

    def con_pagina(self, tamanio, hacer, entrar=True):
        errores = []
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            try:
                pagina = navegador.new_page(viewport=tamanio)
                pagina.on('pageerror', lambda e: errores.append(f'JS: {e}'))
                pagina.on('console', lambda m: m.type == 'error' and not m.text.startswith('Failed to load resource')
                          and errores.append(f'consola: {m.text}'))
                pagina.on('response', lambda r: r.status >= 400 and '/static/' in r.url
                          and errores.append(f'{r.status} en {r.url}'))
                if entrar:
                    pagina.goto(self.live_server_url + reverse('seguridad:login'))
                    pagina.fill('[name="identificador"]', self.admin.nombre_usuario)
                    pagina.fill('[name="clave"]', self.clave)
                    pagina.click('form button[type="submit"]')
                    pagina.wait_for_load_state('networkidle')
                hacer(pagina)
            finally:
                navegador.close()
        self.assertEqual(errores, [])

    def test_el_ingreso_queda_arriba_y_entero_en_celular_y_tablet(self):
        for tamanio in (CELULAR, TABLET):
            def hacer(pagina):
                pagina.goto(self.live_server_url + reverse('seguridad:login'))
                caja = pagina.locator('.acceso-caja .card').bounding_box()
                boton = pagina.locator('form button[type="submit"]').bounding_box()
                self.assertLessEqual(boton['y'] + boton['height'], tamanio['height'], tamanio)
                self.assertGreaterEqual(boton['height'], 44)
                if tamanio is CELULAR:
                    self.assertLess(caja['y'], 160, 'la tarjeta de ingreso no puede quedar empujada abajo')
                self.assertLessEqual(pagina.evaluate(DESBORDE), 1)
            self.con_pagina(tamanio, hacer, entrar=False)

    def test_los_atajos_del_inicio_forman_filas_completas_alineadas_con_el_resumen(self):
        def hacer(pagina):
            pagina.goto(self.live_server_url + reverse('home'))
            atajos = pagina.eval_on_selector_all(
                '.grilla-atajos > a', 'es => es.map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width)]; })')
            resumen = pagina.eval_on_selector_all(
                '.grilla-resumen > a', 'es => es.map(e => Math.round(e.getBoundingClientRect().x))')
            self.assertEqual(len(atajos), 6)
            columnas = sorted({x for x, _y, _w in atajos})
            filas = sorted({y for _x, y, _w in atajos})
            self.assertEqual((len(columnas), len(filas)), (3, 2))
            self.assertEqual(len({w for _x, _y, w in atajos}), 1)
            self.assertEqual(sorted(resumen), columnas[:len(resumen)])
        self.con_pagina(ESCRITORIO, hacer)

    def test_ninguna_pantalla_principal_se_desborda_a_lo_ancho(self):
        for tamanio in (CELULAR, TABLET):
            def hacer(pagina):
                for nombre in PANTALLAS:
                    pagina.goto(self.live_server_url + reverse(nombre))
                    pagina.wait_for_load_state('networkidle')
                    self.assertLessEqual(pagina.evaluate(DESBORDE), 1, f'{nombre} a {tamanio["width"]} px')
            self.con_pagina(tamanio, hacer)

    def test_exportar_dice_que_exporta_y_su_menu_entra_en_la_pantalla(self):
        for tamanio in (CELULAR, TABLET):
            def hacer(pagina):
                pagina.goto(self.live_server_url + reverse('negocio:reportes'))
                pagina.wait_for_load_state('networkidle')
                boton = pagina.locator('.exportar-capsula-boton')
                titulo = pagina.inner_text('#titDetalle').strip()
                self.assertIn(f'Exportar «{titulo}»', boton.inner_text())
                boton.click()
                menu = pagina.locator('.exportar-capsula .dropdown-menu')
                menu.wait_for(state='visible')
                caja = menu.bounding_box()
                self.assertGreaterEqual(caja['x'], 0)
                self.assertLessEqual(caja['x'] + caja['width'], tamanio['width'] + 1)
                if tamanio is CELULAR:
                    titulo_caja = pagina.locator('#titDetalle').bounding_box()
                    boton_caja = boton.bounding_box()
                    self.assertGreater(boton_caja['y'], titulo_caja['y'])
                    self.assertGreater(boton_caja['width'], tamanio['width'] * 0.8)
                for enlace in pagina.eval_on_selector_all('.exportar-capsula .dropdown-item',
                                                          'es => es.map(e => e.getAttribute("hx-boost"))'):
                    self.assertEqual(enlace, 'false')
                self.assertLessEqual(pagina.evaluate(DESBORDE), 1)
            self.con_pagina(tamanio, hacer)
