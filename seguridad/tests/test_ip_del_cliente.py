"""La IP con la que queda registrado quien entra (DD-103): detrás del
túnel de Cloudflare manda CF-Connecting-IP; X-Forwarded-For se lee desde
la derecha, porque lo de la izquierda lo escribe cualquiera."""
import os
import subprocess
import sys

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, override_settings

from seguridad.servicios import ip_del_request


class IpDelClienteTest(SimpleTestCase):

    def pedido(self, **encabezados):
        return RequestFactory().get('/', REMOTE_ADDR='172.18.0.5', **encabezados)

    @override_settings(CONFIAR_EN_PROXY=True, ENCABEZADO_IP_CLIENTE='HTTP_CF_CONNECTING_IP', PROXIES_DE_CONFIANZA=1)
    def test_cloudflare_manda(self):
        pedido = self.pedido(HTTP_CF_CONNECTING_IP='190.12.34.56', HTTP_X_FORWARDED_FOR='6.6.6.6, 190.12.34.56')
        self.assertEqual(ip_del_request(pedido), '190.12.34.56')
        pedido = self.pedido(HTTP_CF_CONNECTING_IP=' 2800:810:4a3::1 ')
        self.assertEqual(ip_del_request(pedido), '2800:810:4a3::1')

    @override_settings(CONFIAR_EN_PROXY=True, ENCABEZADO_IP_CLIENTE='HTTP_CF_CONNECTING_IP', PROXIES_DE_CONFIANZA=1)
    def test_sin_cloudflare_el_forwarded_for_se_lee_desde_la_derecha(self):
        # El cliente escribió «6.6.6.6»; el proxy agregó la IP real al final.
        pedido = self.pedido(HTTP_X_FORWARDED_FOR='6.6.6.6, 190.12.34.56')
        self.assertEqual(ip_del_request(pedido), '190.12.34.56')
        with self.settings(PROXIES_DE_CONFIANZA=2):
            self.assertEqual(ip_del_request(self.pedido(HTTP_X_FORWARDED_FOR='6.6.6.6, 190.12.34.56, 10.0.0.2')),
                             '190.12.34.56')
            # Menos saltos que proxies: no se le cree a ninguno.
            self.assertEqual(ip_del_request(self.pedido(HTTP_X_FORWARDED_FOR='6.6.6.6')), '172.18.0.5')

    @override_settings(CONFIAR_EN_PROXY=True, ENCABEZADO_IP_CLIENTE='HTTP_CF_CONNECTING_IP', PROXIES_DE_CONFIANZA=1)
    def test_lo_que_no_es_una_ip_no_se_usa(self):
        for basura in ('<script>', 'localhost', '1.2.3.4.5', "1.1.1.1' OR 1=1", 'x' * 300):
            pedido = self.pedido(HTTP_CF_CONNECTING_IP=basura, HTTP_X_FORWARDED_FOR=basura)
            self.assertEqual(ip_del_request(pedido), '172.18.0.5', basura)

    @override_settings(CONFIAR_EN_PROXY=True, ENCABEZADO_IP_CLIENTE='', PROXIES_DE_CONFIANZA=1)
    def test_detras_de_nginx_en_docker_cada_cliente_con_su_ip(self):
        """DD-104: REMOTE_ADDR es el contenedor de Nginx (172.18.0.5) para
        todos; si se usara, un bloqueo por intentos frenaría al local entero."""
        caja = self.pedido(HTTP_X_FORWARDED_FOR='190.12.34.56')
        celular = self.pedido(HTTP_X_FORWARDED_FOR='181.45.6.7')
        self.assertEqual(ip_del_request(caja), '190.12.34.56')
        self.assertEqual(ip_del_request(celular), '181.45.6.7')
        # Con Nginx al frente, un CF-Connecting-IP lo escribe el cliente: no vale.
        falso = self.pedido(HTTP_CF_CONNECTING_IP='7.7.7.7', HTTP_X_FORWARDED_FOR='6.6.6.6, 190.12.34.56')
        self.assertEqual(ip_del_request(falso), '190.12.34.56')

    def test_por_defecto_no_se_le_cree_a_cf_connecting_ip(self):
        self.assertTrue(settings.CONFIAR_EN_PROXY)
        self.assertEqual(settings.ENCABEZADO_IP_CLIENTE, '')

    @override_settings(CONFIAR_EN_PROXY=False)
    def test_sin_proxy_de_confianza_no_se_lee_ningun_encabezado(self):
        pedido = self.pedido(HTTP_CF_CONNECTING_IP='190.12.34.56', HTTP_X_FORWARDED_FOR='190.12.34.56')
        self.assertEqual(ip_del_request(pedido), '172.18.0.5')


