"""Pruebas mínimas del Hito 2. Se corren con:  python manage.py test usuarios

Todo está escrito contra el DER de alto refinamiento:
PK propia (``id_usuario``), DNI como texto único (``dni_usuario``),
nombre de usuario autogenerado (``nombre_usuario``) y borrado lógico
invertido (``borrado_logico``: False = vigente, True = eliminado).
"""
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from .models import Perfil, PerfilPermiso, Permiso, Usuario


def crear_usuario(dni, apellido, nombre, perfil, clave='secreto123',
                  debe_cambiar_clave=True):
    """Atajo para armar usuarios de prueba con la clave ya hasheada."""
    u = Usuario(dni_usuario=dni, apellido=apellido, nombre=nombre,
                correo=f'{nombre.lower()}@cafeto.com', id_perfil=perfil)
    u.set_clave(clave)
    u.debe_cambiar_clave = debe_cambiar_clave
    u.save()
    return u


class UsuarioModelTest(TestCase):
    def setUp(self):
        self.perfil = Perfil.objects.create(nombre='PerfilPrueba')

    def test_nombre_usuario_autogenerado(self):
        u = crear_usuario('12345678', 'Pérez', 'Juan', self.perfil)
        self.assertEqual(u.nombre_usuario, 'perezj')

    def test_clave_se_guarda_hasheada(self):
        u = crear_usuario('12345678', 'Pérez', 'Juan', self.perfil)
        self.assertNotEqual(u.clave, 'secreto123')
        self.assertTrue(u.verificar_clave('secreto123'))

    def test_baja_logica(self):
        u = crear_usuario('12345678', 'Pérez', 'Juan', self.perfil)
        u.dar_de_baja()
        # borrado_logico = True significa "eliminado", pero la fila sigue ahí.
        self.assertTrue(u.borrado_logico)
        self.assertIsNotNone(u.fecha_baja)
        self.assertTrue(Usuario.objects.filter(dni_usuario='12345678').exists())

    def test_nombre_y_apellido_solo_letras(self):
        u = Usuario(dni_usuario='12345678', apellido='Pérez3', nombre='Juan',
                    correo='j@c.com', id_perfil=self.perfil)
        with self.assertRaises(ValidationError):
            u.full_clean()


class UltimoAdministradorTest(TestCase):
    """Regla de negocio: el sistema debe conservar siempre un Administrador
    vigente. La baja lógica del último se bloquea en el modelo y en la vista."""

    def setUp(self):
        self.p_admin = Perfil.objects.get(nombre='Administrador')
        # El admin semilla de la migración de datos ya existe: ese es "el único".
        self.admin_semilla = Usuario.objects.get(dni_usuario='30000000')

    def test_no_se_puede_dar_de_baja_al_unico_administrador(self):
        self.assertTrue(self.admin_semilla.es_ultimo_administrador)
        with self.assertRaises(ValidationError):
            self.admin_semilla.dar_de_baja()
        self.admin_semilla.refresh_from_db()
        self.assertFalse(self.admin_semilla.borrado_logico)

    def test_con_dos_administradores_la_baja_se_permite(self):
        otro = crear_usuario('31000000', 'Suarez', 'Mara', self.p_admin)
        self.assertFalse(otro.es_ultimo_administrador)
        otro.dar_de_baja()
        self.assertTrue(otro.borrado_logico)
        # Y ahora el que queda vuelve a ser "el último".
        self.admin_semilla.refresh_from_db()
        self.assertTrue(self.admin_semilla.es_ultimo_administrador)

    def test_la_vista_devuelve_el_error_y_no_borra(self):
        admin = crear_usuario('31000000', 'Suarez', 'Mara', self.p_admin,
                              debe_cambiar_clave=False)
        # Dejamos un solo administrador vigente: el semilla se da de baja.
        self.admin_semilla.dar_de_baja()

        self.client.post(reverse('usuarios:login'),
                         {'identificador': '31000000', 'clave': 'secreto123'})
        respuesta = self.client.post(
            reverse('usuarios:eliminar', args=[admin.id_usuario]), follow=True)

        admin.refresh_from_db()
        self.assertFalse(admin.borrado_logico)
        textos = [m.message for m in respuesta.context['messages']]
        self.assertTrue(any('al menos un administrador activo' in t
                            for t in textos))

    def test_el_listado_esconde_el_tacho_del_ultimo_admin(self):
        self.admin_semilla.debe_cambiar_clave = False
        self.admin_semilla.save()
        self.client.post(reverse('usuarios:login'),
                         {'identificador': '30000000', 'clave': 'cafeto2025'})
        respuesta = self.client.get(reverse('usuarios:lista'))
        self.assertContains(respuesta, 'es el único administrador activo')
        # El tacho rojo (bi-trash) no se dibuja para esa fila.
        self.assertNotContains(respuesta, 'bi bi-trash')


