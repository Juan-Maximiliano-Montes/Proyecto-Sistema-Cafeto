"""Hallazgos de la auditoría de noviembre 2026 sobre Seguridad.

    RevocarAlRestablecerTest   restablecer la clave tiene que ECHAR a quien
                               esté adentro: si no, una sesión abierta
                               elegía clave nueva sin conocer ninguna
    PerfilSinPermisosTest      DD-14 también en el ALTA
    EdicionDeDadoDeBajaTest    V-G13: lo dado de baja no se edita
    MenuPorPermisoTest         quien concilia pagos y quien administra el
                               fiado tienen que VER su pantalla en el menú
    ImagenesExternasTest       la CSP deja entrar la foto de Open Food
                               Facts sin aflojar script-src
"""
from django.test import TestCase
from django.urls import reverse

from api_movil.models import SesionMovil
from nucleo.tests import fabrica as f
from seguridad import menu
from seguridad.models import Perfil, PerfilPermiso, Permiso, Usuario

CLAVE_NUEVA = 'Otra.Clave.Larga.2026'


def perfil_con(nombre, *codigos):
    """Un perfil con exactamente esos permisos."""
    perfil = Perfil.objects.create(nombre=nombre[:20],
                                   tope_descuento_porcentaje=0)
    for codigo in codigos:
        PerfilPermiso.objects.create(
            perfil=perfil, permiso=Permiso.objects.get(codigo=codigo))
    return perfil


# ===========================================================================
class RevocarAlRestablecerTest(TestCase):
    """La clave se restablece porque la cuenta se perdió o se comprometió.

    El agujero: la sesión que ya estaba abierta seguía viva, y
    ``/cambiar-clave/`` no pide la clave actual cuando
    ``debe_cambiar_clave`` está puesto. Quien estuviera adentro elegía una
    clave nueva sin haber conocido ni la vieja ni la provisoria, y se
    quedaba con la cuenta.
    """

    def setUp(self):
        self.titular = f.titular()
        self.beto = f.usuario('40000001', 'Operador', nombre='Beto')

    def _sesion_movil(self):
        return SesionMovil.objects.create(
            id_usuario=self.beto, jti='x' * 32, ip_origen='127.0.0.1')

    def test_el_modelo_cierra_la_web_y_el_celular(self):
        from seguridad.servicios import abrir_sesion

        web = abrir_sesion(self.beto, ip='127.0.0.1')
        movil = self._sesion_movil()
        self.beto.restablecer_clave()
        web.refresh_from_db()
        movil.refresh_from_db()
        self.assertFalse(web.activa)
        self.assertEqual(web.motivo_cierre, 'REVOCACION')
        self.assertIsNotNone(movil.revocada_en)
        self.assertEqual(movil.motivo_revocacion, 'CAMBIO_DE_CLAVE')

    def test_la_pantalla_de_usuarios_tambien_lo_cierra(self):
        from seguridad.servicios import abrir_sesion

        sesion = abrir_sesion(self.beto, ip='127.0.0.1')
        movil = self._sesion_movil()
        f.entrar(self.client, self.titular)
        self.client.post(reverse('seguridad:restablecer_clave', args=[self.beto.pk]))
        sesion.refresh_from_db()
        movil.refresh_from_db()
        self.assertFalse(sesion.activa)
        self.assertIsNotNone(movil.revocada_en)

    def test_la_sesion_abierta_ya_no_puede_elegir_clave(self):
        """Lo que esto previene, de punta a punta."""
        atacante = self.client
        f.entrar(atacante, self.beto)
        self.assertEqual(atacante.get(reverse('home')).status_code, 200)
        # La titular restablece la clave de Beto desde otro navegador.
        otro = self.client_class()
        f.entrar(otro, self.titular)
        otro.post(reverse('seguridad:restablecer_clave', args=[self.beto.pk]))
        # La sesión que había quedado abierta ya no sirve para nada.
        respuesta = atacante.post(reverse('seguridad:cambiar_clave'), {
            'clave_nueva': CLAVE_NUEVA, 'clave_nueva_2': CLAVE_NUEVA}, follow=True)
        self.assertTemplateUsed(respuesta, 'seguridad/login.html')
        self.beto.refresh_from_db()
        self.assertFalse(self.beto.verificar_clave(CLAVE_NUEVA))

    def test_no_toca_las_sesiones_de_otra_persona(self):
        from seguridad.servicios import abrir_sesion

        ana = f.usuario('40000003', 'Operador', nombre='Ana')
        suya = abrir_sesion(ana, ip='127.0.0.1')
        self.beto.restablecer_clave()
        suya.refresh_from_db()
        self.assertTrue(suya.activa)


