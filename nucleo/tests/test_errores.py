"""Blindaje contra errores (DD-100): páginas propias, excepciones de negocio
traducidas y un recorrido por TODAS las rutas sin un solo 500."""
import re

from django.test import Client, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver

from nucleo.tests import fabrica as f
from ventas import servicios as ventas
from decimal import Decimal as D


@override_settings(ROOT_URLCONF='nucleo.tests.urls_errores', DEBUG=False)
class PaginasDeErrorTest(TestCase):

    def setUp(self):
        self.client = Client(raise_request_exception=False)
        f.entrar(self.client, f.titular())

    def test_404_con_la_identidad_de_cafeto(self):
        respuesta = self.client.get('/no-existe-esta-ruta/')
        self.assertEqual(respuesta.status_code, 404)
        self.assertContains(respuesta, 'No encontramos esa página', status_code=404)
        self.assertContains(respuesta, 'Volver al inicio', status_code=404)
        self.assertContains(respuesta, 'Ir al mostrador', status_code=404)

    def test_500_sin_traceback_y_con_referencia(self):
        respuesta = self.client.get('/falla/rota/')
        self.assertEqual(respuesta.status_code, 500)
        html = respuesta.content.decode()
        self.assertIn('Algo falló de nuestro lado', html)
        self.assertRegex(html, r'Referencia <strong[^>]*>[0-9A-F]{8}</strong>')
        for filtrado in ('Traceback', 'RuntimeError', 'SELECT', 'usuarios'):
            self.assertNotIn(filtrado, html)

    def test_validacion_en_una_consulta_muestra_el_aviso(self):
        respuesta = self.client.get('/falla/validacion/')
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(respuesta, 'El stock no alcanza: quedan 2 unidades.', status_code=400)
        self.assertNotContains(respuesta, 'ValidationError', status_code=400)

    def test_validacion_en_un_formulario_vuelve_con_el_aviso(self):
        respuesta = self.client.post('/falla/validacion/', HTTP_REFERER='http://testserver/stock/productos/')
        self.assertRedirects(respuesta, 'http://testserver/stock/productos/', fetch_redirect_response=False)
        mensajes = [str(m) for m in respuesta.wsgi_request._messages]
        self.assertEqual(mensajes, ['El stock no alcanza: quedan 2 unidades.'])

    def test_a_otro_sitio_no_se_redirige(self):
        respuesta = self.client.post('/falla/validacion/', HTTP_REFERER='https://otro-sitio.example/')
        self.assertEqual(respuesta.status_code, 400)

    def test_javascript_y_api_reciben_json(self):
        respuesta = self.client.post('/falla/integridad/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(respuesta.status_code, 409)
        datos = respuesta.json()
        self.assertFalse(datos['ok'])
        self.assertEqual(datos['error'], 'Ya existe un registro con ese valor.')
        self.assertNotIn('uq_productos_codigo', respuesta.content.decode())

    def test_borrar_algo_con_movimientos_explica_la_baja(self):
        respuesta = self.client.get('/falla/protegido/')
        self.assertEqual(respuesta.status_code, 409)
        self.assertContains(respuesta, 'Dalo de baja', status_code=409)

    def test_un_numero_invalido_no_rompe(self):
        respuesta = self.client.get('/falla/decimal/')
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(respuesta, 'no es un número válido', status_code=400)


def _rutas(resolver=None, prefijo=''):
    """``(ruta, nombre)`` de cada patrón, con los parámetros reemplazados."""
    resolver = resolver or get_resolver()
    for patron in resolver.url_patterns:
        texto = prefijo + str(patron.pattern).lstrip('^').rstrip('$')
        if isinstance(patron, URLResolver):
            yield from _rutas(patron, texto)
        elif isinstance(patron, URLPattern):
            yield texto, patron.name or ''


SIN_RECORRER = re.compile(r'(salir|logout|cerrar-sesion|^media/|^admin/|^__)')


def _concreta(ruta, valor):
    ruta = re.sub(r'<int:\w+>', str(valor), ruta)
    ruta = re.sub(r'<slug:\w+>', 'ventas', ruta)
    ruta = re.sub(r'<uuid:\w+>', '00000000-0000-0000-0000-000000000000', ruta)
    ruta = re.sub(r'<(str:|path:)?\w+>', 'x', ruta)
    ruta = re.sub(r'\(\?P<\w+>[^)]*\)', 'x', ruta)
    return '/' + ruta


@override_settings(DEBUG=False)
class NingunaRutaDa500Test(TestCase):
    """Recorre todas las rutas del sistema (GET) con la titular logueada y
    sin sesión, con un id que existe y con uno que no: ninguna puede
    devolver 500."""

    @classmethod
    def setUpTestData(cls):
        cls.duenia = f.titular()
        cls.cajera = f.usuario('40900001', 'Operador')
        prov = f.proveedor()
        alfajor = f.producto('Alfajor', prov=prov)
        f.comprar(cls.duenia, prov, (alfajor, 10, '600'))
        turno = f.abrir_turno(cls.cajera)
        ventas.registrar(turno, cls.cajera, [ventas.Item(alfajor, D('1'))], [ventas.Pago(f.efectivo())])

    def recorrer(self, cliente):
        rotas = []
        rutas = sorted({r for r, _n in _rutas() if not SIN_RECORRER.search(r)})
        self.assertGreater(len(rutas), 100)
        for ruta in rutas:
            for valor in (1, 999999):
                url = _concreta(ruta, valor)
                respuesta = cliente.get(url)
                # 503 es «servicio no configurado» (api/servicio sin su token): es a propósito.
                if respuesta.status_code == 500:
                    rotas.append(f'{url} → {respuesta.status_code}')
                if '<' not in ruta:
                    break
        self.assertEqual(rotas, [], 'Rutas que devolvieron 500:\n' + '\n'.join(rotas))

    def test_con_la_titular(self):
        cliente = Client(raise_request_exception=False)
        f.entrar(cliente, self.duenia)
        self.recorrer(cliente)

    def test_con_la_cajera(self):
        cliente = Client(raise_request_exception=False)
        f.entrar(cliente, self.cajera)
        self.recorrer(cliente)

    def test_sin_sesion(self):
        self.recorrer(Client(raise_request_exception=False))


@override_settings(ROOT_URLCONF='nucleo.tests.urls_errores', DEBUG=False)
class MasPaginasDeErrorTest(TestCase):
    """DD-101: 405, 429, 503 (base caída) y el 404 aun con DEBUG."""

    def setUp(self):
        self.client = Client(raise_request_exception=False)
        f.entrar(self.client, f.titular())

    def test_sin_base_de_datos_una_pagina_503_que_se_reintenta_sola(self):
        respuesta = self.client.get('/falla/sin-base/')
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta['Retry-After'], '20')
        self.assertContains(respuesta, 'Estamos reconectando con la base de datos', status_code=503)
        self.assertNotContains(respuesta, 'server closed', status_code=503)
        json = self.client.get('/falla/sin-base/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(json.status_code, 503)
        self.assertFalse(json.json()['ok'])

    def test_405_con_la_identidad_de_cafeto(self):
        respuesta = self.client.get('/falla/solo-post/')
        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(respuesta['Allow'], 'POST')
        self.assertContains(respuesta, 'Esa acción no se hace así', status_code=405)

    def test_la_regla_de_la_base_dice_que_revisar(self):
        respuesta = self.client.get('/falla/turno-duplicado/')
        self.assertContains(respuesta, 'Esta caja ya tiene un turno abierto', status_code=409)
        self.assertNotContains(respuesta, 'no cumplen una regla del sistema', status_code=409)

    @override_settings(DEBUG=True)
    def test_con_debug_el_404_tambien_es_el_de_cafeto(self):
        respuesta = self.client.get('/ruta-inexistente/')
        self.assertEqual(respuesta.status_code, 404)
        self.assertContains(respuesta, 'No encontramos esa página', status_code=404)
        self.assertNotContains(respuesta, 'Using the URLconf', status_code=404)

    def test_las_paginas_de_nginx_no_dependen_de_django(self):
        from pathlib import Path

        from django.conf import settings
        for codigo in (502, 503):
            html = (Path(settings.BASE_DIR) / 'templates' / f'{codigo}.html').read_text()
            self.assertNotIn('{%', html)
            self.assertNotIn('/static/', html)
            self.assertIn('opacity: .05', html)
        for codigo in (400, 403, 404, 405, 429, 500):
            self.assertTrue((Path(settings.BASE_DIR) / 'templates' / f'{codigo}.html').exists())


class MensajesConCruzTest(TestCase):
    """La X de los avisos está adentro del aviso y los cierra (DD-101)."""

    def test_el_aviso_es_un_alert_de_bootstrap_y_la_x_queda_adentro(self):
        from pathlib import Path

        from django.conf import settings
        from django.template.loader import get_template
        fuente = get_template('parciales/_mensajes.html').template.source
        self.assertIn('alert alert-dismissible', fuente)
        self.assertIn('data-bs-dismiss="alert"', fuente)
        css = (Path(settings.BASE_DIR) / 'nucleo/static/cafeto/css/cafeto.css').read_text()
        self.assertIn('.aviso.alert-dismissible { position: relative; padding-right: 2.75rem; }', css)


class TraduccionDeReglasTest(TestCase):

    def test_cada_regla_de_las_tablas_criticas_tiene_su_mensaje(self):
        from django.db import connection

        from nucleo.mensajes_bd import POR_RESTRICCION
        with connection.cursor() as cursor:
            cursor.execute("""SELECT conname FROM pg_constraint
                               WHERE contype IN ('c','u') AND conrelid::regclass::text IN
                               ('turnos_caja','compras','facturas_recibidas','movimientos_cuenta',
                                'solicitudes_autorizacion')
                              UNION SELECT indexname FROM pg_indexes
                               WHERE tablename IN ('turnos_caja','compras','facturas_recibidas',
                                                   'movimientos_cuenta','solicitudes_autorizacion')
                                 AND indexdef ILIKE '%UNIQUE%' AND indexname NOT LIKE '%pkey'""")
            nombres = {fila[0] for fila in cursor.fetchall()}
        self.assertEqual(sorted(nombres - set(POR_RESTRICCION)), [])
