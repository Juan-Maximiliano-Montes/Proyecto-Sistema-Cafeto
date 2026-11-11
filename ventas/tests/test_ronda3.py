"""Tercera ronda de pruebas destructivas (DD-79): datos que rompen la lógica."""
from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras import egresos, servicios as compras
from compras.models import TipoEgreso
from fidelizacion.forms import ClienteForm
from fidelizacion.models import CategoriaFidelizacion
from nucleo.tests import fabrica
from nucleo.validaciones import exigir_dni_creible, validar_telefono
from stock import precios, servicios as stock
from ventas import caja
from ventas.models import MedioPago, TurnoCaja


class OrdenDeCompraTest(TestCase):
    def setUp(self):
        self.usuario = fabrica.usuario('30111222', 'Administrador')
        self.proveedor = fabrica.proveedor()
        self.agua = fabrica.producto('Agua de prueba', prov=self.proveedor)

    def _item(self, producto):
        return {'producto': producto, 'cantidad': D('1'), 'precio_unitario': D('10')}

    def test_el_mismo_producto_dos_veces_se_avisa_y_no_deja_orden_a_medias(self):
        from compras.models import OrdenCompra
        with self.assertRaisesMessage(ValidationError, 'dos veces'):
            compras.emitir_orden(self.proveedor, self.usuario,
                                 [self._item(self.agua), self._item(self.agua)])
        self.assertEqual(OrdenCompra.objects.count(), 0)

    def test_lo_que_se_elabora_en_el_local_no_se_pide(self):
        cafe = fabrica.producto('Café de prueba', tipo='ELABORADO')
        with self.assertRaisesMessage(ValidationError, 'se elabora en el local'):
            compras.emitir_orden(self.proveedor, self.usuario, [self._item(cafe)])


class VencimientosLejanosTest(TestCase):
    def setUp(self):
        self.usuario = fabrica.usuario('30111223', 'Administrador')
        self.proveedor = fabrica.proveedor()
        self.hoy = timezone.localdate()

    def test_un_lote_que_vence_en_treinta_anios_es_un_error_de_tipeo(self):
        yogur = fabrica.producto('Yogur de prueba', prov=self.proveedor,
                                 controla_vencimiento=True)
        with self.assertRaisesMessage(ValidationError, 'Revisá el año'):
            fabrica.comprar(self.usuario, self.proveedor,
                            (yogur, 2, 100, [(self.hoy + timedelta(days=11000), 2)]))

    def test_una_tanda_no_dura_veinte_anios(self):
        harina = fabrica.producto('Harina de prueba', tipo='INSUMO',
                                  prov=self.proveedor, unidad='KG')
        pan = fabrica.producto('Pan de prueba', tipo='PRODUCIDO',
                               controla_vencimiento=True)
        fabrica.receta(pan, (harina, '0.5'))
        fabrica.comprar(self.usuario, self.proveedor, (harina, 10, 100))
        with self.assertRaisesMessage(ValidationError, 'revisá el año'):
            stock.registrar_produccion(pan, D('2'), self.usuario,
                                       self.hoy + timedelta(days=7300))


class SaltoDeCostoTest(TestCase):
    def test_un_costo_con_un_cero_de_mas_pide_confirmacion(self):
        from compras.views.recepciones import _costos_que_llaman_la_atencion
        proveedor = fabrica.proveedor()
        agua = fabrica.producto('Agua de prueba', prov=proveedor, costo='600')
        renglon = lambda costo: compras.RenglonRecibido(agua, D('1'), D(costo))
        self.assertEqual(_costos_que_llaman_la_atencion([renglon('650')]), [])
        self.assertEqual(len(_costos_que_llaman_la_atencion([renglon('6000')])), 1)
        self.assertEqual(len(_costos_que_llaman_la_atencion([renglon('60')])), 1)


class PreciosBajoElCostoTest(TestCase):
    def test_la_rebaja_masiva_marca_lo_que_queda_a_perdida(self):
        from stock.models import Producto
        proveedor = fabrica.proveedor()
        fabrica.producto('Agua de prueba', prov=proveedor, precio='1000', costo='600')
        productos = Producto.objects.filter(nombre_producto='Agua de prueba')
        self.assertEqual(precios.quedan_bajo_costo(productos, D('-10'), D('10')), [])
        self.assertEqual(len(precios.quedan_bajo_costo(productos, D('-50'), D('10'))), 1)

    def test_la_pantalla_no_aplica_sin_la_confirmacion(self):
        from stock.models import Producto
        usuario = fabrica.usuario('30111224', 'Administrador')
        fabrica.entrar(self.client, usuario)
        proveedor = fabrica.proveedor()
        agua = fabrica.producto('Agua de prueba', prov=proveedor, precio='1000', costo='600')
        url = reverse('stock:precios')
        previa = self.client.get(url, {'porcentaje': '-50', 'redondeo': '10'})
        self.assertContains(previa, 'Bajo el costo')
        datos = {'porcentaje': '-50', 'redondeo': '10',
                 'token_operacion': previa.context['token']}
        respuesta = self.client.post(url, datos)
        self.assertContains(respuesta, 'por debajo')
        self.assertEqual(Producto.objects.get(pk=agua.pk).precio_venta, D('1000'))
        datos['token_operacion'] = respuesta.context['token']
        self.client.post(url, {**datos, 'confirmo_bajo_costo': '1'})
        self.assertEqual(Producto.objects.get(pk=agua.pk).precio_venta, D('500'))


