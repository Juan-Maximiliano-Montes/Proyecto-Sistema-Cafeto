"""Pruebas de los tres hallazgos del reporte de QA.

Cada clase cubre uno, y en los tres el caso que importa no es el feliz: es
el que demuestra que el candado viejo estaba roto.

  * ``RecetaEnLaInterfazTest``   — el ``RecetaFormSet`` existía en el
    backend pero ninguna pantalla lo dibujaba ni lo guardaba.
  * ``PerfilDeSistemaTest``      — la protección del administrador
    dependía de que el perfil siguiera llamándose "Administrador", un
    texto que el propio administrador puede editar.
  * ``RedaccionDeMensajesTest``  — el aviso de baja del efectivo estaba
    redactado como fragmento y se leía mal.

Estas pruebas arman su propio montaje en vez de apoyarse en
``BaseMaestros``: lo que verifican no puede depender de cómo se llamen las
filas semilla, porque el hallazgo número dos es justamente que el sistema
dependía de un nombre.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.utils import timezone

from cafeto.motivos import MotivoCompleto, redactar
from maestros.crud import _guardar_renglones, armar_formset, crear, editar
from maestros.forms import ProductoForm, RecetaFormSet
from maestros.models import (Beneficio, BeneficioProducto,
                             CategoriaFidelizacion, Marca, MedioPago,
                             ModoEntrega, Producto, Rubro, TipoBeneficio)
from maestros.views import BENEFICIOS, RUBROS
from proveedores.models import Proveedor
from usuarios.forms import PerfilForm
from usuarios.models import Perfil, PerfilPermiso, Permiso, Usuario

CUIT_QA = '30-71234567-6'


def _agregar_mensajes(pedido):
    """Deja el request listo para ``django.contrib.messages``.

    Las vistas del CRUD avisan con ``messages``, que necesita una sesión
    detrás. Un request de ``RequestFactory`` no la trae.
    """
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.contrib.sessions.backends.db import SessionStore

    pedido.session = SessionStore()
    pedido._messages = FallbackStorage(pedido)
    return pedido


# ===========================================================================
#  1 · LA RECETA SE PUEDE MANIPULAR DESDE LA INTERFAZ
# ===========================================================================
class RecetaEnLaInterfazTest(TestCase):
    """Un beneficio en especie entrega productos: hay que poder cargarlos.

    El formset estaba escrito y no lo usaba nadie: ni la configuración del
    CRUD lo declaraba, ni el template lo dibujaba, ni la vista lo guardaba.
    Un beneficio ESPECIE sólo se podía completar desde el shell.
    """

    def setUp(self):
        self.categoria = CategoriaFidelizacion.objects.create(
            nombre_categoria='Bronce QA', puntos_minimos=7700,
            importe_por_punto=Decimal('100'))
        rubro = Rubro.objects.create(nombre_rubro='Cafetería QA')
        marca = Marca.objects.create(nombre_marca='La Morenita QA')
        proveedor = Proveedor.objects.create(
            nombre_proveedor='La Salteña QA', cuit_proveedor=CUIT_QA)
        self.productos = [
            Producto.objects.create(
                nombre_producto=nombre, id_rubro=rubro, id_marca=marca,
                id_proveedor=proveedor, precio_venta=Decimal('100'),
                precio_costo=Decimal('50'), stock_minimo=1)
            for nombre in ('Café QA', 'Medialuna QA', 'Jugo QA')]

    def _beneficio(self):
        return Beneficio.objects.create(
            id_categoria=self.categoria, descripcion_beneficio='Combo QA',
            tipo_beneficio=TipoBeneficio.CANJEABLE,
            modo_entrega=ModoEntrega.ESPECIE, puntos_requeridos=50,
            vigencia_desde=datetime.date.today())

    def _post(self, **extra):
        """El POST de un formset: siempre lleva su management form."""
        datos = {'renglones-TOTAL_FORMS': '0', 'renglones-INITIAL_FORMS': '0',
                 'renglones-MIN_NUM_FORMS': '0',
                 'renglones-MAX_NUM_FORMS': '1000'}
        datos.update(extra)
        return datos

    # -- la configuración ----------------------------------------------
    def test_beneficios_declara_su_receta_y_las_demas_no(self):
        self.assertIs(BENEFICIOS.formset, RecetaFormSet)
        self.assertEqual(BENEFICIOS.formset_depende_de, 'modo_entrega')
        self.assertEqual(BENEFICIOS.formset_visible_si, ModoEntrega.ESPECIE)
        self.assertIsNone(RUBROS.formset)
        self.assertIsNone(armar_formset(RUBROS))

    # -- el template ----------------------------------------------------
    def test_el_formulario_dibuja_el_formset(self):
        """Lo que faltaba: el HTML del formset dentro del modal."""
        html = render_to_string('maestros/_campos.html', {
            'cfg': BENEFICIOS, 'form': BENEFICIOS.formulario(),
            'formset': armar_formset(BENEFICIOS)})
        for marca in ('js-renglones', 'renglones-TOTAL_FORMS', '__prefix__',
                      'js-agregar-renglon', 'js-quitar-renglon',
                      'Receta del beneficio',
                      'data-depende-de="modo_entrega"',
                      'data-visible-si="ESPECIE"'):
            self.assertIn(marca, html)

    def test_una_entidad_sin_receta_no_dibuja_nada(self):
        html = render_to_string('maestros/_campos.html', {
            'cfg': RUBROS, 'form': RUBROS.formulario(), 'formset': None})
        self.assertNotIn('js-renglones', html)

    def test_el_fragmento_de_edicion_trae_la_receta_cargada(self):
        """El modal se arma por fetch: el fragmento tiene que traerla.

        Se llama a la vista directamente, con el usuario puesto a mano en
        el request: lo único que mira ``@exige`` es ``usuario_actual``, y
        así la prueba no depende del login ni de la sesión de negocio.
        """
        beneficio = self._beneficio()
        BeneficioProducto.objects.create(
            id_beneficio=beneficio, id_producto=self.productos[0], cantidad=3)

        perfil = Perfil.objects.create(nombre='Admin QA', es_sistema=True)
        usuario = Usuario(dni_usuario='49000001', apellido='Haro',
                          nombre='Lucrecia', correo='qa@cafeto.test',
                          id_perfil=perfil)
        usuario.set_clave('Clave.Segura.2026')
        usuario.save()

        pedido = RequestFactory().get(
            BENEFICIOS.url('editar', beneficio.pk), {'formulario': '1'})
        pedido.usuario_actual = usuario
        respuesta = editar(pedido, 'beneficios', beneficio.pk)

        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.content.decode()
        self.assertIn('js-renglones', html)
        self.assertIn('renglones-INITIAL_FORMS" value="1"', html)
        self.assertIn('value="3"', html)

    # -- el POST sin formset --------------------------------------------
    def test_un_post_sin_formset_no_arma_formset(self):
        """Un beneficio de DESCUENTO no manda receta, y eso es correcto.

        Sin este cuidado, ``armar_formset`` construía el formset igual, no
        encontraba el *management form* y daba un error de formulario mal
        armado. El resultado era que el alta de CUALQUIER beneficio de
        descuento fallaba, con un mensaje genérico que no decía por qué.
        """
        datos = {'descripcion_beneficio': '10% en cafetería',
                 'modo_entrega': ModoEntrega.DESCUENTO}
        self.assertIsNone(armar_formset(BENEFICIOS, datos))

    def test_un_post_con_formset_si_lo_arma(self):
        self.assertIsNotNone(armar_formset(BENEFICIOS, self._post()))

    def test_el_alta_de_un_descuento_sin_receta_funciona(self):
        """La prueba de punta a punta de lo anterior, por la vista."""
        perfil = Perfil.objects.create(nombre='Admin alta QA', es_sistema=True)
        usuario = Usuario(dni_usuario='49000009', apellido='Haro',
                          nombre='Lucrecia', correo='alta@cafeto.test',
                          id_perfil=perfil)
        usuario.set_clave('Clave.Segura.2026')
        usuario.save()

        pedido = RequestFactory().post(BENEFICIOS.url('crear'), {
            'id_categoria': str(self.categoria.pk),
            'descripcion_beneficio': '10% en cafetería QA',
            'tipo_beneficio': TipoBeneficio.AUTOMATICO,
            'modo_entrega': ModoEntrega.DESCUENTO,
            'porcentaje_descuento': '10',
            'puntos_requeridos': '',
            'vigencia_desde': datetime.date.today().strftime('%d/%m/%Y'),
            'vigencia_hasta': '',
        })
        pedido.usuario_actual = usuario
        _agregar_mensajes(pedido)

        crear(pedido, 'beneficios')

        self.assertTrue(Beneficio.objects.filter(
            descripcion_beneficio='10% en cafetería QA').exists())

    # -- el guardado ----------------------------------------------------
    def test_se_guarda_la_receta_cargada(self):
        beneficio = self._beneficio()
        formset = RecetaFormSet(self._post(**{
            'renglones-TOTAL_FORMS': '2',
            'renglones-0-id_producto': str(self.productos[0].pk),
            'renglones-0-cantidad': '1',
            'renglones-1-id_producto': str(self.productos[1].pk),
            'renglones-1-cantidad': '2',
        }), instance=beneficio)

        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertIsNone(_guardar_renglones(BENEFICIOS, formset, beneficio))
        self.assertEqual(beneficio.renglones.count(), 2)

    def test_la_receta_se_reemplaza_entera(self):
        """El caso que rompe si se borra antes de dar de alta.

        ``formset.save()`` borra primero y agrega después. Al borrar el
        último renglón viejo, el beneficio queda un instante sin receta y
        ``BeneficioProducto.delete()`` lo corta por DD-18 — aunque el
        estado final sea perfectamente válido. Por eso el CRUD escribe al
        revés: altas y cambios primero, borrados al final.
        """
        beneficio = self._beneficio()
        viejos = [BeneficioProducto.objects.create(
            id_beneficio=beneficio, id_producto=producto, cantidad=1)
            for producto in self.productos[:2]]

        formset = RecetaFormSet(self._post(**{
            'renglones-TOTAL_FORMS': '3', 'renglones-INITIAL_FORMS': '2',
            'renglones-0-id_beneficio_producto': str(viejos[0].pk),
            'renglones-0-id_producto': str(self.productos[0].pk),
            'renglones-0-cantidad': '1', 'renglones-0-DELETE': 'on',
            'renglones-1-id_beneficio_producto': str(viejos[1].pk),
            'renglones-1-id_producto': str(self.productos[1].pk),
            'renglones-1-cantidad': '1', 'renglones-1-DELETE': 'on',
            'renglones-2-id_producto': str(self.productos[2].pk),
            'renglones-2-cantidad': '5',
        }), instance=beneficio)

        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertIsNone(_guardar_renglones(BENEFICIOS, formset, beneficio))
        self.assertEqual(
            [renglon.id_producto for renglon in beneficio.renglones.all()],
            [self.productos[2]])

    def test_en_especie_sin_receta_se_rechaza_con_su_mensaje(self):
        beneficio = self._beneficio()
        formset = RecetaFormSet(self._post(**{
            'renglones-TOTAL_FORMS': '1',
            'renglones-0-id_producto': '', 'renglones-0-cantidad': '1',
        }), instance=beneficio)

        self.assertTrue(formset.is_valid(), formset.errors)
        error = _guardar_renglones(BENEFICIOS, formset, beneficio)
        self.assertIn('necesita al menos un producto', error)


# ===========================================================================
#  2 · EL CANDADO DEL ADMINISTRADOR NO DEPENDE DE UN TEXTO EDITABLE
# ===========================================================================
class PerfilDeSistemaTest(TestCase):
    """Renombrar el perfil no puede abrir ni cerrar ninguna puerta.

    Antes, cuatro lugares comparaban ``Perfil.nombre`` contra
    ``'Administrador'``. Como ese nombre se edita desde la pantalla de
    Perfiles, el candado se abría solo en las dos direcciones.

    El montaje lo deja a la vista: el perfil de sistema de estas pruebas
    NO se llama "Administrador". Si algo siguiera mirando el nombre, estas
    pruebas lo marcarían enseguida.
    """

    def setUp(self):
        self.permiso = Permiso.objects.create(
            codigo='ver_qa', nombre='Ver QA', modulo='GENERAL')
        self.admin = Perfil.objects.create(
            nombre='Jefatura QA', descripcion='Acceso total',
            es_sistema=True)
        PerfilPermiso.objects.create(perfil=self.admin, permiso=self.permiso)

        self.operador = Perfil.objects.create(nombre='Operador QA')
        PerfilPermiso.objects.create(
            perfil=self.operador, permiso=self.permiso)

        self.usuario_admin = self._usuario('49000001', self.admin)
        self.usuario_operador = self._usuario('49000002', self.operador)

    def _usuario(self, dni, perfil):
        usuario = Usuario(dni_usuario=dni, apellido='Haro', nombre='Lucrecia',
                          correo=f'{dni}@cafeto.test', id_perfil=perfil)
        usuario.set_clave('Clave.Segura.2026')
        usuario.save()
        return usuario

    # -- el acceso total -------------------------------------------------
    def test_el_acceso_total_lo_da_la_marca_y_no_el_nombre(self):
        """El perfil no se llama "Administrador" y manda igual."""
        self.assertNotEqual(self.admin.nombre, 'Administrador')
        self.assertTrue(
            self.usuario_admin.tiene_permiso('permiso_que_no_existe'))

    def test_renombrarlo_no_le_saca_el_acceso_total(self):
        Perfil.objects.filter(pk=self.admin.pk).update(nombre='Otra Cosa')
        self.usuario_admin.refresh_from_db()
        self.assertTrue(
            self.usuario_admin.tiene_permiso('permiso_que_no_existe'))

    def test_llamarse_administrador_no_regala_acceso_total(self):
        """La otra mitad del agujero: el impostor.

        Se reproduce el ataque completo. Primero se corre de nombre al
        perfil de sistema que siembra la base —si no, el nombre está
        ocupado y el índice único no deja—; después el perfil operativo
        se queda con el nombre "Administrador". Antes, eso bastaba para
        tener el sistema entero.
        """
        Perfil.objects.filter(es_sistema=True).exclude(
            pk=self.admin.pk).update(nombre='Jefatura sembrada')
        Perfil.objects.filter(pk=self.operador.pk).update(
            nombre='Administrador')
        self.usuario_operador.refresh_from_db()

        self.assertFalse(self.usuario_operador.tiene_permiso('crear_usuario'))
        # Lo que SÍ tiene asignado lo sigue teniendo.
        self.assertTrue(self.usuario_operador.tiene_permiso('ver_qa'))

    # -- la baja ---------------------------------------------------------
    def test_el_perfil_de_sistema_no_se_da_de_baja(self):
        motivos = self.admin.motivos_que_impiden_la_baja()
        self.assertIn('no puede ser eliminado',
                      redactar('No se puede dar de baja', motivos))

    def test_sigue_protegido_despues_de_renombrarlo(self):
        Perfil.objects.filter(pk=self.admin.pk).update(nombre='Otra Cosa')
        self.assertTrue(
            Perfil.objects.get(pk=self.admin.pk)
            .motivos_que_impiden_la_baja())

    def test_un_perfil_comun_con_usuarios_activos_tampoco(self):
        motivos = self.operador.motivos_que_impiden_la_baja()
        self.assertEqual(motivos, ['tiene 1 usuario(s) activo(s) asignado(s)'])

    def test_un_perfil_comun_vacio_si_se_da_de_baja(self):
        vacio = Perfil.objects.create(nombre='Sin Usuarios QA')
        self.assertEqual(vacio.motivos_que_impiden_la_baja(), [])

    # -- la edición ------------------------------------------------------
    def test_el_nombre_del_perfil_de_sistema_no_se_edita(self):
        form = PerfilForm(
            {'nombre': 'Otra Cosa', 'descripcion': 'Acceso total',
             'permisos': [self.permiso.pk]},
            instance=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('nombre', form.errors)

    def test_su_descripcion_si_se_edita(self):
        form = PerfilForm(
            {'nombre': self.admin.nombre, 'descripcion': 'Manda en todo',
             'permisos': [self.permiso.pk]},
            instance=self.admin)
        self.assertTrue(form.is_valid(), form.errors)

    def test_un_perfil_comun_si_se_renombra(self):
        form = PerfilForm(
            {'nombre': 'Cajero QA', 'descripcion': 'Cobra',
             'permisos': [self.permiso.pk]},
            instance=self.operador)
        self.assertTrue(form.is_valid(), form.errors)

    # -- el último administrador -----------------------------------------
    def test_el_ultimo_administrador_se_mide_por_la_marca(self):
        """Renombrado y solo: no se puede dar de baja igual.

        La base siembra su propio administrador, así que primero se lo
        desactiva: lo que se prueba es que, cuando el que queda es ESTE
        —con el perfil renombrado—, el candado lo reconoce por la marca.
        """
        Perfil.objects.filter(pk=self.admin.pk).update(nombre='Otra Cosa')
        # activo y fecha_baja tienen que contar lo mismo (ck_usuarios_baja).
        Usuario.objects.filter(id_perfil__es_sistema=True).exclude(
            pk=self.usuario_admin.pk).update(
                activo=False, fecha_baja=timezone.now())
        self.usuario_admin.refresh_from_db()

        self.assertTrue(self.usuario_admin.es_ultimo_administrador)
        with self.assertRaises(ValidationError):
            self.usuario_admin.dar_de_baja()

    def test_un_operador_nunca_es_el_ultimo_administrador(self):
        self.assertFalse(self.usuario_operador.es_ultimo_administrador)

    # -- el catálogo completo --------------------------------------------
    def test_el_catalogo_completo_va_por_la_marca(self):
        """El permiso nuevo le llega al perfil marcado, no al que se llama
        de cierta forma.

        No se mira el número que devuelve ``asegurar_catalogo_completo()``:
        la base de la aplicación ya trae su propio perfil de sistema
        —el que siembra ``usuarios/0002``— y el método los completa a
        todos. Lo que importa acá es a QUIÉN le llegó el permiso nuevo.
        """
        Perfil.objects.filter(pk=self.admin.pk).update(nombre='Otra Cosa')
        Permiso.objects.create(codigo='ver_qa_2', nombre='Ver QA 2',
                               modulo='GENERAL')

        Perfil.asegurar_catalogo_completo()

        self.assertTrue(PerfilPermiso.objects.filter(
            perfil=self.admin, permiso__codigo='ver_qa_2').exists())
        # Al operador no le toca nada: no está marcado.
        self.assertEqual(
            PerfilPermiso.objects.filter(perfil=self.operador).count(), 1)


# ===========================================================================
#  3 · REDACCIÓN DE LOS MENSAJES
# ===========================================================================
class RedaccionDeMensajesTest(TestCase):
    """Un ERP no manda emoji, y no deja frases mal engarzadas."""

    def test_el_efectivo_dice_la_frase_completa(self):
        efectivo, _ = MedioPago.objects.get_or_create(
            nombre_medio_pago='Efectivo',
            defaults={'recargo_porcentaje': Decimal('0')})
        MedioPago.objects.create(
            nombre_medio_pago='Tarjeta QA', recargo_porcentaje=Decimal('5'))

        texto = redactar('No se puede dar de baja "Efectivo"',
                         efectivo.motivos_que_impiden_la_baja())

        self.assertEqual(
            texto,
            "El medio de pago 'Efectivo' es el método por defecto del "
            'sistema y no puede ser deshabilitado.')
        self.assertNotIn('porque', texto)

    def test_un_motivo_comun_sigue_engarzandose(self):
        texto = redactar('No se puede dar de baja "Cafetería"',
                         ['tiene 3 producto(s) vigente(s)'])
        self.assertEqual(
            texto,
            'No se puede dar de baja "Cafetería" porque tiene 3 '
            'producto(s) vigente(s).')

    def test_se_pueden_mezclar_completo_y_fragmento(self):
        texto = redactar('No se puede dar de baja "X"',
                         [MotivoCompleto('Es intocable.'), 'tiene 2 hijos'])
        self.assertEqual(
            texto,
            'Es intocable. No se puede dar de baja "X" porque tiene 2 hijos.')

    def test_el_aviso_de_vender_bajo_costo_no_trae_emoji(self):
        campo = ProductoForm().fields['confirmo_bajo_costo']
        for texto in (campo.label, campo.help_text):
            self.assertTrue(all(ord(letra) < 0x2190 for letra in texto),
                            texto)
