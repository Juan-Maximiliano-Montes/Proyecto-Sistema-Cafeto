"""Pruebas del módulo Seguridad. Se corren con:  python manage.py test seguridad

Todo está escrito contra el DER de alto refinamiento:
PK propia (``id_usuario``), DNI como texto único (``dni_usuario``),
nombre de usuario autogenerado (``nombre_usuario``) y borrado lógico
invertido (``borrado_logico``: False = vigente, True = eliminado).
"""
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from seguridad.models import Perfil, PerfilPermiso, Permiso, Usuario


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

    def _entrar(self, dni, clave='secreto123'):
        self.client.post(reverse('seguridad:login'),
                         {'identificador': dni, 'clave': clave})

    def test_la_vista_no_da_de_baja_al_titular(self):
        """Otro administrador no puede sacar del medio al dueño."""
        crear_usuario('31000000', 'Suarez', 'Mara', self.p_admin,
                      debe_cambiar_clave=False)
        self._entrar('31000000')
        respuesta = self.client.post(
            reverse('seguridad:eliminar', args=[self.admin_semilla.pk]),
            follow=True)
        self.admin_semilla.refresh_from_db()
        self.assertTrue(self.admin_semilla.activo)
        textos = ' '.join(m.message for m in respuesta.context['messages'])
        self.assertIn('titular del sistema no se puede dar de baja', textos)

    def test_nadie_se_da_de_baja_a_si_mismo(self):
        mara = crear_usuario('31000000', 'Suarez', 'Mara', self.p_admin,
                             debe_cambiar_clave=False)
        self._entrar('31000000')
        respuesta = self.client.post(
            reverse('seguridad:eliminar', args=[mara.pk]), follow=True)
        mara.refresh_from_db()
        self.assertTrue(mara.activo)
        textos = ' '.join(m.message for m in respuesta.context['messages'])
        self.assertIn('No podés darte de baja a vos mismo', textos)

    def test_el_listado_esconde_el_tacho_del_titular(self):
        self.admin_semilla.debe_cambiar_clave = False
        self.admin_semilla.save()
        self._entrar('30000000', 'cafeto2025')
        respuesta = self.client.get(reverse('seguridad:lista'))
        self.assertContains(respuesta, 'El titular del sistema no se da de baja')
        # El tacho rojo (bi-trash) no se dibuja para esa fila.
        self.assertNotContains(respuesta, 'bi bi-trash')


class InterceptorLoginTest(TestCase):
    def setUp(self):
        self.perfil = Perfil.objects.create(nombre='PerfilPrueba')
        self.u = crear_usuario('12345678', 'Pérez', 'Juan', self.perfil)

    def test_login_con_clave_provisoria_redirige_a_cambio(self):
        c = Client()
        r = c.post(reverse('seguridad:login'),
                   {'identificador': '12345678', 'clave': 'secreto123'})
        self.assertRedirects(r, reverse('seguridad:cambiar_clave'))

    def test_login_acepta_el_nombre_de_usuario(self):
        """DNI o nombre de usuario, indistintamente."""
        c = Client()
        r = c.post(reverse('seguridad:login'),
                   {'identificador': 'perezj', 'clave': 'secreto123'})
        self.assertRedirects(r, reverse('seguridad:cambiar_clave'))

    def test_usuario_con_borrado_logico_no_entra(self):
        self.u.dar_de_baja()
        c = Client()
        r = c.post(reverse('seguridad:login'),
                   {'identificador': '12345678', 'clave': 'secreto123'},
                   follow=True)
        textos = [m.message for m in r.context['messages']]
        self.assertTrue(any('dado de baja' in t for t in textos))

    def test_sin_sesion_redirige_a_login(self):
        c = Client()
        r = c.get(reverse('seguridad:lista'))
        self.assertRedirects(r, reverse('seguridad:login'))


