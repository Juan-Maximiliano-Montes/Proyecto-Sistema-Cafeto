"""Stock: la matriz de tipos, el libro, FEFO, producción y ajustes.

Cada clase prueba una garantía que el negocio necesita:

    MatrizDeTiposTest       que cada tipo de producto lleve lo que tiene
                            que llevar, en el modelo y en la base
    LibroDeStockTest        que no se venda lo que no hay ni media lata
    ProduccionTest          que una tanda descuente sus insumos
    AjustesTest             que nada se mueva sin autorización
    AlertasTest             vencimientos, ofertas y bajo mínimo
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from nucleo.tests import fabrica as f
from stock import servicios as stock
from ventas import servicios as ventas
from stock.models import (
    AjusteStock,
    ComposicionProducto,
    Lote,
    MovimientoStock,
    Producto,
    TipoAjuste,
)

D = Decimal


class Base(TestCase):

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.prov = f.proveedor()
        self.gaseosa = f.producto('Gaseosa cola', prov=self.prov,
                                  contenido='500', unidad='ML')
        self.yogur = f.producto('Yogur frutilla', prov=self.prov,
                                controla_vencimiento=True)
        self.grano = f.producto('Café en grano', 'INSUMO', prov=self.prov,
                                costo='20000', rubro_nombre='Cafetería',
                                contenido='1', unidad='KG')
        self.leche = f.producto('Leche', 'INSUMO', prov=self.prov,
                                costo='1500', rubro_nombre='Cafetería',
                                contenido='1', unidad='L')


# ===========================================================================
class MatrizDeTiposTest(Base):

    def test_lo_que_se_compra_lleva_proveedor(self):
        with self.assertRaises(ValidationError) as error:
            f.producto('Galletitas', prov=None)
        self.assertIn('id_proveedor', error.exception.message_dict)

    def test_lo_elaborado_no_lleva_proveedor_ni_se_lo_ponen(self):
        cafe = f.producto('Cortado', 'ELABORADO', prov=self.prov,
                          rubro_nombre='Cafetería')
        self.assertIsNone(cafe.id_proveedor)

    def test_el_insumo_no_tiene_precio_de_venta(self):
        self.assertEqual(self.grano.precio_venta, 0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Producto.objects.filter(pk=self.grano.pk).update(
                precio_venta=D('100'))

    def test_lo_vendible_tiene_precio(self):
        with self.assertRaises(ValidationError):
            f.producto('Agua', prov=self.prov, precio='0')

    def test_un_combo_no_controla_vencimiento(self):
        with self.assertRaises(ValidationError):
            f.producto('Combo', 'COMBO', controla_vencimiento=True)

    def test_fraccionable_solo_por_kilo_o_litro(self):
        with self.assertRaises(ValidationError):
            f.producto('Queso', prov=self.prov, fraccionable=True,
                       contenido='500', unidad='G')
        queso = f.producto('Queso cremoso', prov=self.prov,
                           fraccionable=True, contenido='1', unidad='KG')
        self.assertFalse(queso.exige_cantidad_entera)

    def test_la_rebaja_pide_vencimiento_y_los_dos_datos(self):
        with self.assertRaises(ValidationError):
            f.producto('Leche larga vida', prov=self.prov,
                       dias_rebaja_vencimiento=3,
                       porcentaje_rebaja_vencimiento=D('20'))
        with self.assertRaises(ValidationError):
            f.producto('Leche chocolatada', prov=self.prov,
                       controla_vencimiento=True, dias_rebaja_vencimiento=3)

    def test_un_combo_no_lleva_insumos(self):
        combo = f.producto('Desayuno', 'COMBO', rubro_nombre='Cafetería')
        with self.assertRaises(ValidationError):
            ComposicionProducto.objects.create(
                id_producto=combo, id_componente=self.grano,
                cantidad=D('0.02'))

    def test_una_receta_no_lleva_media_lata(self):
        cafe = f.producto('Cortado', 'ELABORADO', rubro_nombre='Cafetería')
        with self.assertRaises(ValidationError):
            ComposicionProducto.objects.create(
                id_producto=cafe, id_componente=self.gaseosa,
                cantidad=D('0.5'))

    def test_el_tipo_no_cambia_con_movimientos(self):
        f.comprar(self.operador, self.prov, (self.gaseosa, 5, '600'))
        self.gaseosa.refresh_from_db()
        self.gaseosa.tipo_producto = 'INSUMO'
        with self.assertRaises(ValidationError):
            self.gaseosa.full_clean()

    def test_el_costo_de_un_elaborado_es_su_receta(self):
        cafe = f.receta(
            f.producto('Café con leche', 'ELABORADO',
                       rubro_nombre='Cafetería', precio='2500'),
            (self.grano, '0.018'), (self.leche, '0.15'))
        # 0,018 × 20000 + 0,15 × 1500 = 360 + 225
        self.assertEqual(cafe.costo_vigente(), D('585.00'))


# ===========================================================================
class LibroDeStockTest(Base):

    def test_la_compra_entra_y_actualiza_el_costo(self):
        f.comprar(self.operador, self.prov, (self.gaseosa, 12, '650'))
        self.gaseosa.refresh_from_db()
        self.assertEqual(self.gaseosa.stock_actual, 12)
        self.assertEqual(self.gaseosa.precio_costo, D('650'))

    def test_no_se_vende_lo_que_no_hay(self):
        f.comprar(self.operador, self.prov, (self.gaseosa, 2, '650'))
        with self.assertRaisesMessage(ValidationError,
                                      'Stock insuficiente de'):
            stock.exigir_disponible(self.gaseosa, 3)

    def test_la_base_tampoco_deja_bajar_de_cero(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Producto.objects.filter(pk=self.gaseosa.pk).update(
                stock_actual=D('-1'))

    def test_el_insumo_si_puede_quedar_negativo(self):
        Producto.objects.filter(pk=self.grano.pk).update(
            stock_actual=D('-0.5'))
        self.grano.refresh_from_db()
        self.assertEqual(self.grano.stock_actual, D('-0.5'))

    def test_media_lata_no(self):
        with self.assertRaisesMessage(ValidationError, 'entera'):
            stock.exigir_cantidad_valida(self.gaseosa, D('0.5'))

    def test_el_libro_no_se_corrige(self):
        f.comprar(self.operador, self.prov, (self.gaseosa, 2, '650'))
        movimiento = MovimientoStock.objects.first()
        movimiento.cantidad = 10
        with self.assertRaises(ValidationError):
            movimiento.save()

    def test_un_asiento_cuelga_de_un_solo_documento(self):
        compra = f.comprar(self.operador, self.prov, (self.gaseosa, 2, '1'))
        ajuste = AjusteStock.objects.create(
            tipo_ajuste='MERMA', motivo='ROTURA', id_usuario=self.operador)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovimientoStock.objects.bulk_create([MovimientoStock(
                id_producto=self.gaseosa, id_usuario=self.operador,
                id_compra=compra, id_ajuste=ajuste, tipo_movimiento='COMPRA',
                cantidad=1)])

    def test_fefo_saca_primero_lo_que_vence_primero(self):
        f.comprar(self.operador, self.prov,
                  (self.yogur, 10, '500', [(f.en_dias(20), 6),
                                           (f.en_dias(5), 4)]))
        plan = stock.planificar_salida(self.yogur, 5)
        self.assertEqual([t.cantidad for t in plan.tramos], [4, 1])
        self.assertEqual(plan.tramos[0].lote.fecha_vencimiento, f.en_dias(5))

    def _yogur_con_un_lote_vencido(self):
        """6 vigentes (vencen en 10 días) y 4 que vencieron ayer."""
        f.comprar(self.operador, self.prov,
                  (self.yogur, 10, '500', [(f.en_dias(10), 6),
                                           (f.en_dias(5), 4)]))
        Lote.objects.filter(id_producto=self.yogur,
                            fecha_vencimiento=f.en_dias(5)).update(
            fecha_vencimiento=f.en_dias(-1))

    def test_lo_vencido_no_se_vende(self):
        self._yogur_con_un_lote_vencido()
        self.assertEqual(stock.disponible(self.yogur), 6)
        self.assertEqual(stock.vencido(self.yogur), 4)
        plan = stock.planificar_salida(self.yogur, 7)
        self.assertEqual(plan.tramos[0].lote.fecha_vencimiento, f.en_dias(10))
        self.assertIsNone(plan.tramos[-1].lote)     # el faltante
        with self.assertRaisesMessage(ValidationError, 'vencidas'):
            stock.exigir_disponible(self.yogur, 7)
        with self.assertRaisesMessage(ValidationError, 'Stock insuficiente'):
            stock.descontar(self.yogur, 7, self.operador, 'VENTA', {})

    def test_la_venta_sale_del_lote_vigente_aunque_haya_uno_vencido(self):
        self._yogur_con_un_lote_vencido()
        caja = f.abrir_turno(self.operador)
        venta, _ = ventas.registrar(caja, self.operador,
                                    [ventas.Item(self.yogur, D('2'))],
                                    [ventas.Pago(f.efectivo())])
        lotes = {m.id_lote.fecha_vencimiento for m in
                 MovimientoStock.objects.filter(id_venta=venta)}
        self.assertEqual(lotes, {f.en_dias(10)})

    def test_un_insumo_por_lote_solo_sale_de_lotes_vigentes(self):
        crema = f.producto('Crema de leche', 'INSUMO', prov=self.prov,
                           costo='3000', rubro_nombre='Cafetería',
                           contenido='1', unidad='L',
                           controla_vencimiento=True)
        f.comprar(self.operador, self.prov,
                  (crema, 2, '3000', [(f.en_dias(3), 2)]))
        stock.exigir_disponible(crema, D('1.5'))        # alcanza
        Lote.objects.filter(id_producto=crema).update(
            fecha_vencimiento=f.en_dias(-1))
        with self.assertRaisesMessage(ValidationError, 'lotes vigentes'):
            stock.exigir_disponible(crema, D('0.1'))

    def test_los_lotes_tienen_que_sumar_lo_recibido(self):
        with self.assertRaisesMessage(ValidationError, 'suman'):
            f.comprar(self.operador, self.prov,
                      (self.yogur, 10, '500', [(f.en_dias(20), 6)]))

    def test_la_misma_factura_no_se_carga_dos_veces(self):
        f.comprar(self.operador, self.prov, (self.gaseosa, 2, '650'),
                  numero=777)
        with self.assertRaisesMessage(ValidationError, 'ya está cargada'):
            f.comprar(self.operador, self.prov, (self.gaseosa, 2, '650'),
                      numero=777)


# ===========================================================================
class ProduccionTest(Base):

    def setUp(self):
        super().setUp()
        self.harina = f.producto('Harina', 'INSUMO', prov=self.prov,
                                 costo='1000', contenido='1', unidad='KG')
        self.empanada = f.receta(
            f.producto('Empanada de carne', 'PRODUCIDO', precio='1500',
                       rubro_nombre='Cafetería'),
            (self.harina, '0.05'))

    def test_una_tanda_entra_y_descuenta_sus_insumos(self):
        f.comprar(self.operador, self.prov, (self.harina, 5, '1000'))
        stock.registrar_produccion(self.empanada, 24, self.operador)
        self.empanada.refresh_from_db()
        self.harina.refresh_from_db()
        self.assertEqual(self.empanada.stock_actual, 24)
        self.assertEqual(self.harina.stock_actual, D('3.800'))
        # 0,05 kg × $1000 = $50 por empanada
        self.assertEqual(self.empanada.precio_costo, D('50.00'))

    def test_sin_receta_no_se_produce(self):
        pizza = f.producto('Pizza', 'PRODUCIDO', precio='8000',
                           rubro_nombre='Cafetería')
        with self.assertRaisesMessage(ValidationError, 'receta'):
            stock.registrar_produccion(pizza, 2, self.operador)

    def test_solo_se_producen_los_producidos(self):
        with self.assertRaises(ValidationError):
            stock.registrar_produccion(self.gaseosa, 2, self.operador)


# ===========================================================================
class AjustesTest(Base):

    def setUp(self):
        super().setUp()
        f.comprar(self.operador, self.prov, (self.gaseosa, 10, '600'))

    def test_la_merma_no_mueve_nada_hasta_que_se_aprueba(self):
        merma = stock.registrar_merma(self.operador, 'ROTURA',
                                      [(self.gaseosa, 2, None)])
        self.gaseosa.refresh_from_db()
        self.assertEqual(self.gaseosa.stock_actual, 10)
        stock.aprobar(merma, self.encargada)
        self.gaseosa.refresh_from_db()
        self.assertEqual(self.gaseosa.stock_actual, 8)
        merma.refresh_from_db()
        self.assertEqual(merma.id_usuario_autorizante, self.encargada)

    def test_rechazar_pide_motivo_y_no_toca_el_stock(self):
        merma = stock.registrar_merma(self.operador, 'ROTURA',
                                      [(self.gaseosa, 2, None)])
        with self.assertRaises(ValidationError):
            stock.rechazar(merma, self.encargada, '')
        stock.rechazar(merma, self.encargada, 'No se rompió nada')
        self.gaseosa.refresh_from_db()
        self.assertEqual(self.gaseosa.stock_actual, 10)

    def test_lo_resuelto_no_se_vuelve_a_resolver(self):
        merma = stock.registrar_merma(self.operador, 'ROTURA',
                                      [(self.gaseosa, 2, None)])
        stock.aprobar(merma, self.encargada)
        with self.assertRaises(ValidationError):
            stock.aprobar(merma, self.encargada)

    def test_otro_motivo_pide_detalle(self):
        with self.assertRaises(ValidationError):
            stock.registrar_merma(self.operador, 'OTRO',
                                  [(self.gaseosa, 1, None)])

    def test_el_conteo_asienta_la_diferencia_aunque_se_siga_vendiendo(self):
        conteo = stock.iniciar_conteo(self.operador)
        stock.cargar_conteo(conteo, self.gaseosa, 9)   # teórico 10
        # entre el conteo y la aprobación se venden 3
        merma = stock.registrar_merma(self.operador, 'ROTURA',
                                      [(self.gaseosa, 3, None)])
        stock.aprobar(merma, self.encargada)      # la aprueba otra persona (DD-67)
        stock.enviar_conteo(conteo)
        stock.aprobar(conteo, self.encargada)
        self.gaseosa.refresh_from_db()
        # 10 - 3 (merma) - 1 (faltante del conteo) = 6
        self.assertEqual(self.gaseosa.stock_actual, 6)

    def test_un_solo_conteo_abierto(self):
        stock.iniciar_conteo(self.operador)
        with self.assertRaises(ValidationError):
            stock.iniciar_conteo(self.encargada)

    def test_el_inventario_inicial_crea_los_lotes(self):
        conteo = stock.iniciar_conteo(self.operador,
                                      motivo='INVENTARIO_INICIAL')
        stock.cargar_conteo(conteo, self.yogur, 6,
                            nuevo_lote={'fecha_vencimiento': f.en_dias(10)})
        stock.enviar_conteo(conteo)
        stock.aprobar(conteo, self.encargada)
        self.yogur.refresh_from_db()
        self.assertEqual(self.yogur.stock_actual, 6)
        self.assertEqual(self.yogur.lotes.get().stock_lote, 6)

    def test_el_traspaso_pasa_de_la_gondola_a_la_cafeteria(self):
        sachet = f.producto('Leche sachet', prov=self.prov, precio='1800')
        f.comprar(self.operador, self.prov, (sachet, 4, '1500'))
        traspaso = stock.registrar_traspaso(
            self.operador, 'USO_EN_CAFETERIA',
            [(sachet, 2, self.leche, D('2'), None)])
        traspaso = stock.aprobar(traspaso, self.encargada)   # otra persona (DD-67)
        sachet.refresh_from_db()
        self.leche.refresh_from_db()
        self.assertEqual(sachet.stock_actual, 2)
        self.assertEqual(self.leche.stock_actual, 2)
        self.assertEqual(traspaso.estado_ajuste, 'APROBADO')

    def test_tipo_y_motivo_tienen_que_corresponder(self):
        with self.assertRaises(ValidationError):
            stock.registrar_merma(self.operador, 'INVENTARIO_INICIAL',
                                  [(self.gaseosa, 1, None)])
        self.assertFalse(AjusteStock.objects.filter(
            tipo_ajuste=TipoAjuste.MERMA).exists())


# ===========================================================================
class AlertasTest(Base):

    def test_proximos_a_vencer_y_en_oferta(self):
        self.yogur.id_rubro.dias_rebaja_vencimiento = 5
        self.yogur.id_rubro.porcentaje_rebaja_vencimiento = D('20')
        self.yogur.id_rubro.save()
        f.comprar(self.operador, self.prov,
                  (self.yogur, 10, '500', [(f.en_dias(3), 4),
                                           (f.en_dias(30), 6)]))
        self.assertEqual(stock.proximos_a_vencer(7).count(), 1)
        ofertas = stock.en_oferta()
        self.assertEqual(len(ofertas), 1)
        lote, porcentaje, precio = ofertas[0]
        self.assertEqual(porcentaje, D('20'))
        self.assertEqual(precio, D('800'))

    def test_bajo_minimo(self):
        Producto.objects.filter(pk=self.gaseosa.pk).update(
            stock_minimo=D('5'))
        f.comprar(self.operador, self.prov, (self.gaseosa, 2, '600'))
        self.assertIn(self.gaseosa, list(stock.bajo_minimo()))