class ConciliacionTest(TestCase):
    def setUp(self):
        self.tipo = TipoEgreso.objects.filter(borrado_logico=False).first()
        self.banco = MedioPago.objects.filter(afecta_caja=False, borrado_logico=False).first()
        self.egreso = egresos.registrar_egreso(
            self.tipo, self.banco, D('1000'), 'Internet de prueba',
            destinatario='Proveedor de internet')
        self.hoy = timezone.localdate()

    def test_no_se_acredita_en_el_futuro(self):
        with self.assertRaisesMessage(ValidationError, 'todavía no llegó'):
            egresos.conciliar(self.egreso, True, self.hoy + timedelta(days=3))

    def test_no_se_acredita_antes_de_pagar(self):
        with self.assertRaisesMessage(ValidationError, 'antes de esa fecha'):
            egresos.conciliar(self.egreso, True, self.hoy - timedelta(days=3))

    def test_hoy_si(self):
        self.assertEqual(egresos.conciliar(self.egreso, True, self.hoy).estado_pago, 'ACREDITADO')


class BajasTest(TestCase):
    def setUp(self):
        self.usuario = fabrica.usuario('30111225', 'Administrador')
        self.proveedor = fabrica.proveedor()

    def test_un_producto_pedido_en_una_orden_abierta_no_se_da_de_baja(self):
        agua = fabrica.producto('Agua de prueba', prov=self.proveedor)
        compras.emitir_orden(self.proveedor, self.usuario, [
            {'producto': agua, 'cantidad': D('1'), 'precio_unitario': D('10')}])
        self.assertTrue(any('orden' in m for m in agua.motivos_que_impiden_la_baja()))

    def test_quien_tiene_la_caja_abierta_no_se_da_de_baja(self):
        cajera = fabrica.usuario('30111226')
        fabrica.abrir_turno(cajera)
        with self.assertRaisesMessage(ValidationError, 'turno sigue abierto'):
            cajera.dar_de_baja()
        turno = TurnoCaja.objects.get(id_usuario_apertura=cajera)
        caja.cerrar(turno, cajera, caja.saldo(turno))
        cajera.dar_de_baja()
        self.assertFalse(cajera.activo)


class TurnoDeOtroDiaTest(TestCase):
    def test_se_avisa_cuando_la_caja_quedo_abierta(self):
        cajera = fabrica.usuario('30111227')
        turno = fabrica.abrir_turno(cajera)
        self.assertFalse(turno.quedo_de_otro_dia)
        TurnoCaja.objects.filter(pk=turno.pk).update(
            fecha_apertura=timezone.now() - timedelta(days=2))
        turno.refresh_from_db()
        self.assertTrue(turno.quedo_de_otro_dia)


class NumerosDeRellenoTest(TestCase):
    def test_un_dni_inventado_no_pasa(self):
        for dni in ('00000000', '11111111', '9999999', '0000123'):
            with self.subTest(dni=dni), self.assertRaises(ValidationError):
                exigir_dni_creible(dni)
        exigir_dni_creible('30000000')
        exigir_dni_creible('4123456')

    def test_un_telefono_de_digitos_iguales_no_pasa(self):
        with self.assertRaises(ValidationError):
            validar_telefono('1111111')
        self.assertEqual(validar_telefono('387 412-3456'), '3874123456')

    def test_el_formulario_de_clientes_lo_dice_en_el_campo(self):
        form = ClienteForm({'apellido_cliente': 'Prueba', 'nombre_cliente': 'Ana',
                            'dni_cliente': '00000000', 'telefono_cliente': '3874111222'})
        self.assertFalse(form.is_valid())
        self.assertIn('dni_cliente', form.errors)

    def test_un_punto_no_cuesta_menos_de_un_peso(self):
        categoria = CategoriaFidelizacion(nombre_categoria='Prueba', puntos_minimos=777,
                                          importe_por_punto=D('0.50'))
        with self.assertRaisesMessage(ValidationError, '1 o más'):
            categoria.full_clean()
