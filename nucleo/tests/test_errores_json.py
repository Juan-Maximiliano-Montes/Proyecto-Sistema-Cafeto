"""La API y el JavaScript de las pantallas reciben JSON en 401, 403, 404,
405 y 429 (DD-103): nunca una página HTML que no pueden leer, ni la página
amarilla de Django con las rutas del sistema."""
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings

from api_movil.autenticacion import emitir_token
from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria

AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


class Comun:

    def es_json_de_error(self, respuesta, estado):
        self.assertEqual(respuesta.status_code, estado)
        self.assertIn('application/json', respuesta['Content-Type'])
        datos = respuesta.json()
        self.assertIs(datos['ok'], False)
        self.assertTrue(datos['detail'])
        self.assertEqual(datos['detail'], datos['error'])
        self.assertNotIn('URLconf', respuesta.content.decode())
        return datos


@override_settings(DEBUG=True)
class RutasQueNoExistenTest(Comun, TestCase):

    def test_api_inexistente_con_debug_es_json(self):
        cliente = Client(raise_request_exception=False)
        for ruta in ('/api/movil/inexistente/', '/api/servicio/inexistente/', '/api/movil/marcas/9999999/x/'):
            datos = self.es_json_de_error(cliente.get(ruta), 404)
            self.assertEqual(datos['detail'], 'Esa dirección no existe.')

    def test_una_pantalla_inexistente_sigue_siendo_html(self):
        cliente = Client(raise_request_exception=False)
        f.entrar(cliente, f.titular())
        respuesta = cliente.get('/no-existe/')
        self.assertEqual(respuesta.status_code, 404)
        self.assertContains(respuesta, 'No encontramos esa página', status_code=404)

    def test_metodo_no_permitido_desde_javascript(self):
        cliente = Client(raise_request_exception=False)
        f.entrar(cliente, f.titular())
        respuesta = cliente.get('/ventas/mostrador/cobrar/', **AJAX)
        datos = self.es_json_de_error(respuesta, 405)
        self.assertIn('POST', respuesta['Allow'])
        self.assertTrue(datos['detail'])

    def test_la_api_movil_conserva_su_405_propio(self):
        cliente = Client(raise_request_exception=False)
        respuesta = cliente.delete('/api/movil/marcas/',
                                   HTTP_AUTHORIZATION=f'Token {emitir_token(f.titular())}')
        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(respuesta.json()['detail'], 'Ese pedido no se puede hacer acá.')


class PermisosDesdeJavascriptTest(Comun, TestCase):

    def setUp(self):
        self.cajera = f.usuario('40800001', 'Operador')
        self.client = Client(raise_request_exception=False)
        f.entrar(self.client, self.cajera)

    def test_sin_permiso_es_403_json_sin_redirigir_ni_mensaje(self):
        antes = LogAuditoria.objects.count()
        respuesta = self.client.get('/stock/api/lotes/', {'producto': 1}, **AJAX)
        if respuesta.status_code != 403:
            # La cajera puede ver lotes: se prueba una acción de la dueña.
            respuesta = self.client.post('/compras/escanear-factura/producto/', '{}',
                                         content_type='application/json', **AJAX)
        datos = self.es_json_de_error(respuesta, 403)
        self.assertIn('permiso', datos['detail'])
        self.assertEqual(list(get_messages(respuesta.wsgi_request)), [])
        self.assertGreater(LogAuditoria.objects.count(), antes)

    def test_requiere_alguno_tambien(self):
        respuesta = self.client.get('/ventas/autorizaciones/', **AJAX)
        if respuesta.status_code == 200:
            self.skipTest('El perfil Operador puede ver la bandeja en esta configuración.')
        self.es_json_de_error(respuesta, 403)

    def test_la_pantalla_comun_sigue_redirigiendo_con_aviso(self):
        respuesta = self.client.post('/compras/escanear-factura/producto/', {})
        self.assertEqual(respuesta.status_code, 302)

    def test_sin_sesion_desde_javascript_es_401_json(self):
        cliente = Client(raise_request_exception=False)
        datos = self.es_json_de_error(cliente.post('/ventas/mostrador/cotizar/', '{}',
                                                   content_type='application/json', **AJAX), 401)
        self.assertEqual(datos['detail'], 'Tenés que iniciar sesión.')
