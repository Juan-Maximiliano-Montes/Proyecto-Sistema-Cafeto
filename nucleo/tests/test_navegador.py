"""Recorrido del sistema en un navegador real (Chromium + Playwright).

Las pruebas del servidor ejecutan las vistas sin navegador: no corren el
JavaScript ni abren las ventanas de Bootstrap. Por eso no vieron que
«Pagar» una factura dejaba la pantalla oscura, ni que el buscador del
combo abría la lista lejos del campo. Esta prueba hace lo que haría una
persona, con cada uno de los tres perfiles:

1. entra con su usuario y su clave, y elige la caja;
2. recorre todas las pantallas que le muestra el menú y las que se
   alcanzan desde ellas (fichas, detalles, exportaciones);
3. en cada pantalla toca cada botón que abre una ventana o una ficha;
4. manda cada formulario de carga vacío y con datos inválidos.

Falla si aparece:
  * un error de JavaScript en la consola;
  * una respuesta 500 del servidor;
  * una ventana que no se abre y deja el fondo oscuro trabando la pantalla.

Corre sólo donde está Playwright (no forma parte de la imagen de Docker):

    pip install playwright==1.56.0 && playwright install chromium
    python manage.py test nucleo.tests.test_navegador
"""
import os
import re
import unittest
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlparse

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import tag
from django.utils import timezone

try:
    from playwright.sync_api import sync_playwright
except ImportError:              # pragma: no cover - fuera del entorno de pruebas
    sync_playwright = None

D = Decimal

# Lo que no se navega: salir, el admin de Django, y lo que descarga un
# archivo (eso se pide aparte y sólo se mira el código de respuesta).
NO_NAVEGAR = re.compile(r'/(logout|admin)/')
ES_DESCARGA = re.compile(r'(exportar|/pdf/|formato=|/ticket/|/tarjeta/|/recaudacion/|modelo)')
MAXIMO_POR_PATRON = 2          # /ventas/12/ y /ventas/13/ son la misma pantalla
MAXIMO_DE_PANTALLAS = 160

VALORES_INVALIDOS = ('', '-1', 'abc', '9' * 40, '<script>x</script>')


def _ir(pagina, url):
    """Navega; si una navegación anterior todavía estaba en curso (un
    formulario recién enviado), espera y vuelve a intentar una vez."""
    for intento in range(3):
        try:
            respuesta = pagina.goto(url)
            pagina.wait_for_load_state('networkidle')
            return respuesta
        except Exception:
            pagina.wait_for_timeout(700)
    return None


def _patron(url):
    """La "misma pantalla": la ruta y los parámetros, con los números
    cambiados por N. Así /ventas/12/ y /ventas/13/ cuentan juntas, pero
    ?ver=a_pagar y ?ver=a_pagar&compra=5 (la factura a pagar) no."""
    partes = urlparse(url)
    consulta = '&'.join(sorted(partes.query.split('&'))) if partes.query else ''
    return re.sub(r'\d+', 'N', partes.path + '?' + consulta)


