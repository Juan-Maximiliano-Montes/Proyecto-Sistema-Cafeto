"""Compras y egresos: que nada se pague dos veces ni de más.

    RecepcionTest   la factura, los lotes y el comprobante único
    EgresosTest     obligaciones, pagos parciales, gasto de caja y banco
    HistoricoTest   el archivado se lleva la familia completa de un turno
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from compras import egresos
from compras.models import Compra, Egreso, TipoEgreso
from nucleo.tests import fabrica as f
from ventas import caja
from ventas import servicios as ventas
from ventas.models import MovimientoCaja, TurnoCaja, Venta

D = Decimal


class Base(TestCase):

    def setUp(self):
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.prov = f.proveedor()
        self.gaseosa = f.producto('Gaseosa cola', prov=self.prov,
                                  precio='1250')
        self.tipo_proveedores = TipoEgreso.objects.get(
            nombre_tipo_egreso='Proveedores')
        self.tipo_varios = TipoEgreso.objects.get(
            nombre_tipo_egreso='Gastos varios')


class RecepcionTest(Base):

    def test_el_total_es_mercaderia_mas_otros_conceptos(self):
        compra = f.comprar(self.encargada, self.prov,
                           (self.gaseosa, 12, '700'), otros='420')
        self.assertEqual(compra.total_compra, D('8820'))
        self.assertEqual(compra.numero_formateado[:5], '0001-')

    def test_a_un_proveedor_de_servicios_no_se_le_compra_mercaderia(self):
        self.prov.tipo_proveedor = 'SERVICIOS'
        self.prov.save()
        with self.assertRaisesMessage(ValidationError, 'mercadería'):
            f.comprar(self.encargada, self.prov, (self.gaseosa, 1, '700'))

    def test_lo_elaborado_no_se_compra(self):
        cafe = f.producto('Café', 'ELABORADO', rubro_nombre='Cafetería')
        with self.assertRaisesMessage(ValidationError, 'no se compra'):
            f.comprar(self.encargada, self.prov, (cafe, 1, '700'))

    def test_varios_lotes_en_un_renglon(self):
        yogur = f.producto('Yogur', prov=self.prov,
                           controla_vencimiento=True)
        f.comprar(self.encargada, self.prov,
                  (yogur, 10, '500', [(f.en_dias(10), 4),
                                      (f.en_dias(20), 6)]))
        self.assertEqual(yogur.lotes.count(), 2)
        yogur.refresh_from_db()
        self.assertEqual(yogur.stock_actual, 10)

    def test_la_mercaderia_no_se_recibe_vencida(self):
        yogur = f.producto('Yogur', prov=self.prov,
                           controla_vencimiento=True)
        with self.assertRaisesMessage(ValidationError, 'no se recibe vencida'):
            f.comprar(self.encargada, self.prov,
                      (yogur, 2, '500', [(f.en_dias(-1), 2)]))

    def test_la_compra_no_lleva_fecha_futura(self):
        from compras import servicios as compras
        with self.assertRaisesMessage(ValidationError, 'posterior a hoy'):
            compras.recepcionar(
                self.prov, self.encargada, 'FACTURA_B', 1, 4321,
                [compras.RenglonRecibido(self.gaseosa, Decimal('1'),
                                         Decimal('650'))],
                fecha_compra=timezone.localdate() + timedelta(days=1))


class EgresosTest(Base):

    def setUp(self):
        super().setUp()
        self.compra = f.comprar(self.encargada, self.prov,
                                (self.gaseosa, 10, '1000'))      # $10.000

    def test_pagos_parciales_hasta_el_total(self):
        transferencia = f.medio('Transferencia')
        egresos.registrar_egreso(
            self.tipo_proveedores, transferencia, D('6000'), 'Pago parcial',
            proveedor=self.prov, compra=self.compra)
        with self.assertRaisesMessage(ValidationError, 'saldo de $ 4.000,00'):
            egresos.registrar_egreso(
                self.tipo_proveedores, transferencia, D('5000'), 'Resto',
                proveedor=self.prov, compra=self.compra)
        self.assertEqual(self.compra.saldo_pendiente, D('4000.00'))

    def test_un_pago_rechazado_libera_el_saldo(self):
        transferencia = f.medio('Transferencia')
        egreso = egresos.registrar_egreso(
            self.tipo_proveedores, transferencia, D('10000'), 'Pago total',
            proveedor=self.prov, compra=self.compra)
        egresos.conciliar(egreso, False, timezone.localdate())
        egresos.registrar_egreso(
            self.tipo_proveedores, transferencia, D('10000'), 'Reintento',
            proveedor=self.prov, compra=self.compra)
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.estado_compra, 'EN_PROCESO_PAGO')

    def test_la_compra_queda_pagada_al_acreditarse(self):
        egreso = egresos.registrar_egreso(
            self.tipo_proveedores, f.medio('Transferencia'), D('10000'),
            'Pago', proveedor=self.prov, compra=self.compra)
        egresos.conciliar(egreso, True, timezone.localdate(), 'TRF-1')
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.estado_compra, 'PAGADA')

    def test_la_misma_transferencia_no_paga_dos_egresos(self):
        transferencia = f.medio('Transferencia')
        uno = egresos.registrar_egreso(
            self.tipo_varios, transferencia, D('100'), 'Algo',
            destinatario='Ferretería')
        dos = egresos.registrar_egreso(
            self.tipo_varios, transferencia, D('200'), 'Otra cosa',
            destinatario='Ferretería')
        egresos.conciliar(uno, True, timezone.localdate(), 'TRF-77')
        with self.assertRaisesMessage(ValidationError, 'ya cancela otro'):
            egresos.conciliar(dos, True, timezone.localdate(), 'trf 77')

    def test_el_gasto_de_caja_sale_del_cajon(self):
        turno = f.abrir_turno(self.encargada, fondo='5000')
        egreso = egresos.gasto_de_caja(
            turno, self.encargada, self.tipo_varios, D('1500'),
            'Bolsa de hielo', f.efectivo(), destinatario='Heladería')
        self.assertEqual(egreso.estado_pago, 'ACREDITADO')
        self.assertEqual(caja.saldo(turno), D('3500'))
        self.assertTrue(MovimientoCaja.objects.filter(
            id_egreso=egreso, tipo_movimiento='GASTO').exists())

    def test_el_gasto_no_puede_superar_lo_que_hay(self):
        turno = f.abrir_turno(self.encargada, fondo='1000')
        with self.assertRaisesMessage(ValidationError, 'No hay suficiente'):
            egresos.gasto_de_caja(
                turno, self.encargada, self.tipo_varios, D('1500'), 'Hielo',
                f.efectivo(), destinatario='Heladería')
        self.assertFalse(Egreso.objects.filter(concepto='Hielo').exists())

    def test_el_efectivo_pide_un_cajon(self):
        with self.assertRaisesMessage(ValidationError, 'cajón'):
            egresos.registrar_egreso(self.tipo_varios, f.efectivo(),
                                     D('100'), 'Algo', destinatario='X')


class HistoricoTest(Base):

    def filas(self, tabla):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT COUNT(*) FROM {tabla}')
            return cursor.fetchone()[0]

    def test_un_turno_viejo_se_archiva_con_toda_su_familia(self):
        f.comprar(self.encargada, self.prov, (self.gaseosa, 5, '700'))
        turno = f.abrir_turno(self.encargada)
        ventas.registrar(turno, self.encargada,
                         [ventas.Item(self.gaseosa, D('1'))],
                         [ventas.Pago(f.efectivo())])
        caja.cerrar(turno, self.encargada, D('6250'))
        TurnoCaja.objects.filter(pk=turno.pk).update(
            fecha_apertura=timezone.now() - timedelta(days=800),
            fecha_cierre=timezone.now() - timedelta(days=800))

        call_command('pasar_a_historico', '--ejecutar', stdout=StringIO())

        self.assertFalse(Venta.objects.exists())
        self.assertFalse(TurnoCaja.objects.exists())
        for tabla in ('hist_ventas', 'hist_detalles_venta', 'hist_pagos_venta',
                      'hist_turnos_caja'):
            with self.subTest(tabla=tabla):
                self.assertEqual(self.filas(tabla), 1)
        self.assertEqual(self.filas('hist_movimientos_caja'), 2)
        # el stock no cambia por archivar: el saldo vive en el producto
        self.gaseosa.refresh_from_db()
        self.assertEqual(self.gaseosa.stock_actual, 4)
        self.assertTrue(Compra.objects.exists())
