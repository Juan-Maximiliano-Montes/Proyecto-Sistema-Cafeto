"""Pruebas del módulo de Proveedores (Hito 2).

Se corren con:  python manage.py test proveedores

Los perfiles y el permiso 'ver_proveedores' ya vienen de la migración de
datos de usuarios (0002_datos_iniciales_v2), y la provincia/localidades de
la migración de datos de proveedores. Acá sólo buscamos lo que necesitamos.
"""
from django.test import TestCase
from django.urls import reverse

from usuarios.models import Perfil, PerfilPermiso, Permiso, Usuario

from .models import Localidad, Proveedor, normalizar_telefono


def crear_usuario(dni, apellido, nombre, perfil):
    u = Usuario(dni_usuario=dni, apellido=apellido, nombre=nombre,
                correo=f'{nombre.lower()}@cafeto.com', id_perfil=perfil)
    u.set_clave('secreto123')
    u.debe_cambiar_clave = False
    u.save()
    return u


class NormalizarTelefonoTest(TestCase):
    """El teléfono entra a la base como una cadena de DÍGITOS.

    Cambió en la Fase 4 (regla V-E): antes se guardaba "387-4123456", con
    el guion separando el código de área. Ahora se guardan sólo los
    dígitos, de 7 a 15, que es lo que admite el VARCHAR(15) del DER y lo
    que hace comparable un número cargado de cualquier manera.
    """

    def test_formatos_equivalentes_quedan_iguales(self):
        for escrito in ['(0387) 412-3456', '0387 4123456', '+54 387 4123456',
                        '387-4123456', '0387 15 4123456']:
            with self.subTest(escrito=escrito):
                self.assertEqual(normalizar_telefono(escrito), '3874123456')

    def test_vacio_queda_en_nulo(self):
        """V-G1: un texto opcional vacío se guarda NULL, no cadena vacía."""
        self.assertIsNone(normalizar_telefono(''))
        self.assertIsNone(normalizar_telefono(None))
        self.assertIsNone(normalizar_telefono('   '))

    def test_el_form_estandariza_al_guardar(self):
        from .forms import ProveedorForm
        form = ProveedorForm({
            'nombre_proveedor': 'La Salteña',
            'cuit_proveedor': '30-12345678-1',
            'telefono_proveedor': '(0387) 412-3456',
            'email_proveedor': 'ventas@lasaltena.com',
            'domicilio': 'Av. Belgrano 1234',
            'id_localidad': Localidad.objects.get(
                nombre_localidad='Salta Capital').pk,
            'condicion_pago': Proveedor.CondicionPago.CONTADO,
        })
        self.assertTrue(form.is_valid(), form.errors)
        proveedor = form.save()
        self.assertEqual(proveedor.telefono_proveedor, '3874123456')


class UbicacionesTest(TestCase):
    """Los datos semilla de Ubicaciones tienen que estar cargados."""

    def test_provincia_y_localidades_semilla(self):
        localidades = Localidad.objects.filter(
            id_provincia__nombre_provincia='Salta')
        self.assertEqual(
            sorted(localidades.values_list('nombre_localidad', flat=True)),
            ['Campo Quijano', 'Salta Capital'])


class VerProveedoresPermisoTest(TestCase):

    def setUp(self):
        self.p_admin = Perfil.objects.get(nombre='Administrador')
        self.p_cajero = Perfil.objects.get(nombre='Cajero')

        self.admin = crear_usuario('30000001', 'Jefe', 'Ana', self.p_admin)
        self.cajero = crear_usuario('40000002', 'Caja', 'Beto', self.p_cajero)

    def _login(self, identificador):
        return self.client.post(reverse('usuarios:login'),
                                {'identificador': identificador,
                                 'clave': 'secreto123'})

    def test_administrador_entra_a_proveedores(self):
        self._login('30000001')
        r = self.client.get(reverse('proveedores:lista'))
        self.assertEqual(r.status_code, 200)

    def test_cajero_es_rebotado_a_mi_perfil(self):
        """Menor privilegio: el Cajero NO administra proveedores."""
        self._login('40000002')
        r = self.client.get(reverse('proveedores:lista'))
        self.assertRedirects(r, reverse('mi_perfil'))

    def test_cajero_no_puede_crear_por_url(self):
        self._login('40000002')
        r = self.client.post(reverse('proveedores:crear'), {}, follow=True)
        # Dos saltos: 'crear_proveedor' lo manda al listado, y el listado
        # (que pide 'ver_proveedores') lo manda a su ficha personal.
        self.assertEqual(r.request['PATH_INFO'], reverse('mi_perfil'))
        self.assertEqual(Proveedor.objects.count(), 0)

    def test_permiso_explicito_habilita_al_cajero(self):
        ver = Permiso.objects.get(codigo='ver_proveedores')
        PerfilPermiso.objects.get_or_create(perfil=self.p_cajero, permiso=ver)
        self._login('40000002')
        r = self.client.get(reverse('proveedores:lista'))
        self.assertEqual(r.status_code, 200)


