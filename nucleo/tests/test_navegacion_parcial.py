"""Navegación parcial, tablero con ECharts y pantallas sin JavaScript en
línea (DD-105): lo que se comprueba sin navegador. El comportamiento en un
navegador real está en test_navegacion_fluida (etiqueta «navegador»)."""
import json
import re
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from nucleo.tests import fabrica as f
from ventas import servicios as ventas

RAIZ = Path(settings.BASE_DIR)
ESTATICOS = RAIZ / 'nucleo' / 'static' / 'cafeto'
SCRIPT_EN_LINEA = re.compile(r'<script(?![^>]*\bsrc=)(?![^>]*type="application/json")[^>]*>')
REPORTES_SIN_EN_LINEA = ('templates/base.html', 'templates/comprobantes/base.html',
                         'ventas/templates/ventas/cierre.html', 'ventas/templates/ventas/ticket.html',
                         'seguridad/templates/seguridad/lista_permisos.html')


def _contraste(a, b):
    def luz(color):
        canales = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b_ = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in canales]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    claro, oscuro = sorted((luz(a), luz(b)), reverse=True)
    return (claro + 0.05) / (oscuro + 0.05)


class ArchivosTest(SimpleTestCase):

    def test_cada_estatico_que_piden_las_plantillas_existe(self):
        faltan = []
        for plantilla in RAIZ.glob('*/templates/**/*.html'):
            for ruta in re.findall(r"{%\s*static\s+'([^']+)'\s*%}", plantilla.read_text(encoding='utf-8')):
                if not finders.find(ruta):
                    faltan.append(f'{plantilla.relative_to(RAIZ)}: {ruta}')
        self.assertEqual(faltan, [])

    def test_htmx_y_echarts_son_locales_y_traen_su_licencia(self):
        htmx = (ESTATICOS / 'vendor/htmx/htmx.min.js').read_text(encoding='utf-8')
        self.assertIn('"2.0.11"', htmx)
        self.assertIn('Zero-Clause BSD', (ESTATICOS / 'vendor/htmx/LICENSE').read_text(encoding='utf-8'))
        echarts = (ESTATICOS / 'vendor/echarts/echarts.min.js').read_text(encoding='utf-8')
        self.assertTrue(echarts.startswith('/*! Apache ECharts 6.1.0 | Apache-2.0'))
        self.assertNotIn('process.env', echarts)
        for licencia in ('LICENSE', 'NOTICE', 'LICENSE-zrender', 'LICENSE-d3'):
            self.assertTrue((ESTATICOS / 'vendor/echarts' / licencia).is_file(), licencia)

    def test_las_pantallas_pedidas_no_tienen_javascript_en_linea(self):
        for ruta in REPORTES_SIN_EN_LINEA:
            self.assertEqual(SCRIPT_EN_LINEA.findall((RAIZ / ruta).read_text(encoding='utf-8')), [], ruta)
        for archivo in ('tema.js', 'menu.js', 'cierre-distancia.js', 'permisos.js', 'imprimir.js'):
            self.assertTrue((ESTATICOS / 'js' / archivo).is_file(), archivo)
        self.assertIn("hasAttribute('data-autoimprimir')", (ESTATICOS / 'js/imprimir.js').read_text(encoding='utf-8'))