# ===========================================================================
class PerfilSinPermisosTest(TestCase):
    """DD-14: un perfil asignable lleva al menos un permiso.

    La regla sólo miraba a los perfiles que YA tenían permisos, así que el
    ALTA dejaba crear uno vacío: se le podía asignar a una persona y esa
    persona no podía hacer nada ni entender por qué.
    """

    def setUp(self):
        f.entrar(self.client, f.titular())
        self.url = reverse('seguridad:crear_perfil')

    def test_no_se_crea_un_perfil_sin_ningun_permiso(self):
        respuesta = self.client.post(self.url, {
            'nombre': 'Repositor', 'descripcion': 'Prueba',
            'tope_descuento_porcentaje': '0'}, follow=True)
        self.assertContains(respuesta, 'sin ningún permiso')
        self.assertFalse(Perfil.objects.filter(nombre='Repositor').exists(),
                         'el perfil no puede quedar guardado y vacío')

    def test_con_un_permiso_se_crea(self):
        permiso = Permiso.objects.get(codigo='ver_ventas')
        self.client.post(self.url, {
            'nombre': 'Repositor', 'descripcion': 'Prueba',
            'tope_descuento_porcentaje': '0', 'permisos': [permiso.pk]}, follow=True)
        perfil = Perfil.objects.get(nombre='Repositor')
        self.assertEqual([p.codigo for p in perfil.permisos.all()], ['ver_ventas'])

    def test_tampoco_se_le_pueden_quitar_todos_despues(self):
        perfil = perfil_con('Repositor', 'ver_ventas')
        respuesta = self.client.post(
            reverse('seguridad:editar_perfil', args=[perfil.pk]),
            {'nombre': 'Repositor', 'descripcion': '',
             'tope_descuento_porcentaje': '0'}, follow=True)
        self.assertContains(respuesta, 'sin ningún permiso')
        self.assertEqual(PerfilPermiso.objects.filter(perfil=perfil).count(), 1)


# ===========================================================================
class EdicionDeDadoDeBajaTest(TestCase):
    """V-G13: lo que está dado de baja se reactiva antes de editarse."""

    def setUp(self):
        f.entrar(self.client, f.titular())
        self.beto = f.usuario('40000001', 'Operador', nombre='Beto')
        self.perfil = perfil_con('Repositor', 'ver_ventas')

    def test_no_se_edita_el_correo_de_un_usuario_de_baja(self):
        self.beto.dar_de_baja()
        respuesta = self.client.post(
            reverse('seguridad:editar', args=[self.beto.pk]),
            {'correo': 'otro@cafeto.test'}, follow=True)
        self.assertContains(respuesta, 'dado de baja')
        self.beto.refresh_from_db()
        self.assertNotEqual(self.beto.correo, 'otro@cafeto.test')

    def test_el_boton_editar_no_se_dibuja_para_uno_de_baja(self):
        self.beto.dar_de_baja()
        html = self.client.get(reverse('seguridad:lista'),
                               {'estado': 'baja'}).content.decode()
        self.assertNotIn(reverse('seguridad:editar', args=[self.beto.pk]), html)
        self.assertIn(reverse('seguridad:reactivar', args=[self.beto.pk]), html)

    def test_un_usuario_vigente_se_sigue_editando(self):
        self.client.post(reverse('seguridad:editar', args=[self.beto.pk]),
                         {'correo': 'otro@cafeto.test'}, follow=True)
        self.beto.refresh_from_db()
        self.assertEqual(self.beto.correo, 'otro@cafeto.test')

    def test_no_se_edita_un_perfil_de_baja(self):
        Perfil.objects.filter(pk=self.perfil.pk).update(borrado_logico=True)
        permiso = Permiso.objects.get(codigo='ver_compras')
        respuesta = self.client.post(
            reverse('seguridad:editar_perfil', args=[self.perfil.pk]),
            {'nombre': 'Otro nombre', 'descripcion': '',
             'tope_descuento_porcentaje': '0', 'permisos': [permiso.pk]}, follow=True)
        self.assertContains(respuesta, 'dado de baja')
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.nombre, 'Repositor')
        self.assertEqual([p.codigo for p in self.perfil.permisos.all()], ['ver_ventas'])


