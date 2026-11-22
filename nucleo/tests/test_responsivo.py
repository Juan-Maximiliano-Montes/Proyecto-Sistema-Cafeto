"""Diseño en celular y tablet, en un navegador real (DD-106).

El ingreso arriba y a la vista, los atajos del Inicio en filas completas,
la tarjeta del informe de Reportes con sus descargas directas, Mi perfil sin
datos cortados, la tabla de Ventas y la barra de cajas limpias, y ninguna
pantalla principal con desplazamiento horizontal ni errores en la consola.

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
CELULAR_CHICO = {'width': 400, 'height': 554}
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

    def con_pagina(self, tamanio, hacer, entrar=True, usuario=None):
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
                    pagina.fill('[name="identificador"]', (usuario or self.admin).nombre_usuario)
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

    def test_la_tarjeta_del_informe_descarga_directo_y_entra_en_la_pantalla(self):
        for tamanio in (CELULAR, CELULAR_CHICO, ESCRITORIO):
            def hacer(pagina):
                pagina.goto(self.live_server_url + reverse('negocio:reportes'))
                pagina.wait_for_load_state('networkidle')
                tarjeta = pagina.locator('.tarjeta-informe').bounding_box()
                titulo = pagina.locator('#titDetalle').bounding_box()
                botones = pagina.locator('.informe-descarga a')
                self.assertEqual(botones.all_inner_texts(), ['Excel', 'PDF'])
                cajas = [b.bounding_box() for b in botones.all()]
                for boton, caja in zip(botones.all(), cajas):
                    self.assertEqual(boton.get_attribute('hx-boost'), 'false')
                    self.assertEqual(boton.locator('i').get_attribute('class'), 'bi bi-download')
                    self.assertLessEqual(caja['x'] + caja['width'], tarjeta['x'] + tarjeta['width'] + 1)
                    self.assertGreaterEqual(caja['x'], tarjeta['x'] - 1)
                self.assertEqual(pagina.locator('.tarjeta-informe .dropdown-menu').count(), 0)
                if tamanio is ESCRITORIO:
                    self.assertLess(abs(cajas[0]['y'] - titulo['y']), titulo['height'] + 24)
                    self.assertGreater(cajas[0]['x'], titulo['x'] + titulo['width'])
                else:
                    self.assertGreater(cajas[0]['y'], titulo['y'] + titulo['height'] - 1)
                    self.assertEqual(round(cajas[0]['y']), round(cajas[1]['y']))
                    self.assertLess(abs(cajas[0]['width'] - cajas[1]['width']), 2)
                    for caja in cajas:
                        self.assertGreaterEqual(caja['height'], 44)
                        self.assertGreater(caja['width'], tarjeta['width'] * 0.4)
                filtros = pagina.locator('.informe-filtros').bounding_box()
                self.assertGreater(filtros['y'], cajas[0]['y'] + cajas[0]['height'] - 1)
                self.assertLessEqual(filtros['x'] + filtros['width'], tarjeta['x'] + tarjeta['width'] + 1)
                self.assertEqual(pagina.eval_on_selector('.informe-filtros', 'e => getComputedStyle(e).borderTopStyle'), 'solid')
                self.assertLessEqual(pagina.evaluate(DESBORDE), 1)
            self.con_pagina(tamanio, hacer)

    def test_mi_perfil_no_corta_ningun_dato_y_le_habla_a_cada_perfil(self):
        from seguridad.models import Usuario
        Usuario.objects.filter(pk=self.admin.pk).update(
            correo='administracion.del.local.cafeto.las.lomitas@correo-del-negocio.com.ar')
        operador = self.usuarios['Operador']
        for usuario, ve_usuarios in ((self.admin, True), (operador, False)):
            for tamanio in (CELULAR, CELULAR_CHICO, ESCRITORIO):
                def hacer(pagina):
                    pagina.goto(self.live_server_url + reverse('mi_perfil'))
                    pagina.wait_for_load_state('networkidle')
                    tarjeta = pagina.locator('article.tarjeta').bounding_box()
                    derecha = tarjeta['x'] + tarjeta['width']
                    for valor in pagina.eval_on_selector_all(
                            '.ficha dd', 'es => es.map(e => [e.getBoundingClientRect().right, e.scrollWidth - e.clientWidth])'):
                        self.assertLessEqual(valor[0], derecha + 0.5, tamanio)
                        self.assertLessEqual(valor[1], 0, tamanio)
                    if tamanio is not ESCRITORIO:
                        dt = pagina.locator('.ficha dt').first.bounding_box()
                        dd = pagina.locator('.ficha dd').first.bounding_box()
                        self.assertGreaterEqual(dd['y'], dt['y'] + dt['height'] - 1)
                    bajada = pagina.inner_text('.encabezado .bajada')
                    if ve_usuarios:
                        self.assertIn('Podés cambiar tu correo desde Usuarios', bajada)
                        self.assertEqual(pagina.get_attribute('.encabezado .bajada a', 'href'), reverse('seguridad:lista'))
                    else:
                        self.assertEqual(bajada, 'Estos datos solo los puede editar un administrador. Si algo está mal, avisale.')
                    self.assertLessEqual(pagina.evaluate(DESBORDE), 1)
                self.con_pagina(tamanio, hacer, usuario=usuario)

    def test_ventas_y_la_barra_de_cajas_quedan_limpias(self):
        for tamanio in (CELULAR, CELULAR_CHICO, ESCRITORIO):
            def hacer(pagina):
                pagina.goto(self.live_server_url + reverse('ventas:ventas') + '?desde=2000-01-01')
                pagina.wait_for_load_state('networkidle')
                self.assertLessEqual(pagina.evaluate(DESBORDE), 1)
                alineacion = pagina.eval_on_selector_all(
                    '.table thead th', 'es => es.map(e => [e.textContent.trim(), getComputedStyle(e).textAlign])')
                alineacion = dict(alineacion)
                self.assertEqual((alineacion['N°'], alineacion['Renglones'], alineacion['Estado']), ('center', 'center', 'center'))
                self.assertEqual((alineacion['Total'], alineacion['Fecha']), ('right', 'left'))
                cabeceras = pagina.eval_on_selector_all(
                    '.table thead th', 'es => es.slice(0, 2).map(e => { const r = e.getBoundingClientRect(); return [r.left, r.right]; })')
                self.assertLessEqual(cabeceras[0][1], cabeceras[1][0] + 0.5)
                if tamanio is ESCRITORIO:
                    return
                chips = pagina.eval_on_selector(
                    '.chips-cajas', 'e => [getComputedStyle(e).scrollbarWidth, e.offsetHeight - e.clientHeight]')
                self.assertEqual(chips, ['none', 0])
                barra = pagina.locator('.barra').bounding_box()
                for chip in pagina.locator('.chips-cajas .chip-caja').all():
                    caja = chip.bounding_box()
                    self.assertGreaterEqual(caja['y'], barra['y'])
                    self.assertLessEqual(caja['y'] + caja['height'], barra['y'] + barra['height'])
                tabla = pagina.locator('.table-responsive').first
                if not tabla.evaluate("e => e.classList.contains('desborda')"):
                    return
                tabla.evaluate('e => e.scrollLeft = 160')
                pagina.wait_for_timeout(100)
                for celda in pagina.eval_on_selector_all(
                        '.table-responsive.desborda .table tr > :first-child',
                        'es => es.map(e => { const s = getComputedStyle(e); return [s.position, s.backgroundColor]; })'):
                    self.assertEqual(celda[0], 'sticky')
                    self.assertRegex(celda[1], r'^rgb\(')
            self.con_pagina(tamanio, hacer)

    def test_los_filtros_ocupan_el_ancho_y_perfiles_no_tiene_etiquetas_sueltas(self):
        def hacer(pagina):
            pagina.goto(self.live_server_url + reverse('stock:ajustes'))
            pagina.wait_for_load_state('networkidle')
            barra = pagina.locator('#herramientas').bounding_box()
            for selector in pagina.locator('#herramientas select').all():
                caja = selector.bounding_box()
                self.assertGreater(caja['width'], barra['width'] * 0.85)
            pagina.goto(self.live_server_url + reverse('seguridad:perfiles'))
            pagina.wait_for_load_state('networkidle')
            sueltas = pagina.eval_on_selector_all(
                'label', 'es => es.filter(e => !e.control).map(e => e.textContent.trim())')
            self.assertEqual(sueltas, [])
            sin_nombre = pagina.eval_on_selector_all(
                'input:not([type=hidden]), select, textarea',
                'es => es.filter(e => !e.labels.length && !e.getAttribute("aria-label")).map(e => e.name)')
            self.assertEqual(sin_nombre, [])
            self.assertIn('son solo del administrador', pagina.inner_text('.encabezado .bajada'))
            pagina.goto(self.live_server_url + reverse('negocio:reportes'))
            pagina.wait_for_load_state('networkidle')
            etiqueta = pagina.eval_on_selector(
                '.informe-descarga-etiqueta', 'e => [getComputedStyle(e).fontWeight, parseFloat(getComputedStyle(e).fontSize)]')
            self.assertEqual(etiqueta[0], '600')
            self.assertGreaterEqual(etiqueta[1], 14)
        self.con_pagina(CELULAR, hacer)
