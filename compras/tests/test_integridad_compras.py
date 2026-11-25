"""CMP-01 a CMP-05: Compras no inflan importes, no imputan al proveedor
reservado ni cuentan dos veces lo que se debe.

    CMP-01  el alta de un producto desde la factura conserva los importes
    CMP-02  el «Proveedor General» no recibe órdenes, compras ni pagos
    CMP-03  un egreso pendiente no se anota sobre una compra, ni cuenta
            como pagado
    CMP-04  un alta de egreso rechazada vuelve a la misma pantalla
    CMP-05  conciliar pide un resultado válido y consume su token
    V-G11   el formulario de proveedores lleva el bloqueo optimista
"""
import json
from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras import egresos, servicios
from compras.forms import ProveedorForm
from compras.models import Compra, Egreso, OrdenCompra, Proveedor, TipoEgreso
from nucleo import idempotencia
from nucleo.formularios import FormularioMaestro
from nucleo.tests import fabrica as f
from stock.models import Producto
from stock.reservados import proveedor_general


class Base(TestCase):

    def setUp(self):
        self.admin = f.titular()
        self.prov = f.proveedor()
        self.yerba = f.producto('Yerba', prov=self.prov, precio='3000', costo='2000')
        self.general = proveedor_general()
        self.tipo = TipoEgreso.objects.get_or_create(nombre_tipo_egreso='Proveedores')[0]

    def compra(self, numero=1):
        return servicios.recepcionar(
            self.prov, self.admin, 'FACTURA_B', 1, numero,
            [servicios.RenglonRecibido(self.yerba, D('1'), D('2000'), [])])

    def alta(self, **datos):
        f.entrar(self.client, self.admin)
        return self.client.post(reverse('compras:alta_producto_factura'), json.dumps(datos),
                                content_type='application/json').json()


# ===========================================================================
#  CMP-01
# ===========================================================================
class AltaDeProductoDesdeLaFacturaTest(Base):

    def test_los_importes_con_decimales_no_se_inflan(self):
        r = self.alta(nombre='Galletitas nuevas', codigo='7790000000017', costo='1250.50',
                      proveedor=str(self.prov.pk))
        self.assertTrue(r['ok'], r)
        producto = Producto.objects.get(pk=r['producto'])
        self.assertEqual(producto.precio_costo, D('1250.50'))
        self.assertEqual(producto.precio_venta, D('1750.70'))       # costo + 40 %
        self.assertEqual(producto.alicuota_iva, D('21.0'))
        self.assertEqual((producto.tipo_producto, producto.unidad_medida), ('REVENTA', 'UNIDAD'))

    def test_acepta_la_alicuota_en_las_tres_formas(self):
        for texto, esperado, codigo in (('10.5', D('10.5'), '7790000000024'),
                                        ('10,5', D('10.5'), '7790000000031'),
                                        ('0', D('0.0'), '7790000000048'),
                                        (21, D('21.0'), '7790000000055')):
            with self.subTest(alicuota=texto):
                r = self.alta(nombre=f'Producto {codigo}', codigo=codigo, costo='100',
                              alicuota_iva=texto, proveedor=str(self.prov.pk))
                self.assertTrue(r['ok'], r)
                self.assertEqual(Producto.objects.get(pk=r['producto']).alicuota_iva, esperado)

    def test_una_alicuota_que_no_existe_se_rechaza(self):
        r = self.alta(nombre='Producto raro', codigo='7790000000062', costo='100', alicuota_iva='27')
        self.assertFalse(r['ok'])
        self.assertIn('IVA', r['error'])

    def test_se_puede_dar_de_alta_un_insumo_por_kilo(self):
        r = self.alta(nombre='Harina 000', codigo='7790000000079', costo='900,50',
                      tipo_producto='INSUMO', unidad_medida='KG', proveedor=str(self.prov.pk))
        self.assertTrue(r['ok'], r)
        producto = Producto.objects.get(pk=r['producto'])
        self.assertEqual((producto.tipo_producto, producto.unidad_medida), ('INSUMO', 'KG'))
        self.assertEqual(producto.precio_costo, D('900.50'))
        self.assertEqual(producto.precio_venta, D('0'))

    def test_tipo_y_unidad_fuera_de_la_lista_se_rechazan(self):
        self.assertFalse(self.alta(nombre='Combo raro', costo='10', tipo_producto='COMBO')['ok'])
        self.assertFalse(self.alta(nombre='Cosa rara', costo='10', unidad_medida='ML')['ok'])

    def test_un_codigo_repetido_se_avisa_sin_romper(self):
        self.alta(nombre='Primera', codigo='7790000000086', costo='10')
        r = self.alta(nombre='Segunda', codigo='7790000000086', costo='10')
        self.assertFalse(r['ok'])
        self.assertIn('7790000000086', r['error'])