def _armar_datos():
    """Un local con un poco de todo: compras impagas, una orden enviada y
    otra con diferencias, ventas, un cambio, un ajuste pendiente, clientes."""
    from compras import egresos, servicios as compras
    from compras.models import TipoEgreso
    from fidelizacion.models import Cliente
    from nucleo.tests import fabrica as f
    from stock import servicios as stock
    from ventas import servicios as ventas
    from ventas.models import MedioPago

    usuarios = {
        'Administrador': f.usuario('30000001', 'Administrador', nombre='Ana'),
        'Encargado': f.usuario('30000002', 'Encargado', nombre='Lucrecia'),
        'Operador': f.usuario('30000003', 'Operador', nombre='Cami'),
    }
    from seguridad.models import Usuario
    Usuario.objects.filter(pk__in=[u.pk for u in usuarios.values()]).update(
        debe_cambiar_clave=False)          # usuarios de prueba, ya con su clave
    admin = usuarios['Administrador']
    prov = f.proveedor()
    gaseosa = f.producto('Gaseosa', prov=prov, precio='1500', costo='700')
    yogur = f.producto('Yogur', prov=prov, controla_vencimiento=True)
    f.comprar(admin, prov, (gaseosa, 40, '700'),
              (yogur, 10, '400', [(f.en_dias(20), 10)]))
    compra = f.comprar(admin, prov, (gaseosa, 10, '700'), numero=777)
    compra.fecha_vencimiento_pago = timezone.localdate() + timedelta(days=5)
    compra.save(update_fields=['fecha_vencimiento_pago'])
    orden = compras.emitir_orden(prov, admin, [
        {'producto': gaseosa, 'cantidad': D('24'), 'precio_unitario': D('650')}])
    compras.enviar_orden(orden)
    MedioPago.objects.create(nombre_medio_pago='Crédito 3 cuotas',
                             es_tarjeta_credito=True, cantidad_cuotas=3,
                             recargo_porcentaje=D('10'))
    turno = f.abrir_turno(usuarios['Operador'], f.caja_minisuper())
    venta, _ = ventas.registrar(turno, usuarios['Operador'],
                                [ventas.Item(gaseosa, D('2'))],
                                [ventas.Pago(f.efectivo())])
    Cliente.objects.create(apellido_cliente='López', nombre_cliente='Sofía',
                           telefono_cliente='3874123456')
    stock.registrar_merma(usuarios['Operador'], 'ROTURA',
                          [(gaseosa, D('1'), None)], 'Se cayó una')
    egresos.registrar_obligacion(
        TipoEgreso.objects.get(nombre_tipo_egreso='Gastos varios'), 'Luz',
        D('15000'), timezone.localdate() + timedelta(days=3),
        destinatario='EDESA')
    return usuarios, f.CLAVE


