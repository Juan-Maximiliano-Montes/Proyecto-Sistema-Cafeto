"""Vender, Conteo, Usuarios y Perfiles con la CSP estricta, en un navegador real (DD-109).

Cada página escucha los errores de JavaScript, los errores de la consola
y el evento securitypolicyviolation: con script-src 'self' un script en
línea que quedara olvidado se bloquea y aparece acá.

    python manage.py test nucleo.tests.test_csp_navegador --tag=navegador
"""
import os
import unittest
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import tag
from django.urls import reverse

from nucleo.tests.test_navegador import _armar_datos

try:
    from playwright.sync_api import sync_playwright
except ImportError:              # pragma: no cover - fuera del entorno de pruebas
    sync_playwright = None

ESCRITORIO = {'width': 1280, 'height': 900}
TABLET = {'width': 768, 'height': 1024}
CELULAR = {'width': 390, 'height': 844}
DESBORDE = 'document.documentElement.scrollWidth - window.innerWidth'
ESCUCHAR_CSP = """
window.__violacionesCSP = [];
document.addEventListener('securitypolicyviolation', function (e) {
    window.__violacionesCSP.push(e.violatedDirective + ' ' + (e.blockedURI || 'inline') + ' ' + (e.sourceFile || ''));
});
"""
CODIGO_GASEOSA = '7790001112223'


