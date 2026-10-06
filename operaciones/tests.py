"""Pruebas del Registro de Actividad (LOG_AUDITORIA en modo lectura).

Dos grupos, y el segundo importa tanto como el primero:

  * que la pantalla muestre y filtre bien;
  * que NO se pueda escribir. Una auditoría que se puede editar no sirve
    para auditar nada, así que la ausencia de rutas de escritura se prueba
    igual que se prueba una regla de negocio.
"""
import datetime

from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from maestros.models import Marca, Producto, Rubro
from maestros.tests import BaseMaestros
from operaciones.auditoria import comparar, etiqueta_de, modelo_de, valor_legible
from operaciones.models import LogAuditoria
from usuarios.models import Permiso, PerfilPermiso


class BaseActividad(BaseMaestros):
    """Un administrador logueado y unos cuantos registros de auditoría."""

    def setUp(self):
        super().setUp()
        self.url = reverse('operaciones:actividad')
        self.sesion = self.admin.sesiones.filter(activa=True).first()

    def asentar(self, operacion, modulo='DATOS_MAESTROS', anterior=None,
                nuevo=None, cuando=None, sesion=None):
        """Escribe un asiento de prueba.

        La fecha va en el INSERT y no con un UPDATE posterior: el trigger
        `trg_libro_inmutable` rechaza cualquier UPDATE sobre este libro, y
        hace bien. Que el helper del test tenga que respetarlo es otra
        prueba de que la regla está puesta donde corresponde.
        """
        datos = {
            'operacion_realizada': operacion,
            'modulo_afectado': modulo,
            'id_sesion': sesion or self.sesion,
            'registro_anterior': anterior,
            'registro_nuevo': nuevo,
        }
        if cuando is not None:
            datos['fecha_hora'] = cuando
        return LogAuditoria.objects.create(**datos)


# ===========================================================================
#  LA PANTALLA ES DE SÓLO LECTURA
# ===========================================================================
class SoloLecturaTest(BaseActividad):

    def test_no_existe_ruta_de_alta_edicion_ni_baja(self):
        """La ausencia de rutas es la garantía: lo que no se nombra, no se pide."""
        for accion in ('crear', 'editar', 'eliminar', 'baja', 'reactivar'):
            with self.assertRaises(
                    NoReverseMatch,
                    msg='No debería existir una ruta "%s" para la auditoría'
                        % accion):
                reverse('operaciones:actividad_%s' % accion)

    def test_un_post_al_listado_no_escribe_nada(self):
        antes = LogAuditoria.objects.count()
        self.client.post(self.url, {'operacion_realizada': 'INVENTADA'})
        self.assertEqual(LogAuditoria.objects.count(), antes)

    def test_la_ficha_no_trae_ningun_control_de_formulario(self):
        registro = self.asentar('ALTA DE RUBRO', nuevo={'id_rubro': 1})
        respuesta = self.client.get(
            reverse('operaciones:ficha_actividad', args=[registro.pk]))
        cuerpo = respuesta.content.decode()
        for control in ('<input', '<select', '<textarea', 'type="submit"'):
            self.assertNotIn(control, cuerpo)