# ===========================================================================
class ModalDePerfilTest(TestCase):
    """Cuando el guardado falla, el modal vuelve CON lo que se había
    cargado: si no, hay que volver a tildar los permisos de memoria."""

    def setUp(self):
        f.entrar(self.client, f.titular())

    def test_el_maxlength_del_nombre_es_el_de_la_columna(self):
        html = self.client.get(reverse('seguridad:perfiles')).content.decode()
        self.assertIn('id="edit_nombre"', html)
        self.assertIn('maxlength="20"', html)
        self.assertNotIn('maxlength="40"', html)

    def test_al_fallar_vuelven_los_tildes_el_tope_y_el_tipo(self):
        perfil = perfil_con('Repositor', 'ver_ventas')
        otro = Permiso.objects.get(codigo='ver_compras')
        respuesta = self.client.post(
            reverse('seguridad:editar_perfil', args=[perfil.pk]),
            {'nombre': 'Administrador',           # repetido: falla
             'descripcion': 'Reposición', 'tope_descuento_porcentaje': '15',
             'permisos': [otro.pk]})
        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.content.decode()
        self.assertEqual(respuesta.context['permisos_elegidos'], {str(otro.pk)})
        self.assertIn(f'id="edit_perm_{otro.pk}"', html)
        self.assertIn('value="15"', html)
        self.assertIn('data-es-sistema="0"', html)
        self.assertIn('value="Administrador"', html)

    def test_el_perfil_del_sistema_se_reconoce_al_reabrirse(self):
        sistema = Perfil.objects.get(es_sistema=True)
        respuesta = self.client.post(
            reverse('seguridad:editar_perfil', args=[sistema.pk]),
            {'nombre': '', 'descripcion': ''})      # nombre vacío: falla
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('data-es-sistema="1"', respuesta.content.decode())


# ===========================================================================
class MenuPorPermisoTest(TestCase):
    """Un ítem del menú tiene que aparecer para TODOS los permisos que
    trabajan en esa pantalla, no sólo para el primero que se escribió."""

    def titulos(self, usuario):
        return {item['titulo'] for grupo in menu.armar(usuario)
                for item in grupo['items']}

    def test_quien_solo_concilia_ve_pagos_y_gastos(self):
        usuario = f.usuario('40000004', 'Operador')
        usuario.id_perfil = perfil_con('Tesorería', 'conciliar_egresos')
        self.assertIn('Pagos y gastos', self.titulos(usuario))

    def test_quien_solo_administra_el_fiado_ve_cuentas_corrientes(self):
        usuario = f.usuario('40000005', 'Operador')
        usuario.id_perfil = perfil_con('Fiado', 'gestionar_credito')
        self.assertIn('Cuentas corrientes', self.titulos(usuario))

    def test_quien_cobra_sigue_viendola(self):
        usuario = f.usuario('40000006', 'Operador')
        usuario.id_perfil = perfil_con('Cobranza', 'cobrar_credito')
        self.assertIn('Cuentas corrientes', self.titulos(usuario))

    def test_quien_no_tiene_nada_de_eso_no_las_ve(self):
        usuario = f.usuario('40000007', 'Operador')
        usuario.id_perfil = perfil_con('Sólo mirar', 'ver_ventas')
        titulos = self.titulos(usuario)
        self.assertNotIn('Cuentas corrientes', titulos)
        self.assertNotIn('Pagos y gastos', titulos)


