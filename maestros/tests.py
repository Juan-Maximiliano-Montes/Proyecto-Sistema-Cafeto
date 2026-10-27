"""Pruebas de "Mantener datos maestros" (sección 4.4.T).

Están organizadas por la regla que prueban, no por pantalla, porque las
nueve pantallas comparten la misma máquina: lo que hay que demostrar es
que cada regla de 4.4.V se cumple, y en qué capa.

De cada regla hay un caso que PASA y uno que FALLA, con los valores del
borde: el largo máximo y el máximo más uno, el 0 y el negativo, el 100 y
el 100,01, la cadena vacía y la de sólo espacios, el mismo nombre con
otra mayúscula o sin acento, el 29 de febrero de un año no bisiesto.

Las últimas clases prueban lo que no depende del formulario: que la base
rechaza por sí sola los valores inválidos aunque se inserte por el ORM, y
que los permisos cortan el acceso aunque se escriba la URL a mano.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from operaciones.models import (
    TurnoCaja,
    Cliente,
    Compra,
    LogAuditoria,
    OrdenCompra,
    Sesion,
)
from proveedores.models import Localidad, Provincia, Proveedor
from usuarios.models import Perfil, PerfilPermiso, Permiso, Usuario

from .models import (
    Beneficio,
    BeneficioProducto,
    CategoriaFidelizacion,
    Marca,
    MedioPago,
    Producto,
    CajaRegistradora,
    Rubro,
    TipoEgreso,
)

CUIT_VALIDO = '30-71234567-1'       # dígito verificador módulo 11 correcto
CUIT_OTRO = '20-12345678-6'
# Un tercero, para las pruebas que llaman a clean() sin querer chocar con el
# proveedor que arma BaseMaestros: desde DD-58, clean() avisa del duplicado.
CUIT_LIBRE = '30-99887766-7'


class BaseMaestros(TestCase):
    """Montaje común: un Administrador logueado y un Operador sin permisos."""

    def setUp(self):
        self.perfil_admin = Perfil.objects.get(nombre='Administrador')
        self.perfil_operador, _ = Perfil.objects.get_or_create(
            nombre='Operador', defaults={'descripcion': 'Tareas operativas'})
        # DD-14 exige que un perfil asignable tenga al menos un permiso.
        PerfilPermiso.objects.get_or_create(
            perfil=self.perfil_operador,
            permiso=Permiso.objects.get(codigo='ver_usuarios'))

        self.admin = self._crear_usuario('40000001', 'Haro', 'Lucrecia',
                                         self.perfil_admin)
        self.operador = self._crear_usuario('40000002', 'Aramayo', 'Camila',
                                            self.perfil_operador)
        self.entrar(self.admin)

        self.proveedor = Proveedor.objects.create(
            nombre_proveedor='La Salteña', cuit_proveedor=CUIT_VALIDO)
        self.rubro = Rubro.objects.get(nombre_rubro='Cafetería')
        self.marca = Marca.objects.create(nombre_marca='La Morenita')

    # -- helpers -------------------------------------------------------
    def _crear_usuario(self, dni, apellido, nombre, perfil):
        usuario = Usuario(dni_usuario=dni, apellido=apellido, nombre=nombre,
                          correo=f'{dni}@cafeto.test', id_perfil=perfil)
        usuario.set_clave('Clave.Segura.2026')
        usuario.save()
        return usuario

    def entrar(self, usuario):
        """Deja la sesión de Django y la de negocio listas.

        La de negocio es la que firma la auditoría: sin ella no se escribe
        LOG_AUDITORIA, así que los tests de auditoría necesitan las dos.
        """
        # DD-01: un usuario tiene UNA sesión activa. Si quedó otra abierta
        # —en los tests, al cambiar de usuario y volver— se revoca antes.
        Sesion.objects.filter(id_usuario=usuario, activa=True).update(
            activa=False, fecha_cierre=timezone.now(),
            motivo_cierre='REVOCACION')
        sesion_negocio = Sesion.objects.create(
            id_usuario=usuario,
            token_hash=f'token-{usuario.pk}-{timezone.now().timestamp()}',
            fecha_expiracion=timezone.now() + timedelta(hours=8),
            ip_origen='127.0.0.1')
        sesion = self.client.session
        sesion['usuario_id'] = usuario.pk
        sesion['id_sesion_negocio'] = sesion_negocio.pk
        sesion.save()
        return sesion_negocio

    def url(self, entidad, accion, pk=None):
        nombre = f'maestros:{entidad}_{accion}'
        if entidad in ('provincias', 'localidades'):
            nombre = f'proveedores:{entidad}_{accion}'
        return reverse(nombre, args=[pk] if pk is not None else None)

    def crear(self, entidad, datos):
        return self.client.post(self.url(entidad, 'crear'), datos, follow=True)

    def editar(self, entidad, pk, datos):
        return self.client.post(self.url(entidad, 'editar', pk), datos,
                                follow=True)

    def mensajes(self, respuesta):
        return [str(m) for m in respuesta.context['messages']]

    def errores_de_campo(self, respuesta, campo):
        """Los errores que el formulario puso EN un campo.

        El duplicado ya no llega como aviso suelto: lo levanta el
        ``clean()`` del modelo atado al campo, así que Django lo dibuja al
        lado del control y el formulario vuelve con lo que se tipeó. Eso
        es lo que hay que comprobar, y no el texto del cartel de arriba.
        """
        formulario = (respuesta.context.get('formulario')
                      or respuesta.context.get('formulario_editar'))
        return formulario.errors.get(campo, [])

    def dar_stock(self, producto, cantidad, lote=None):
        """Carga stock por el camino real: un movimiento de ENTRADA.

        ``productos.stock_actual`` es derivado (RN-5.1.1) y el mixin
        ``ConSaldosDerivados`` rechaza cualquier UPDATE directo. El único
        que lo escribe es el asiento en MOVIMIENTOS_STOCK. Así que el test
        hace lo que haría el sistema: una compra, su renglón y el
        movimiento de entrada.
        """
        from operaciones.models import DetalleCompra, MovimientoStock

        compra = Compra.objects.create(
            id_proveedor=self.proveedor, id_usuario=self.admin,
            numero_factura=f'A-0001-{producto.pk:08d}',
            total_compra=Decimal('1000'))
        DetalleCompra.objects.create(
            id_compra=compra, id_producto=producto,
            cantidad_recibida=cantidad,
            precio_costo_unitario=Decimal('100'))
        MovimientoStock.objects.create(
            id_producto=producto, id_usuario=self.admin, id_compra=compra,
            id_lote=lote, tipo_movimiento='ENTRADA', cantidad=cantidad)
        producto.refresh_from_db()
        return producto

    def producto_de_prueba(self, **extra):
        datos = dict(
            nombre_producto='Café molido',
            id_rubro=self.rubro, id_proveedor=self.proveedor,
            id_marca=self.marca, contenido=Decimal('500'), unidad_medida='G',
            precio_venta=Decimal('5000.00'), precio_costo=Decimal('3000.00'),
            stock_minimo=5)
        datos.update(extra)
        return Producto.objects.create(**datos)


# ===========================================================================
#  ALTA VÁLIDA Y AUDITORÍA  (4.4 d)
# ===========================================================================
class AltaYAuditoriaTest(BaseMaestros):

    def test_alta_valida_crea_la_fila(self):
        respuesta = self.crear('rubros', {
            'nombre_rubro': 'Panadería',
            'descripcion_rubro': 'Facturas y pan del día',
            'activo': 'on'})
        self.assertEqual(respuesta.status_code, 200)
        rubro = Rubro.objects.get(nombre_rubro='Panadería')
        self.assertFalse(rubro.borrado_logico)
        self.assertEqual(rubro.descripcion_rubro, 'Facturas y pan del día')

    def test_el_alta_queda_en_el_log_de_auditoria(self):
        self.crear('rubros', {'nombre_rubro': 'Panadería', 'activo': 'on'})

        registro = LogAuditoria.objects.filter(
            modulo_afectado='DATOS_MAESTROS').latest('fecha_hora')
        self.assertEqual(registro.operacion_realizada, 'ALTA DE RUBRO')
        self.assertIsNone(registro.registro_anterior)
        self.assertEqual(registro.registro_nuevo['nombre_rubro'], 'Panadería')
        self.assertEqual(registro.id_sesion.id_usuario, self.admin)

    def test_la_modificacion_guarda_el_antes_y_el_despues(self):
        rubro = Rubro.objects.create(nombre_rubro='Panadería')
        self.editar('rubros', rubro.pk, {
            'nombre_rubro': 'Panadería artesanal', 'activo': 'on'})

        registro = LogAuditoria.objects.filter(
            operacion_realizada='MODIFICACION DE RUBRO').latest('fecha_hora')
        self.assertEqual(registro.registro_anterior['nombre_rubro'],
                         'Panadería')
        self.assertEqual(registro.registro_nuevo['nombre_rubro'],
                         'Panadería artesanal')

    def test_la_baja_y_la_reactivacion_tambien_se_auditan(self):
        rubro = Rubro.objects.create(nombre_rubro='Panadería')

        self.client.post(self.url('rubros', 'eliminar', rubro.pk), follow=True)
        baja = LogAuditoria.objects.latest('fecha_hora')
        self.assertEqual(baja.operacion_realizada, 'BAJA DE RUBRO')
        self.assertFalse(baja.registro_anterior['borrado_logico'])
        self.assertTrue(baja.registro_nuevo['borrado_logico'])

        self.client.post(self.url('rubros', 'reactivar', rubro.pk), follow=True)
        alta = LogAuditoria.objects.latest('fecha_hora')
        self.assertEqual(alta.operacion_realizada, 'REACTIVACION DE RUBRO')
        self.assertFalse(alta.registro_nuevo['borrado_logico'])


# ===========================================================================
#  V-G1 · LIMPIEZA DE TEXTO
# ===========================================================================
class LimpiezaDeTextoTest(BaseMaestros):

    def test_pasa_los_espacios_se_recortan_y_se_colapsan(self):
        self.crear('rubros', {'nombre_rubro': '  Pana   dería  ', 'activo': 'on'})
        self.assertTrue(Rubro.objects.filter(nombre_rubro='Pana dería').exists())

    def test_falla_solo_espacios_es_obligatorio(self):
        self.crear('rubros', {'nombre_rubro': '     ', 'activo': 'on'})
        self.assertFalse(Rubro.objects.filter(nombre_rubro='     ').exists())

    def test_falla_cadena_vacia(self):
        respuesta = self.crear('rubros', {'nombre_rubro': '', 'activo': 'on'})
        self.assertContains(respuesta, 'obligatorio')

    def test_pasa_el_opcional_vacio_se_guarda_nulo(self):
        self.crear('rubros', {'nombre_rubro': 'Panadería',
                              'descripcion_rubro': '   ', 'activo': 'on'})
        self.assertIsNone(
            Rubro.objects.get(nombre_rubro='Panadería').descripcion_rubro)


# ===========================================================================
#  V-G2 · LONGITUD  ·  V-G3 · CARACTERES
# ===========================================================================
class LongitudYCaracteresTest(BaseMaestros):

    def test_pasa_el_largo_maximo_exacto(self):
        nombre = 'R' * 40                       # nombre_rubro VARCHAR(40)
        self.crear('rubros', {'nombre_rubro': nombre, 'activo': 'on'})
        self.assertTrue(Rubro.objects.filter(nombre_rubro=nombre).exists())

    def test_falla_el_maximo_mas_uno(self):
        nombre = 'R' * 41
        self.crear('rubros', {'nombre_rubro': nombre, 'activo': 'on'})
        self.assertFalse(Rubro.objects.filter(nombre_rubro=nombre).exists())

    def test_falla_por_debajo_del_minimo(self):
        """El rubro pide 2 caracteres: una letra sola no alcanza."""
        self.crear('rubros', {'nombre_rubro': 'X', 'activo': 'on'})
        self.assertFalse(Rubro.objects.filter(nombre_rubro='X').exists())

    def test_pasa_el_minimo_exacto(self):
        self.crear('rubros', {'nombre_rubro': 'Té', 'activo': 'on'})
        self.assertTrue(Rubro.objects.filter(nombre_rubro='Té').exists())

    def test_falla_simbolos_prohibidos(self):
        respuesta = self.crear('rubros', {'nombre_rubro': 'Café <script>',
                                          'activo': 'on'})
        self.assertFalse(
            Rubro.objects.filter(nombre_rubro__contains='script').exists())
        self.assertContains(respuesta, 'no admite los símbolos')

    def test_falla_salto_de_linea_en_un_campo_de_una_linea(self):
        with self.assertRaises(ValidationError):
            Rubro(nombre_rubro='Café\nMolido').clean()


# ===========================================================================
#  V-G4 · NÚMEROS
# ===========================================================================
class NumerosTest(BaseMaestros):

    def test_pasa_recargo_cero(self):
        self.crear('medios_pago', {'nombre_medio_pago': 'Cheque',
                                   'recargo_porcentaje': '0'})
        self.assertEqual(
            MedioPago.objects.get(nombre_medio_pago='Cheque')
            .recargo_porcentaje, Decimal('0'))

    def test_pasa_recargo_cien(self):
        self.crear('medios_pago', {'nombre_medio_pago': 'Cheque',
                                   'recargo_porcentaje': '100'})
        self.assertTrue(
            MedioPago.objects.filter(nombre_medio_pago='Cheque').exists())

    def test_falla_recargo_cien_coma_cero_uno(self):
        self.crear('medios_pago', {'nombre_medio_pago': 'Cheque',
                                   'recargo_porcentaje': '100,01'})
        self.assertFalse(
            MedioPago.objects.filter(nombre_medio_pago='Cheque').exists())

    def test_falla_recargo_negativo(self):
        self.crear('medios_pago', {'nombre_medio_pago': 'Cheque',
                                   'recargo_porcentaje': '-1'})
        self.assertFalse(
            MedioPago.objects.filter(nombre_medio_pago='Cheque').exists())

    def test_falla_mas_decimales_de_los_permitidos(self):
        with self.assertRaises(ValidationError):
            MedioPago(nombre_medio_pago='Cheque',
                      recargo_porcentaje=Decimal('1.234')).clean()

    def test_falla_precio_de_venta_cero(self):
        """El precio de venta tiene que ser MAYOR que cero."""
        self.crear('productos', {
            'nombre_producto': 'Café en grano 1 kg',
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': self.rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'KG', 'precio_venta': '0',
            'precio_costo': '0', 'stock_minimo': '0'})
        self.assertFalse(Producto.objects.filter(
            nombre_producto='Café en grano 1 kg').exists())

    def test_falla_stock_minimo_fuera_de_rango(self):
        with self.assertRaises(ValidationError):
            Producto(nombre_producto='X Y', id_rubro=self.rubro, id_marca=self.marca, contenido=Decimal('1'),
                     id_proveedor=self.proveedor,
                     precio_venta=Decimal('10'), precio_costo=Decimal('1'),
                     stock_minimo=100000).clean()

    def test_pasa_stock_minimo_en_el_tope(self):
        producto = Producto(
            nombre_producto='Café tope', id_rubro=self.rubro, id_marca=self.marca, contenido=Decimal('1'),
                     id_proveedor=self.proveedor, precio_venta=Decimal('10'),
            precio_costo=Decimal('1'), stock_minimo=99999)
        producto.clean()           # no levanta


# ===========================================================================
#  V-G5 · FECHAS  (beneficios)
# ===========================================================================
class FechasTest(BaseMaestros):

    def setUp(self):
        super().setUp()
        self.categoria = CategoriaFidelizacion.objects.get(
            nombre_categoria='Gold')

    def datos_beneficio(self, **extra):
        hoy = timezone.localdate()
        datos = {
            'id_categoria': self.categoria.pk,
            'descripcion_beneficio': '10% en cafetería',
            'tipo_beneficio': 'AUTOMATICO',
            'modo_entrega': 'DESCUENTO',
            'porcentaje_descuento': '10',
            'puntos_requeridos': '',
            'vigencia_desde': hoy.strftime('%d/%m/%Y'),
            'vigencia_hasta': '',
        }
        datos.update(extra)
        return datos

    def test_pasa_vigencia_desde_hoy(self):
        self.crear('beneficios', self.datos_beneficio())
        self.assertTrue(Beneficio.objects.filter(
            descripcion_beneficio='10% en cafetería').exists())

    def test_falla_29_de_febrero_de_anio_no_bisiesto(self):
        respuesta = self.crear('beneficios',
                               self.datos_beneficio(vigencia_desde='29/02/2027'))
        self.assertFalse(Beneficio.objects.exists())
        self.assertContains(respuesta, 'fecha')

    def test_falla_anio_fuera_de_rango(self):
        self.crear('beneficios', self.datos_beneficio(vigencia_desde='01/01/0202'))
        self.assertFalse(Beneficio.objects.exists())

    def test_falla_vigencia_hasta_anterior_a_desde(self):
        hoy = timezone.localdate()
        ayer = hoy - timedelta(days=1)
        self.crear('beneficios', self.datos_beneficio(
            vigencia_hasta=ayer.strftime('%d/%m/%Y')))
        self.assertFalse(Beneficio.objects.exists())

    def test_pasa_vigencia_hasta_igual_a_desde(self):
        hoy = timezone.localdate()
        self.crear('beneficios', self.datos_beneficio(
            vigencia_hasta=hoy.strftime('%d/%m/%Y')))
        self.assertTrue(Beneficio.objects.exists())

    def test_falla_vigencia_que_empieza_en_el_pasado(self):
        ayer = timezone.localdate() - timedelta(days=1)
        self.crear('beneficios', self.datos_beneficio(
            vigencia_desde=ayer.strftime('%d/%m/%Y')))
        self.assertFalse(Beneficio.objects.exists())

    def test_falla_vigencia_a_mas_de_dos_anios(self):
        lejos = timezone.localdate() + timedelta(days=800)
        self.crear('beneficios', self.datos_beneficio(
            vigencia_desde=lejos.strftime('%d/%m/%Y')))
        self.assertFalse(Beneficio.objects.exists())


# ===========================================================================
#  V-G8 · DUPLICADOS POR CLAVE NORMALIZADA
# ===========================================================================
class DuplicadosTest(BaseMaestros):

    def test_falla_el_mismo_nombre_con_otra_mayuscula(self):
        respuesta = self.crear('rubros', {'nombre_rubro': 'CAFETERÍA',
                                          'activo': 'on'})
        self.assertEqual(Rubro.objects.filter(
            nombre_rubro__in=['Cafetería', 'CAFETERÍA']).count(), 1)

        errores = self.errores_de_campo(respuesta, 'nombre_rubro')
        self.assertEqual(len(errores), 1, 'el aviso sale repetido')
        self.assertIn('Ya existe un rubro con este nombre', errores[0])
        self.assertIn('Cafetería', errores[0])

    def test_el_duplicado_no_le_borra_lo_que_escribio(self):
        """Antes el redirect vaciaba el formulario y había que tipear todo
        de nuevo. Ahora vuelve con los datos y el error al lado."""
        respuesta = self.crear('rubros', {'nombre_rubro': 'CAFETERÍA',
                                          'descripcion_rubro': 'Lo mío',
                                          'activo': 'on'})
        cuerpo = respuesta.content.decode()
        self.assertIn('value="CAFETERÍA"', cuerpo)
        self.assertIn('value="Lo mío"', cuerpo)

    def test_falla_el_mismo_nombre_sin_acento(self):
        self.crear('rubros', {'nombre_rubro': 'Cafeteria', 'activo': 'on'})
        self.assertFalse(Rubro.objects.filter(nombre_rubro='Cafeteria').exists())

    def test_falla_el_mismo_nombre_con_espacios_alrededor(self):
        """Los espacios de los bordes y los repetidos no hacen otro rubro."""
        self.crear('rubros', {'nombre_rubro': '  Cafetería  ', 'activo': 'on'})
        self.assertEqual(Rubro.objects.filter(
            nombre_rubro__icontains='cafeter').count(), 1)

    def test_pasa_un_nombre_que_solo_se_parece(self):
        """"Cafe tería" con un espacio en el medio es OTRO nombre."""
        self.crear('rubros', {'nombre_rubro': 'Cafe tería', 'activo': 'on'})
        self.assertTrue(
            Rubro.objects.filter(nombre_rubro='Cafe tería').exists())

    def test_el_duplicado_dado_de_baja_ofrece_reactivar(self):
        """D-10: no se crea una fila nueva, se avisa que hay que reactivar."""
        rubro = Rubro.objects.create(nombre_rubro='Panadería',
                                     borrado_logico=True)
        respuesta = self.crear('rubros', {'nombre_rubro': 'panaderia',
                                          'activo': 'on'})
        self.assertEqual(Rubro.objects.filter(
            nombre_rubro__icontains='panader').count(), 1)
        self.assertIn('reactivalo',
                      ' '.join(self.errores_de_campo(respuesta, 'nombre_rubro')))
        self.assertTrue(Rubro.objects.get(pk=rubro.pk).borrado_logico)

    def test_pasa_una_localidad_homonima_en_otra_provincia(self):
        """El único de localidades es DENTRO de la provincia."""
        salta = Provincia.objects.get(nombre_provincia='Salta')
        jujuy = Provincia.objects.get(nombre_provincia='Jujuy')
        Localidad.objects.create(id_provincia=salta, nombre_localidad='El Carmen')

        self.crear('localidades', {'id_provincia': jujuy.pk,
                                   'nombre_localidad': 'El Carmen',
                                   'codigo_postal': '4603'})
        self.assertEqual(
            Localidad.objects.filter(nombre_localidad='El Carmen').count(), 2)

    def test_falla_la_misma_localidad_en_la_misma_provincia(self):
        salta = Provincia.objects.get(nombre_provincia='Salta')
        self.crear('localidades', {'id_provincia': salta.pk,
                                   'nombre_localidad': 'SALTA CAPITAL',
                                   'codigo_postal': '4400'})
        self.assertEqual(Localidad.objects.filter(
            id_provincia=salta,
            nombre_localidad__iexact='salta capital').count(), 1)


# ===========================================================================
#  V-G9 · DOMINIOS CERRADOS
# ===========================================================================
class DominiosCerradosTest(BaseMaestros):

    def test_pasa_una_unidad_de_la_lista(self):
        self.crear('productos', {
            'nombre_producto': 'Café en grano 1 kg',
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': self.rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'KG', 'precio_venta': '12000',
            'precio_costo': '8000', 'stock_minimo': '2'})
        self.assertEqual(Producto.objects.get(
            nombre_producto='Café en grano 1 kg').unidad_medida, 'KG')

    def test_falla_una_unidad_inventada(self):
        self.crear('productos', {
            'nombre_producto': 'Café en grano 1 kg',
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': self.rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'BOLSA', 'precio_venta': '12000',
            'precio_costo': '8000', 'stock_minimo': '2'})
        self.assertFalse(Producto.objects.filter(
            nombre_producto='Café en grano 1 kg').exists())

    def test_falla_un_tipo_de_beneficio_inventado(self):
        categoria = CategoriaFidelizacion.objects.get(nombre_categoria='Gold')
        self.crear('beneficios', {
            'id_categoria': categoria.pk,
            'descripcion_beneficio': 'Beneficio raro',
            'tipo_beneficio': 'MAGICO', 'modo_entrega': 'DESCUENTO',
            'porcentaje_descuento': '10',
            'vigencia_desde': timezone.localdate().strftime('%d/%m/%Y')})
        self.assertFalse(Beneficio.objects.exists())


# ===========================================================================
#  V-G10 · BAJAS Y REACTIVACIÓN
# ===========================================================================
class BajasYReactivacionTest(BaseMaestros):

    def test_baja_aceptada_y_reactivacion(self):
        rubro = Rubro.objects.create(nombre_rubro='Panadería')

        self.client.post(self.url('rubros', 'eliminar', rubro.pk), follow=True)
        rubro.refresh_from_db()
        self.assertTrue(rubro.borrado_logico)

        self.client.post(self.url('rubros', 'reactivar', rubro.pk), follow=True)
        rubro.refresh_from_db()
        self.assertFalse(rubro.borrado_logico)

    def test_la_baja_nunca_borra_la_fila(self):
        rubro = Rubro.objects.create(nombre_rubro='Panadería')
        self.client.post(self.url('rubros', 'eliminar', rubro.pk), follow=True)
        self.assertTrue(Rubro.objects.filter(pk=rubro.pk).exists())

    def test_rubro_con_productos_vigentes_no_se_da_de_baja(self):
        self.producto_de_prueba()
        respuesta = self.client.post(
            self.url('rubros', 'eliminar', self.rubro.pk), follow=True)
        self.rubro.refresh_from_db()
        self.assertFalse(self.rubro.borrado_logico)
        self.assertIn('producto(s) vigente(s)', ' '.join(self.mensajes(respuesta)))

    def test_producto_con_stock_no_se_da_de_baja(self):
        producto = self.dar_stock(self.producto_de_prueba(), 7)
        respuesta = self.client.post(
            self.url('productos', 'eliminar', producto.pk), follow=True)
        producto.refresh_from_db()
        self.assertFalse(producto.borrado_logico)
        self.assertIn('7 unidad(es) en stock',
                      ' '.join(self.mensajes(respuesta)))

    def test_el_efectivo_no_se_da_de_baja(self):
        efectivo = MedioPago.objects.get(nombre_medio_pago='Efectivo')
        respuesta = self.client.post(
            self.url('medios_pago', 'eliminar', efectivo.pk), follow=True)
        efectivo.refresh_from_db()
        self.assertFalse(efectivo.borrado_logico)
        self.assertIn('efectivo', ' '.join(self.mensajes(respuesta)).lower())

    def test_el_ultimo_medio_de_pago_vigente_no_se_da_de_baja(self):
        MedioPago.objects.exclude(nombre_medio_pago='QR').update(
            borrado_logico=True)
        ultimo = MedioPago.objects.get(nombre_medio_pago='QR')
        respuesta = self.client.post(
            self.url('medios_pago', 'eliminar', ultimo.pk), follow=True)
        ultimo.refresh_from_db()
        self.assertFalse(ultimo.borrado_logico)
        self.assertIn('último medio de pago',
                      ' '.join(self.mensajes(respuesta)))

    def test_caja_con_turno_abierto_no_se_da_de_baja(self):
        caja = CajaRegistradora.objects.create(nombre_caja_registradora='Caja Barra')
        TurnoCaja.objects.create(id_caja_registradora=caja,
                            id_usuario_apertura=self.admin,
                            monto_inicial=Decimal('1000'))
        respuesta = self.client.post(
            self.url('cajas_registradoras', 'eliminar', caja.pk), follow=True)
        caja.refresh_from_db()
        self.assertFalse(caja.borrado_logico)
        self.assertIn('turno de caja ABIERTO',
                      ' '.join(self.mensajes(respuesta)))

    def test_la_ultima_caja_vigente_no_se_da_de_baja(self):
        unica = CajaRegistradora.objects.get(nombre_caja_registradora='Caja Mostrador')
        respuesta = self.client.post(
            self.url('cajas_registradoras', 'eliminar', unica.pk), follow=True)
        unica.refresh_from_db()
        self.assertFalse(unica.borrado_logico)
        self.assertIn('última caja registradora', ' '.join(self.mensajes(respuesta)))

    def test_categoria_de_ingreso_no_se_da_de_baja(self):
        bronze = CategoriaFidelizacion.objects.get(puntos_minimos=0)
        respuesta = self.client.post(
            self.url('categorias', 'eliminar', bronze.pk), follow=True)
        bronze.refresh_from_db()
        self.assertFalse(bronze.borrado_logico)
        self.assertIn('categoría de ingreso',
                      ' '.join(self.mensajes(respuesta)))

    def test_categoria_con_clientes_no_se_da_de_baja(self):
        gold = CategoriaFidelizacion.objects.get(nombre_categoria='Gold')
        Cliente.objects.create(id_categoria=gold, apellido_cliente='Pérez',
                               nombre_cliente='Juan', qr_cliente='QR-0001')
        respuesta = self.client.post(
            self.url('categorias', 'eliminar', gold.pk), follow=True)
        gold.refresh_from_db()
        self.assertFalse(gold.borrado_logico)
        self.assertIn('cliente(s) vigente(s)',
                      ' '.join(self.mensajes(respuesta)))

    def test_tipo_de_egreso_con_pendientes_no_se_da_de_baja(self):
        from operaciones.models import Egreso

        tipo = TipoEgreso.objects.get(nombre_tipo_egreso='Servicios')
        Egreso.objects.create(
            id_tipo_egreso=tipo, destinatario='EDESA',
            monto=Decimal('50000'), concepto='Factura de luz',
            fecha_vencimiento=timezone.localdate() + timedelta(days=10),
            estado_pago='PENDIENTE_PAGO')
        respuesta = self.client.post(
            self.url('tipos_egreso', 'eliminar', tipo.pk), follow=True)
        tipo.refresh_from_db()
        self.assertFalse(tipo.borrado_logico)
        self.assertIn('sin acreditar', ' '.join(self.mensajes(respuesta)))

    def test_proveedor_con_orden_abierta_no_se_da_de_baja(self):
        OrdenCompra.objects.create(id_proveedor=self.proveedor,
                                   id_usuario=self.admin,
                                   estado_orden='EMITIDA')
        motivos = self.proveedor.motivos_que_impiden_la_baja()
        self.assertTrue(any('orden' in m for m in motivos))

    def test_proveedor_con_compra_impaga_no_se_da_de_baja(self):
        Compra.objects.create(id_proveedor=self.proveedor,
                              id_usuario=self.admin,
                              numero_factura='A-0001-00000001',
                              total_compra=Decimal('10000'),
                              estado_compra='PENDIENTE')
        motivos = self.proveedor.motivos_que_impiden_la_baja()
        self.assertTrue(any('pagada' in m for m in motivos))

    def test_no_se_reactiva_un_producto_con_el_rubro_de_baja(self):
        producto = self.producto_de_prueba(borrado_logico=True)
        Rubro.objects.filter(pk=self.rubro.pk).update(borrado_logico=True)
        respuesta = self.client.post(
            self.url('productos', 'reactivar', producto.pk), follow=True)
        producto.refresh_from_db()
        self.assertTrue(producto.borrado_logico)
        self.assertIn('rubro', ' '.join(self.mensajes(respuesta)))

    def test_no_se_edita_un_registro_dado_de_baja(self):
        rubro = Rubro.objects.create(nombre_rubro='Panadería',
                                     borrado_logico=True)
        respuesta = self.editar('rubros', rubro.pk,
                                {'nombre_rubro': 'Otro', 'activo': 'on'})
        rubro.refresh_from_db()
        self.assertEqual(rubro.nombre_rubro, 'Panadería')
        self.assertIn('reactivalo antes', ' '.join(self.mensajes(respuesta)))

    def test_provincias_y_localidades_no_tienen_baja(self):
        """El catálogo geográfico sólo tiene alta y modificación."""
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):
            reverse('proveedores:provincias_eliminar', args=[1])
        with self.assertRaises(NoReverseMatch):
            reverse('proveedores:localidades_eliminar', args=[1])


# ===========================================================================
#  V-E · REGLAS PROPIAS DE CADA ENTIDAD
# ===========================================================================
class ReglasPorEntidadTest(BaseMaestros):

    # -- PRODUCTOS -----------------------------------------------------
    def test_pasa_codigo_de_barras_valido(self):
        self.crear('productos', {
            'nombre_producto': 'Yerba Playadito 1 kg',
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': self.rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'KG', 'precio_venta': '6000',
            'precio_costo': '4000', 'stock_minimo': '3',
            'codigo_barras': '7790387011456'})
        self.assertTrue(Producto.objects.filter(
            codigo_barras='7790387011456').exists())

    def test_falla_codigo_de_barras_con_verificador_malo(self):
        respuesta = self.crear('productos', {
            'nombre_producto': 'Yerba Playadito 1 kg',
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': self.rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'KG', 'precio_venta': '6000',
            'precio_costo': '4000', 'stock_minimo': '3',
            'codigo_barras': '7790387011457'})
        self.assertFalse(Producto.objects.exists())
        self.assertContains(respuesta, 'no verifica')

    def test_falla_codigo_de_barras_con_largo_invalido(self):
        with self.assertRaises(ValidationError):
            Producto(nombre_producto='X Y', id_rubro=self.rubro, id_marca=self.marca, contenido=Decimal('1'),
                     id_proveedor=self.proveedor, precio_venta=Decimal('10'),
                     precio_costo=Decimal('1'), stock_minimo=0,
                     codigo_barras='12345').clean()

    def test_vender_bajo_el_costo_exige_confirmacion(self):
        datos = {
            'nombre_producto': 'Café en liquidación',
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': self.rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'UNIDAD', 'precio_venta': '1000',
            'precio_costo': '3000', 'stock_minimo': '0'}
        respuesta = self.crear('productos', datos)
        self.assertFalse(Producto.objects.exists())
        self.assertContains(respuesta, 'menor que el de costo')

        datos['confirmo_bajo_costo'] = 'on'
        self.crear('productos', datos)
        self.assertTrue(Producto.objects.filter(
            nombre_producto='Café en liquidación').exists())

    def test_la_confirmacion_de_bajo_costo_queda_en_la_auditoria(self):
        self.crear('productos', {
            'nombre_producto': 'Café en liquidación',
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': self.rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'UNIDAD', 'precio_venta': '1000',
            'precio_costo': '3000', 'stock_minimo': '0',
            'confirmo_bajo_costo': 'on'})
        registro = LogAuditoria.objects.latest('fecha_hora')
        self.assertIn('por debajo del costo', registro.detalle_operacion)

    def test_no_se_deja_de_controlar_vencimiento_con_lotes_con_stock(self):
        from operaciones.models import Lote

        producto = self.producto_de_prueba(controla_vencimiento=True)
        lote = Lote.objects.create(id_producto=producto, cantidad_inicial=10)
        self.dar_stock(producto, 10, lote=lote)

        producto.controla_vencimiento = False
        with self.assertRaises(ValidationError):
            producto.clean()

    def test_no_se_empieza_a_controlar_vencimiento_con_stock_cargado(self):
        producto = self.dar_stock(
            self.producto_de_prueba(controla_vencimiento=False), 4)
        producto.controla_vencimiento = True
        with self.assertRaises(ValidationError):
            producto.clean()

    # -- MEDIOS DE PAGO ------------------------------------------------
    def test_el_efectivo_no_admite_recargo(self):
        efectivo = MedioPago.objects.get(nombre_medio_pago='Efectivo')
        efectivo.recargo_porcentaje = Decimal('5')
        with self.assertRaises(ValidationError):
            efectivo.clean()

    # -- CATEGORÍAS ----------------------------------------------------
    def test_no_se_sube_el_umbral_de_la_categoria_de_ingreso(self):
        bronze = CategoriaFidelizacion.objects.get(puntos_minimos=0)
        bronze.puntos_minimos = 500
        with self.assertRaises(ValidationError):
            bronze.clean()

    def test_falla_importe_por_punto_cero(self):
        self.crear('categorias', {'nombre_categoria': 'Diamond',
                                  'puntos_minimos': '50000',
                                  'importe_por_punto': '0'})
        self.assertFalse(CategoriaFidelizacion.objects.filter(
            nombre_categoria='Diamond').exists())

    def test_falla_umbral_repetido(self):
        self.crear('categorias', {'nombre_categoria': 'Diamond',
                                  'puntos_minimos': '1000',
                                  'importe_por_punto': '40'})
        self.assertFalse(CategoriaFidelizacion.objects.filter(
            nombre_categoria='Diamond').exists())

    # -- BENEFICIOS ----------------------------------------------------
    def test_descuento_exige_porcentaje_y_rechaza_puntos(self):
        categoria = CategoriaFidelizacion.objects.get(nombre_categoria='Gold')
        base = {
            'id_categoria': categoria.pk,
            'descripcion_beneficio': 'Beneficio de prueba',
            'tipo_beneficio': 'AUTOMATICO', 'modo_entrega': 'DESCUENTO',
            'vigencia_desde': timezone.localdate().strftime('%d/%m/%Y')}

        self.crear('beneficios', dict(base, porcentaje_descuento=''))
        self.assertFalse(Beneficio.objects.exists())

        self.crear('beneficios', dict(base, porcentaje_descuento='15'))
        self.assertTrue(Beneficio.objects.exists())

    def test_canjeable_exige_puntos(self):
        categoria = CategoriaFidelizacion.objects.get(nombre_categoria='Gold')
        self.crear('beneficios', {
            'id_categoria': categoria.pk,
            'descripcion_beneficio': 'Café gratis al canjear',
            'tipo_beneficio': 'CANJEABLE', 'modo_entrega': 'DESCUENTO',
            'porcentaje_descuento': '100', 'puntos_requeridos': '',
            'vigencia_desde': timezone.localdate().strftime('%d/%m/%Y')})
        self.assertFalse(Beneficio.objects.exists())

    def test_no_se_superponen_dos_automaticos_de_descuento(self):
        categoria = CategoriaFidelizacion.objects.get(nombre_categoria='Gold')
        hoy = timezone.localdate()
        Beneficio.objects.create(
            id_categoria=categoria, descripcion_beneficio='Primero',
            tipo_beneficio='AUTOMATICO', modo_entrega='DESCUENTO',
            porcentaje_descuento=Decimal('10'), vigencia_desde=hoy)

        segundo = Beneficio(
            id_categoria=categoria, descripcion_beneficio='Segundo',
            tipo_beneficio='AUTOMATICO', modo_entrega='DESCUENTO',
            porcentaje_descuento=Decimal('20'), vigencia_desde=hoy)
        with self.assertRaises(ValidationError):
            segundo.clean()

    def test_especie_exige_receta(self):
        categoria = CategoriaFidelizacion.objects.get(nombre_categoria='Gold')
        beneficio = Beneficio.objects.create(
            id_categoria=categoria, descripcion_beneficio='Alfajor de regalo',
            tipo_beneficio='CANJEABLE', modo_entrega='ESPECIE',
            puntos_requeridos=100, vigencia_desde=timezone.localdate())
        with self.assertRaises(ValidationError):
            beneficio.validar_receta()

        BeneficioProducto.objects.create(
            id_beneficio=beneficio, id_producto=self.producto_de_prueba(),
            cantidad=1)
        beneficio.validar_receta()      # ya no levanta

    def test_la_receta_no_admite_un_producto_dado_de_baja(self):
        categoria = CategoriaFidelizacion.objects.get(nombre_categoria='Gold')
        beneficio = Beneficio.objects.create(
            id_categoria=categoria, descripcion_beneficio='Alfajor de regalo',
            tipo_beneficio='CANJEABLE', modo_entrega='ESPECIE',
            puntos_requeridos=100, vigencia_desde=timezone.localdate())
        producto = self.producto_de_prueba(borrado_logico=True)
        with self.assertRaises(ValidationError):
            BeneficioProducto(id_beneficio=beneficio, id_producto=producto,
                              cantidad=1).clean()

    def test_la_cantidad_de_la_receta_va_de_1_a_99(self):
        categoria = CategoriaFidelizacion.objects.get(nombre_categoria='Gold')
        beneficio = Beneficio.objects.create(
            id_categoria=categoria, descripcion_beneficio='Alfajor de regalo',
            tipo_beneficio='CANJEABLE', modo_entrega='ESPECIE',
            puntos_requeridos=100, vigencia_desde=timezone.localdate())
        renglon = BeneficioProducto(id_beneficio=beneficio,
                                    id_producto=self.producto_de_prueba())
        renglon.cantidad = 99
        renglon.clean()                 # el tope pasa
        renglon.cantidad = 100
        with self.assertRaises(ValidationError):
            renglon.clean()

    # -- CAJAS REGISTRADORAS -----------------------------------------------
    def test_la_caja_registradora_pide_al_menos_tres_caracteres(self):
        self.crear('cajas_registradoras', {'nombre_caja_registradora': 'C1'})
        self.assertFalse(CajaRegistradora.objects.filter(nombre_caja_registradora='C1').exists())
        self.crear('cajas_registradoras', {'nombre_caja_registradora': 'C12'})
        self.assertTrue(CajaRegistradora.objects.filter(nombre_caja_registradora='C12').exists())

    # -- LOCALIDADES ---------------------------------------------------
    def test_codigo_postal_clasico_y_cpa(self):
        salta = Provincia.objects.get(nombre_provincia='Salta')
        self.crear('localidades', {'id_provincia': salta.pk,
                                   'nombre_localidad': 'Rosario de Lerma',
                                   'codigo_postal': '4405'})
        self.assertEqual(Localidad.objects.get(
            nombre_localidad='Rosario de Lerma').codigo_postal, '4405')

        self.crear('localidades', {'id_provincia': salta.pk,
                                   'nombre_localidad': 'Cerrillos',
                                   'codigo_postal': 'a4403xab'})
        self.assertEqual(Localidad.objects.get(
            nombre_localidad='Cerrillos').codigo_postal, 'A4403XAB')

    def test_falla_codigo_postal_invalido(self):
        salta = Provincia.objects.get(nombre_provincia='Salta')
        self.crear('localidades', {'id_provincia': salta.pk,
                                   'nombre_localidad': 'Chicoana',
                                   'codigo_postal': '44'})
        self.assertFalse(
            Localidad.objects.filter(nombre_localidad='Chicoana').exists())

    # -- PROVINCIAS ----------------------------------------------------
    def test_el_alta_de_provincia_pide_confirmacion(self):
        respuesta = self.crear('provincias',
                               {'nombre_provincia': 'Nueva Jurisdicción'})
        self.assertFalse(Provincia.objects.filter(
            nombre_provincia='Nueva Jurisdicción').exists())
        self.assertContains(respuesta, 'Confirmá')

        self.crear('provincias', {'nombre_provincia': 'Nueva Jurisdicción',
                                  'confirmo_alta': 'on'})
        self.assertTrue(Provincia.objects.filter(
            nombre_provincia='Nueva Jurisdicción').exists())

    # -- PROVEEDORES (pantalla existente llevada a las reglas nuevas) ---
    def test_cuit_con_verificador_invalido(self):
        proveedor = Proveedor(nombre_proveedor='Mal CUIT',
                              cuit_proveedor='30-11111111-1')
        with self.assertRaises(ValidationError):
            proveedor.clean()

    def test_cuit_con_prefijo_invalido(self):
        proveedor = Proveedor(nombre_proveedor='Prefijo raro',
                              cuit_proveedor='99-12345678-1')
        with self.assertRaises(ValidationError):
            proveedor.clean()

    def test_el_cuit_se_guarda_con_guiones(self):
        proveedor = Proveedor(nombre_proveedor='Sin guiones',
                              cuit_proveedor='30998877667')
        proveedor.clean()
        self.assertEqual(proveedor.cuit_proveedor, CUIT_LIBRE)

    def test_domicilio_sin_localidad_es_error(self):
        proveedor = Proveedor(nombre_proveedor='Con domicilio',
                              cuit_proveedor=CUIT_OTRO,
                              domicilio='Av. Belgrano 1234')
        with self.assertRaises(ValidationError):
            proveedor.clean()

    def test_telefono_fuera_de_rango(self):
        proveedor = Proveedor(nombre_proveedor='Tel corto',
                              cuit_proveedor=CUIT_OTRO,
                              telefono_proveedor='12345')
        with self.assertRaises(ValidationError):
            proveedor.clean()


# ===========================================================================
#  V-G6 / V-G7 / V-G13 · POST MANIPULADO
# ===========================================================================
class PostManipuladoTest(BaseMaestros):

    def test_no_se_asocia_un_rubro_dado_de_baja(self):
        rubro = Rubro.objects.create(nombre_rubro='Panadería',
                                     borrado_logico=True)
        self.crear('productos', {
            'nombre_producto': 'Factura de grasa',
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'UNIDAD', 'precio_venta': '800',
            'precio_costo': '400', 'stock_minimo': '0'})
        self.assertFalse(Producto.objects.exists())

    def test_no_se_asocia_un_rubro_inactivo(self):
        Rubro.objects.filter(pk=self.rubro.pk).update(activo=False)
        self.crear('productos', {
            'nombre_producto': 'Café con rubro inactivo',
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': self.rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'UNIDAD', 'precio_venta': '800',
            'precio_costo': '400', 'stock_minimo': '0'})
        self.assertFalse(Producto.objects.exists())

    def test_no_se_asocia_un_proveedor_dado_de_baja(self):
        Proveedor.objects.filter(pk=self.proveedor.pk).update(
            borrado_logico=True)
        self.crear('productos', {
            'nombre_producto': 'Café sin proveedor',
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': self.rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'UNIDAD', 'precio_venta': '800',
            'precio_costo': '400', 'stock_minimo': '0'})
        self.assertFalse(Producto.objects.exists())

    def test_se_ignora_el_stock_actual_que_venga_en_el_post(self):
        self.crear('productos', {
            'nombre_producto': 'Café con stock trucho',
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': self.rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'UNIDAD', 'precio_venta': '800',
            'precio_costo': '400', 'stock_minimo': '0',
            'stock_actual': '9999'})
        self.assertEqual(Producto.objects.get(
            nombre_producto='Café con stock trucho').stock_actual, 0)

    def test_se_ignora_el_borrado_logico_que_venga_en_el_post(self):
        self.crear('rubros', {'nombre_rubro': 'Panadería', 'activo': 'on',
                              'borrado_logico': 'True'})
        self.assertFalse(
            Rubro.objects.get(nombre_rubro='Panadería').borrado_logico)

    def test_se_ignora_el_precio_de_costo_al_editar(self):
        """RN-4.2.4: después del alta, el costo lo actualiza la compra."""
        producto = self.producto_de_prueba()
        self.editar('productos', producto.pk, {
            'nombre_producto': producto.nombre_producto,
            'id_marca': self.marca.pk, 'contenido': '1',
            'id_rubro': self.rubro.pk, 'id_proveedor': self.proveedor.pk,
            'unidad_medida': 'UNIDAD', 'precio_venta': '6000',
            'precio_costo': '1', 'stock_minimo': '5'})
        producto.refresh_from_db()
        self.assertEqual(producto.precio_costo, Decimal('3000.00'))
        self.assertEqual(producto.precio_venta, Decimal('6000.00'))

    def test_un_id_inexistente_da_404(self):
        respuesta = self.client.post(self.url('rubros', 'editar', 999999))
        self.assertEqual(respuesta.status_code, 404)


# ===========================================================================
#  V-G11 · EDICIÓN CONCURRENTE
# ===========================================================================
class EdicionConcurrenteTest(BaseMaestros):

    def test_se_rechaza_si_otro_usuario_modifico_mientras_tanto(self):
        rubro = Rubro.objects.create(nombre_rubro='Panadería')
        version_vieja = rubro.fecha_modificacion.isoformat()

        # Otro usuario guarda primero: la fecha de modificación cambia.
        Rubro.objects.filter(pk=rubro.pk).update(
            nombre_rubro='Panadería artesanal',
            fecha_modificacion=timezone.now() + timedelta(seconds=5))

        respuesta = self.editar('rubros', rubro.pk, {
            'nombre_rubro': 'Panadería industrial', 'activo': 'on',
            'version_leida': version_vieja})

        rubro.refresh_from_db()
        self.assertEqual(rubro.nombre_rubro, 'Panadería artesanal')
        self.assertContains(respuesta, 'Otro usuario modificó')

    def test_pasa_cuando_nadie_toco_el_registro(self):
        rubro = Rubro.objects.create(nombre_rubro='Panadería')
        self.editar('rubros', rubro.pk, {
            'nombre_rubro': 'Panadería artesanal', 'activo': 'on',
            'version_leida': rubro.fecha_modificacion.isoformat()})
        rubro.refresh_from_db()
        self.assertEqual(rubro.nombre_rubro, 'Panadería artesanal')


# ===========================================================================
#  PERMISOS
# ===========================================================================
class PermisosTest(BaseMaestros):

    def setUp(self):
        super().setUp()
        self.client.logout()
        self.entrar(self.operador)

    def test_el_operador_no_ve_el_menu_de_datos_maestros(self):
        respuesta = self.client.get(reverse('mi_perfil'))
        self.assertNotContains(respuesta, 'Inventario y Menú')
        self.assertNotContains(respuesta, 'Ajustes del Local')

    def test_el_operador_no_entra_por_url_directa(self):
        respuesta = self.client.get(self.url('rubros', 'lista'), follow=True)
        self.assertIn('Acceso denegado', ' '.join(self.mensajes(respuesta)))

    def test_el_operador_no_puede_crear_aunque_postee(self):
        self.crear('rubros', {'nombre_rubro': 'Panadería', 'activo': 'on'})
        self.assertFalse(Rubro.objects.filter(nombre_rubro='Panadería').exists())

    def test_el_operador_no_puede_dar_de_baja(self):
        rubro = Rubro.objects.create(nombre_rubro='Panadería')
        self.client.post(self.url('rubros', 'eliminar', rubro.pk), follow=True)
        rubro.refresh_from_db()
        self.assertFalse(rubro.borrado_logico)

    def test_el_administrador_si_ve_el_menu(self):
        self.client.logout()
        self.entrar(self.admin)
        respuesta = self.client.get(self.url('rubros', 'lista'))
        # El menú se agrupa por área de trabajo, con el vocabulario del
        # negocio: "Inventario y Menú", no "Datos maestros" (que es el
        # nombre del proceso N° 10 del DFD, no algo que el usuario entienda).
        self.assertContains(respuesta, 'Inventario y Menú')

    def test_con_el_permiso_de_ver_entra_pero_no_crea(self):
        PerfilPermiso.objects.create(
            perfil=self.perfil_operador,
            permiso=Permiso.objects.get(codigo='ver_rubros'))
        respuesta = self.client.get(self.url('rubros', 'lista'))
        self.assertEqual(respuesta.status_code, 200)
        # Sin 'crear_rubro' el botón no se dibuja.
        self.assertNotContains(respuesta, 'modalCrear')


# ===========================================================================
#  LA BASE COMO ÚLTIMA BARRERA (capa 3)
# ===========================================================================
class LaBaseRechazaSolaTest(BaseMaestros):
    """Inserciones por el ORM, salteando formulario y ``clean()``.

    Es la prueba de que la tercera capa existe de verdad: aunque alguien
    escriba ``Rubro.objects.create(...)`` desde el shell, la base no deja
    entrar un dato inconsistente.
    """

    def test_check_de_texto_no_vacio(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Rubro.objects.create(nombre_rubro='   ')

    def test_unico_normalizado_de_rubros(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Rubro.objects.create(nombre_rubro='CAFETERIA')

    def test_check_de_recargo_fuera_de_rango(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            MedioPago.objects.create(nombre_medio_pago='Cheque',
                                     recargo_porcentaje=Decimal('150'))

    def test_check_de_unidad_de_medida(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Producto.objects.create(
                nombre_producto='Unidad inventada', id_rubro=self.rubro, id_marca=self.marca, contenido=Decimal('1'),
                     id_proveedor=self.proveedor, unidad_medida='BOLSA',
                precio_venta=Decimal('10'), precio_costo=Decimal('1'))

    def test_check_del_codigo_de_barras(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Producto.objects.create(
                nombre_producto='Código corto', id_rubro=self.rubro, id_marca=self.marca, contenido=Decimal('1'),
                     id_proveedor=self.proveedor, codigo_barras='123',
                precio_venta=Decimal('10'), precio_costo=Decimal('1'))

    def test_check_de_la_caja_de_tres_caracteres(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CajaRegistradora.objects.create(nombre_caja_registradora='C1')

    def test_check_de_importe_por_punto_positivo(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CategoriaFidelizacion.objects.create(
                nombre_categoria='Diamond', puntos_minimos=50000,
                importe_por_punto=Decimal('0'))

    def test_unico_de_puntos_minimos(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CategoriaFidelizacion.objects.create(
                nombre_categoria='Diamond', puntos_minimos=0,
                importe_por_punto=Decimal('40'))

    def test_check_de_coherencia_del_beneficio(self):
        """DESCUENTO sin porcentaje: lo frena ck_beneficios_que_entrega."""
        categoria = CategoriaFidelizacion.objects.get(nombre_categoria='Gold')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Beneficio.objects.create(
                id_categoria=categoria, descripcion_beneficio='Incoherente',
                tipo_beneficio='AUTOMATICO', modo_entrega='DESCUENTO',
                porcentaje_descuento=None,
                vigencia_desde=timezone.localdate())

    def test_unico_de_cuit(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Proveedor.objects.create(nombre_proveedor='Otro',
                                     cuit_proveedor=CUIT_VALIDO)

    def test_check_del_formato_de_cuit(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Proveedor.objects.create(nombre_proveedor='Sin guiones',
                                     cuit_proveedor='30712345671')

    def test_el_borrado_fisico_esta_prohibido(self):
        """RN-8.1.1: el DELETE no corre, por ninguno de los dos caminos.

        Lo corta el mixin `SinBajaFisica`, así que la excepción es un
        ValidationError de Django.

        Se prueban los dos caminos: `QuerySet.delete()` emite un DELETE
        masivo y NO pasa por el `delete()` de la instancia, así que
        cubrir sólo uno dejaría la puerta abierta.
        """
        rubro = Rubro.objects.create(nombre_rubro='Panadería')

        with self.assertRaises(ValidationError):
            rubro.delete()

        with self.assertRaises(ValidationError):
            Rubro.objects.filter(pk=rubro.pk).delete()

        self.assertTrue(Rubro.objects.filter(pk=rubro.pk).exists(),
                        'la fila tiene que seguir ahí')

    def test_el_pasaje_a_historico_si_puede_borrar(self):
        """La única excepción: copiar al histórico y sacar del libro vivo."""
        from cafeto import contexto

        rubro = Rubro.objects.create(nombre_rubro='Panadería')
        with contexto.pasaje_historico():
            rubro.delete()
        self.assertFalse(Rubro.objects.filter(pk=rubro.pk).exists())

    def test_el_sello_registra_quien_modifico(self):
        """DD-17: el sello lo pone Python, leyendo el contexto del pedido.

        El usuario viaja en un ContextVar que fija el middleware, y el
        sello lo ponen `save()` y `update()` vía
        `ConSelloDeModificacion`.

        Se prueban los DOS caminos: `QuerySet.update()` no pasa por
        `save()`, así que si sólo estuviera cubierto uno, una modificación
        masiva dejaría la fila mintiendo sobre quién la tocó.
        """
        from cafeto import contexto

        rubro = Rubro.objects.create(nombre_rubro='Panadería')

        with contexto.usar(self.admin):
            Rubro.objects.filter(pk=rubro.pk).update(
                nombre_rubro='Panadería artesanal')
        rubro.refresh_from_db()
        self.assertEqual(rubro.usuario_modificacion_id, self.admin.pk,
                         'update() tiene que sellar')

        otro = Rubro.objects.create(nombre_rubro='Kiosco')
        with contexto.usar(self.admin):
            otro.nombre_rubro = 'Kiosco y golosinas'
            otro.save()
        otro.refresh_from_db()
        self.assertEqual(otro.usuario_modificacion_id, self.admin.pk,
                         'save() tiene que sellar')

    def test_sin_usuario_en_contexto_el_sello_queda_nulo(self):
        """El shell, un comando o una migración no son "alguien"."""
        rubro = Rubro.objects.create(nombre_rubro='Panadería')
        rubro.nombre_rubro = 'Panadería artesanal'
        rubro.save()
        rubro.refresh_from_db()
        self.assertIsNone(rubro.usuario_modificacion_id)
        self.assertIsNotNone(rubro.fecha_modificacion)