# ===========================================================================
#  PERMISOS
# ===========================================================================
class PermisosActividadTest(BaseActividad):

    def test_el_administrador_entra(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_el_operador_sin_permiso_rebota(self):
        self.client.logout()
        self.entrar(self.operador)
        respuesta = self.client.get(self.url, follow=True)
        self.assertIn('Acceso denegado', ' '.join(self.mensajes(respuesta)))

    def test_el_operador_tampoco_abre_la_ficha_por_url_directa(self):
        registro = self.asentar('ALTA DE RUBRO', nuevo={'id_rubro': 1})
        self.client.logout()
        self.entrar(self.operador)
        respuesta = self.client.get(
            reverse('operaciones:ficha_actividad', args=[registro.pk]),
            follow=True)
        self.assertIn('Acceso denegado', ' '.join(self.mensajes(respuesta)))

    def test_con_el_permiso_tildado_el_operador_entra(self):
        PerfilPermiso.objects.create(
            perfil=self.perfil_operador,
            permiso=Permiso.objects.get(codigo='ver_auditoria'))
        self.client.logout()
        self.entrar(self.operador)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_el_menu_muestra_el_item_solo_a_quien_puede(self):
        from usuarios.menu import armar

        def items(usuario):
            return [i['titulo'] for g in armar(usuario) for i in g['items']]

        self.assertIn('Registro de Actividad', items(self.admin))
        self.assertNotIn('Registro de Actividad', items(self.operador))


# ===========================================================================
#  FILTROS
# ===========================================================================
class FiltrosActividadTest(BaseActividad):

    def setUp(self):
        super().setUp()
        self.ayer = timezone.now() - datetime.timedelta(days=1)
        self.hace_una_semana = timezone.now() - datetime.timedelta(days=7)
        self.asentar('ALTA DE RUBRO', cuando=self.hace_una_semana)
        self.asentar('BAJA DE PRODUCTO', cuando=self.ayer)
        self.asentar('ALTA DE USUARIO', modulo='USUARIOS')

    def filas(self, **filtros):
        return self.client.get(self.url, filtros).context['registros']

    def test_sin_filtros_trae_todo(self):
        self.assertEqual(len(self.filas()), 3)

    def test_filtra_por_modulo(self):
        filas = self.filas(modulo='USUARIOS')
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0].operacion_realizada, 'ALTA DE USUARIO')

    def test_filtra_por_usuario(self):
        self.assertEqual(len(self.filas(usuario=self.admin.pk)), 3)

    def test_un_usuario_sin_actividad_no_es_una_opcion(self):
        """El combo ofrece sólo a quienes figuran en el log.

        Por eso filtrar por el operador --que no hizo nada-- no devuelve
        "0 registros" sino un error de formulario: ese valor nunca estuvo
        entre las opciones. Un combo que ofrece cincuenta nombres de los que
        cuarenta no devuelven nada no es un filtro, es una trampa.
        """
        respuesta = self.client.get(self.url, {'usuario': self.operador.pk})
        self.assertIn('usuario', respuesta.context['formulario'].errors)

        opciones = [v for v, _ in
                    respuesta.context['formulario'].fields['usuario'].choices]
        self.assertIn(self.admin.pk, opciones)
        self.assertNotIn(self.operador.pk, opciones)

    def test_filtra_por_rango_de_fechas(self):
        desde = (self.ayer - datetime.timedelta(days=1)).date().isoformat()
        self.assertEqual(len(self.filas(desde=desde)), 2)

    def test_el_dia_de_hasta_entra_completo(self):
        """Un asiento de las 23:50 tiene que entrar si "hasta" es ese día."""
        hoy = timezone.localdate()
        self.assertEqual(len(self.filas(hasta=hoy.isoformat())), 3)

    def test_filtra_por_texto_de_la_operacion(self):
        filas = self.filas(busqueda='baja')
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0].operacion_realizada, 'BAJA DE PRODUCTO')

    def test_hasta_anterior_a_desde_es_error(self):
        respuesta = self.client.get(self.url, {
            'desde': timezone.localdate().isoformat(),
            'hasta': (timezone.localdate()
                      - datetime.timedelta(days=3)).isoformat()})
        self.assertFalse(respuesta.context['formulario'].is_valid())

    def test_una_fecha_futura_es_error(self):
        manana = timezone.localdate() + datetime.timedelta(days=1)
        respuesta = self.client.get(self.url, {'desde': manana.isoformat()})
        self.assertIn('desde', respuesta.context['formulario'].errors)

    def test_un_filtro_invalido_no_vacia_la_tabla(self):
        """Con un filtro mal cargado se muestra todo y el error al lado.

        Vaciar la tabla haría creer que no hay actividad, que es una
        conclusión distinta y falsa.
        """
        manana = timezone.localdate() + datetime.timedelta(days=1)
        respuesta = self.client.get(self.url, {'desde': manana.isoformat()})
        self.assertEqual(len(respuesta.context['registros']), 3)

    def test_los_combos_ofrecen_solo_lo_que_existe(self):
        formulario = self.client.get(self.url).context['formulario']
        modulos = [v for v, _ in formulario.fields['modulo'].choices]
        self.assertIn('USUARIOS', modulos)
        self.assertNotIn('COMPRAS', modulos)


