"""El panel del celular con sugerencias pendientes (Etapa 2).

El panel del celular arma cada aviso buscando su clave en
``DESTINO_DEL_AVISO``. Una clave nueva que no esté ahí no da un aviso
raro: tira ``KeyError`` y deja a toda la app sin panel. Se prueba con una
sugerencia de verdad, no con la tabla sola.
"""
from api_movil.tests.base import BaseApi
from nucleo.tests import fabrica as f
from stock import sugerencias as servicio


class PanelConSugerenciasTest(BaseApi):

    def setUp(self):
        super().setUp()
        self.prov = f.proveedor()
        self.leche = f.producto('Leche entera', prov=self.prov)
        servicio.proponer(self.leche, 'RUBRO',
                          'Open Food Facts lo tiene en lácteos',
                          {'rubro': 'Lácteos'})

    def test_el_panel_responde_y_manda_la_sugerencia_a_la_web(self):
        respuesta = self.pedir('get', '/panel/', como=self.admin)
        self.assertEqual(respuesta.status_code, 200)
        avisos = respuesta.json()['avisos']
        aviso = next(a for a in avisos if 'sugerencia' in a['texto'])
        self.assertEqual(aviso['destino'], 'WEB')
        self.assertEqual(aviso['tipo'], 'aviso')

    def test_a_quien_no_edita_productos_no_le_llega(self):
        respuesta = self.pedir('get', '/panel/', como=self.operador)
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse([a for a in respuesta.json()['avisos']
                          if 'sugerencia' in a['texto']])

    def test_dice_lo_mismo_que_el_inicio_de_la_web(self):
        from django.urls import reverse

        celular = self.pedir('get', '/panel/', como=self.admin).json()
        f.entrar(self.client, self.admin)
        web = self.client.get(reverse('home'), follow=True).context
        self.assertEqual([texto for _, _, texto, _ in web['avisos']],
                         [a['texto'] for a in celular['avisos']])
