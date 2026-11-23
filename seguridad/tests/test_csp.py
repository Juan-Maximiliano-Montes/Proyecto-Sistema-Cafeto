"""CSP estricta en script-src y plantillas sin JavaScript en línea (DD-109)."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase


def directivas(cabecera):
    resultado = {}
    for parte in cabecera.split(';'):
        palabras = parte.split()
        if palabras:
            resultado[palabras[0]] = palabras[1:]
    return resultado


class CabeceraCSPTest(TestCase):

    def _csp(self, url):
        respuesta = self.client.get(url)
        self.assertIn('Content-Security-Policy', respuesta.headers, url)
        return directivas(respuesta['Content-Security-Policy'])

    def test_script_src_sin_unsafe_inline_ni_eval(self):
        for url in ('/login/', '/ventas/mostrador/', '/no-existe/'):
            csp = self._csp(url)
            self.assertEqual(csp['script-src'], ["'self'"], url)
            self.assertNotIn("'unsafe-inline'", csp['script-src'])
            self.assertNotIn("'unsafe-eval'", csp['script-src'])
            self.assertEqual(csp['object-src'], ["'none'"])
            self.assertEqual(csp['frame-ancestors'], ["'none'"])
            self.assertEqual(csp['base-uri'], ["'self'"])

    def test_style_src_sigue_aceptando_estilos_en_linea(self):
        self.assertIn("'unsafe-inline'", self._csp('/login/')['style-src'])

    def test_la_api_tambien_la_lleva(self):
        respuesta = self.client.get('/api/movil/', HTTP_ACCEPT='application/json')
        self.assertIn("script-src 'self'", respuesta['Content-Security-Policy'])


class PlantillasSinJavaScriptEnLineaTest(SimpleTestCase):

    SCRIPT = re.compile(r'<script\b([^>]*)>(.*?)</script\s*>', re.S | re.I)
    NO_EJECUTABLES = {'application/json', 'application/ld+json', 'text/template'}
    MANEJADOR = re.compile(r'<[a-zA-Z][^<>]*?\son[a-z]+\s*=', re.S)
    COMENTARIOS = re.compile(r'\{% comment %\}.*?\{% endcomment %\}|\{#.*?#\}|<!--.*?-->', re.S)

    def _plantillas(self):
        raiz = Path(settings.BASE_DIR)
        vistas = set(raiz.glob('templates/**/*.html')) | set(raiz.glob('*/templates/**/*.html'))
        return sorted(p for p in vistas if 'node_modules' not in p.parts)

    def test_hay_plantillas_para_revisar(self):
        self.assertGreater(len(self._plantillas()), 50)

    def test_ningun_script_ejecutable_en_linea(self):
        encontrados = []
        for plantilla in self._plantillas():
            texto = self.COMENTARIOS.sub('', plantilla.read_text(encoding='utf-8'))
            for script in self.SCRIPT.finditer(texto):
                atributos, cuerpo = script.group(1), script.group(2)
                tipo = re.search(r'\btype\s*=\s*["\']([^"\']+)', atributos)
                if tipo and tipo.group(1).strip().lower() in self.NO_EJECUTABLES:
                    continue
                if re.search(r'\bsrc\s*=', atributos) and not cuerpo.strip():
                    continue
                encontrados.append(str(plantilla))
        self.assertEqual(encontrados, [])

    def test_ningun_manejador_on_ni_javascript_en_enlaces(self):
        encontrados = []
        for plantilla in self._plantillas():
            texto = self.COMENTARIOS.sub('', plantilla.read_text(encoding='utf-8'))
            if self.MANEJADOR.search(texto) or re.search(r'(href|action|src)\s*=\s*["\']\s*javascript:', texto, re.I):
                encontrados.append(str(plantilla))
        self.assertEqual(encontrados, [])


class SugerenciasDeMejoraSinJavaScriptEnLineaTest(TestCase):
    """La pantalla nueva de «Sugerencias de mejora», ya servida (DD-109).

    Las dos acciones (Aplicar y Descartar) piden confirmación con un modal,
    y ese modal lo arma ``cafeto.js`` leyendo ``data-confirmar``: no hay
    ``onclick`` ni ``<script>`` en línea que la CSP estricta bloquee y deje
    el botón sin confirmación.
    """

    PLANTILLA = Path(__file__).resolve().parents[2] / 'stock' / 'templates' / 'stock' / 'sugerencias.html'

    def setUp(self):
        from nucleo.tests import fabrica as f
        from stock import sugerencias

        self.admin = f.usuario('30555100', 'Administrador')
        proveedor = f.proveedor()
        producto = f.producto('Leche entera', prov=proveedor)
        sugerencias.proponer(producto, 'RUBRO', 'Parece de lácteos',
                             {'rubro': 'Lácteos'})
        f.entrar(self.client, self.admin)

    def test_la_cabecera_llega_estricta_y_la_pagina_no_trae_javascript_en_linea(self):
        respuesta = self.client.get('/stock/sugerencias/')
        self.assertEqual(respuesta.status_code, 200)
        csp = directivas(respuesta['Content-Security-Policy'])
        self.assertEqual(csp['script-src'], ["'self'"])
        html = respuesta.content.decode()
        cuerpo = html.split('id="contenido"', 1)[-1] if 'id="contenido"' in html else html
        self.assertNotIn('onclick', cuerpo)
        self.assertNotIn('javascript:', cuerpo)

    def test_la_confirmacion_va_por_dato_y_no_por_script(self):
        texto = self.PLANTILLA.read_text(encoding='utf-8')
        self.assertIn('data-confirmar=', texto)
        self.assertNotIn('<script', texto)
        self.assertIsNone(PlantillasSinJavaScriptEnLineaTest.MANEJADOR.search(texto))