# ===========================================================================
#  TRADUCCIÓN DE LAS FOTOS JSONB
# ===========================================================================
class TraduccionDeAuditoriaTest(BaseMaestros):

    def test_el_producto_no_se_confunde_con_la_marca(self):
        """La foto del producto trae id_marca, que es la PK de MARCAS.

        Buscando el modelo sólo por "¿está su PK entre las claves?", el
        producto se identificaba como marca y TODAS las etiquetas salían
        mal. Gana el modelo que explique más columnas.
        """
        foto = {'id_producto': 1, 'id_marca': 2, 'id_rubro': 3,
                'nombre_producto': 'Café molido', 'precio_venta': '8500.00',
                'stock_actual': 0}
        self.assertIs(modelo_de(foto), Producto)

    def test_la_clave_foranea_se_muestra_por_su_nombre(self):
        self.assertEqual(
            valor_legible(Producto, 'id_marca', self.marca.pk),
            str(self.marca))

    def test_una_fk_que_ya_no_existe_no_rompe(self):
        self.assertIn('ya no existe',
                      str(valor_legible(Producto, 'id_marca', 999999)))

    def test_la_columna_se_muestra_con_su_etiqueta(self):
        self.assertEqual(etiqueta_de(Rubro, 'nombre_rubro'), 'Rubro')

    def test_los_booleanos_y_los_nulos_se_leen(self):
        self.assertEqual(valor_legible(Rubro, 'activo', True), 'Sí')
        self.assertEqual(valor_legible(Rubro, 'activo', False), 'No')
        self.assertEqual(valor_legible(Rubro, 'descripcion_rubro', None), '—')

    def test_la_fecha_iso_se_muestra_en_formato_de_aca(self):
        self.assertEqual(
            valor_legible(Rubro, 'fecha_modificacion', '2026-10-05T17:45:00'),
            '05/10/2026 17:45')

    def test_un_alta_lo_dice(self):
        _, resumen = comparar(None, {'id_rubro': 1, 'nombre_rubro': 'Café'})
        self.assertIn('alta', resumen)

    def test_una_baja_lo_dice(self):
        _, resumen = comparar({'id_rubro': 1, 'nombre_rubro': 'Café'}, None)
        self.assertIn('eliminó', resumen)

    def test_una_modificacion_cuenta_los_campos_tocados(self):
        antes = {'id_rubro': 1, 'nombre_rubro': 'Café', 'activo': True}
        ahora = {'id_rubro': 1, 'nombre_rubro': 'Cafetería', 'activo': True}
        filas, resumen = comparar(antes, ahora)
        self.assertEqual(resumen, 'Cambió 1 campo.')
        self.assertEqual(sum(1 for f in filas if f.destacada), 1)

    def test_lo_que_cambio_va_primero(self):
        antes = {'id_rubro': 1, 'activo': True, 'nombre_rubro': 'Café'}
        ahora = {'id_rubro': 1, 'activo': True, 'nombre_rubro': 'Cafetería'}
        filas, _ = comparar(antes, ahora)
        self.assertTrue(filas[0].destacada,
                        'El campo que cambió tiene que quedar arriba')

    def test_el_sello_tecnico_no_ensucia_la_comparacion(self):
        filas, _ = comparar(
            {'id_rubro': 1, 'fecha_modificacion': '2026-01-01T00:00:00'},
            {'id_rubro': 1, 'fecha_modificacion': '2026-10-05T00:00:00'})
        etiquetas = [f.etiqueta for f in filas]
        self.assertNotIn('Fecha de modificación', etiquetas)


