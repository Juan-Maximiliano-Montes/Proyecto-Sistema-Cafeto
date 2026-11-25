"""SEG-01: lo que le contesta el sistema a quien no puede pasar.

* ``requiere_permiso`` rebota al inicio por defecto y nombra el permiso en
  castellano, no por su código técnico.
* Con una clave provisoria sin cambiar, un pedido que espera JSON recibe un
  403 con el motivo en vez de una redirección que no puede leer.
"""
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from nucleo.tests import fabrica as f
from seguridad.decorators import nombre_legible
from seguridad.models import Permiso, Usuario

CODIGO = 'ver_usuarios'
JSON = {'HTTP_ACCEPT': 'application/json'}
XHR = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


@override_settings(ROOT_URLCONF='seguridad.tests.urls_permiso')
class RequierePermisoTest(TestCase):

    def setUp(self):
        self.operador = f.usuario('40200001', 'Operador')
        self.nombre = Permiso.objects.get(codigo=CODIGO).nombre
        self.assertFalse(self.operador.tiene_permiso(CODIGO))      # la precondición
        f.entrar(self.client, self.operador)

    def mensajes(self, respuesta):
        return [str(m) for m in get_messages(respuesta.wsgi_request)]

    def test_sin_el_permiso_rebota_al_inicio_por_defecto(self):
        respuesta = self.client.get('/prueba/protegida/')
        self.assertRedirects(respuesta, reverse('home'), fetch_redirect_response=False)

    def test_el_aviso_dice_el_nombre_del_permiso_y_no_su_codigo(self):
        (aviso,) = self.mensajes(self.client.get('/prueba/protegida/'))
        self.assertIn('Acceso denegado', aviso)
        self.assertIn(f'("{self.nombre}")', aviso)
        self.assertNotIn(CODIGO, aviso)

    def test_el_pedido_json_tambien_lo_nombra(self):
        respuesta = self.client.get('/prueba/protegida/', **JSON)
        self.assertEqual(respuesta.status_code, 403)
        self.assertIn(f'("{self.nombre}")', respuesta.json()['error'])
        self.assertNotIn(CODIGO, respuesta.json()['error'])

    def test_con_el_permiso_pasa(self):
        admin = f.usuario('40200002', 'Administrador')
        f.entrar(self.client, admin)
        self.assertContains(self.client.get('/prueba/protegida/'), 'adentro')

    def test_un_permiso_que_no_esta_cargado_se_nombra_por_su_codigo(self):
        self.assertEqual(nombre_legible(CODIGO), self.nombre)
        self.assertEqual(nombre_legible('permiso_que_no_existe'), 'permiso_que_no_existe')


class ClaveProvisoriaTest(TestCase):

    def setUp(self):
        self.usuario = f.usuario('40200003', 'Administrador')
        f.entrar(self.client, self.usuario)
        Usuario.objects.filter(pk=self.usuario.pk).update(debe_cambiar_clave=True)
        self.cambiar = reverse('seguridad:cambiar_clave')

    def test_una_pagina_comun_redirige_al_cambio_de_clave(self):
        respuesta = self.client.get(reverse('seguridad:lista'))
        self.assertRedirects(respuesta, self.cambiar, fetch_redirect_response=False)

    def test_un_pedido_json_recibe_un_403_con_el_motivo(self):
        for cabecera in (JSON, XHR):
            with self.subTest(cabecera=cabecera):
                respuesta = self.client.get(reverse('seguridad:lista'), **cabecera)
                self.assertEqual(respuesta.status_code, 403)
                self.assertEqual(respuesta['Content-Type'], 'application/json')
                self.assertEqual(respuesta.json(), {
                    'ok': False,
                    'error': 'Tenés que cambiar tu clave provisoria antes de continuar.'})

    def test_el_post_json_tambien(self):
        respuesta = self.client.post(reverse('seguridad:lista'), {}, **XHR)
        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(respuesta.json()['ok'])

    def test_la_pantalla_de_cambio_de_clave_no_se_bloquea_a_si_misma(self):
        self.assertEqual(self.client.get(self.cambiar).status_code, 200)
        self.assertEqual(self.client.get(self.cambiar, **JSON).status_code, 200)

    def test_cambiada_la_clave_el_pedido_json_pasa(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(debe_cambiar_clave=False)
        self.assertNotEqual(self.client.get(reverse('seguridad:lista'), **JSON).status_code, 403)