# ===========================================================================
#  CMP-02
# ===========================================================================
class ProveedorGeneralTest(Base):

    def test_no_se_le_emite_una_orden(self):
        with self.assertRaisesMessage(ValidationError, 'reservado'):
            servicios.emitir_orden(self.general, self.admin, [
                {'producto': self.yerba, 'cantidad': 1, 'precio_unitario': D('2000')}])
        orden = OrdenCompra(id_proveedor=self.general, id_usuario=self.admin)
        with self.assertRaises(ValidationError) as error:
            orden.full_clean(validate_constraints=False)
        self.assertIn('id_proveedor', error.exception.message_dict)

    def test_no_se_le_carga_una_compra(self):
        with self.assertRaises(ValidationError):
            servicios.recepcionar(
                self.general, self.admin, 'FACTURA_B', 1, 7,
                [servicios.RenglonRecibido(self.yerba, D('1'), D('2000'), [])])
        self.assertFalse(Compra.objects.exists())
        compra = Compra(id_proveedor=self.general, id_usuario=self.admin)
        with self.assertRaises(ValidationError) as error:
            compra.full_clean(validate_constraints=False, validate_unique=False)
        self.assertIn('id_proveedor', error.exception.message_dict)

    def test_no_se_le_paga(self):
        with self.assertRaisesMessage(ValidationError, 'reservado'):
            egresos.registrar_obligacion(self.tipo, 'Algo a pagar', D('500'),
                                         timezone.localdate(), proveedor=self.general)
        egreso = Egreso(id_tipo_egreso=self.tipo, id_proveedor=self.general, monto=D('500'),
                        concepto='Algo', estado_pago='PENDIENTE_PAGO')
        with self.assertRaises(ValidationError) as error:
            egreso.full_clean(validate_constraints=False)
        self.assertIn('id_proveedor', error.exception.message_dict)

    def test_las_pantallas_no_lo_ofrecen(self):
        f.entrar(self.client, self.admin)
        for nombre in ('compras:egresos', 'compras:nueva_orden', 'compras:recepcion'):
            with self.subTest(pantalla=nombre):
                proveedores = list(self.client.get(reverse(nombre)).context['proveedores'])
                self.assertIn(self.prov, proveedores)
                self.assertNotIn(self.general, proveedores)

    def test_un_proveedor_real_sigue_funcionando(self):
        self.assertTrue(self.compra().pk)


# ===========================================================================
#  CMP-03
# ===========================================================================
class EgresoPendienteSobreUnaCompraTest(Base):

    def test_no_se_anota_una_obligacion_sobre_una_compra(self):
        compra = self.compra()
        with self.assertRaisesMessage(ValidationError, 'ya es la obligación de pago'):
            egresos.registrar_obligacion(self.tipo, 'Pago de la factura', D('2000'),
                                         timezone.localdate(), proveedor=self.prov, compra=compra)
        self.assertFalse(Egreso.objects.exists())

    def test_una_obligacion_sin_compra_sigue_permitida(self):
        egreso = egresos.registrar_obligacion(self.tipo, 'Luz de noviembre', D('500'),
                                              timezone.localdate(), destinatario='EDESA')
        self.assertEqual(egreso.estado_pago, 'PENDIENTE_PAGO')

    def test_lo_pendiente_no_cuenta_como_pagado(self):
        compra = self.compra()
        # Un pendiente heredado de antes de la regla: no es plata pagada.
        Egreso.objects.create(
            id_tipo_egreso=self.tipo, id_proveedor=self.prov, id_compra=compra, monto=D('2000'),
            concepto='Pendiente viejo', fecha_vencimiento=timezone.localdate(),
            estado_pago='PENDIENTE_PAGO')
        anotada = Compra.con_pagado(Compra.objects.filter(pk=compra.pk)).get()
        self.assertEqual(anotada.pagado, D('0'))
        self.assertEqual(Compra.objects.get(pk=compra.pk).pagado, D('0'))
        self.assertEqual(egresos.compras_por_pagar().get(pk=compra.pk).falta, D('2000'))

    def test_lo_pagado_si_cuenta(self):
        compra = self.compra()
        egresos.registrar_egreso(self.tipo, f.medio('Transferencia'), D('800'), 'Pago parcial',
                                 proveedor=self.prov, compra=compra)
        anotada = Compra.con_pagado(Compra.objects.filter(pk=compra.pk)).get()
        self.assertEqual(anotada.pagado, D('800'))


