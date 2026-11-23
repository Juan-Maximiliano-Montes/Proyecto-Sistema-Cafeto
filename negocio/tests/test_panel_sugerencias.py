"""El aviso de sugerencias en el inicio de la web y en el panel del celular.

Cada aviso del panel lleva una ``clave``, y cada pantalla tiene su tabla de
destinos: si una clave nueva no se agrega a las dos, el panel explota con
``KeyError`` apenas aparece la primera sugerencia. Eso es lo que se cuida
acá, con el aviso realmente presente.
"""
from django.test import TestCase
from django.urls import reverse

from negocio import panel
from nucleo.tests import fabrica as f
from stock import sugerencias as servicio

CLAVE = 'SUGERENCIAS_CATALOGO'


class Base(TestCase):

    def setUp(self):
        self.admin = f.usuario('30444100', 'Administrador')
        self.prov = f.proveedor()
        self.leche = f.producto('Leche entera', prov=self.prov)

    def proponer(self):
        return servicio.proponer(
            self.leche, 'RUBRO', 'Open Food Facts lo tiene en lácteos',
            {'rubro': 'Lácteos'})[0]


class AvisoDelPanelTest(Base):

    def test_sin_sugerencias_no_hay_aviso(self):
        _, avisos = panel.del_dia(self.admin)
        self.assertNotIn(CLAVE, [a['clave'] for a in avisos])

    def test_con_sugerencias_avisa_a_quien_edita_productos(self):
        self.proponer()
        _, avisos = panel.del_dia(self.admin)
        aviso = next(a for a in avisos if a['clave'] == CLAVE)
        self.assertEqual(aviso['tipo'], 'aviso')
        self.assertIn('sugerencia', aviso['texto'])

    def test_a_quien_no_edita_productos_no_le_aparece(self):
        self.proponer()
        cajera = f.usuario('30444101', 'Operador')
        _, avisos = panel.del_dia(cajera)
        self.assertNotIn(CLAVE, [a['clave'] for a in avisos])

    def test_una_sugerencia_resuelta_deja_de_avisar(self):
        sugerencia = self.proponer()
        servicio.descartar(sugerencia.pk, self.admin)
        _, avisos = panel.del_dia(self.admin)
        self.assertNotIn(CLAVE, [a['clave'] for a in avisos])


class InicioWebTest(Base):

    def test_el_inicio_dibuja_el_aviso_y_lo_enlaza(self):
        self.proponer()
        f.entrar(self.client, self.admin)
        inicio = self.client.get(reverse('home'))
        self.assertEqual(inicio.status_code, 200)
        destinos = [url for _tipo, _icono, _texto, url in inicio.context['avisos']]
        self.assertIn(reverse('stock:sugerencias'), destinos)
        self.assertContains(inicio, 'sugerencia')

    def test_todas_las_claves_del_panel_tienen_destino(self):
        """Ninguna clave sin entrada en AVISOS: ahí vive el KeyError."""
        from negocio.views.inicio import AVISOS

        self.proponer()
        _, avisos = panel.del_dia(self.admin)
        for aviso in avisos:
            self.assertIn(aviso['clave'], AVISOS)


class PanelMovilTest(Base):

    def test_el_panel_del_celular_no_rompe_y_manda_a_la_web(self):
        from api_movil.views_tareas import DESTINO_DEL_AVISO

        self.proponer()
        self.assertEqual(DESTINO_DEL_AVISO[CLAVE], 'WEB')
        _, avisos = panel.del_dia(self.admin)
        for aviso in avisos:
            self.assertIn(aviso['clave'], DESTINO_DEL_AVISO)
