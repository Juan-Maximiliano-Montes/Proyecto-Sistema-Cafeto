"""Pruebas de lo que pidió CORRECCIONES.txt.

Una por regla y con el caso límite al lado del caso que pasa: una regla de
rango sin su borde probado no está probada.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from cafeto.validaciones import (
    NO_NEGATIVO,
    VALIDADOR_CUIT,
    VALIDADOR_DNI,
    VALIDADOR_TELEFONO,
    anios_cumplidos,
    validar_edad,
)
from maestros.models import CategoriaFidelizacion, Marca, Producto
from maestros.tests import BaseMaestros
from operaciones.models import Cliente
from proveedores.models import Proveedor
from usuarios.menu import armar


HOY = datetime.date(2026, 10, 5)


# ===========================================================================
#  5 · EDAD DEL CLIENTE (14 a 100, nunca futura)
# ===========================================================================
class EdadDelClienteTest(TestCase):

    def test_los_dos_bordes_entran(self):
        """Exactamente 14 y exactamente 100 son válidos: el rango es cerrado."""
        for fecha in (datetime.date(2012, 10, 5), datetime.date(1926, 10, 5)):
            validar_edad(fecha, HOY)

    def test_un_dia_antes_de_cumplir_catorce_no_entra(self):
        """El 6 de octubre de 2012 todavía tiene 13 el 5 de octubre de 2026."""
        self.assertEqual(anios_cumplidos(datetime.date(2012, 10, 6), HOY), 13)
        with self.assertRaises(ValidationError):
            validar_edad(datetime.date(2012, 10, 6), HOY)

    def test_el_que_ya_cumplio_ciento_uno_no_entra(self):
        self.assertEqual(anios_cumplidos(datetime.date(1925, 10, 5), HOY), 101)
        with self.assertRaises(ValidationError):
            validar_edad(datetime.date(1925, 10, 5), HOY)

    def test_la_fecha_futura_se_rechaza_con_su_propio_mensaje(self):
        with self.assertRaises(ValidationError) as cajon:
            validar_edad(datetime.date(2027, 1, 1), HOY)
        self.assertIn('posterior a hoy', cajon.exception.messages[0])

    def test_sin_fecha_no_hay_nada_que_validar(self):
        """El dato es opcional: no cargarlo no puede ser un error."""
        self.assertIsNone(validar_edad(None, HOY))

    def test_el_modelo_rechaza_al_menor_de_catorce(self):
        categoria = CategoriaFidelizacion.objects.get(puntos_minimos=0)
        cliente = Cliente(
            id_categoria=categoria, apellido_cliente='Vera',
            nombre_cliente='Tomás', qr_cliente='CAF-TEST-1',
            fecha_nacimiento=datetime.date.today() - datetime.timedelta(days=365 * 10))
        with self.assertRaises(ValidationError):
            cliente.clean()


# ===========================================================================
#  5 · VALIDADORES NATIVOS
# ===========================================================================
class ValidadoresNativosTest(TestCase):
    """Rechazan letras; toleran los separadores que la gente tipea.

    Que el CUIT con guiones pase es parte del contrato: el validador de
    campo corre ANTES que la normalización del modelo, así que si exigiera
    los 11 dígitos pelados rechazaría un CUIT correcto.
    """

    def test_el_cuit_con_guiones_pasa_y_con_letra_no(self):
        VALIDADOR_CUIT('30-71234567-1')
        VALIDADOR_CUIT('30712345671')
        with self.assertRaises(ValidationError):
            VALIDADOR_CUIT('3071234567A')

    def test_el_dni_con_puntos_pasa_y_con_letra_no(self):
        VALIDADOR_DNI('40.125.874')
        VALIDADOR_DNI('40125874')
        with self.assertRaises(ValidationError):
            VALIDADOR_DNI('4012587A')

    def test_el_telefono_con_espacios_pasa_y_con_letra_no(self):
        VALIDADOR_TELEFONO('(387) 555 1234')
        with self.assertRaises(ValidationError):
            VALIDADOR_TELEFONO('387555123A')

    def test_el_importe_negativo_se_rechaza_y_el_cero_no(self):
        NO_NEGATIVO(Decimal('0'))
        with self.assertRaises(ValidationError):
            NO_NEGATIVO(Decimal('-0.01'))

    def test_el_precio_negativo_no_llega_a_la_base(self):
        producto = Producto(nombre_producto='Prueba',
                            precio_venta=Decimal('-5'))
        with self.assertRaises(ValidationError) as cajon:
            producto.full_clean()
        self.assertIn('precio_venta', cajon.exception.message_dict)


# ===========================================================================
#  5 · EL DUPLICADO QUE ESTÁ DADO DE BAJA
# ===========================================================================
class DuplicadoDadoDeBajaTest(TestCase):

    def test_el_proveedor_dado_de_baja_sugiere_reactivar(self):
        Proveedor.objects.create(
            nombre_proveedor='La Salteña', cuit_proveedor='30-71234567-1',
            condicion_pago=Proveedor.CondicionPago.CONTADO,
            borrado_logico=True)
        nuevo = Proveedor(
            nombre_proveedor='La Salteña SRL', cuit_proveedor='30-71234567-1',
            condicion_pago=Proveedor.CondicionPago.CONTADO)
        with self.assertRaises(ValidationError) as cajon:
            nuevo.clean()
        self.assertIn('Reactivar', ' '.join(cajon.exception.messages))

    def test_el_proveedor_vigente_dice_que_ya_existe(self):
        Proveedor.objects.create(
            nombre_proveedor='La Salteña', cuit_proveedor='30-71234567-1',
            condicion_pago=Proveedor.CondicionPago.CONTADO)
        nuevo = Proveedor(
            nombre_proveedor='La Salteña SRL', cuit_proveedor='30-71234567-1',
            condicion_pago=Proveedor.CondicionPago.CONTADO)
        with self.assertRaises(ValidationError) as cajon:
            nuevo.clean()
        self.assertIn('ya existe', ' '.join(cajon.exception.messages).lower())

    def test_editar_la_propia_fila_no_se_traba_con_ella_misma(self):
        proveedor = Proveedor.objects.create(
            nombre_proveedor='La Salteña', cuit_proveedor='30-71234567-1',
            condicion_pago=Proveedor.CondicionPago.CONTADO)
        proveedor.nombre_proveedor = 'La Salteña SRL'
        proveedor.clean()          # no tiene que levantar nada


# ===========================================================================
#  4 · FICHA DE SÓLO LECTURA
# ===========================================================================
class FichaDeSoloLecturaTest(BaseMaestros):

    def test_la_ficha_muestra_los_datos_y_ningun_control(self):
        marca = Marca.objects.create(nombre_marca='Cabrales')
        respuesta = self.client.get(self.url('marcas', 'ver', marca.pk))
        self.assertEqual(respuesta.status_code, 200)
        cuerpo = respuesta.content.decode()
        self.assertIn('Cabrales', cuerpo)
        for control in ('<input', '<select', '<textarea'):
            self.assertNotIn(control, cuerpo)

    def test_la_ficha_tambien_abre_una_fila_dada_de_baja(self):
        """Consultar no es editar: lo dado de baja se tiene que poder mirar."""
        marca = Marca.objects.create(nombre_marca='Vieja', borrado_logico=True)
        respuesta = self.client.get(self.url('marcas', 'ver', marca.pk))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Dado de baja')

    def test_sin_permiso_de_ver_no_hay_ficha(self):
        marca = Marca.objects.create(nombre_marca='Cabrales')
        self.client.logout()
        self.entrar(self.operador)
        respuesta = self.client.get(
            self.url('marcas', 'ver', marca.pk), follow=True)
        self.assertIn('Acceso denegado', ' '.join(self.mensajes(respuesta)))

    def test_el_listado_trae_el_boton_del_ojo(self):
        respuesta = self.client.get(self.url('marcas', 'lista'))
        self.assertContains(respuesta, 'boton-ver')
        self.assertContains(respuesta, 'bi-eye')


# ===========================================================================
#  1, 2 y 3 · INTERFAZ
# ===========================================================================
class InterfazTest(BaseMaestros):

    def test_el_listado_ya_no_trae_el_parrafo_de_ayuda(self):
        respuesta = self.client.get(self.url('productos', 'lista'))
        self.assertNotContains(respuesta, 'bi-info-circle')

    def test_el_nombre_va_pegado_al_id(self):
        """ID → Producto → Marca, y no ID → Marca → Producto."""
        from maestros.views import PRODUCTOS

        etiquetas = [columna.etiqueta for columna in PRODUCTOS.columnas]
        self.assertEqual(etiquetas[:3], ['Producto', 'Marca', 'Presentación'])

    def test_todas_las_entidades_ponen_el_nombre_primero(self):
        from maestros.crud import REGISTRO

        for clave, cfg in REGISTRO.items():
            primera = cfg.columnas[0]
            self.assertEqual(
                primera.atributo, cfg.campo_nombre,
                'En "%s" la primera columna es "%s" y no el nombre ("%s"): '
                'el ID de al lado se lee como si fuera de esa columna.'
                % (clave, primera.atributo, cfg.campo_nombre))

    def test_el_menu_usa_el_vocabulario_del_negocio(self):
        titulos = [grupo['titulo'] for grupo in armar(self.admin)]
        self.assertIn('Inventario y Menú', titulos)
        self.assertIn('Club Cafeto', titulos)
        self.assertIn('Ajustes del Local', titulos)
        self.assertIn('Equipo y Permisos', titulos)
        for tecnico in ('Datos maestros', 'Catálogo', 'Configuración'):
            self.assertNotIn(tecnico, titulos)

    def test_el_formulario_pide_confirmacion_antes_de_guardar(self):
        respuesta = self.client.get(self.url('marcas', 'lista'))
        self.assertContains(respuesta, '¿Confirma que desea guardar estos datos?')


# ===========================================================================
#  AUDITORÍA DE CONSISTENCIA
# ===========================================================================
class AuditarConsistenciaTest(BaseMaestros):
    """BaseMaestros ya deja rubro, proveedor y marca listos."""

    def _producto(self, nombre, contenido, unidad):
        return Producto.objects.create(
            nombre_producto=nombre, id_marca=self.marca,
            id_rubro=self.rubro, id_proveedor=self.proveedor,
            precio_venta=Decimal('100'), contenido=contenido,
            unidad_medida=unidad)

    def test_detecta_el_producto_con_la_marca_adentro_del_nombre(self):
        from io import StringIO

        from django.core.management import call_command

        sucio = self._producto('Café molido La Morenita 500 g',
                               Decimal('1'), 'UNIDAD')
        limpio = self._producto('Café molido', Decimal('500'), 'G')

        salida = StringIO()
        call_command('auditar_consistencia', '--dar-de-baja', stdout=salida)

        sucio.refresh_from_db()
        limpio.refresh_from_db()
        self.assertTrue(sucio.borrado_logico, 'el sucio tenía que caer')
        self.assertFalse(limpio.borrado_logico, 'el limpio tenía que quedar')

    def test_sin_el_flag_no_toca_nada(self):
        from io import StringIO

        from django.core.management import call_command

        sucio = self._producto('Café La Morenita', Decimal('1'), 'UNIDAD')

        call_command('auditar_consistencia', stdout=StringIO())

        sucio.refresh_from_db()
        self.assertFalse(sucio.borrado_logico)


# ===========================================================================
#  LA COLUMNA QUE CUENTA RELACIONADOS
# ===========================================================================
class ColumnaDeConteoTest(BaseMaestros):
    """`productos.count` tiene que dar el número, no un guión.

    El RelatedManager de Django **es** invocable: define
    ``__call__(self, *, manager)``. `valor_de` lo llamaba sin argumentos,
    se comía el TypeError y devolvía None, así que la columna "Productos"
    de Marcas mostraba "—" para todas las marcas, siempre.
    """

    def test_valor_de_cuenta_los_relacionados(self):
        from maestros.templatetags.maestros_extras import valor_de

        Producto.objects.create(
            nombre_producto='Café molido', id_marca=self.marca,
            id_rubro=self.rubro, id_proveedor=self.proveedor,
            precio_venta=Decimal('100'), contenido=Decimal('500'),
            unidad_medida='G')
        self.assertEqual(valor_de(self.marca, 'productos.count'), 1)

    def test_sin_relacionados_da_cero_y_no_none(self):
        from maestros.templatetags.maestros_extras import valor_de

        sola = Marca.objects.create(nombre_marca='Cabrales')
        self.assertEqual(valor_de(sola, 'productos.count'), 0)

    def test_el_listado_muestra_el_numero(self):
        Producto.objects.create(
            nombre_producto='Café molido', id_marca=self.marca,
            id_rubro=self.rubro, id_proveedor=self.proveedor,
            precio_venta=Decimal('100'), contenido=Decimal('500'),
            unidad_medida='G')
        respuesta = self.client.get(self.url('marcas', 'lista'))
        cuerpo = respuesta.content.decode()
        fila = cuerpo[cuerpo.index('La Morenita'):]
        self.assertIn('>1<', fila[:600].replace(' ', '').replace('\n', ''))