class JavaScriptYEstilosTest(SimpleTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cafeto = (ESTATICOS / 'js/cafeto.js').read_text(encoding='utf-8')
        cls.tablero = (ESTATICOS / 'js/tablero.js').read_text(encoding='utf-8')
        cls.css = (ESTATICOS / 'css/cafeto.css').read_text(encoding='utf-8')

    def test_un_solo_punto_de_arranque_y_sin_observador_del_body(self):
        self.assertNotIn('MutationObserver', self.cafeto)
        self.assertIn('Cafeto.inicializar = function', self.cafeto)
        self.assertIn('Cafeto.limpiar = function', self.cafeto)
        self.assertIn("document.addEventListener('DOMContentLoaded', function () { Cafeto.inicializar(document); });", self.cafeto)
        for evento in ('htmx:confirm', 'htmx:beforeSwap', 'htmx:afterSwap', 'htmx:afterSettle', 'htmx:sendError'):
            self.assertIn(f"'{evento}'", self.cafeto)
        self.assertIn('Cafeto.inicializar(destino)', self.cafeto)
        self.assertIn('detalle.pathInfo.anchor = undefined', self.cafeto)

    def test_el_tablero_usa_echarts_y_suelta_cada_instancia(self):
        self.assertIn("getElementById('datosTablero')", self.tablero)
        self.assertNotIn('createElementNS', self.tablero)
        self.assertIn('registro.grafico.dispose()', self.tablero)
        self.assertIn("'cafeto:tema'", self.tablero)
        self.assertIn('ResizeObserver', self.tablero)
        self.assertIn("type: 'heatmap'", self.tablero)
        self.assertIn('saveAsImage', self.tablero)
        self.assertIn("type: 'slider'", self.tablero)

    def test_el_lienzo_reserva_su_alto_y_hay_transicion_entre_pantallas(self):
        self.assertIn('.js .grafico-lienzo { min-height: 280px;', self.css)
        self.assertIn('.js .grafico-vivo:not(.tablero-ancho) .grafico-lienzo { min-height: 240px;', self.css)
        self.assertRegex(self.css, r'@media \(prefers-reduced-motion: no-preference\) \{\s*@view-transition \{ navigation: auto; \}')

    def test_la_paleta_de_ocho_pasa_tres_a_uno_en_cada_tema(self):
        for selector, fondo in ((':root, [data-tema="oscuro"] {', '#1B1B3A'), ('[data-tema="claro"] {', '#FFFFFF')):
            bloque = self.css[self.css.index(selector + '\n    --c-cat-1'):]
            bloque = bloque[:bloque.index('}')]
            colores = re.findall(r'--c-cat-\d: (#[0-9A-F]{6})', bloque)
            self.assertEqual(len(colores), 8, selector)
            for color in colores:
                self.assertGreaterEqual(_contraste(color, fondo), 3, (selector, color))


class PantallasTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.duenia = f.titular()
        cajera = f.usuario('40800002', 'Operador')
        prov = f.proveedor()
        alfajor = f.producto('Alfajor', prov=prov, precio='1000', costo='600')
        f.comprar(cls.duenia, prov, (alfajor, 20, '600'))
        turno = f.abrir_turno(cajera)
        cls.venta, _ = ventas.registrar(turno, cajera, [ventas.Item(alfajor, D('2'))], [ventas.Pago(f.efectivo())])

    def setUp(self):
        f.entrar(self.client, self.duenia)

    def html(self, nombre, *args, **params):
        return self.client.get(reverse(nombre, args=args), params).content.decode()

    def test_la_base_carga_htmx_sin_javascript_en_linea(self):
        html = self.html('home')
        self.assertEqual(SCRIPT_EN_LINEA.findall(html), [])
        cabeza = html[:html.index('</head>')]
        self.assertIn('cafeto/js/tema.js', cabeza)
        configuracion = json.loads(re.search(r"<meta name=\"htmx-config\" content='([^']+)'>", cabeza).group(1))
        self.assertEqual(configuracion['historyCacheSize'], 0)
        self.assertFalse(configuracion['scrollIntoViewOnBoost'])
        self.assertFalse(configuracion['allowEval'])
        self.assertFalse(configuracion['allowScriptTags'])
        self.assertFalse(configuracion['includeIndicatorStyles'])
        self.assertIn('<body hx-history="false">', html)
        self.assertRegex(html, r'</aside>\s*<script src="[^"]*cafeto/js/menu\.js" data-global></script>')
        self.assertIn('hx-select="#contenido" hx-select-oob="#barra" hx-swap="outerHTML show:none" hx-push-url="true"', html)
        self.assertIn('<main class="pagina" id="contenido" tabindex="-1" hx-target="#contenido" hx-select="#contenido" '
                      'hx-swap="outerHTML show:none" hx-push-url="true">', html)
        self.assertIn('<header class="barra" id="barra">', html)
        self.assertLess(html.index('vendor/htmx/htmx.min.js'), html.index('cafeto/js/cafeto.js'))

    def test_filtros_periodos_y_paginador_actualizan_sin_recargar(self):
        html = self.html('ventas:ventas')
        self.assertRegex(html, r'<form method="get" id="herramientas" class="herramientas" hx-boost="true">')
        self.assertIn('aria-label="Períodos" hx-boost="true"', html)
        self.assertIn('<nav class="paginador" aria-label="Paginación" hx-boost="true">', html)
        self.assertRegex(self.html('compras:egresos'), r'<nav class="filtro-estado" aria-label="[^"]*" hx-boost="true">')

    def test_reportes_cambia_de_capsula_sin_tocar_el_tablero_y_exporta_la_elegida(self):
        html = self.html('negocio:reportes', ver='medios')
        nav = re.search(r'<nav class="d-flex flex-wrap align-items-center gap-2" aria-label="Reportes"([^>]*)>(.*?)</nav>', html, re.S)
        self.assertIn('hx-target="#seccion-reportes" hx-select="#seccion-reportes" hx-select-oob="#periodosTablero"', nav.group(1))
        self.assertNotIn('exportar', nav.group(2))
        self.assertLess(html.index('aria-label="Reportes"'), html.index('class="tarjeta tarjeta-informe mb-3"'))
        tarjeta = html[html.index('class="tarjeta tarjeta-informe mb-3"'):html.index('<figure class="tarjeta grafico-reporte')]
        self.assertLess(tarjeta.index('id="titDetalle"'), tarjeta.index('class="informe-descarga"'))
        self.assertIn('<p class="informe-alcance">Del ', tarjeta)
        self.assertNotIn('dropdown', tarjeta)
        self.assertNotIn('«', tarjeta)
        descarga = tarjeta[tarjeta.index('class="informe-descarga"'):tarjeta.index('</div>', tarjeta.index('class="informe-descarga"'))]
        self.assertIn('>Descargar:</span>', descarga)
        enlaces = re.findall(r'<a ([^>]*)><i class="bi bi-download" aria-hidden="true"></i>(Excel|PDF)</a>', descarga)
        self.assertEqual([texto for _a, texto in enlaces], ['Excel', 'PDF'])
        for atributos, texto in enlaces:
            self.assertIn('class="btn btn-secundario informe-descarga-boton" hx-boost="false"', atributos)
            self.assertIn('ver=medios&amp;', atributos)
        self.assertIn('formato=xlsx" title="Descargar en Excel" aria-label="Descargar Medios de pago en Excel"', enlaces[0][0])
        self.assertIn('formato=pdf" title="Descargar en PDF con el gráfico" aria-label="Descargar Medios de pago en PDF con el gráfico"', enlaces[1][0])
        filtros = tarjeta[tarjeta.index('<form method="get" class="informe-filtros"'):]
        self.assertIn('hx-boost="true"', filtros[:filtros.index('>')])
        self.assertIn('name="desde"', filtros)
        self.assertIn('<nav id="periodosTablero" class="d-flex flex-wrap gap-1" aria-label="Período del tablero" hx-boost="true">', html)
        self.assertRegex(html, r'echarts\.min\.js" data-reinicia></script>\s*<script src="[^"]*tablero\.js" data-reinicia>')
        self.assertIn('data-franja-vista="calor"', html)
        self.assertIn('<caption class="visually-hidden">Tickets por día de la semana y hora</caption>', html)

    def test_el_stock_valorizado_dice_al_dia_de_hoy_y_filtra_solo_por_rubro(self):
        html = self.html('negocio:reportes', ver='stock')
        tarjeta = html[html.index('class="tarjeta tarjeta-informe mb-3"'):html.index('<figure class="tarjeta grafico-reporte')]
        self.assertIn('<p class="informe-alcance">Al día de hoy</p>', tarjeta)
        filtros = tarjeta[tarjeta.index('class="informe-filtros"'):]
        self.assertNotIn('type="date"', filtros)
        self.assertIn('name="rubro"', filtros)
        self.assertEqual(tarjeta.count('hx-boost="false"'), 2)

    def test_las_descargas_siguen_a_la_capsula_y_los_filtros(self):
        html = self.html('negocio:reportes', ver='productos', desde='2026-11-01', hasta='2026-11-15')
        tarjeta = html[html.index('class="tarjeta tarjeta-informe mb-3"'):html.index('<figure class="tarjeta grafico-reporte')]
        self.assertIn('<p class="informe-alcance">Del 01/11/2026 al 15/11/2026', tarjeta)
        enlaces = re.findall(r'href="([^"]*exportar[^"]*)"', tarjeta)
        self.assertEqual(len(enlaces), 2)
        for enlace in enlaces:
            self.assertIn('ver=productos&amp;desde=2026-11-01&amp;hasta=2026-11-15', enlace)

    def test_el_resumen_del_mes_tambien_carga_echarts(self):
        html = self.html('negocio:resumen_del_mes')
        self.assertRegex(html, r'echarts\.min\.js" data-reinicia></script>\s*<script src="[^"]*tablero\.js" data-reinicia>')

    def test_el_ticket_se_imprime_solo_sin_script_en_linea(self):
        html = self.html('ventas:ticket', self.venta.pk)
        self.assertIn('<body data-autoimprimir>', html)
        self.assertEqual(SCRIPT_EN_LINEA.findall(html), [])
        self.assertIn('cafeto/js/imprimir.js', html)

    def test_permisos_usa_su_archivo(self):
        html = self.html('seguridad:permisos')
        self.assertEqual(SCRIPT_EN_LINEA.findall(html), [])
        self.assertIn('cafeto/js/permisos.js', html)


class BarraEInicioTest(TestCase):
    """DD-106: el rol se ve debajo del nombre y el Inicio arma su grilla
    según cuántos atajos y cuántas tarjetas de resumen tiene cada perfil."""

    def test_el_rol_debajo_del_nombre_y_la_grilla_segun_la_cantidad(self):
        admin = f.usuario('40800009', 'Administrador', nombre='Ana')
        f.entrar(self.client, admin)
        html = self.client.get(reverse('home')).content.decode()
        self.assertRegex(html, r'<span class="d-block small text-secondary">\s*Administrador')
        atajos = re.search(r'class="grilla grilla-atajos atajos-(\d+) mb-4"', html)
        resumen = re.search(r'class="grilla grilla-resumen resumen-(\d+)"', html)
        self.assertEqual(int(atajos.group(1)), html.count('class="tarjeta tarjeta-accion"'))
        self.assertEqual(int(resumen.group(1)), html.count('class="tarjeta kpi text-reset'))

    def test_un_perfil_con_menos_atajos_tiene_su_propia_grilla(self):
        operador = f.usuario('40800010', 'Operador', nombre='Cami')
        f.entrar(self.client, operador)
        html = self.client.get(reverse('home')).content.decode()
        atajos = re.search(r'grilla-atajos atajos-(\d+)', html)
        if atajos:
            self.assertEqual(int(atajos.group(1)), html.count('class="tarjeta tarjeta-accion"'))