# ===========================================================================
class ImagenesExternasTest(TestCase):
    """DD-109: la foto que valida ``stock/imagenes.py`` viene de Open Food
    Facts, que la sirve desde varios dominios y los cambia. Sin 'https:' en
    img-src la ficha del producto quedaba con el recuadro roto.

    Lo que NO se afloja: script-src sigue siendo 'self' a secas.
    """

    def test_img_src_acepta_https(self):
        from django.conf import settings

        self.assertIn('https:', settings.CAFETO_CSP['img-src'])
        for esperado in ("'self'", 'data:', 'blob:'):
            self.assertIn(esperado, settings.CAFETO_CSP['img-src'])

    def test_script_src_sigue_siendo_self_a_secas(self):
        from django.conf import settings

        self.assertEqual(settings.CAFETO_CSP['script-src'], ["'self'"])
        for directiva in ('script-src', 'default-src', 'object-src', 'frame-ancestors'):
            self.assertNotIn('https:', settings.CAFETO_CSP[directiva], directiva)

    def test_la_cabecera_que_sale_lo_dice(self):
        f.entrar(self.client, f.titular())
        cabecera = self.client.get(reverse('home'))['Content-Security-Policy']
        self.assertIn('img-src', cabecera)
        directiva = next(p for p in cabecera.split(';') if p.strip().startswith('img-src'))
        self.assertIn('https:', directiva)
        self.assertIn("script-src 'self';", cabecera + ';')


# ===========================================================================
class DniConPuntosTest(TestCase):
    """El DNI se copia de un documento y viene con puntos: "40.125.874" y
    "40125874" son el mismo (paridad con ClienteForm, DD-48)."""

    def setUp(self):
        f.entrar(self.client, f.titular())
        self.perfil = perfil_con('Repositor', 'ver_ventas')

    def alta(self, dni):
        return self.client.post(reverse('seguridad:crear'), {
            'dni_usuario': dni, 'apellido': 'Gómez', 'nombre': 'Rita',
            'correo': 'rita@cafeto.test', 'id_perfil': self.perfil.pk}, follow=True)

    def test_el_dni_con_puntos_se_limpia(self):
        self.alta('40.125.874')
        self.assertTrue(Usuario.objects.filter(dni_usuario='40125874').exists())

    def test_y_el_repetido_se_detecta_aunque_venga_con_puntos(self):
        self.alta('40125874')
        respuesta = self.client.post(reverse('seguridad:crear'), {
            'dni_usuario': '40.125.874', 'apellido': 'Pérez', 'nombre': 'Ema',
            'correo': 'ema@cafeto.test', 'id_perfil': self.perfil.pk})
        self.assertContains(respuesta, 'Ya existe un usuario con ese DNI')
        self.assertEqual(Usuario.objects.filter(dni_usuario='40125874').count(), 1)

    def test_con_letras_lo_rechaza_diciendo_por_que(self):
        respuesta = self.alta('40abc874')
        self.assertContains(respuesta, 'sin letras')

    def test_el_navegador_no_corta_lo_que_se_pega(self):
        """El campo no lleva maxlength ni pattern: con maxlength=8 (el del
        VARCHAR) el navegador cortaba «40.125.874» en el caracter 8 y el
        formateador terminaba dejando 401258, otro DNI, sin avisar nada."""
        from seguridad.forms import UsuarioAltaForm

        campo = UsuarioAltaForm().fields['dni_usuario']
        self.assertIsNone(campo.max_length)
        self.assertNotIn('maxlength', campo.widget.attrs)
        self.assertNotIn('pattern', campo.widget.attrs)
        html = self.client.get(reverse('seguridad:lista')).content.decode()
        control = html[html.index('id="id_dni_usuario"') - 400:
                       html.index('id="id_dni_usuario"') + 200]
        self.assertIn('data-formato="dni"', control)
        self.assertNotIn('maxlength', control)

    def test_un_dni_de_mas_de_ocho_digitos_se_rechaza_igual(self):
        respuesta = self.alta('401258741')
        self.assertContains(respuesta, '7 u 8 dígitos')
        self.assertFalse(Usuario.objects.filter(dni_usuario='401258741').exists())
