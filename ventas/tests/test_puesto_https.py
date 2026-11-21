"""La cookie firmada del puesto (cafeto_puesto) detrás del proxy HTTPS del
VPS (DD-104): sólo viaja cifrada."""
from django.test import TestCase, override_settings
from django.urls import reverse

from nucleo.tests import fabrica as f
from ventas import puesto


class CookieDelPuestoTest(TestCase):

    def setUp(self):
        f.entrar(self.client, f.titular())

    def elegir(self, **encabezados):
        respuesta = self.client.post(reverse('ventas:elegir_caja'), {'caja': f.caja_minisuper().pk}, **encabezados)
        return respuesta.cookies[puesto.COOKIE]

    @override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'))
    def test_detras_de_nginx_con_https_sale_segura(self):
        cookie = self.elegir(HTTP_X_FORWARDED_PROTO='https')
        self.assertTrue(cookie['secure'])
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')

    @override_settings(SESSION_COOKIE_SECURE=True)
    def test_con_cookies_seguras_sale_segura_aunque_el_pedido_parezca_http(self):
        self.assertTrue(self.elegir()['secure'])

    def test_en_desarrollo_por_http_no(self):
        self.assertFalse(self.elegir()['secure'])
