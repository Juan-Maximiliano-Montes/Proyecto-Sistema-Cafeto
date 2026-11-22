"""Navegación fluida y tablero con ECharts en un navegador real (DD-105).

Lo que una prueba del servidor no ve: que cambiar de cápsula, de filtro o
de pantalla desde el menú no recargue la página ni vuelva arriba, que el
tablero se dibuje con ECharts y se vuelva a dibujar al cambiar el tema, y
que nada de eso deje errores en la consola ni pida un archivo que no está.

    python manage.py test nucleo.tests.test_navegacion_fluida --tag=navegador
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

GRAFICOS_DIBUJADOS = "document.querySelectorAll('[data-grafico] .grafico-echarts canvas').length >= 4"
# Los cuatro gráficos del tablero de la pantalla ACTUAL (#contenido) están
# vivos: cada uno con su instancia de ECharts sin soltar y un <canvas> que se
# ve (ancho y alto mayores que cero, en la página y en píxeles del lienzo).
CUATRO_A_LA_VISTA = """() => {
    const figuras = Array.from(document.querySelectorAll('#contenido [data-grafico]'));
    return figuras.length === 4 && figuras.every(figura => {
        const lugar = figura.querySelector('.grafico-echarts');
        const instancia = lugar && window.echarts && window.echarts.getInstanceByDom(lugar);
        const lienzo = lugar && lugar.querySelector('canvas');
        if (!instancia || instancia.isDisposed() || !lienzo || !lienzo.isConnected) { return false; }
        const caja = lienzo.getBoundingClientRect();
        return caja.width > 0 && caja.height > 0 && lienzo.width > 0 && lienzo.height > 0;
    });
}"""
# El gráfico de evolución dibuja los datos que trajo la ÚLTIMA respuesta, no
# los de la primera carga: tantos puntos como dice el #datosTablero de ahora.
EVOLUCION_CON_DATOS_FRESCOS = """() => {
    const fuente = document.querySelector('#contenido #datosTablero');
    const lugar = document.querySelector('#contenido [data-grafico=evolucion] .grafico-echarts');
    const instancia = fuente && lugar && window.echarts.getInstanceByDom(lugar);
    if (!instancia) { return null; }
    const texto = fuente.content ? fuente.content.textContent : fuente.textContent;
    return [JSON.parse(texto).evolucion.puntos.length, instancia.getOption().xAxis[0].data.length];
}"""


@tag('navegador')
class NavegacionFluidaTest(StaticLiveServerTestCase):
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

    def url(self, nombre):
        return self.live_server_url + reverse(nombre)

    def con_pagina(self, hacer, ancho=1366):
        errores, parciales = [], []
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            try:
                pagina = navegador.new_page(viewport={'width': ancho, 'height': 800})
                pagina.on('pageerror', lambda e: errores.append(f'JS: {e}'))
                pagina.on('console', lambda m: m.type == 'error' and not m.text.startswith('Failed to load resource')
                          and errores.append(f'consola: {m.text}'))
                pagina.on('response', lambda r: r.status >= 400 and '/static/' in r.url
                          and errores.append(f'{r.status} en {r.url}'))
                pagina.on('request', lambda r: r.headers.get('hx-request') and parciales.append(r.url))
                pagina.goto(self.live_server_url + '/login/')
                pagina.fill('[name="identificador"]', self.admin.nombre_usuario)
                pagina.fill('[name="clave"]', self.clave)
                pagina.click('form button[type="submit"]')
                pagina.wait_for_load_state('networkidle')
                hacer(pagina, parciales)
            finally:
                navegador.close()
        self.assertEqual(errores, [])

    @staticmethod
    def abrir_area(pagina, href):
        pagina.evaluate(f"document.querySelector('.lateral a[href=\"{href}\"]').closest('details').open = true")

    @staticmethod
    def marcar(pagina):
        pagina.evaluate('window.__sinRecargar = 1')

    @staticmethod
    def sigue_sin_recargar(pagina):
        return pagina.evaluate('window.__sinRecargar === 1')

    # ------------------------------------------------------------------
    def test_la_capsula_cambia_sin_recargar_ni_saltar_ni_redibujar_el_tablero(self):
        def hacer(pagina, parciales):
            pagina.goto(self.url('negocio:reportes'))
            pagina.wait_for_function(GRAFICOS_DIBUJADOS)
            self.marcar(pagina)
            pagina.evaluate("document.querySelector('[data-grafico=evolucion] canvas').dataset.marca = 'x'")
            capsula = pagina.locator('.capsulas-reportes nav a:not([aria-current])').first
            clave = capsula.get_attribute('href').split('ver=')[1].split('&')[0]
            pagina.evaluate("document.querySelector('.capsulas-reportes').scrollIntoView({block: 'center', behavior: 'instant'})")
            antes = pagina.evaluate('window.scrollY')
            self.assertGreater(antes, 300)
            capsula.click()
            pagina.wait_for_function(f"location.search.includes('ver={clave}')")
            pagina.wait_for_selector(f'.capsulas-reportes nav a[aria-current="page"][href*="ver={clave}&"]')
            pagina.wait_for_load_state('networkidle')
            self.assertTrue(self.sigue_sin_recargar(pagina))
            ahora, tope = pagina.evaluate('[window.scrollY, document.documentElement.scrollHeight - window.innerHeight]')
            self.assertLess(abs(ahora - min(antes, tope)), 4)
            self.assertEqual(pagina.evaluate('document.activeElement.getAttribute("aria-current")'), 'page')
            self.assertEqual(pagina.evaluate("document.querySelector('[data-grafico=evolucion] canvas').dataset.marca"), 'x')
            enlaces = pagina.eval_on_selector_all('#periodosTablero a', 'es => es.map(e => e.getAttribute("href"))')
            self.assertTrue(enlaces and all(f'ver={clave}&' in e for e in enlaces), enlaces)
            titulo = pagina.inner_text('#titDetalle').strip()
            self.assertIn(f'Exportar «{titulo}»', pagina.inner_text('.exportar-capsula-boton'))
            self.assertTrue(any('/negocio/reportes/' in u for u in parciales))
        self.con_pagina(hacer)

    def test_mapa_de_calor_y_tema_redibujan_los_graficos(self):
        def hacer(pagina, parciales):
            pagina.goto(self.url('negocio:reportes'))
            pagina.wait_for_function(GRAFICOS_DIBUJADOS)
            pagina.click('[data-franja-vista="calor"]')
            pagina.wait_for_function("document.querySelector('[data-grafico=franjas] .js-pico').textContent.includes('Más movimiento')")
            self.assertEqual(pagina.get_attribute('[data-franja-vista="calor"]', 'aria-pressed'), 'true')
            pagina.evaluate("document.querySelectorAll('[data-grafico] canvas').forEach(c => c.dataset.marca = 'x')")
            pagina.click('[data-tema-alternar]')
            pagina.wait_for_function("document.documentElement.dataset.tema === 'claro'")
            pagina.wait_for_function("Array.from(document.querySelectorAll('[data-grafico] canvas')).every(c => !c.dataset.marca)"
                                     " && document.querySelectorAll('[data-grafico] canvas').length >= 4")
            pagina.set_viewport_size({'width': 390, 'height': 800})
            pagina.wait_for_timeout(400)
            self.assertGreaterEqual(pagina.evaluate("document.querySelectorAll('[data-grafico] canvas').length"), 4)
        self.con_pagina(hacer)

    def test_el_menu_cambia_de_pantalla_sin_recargar_y_marca_la_actual(self):
        def hacer(pagina, parciales):
            pagina.goto(self.url('ventas:ventas'))
            pagina.wait_for_load_state('networkidle')
            self.marcar(pagina)
            pagina.click('.lateral a.lateral-link[href="/ventas/turnos/"]')
            pagina.wait_for_function("location.pathname === '/ventas/turnos/' && document.title.includes('Turnos')")
            pagina.wait_for_selector('.lateral a.lateral-link.activo[href="/ventas/turnos/"]')
            pagina.wait_for_load_state('networkidle')
            self.assertTrue(self.sigue_sin_recargar(pagina))
            activos = pagina.eval_on_selector_all('.lateral .lateral-link.activo', 'es => es.map(e => e.getAttribute("href"))')
            self.assertEqual(activos, ['/ventas/turnos/'])
            self.assertEqual(pagina.get_attribute('.lateral a[href="/ventas/turnos/"]', 'aria-current'), 'page')
            self.assertEqual(pagina.evaluate('window.scrollY'), 0)
            self.assertEqual(pagina.locator('#barra').count(), 1)
            pagina.go_back()
            pagina.wait_for_function("location.pathname === '/ventas/ventas/'")
            pagina.wait_for_load_state('networkidle')
            self.assertEqual(pagina.locator('.lateral a[href="/ventas/ventas/"].activo').count(), 1)
        self.con_pagina(hacer)

    def test_una_pantalla_con_scripts_propios_se_abre_entera_y_la_proxima_vez_directo(self):
        def hacer(pagina, parciales):
            pagina.goto(self.url('ventas:ventas'))
            pagina.wait_for_load_state('networkidle')
            self.marcar(pagina)
            self.abrir_area(pagina, '/negocio/reportes/')
            pagina.click('.lateral a.lateral-link[href="/negocio/reportes/"]')
            pagina.wait_for_function(GRAFICOS_DIBUJADOS)
            self.assertFalse(self.sigue_sin_recargar(pagina))
            pagina.goto(self.url('ventas:ventas'))
            pagina.wait_for_load_state('networkidle')
            parciales.clear()
            self.abrir_area(pagina, '/negocio/reportes/')
            pagina.click('.lateral a.lateral-link[href="/negocio/reportes/"]')
            pagina.wait_for_function(GRAFICOS_DIBUJADOS)
            self.assertFalse(any('/negocio/reportes/' in u for u in parciales), parciales)
        self.con_pagina(hacer)

    def test_un_filtro_se_aplica_sin_recargar(self):
        def hacer(pagina, parciales):
            pagina.goto(self.url('ventas:ventas'))
            pagina.wait_for_load_state('networkidle')
            self.marcar(pagina)
            pagina.select_option('#herramientas [name="estado"]', 'ANULADA')
            pagina.wait_for_function("location.search.includes('estado=ANULADA')")
            pagina.wait_for_load_state('networkidle')
            self.assertTrue(self.sigue_sin_recargar(pagina))
            self.assertEqual(pagina.input_value('#herramientas [name="estado"]'), 'ANULADA')
        self.con_pagina(hacer)

    def test_sin_sesion_el_menu_lleva_al_ingreso(self):
        def hacer(pagina, parciales):
            pagina.goto(self.url('ventas:ventas'))
            pagina.wait_for_load_state('networkidle')
            pagina.context.clear_cookies()
            pagina.click('.lateral a.lateral-link[href="/ventas/turnos/"]')
            pagina.wait_for_function("location.pathname.startsWith('/login/')")
            self.assertEqual(pagina.locator('[name="identificador"]').count(), 1)
        self.con_pagina(hacer)

    def test_el_area_abierta_del_menu_sigue_abierta_al_volver(self):
        def hacer(pagina, parciales):
            pagina.goto(self.url('ventas:ventas'))
            pagina.wait_for_load_state('networkidle')
            cerrada = pagina.locator('.lateral details.lateral-desplegable:not([open])').first
            grupo = cerrada.get_attribute('data-grupo')
            cerrada.locator('summary').click()
            pagina.goto(self.url('ventas:turnos'))
            pagina.wait_for_load_state('domcontentloaded')
            self.assertTrue(pagina.evaluate(f"document.querySelector('details[data-grupo=\"{grupo}\"]').open"))
        self.con_pagina(hacer)


    # ------------------------------------------------------------------
    #  El tablero de ECharts sobrevive a cada actualización parcial
    # ------------------------------------------------------------------
    def elegir_periodo(self, pagina, etiqueta):
        pagina.locator('#periodosTablero a', has_text=etiqueta).click()
        pagina.wait_for_function(
            "e => { const a = document.querySelector('#periodosTablero a[aria-current]');"
            " return a && a.textContent.trim() === e; }", arg=etiqueta)
        pagina.wait_for_load_state('networkidle')
        pagina.wait_for_function(CUATRO_A_LA_VISTA)
        puntos, dibujados = pagina.evaluate(EVOLUCION_CON_DATOS_FRESCOS)
        self.assertEqual(puntos, dibujados, etiqueta)

    @staticmethod
    def unidad(pagina):
        return pagina.inner_text('#contenido [data-grafico=evolucion] figcaption .text-tenue').strip()

    def test_desde_inicio_el_tablero_se_dibuja_y_sigue_vivo_tras_cada_cambio_parcial(self):
        def hacer(pagina, parciales):
            pagina.goto(self.url('home'))
            pagina.wait_for_load_state('networkidle')
            self.abrir_area(pagina, '/negocio/reportes/')
            pagina.click('.lateral a.lateral-link[href="/negocio/reportes/"]')
            pagina.wait_for_function("location.pathname === '/negocio/reportes/'")
            pagina.wait_for_load_state('networkidle')
            pagina.wait_for_function(CUATRO_A_LA_VISTA)

            # El período cambia todo #contenido (outerHTML): datos nuevos y
            # los cuatro gráficos dibujados otra vez sobre los nodos nuevos.
            self.marcar(pagina)
            parciales.clear()
            self.elegir_periodo(pagina, 'Hoy')
            self.assertEqual(self.unidad(pagina), 'por día')
            self.elegir_periodo(pagina, '90 días')
            self.assertEqual(self.unidad(pagina), 'por semana')
            desde = pagina.evaluate("(() => { const f = document.getElementById('datosTablero');"
                                    " return JSON.parse(f.content ? f.content.textContent : f.textContent).desde; })()")
            self.assertIn(f'desde={desde}', pagina.evaluate('location.search'))
            self.assertTrue(self.sigue_sin_recargar(pagina))
            self.assertTrue(any('/negocio/reportes/' in u for u in parciales), parciales)
            self.assertEqual(pagina.locator('#datosTablero').count(), 1)

            # Los botones de cada gráfico redibujan sólo ese gráfico.
            for boton in ('[data-franja-vista="calor"]', '[data-franja-por="monto"]', '[data-top-de="rubros"]',
                          '[data-top-por="ganancia"]', '[data-serie="egresos"]', '[data-franja-vista="barras"]'):
                pagina.click(f'#contenido {boton}')
                pagina.wait_for_timeout(150)
                pagina.wait_for_function(CUATRO_A_LA_VISTA)
            self.assertEqual(pagina.get_attribute('#contenido [data-serie="egresos"]', 'aria-pressed'), 'false')

            # Una cápsula de abajo cambia sólo #seccion-reportes: el tablero
            # de arriba no se suelta ni se vuelve a dibujar.
            pagina.evaluate("document.querySelectorAll('#contenido [data-grafico] canvas')"
                            ".forEach(c => c.dataset.marca = 'x')")
            pagina.click('.capsulas-reportes nav a[href*="ver=medios&"]')
            pagina.wait_for_selector('.capsulas-reportes nav a[aria-current="page"][href*="ver=medios&"]')
            pagina.wait_for_load_state('networkidle')
            pagina.wait_for_function(CUATRO_A_LA_VISTA)
            self.assertTrue(pagina.evaluate(
                "Array.from(document.querySelectorAll('#contenido [data-grafico] canvas'))"
                ".every(c => c.dataset.marca === 'x')"))
            self.assertEqual(self.unidad(pagina), 'por semana')
            self.assertTrue(self.sigue_sin_recargar(pagina))

            # Y otra vez el período, ya con la cápsula cambiada.
            self.elegir_periodo(pagina, '7 días')
            self.assertIn('ver=medios', pagina.evaluate('location.search'))
            self.assertTrue(self.sigue_sin_recargar(pagina))
        self.con_pagina(hacer)

    def test_el_resumen_del_mes_dibuja_el_tablero_al_llegar_desde_reportes(self):
        def hacer(pagina, parciales):
            pagina.goto(self.url('home'))
            pagina.wait_for_load_state('networkidle')
            self.abrir_area(pagina, '/negocio/reportes/')
            pagina.click('.lateral a.lateral-link[href="/negocio/reportes/"]')
            pagina.wait_for_function(CUATRO_A_LA_VISTA)
            pagina.click('#contenido .acciones a[href="/negocio/resumen-del-mes/"]')
            pagina.wait_for_function("location.pathname === '/negocio/resumen-del-mes/'")
            pagina.wait_for_load_state('networkidle')
            pagina.wait_for_function(CUATRO_A_LA_VISTA)
            pagina.click('#contenido [data-franja-vista="calor"]')
            pagina.wait_for_function(CUATRO_A_LA_VISTA)
        self.con_pagina(hacer)