# ===========================================================================
#  CMP-04
# ===========================================================================
class AltaDeEgresoRechazadaTest(Base):

    def test_vuelve_a_la_misma_pantalla_con_lo_tipeado(self):
        f.entrar(self.client, self.admin)
        token = self.client.get(reverse('compras:egresos')).context['token']
        respuesta = self.client.post(reverse('compras:registrar_egreso'), {
            'modo': 'anotar', 'token_operacion': token, 'tipo': self.tipo.pk,
            'concepto': 'Luz de noviembre', 'monto': '0', 'destinatario': 'EDESA'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, 'compras/egresos.html')
        contexto = respuesta.context
        self.assertTrue(contexto['abrir_modal_egreso'])
        self.assertEqual(contexto['datos']['concepto'], 'Luz de noviembre')
        self.assertEqual(contexto['datos']['destinatario'], 'EDESA')
        self.assertIn('mayor que cero', contexto['error'])
        for clave in ('tipos', 'medios', 'proveedores', 'turno', 'token', 'hoy'):
            self.assertIn(clave, contexto)
        self.assertEqual(contexto['token'], token)
        self.assertFalse(Egreso.objects.exists())

    def test_un_alta_valida_sigue_redirigiendo(self):
        f.entrar(self.client, self.admin)
        token = self.client.get(reverse('compras:egresos')).context['token']
        respuesta = self.client.post(reverse('compras:registrar_egreso'), {
            'modo': 'anotar', 'token_operacion': token, 'tipo': self.tipo.pk,
            'concepto': 'Luz de noviembre', 'monto': '500', 'destinatario': 'EDESA',
            'vence': timezone.localdate().isoformat()})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(Egreso.objects.count(), 1)


# ===========================================================================
#  CMP-05
# ===========================================================================
class ConciliarTest(Base):

    def setUp(self):
        super().setUp()
        self.egreso = egresos.registrar_egreso(
            self.tipo, f.medio('Transferencia'), D('800'), 'Pago a proveedor',
            proveedor=self.prov)
        f.entrar(self.client, self.admin)

    def conciliar(self, **datos):
        return self.client.post(reverse('compras:conciliar_egreso', args=[self.egreso.pk]), datos)

    def estado(self):
        return Egreso.objects.get(pk=self.egreso.pk).estado_pago

    def test_un_resultado_desconocido_no_rechaza_el_pago(self):
        for resultado in ('', 'cualquiera', 'ACREDITADO'):
            with self.subTest(resultado=resultado):
                self.conciliar(resultado=resultado)
                self.assertEqual(self.estado(), 'PENDIENTE_ACREDITACION')

    def test_acreditado_y_rechazado_se_concilian(self):
        self.conciliar(resultado='rechazado')
        self.assertEqual(self.estado(), 'RECHAZADO')

    def test_el_token_se_consume_una_sola_vez(self):
        token = self.client.get(reverse('compras:egresos')).context['token']
        self.conciliar(resultado='acreditado', token_operacion=token)
        self.assertEqual(self.estado(), 'ACREDITADO')
        self.assertNotIn(token, self.client.session.get(idempotencia.CLAVE, []))
        respuesta = self.conciliar(resultado='acreditado', token_operacion=token)
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.estado(), 'ACREDITADO')

    def test_un_token_ajeno_no_concilia(self):
        self.conciliar(resultado='acreditado', token_operacion='token-que-nadie-emitio')
        self.assertEqual(self.estado(), 'PENDIENTE_ACREDITACION')

    def test_si_la_regla_falla_el_token_se_devuelve(self):
        token = self.client.get(reverse('compras:egresos')).context['token']
        self.conciliar(resultado='acreditado', token_operacion=token, fecha='2999-01-01')
        self.assertEqual(self.estado(), 'PENDIENTE_ACREDITACION')
        self.assertIn(token, self.client.session.get(idempotencia.CLAVE, []))


# ===========================================================================
#  V-G11: el bloqueo optimista de los proveedores
# ===========================================================================
class ProveedorFormTest(Base):

    def datos(self, **extra):
        return {'nombre_proveedor': self.prov.nombre_proveedor, 'cuit_proveedor': self.prov.cuit_proveedor,
                'condicion_pago': 'CONTADO', 'tipo_proveedor': 'MERCADERIA', **extra}

    def test_es_un_formulario_maestro(self):
        self.assertTrue(issubclass(ProveedorForm, FormularioMaestro))
        self.assertIn('version_leida', ProveedorForm(instance=self.prov).fields)

    def test_si_otro_lo_modifico_no_se_pisa(self):
        leida = ProveedorForm(instance=self.prov).fields['version_leida'].initial
        Proveedor.objects.filter(pk=self.prov.pk).update(
            fecha_modificacion=timezone.now() + timedelta(seconds=5))
        formulario = ProveedorForm(self.datos(telefono_proveedor='3874123456', version_leida=leida),
                                   instance=Proveedor.objects.get(pk=self.prov.pk))
        self.assertFalse(formulario.is_valid())
        self.assertIn('Otro usuario modificó', ' '.join(formulario.non_field_errors()))

    def test_sin_cambios_ajenos_se_guarda(self):
        leida = ProveedorForm(instance=self.prov).fields['version_leida'].initial
        formulario = ProveedorForm(self.datos(telefono_proveedor='3874123456', version_leida=leida),
                                   instance=self.prov)
        self.assertTrue(formulario.is_valid(), formulario.errors)