class InterceptorLoginTest(TestCase):
    def setUp(self):
        self.perfil = Perfil.objects.create(nombre='PerfilPrueba')
        self.u = crear_usuario('12345678', 'Pérez', 'Juan', self.perfil)

    def test_login_con_clave_provisoria_redirige_a_cambio(self):
        c = Client()
        r = c.post(reverse('usuarios:login'),
                   {'identificador': '12345678', 'clave': 'secreto123'})
        self.assertRedirects(r, reverse('usuarios:cambiar_clave'))

    def test_login_acepta_el_nombre_de_usuario(self):
        """DNI o nombre de usuario, indistintamente."""
        c = Client()
        r = c.post(reverse('usuarios:login'),
                   {'identificador': 'perezj', 'clave': 'secreto123'})
        self.assertRedirects(r, reverse('usuarios:cambiar_clave'))

    def test_usuario_con_borrado_logico_no_entra(self):
        self.u.dar_de_baja()
        c = Client()
        r = c.post(reverse('usuarios:login'),
                   {'identificador': '12345678', 'clave': 'secreto123'},
                   follow=True)
        textos = [m.message for m in r.context['messages']]
        self.assertTrue(any('dado de baja' in t for t in textos))

    def test_sin_sesion_redirige_a_login(self):
        c = Client()
        r = c.get(reverse('usuarios:lista'))
        self.assertRedirects(r, reverse('usuarios:login'))


