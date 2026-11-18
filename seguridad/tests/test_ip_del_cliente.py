"""La IP con la que queda registrado quien entra (DD-103): detrás del
túnel de Cloudflare manda CF-Connecting-IP; X-Forwarded-For se lee desde
la derecha, porque lo de la izquierda lo escribe cualquiera."""
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

    @override_settings(CONFIAR_EN_PROXY=False)
    def test_sin_proxy_de_confianza_no_se_lee_ningun_encabezado(self):
        pedido = self.pedido(HTTP_CF_CONNECTING_IP='190.12.34.56', HTTP_X_FORWARDED_FOR='190.12.34.56')
        self.assertEqual(ip_del_request(pedido), '172.18.0.5')
