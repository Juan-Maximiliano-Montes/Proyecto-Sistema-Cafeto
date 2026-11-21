"""CORS para el frontend en React: sólo /api/movil/, sin cookies, con
Idempotency-Key (DD-104)."""
from django.test import TestCase, override_settings

from api_movil.autenticacion import emitir_token
from nucleo.tests import fabrica as f

VITE = 'http://localhost:5173'
AJENO = 'https://otro-sitio.example'


@override_settings(CORS_ALLOWED_ORIGINS=[VITE])
class CorsTest(TestCase):

    def preflight(self, ruta, origen=VITE, encabezados='authorization, content-type, idempotency-key'):
        return self.client.options(ruta, HTTP_ORIGIN=origen, HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
                                   HTTP_ACCESS_CONTROL_REQUEST_HEADERS=encabezados)

    def test_el_preflight_de_la_api_movil_acepta_idempotency_key(self):
        respuesta = self.preflight('/api/movil/stock/mermas/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Access-Control-Allow-Origin'], VITE)
        permitidos = respuesta['Access-Control-Allow-Headers'].lower()
        for encabezado in ('idempotency-key', 'authorization', 'content-type'):
            self.assertIn(encabezado, permitidos)
        self.assertIn('POST', respuesta['Access-Control-Allow-Methods'])
        self.assertNotIn('Access-Control-Allow-Credentials', respuesta)

    def test_la_respuesta_real_lleva_el_origen_y_nunca_credenciales(self):
        usuario = f.usuario('40000001', 'Operador')
        respuesta = self.client.get('/api/movil/rubros/', HTTP_ORIGIN=VITE,
                                    HTTP_AUTHORIZATION=f'Token {emitir_token(usuario)}')
        self.assertEqual(respuesta['Access-Control-Allow-Origin'], VITE)
        self.assertNotIn('Access-Control-Allow-Credentials', respuesta)
        # Un error de la API también: si no, el navegador esconde el 401.
        sin_token = self.client.get('/api/movil/rubros/', HTTP_ORIGIN=VITE)
        self.assertEqual(sin_token.status_code, 401)
        self.assertEqual(sin_token['Access-Control-Allow-Origin'], VITE)

    def test_un_origen_que_no_esta_en_la_lista_no_recibe_permiso(self):
        respuesta = self.preflight('/api/movil/stock/mermas/', origen=AJENO)
        self.assertNotIn('Access-Control-Allow-Origin', respuesta)

    def test_fuera_de_la_api_movil_no_hay_cors(self):
        for ruta in ('/api/servicio/resumen/', '/login/', '/ventas/mostrador/', '/api/movilx/'):
            respuesta = self.preflight(ruta)
            self.assertNotIn('Access-Control-Allow-Origin', respuesta, ruta)
            respuesta = self.client.get(ruta, HTTP_ORIGIN=VITE)
            self.assertNotIn('Access-Control-Allow-Origin', respuesta, ruta)

    def test_la_configuracion_no_se_afloja(self):
        from django.conf import settings
        self.assertEqual(settings.CORS_URLS_REGEX, r'^/api/movil/.*$')
        self.assertFalse(settings.CORS_ALLOW_CREDENTIALS)
        self.assertFalse(getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False))
        self.assertIn('idempotency-key', settings.CORS_ALLOW_HEADERS)
        self.assertEqual(settings.MIDDLEWARE[0], 'corsheaders.middleware.CorsMiddleware')