@tag('navegador')
class RecorridoEnNavegadorTest(StaticLiveServerTestCase):
    serialized_rollback = True      # conserva los datos iniciales de las migraciones

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            # SkipTest es lo que unittest entiende como "saltear": desde
            # setUpClass marca salteada la clase entera.
            raise unittest.SkipTest('Playwright no está instalado.')
        # Playwright corre su propio bucle de eventos en este hilo; el ORM
        # se usa sólo para armar los datos, antes de abrir el navegador.
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
        super().setUpClass()

    def setUp(self):
        self.usuarios, self.clave = _armar_datos()
        self.problemas = []
        self.cuenta = Counter()

    # ------------------------------------------------------------------
    def anotar(self, perfil, donde, que):
        texto = f'[{perfil}] {donde}: {que}'
        if texto not in self.problemas:
            self.problemas.append(texto)

    def test_cada_perfil_recorre_todo_sin_errores(self):
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            try:
                for perfil, usuario in self.usuarios.items():
                    contexto = navegador.new_context(viewport={'width': 1366, 'height': 800})
                    try:
                        self.recorrer(contexto.new_page(), perfil, usuario)
                    finally:
                        contexto.close()
            finally:
                navegador.close()
        print('\nRecorrido:', dict(self.cuenta))
        self.assertEqual(self.problemas, [], '\n' + '\n'.join(self.problemas))

    # ------------------------------------------------------------------
    #  Dos recorridos puntuales, de errores que un clic suelto no ve
    # ------------------------------------------------------------------
    def entrar(self, pagina, usuario):
        pagina.goto(self.live_server_url + '/login/')
        pagina.fill('[name="identificador"]', usuario.nombre_usuario)
        pagina.fill('[name="clave"]', self.clave)
        pagina.click('form button[type="submit"]')
        pagina.wait_for_load_state('networkidle')

    def con_pagina(self, hacer):
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            try:
                pagina = navegador.new_page(viewport={'width': 1366, 'height': 800})
                errores = []
                pagina.on('pageerror', lambda e: errores.append(str(e)))
                hacer(pagina)
                self.assertEqual(errores, [])
            finally:
                navegador.close()

    def test_el_buscador_del_combo_abre_junto_al_campo(self):
        """Error 5 de la prueba integral: la lista aparecía ~680 px arriba."""
        def hacer(pagina):
            self.entrar(pagina, self.usuarios['Administrador'])
            pagina.goto(self.live_server_url + '/stock/productos/')
            pagina.click('[data-bs-target="#modalCrear"]')
            modal = pagina.locator('#modalCrear')
            modal.locator('[name="tipo_producto"]').select_option('COMBO')
            caja = modal.locator('.js-renglones .select2-selection').first
            caja.scroll_into_view_if_needed()
            caja.click()
            pagina.keyboard.type('gas')
            pagina.wait_for_selector('.select2-results__option', timeout=5000)
            campo = caja.bounding_box()
            lista = pagina.locator('.select2-dropdown').bounding_box()
            pegada = min(abs(lista['y'] - (campo['y'] + campo['height'])),
                         abs((lista['y'] + lista['height']) - campo['y']))
            self.assertLess(pegada, 40, f'campo en {campo}, lista en {lista}')
        self.con_pagina(hacer)

    def test_avisa_un_nombre_parecido_al_salir_del_campo(self):
        """DD-70: el aviso aparece antes de guardar, al salir del campo."""
        def hacer(pagina):
            self.entrar(pagina, self.usuarios['Administrador'])
            pagina.goto(self.live_server_url + '/stock/productos/')
            pagina.click('[data-bs-target="#modalCrear"]')
            modal = pagina.locator('#modalCrear')
            modal.locator('[name="nombre_producto"]').fill('Gaseosas')
            modal.locator('[name="nombre_producto"]').press('Tab')
            aviso = modal.locator('.aviso-parecidos')
            aviso.wait_for(timeout=5000)
            self.assertIn('Gaseosa', aviso.inner_text())

            # Lo que pasó probando: borrar el nombre y escribir el MISMO de
            # nuevo. El navegador no dispara "change" (el texto final es
            # igual) y el aviso no volvía.
            campo = modal.locator('[name="nombre_producto"]')
            campo.click()
            campo.press('Control+a')
            campo.press('Backspace')
            aviso.wait_for(state='detached', timeout=5000)
            campo.press_sequentially('Gaseosas', delay=30)
            campo.press('Tab')
            aviso.wait_for(timeout=5000)
            self.assertEqual(modal.locator('.aviso-parecidos').count(), 1)

            # Y sin salir del campo: aparece solo, tras una pausa al escribir.
            campo.click()
            campo.press('Control+a')
            campo.press('Backspace')
            campo.press_sequentially('Gaseosas', delay=30)
            aviso.wait_for(timeout=5000)
        self.con_pagina(hacer)

    def test_el_mas_da_de_alta_la_marca_sin_salir_del_producto(self):
        """DD-70: el "+" despliega el alta de marca; al guardar queda elegida
        y lo ya escrito en el producto sigue ahí."""
        def hacer(pagina):
            self.entrar(pagina, self.usuarios['Administrador'])
            pagina.goto(self.live_server_url + '/stock/productos/')
            pagina.click('[data-bs-target="#modalCrear"]')
            modal = pagina.locator('#modalCrear')
            modal.locator('[name="nombre_producto"]').fill('Alfajor triple')
            mas = modal.locator('.js-alta-rapida[data-destino$="marca"]')
            mas.click()
            panel = modal.locator('.alta-rapida-panel:not([hidden])')
            panel.locator('[name="rapido-nombre_marca"]').fill('Cachafaz')
            panel.locator('.js-alta-rapida-guardar').click()
            pagina.wait_for_function(
                """() => { const s = document.querySelector('#modalCrear select[name="id_marca"]');
                           return s && s.selectedOptions[0] && s.selectedOptions[0].text.includes('Cachafaz'); }""",
                timeout=5000)
            self.assertTrue(modal.is_visible())
            self.assertEqual(modal.locator('[name="nombre_producto"]').input_value(), 'Alfajor triple')
            self.assertEqual(modal.locator('.alta-rapida-panel:not([hidden])').count(), 0)
        self.con_pagina(hacer)

    def test_un_pago_sin_a_quien_avisa_sin_cerrar_la_ventana(self):
        """El servidor rechaza el egreso sin acreedor, pero la ventana se
        cerraba y se perdía lo cargado: ahora avisa al lado del campo."""
        def hacer(pagina):
            self.entrar(pagina, self.usuarios['Administrador'])
            pagina.goto(self.live_server_url + '/compras/egresos/')
            pagina.click('[data-bs-target="#modalEgreso"]')
            modal = pagina.locator('#modalEgreso')
            modal.locator('[name="monto"]').fill('1500')
            modal.locator('[name="concepto"]').fill('Luz de octubre')
            modal.locator('button[type="submit"]').click()
            error = modal.locator('.error-campo.js-acreedor')
            error.wait_for(timeout=5000)
            self.assertIn('a quién se le paga', error.inner_text())
            self.assertTrue(modal.is_visible())
            self.assertEqual(modal.locator('[name="concepto"]').input_value(), 'Luz de octubre')
            # Con el destinatario, sigue el resumen de confirmación de siempre.
            modal.locator('[name="destinatario"]').fill('EDESA')
            modal.locator('button[type="submit"]').click()
            pagina.wait_for_selector('.modal.show >> text=¿Guardamos estos datos?', timeout=5000)
            self.assertEqual(modal.locator('.error-campo.js-acreedor').count(), 0)
        self.con_pagina(hacer)

    def test_el_mostrador_muestra_el_stock_despues_de_vender(self):
        """Error 6 de la prueba integral: la grilla seguía con el stock viejo."""
        def stock(pagina):
            texto = pagina.locator('.producto-boton', has_text='Gaseosa').locator(
                '.stock', has_text='Stock').inner_text()
            return int(re.search(r'\d+', texto).group())

        def hacer(pagina):
            self.entrar(pagina, self.usuarios['Operador'])
            pagina.goto(self.live_server_url + '/ventas/caja/')
            pagina.locator('form[action$="/caja/elegir/"]', has_text='Minisuper').locator('button[type="submit"]').first.click()
            pagina.wait_for_load_state('networkidle')
            pagina.goto(self.live_server_url + '/ventas/mostrador/')
            pagina.wait_for_selector('.producto-boton')
            antes = stock(pagina)
            pagina.locator('.producto-boton', has_text='Gaseosa').click()
            pagina.locator('.js-medio[data-efectivo="1"]').click()
            pagina.wait_for_selector('#cobrar:not([disabled])', timeout=5000)
            pagina.click('#cobrar')
            pagina.wait_for_selector('#modalCobrado.show', timeout=5000)
            pagina.click('#nuevaVenta')
            pagina.wait_for_timeout(1200)
            self.assertEqual(stock(pagina), antes - 1)
        self.con_pagina(hacer)

    # ------------------------------------------------------------------
    def recorrer(self, pagina, perfil, usuario):
        base = self.live_server_url
        actual = {'url': ''}
        pagina.on('pageerror', lambda e: self.anotar(perfil, actual['url'], f'JS: {e}'))
        pagina.on('console', lambda m: m.type == 'error' and not m.text.startswith(
            'Failed to load resource') and self.anotar(perfil, actual['url'], f'consola: {m.text}'))
        pagina.on('response', lambda r: r.status >= 500 and self.anotar(
            perfil, actual['url'], f'{r.status} en {r.request.method} {r.url}'))
        pagina.on('dialog', lambda d: d.dismiss())

        pagina.goto(base + '/login/')
        pagina.fill('[name="identificador"]', usuario.nombre_usuario)
        pagina.fill('[name="clave"]', self.clave)
        pagina.click('form button[type="submit"]')
        pagina.wait_for_load_state('networkidle')
        if '/cambiar-clave/' in pagina.url:
            self.anotar(perfil, 'login', 'pide cambiar la clave: el usuario de prueba no debería')
            return
        # La caja del dispositivo: la del Minisuper, que tiene el turno abierto.
        pagina.goto(base + '/ventas/caja/')
        boton = pagina.locator('form[action$="/caja/elegir/"]', has_text='Minisuper').locator('button[type="submit"]').first
        if boton.count():
            boton.click()
            pagina.wait_for_load_state('networkidle')

        pendientes = [base + '/']
        pendientes += [base + a.get_attribute('href') for a in
                       pagina.locator('nav a[href^="/"]').all()]
        vistas, por_patron, descargas = set(), Counter(), set()
        while pendientes and len(vistas) < MAXIMO_DE_PANTALLAS:
            url = pendientes.pop(0)
            ruta = urlparse(url).path + ('?' + urlparse(url).query if urlparse(url).query else '')
            if ruta in vistas or NO_NAVEGAR.search(ruta):
                continue
            if ES_DESCARGA.search(ruta):
                if ruta not in descargas:
                    descargas.add(ruta)
                    respuesta = pagina.request.get(url)
                    self.cuenta['descargas'] += 1
                    if respuesta.status >= 500:
                        self.anotar(perfil, ruta, f'descarga con {respuesta.status}')
                continue
            if por_patron[_patron(url)] >= MAXIMO_POR_PATRON:
                continue
            vistas.add(ruta)
            self.cuenta['pantallas'] += 1
            por_patron[_patron(url)] += 1
            actual['url'] = ruta
            respuesta = _ir(pagina, url)
            if respuesta is None or respuesta.status >= 400 and respuesta.status != 404:
                if respuesta is not None and respuesta.status >= 500:
                    self.anotar(perfil, ruta, f'la pantalla devuelve {respuesta.status}')
                continue
            # Todos los enlaces, también los relativos ("?ver=a_pagar&compra=5"):
            # el navegador ya los resuelve a una dirección completa.
            for href in pagina.eval_on_selector_all('a[href]', 'es => es.map(e => e.href)'):
                href = href.split('#')[0]
                if href.startswith(base) and '/static/' not in href:
                    pendientes.append(href)
            self.tocar_botones(pagina, perfil, ruta)
            self.mandar_formularios(pagina, perfil, ruta, url)

    # ------------------------------------------------------------------
    def tocar_botones(self, pagina, perfil, ruta):
        """Cada botón que abre una ventana o una ficha, uno por uno."""
        selector = '[data-bs-toggle="modal"]:visible, [data-remoto]:visible'
        for i in range(pagina.locator(selector).count()):
            boton = pagina.locator(selector).nth(i)
            try:
                texto = (boton.inner_text(timeout=1000) or boton.get_attribute('aria-label') or '').strip()[:40]
                boton.click(timeout=3000)
            except Exception:
                continue          # tapado o fuera de pantalla: no lo toca una persona
            pagina.wait_for_timeout(450)
            self.cuenta['botones'] += 1
            abierta = pagina.locator('.modal.show').count()
            fondo = pagina.locator('.modal-backdrop').count()
            if fondo and not abierta:
                self.anotar(perfil, ruta, f'«{texto}» oscurece la pantalla y no abre nada')
                pagina.reload()
                pagina.wait_for_load_state('networkidle')
                continue
            if abierta:
                pagina.keyboard.press('Escape')
                pagina.wait_for_timeout(350)
                if pagina.locator('.modal.show').count():
                    cerrar = pagina.locator('.modal.show [data-bs-dismiss="modal"]').first
                    if cerrar.count():
                        cerrar.click()
                        pagina.wait_for_timeout(350)

    def mandar_formularios(self, pagina, perfil, ruta, url):
        """Los formularios de carga (los que tienen algo para escribir),
        vacíos y con datos inválidos. Lo que se busca es que el servidor
        conteste con un aviso y nunca con un error 500."""
        cuantos = pagina.evaluate("""() => Array.from(document.querySelectorAll('form[method="post"], form[method="POST"]'))
            .filter(f => f.querySelector('input[type=text], input[type=number], input:not([type]), textarea, input[type=date]'))
            .length""")
        for indice in range(cuantos):
            for valor in VALORES_INVALIDOS:
                if _ir(pagina, url) is None:
                    continue
                try:
                    pagina.evaluate("""([indice, valor]) => {
                        const formularios = Array.from(document.querySelectorAll('form[method="post"], form[method="POST"]'))
                            .filter(f => f.querySelector('input[type=text], input[type=number], input:not([type]), textarea, input[type=date]'));
                        const f = formularios[indice];
                        if (!f) { return; }
                        f.querySelectorAll('input[type=text], input[type=number], input:not([type]), textarea, input[type=date]')
                            .forEach(c => { if (c.name && c.name !== 'token_operacion') { c.value = c.type === 'date' ? '' : valor; } });
                        f.noValidate = true;
                        HTMLFormElement.prototype.submit.call(f);
                    }""", [indice, valor])
                    pagina.wait_for_load_state('networkidle')
                    self.cuenta['formularios enviados'] += 1
                except Exception as error:   # una navegación cortada no es un fallo del sistema
                    if '500' in str(error):
                        self.anotar(perfil, ruta, f'formulario {indice} con «{valor[:12]}»: {error}')
