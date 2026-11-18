"""Los flujos de n8n que se importan (docs/n8n_workflows_cafeto.json) no
dejan puertas abiertas (DD-103): el webhook de la factura leída exige el
token de Cafeto, el adjunto de WhatsApp sólo se baja del WAHA propio (con
la clave de WAHA no se le pide nada a otro servidor) y una factura que el
lector no pudo leer (422) avisa en lugar de seguir a la bandeja."""
import json
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

RUTA = Path(settings.BASE_DIR) / 'docs' / 'n8n_workflows_cafeto.json'


class FlujosN8nTest(SimpleTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.flujos = {f['name']: f for f in json.loads(RUTA.read_text(encoding='utf-8'))}

    def nodos(self, flujo):
        return {n['name']: n for n in flujo['nodes']}

    def siguientes(self, flujo, nombre, salida=0):
        return [d['node'] for d in flujo['connections'].get(nombre, {}).get('main', [[]] * (salida + 1))[salida]]

    def test_las_conexiones_apuntan_a_nodos_que_existen(self):
        for nombre, flujo in self.flujos.items():
            nodos = self.nodos(flujo)
            for origen, salidas in flujo['connections'].items():
                self.assertIn(origen, nodos, nombre)
                for salida in salidas['main']:
                    for destino in salida:
                        self.assertIn(destino['node'], nodos, nombre)

    def test_la_factura_leida_exige_el_token_de_cafeto(self):
        flujo = self.flujos['Cafeto · Factura leída → compra pre-cargada']
        nodos = self.nodos(flujo)
        self.assertEqual(self.siguientes(flujo, 'Llega la factura ya leída'), ['¿Trae el token de Cafeto?'])
        control = json.dumps(nodos['¿Trae el token de Cafeto?']['parameters'], ensure_ascii=False)
        self.assertIn('$json.headers.authorization', control)
        self.assertIn('Bearer {{ $env.CAFETO_N8N_TOKEN }}', control)
        self.assertIn("String($env.CAFETO_N8N_TOKEN || '').length >= 32", control)
        self.assertEqual(self.siguientes(flujo, '¿Trae el token de Cafeto?'), ['Pre-cargar la compra'])
        self.assertEqual(self.siguientes(flujo, '¿Trae el token de Cafeto?', 1), ['Rechazar sin token'])
        rechazo = nodos['Rechazar sin token']
        self.assertEqual(rechazo['type'], 'n8n-nodes-base.respondToWebhook')
        self.assertEqual(rechazo['parameters']['options']['responseCode'], 401)
        self.assertEqual(nodos['Llega la factura ya leída']['parameters']['responseMode'], 'responseNode')
        self.assertIn('r.en_bandeja', nodos['Armar el aviso']['parameters']['jsCode'])

    def test_el_adjunto_se_baja_sólo_del_waha_propio(self):
        flujo = self.flujos['Cafeto · Factura por WhatsApp o correo → bandeja de compras']
        filtro = self.nodos(flujo)['¿Trae un adjunto del WAHA propio y del número de compras?']
        condiciones = json.dumps(filtro['parameters'], ensure_ascii=False)
        self.assertIn("String($env.WAHA_URL || '').startsWith('http')", condiciones)
        self.assertIn('"operation": "startsWith"', condiciones)
        self.assertIn('$env.CAFETO_WHATSAPP_COMPRAS', condiciones)
        self.assertEqual(self.siguientes(flujo, '¿Trae un adjunto del WAHA propio y del número de compras?'),
                         ['Bajar la foto o el PDF'])

    def test_lo_que_el_lector_no_pudo_leer_avisa_y_no_va_a_la_bandeja(self):
        flujo = self.flujos['Cafeto · Factura por WhatsApp o correo → bandeja de compras']
        nodos = self.nodos(flujo)
        self.assertTrue(nodos['Leer la factura (lector de Cafeto)']['parameters']['options']
                        ['response']['response']['neverError'])
        self.assertEqual(self.siguientes(flujo, 'Leer la factura (lector de Cafeto)'), ['¿Se pudo leer?'])
        self.assertEqual(self.siguientes(flujo, '¿Se pudo leer?'), ['Dejarla en la bandeja de Compras'])
        self.assertEqual(self.siguientes(flujo, '¿Se pudo leer?', 1), ['Armar el aviso'])
        self.assertIn('!$json.detail', json.dumps(nodos['¿Se pudo leer?']['parameters']))
        self.assertIn('r.detail', nodos['Armar el aviso']['parameters']['jsCode'])