class VerUsuariosPermisoTest(TestCase):
    """Directorio general restringido: sólo entra quien tiene 'ver_usuarios'
    (o el perfil Administrador).

    Los perfiles y permisos base ya vienen de la migración de datos
    (0002_datos_iniciales_v2), así que acá sólo los buscamos.
    """

    def setUp(self):
        self.p_admin = Perfil.objects.get(nombre='Administrador')
        self.p_cajero = Perfil.objects.get(nombre='Cajero')

        self.admin = crear_usuario('30000001', 'Jefe', 'Ana', self.p_admin,
                                   debe_cambiar_clave=False)
        self.cajero = crear_usuario('40000002', 'Caja', 'Beto', self.p_cajero,
                                    debe_cambiar_clave=False)

    def _login(self, identificador):
        return self.client.post(reverse('usuarios:login'),
                                {'identificador': identificador,
                                 'clave': 'secreto123'})

    def test_administrador_ve_el_directorio(self):
        self._login('30000001')
        r = self.client.get(reverse('usuarios:lista'))
        self.assertEqual(r.status_code, 200)

    def test_cajero_es_rebotado_a_mi_perfil(self):
        self._login('40000002')
        r = self.client.get(reverse('usuarios:lista'))
        self.assertRedirects(r, reverse('mi_perfil'))

    def test_mi_perfil_muestra_los_datos_del_logueado(self):
        self._login('40000002')
        r = self.client.get(reverse('mi_perfil'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Caja')
        self.assertContains(r, self.cajero.nombre_usuario)

    def test_permiso_explicito_habilita_el_directorio(self):
        ver = Permiso.objects.get(codigo='ver_usuarios')
        PerfilPermiso.objects.get_or_create(perfil=self.p_cajero, permiso=ver)
        self._login('40000002')
        r = self.client.get(reverse('usuarios:lista'))
        self.assertEqual(r.status_code, 200)


class PermisosGranularesTest(TestCase):
    """Cada acción del CRUD de usuarios exige SU permiso.

    El perfil de prueba arranca con 'ver_usuarios' y nada más: ve el
    directorio, pero ningún botón de acción se le dibuja ni le funciona
    forzando la URL.
    """

    def setUp(self):
        self.perfil = Perfil.objects.create(nombre='Consulta')
        self._asignar('ver_usuarios')
        crear_usuario('45000001', 'Mirón', 'Sara', self.perfil,
                      debe_cambiar_clave=False)
        self.victima = crear_usuario(
            '46000002', 'Blanco', 'Tito', self.perfil, debe_cambiar_clave=False)
        self.client.post(reverse('usuarios:login'),
                         {'identificador': '45000001', 'clave': 'secreto123'})

    def _asignar(self, *codigos):
        for codigo in codigos:
            PerfilPermiso.objects.get_or_create(
                perfil=self.perfil,
                permiso=Permiso.objects.get(codigo=codigo))

    def test_ve_el_listado_sin_botones_de_accion(self):
        r = self.client.get(reverse('usuarios:lista'))
        self.assertEqual(r.status_code, 200)
        # El ojo sí: entrar acá ya exigió 'ver_usuarios'.
        self.assertContains(r, 'btn-detalle')
        # Buscamos el markup completo de cada botón: las clases sueltas
        # aparecen también en los handlers de jQuery del bloque de scripts.
        self.assertNotContains(r, 'Nuevo usuario')
        self.assertNotContains(r, 'btn btn-sm btn-outline-primary btn-editar')
        self.assertNotContains(r, 'btn btn-sm btn-outline-warning btn-restablecer')
        self.assertNotContains(r, 'bi bi-trash')

    def test_no_puede_dar_de_baja_por_url(self):
        r = self.client.post(
            reverse('usuarios:eliminar', args=[self.victima.pk]))
        self.assertRedirects(r, reverse('usuarios:lista'))
        self.victima.refresh_from_db()
        self.assertFalse(self.victima.borrado_logico)

    def test_baja_y_reactivacion_son_permisos_distintos(self):
        """'baja_usuario' ya no arrastra la reactivación."""
        self._asignar('baja_usuario')
        self.client.post(reverse('usuarios:eliminar', args=[self.victima.pk]))
        self.victima.refresh_from_db()
        self.assertTrue(self.victima.borrado_logico)

        self.client.post(reverse('usuarios:reactivar', args=[self.victima.pk]))
        self.victima.refresh_from_db()
        self.assertTrue(self.victima.borrado_logico)     # sigue de baja

        self._asignar('reactivar_usuario')
        self.client.post(reverse('usuarios:reactivar', args=[self.victima.pk]))
        self.victima.refresh_from_db()
        self.assertFalse(self.victima.borrado_logico)

    def test_el_boton_reactivar_pide_su_propio_permiso(self):
        # La clase suelta 'btn-reactivar' también aparece en el handler de
        # jQuery, así que buscamos el markup completo del botón.
        boton = 'btn btn-sm btn-outline-success btn-reactivar'
        self.victima.dar_de_baja()
        self._asignar('baja_usuario')
        r = self.client.get(reverse('usuarios:lista'), {'estado': 'todos'})
        self.assertNotContains(r, boton)

        self._asignar('reactivar_usuario')
        r = self.client.get(reverse('usuarios:lista'), {'estado': 'todos'})
        self.assertContains(r, boton)


class EndpointsAjaxProtegidosTest(TestCase):
    """Los endpoints JSON no quedan abiertos a cualquier sesión válida, y
    responden 403 con cuerpo JSON en vez de un redirect (que al script le
    llegaría como HTML)."""

    def setUp(self):
        self.perfil = Perfil.objects.create(nombre='SinPermisos')
        crear_usuario('45000001', 'Mirón', 'Sara', self.perfil,
                      debe_cambiar_clave=False)
        self.otro = crear_usuario('46000002', 'Blanco', 'Tito', self.perfil,
                                  debe_cambiar_clave=False)
        self.client.post(reverse('usuarios:login'),
                         {'identificador': '45000001', 'clave': 'secreto123'})

    def test_detalle_sin_ver_usuarios_da_403(self):
        r = self.client.get(reverse('usuarios:detalle', args=[self.otro.pk]))
        self.assertEqual(r.status_code, 403)
        self.assertIn('error', r.json())

    def test_verificar_dni_sin_crear_usuario_da_403(self):
        r = self.client.get(reverse('usuarios:verificar_dni'),
                            {'dni': '46000002'})
        self.assertEqual(r.status_code, 403)

    def test_con_el_permiso_responden_normal(self):
        for codigo in ('ver_usuarios', 'crear_usuario'):
            PerfilPermiso.objects.get_or_create(
                perfil=self.perfil,
                permiso=Permiso.objects.get(codigo=codigo))

        r = self.client.get(reverse('usuarios:detalle', args=[self.otro.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['dni_usuario'], '46000002')

        r = self.client.get(reverse('usuarios:verificar_dni'),
                            {'dni': '46000002'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['existe'])


class CatalogoDePermisosTest(TestCase):
    """El catálogo semilla y los decoradores tienen que hablar el mismo
    idioma: si una vista pide un código que no existe en la tabla, nadie
    salvo el Administrador podría entrar nunca."""

    # El catálogo NO tiene crear_permiso / editar_permiso /
    # eliminar_permiso: el panel de Permisos es de sólo lectura.
    CODIGOS_ESPERADOS = {
        'ver_usuarios', 'crear_usuario', 'editar_usuario', 'baja_usuario',
        'reactivar_usuario', 'restablecer_clave',
        'ver_perfiles', 'crear_perfil', 'editar_perfil', 'eliminar_perfil',
        'reactivar_perfil',
        'ver_permisos',
        'ver_proveedores', 'crear_proveedor', 'editar_proveedor',
        'eliminar_proveedor', 'reactivar_proveedor',
        # Del Registro de Actividad hay UNO solo y no es un olvido:
        # LOG_AUDITORIA es un libro append-only, así que crear, editar,
        # eliminar y reactivar no existen (DD-60).
        'ver_auditoria',
    }

    # Los CRUD de "Mantener datos maestros" (DFD 10). Los códigos NO se
    # repiten a mano: se le preguntan al REGISTRO de ``maestros/crud.py``,
    # que es el que los consume. Cada pantalla declara su
    # ``ConfiguracionCRUD`` y el decorador ``exige()`` pide
    # ``cfg.permiso(accion)``; si esos códigos no están sembrados, nadie
    # salvo el perfil de sistema puede entrar.
    #
    # Preguntarle al registro y no a una lista escrita al lado tiene dos
    # ventajas: si mañana se agrega una entidad, el test la exige solo, y
    # lo que se compara es la semilla contra lo que el código REALMENTE
    # pide, no contra otra copia de la misma lista.
    @property
    def CODIGOS_DE_DATOS_MAESTROS(self):
        import maestros.views      # noqa: F401  (llena el REGISTRO)
        import proveedores.views   # noqa: F401
        from maestros.crud import REGISTRO

        codigos = set()
        for configuracion in REGISTRO.values():
            codigos.update({configuracion.permiso('ver'),
                            configuracion.permiso('crear'),
                            configuracion.permiso('editar')})
            if configuracion.tiene_baja:
                codigos.update({configuracion.permiso('eliminar'),
                                configuracion.permiso('reactivar')})
        return codigos

    @property
    def TODOS_LOS_CODIGOS(self):
        return self.CODIGOS_ESPERADOS | self.CODIGOS_DE_DATOS_MAESTROS

    def test_estan_todos_los_permisos_granulares(self):
        self.assertEqual(
            set(Permiso.objects.values_list('codigo', flat=True)),
            self.TODOS_LOS_CODIGOS)

    def test_todos_tienen_modulo_asignado(self):
        self.assertFalse(
            Permiso.objects.filter(modulo='').exists(),
            'hay permisos sin módulo: no se agruparían en la pantalla')

    def test_el_administrador_los_tiene_todos(self):
        admin = Perfil.objects.get(nombre='Administrador')
        self.assertEqual(
            set(admin.permisos.values_list('codigo', flat=True)),
            self.TODOS_LOS_CODIGOS)

    def test_el_cajero_no_tiene_ninguno(self):
        """Menor privilegio: el Cajero arranca en cero y se le tilda a mano
        lo que necesite desde la pantalla de Perfiles."""
        cajero = Perfil.objects.get(nombre='Cajero')
        self.assertEqual(cajero.permisos.count(), 0)


class PanelDePermisosSoloLecturaTest(TestCase):
    """El catálogo de permisos no se administra desde la pantalla.

    Los códigos existen porque una vista los exige con @requiere_permiso:
    crear uno desde el navegador daría una fila que nadie consulta, y
    renombrarlo dejaría al decorador pidiendo algo que no está en la tabla.
    """

    def setUp(self):
        self.admin = Usuario.objects.get(dni_usuario='30000000')
        self.admin.debe_cambiar_clave = False
        self.admin.save()
        self.client.post(reverse('usuarios:login'),
                         {'identificador': '30000000', 'clave': 'cafeto2025'})

    def test_el_listado_sigue_funcionando(self):
        r = self.client.get(reverse('usuarios:permisos'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ver_usuarios')
        self.assertContains(r, 'btn-detalle')       # el ojo queda

    def test_no_hay_boton_ni_modal_de_nuevo_permiso(self):
        r = self.client.get(reverse('usuarios:permisos'))
        self.assertNotContains(r, 'Nuevo permiso')
        self.assertNotContains(r, 'id="modalCrear"')
        self.assertNotContains(r, 'id="modalEditar"')

    def test_no_hay_botones_de_editar_ni_eliminar(self):
        r = self.client.get(reverse('usuarios:permisos'))
        self.assertNotContains(r, 'btn btn-sm btn-outline-primary btn-editar')
        self.assertNotContains(r, 'bi bi-trash')

    def test_apunta_a_perfiles_para_asignar(self):
        """La pantalla tiene que dar la salida hacia donde se asignan los
        permisos. Alcanza con el botón del encabezado: sin texto explicativo
        adicional, que ensuciaba el panel."""
        r = self.client.get(reverse('usuarios:permisos'))
        self.assertContains(r, reverse('usuarios:perfiles'))
        self.assertContains(r, 'Asignar a perfiles')

    def test_las_urls_de_escritura_ya_no_existen(self):
        from django.urls import NoReverseMatch
        for nombre in ('crear_permiso', 'editar_permiso', 'eliminar_permiso'):
            with self.subTest(nombre=nombre):
                with self.assertRaises(NoReverseMatch):
                    reverse(f'usuarios:{nombre}')

    def test_el_post_directo_a_las_urls_viejas_da_404(self):
        for ruta in ('/usuarios/permisos/crear/',
                     '/usuarios/permisos/editar/1/',
                     '/usuarios/permisos/eliminar/1/'):
            with self.subTest(ruta=ruta):
                r = self.client.post(ruta, {})
                self.assertEqual(r.status_code, 404)


class AsignarPermisosAPerfilesTest(TestCase):
    """Lo que el Administrador SÍ administra: qué permisos tiene cada perfil.

    Esa es la funcionalidad que el panel de Permisos delega en la pantalla
    de Perfiles, y es la que tiene que seguir funcionando.
    """

    def setUp(self):
        admin = Usuario.objects.get(dni_usuario='30000000')
        admin.debe_cambiar_clave = False
        admin.save()
        self.client.post(reverse('usuarios:login'),
                         {'identificador': '30000000', 'clave': 'cafeto2025'})
        self.cajero = Perfil.objects.get(nombre='Cajero')

    def _guardar_permisos(self, *codigos):
        ids = list(Permiso.objects.filter(codigo__in=codigos)
                   .values_list('id_permiso', flat=True))
        return self.client.post(
            reverse('usuarios:editar_perfil', args=[self.cajero.pk]),
            {'nombre': self.cajero.nombre,
             'descripcion': self.cajero.descripcion,
             'permisos': ids})

    def test_asignar_permisos_al_cajero(self):
        self._guardar_permisos('ver_proveedores', 'editar_proveedor')
        self.assertEqual(
            set(self.cajero.permisos.values_list('codigo', flat=True)),
            {'ver_proveedores', 'editar_proveedor'})

    def test_desasignar_deja_solo_lo_tildado(self):
        self._guardar_permisos('ver_proveedores', 'editar_proveedor')
        self._guardar_permisos('ver_proveedores')
        self.assertEqual(
            set(self.cajero.permisos.values_list('codigo', flat=True)),
            {'ver_proveedores'})

    def test_no_se_puede_dejar_un_perfil_sin_ningun_permiso(self):
        """DD-14: un perfil asignable conserva al menos un permiso.

        Cambió en la Fase 1: antes destildar todo dejaba el perfil en
        cero. Un perfil sin permisos es una trampa —se le puede asignar a
        un usuario y después esa persona no puede hacer nada— así que la
        base lo prohíbe con ``trg_perfil_conserva_permiso`` y la vista lo
        avisa antes, en castellano.
        """
        self._guardar_permisos('ver_proveedores')
        self.assertEqual(self.cajero.permisos.count(), 1)

        respuesta = self.client.post(
            reverse('usuarios:editar_perfil', args=[self.cajero.pk]),
            {'nombre': self.cajero.nombre,
             'descripcion': self.cajero.descripcion},
            follow=True)

        # El permiso sigue ahí y el usuario recibió la explicación.
        self.assertEqual(self.cajero.permisos.count(), 1)
        self.assertContains(respuesta, 'quedaría sin ningún permiso')

    def test_el_tilde_ofrece_el_catalogo_completo_agrupado(self):
        r = self.client.get(reverse('usuarios:perfiles'))
        self.assertEqual(r.status_code, 200)
        for codigo in Permiso.objects.values_list('codigo', flat=True):
            self.assertContains(r, f'value="{Permiso.objects.get(codigo=codigo).pk}"')
        # Agrupado por módulo, no en una lista plana
        self.assertContains(r, 'Proveedores')
        self.assertContains(r, 'Usuarios')
