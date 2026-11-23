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


class LeerFlujos:

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.flujos = {f['name']: f for f in json.loads(RUTA.read_text(encoding='utf-8'))}

    def nodos(self, flujo):
        return {n['name']: n for n in flujo['nodes']}

    def siguientes(self, flujo, nombre, salida=0):
        return [d['node'] for d in flujo['connections'].get(nombre, {}).get('main', [[]] * (salida + 1))[salida]]


class FlujosN8nTest(LeerFlujos, SimpleTestCase):

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


class AlineacionConDjangoTest(LeerFlujos, SimpleTestCase):
    """DD-104: un solo encabezado (``Authorization: Bearer``) en los dos
    sentidos, y cada dirección del sistema que usan los flujos existe."""

    def llamadas_al_sistema(self):
        for flujo in self.flujos.values():
            for n in flujo['nodes']:
                url = n.get('parameters', {}).get('url', '')
                if n['type'].endswith('httpRequest') and '$env.CAFETO_URL' in url:
                    yield flujo['name'], n

    def test_todas_las_llamadas_al_sistema_van_con_bearer(self):
        llamadas = list(self.llamadas_al_sistema())
        self.assertGreater(len(llamadas), 8)
        for nombre, n in llamadas:
            encabezados = {h['name']: h['value'] for h in n['parameters']['headerParameters']['parameters']}
            self.assertEqual(encabezados.get('Authorization'), '=Bearer {{ $env.CAFETO_N8N_TOKEN }}',
                             f'{nombre} · {n["name"]}')
        self.assertNotIn('x-cafeto-token', RUTA.read_text(encoding='utf-8').lower())

    def test_cada_direccion_del_sistema_existe(self):
        import re

        from django.urls import Resolver404, resolve
        for nombre, n in self.llamadas_al_sistema():
            ruta = n['parameters']['url'].split('}}', 1)[1]
            ruta = re.sub(r'\{\{[^}]*\}\}', '1', ruta)
            try:
                coincidencia = resolve(ruta)
            except Resolver404:
                self.fail(f'{nombre} · {n["name"]}: {ruta} no existe')
            self.assertEqual(coincidencia.namespace, 'api_servicio', ruta)

    def test_el_aviso_de_autorizacion_exige_el_bearer_del_sistema(self):
        """El token se controla en un nodo IF, igual que en el de facturas.

        Antes lo revisaba el propio nodo Code y cortaba con ``return []``:
        el flujo terminaba sin mandar nada y sin dejar rastro, así que un
        token mal puesto y una caída del JS Task Runner —que en un equipo
        con poca memoria pasa— se veían exactamente igual. Con el IF, el
        rechazo es un 401 que el sistema anota en su log.
        """
        flujo = self.flujos['Cafeto · Autorización a distancia por WhatsApp']
        nodos = self.nodos(flujo)
        self.assertEqual(self.siguientes(flujo, 'La caja pide autorización'),
                         ['¿Trae el token de Cafeto?'])
        control = json.dumps(nodos['¿Trae el token de Cafeto?']['parameters'], ensure_ascii=False)
        self.assertIn('$json.headers.authorization', control)
        self.assertIn('Bearer {{ $env.CAFETO_N8N_TOKEN }}', control)
        self.assertIn("String($env.CAFETO_N8N_TOKEN || '').length >= 32", control)
        self.assertEqual(self.siguientes(flujo, '¿Trae el token de Cafeto?'), ['Armar el mensaje'])
        self.assertEqual(self.siguientes(flujo, '¿Trae el token de Cafeto?', 1), ['Rechazar sin token'])
        rechazo = nodos['Rechazar sin token']
        self.assertEqual(rechazo['type'], 'n8n-nodes-base.respondToWebhook')
        self.assertEqual(rechazo['parameters']['options']['responseCode'], 401)
        self.assertEqual(nodos['La caja pide autorización']['parameters']['responseMode'],
                         'responseNode')
        # El nodo Code ya no decide nada sobre el token: sólo arma el texto.
        # Se miran las LÍNEAS de código, no los comentarios: el comentario
        # del nodo explica justamente el «return []» que se sacó.
        lineas = nodos['Armar el mensaje']['parameters']['jsCode'].splitlines()
        codigo = '\n'.join(l for l in lineas if not l.strip().startswith('//'))
        self.assertNotIn('return []', codigo)
        self.assertNotIn('CAFETO_N8N_TOKEN', codigo)

    def test_los_mensajes_de_whatsapp_van_con_json_stringify(self):
        """Un salto de línea sin escapar rompe el JSON que recibe WAHA.

        Los mensajes llevan varias líneas (el PIN, el enlace, los
        renglones del ticket). Armando el cuerpo a mano, el primer ``\\n``
        dejaba a WAHA con un JSON inválido y el aviso no salía.
        """
        encontrados = 0
        for nombre, flujo in self.flujos.items():
            for nodo in flujo['nodes']:
                if not nodo['type'].endswith('httpRequest'):
                    continue
                if 'whatsapp' not in nodo['name'].lower():
                    continue
                encontrados += 1
                self.assertIn('JSON.stringify', nodo['parameters'].get('jsonBody', ''),
                              f'{nombre} · {nodo["name"]}')
        self.assertGreaterEqual(encontrados, 6)

    def test_las_fotos_de_los_productos_sin_foto(self):
        flujo = self.flujos['Cafeto · Fotos de los productos sin foto (Open Food Facts)']
        nodos = self.nodos(flujo)
        self.assertEqual(nodos['Productos sin foto']['parameters']['url'],
                         '={{ $env.CAFETO_URL }}/api/servicio/productos/sin-foto/')
        self.assertIn('/api/servicio/productos/{{ $json.id }}/foto/', nodos['Ponerle la foto']['parameters']['url'])
        orden = ['Cada noche 3:30', 'Productos sin foto', 'Uno por producto',
                 'Buscar la foto en Open Food Facts', '¿Tiene foto?', 'Ponerle la foto']
        for actual, siguiente in zip(orden, orden[1:]):
            self.assertEqual(self.siguientes(flujo, actual), [siguiente])
        off = nodos['Buscar la foto en Open Food Facts']['parameters']
        self.assertTrue(off['options']['response']['response']['neverError'])
        self.assertEqual(off['options']['batching']['batch']['batchSize'], 1)
        self.assertIn('User-Agent', {h['name'] for h in off['headerParameters']['parameters']})
        self.assertIn("replace(/^http:/, 'https:')", nodos['¿Tiene foto?']['parameters']['jsCode'])