class PermisosGranularesTest(TestCase):
    """Un perfil con SÓLO 'ver_proveedores' entra al listado en modo lectura
    y no puede crear, editar, dar de baja ni reactivar."""

    def setUp(self):
        self.perfil = Perfil.objects.create(nombre='Consulta')
        self._asignar('ver_proveedores')
        crear_usuario('45000001', 'Mirón', 'Sara', self.perfil)
        self.client.post(reverse('usuarios:login'),
                         {'identificador': '45000001', 'clave': 'secreto123'})
        self.proveedor = Proveedor.objects.create(
            nombre_proveedor='La Salteña',
            cuit_proveedor='30-12345678-1',
        )

    def _asignar(self, *codigos):
        for codigo in codigos:
            PerfilPermiso.objects.get_or_create(
                perfil=self.perfil,
                permiso=Permiso.objects.get(codigo=codigo))

    def test_entra_al_listado(self):
        r = self.client.get(reverse('proveedores:lista'))
        self.assertEqual(r.status_code, 200)

    def test_sin_los_botones_de_accion(self):
        r = self.client.get(reverse('proveedores:lista'))
        self.assertContains(r, 'btn-ver')           # el ojo sí
        self.assertNotContains(r, 'Nuevo proveedor')
        self.assertNotContains(r, 'btn btn-sm btn-outline-primary btn-editar')
        self.assertNotContains(r, 'bi bi-trash')

    def test_no_puede_crear_por_url(self):
        r = self.client.post(reverse('proveedores:crear'), {
            'nombre_proveedor': 'Colada',
            'cuit_proveedor': '30-99999999-5',
            'condicion_pago': Proveedor.CondicionPago.CONTADO,
        })
        self.assertRedirects(r, reverse('proveedores:lista'))
        self.assertEqual(Proveedor.objects.count(), 1)   # sólo el del setUp

    def test_no_puede_editar_por_url(self):
        r = self.client.post(
            reverse('proveedores:editar', args=[self.proveedor.pk]),
            {'nombre_proveedor': 'Cambiado',
             'cuit_proveedor': '30-12345678-1',
             'condicion_pago': Proveedor.CondicionPago.CONTADO})
        self.assertRedirects(r, reverse('proveedores:lista'))
        self.proveedor.refresh_from_db()
        self.assertEqual(self.proveedor.nombre_proveedor, 'La Salteña')

    def test_no_puede_eliminar_por_url(self):
        r = self.client.post(
            reverse('proveedores:eliminar', args=[self.proveedor.pk]))
        self.assertRedirects(r, reverse('proveedores:lista'))
        self.proveedor.refresh_from_db()
        self.assertFalse(self.proveedor.borrado_logico)

    def test_no_puede_reactivar_por_url(self):
        self.proveedor.borrado_logico = True
        self.proveedor.save()
        r = self.client.post(
            reverse('proveedores:reactivar', args=[self.proveedor.pk]))
        self.assertRedirects(r, reverse('proveedores:lista'))
        self.proveedor.refresh_from_db()
        self.assertTrue(self.proveedor.borrado_logico)

    def test_con_crear_proveedor_si_puede_y_ve_el_boton(self):
        self._asignar('crear_proveedor')
        r = self.client.get(reverse('proveedores:lista'))
        self.assertContains(r, 'Nuevo proveedor')
        self.client.post(reverse('proveedores:crear'), {
            'nombre_proveedor': 'Colada',
            'cuit_proveedor': '30-99999999-5',
            'condicion_pago': Proveedor.CondicionPago.CONTADO,
        })
        self.assertTrue(
            Proveedor.objects.filter(nombre_proveedor='Colada').exists())

    def test_eliminar_y_reactivar_son_permisos_distintos(self):
        """Tener 'eliminar_proveedor' no habilita la reactivación."""
        self._asignar('eliminar_proveedor')
        self.client.post(
            reverse('proveedores:eliminar', args=[self.proveedor.pk]))
        self.proveedor.refresh_from_db()
        self.assertTrue(self.proveedor.borrado_logico)

        self.client.post(
            reverse('proveedores:reactivar', args=[self.proveedor.pk]))
        self.proveedor.refresh_from_db()
        self.assertTrue(self.proveedor.borrado_logico)   # sigue de baja


class BorradoLogicoProveedorTest(TestCase):

    def setUp(self):
        p_admin = Perfil.objects.get(nombre='Administrador')
        crear_usuario('30000001', 'Jefe', 'Ana', p_admin)
        self.client.post(reverse('usuarios:login'),
                         {'identificador': '30000001', 'clave': 'secreto123'})
        self.proveedor = Proveedor.objects.create(
            nombre_proveedor='La Salteña',
            cuit_proveedor='30-12345678-1',
            id_localidad=Localidad.objects.get(nombre_localidad='Salta Capital'),
        )

    def test_la_baja_no_borra_la_fila_y_sella_al_responsable(self):
        self.client.post(reverse('proveedores:eliminar',
                                 args=[self.proveedor.pk]))
        self.proveedor.refresh_from_db()
        self.assertTrue(self.proveedor.borrado_logico)
        self.assertEqual(self.proveedor.usuario_modificacion.dni_usuario,
                         '30000001')
        self.assertTrue(Proveedor.objects.filter(pk=self.proveedor.pk).exists())

    def test_el_listado_por_defecto_oculta_los_borrados(self):
        self.proveedor.borrado_logico = True
        self.proveedor.save()
        r = self.client.get(reverse('proveedores:lista'))
        self.assertNotContains(r, 'La Salteña')
        # Con el filtro de estado en "inactivos" sí aparece.
        r = self.client.get(reverse('proveedores:lista'),
                            {'estado': 'inactivos'})
        self.assertContains(r, 'La Salteña')

    def test_reactivar_vuelve_a_mostrarlo(self):
        self.proveedor.borrado_logico = True
        self.proveedor.save()
        self.client.post(reverse('proveedores:reactivar',
                                 args=[self.proveedor.pk]))
        self.proveedor.refresh_from_db()
        self.assertFalse(self.proveedor.borrado_logico)