# ===========================================================================
#  LA PANTALLA, DE PUNTA A PUNTA
# ===========================================================================
class PantallaActividadTest(BaseActividad):

    def test_el_titulo_es_el_que_ve_la_duena(self):
        respuesta = self.client.get(self.url)
        self.assertContains(respuesta, 'Registro de Actividad')
        self.assertNotContains(respuesta, 'LOG_AUDITORIA')

    def test_la_tabla_trae_las_cinco_columnas_pedidas(self):
        self.asentar('ALTA DE RUBRO', nuevo={'id_rubro': 1})
        respuesta = self.client.get(self.url)
        for columna in ('Fecha / Hora', 'Usuario', 'Operación',
                        'Módulo afectado', 'Acciones'):
            self.assertContains(respuesta, columna)

    def test_cada_fila_trae_el_boton_del_ojo(self):
        self.asentar('ALTA DE RUBRO', nuevo={'id_rubro': 1})
        respuesta = self.client.get(self.url)
        self.assertContains(respuesta, 'boton-ver-actividad')
        self.assertContains(respuesta, 'bi-eye')

    def test_la_ficha_muestra_el_cambio_en_castellano(self):
        rubro = Rubro.objects.create(nombre_rubro='Panadería')
        registro = self.asentar(
            'MODIFICACION DE RUBRO',
            anterior={'id_rubro': rubro.pk, 'nombre_rubro': 'Panadería'},
            nuevo={'id_rubro': rubro.pk, 'nombre_rubro': 'Pastelería'})
        respuesta = self.client.get(
            reverse('operaciones:ficha_actividad', args=[registro.pk]))
        self.assertContains(respuesta, 'Cambió 1 campo')
        self.assertContains(respuesta, 'Panadería')
        self.assertContains(respuesta, 'Pastelería')
        # La etiqueta, no el nombre de la columna.
        self.assertNotContains(respuesta, 'nombre_rubro')

    def _consultas_del_listado(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as capturadas:
            respuesta = self.client.get(self.url)
            list(respuesta.context['registros'])
        return len(capturadas)

    def test_el_listado_no_dispara_una_consulta_por_fila(self):
        """N+1: lo que importa no es el número, es que no CREZCA.

        Sin `select_related` en los dos saltos (sesión -> usuario), cada
        fila de la tabla agrega dos consultas para mostrar quién la hizo.
        Se compara un listado de 1 fila contra uno de 20: si el costo fuera
        por fila, la diferencia sería de decenas.
        """
        self.asentar('ALTA DE RUBRO 0')
        con_una = self._consultas_del_listado()

        for n in range(1, 20):
            self.asentar('ALTA DE RUBRO %d' % n)
        con_veinte = self._consultas_del_listado()

        self.assertEqual(
            con_una, con_veinte,
            'El listado hace %d consultas con 1 fila y %d con 20: hay un '
            'N+1.' % (con_una, con_veinte))


# ===========================================================================
#  PASAJE A HISTÓRICO
# ===========================================================================
class PasajeAHistoricoTest(BaseActividad):
    """`manage.py pasar_a_historico`, que reemplaza a sp_pasaje_historico.

    Es la ÚNICA baja física del sistema (RN-8.1.1), así que lo que más
    importa probar no es que archive: es que **no borre de más** y que la
    simulación no toque nada.
    """

    def filas(self, tabla):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM %s' % tabla)
            return cursor.fetchone()[0]

    def correr(self, *extra):
        from io import StringIO

        from django.core.management import call_command

        salida = StringIO()
        call_command('pasar_a_historico', *extra, stdout=salida)
        return salida.getvalue()

    def test_la_simulacion_no_toca_nada(self):
        """Sin --ejecutar informa y deshace: es lo que la hace usable."""
        self.asentar('ALTA DE RUBRO')
        vivos = LogAuditoria.objects.count()

        salida = self.correr('--meses-log', '0')

        self.assertIn('SIMULACIÓN', salida)
        self.assertEqual(LogAuditoria.objects.count(), vivos)
        self.assertEqual(self.filas('hist_log_auditoria'), 0)

    def test_archiva_lo_viejo_y_deja_lo_reciente(self):
        """El corte parte el libro en dos: lo viejo se va, lo de hoy queda.

        Se prueban las dos mitades en el mismo test a propósito. Un test
        que sólo mirara lo archivado pasaría igual si el proceso se
        llevara TODO por delante, que es justo el error que no se puede
        cometer en la única baja física del sistema.
        """
        viejo = self.asentar(
            'ALTA ANTIGUA',
            cuando=timezone.now() - datetime.timedelta(days=400))
        reciente = self.asentar('ALTA DE HOY')

        self.correr('--ejecutar', '--meses-log', '12')

        self.assertFalse(
            LogAuditoria.objects.filter(pk=viejo.pk).exists(),
            'el asiento viejo tenía que salir del libro vivo')
        self.assertTrue(
            LogAuditoria.objects.filter(pk=reciente.pk).exists(),
            'el asiento de hoy NO se archiva')
        self.assertEqual(self.filas('hist_log_auditoria'), 1,
                         'y el viejo tiene que estar copiado en el histórico')

    def test_no_archiva_lo_que_todavia_es_reciente(self):
        """El corte es antigüedad: lo de hoy se queda."""
        self.asentar('ALTA DE RUBRO')
        vivos = LogAuditoria.objects.count()

        self.correr('--ejecutar', '--meses-log', '12')

        self.assertEqual(LogAuditoria.objects.count(), vivos)
        self.assertEqual(self.filas('hist_log_auditoria'), 0)

    def test_un_turno_abierto_no_se_archiva(self):
        """Regla 1: nada se archiva sin estar cerrado."""
        from maestros.models import CajaRegistradora
        from operaciones.models import TurnoCaja

        caja = CajaRegistradora.objects.create(
            nombre_caja_registradora='Caja Prueba')
        TurnoCaja.objects.create(
            id_caja_registradora=caja, id_usuario_apertura=self.admin,
            monto_inicial=0, estado_turno='ABIERTA')

        self.correr('--ejecutar', '--meses-operativo', '0')

        self.assertEqual(TurnoCaja.objects.count(), 1,
                         'un turno ABIERTO nunca se archiva')

    def test_fuera_del_pasaje_el_borrado_sigue_prohibido(self):
        """El permiso de borrar dura lo que dura el proceso."""
        from django.core.exceptions import ValidationError

        registro = self.asentar('ALTA DE RUBRO')
        with self.assertRaises(ValidationError):
            registro.delete()
