"""Lo que encontró la auditoría en Compras (DD-101)."""
from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras import servicios
from compras.models import Compra, CondicionPago
from nucleo.tests import fabrica as f


class Base(TestCase):

    def setUp(self):
        self.admin = f.titular()
        self.prov = f.proveedor()
        self.yerba = f.producto('Yerba', prov=self.prov, precio='3000', costo='2000')


class SalvatajeConCostoCeroTest(Base):

    def test_un_producto_sin_costo_toma_el_de_la_compra_de_salvataje(self):
        migrado = f.producto('Fideos del Excel', prov=self.prov, precio='1000', costo='0')
        otro = f.proveedor('Supermercado de la esquina', '30-71000000-6')
        servicios.recepcionar(otro, self.admin, 'TICKET', 1, 90,
                              [servicios.RenglonRecibido(migrado, D('4'), D('650'), [])], ocasional=True)
        migrado.refresh_from_db()
        # El costo se completa (si no, la ganancia del mes saldría inflada)...
        self.assertEqual(migrado.precio_costo, D('650.00'))
        # ...pero el proveedor habitual no cambia.
        self.assertEqual(migrado.id_proveedor, self.prov)

    def test_uno_con_costo_lo_conserva(self):
        otro = f.proveedor('Supermercado de la esquina', '30-71000000-6')
        servicios.recepcionar(otro, self.admin, 'TICKET', 1, 91,
                              [servicios.RenglonRecibido(self.yerba, D('2'), D('3500'), [])], ocasional=True)
        self.yerba.refresh_from_db()
        self.assertEqual(self.yerba.precio_costo, D('2000.00'))


class VencimientoDeLaFacturaTest(Base):

    def test_sin_fecha_vence_segun_la_condicion_del_proveedor(self):
        hoy = timezone.localdate()
        for condicion, dias, numero in ((CondicionPago.CONTADO, 0, 101), (CondicionPago.DIAS_15, 15, 102),
                                        (CondicionPago.DIAS_30, 30, 103), (CondicionPago.DIAS_60, 60, 104),
                                        (CondicionPago.CUENTA_CORRIENTE, 30, 105)):
            with self.subTest(condicion=condicion):
                self.prov.condicion_pago = condicion
                self.prov.save()
                compra = servicios.recepcionar(
                    self.prov, self.admin, 'FACTURA_B', 1, numero,
                    [servicios.RenglonRecibido(self.yerba, D('1'), D('2000'), [])])
                self.assertEqual(compra.fecha_vencimiento_pago, hoy + timedelta(days=dias))

    def test_a_pagar_muestra_siempre_la_insignia_de_vencimiento(self):
        servicios.recepcionar(self.prov, self.admin, 'FACTURA_B', 1, 200,
                              [servicios.RenglonRecibido(self.yerba, D('1'), D('2000'), [])])
        self.assertFalse(Compra.objects.filter(fecha_vencimiento_pago__isnull=True).exists())
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(reverse('compras:egresos'), {'ver': 'a_pagar'})
        self.assertContains(pantalla, 'Vence hoy')


class AltaDesdeLaFacturaTest(Base):

    def test_el_producto_nuevo_lleva_iva_general(self):
        f.entrar(self.client, self.admin)
        respuesta = self.client.post(reverse('compras:alta_producto_factura'),
                                     {'nombre': 'Galletitas nuevas 300 g', 'codigo': '7790000000017',
                                      'costo': '980', 'proveedor': str(self.prov.pk)},
                                     content_type='application/json').json()
        self.assertTrue(respuesta['ok'], respuesta)
        from stock.models import Producto
        self.assertEqual(Producto.objects.get(pk=respuesta['producto']).alicuota_iva, D('21.0'))


class PantallaDeRecepcionTest(Base):

    def test_el_combo_no_atraviesa_el_modal_y_no_se_guarda_con_altas_pendientes(self):
        from pathlib import Path

        from django.conf import settings
        css = (Path(settings.BASE_DIR) / 'nucleo/static/cafeto/css/cafeto.css').read_text()
        self.assertNotIn('.select2-container { z-index: 1100', css)
        js = (Path(settings.BASE_DIR) / 'nucleo/static/cafeto/js/escanear-factura.js').read_text()
        self.assertIn("window.addEventListener('submit'", js)
        self.assertIn('sin dar de alta', js)