@tag('navegador')
class CSPEnNavegadorTest(StaticLiveServerTestCase):
    serialized_rollback = True

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest('Playwright no está instalado.')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
        super().setUpClass()

    def setUp(self):
        from stock.models import Producto
        self.usuarios, self.clave = _armar_datos()
        Producto.objects.filter(nombre_producto='Gaseosa').update(codigo_barras=CODIGO_GASEOSA)

    # ------------------------------------------------------------------
    def con_pagina(self, hacer, usuario, tamanio=ESCRITORIO):
        problemas = []
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            try:
                contexto = navegador.new_context(viewport=tamanio)
                contexto.add_init_script(ESCUCHAR_CSP)
                pagina = contexto.new_page()
                pagina.on('pageerror', lambda e: problemas.append(f'JS: {e}'))
                pagina.on('console', lambda m: m.type == 'error' and problemas.append(f'consola: {m.text}'))
                pagina.on('response', lambda r: r.status >= 500 and problemas.append(f'{r.status} en {r.url}'))
                pagina.goto(self.live_server_url + reverse('seguridad:login'))
                pagina.fill('[name="identificador"]', usuario.nombre_usuario)
                pagina.fill('[name="clave"]', self.clave)
                pagina.click('form button[type="submit"]')
                pagina.wait_for_load_state('networkidle')
                hacer(pagina, problemas)
                problemas.extend(f'CSP: {v}' for v in self.violaciones(pagina))
            finally:
                navegador.close()
        self.assertEqual(problemas, [])

    @staticmethod
    def violaciones(pagina):
        return pagina.evaluate('() => window.__violacionesCSP || []')

    def ir(self, pagina, nombre, problemas):
        """Abre la pantalla y junta las violaciones de la anterior antes de irse."""
        problemas.extend(f'CSP: {v}' for v in self.violaciones(pagina))
        pagina.goto(self.live_server_url + reverse(nombre))
        pagina.wait_for_load_state('networkidle')

    def sin_desborde(self, pagina, donde):
        self.assertLessEqual(pagina.evaluate(DESBORDE), 1, donde)

    # ------------------------------------------------------------------
    def test_la_csp_esta_activa_y_se_detecta(self):
        """Control de la propia prueba: un script en línea inyectado se bloquea."""
        def hacer(pagina, problemas):
            cabecera = pagina.goto(self.live_server_url + reverse('home')).headers['content-security-policy']
            self.assertIn("script-src 'self'", cabecera)
            self.assertNotIn("'unsafe-inline'", cabecera.split('script-src')[1].split(';')[0])
            pagina.evaluate("""() => { const s = document.createElement('script');
                                       s.textContent = 'window.__ejecutado = true'; document.body.appendChild(s); }""")
            pagina.wait_for_timeout(200)
            self.assertIsNone(pagina.evaluate('() => window.__ejecutado'))
            self.assertTrue(any(v.startswith('script-src') for v in self.violaciones(pagina)))
            pagina.evaluate('() => { window.__violacionesCSP = []; }')
            problemas[:] = [p for p in problemas if 'Content Security Policy' not in p]
        self.con_pagina(hacer, self.usuarios['Administrador'])

    def test_vender_flujo_completo_sin_errores_ni_violaciones(self):
        from ventas.models import Venta

        def barra(pagina):
            return pagina.locator('#barraCantidad').text_content().strip()

        def hacer(pagina, problemas):
            pagina.goto(self.live_server_url + reverse('ventas:caja'))
            pagina.locator('form[action$="/caja/elegir/"]', has_text='Minisuper').locator(
                'button[type="submit"]').first.click()
            pagina.wait_for_load_state('networkidle')
            self.ir(pagina, 'ventas:mostrador', problemas)
            pagina.wait_for_selector('.producto-boton')
            self.assertEqual(barra(pagina), '0 productos')
            antes = Venta.objects.count()

            pagina.locator('.producto-boton', has_text='Gaseosa').click()
            pagina.wait_for_function("() => document.getElementById('barraCantidad').textContent.trim() === '1 producto'")
            renglon = pagina.locator('#renglones .renglon').first
            renglon.locator('button', has_text='+').click()
            pagina.wait_for_function("() => document.querySelector('#renglones input[aria-label=\"Cantidad\"]').value === '2'")
            self.assertEqual(barra(pagina), '1 producto')

            pagina.fill('#buscar', 'Yogur')
            pagina.locator('.producto-boton', has_text='Yogur').first.click()
            pagina.wait_for_function("() => document.getElementById('barraCantidad').textContent.trim() === '2 productos'")
            self.assertNotIn('(s)', pagina.locator('#barraTicket').text_content())
            self.sin_desborde(pagina, 'Vender a 768 px')

            pagina.keyboard.press('F4')                       # atajo: efectivo
            pagina.wait_for_selector('#cobrar:not([disabled])', timeout=5000)
            pagina.keyboard.press('F9')                       # atajo: cobrar
            pagina.wait_for_selector('.modal.show .js-aceptar', timeout=5000)
            self.assertIn('Total a cobrar', pagina.locator('.modal.show').inner_text())
            pagina.locator('.modal.show .js-aceptar').click()
            # Mientras viaja el cobro, otro F9 y otro clic en Cobrar: no se cobra dos veces.
            pagina.keyboard.press('F9')
            pagina.evaluate("() => document.getElementById('cobrar').click()")
            pagina.wait_for_selector('#modalCobrado.show', timeout=8000)
            self.assertTrue(pagina.locator('#cobradoTotal').text_content().strip().startswith('$'))
            self.assertEqual(Venta.objects.count(), antes + 1)
            venta = Venta.objects.order_by('-pk').first()
            cantidades = {d.id_producto.nombre_producto: d.cantidad for d in venta.detalles.select_related('id_producto')}
            self.assertEqual(cantidades.get('Gaseosa'), Decimal('2'))
            self.assertIn('Yogur', cantidades)

            pagina.wait_for_function("() => document.activeElement && document.activeElement.id === 'nuevaVenta'")
            pagina.keyboard.press('Enter')                    # atajo: nueva venta
            pagina.wait_for_selector('#modalCobrado.show', state='detached', timeout=5000)
            pagina.wait_for_function("() => document.getElementById('barraCantidad').textContent.trim() === '0 productos'")
            self.assertEqual(Venta.objects.count(), antes + 1)
        self.con_pagina(hacer, self.usuarios['Operador'], TABLET)

    def test_conteo_busca_anota_y_avisa_sin_errores(self):
        def hacer(pagina, problemas):
            self.ir(pagina, 'stock:conteo', problemas)
            pagina.locator('button[type="submit"]', has_text='Empezar el conteo').click()
            pagina.wait_for_selector('#escaner')
            pagina.fill('#escaner', 'abc')
            pagina.keyboard.press('Enter')
            pagina.wait_for_selector('#rafagaAviso:not(.d-none)')
            self.assertIn('no parece un código', pagina.locator('#rafagaAviso').inner_text())
            pagina.fill('#escaner', CODIGO_GASEOSA)
            pagina.keyboard.press('Enter')
            pagina.wait_for_selector('#rafagaEncontrado:not(.d-none)')
            self.assertIn('Gaseosa', pagina.locator('#rafagaNombre').inner_text())
            pagina.fill('#rafagaContada', '7')
            pagina.keyboard.press('Enter')
            pagina.wait_for_load_state('networkidle')
            self.assertIn('Gaseosa', pagina.locator('main').inner_text())
            pagina.locator('label[for="sumarRepetidos"]').click()
            self.assertEqual(pagina.locator('#rafagaSumar').input_value(), '')
        self.con_pagina(hacer, self.usuarios['Administrador'])

    def test_usuarios_detalle_y_dni_repetido_sin_errores(self):
        def hacer(pagina, problemas):
            self.ir(pagina, 'seguridad:lista', problemas)
            pagina.locator('.btn-detalle').first.click()
            pagina.wait_for_selector('#modalDetalle.show')
            self.assertRegex(pagina.locator('#det_dni').inner_text(), r'\d{7,8}')
            pagina.locator('#modalDetalle .btn-close').click()
            pagina.wait_for_selector('#modalDetalle.show', state='detached')
            pagina.locator('[data-bs-target="#modalCrear"]').first.click()
            pagina.wait_for_selector('#modalCrear.show')
            pagina.fill('#id_dni_usuario', self.usuarios['Operador'].dni_usuario)
            pagina.wait_for_selector('#avisoDni:not(.d-none)', timeout=5000)
            self.assertTrue(pagina.locator('#btnGuardarCrear').is_disabled())
        self.con_pagina(hacer, self.usuarios['Administrador'])

    def test_perfiles_detalle_y_edicion_sin_errores(self):
        def hacer(pagina, problemas):
            self.ir(pagina, 'seguridad:perfiles', problemas)
            pagina.locator('.btn-detalle').first.click()
            pagina.wait_for_selector('#modalDetalle.show')
            self.assertNotEqual(pagina.locator('#det_nombre').inner_text().strip(), '')
            pagina.locator('#modalDetalle .btn-close').click()
            pagina.wait_for_selector('#modalDetalle.show', state='detached')
            pagina.locator('.btn-editar').first.click()
            pagina.wait_for_selector('#modalEditar.show')
            self.assertNotEqual(pagina.locator('#edit_nombre').input_value(), '')
        self.con_pagina(hacer, self.usuarios['Administrador'])

    def test_conteo_usuarios_y_perfiles_sin_desborde_en_tres_anchos(self):
        for tamanio in (ESCRITORIO, TABLET, CELULAR):
            def hacer(pagina, problemas, tamanio=tamanio):
                for nombre in ('stock:conteo', 'seguridad:lista', 'seguridad:perfiles'):
                    self.ir(pagina, nombre, problemas)
                    self.sin_desborde(pagina, f'{nombre} a {tamanio["width"]} px')
            with self.subTest(ancho=tamanio['width']):
                self.con_pagina(hacer, self.usuarios['Administrador'], tamanio)
