"""El aviso del sistema a n8n (DD-104): el mismo encabezado con que n8n le
habla al sistema, un reintento corto y la falla en el log con su motivo."""
import urllib.error
from unittest import mock

from django.test import SimpleTestCase, override_settings

from ventas import a_distancia

TOKEN = 'x' * 40
URL = 'http://n8n:5678/webhook/cafeto-autorizacion'


class Respuesta:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def error_http(codigo):
    return urllib.error.HTTPError(URL, codigo, 'mal', {}, None)


@override_settings(CAFETO_N8N_TOKEN=TOKEN)
class WebhookTest(SimpleTestCase):

    def llamar(self, *efectos):
        falla = any(isinstance(e, Exception) for e in efectos)
        log = (self.assertLogs if falla else self.assertNoLogs)('ventas.a_distancia', level='WARNING')
        with mock.patch('urllib.request.urlopen', side_effect=list(efectos)) as abrir, \
                mock.patch('time.sleep') as dormir, log as registro:
            resultado = a_distancia._webhook(URL, {'solicitud': 7})
        return resultado, abrir, dormir, registro

    def test_manda_authorization_bearer_y_no_el_encabezado_viejo(self):
        resultado, abrir, dormir, _ = self.llamar(Respuesta())
        self.assertEqual(resultado, 200)
        pedido = abrir.call_args.args[0]
        self.assertEqual(pedido.get_header('Authorization'), f'Bearer {TOKEN}')
        self.assertIsNone(pedido.get_header('X-cafeto-token'))
        dormir.assert_not_called()

    def test_si_n8n_no_contesta_reintenta_una_vez_al_segundo(self):
        resultado, abrir, dormir, registro = self.llamar(urllib.error.URLError('connection refused'), Respuesta())
        self.assertEqual(resultado, 200)
        self.assertEqual(abrir.call_count, 2)
        dormir.assert_called_once_with(1)
        self.assertIn('connection refused', registro.output[0])
        self.assertIn('se reintenta', registro.output[0])

    def test_dos_errores_del_servidor_quedan_en_el_log_con_el_codigo(self):
        resultado, abrir, _dormir, registro = self.llamar(error_http(502), error_http(503))
        self.assertIsNone(resultado)
        self.assertEqual(abrir.call_count, 2)
        self.assertIn('HTTP 503', registro.output[-1])
        self.assertTrue(registro.records[-1].levelname == 'ERROR')

    def test_un_401_no_se_reintenta(self):
        resultado, abrir, dormir, registro = self.llamar(error_http(401))
        self.assertIsNone(resultado)
        self.assertEqual(abrir.call_count, 1)
        dormir.assert_not_called()
        self.assertIn('HTTP 401', registro.output[-1])
        self.assertNotIn(TOKEN, ' '.join(registro.output))