class ProduccionTest(SimpleTestCase):
    """La configuración con DJANGO_DEBUG=False, como la arma el VPS (DD-104):
    se carga en un proceso aparte para no tocar la de las pruebas."""

    def configuracion(self, **entorno):
        codigo = (
            'import json, django; from django.conf import settings as s; django.setup();'
            'from django.urls import resolve, Resolver404\n'
            'def hay(r):\n'
            '    try: resolve(r); return True\n'
            '    except Resolver404: return False\n'
            'print(json.dumps({"session": s.SESSION_COOKIE_SECURE, "csrf": s.CSRF_COOKIE_SECURE,'
            ' "proxy": list(getattr(s, "SECURE_PROXY_SSL_HEADER", None) or []),'
            ' "origenes": s.CSRF_TRUSTED_ORIGINS, "cors": s.CORS_ALLOWED_ORIGINS,'
            ' "admin": hay("/" + (s.CAFETO_ADMIN_URL or "admin/")), "exento": s.SECURE_REDIRECT_EXEMPT}))')
        base = {k: v for k, v in os.environ.items() if not k.startswith(('DJANGO_', 'CAFETO_'))}
        base.update({'DJANGO_SETTINGS_MODULE': 'cafeto.settings', 'DJANGO_DEBUG': 'False',
                     'DJANGO_SECRET_KEY': 'una-clave-larga-de-prueba-para-produccion-1234567890',
                     'DJANGO_ALLOWED_HOSTS': 'cafeto.tu-dominio.com,web'}, **entorno)
        salida = subprocess.run([sys.executable, '-c', codigo], env=base, capture_output=True, text=True,
                                cwd=settings.BASE_DIR, check=True)
        import json
        return json.loads(salida.stdout.strip().splitlines()[-1])

    def test_cookies_seguras_proxy_y_csrf_del_entorno(self):
        datos = self.configuracion(DJANGO_CSRF_TRUSTED_ORIGINS='https://cafeto.tu-dominio.com',
                                   CAFETO_CORS_ORIGINS='https://app.tu-dominio.com')
        self.assertTrue(datos['session'])
        self.assertTrue(datos['csrf'])
        self.assertEqual(datos['proxy'], ['HTTP_X_FORWARDED_PROTO', 'https'])
        self.assertEqual(datos['origenes'], ['https://cafeto.tu-dominio.com'])
        self.assertEqual(datos['cors'], ['https://app.tu-dominio.com'])
        self.assertEqual(datos['exento'], [r'^api/servicio/'])

    def test_sin_cafeto_admin_url_no_hay_admin_y_con_una_ruta_si(self):
        self.assertFalse(self.configuracion()['admin'])
        self.assertTrue(self.configuracion(CAFETO_ADMIN_URL='/gestion-7f3k2/')['admin'])

    def test_sin_origenes_cors_en_produccion_no_se_abre_a_nadie(self):
        self.assertEqual(self.configuracion()['cors'], [])

    def test_cloudflare_tunnel_suma_host_y_origen_https(self):
        codigo = (
            'import json, django; from django.conf import settings as s; django.setup();'
            'print(json.dumps({"hosts": s.ALLOWED_HOSTS, "origenes": s.CSRF_TRUSTED_ORIGINS,'
            ' "proxy": list(getattr(s, "SECURE_PROXY_SSL_HEADER", None) or [])}))')
        base = {k: v for k, v in os.environ.items() if not k.startswith(('DJANGO_', 'CAFETO_'))}
        base.update({
            'DJANGO_SETTINGS_MODULE': 'cafeto.settings',
            'DJANGO_DEBUG': 'False',
            'DJANGO_SECRET_KEY': 'una-clave-larga-de-prueba-para-produccion-1234567890',
            'DJANGO_ALLOWED_HOSTS': '192.168.0.10,cafeto.local',
            'CAFETO_TUNNEL_DOMINIO': 'cafeto.midominio.com.ar',
        })
        salida = subprocess.run([sys.executable, '-c', codigo], env=base,
                                capture_output=True, text=True, cwd=settings.BASE_DIR, check=True)
        import json
        datos = json.loads(salida.stdout.strip().splitlines()[-1])
        self.assertIn('cafeto.midominio.com.ar', datos['hosts'])
        self.assertIn('https://cafeto.midominio.com.ar', datos['origenes'])
        self.assertEqual(datos['proxy'], ['HTTP_X_FORWARDED_PROTO', 'https'])