class VerUsuariosPermisoTest(TestCase):
    """Directorio general restringido: sólo entra quien tiene 'ver_usuarios'
    (o el perfil Administrador).

    Los perfiles y permisos base ya vienen de la migración de datos
    (0002_datos_iniciales_v2), así que acá sólo los buscamos.
    """

    def setUp(self):
        self.p_admin = Perfil.objects.get(nombre='Administrador')
        self.p_cajero = Perfil.objects.get(nombre='Operador')

        self.admin = crear_usuario('30000001', 'Jefe', 'Ana', self.p_admin,
                                   debe_cambiar_clave=False)
        self.cajero = crear_usuario('40000002', 'Caja', 'Beto', self.p_cajero,
                                    debe_cambiar_clave=False)

    def _login(self, identificador):
        return self.client.post(reverse('seguridad:login'),
                                {'identificador': identificador,
                                 'clave': 'secreto123'})

    def test_administrador_ve_el_directorio(self):
        self._login('30000001')
        r = self.client.get(reverse('seguridad:lista'))
        self.assertEqual(r.status_code, 200)

    def test_cajero_es_rebotado_a_mi_perfil(self):
        self._login('40000002')
        r = self.client.get(reverse('seguridad:lista'))
        self.assertRedirects(r, reverse('mi_perfil'))

    def test_mi_perfil_muestra_los_datos_del_logueado(self):
        self._login('40000002')
        r = self.client.get(reverse('mi_perfil'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Caja')
        self.assertContains(r, self.cajero.nombre_usuario)

    def test_ver_usuarios_no_se_le_puede_dar_a_otro_perfil(self):
        """Los permisos de Seguridad son exclusivos del administrador."""
        ver = Permiso.objects.get(codigo='ver_usuarios')
        with self.assertRaises(ValidationError):
            PerfilPermiso.objects.create(perfil=self.p_cajero, permiso=ver)

    def test_ni_colando_la_fila_por_fuera_del_modelo(self):
        """Defensa en profundidad: aunque la fila esté en la base (alguien
        la insertó por SQL), el permiso no habilita a un perfil común."""
        ver = Permiso.objects.get(codigo='ver_usuarios')
        PerfilPermiso.objects.bulk_create(
            [PerfilPermiso(perfil=self.p_cajero, permiso=ver)])
        self.assertFalse(self.cajero.tiene_permiso('ver_usuarios'))
        self._login('40000002')
        r = self.client.get(reverse('seguridad:lista'))
        self.assertRedirects(r, reverse('mi_perfil'))


class PermisosDeSeguridadExclusivosTest(TestCase):
    """Un perfil común no recibe permisos de Seguridad por ningún camino.

    Si pudiera, quien tuviera "editar_perfil" se daría a sí mismo todo lo
    demás: la escalera de privilegios empieza ahí.
    """

    def setUp(self):
        self.perfil = Perfil.objects.create(nombre='Consulta')
        self.perfil.permisos.add(Permiso.objects.get(codigo='ver_rubros'))
        admin = Usuario.objects.get(dni_usuario='30000000')
        admin.debe_cambiar_clave = False
        admin.save()
        self.client.post(reverse('seguridad:login'),
                         {'identificador': '30000000', 'clave': 'cafeto2025'})

    def test_el_modelo_rechaza_cada_permiso_de_seguridad(self):
        for permiso in Permiso.objects.filter(modulo='SEGURIDAD'):
            with self.subTest(codigo=permiso.codigo):
                with self.assertRaises(ValidationError):
                    PerfilPermiso.objects.create(perfil=self.perfil,
                                                 permiso=permiso)

    def test_el_formulario_ni_los_ofrece(self):
        from seguridad.forms import PerfilForm
        ofrecidos = PerfilForm(instance=self.perfil).fields['permisos'].queryset
        self.assertFalse(ofrecidos.filter(modulo='SEGURIDAD').exists())
        self.assertTrue(ofrecidos.filter(codigo='ver_rubros').exists())

    def test_un_post_forzado_no_los_asigna(self):
        ids = list(Permiso.objects.filter(
            codigo__in=('ver_rubros', 'editar_perfil'))
            .values_list('id_permiso', flat=True))
        self.client.post(
            reverse('seguridad:editar_perfil', args=[self.perfil.pk]),
            {'nombre': 'Consulta', 'descripcion': '',
             'tope_descuento_porcentaje': '0', 'permisos': ids})
        self.assertFalse(self.perfil.permisos.filter(
            codigo='editar_perfil').exists())

    def test_el_administrador_conserva_todos(self):
        admin = Perfil.objects.get(nombre='Administrador')
        self.assertEqual(
            admin.permisos.filter(modulo='SEGURIDAD').count(),
            Permiso.objects.filter(modulo='SEGURIDAD').count())


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
        self.client.post(reverse('seguridad:login'),
                         {'identificador': '45000001', 'clave': 'secreto123'})

    def test_detalle_sin_ver_usuarios_da_403(self):
        r = self.client.get(reverse('seguridad:detalle', args=[self.otro.pk]))
        self.assertEqual(r.status_code, 403)
        self.assertIn('error', r.json())

    def test_verificar_dni_sin_crear_usuario_da_403(self):
        r = self.client.get(reverse('seguridad:verificar_dni'),
                            {'dni': '46000002'})
        self.assertEqual(r.status_code, 403)

    def test_el_administrador_recibe_la_respuesta_normal(self):
        self.client.logout()
        admin = Usuario.objects.get(dni_usuario='30000000')
        admin.debe_cambiar_clave = False
        admin.save()
        self.client.post(reverse('seguridad:login'),
                         {'identificador': '30000000', 'clave': 'cafeto2025'})

        r = self.client.get(reverse('seguridad:detalle', args=[self.otro.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['dni_usuario'], '46000002')

        r = self.client.get(reverse('seguridad:verificar_dni'),
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
        # Cambio de perfil permanente y perfil temporal (reemplazos).
        'cambiar_perfil_usuario', 'asignar_perfil_temporal',
    }

    # Los procesos (vender, contar, aprobar...): los pide cada vista de
    # proceso con su @requiere_permiso.
    CODIGOS_DE_PROCESO = {
        'vender', 'abrir_caja', 'cerrar_caja', 'supervisar_cierre',
        'mover_efectivo', 'registrar_cambio', 'autorizar_devolucion',
        'anular_venta', 'ver_ventas', 'ver_caja', 'ver_stock', 'contar_stock',
        'registrar_merma', 'registrar_traspaso', 'autorizar_ajustes',
        'registrar_produccion', 'imprimir_ofertas', 'actualizar_precios',
        'importar_productos', 'ver_compras', 'registrar_compra',
        'gestionar_ordenes', 'ver_egresos', 'registrar_egreso',
        'registrar_gasto_caja', 'conciliar_egresos', 'importar_proveedores',
        'ver_reportes', 'editar_parametros',
        # DD-91: el fiado. Cobrar lo tienen los tres perfiles; decidir, la Administración.
        'cobrar_credito', 'gestionar_credito',
    }

    # Los CRUD de "Mantener datos maestros" (DFD 10). Los códigos NO se
    # repiten a mano: se le preguntan al REGISTRO de ``nucleo/crud.py``,
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
        from nucleo.crud import REGISTRO  # lo llena el ready() de cada app

        codigos = set()
        for configuracion in REGISTRO.values():
            codigos.update({configuracion.permiso('ver'),
                            configuracion.permiso('crear'),
                            configuracion.permiso('editar'),
                            configuracion.permiso('exportar')})
            if configuracion.tiene_baja:
                codigos.update({configuracion.permiso('eliminar'),
                                configuracion.permiso('reactivar')})
        return codigos

    @property
    def TODOS_LOS_CODIGOS(self):
        return (self.CODIGOS_ESPERADOS | self.CODIGOS_DE_DATOS_MAESTROS
                | self.CODIGOS_DE_PROCESO)

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

    def test_los_perfiles_operativos_arrancan_con_lo_justo(self):
        """Menor privilegio: el Operador vende y carga; la Encargada además
        autoriza. Ninguno tiene permisos de Seguridad."""
        operador = Perfil.objects.get(nombre='Operador')
        encargado = Perfil.objects.get(nombre='Encargado')
        for perfil in (operador, encargado):
            with self.subTest(perfil=perfil.nombre):
                self.assertFalse(perfil.permisos.filter(
                    modulo='SEGURIDAD').exists())
                self.assertTrue(perfil.permisos.filter(
                    codigo='vender').exists())
        self.assertFalse(operador.permisos.filter(
            codigo='autorizar_ajustes').exists())
        self.assertTrue(encargado.permisos.filter(
            codigo='autorizar_ajustes').exists())

    def test_los_de_seguridad_son_los_de_usuarios_perfiles_y_auditoria(self):
        self.assertEqual(
            set(Permiso.objects.filter(modulo='SEGURIDAD')
                .values_list('codigo', flat=True)),
            self.CODIGOS_ESPERADOS - {
                'ver_proveedores', 'crear_proveedor', 'editar_proveedor',
                'eliminar_proveedor', 'reactivar_proveedor'})


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
        self.client.post(reverse('seguridad:login'),
                         {'identificador': '30000000', 'clave': 'cafeto2025'})

    def test_el_listado_sigue_funcionando(self):
        # Paginado: se filtra por el módulo para que el código esté a la vista.
        r = self.client.get(reverse('seguridad:permisos'), {'modulo': 'SEGURIDAD'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ver_usuarios')
        self.assertContains(r, 'btn-detalle')       # el ojo queda

    def test_no_hay_boton_ni_modal_de_nuevo_permiso(self):
        r = self.client.get(reverse('seguridad:permisos'))
        self.assertNotContains(r, 'Nuevo permiso')
        self.assertNotContains(r, 'id="modalCrear"')
        self.assertNotContains(r, 'id="modalEditar"')

    def test_no_hay_botones_de_editar_ni_eliminar(self):
        r = self.client.get(reverse('seguridad:permisos'))
        self.assertNotContains(r, 'btn btn-sm btn-outline-primary btn-editar')
        self.assertNotContains(r, 'bi bi-trash')

    def test_apunta_a_perfiles_para_asignar(self):
        """La pantalla tiene que dar la salida hacia donde se asignan los
        permisos. Alcanza con el botón del encabezado: sin texto explicativo
        adicional, que ensuciaba el panel."""
        r = self.client.get(reverse('seguridad:permisos'))
        self.assertContains(r, reverse('seguridad:perfiles'))
        self.assertContains(r, 'Asignar a perfiles')

    def test_las_urls_de_escritura_ya_no_existen(self):
        from django.urls import NoReverseMatch
        for nombre in ('crear_permiso', 'editar_permiso', 'eliminar_permiso'):
            with self.subTest(nombre=nombre):
                with self.assertRaises(NoReverseMatch):
                    reverse(f'seguridad:{nombre}')

    def test_el_post_directo_a_las_urls_viejas_da_404(self):
        for ruta in ('/permisos/crear/',
                     '/permisos/1/editar/',
                     '/permisos/1/baja/'):
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
        self.client.post(reverse('seguridad:login'),
                         {'identificador': '30000000', 'clave': 'cafeto2025'})
        self.cajero = Perfil.objects.get(nombre='Operador')

    def _guardar_permisos(self, *codigos):
        ids = list(Permiso.objects.filter(codigo__in=codigos)
                   .values_list('id_permiso', flat=True))
        return self.client.post(
            reverse('seguridad:editar_perfil', args=[self.cajero.pk]),
            {'nombre': self.cajero.nombre,
             'descripcion': self.cajero.descripcion,
             'tope_descuento_porcentaje': '10',
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
            reverse('seguridad:editar_perfil', args=[self.cajero.pk]),
            {'nombre': self.cajero.nombre,
             'descripcion': self.cajero.descripcion,
             'tope_descuento_porcentaje': '10'},
            follow=True)

        # El permiso sigue ahí y el usuario recibió la explicación.
        self.assertEqual(self.cajero.permisos.count(), 1)
        self.assertContains(respuesta, 'quedaría sin ningún permiso')

    def test_el_tilde_ofrece_el_catalogo_completo_agrupado(self):
        r = self.client.get(reverse('seguridad:perfiles'))
        self.assertEqual(r.status_code, 200)
        for codigo in Permiso.objects.values_list('codigo', flat=True):
            self.assertContains(r, f'value="{Permiso.objects.get(codigo=codigo).pk}"')
        # Agrupado por módulo, no en una lista plana
        self.assertContains(r, 'Compras y proveedores')
        self.assertContains(r, 'Seguridad y acceso')
        self.assertContains(r, 'solo administrador')