class FlujosDeMejoraContinuaTest(LeerFlujos, SimpleTestCase):
    """Los tres flujos de la Etapa 2 (DD-84: proponen, no cambian).

    El nocturno de rubros es el único que escribe, y escribe en la bandeja
    de SUGERENCIAS: nada del catálogo cambia sin que una persona lo
    apruebe. Los otros dos sólo leen y mandan un mensaje.
    """

    def test_el_semanal_mira_recetas_y_margenes_en_un_solo_mensaje(self):
        flujo = self.flujos['Cafeto · Recetas y márgenes (semanal)']
        nodos = self.nodos(flujo)
        self.assertEqual(nodos['Lunes 09:00']['parameters']['rule']['interval'][0]['expression'],
                         '0 9 * * 1')
        self.assertEqual(nodos['Elaborados sin receta']['parameters']['url'],
                         '={{ $env.CAFETO_URL }}/api/servicio/productos/sin-receta/')
        self.assertEqual(nodos['Productos con margen caído']['parameters']['url'],
                         '={{ $env.CAFETO_URL }}/api/servicio/productos/margen-bajo/')
        orden = ['Lunes 09:00', 'Elaborados sin receta', 'Productos con margen caído',
                 'Armar el mensaje', 'Mandar por WhatsApp']
        for actual, siguiente in zip(orden, orden[1:]):
            self.assertEqual(self.siguientes(flujo, actual), [siguiente])
        # Un solo mensaje con las dos cosas: el top de recetas con lo que
        # facturó y los márgenes caídos.
        codigo = nodos['Armar el mensaje']['parameters']['jsCode']
        self.assertIn("$('Elaborados sin receta')", codigo)
        self.assertIn('facturado', codigo)
        self.assertIn('SIN_COSTO', codigo)
        self.assertIn('utilidad_porcentaje', codigo)

    def test_la_mision_de_conteo_sale_de_lunes_a_sabado_antes_de_abrir(self):
        flujo = self.flujos['Cafeto · Misión conteo rápido (diario)']
        nodos = self.nodos(flujo)
        self.assertEqual(nodos['Lunes a sábado 8:30']['parameters']['rule']['interval'][0]['expression'],
                         '30 8 * * 1-6')
        self.assertEqual(nodos['Lo que conviene contar hoy']['parameters']['url'],
                         '={{ $env.CAFETO_URL }}/api/servicio/productos/conteo-rapido/')
        orden = ['Lunes a sábado 8:30', 'Lo que conviene contar hoy',
                 'Armar la misión', 'Mandar por WhatsApp']
        for actual, siguiente in zip(orden, orden[1:]):
            self.assertEqual(self.siguientes(flujo, actual), [siguiente])
        # Sin nada que contar no se manda un mensaje vacío.
        self.assertIn('return []', nodos['Armar la misión']['parameters']['jsCode'])

    def test_el_nocturno_de_rubros_propone_y_no_cambia_nada(self):
        flujo = self.flujos['Cafeto · Sugerir rubros (nocturno)']
        nodos = self.nodos(flujo)
        self.assertEqual(nodos['Cada noche 3:30']['parameters']['rule']['interval'][0]['expression'],
                         '30 3 * * *')
        orden = ['Cada noche 3:30', 'Productos sin clasificar', 'Uno por producto',
                 'Categorías en Open Food Facts', 'Proponer el rubro', 'Dejar la sugerencia']
        for actual, siguiente in zip(orden, orden[1:]):
            self.assertEqual(self.siguientes(flujo, actual), [siguiente])
        # Lo único que escribe es la bandeja de sugerencias.
        final = nodos['Dejar la sugerencia']['parameters']
        self.assertEqual(final['url'],
                         '={{ $env.CAFETO_URL }}/api/servicio/productos/sugerencias/')
        self.assertEqual(final['method'], 'POST')
        self.assertIn('JSON.stringify', final['jsonBody'])
        for nodo in flujo['nodes']:
            url = nodo.get('parameters', {}).get('url', '')
            if '$env.CAFETO_URL' in url and nodo['parameters'].get('method') == 'POST':
                self.assertIn('/sugerencias/', url)

    def test_los_rubros_del_local_estan_en_las_palabras_clave(self):
        codigo = self.nodos(
            self.flujos['Cafeto · Sugerir rubros (nocturno)'])['Proponer el rubro']['parameters']['jsCode']
        for rubro in ('Almacén', 'Bebidas con Alcohol', 'Bebidas sin Alcohol',
                      'Limpieza', 'Lácteos', 'Perfumería', 'Kiosco',
                      'Panadería', 'Fiambrería'):
            self.assertIn(rubro, codigo)
        self.assertIn("tipo: 'RUBRO'", codigo)

    def test_open_food_facts_se_consulta_con_cuidado(self):
        """De a uno, con pausa, con User-Agent y sin cortar el flujo si falla."""
        off = self.nodos(self.flujos['Cafeto · Sugerir rubros (nocturno)'])[
            'Categorías en Open Food Facts']['parameters']
        self.assertTrue(off['options']['response']['response']['neverError'])
        self.assertEqual(off['options']['batching']['batch']['batchSize'], 1)
        self.assertIn('User-Agent', {h['name'] for h in off['headerParameters']['parameters']})
        self.assertNotIn('Authorization',
                         {h['name'] for h in off['headerParameters']['parameters']})

    def test_los_parametros_viajan_aparte_de_la_direccion(self):
        """La dirección es la ruta que publica Django, sin ``?`` pegado."""
        for nombre in ('Cafeto · Recetas y márgenes (semanal)',
                       'Cafeto · Misión conteo rápido (diario)',
                       'Cafeto · Sugerir rubros (nocturno)'):
            for nodo in self.flujos[nombre]['nodes']:
                url = nodo.get('parameters', {}).get('url', '')
                if '$env.CAFETO_URL' in url:
                    self.assertNotIn('?', url, f'{nombre} · {nodo["name"]}')
