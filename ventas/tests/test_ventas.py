"""Caja, ventas y cambios: que el dinero y la mercadería cuadren siempre.

    CajaTest        turnos por cajón, relevo, arqueo ciego y supervisión
    VentaTest       el ticket: precios, redondeo, pago mixto, stock, caja
    DescuentosTest  topes por perfil, autorización y rebaja por vencimiento
    CombosTest      recetas y combos descuentan sus componentes
    CambiosTest     devoluciones y cambios contra la venta original
"""
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from negocio.models import ParametrosNegocio
from nucleo.tests import fabrica as f
from ventas import cambios, caja
from ventas import servicios as ventas
from ventas.models import (
    DetalleVenta,
    MedioPago,
    MovimientoCaja,
    PagoVenta,
    TurnoCaja,
    Venta,
)

D = Decimal
Item, Pago = ventas.Item, ventas.Pago


class Base(TestCase):

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Beto')
        self.operadora = f.usuario('40000003', 'Operador', nombre='Ana')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.prov = f.proveedor()
        self.gaseosa = f.producto('Gaseosa cola', prov=self.prov,
                                  precio='1250', costo='700')
        self.chicle = f.producto('Chicle', prov=self.prov, precio='333.33',
                                 costo='100')
        f.comprar(self.operador, self.prov, (self.gaseosa, 10, '700'),
                  (self.chicle, 50, '100'))
        self.turno = f.abrir_turno(self.operador, fondo='5000')

    def vender(self, *items, pagos=None, usuario=None, **extra):
        items = [Item(p, D(str(c)), *resto) for p, c, *resto in items]
        venta, cotizacion = ventas.registrar(
            self.turno, usuario or self.operador, items,
            pagos if pagos is not None else [Pago(f.efectivo())], **extra)
        return venta, cotizacion


# ===========================================================================
class CajaTest(Base):

    def test_un_cajon_no_tiene_dos_turnos(self):
        with self.assertRaisesMessage(ValidationError, 'ya tiene un turno'):
            caja.abrir(f.caja_minisuper(), self.operadora, D('1000'))

    def test_dos_cajones_abren_a_la_vez(self):
        otro = caja.abrir(f.caja_cafeteria(), self.operadora, D('3000'))
        self.assertTrue(otro.abierto)

    def test_en_un_turno_venden_varios(self):
        self.vender((self.gaseosa, 1))
        self.vender((self.gaseosa, 1), usuario=self.operadora)
        self.assertEqual(Venta.objects.filter(
            id_turno_caja=self.turno).count(), 2)

    def test_el_retiro_no_deja_el_cajon_en_negativo(self):
        with self.assertRaisesMessage(ValidationError,
                                      'No hay suficiente efectivo'):
            caja.retiro(self.turno, self.encargada, D('6000'), 'Depósito')
        caja.retiro(self.turno, self.encargada, D('4000'), 'Depósito')
        self.assertEqual(caja.saldo(self.turno), D('1000'))

    def test_cierre_sin_diferencia(self):
        self.vender((self.gaseosa, 2))
        cerrado = caja.cerrar(self.turno, self.operador, D('7500'))
        self.assertEqual(cerrado.diferencia_caja, 0)
        self.assertEqual(cerrado.tipo_cierre, 'NORMAL')

    def test_la_diferencia_hay_que_explicarla(self):
        with self.assertRaisesMessage(ValidationError, 'contá a qué se debe'):
            caja.cerrar(self.turno, self.operador, D('4900'))
        cerrado = caja.cerrar(self.turno, self.operador, D('4900'),
                              'Faltó un billete de cien')
        self.assertEqual(cerrado.diferencia_caja, D('-100'))

    def test_una_diferencia_grande_la_supervisa_otra_persona(self):
        tolerancia = ParametrosNegocio.vigentes().tolerancia_arqueo
        contado = D('5000') - tolerancia - 1
        with self.assertRaisesMessage(ValidationError, 'supervisar'):
            caja.cerrar(self.turno, self.operador, contado, 'No sé')
        with self.assertRaisesMessage(ValidationError, 'otra persona'):
            caja.cerrar(self.turno, self.operador, contado, 'No sé',
                        supervisor=self.operador)
        cerrado = caja.cerrar(self.turno, self.operador, contado,
                              'Se contó mal el fondo',
                              supervisor=self.encargada)
        self.assertEqual(cerrado.tipo_cierre, 'SUPERVISADO')

    def test_el_relevo_abre_con_lo_contado(self):
        self.vender((self.gaseosa, 1))
        cerrado, nuevo = caja.relevar(self.turno, self.operador, D('6250'),
                                      self.operadora)
        self.assertEqual(cerrado.tipo_cierre, 'RELEVO')
        self.assertEqual(nuevo.monto_inicial, D('6250'))
        self.assertEqual(caja.saldo(nuevo), D('6250'))

    def test_un_turno_cerrado_no_se_vuelve_a_cerrar_ni_admite_ventas(self):
        caja.cerrar(self.turno, self.operador, D('5000'))
        with self.assertRaises(ValidationError):
            caja.cerrar(self.turno, self.operador, D('5000'))
        with self.assertRaisesMessage(ValidationError, 'cerrado'):
            self.vender((self.gaseosa, 1))

    def test_la_base_exige_la_diferencia_bien_calculada(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TurnoCaja.objects.filter(pk=self.turno.pk).update(
                estado_turno='CERRADA', monto_final=D('10'),
                monto_esperado=D('5000'), diferencia_caja=D('0'),
                tipo_cierre='NORMAL', id_usuario_cierre=self.operador,
                fecha_cierre=self.turno.fecha_apertura)


# ===========================================================================
class VentaTest(Base):

    def test_venta_simple_en_efectivo_con_vuelto(self):
        venta, cot = self.vender((self.gaseosa, 2),
                                 pagos=[Pago(f.efectivo(), None, D('3000'))])
        self.assertEqual(venta.total_venta, D('2500'))
        pago = PagoVenta.objects.get(id_venta=venta)
        self.assertEqual(pago.monto_vuelto, D('500'))
        self.gaseosa.refresh_from_db()
        self.assertEqual(self.gaseosa.stock_actual, 8)
        self.assertEqual(caja.saldo(self.turno), D('7500'))

    def test_el_precio_se_toma_al_confirmar(self):
        self.gaseosa.precio_venta = D('1400')
        self.gaseosa.save()
        venta, _ = self.vender((self.gaseosa, 1))
        renglon = DetalleVenta.objects.get(id_venta=venta)
        self.assertEqual(renglon.precio_unitario, D('1400'))
        self.assertEqual(renglon.costo_unitario, D('700'))

    def test_el_total_se_redondea_al_peso(self):
        venta, _ = self.vender((self.chicle, 1))        # 333,33
        self.assertEqual(venta.total_venta, D('333'))
        self.assertEqual(venta.ajuste_redondeo, D('-0.33'))

    def test_pago_mixto_con_recargo_solo_en_la_tarjeta(self):
        # El recargo es el de un plan en cuotas (DD-65, Ley 25.065).
        credito = MedioPago.objects.create(
            nombre_medio_pago='Crédito 3 cuotas', es_tarjeta_credito=True,
            cantidad_cuotas=3, recargo_porcentaje=D('10'))
        venta, _ = self.vender(
            (self.gaseosa, 4),                           # 5000
            pagos=[Pago(f.efectivo(), D('3000')), Pago(credito)])
        self.assertEqual(venta.monto_recargo, D('200'))  # 10 % de 2000
        self.assertEqual(venta.total_venta, D('5200'))
        # al cajón entra sólo lo del efectivo
        self.assertEqual(caja.saldo(self.turno), D('8000'))

    def test_los_pagos_tienen_que_cubrir_el_total(self):
        with self.assertRaisesMessage(ValidationError, 'Indicá cuánto'):
            self.vender((self.gaseosa, 2),
                        pagos=[Pago(f.efectivo()), Pago(f.medio('QR'))])
        with self.assertRaisesMessage(ValidationError, 'sólo faltan'):
            self.vender((self.gaseosa, 2),
                        pagos=[Pago(f.efectivo(), D('9999'))])

    def test_el_efectivo_recibido_tiene_que_alcanzar(self):
        with self.assertRaisesMessage(ValidationError, 'no alcanza'):
            self.vender((self.gaseosa, 2),
                        pagos=[Pago(f.efectivo(), None, D('1000'))])

    def test_una_transferencia_no_lleva_vuelto(self):
        with self.assertRaisesMessage(ValidationError, 'no entra al cajón'):
            self.vender((self.gaseosa, 1),
                        pagos=[Pago(f.medio('Transferencia'), None,
                                    D('2000'))])

    def test_sin_stock_no_se_cobra_nada(self):
        with self.assertRaisesMessage(ValidationError, 'Stock insuficiente'):
            self.vender((self.gaseosa, 11))
        self.assertEqual(Venta.objects.count(), 0)
        self.assertEqual(caja.saldo(self.turno), D('5000'))

    def test_el_mismo_token_no_cobra_dos_veces(self):
        token = uuid.uuid4()
        primera, _ = self.vender((self.gaseosa, 1), token=token)
        segunda, _ = self.vender((self.gaseosa, 1), token=token)
        self.assertEqual(primera.pk, segunda.pk)
        self.gaseosa.refresh_from_db()
        self.assertEqual(self.gaseosa.stock_actual, 9)

    def test_una_venta_emitida_no_se_modifica(self):
        venta, _ = self.vender((self.gaseosa, 1))
        venta.total_venta = D('1')
        with self.assertRaises(ValidationError):
            venta.save()
        venta.refresh_from_db()
        venta.comprobante_fiscal_ref = '0003-00001234'
        venta.save(update_fields=['comprobante_fiscal_ref'])

    def test_la_base_verifica_el_total(self):
        venta, _ = self.vender((self.gaseosa, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Venta.objects.filter(pk=venta.pk).update(total_venta=D('1'))

    def test_un_insumo_no_se_vende(self):
        grano = f.producto('Café en grano', 'INSUMO', prov=self.prov,
                           unidad='KG')
        with self.assertRaisesMessage(ValidationError, 'insumo'):
            self.vender((grano, 1))

    def test_se_vende_por_peso(self):
        queso = f.producto('Queso cremoso', prov=self.prov, precio='9000',
                           fraccionable=True, contenido='1', unidad='KG')
        f.comprar(self.operador, self.prov, (queso, D('2.5'), '6000'))
        venta, _ = self.vender((queso, D('0.350')))
        self.assertEqual(venta.total_venta, D('3150'))
        queso.refresh_from_db()
        self.assertEqual(queso.stock_actual, D('2.150'))


# ===========================================================================
class DescuentosTest(Base):

    def test_dentro_del_tope_no_pide_autorizacion(self):
        venta, _ = self.vender((self.gaseosa, 1, D('10'), 'PROMOCION'))
        renglon = venta.detalles.get()
        self.assertEqual(renglon.monto_descuento_linea, D('125'))
        self.assertIsNone(renglon.id_usuario_autorizante)

    def test_fuera_del_tope_lo_autoriza_alguien_con_tope_mayor(self):
        with self.assertRaisesMessage(ValidationError, 'supera tu tope'):
            self.vender((self.gaseosa, 1, D('15'), 'PRODUCTO_DANADO'))
        with self.assertRaisesMessage(ValidationError, 'tampoco'):
            self.vender((self.gaseosa, 1, D('15'), 'PRODUCTO_DANADO'),
                        autorizante=self.operadora)
        venta, _ = self.vender((self.gaseosa, 1, D('15'), 'PRODUCTO_DANADO'),
                               autorizante=self.encargada)
        self.assertEqual(venta.detalles.get().id_usuario_autorizante,
                         self.encargada)

    def test_el_descuento_lleva_motivo(self):
        with self.assertRaisesMessage(ValidationError, 'motivo'):
            self.vender((self.gaseosa, 1, D('5'), None))
        with self.assertRaisesMessage(ValidationError, 'Contá'):
            self.vender((self.gaseosa, 1, D('5'), 'OTRO'))

    def test_la_rebaja_por_vencimiento_es_automatica(self):
        yogur = f.producto('Yogur', prov=self.prov, precio='1000',
                           controla_vencimiento=True,
                           dias_rebaja_vencimiento=3,
                           porcentaje_rebaja_vencimiento=D('30'))
        f.comprar(self.operador, self.prov,
                  (yogur, 5, '500', [(f.en_dias(2), 2), (f.en_dias(30), 3)]))
        venta, _ = self.vender((yogur, 3))
        renglon = venta.detalles.get()
        # salen 2 del lote que vence en 2 días (con 30 %) y 1 del otro
        self.assertEqual(renglon.motivo_descuento, 'VENCIMIENTO')
        self.assertEqual(renglon.monto_descuento_linea, D('600'))
        self.assertEqual(venta.total_venta, D('2400'))
        # lo que queda es del lote lejano: ya no tiene rebaja
        venta, _ = self.vender((yogur, 1))
        self.assertEqual(venta.total_venta, D('1000'))

    def test_la_rebaja_no_se_suma_a_otro_descuento(self):
        yogur = f.producto('Yogur', prov=self.prov, precio='1000',
                           controla_vencimiento=True,
                           dias_rebaja_vencimiento=3,
                           porcentaje_rebaja_vencimiento=D('30'))
        f.comprar(self.operador, self.prov,
                  (yogur, 2, '500', [(f.en_dias(2), 2)]))
        with self.assertRaisesMessage(ValidationError, 'ya tiene la rebaja'):
            self.vender((yogur, 1, D('5'), 'PROMOCION'))

    def test_el_administrador_no_tiene_tope(self):
        admin = f.usuario('31000009', 'Administrador')
        venta, _ = self.vender((self.gaseosa, 1, D('80'), 'CORTESIA'),
                               usuario=admin)
        self.assertEqual(venta.total_venta, D('250'))


# ===========================================================================
class CombosTest(Base):

    def setUp(self):
        super().setUp()
        self.grano = f.producto('Café en grano', 'INSUMO', prov=self.prov,
                                costo='20000', unidad='KG')
        self.medialuna = f.producto('Medialuna', prov=self.prov,
                                    precio='600', costo='300',
                                    rubro_nombre='Cafetería')
        f.comprar(self.operador, self.prov, (self.medialuna, 3, '300'))
        self.cafe = f.receta(
            f.producto('Café', 'ELABORADO', precio='2000',
                       rubro_nombre='Cafetería'),
            (self.grano, '0.018'))
        self.combo = f.receta(
            f.producto('Café con 2 medialunas', 'COMBO', precio='2800',
                       rubro_nombre='Cafetería'),
            (self.cafe, '1'), (self.medialuna, '2'))

    def test_el_combo_descuenta_sus_componentes(self):
        self.vender((self.combo, 1))
        self.medialuna.refresh_from_db()
        self.grano.refresh_from_db()
        self.assertEqual(self.medialuna.stock_actual, 1)
        # el insumo queda en negativo: todavía no se cargó su compra
        self.assertEqual(self.grano.stock_actual, D('-0.018'))

    def test_el_aviso_suma_el_combo_y_lo_suelto(self):
        with self.assertRaisesMessage(ValidationError, 'Stock insuficiente'):
            self.vender((self.combo, 1), (self.medialuna, 2))

    def test_el_combo_congela_el_costo_de_sus_componentes(self):
        venta, _ = self.vender((self.combo, 1))
        # 0,018 × 20000 + 2 × 300
        self.assertEqual(venta.detalles.get().costo_unitario, D('960.00'))


# ===========================================================================
class CambiosTest(Base):

    def setUp(self):
        super().setUp()
        self.venta, _ = self.vender((self.gaseosa, 3, D('10'), 'PROMOCION'))
        self.renglon = self.venta.detalles.get()        # 1125 c/u

    def cambio(self, devueltos, entregados=(), **extra):
        extra.setdefault('motivo', 'PRODUCTO_DANADO')
        return cambios.registrar(
            self.turno, self.operador, self.venta,
            devueltos=[cambios.Devuelto(*d) for d in devueltos],
            entregados=[cambios.Entregado(*e) for e in entregados], **extra)

    def test_cambio_por_el_mismo_producto_sin_diferencia(self):
        # La lata vino abollada: la devuelve (no apta) y se lleva otra.
        # Pagó $1125 (con el 10 %) y la nueva vale $1250: paga $125.
        cambio = self.cambio([(self.renglon, D('1'), False)],
                             [(self.gaseosa, D('1'))],
                             medio_pago=f.efectivo())
        self.assertEqual(cambio.importe_cobrado, D('125'))
        self.assertEqual(cambio.importe_devuelto, 0)
        self.gaseosa.refresh_from_db()
        self.assertEqual(self.gaseosa.stock_actual, 6)  # 10 - 3 - 1

    def test_devolver_dinero_pide_autorizacion_y_sale_del_cajon(self):
        antes = caja.saldo(self.turno)
        with self.assertRaisesMessage(ValidationError, 'autorizar'):
            self.cambio([(self.renglon, D('2'), True)],
                        medio_pago=f.efectivo())
        cambio = self.cambio([(self.renglon, D('2'), True)],
                             medio_pago=f.efectivo(),
                             autorizante=self.encargada)
        self.assertEqual(cambio.importe_devuelto, D('2250'))
        self.assertEqual(caja.saldo(self.turno), antes - D('2250'))
        self.gaseosa.refresh_from_db()
        self.assertEqual(self.gaseosa.stock_actual, 9)  # 10 - 3 + 2

    def test_no_se_devuelve_mas_de_lo_comprado(self):
        self.cambio([(self.renglon, D('2'), False)],
                    medio_pago=f.efectivo(), autorizante=self.encargada)
        with self.assertRaisesMessage(ValidationError,
                                      'se pueden devolver 1'):
            self.cambio([(self.renglon, D('2'), False)],
                        medio_pago=f.efectivo(), autorizante=self.encargada)

    def test_no_se_devuelve_un_renglon_de_otra_venta(self):
        otra, _ = self.vender((self.gaseosa, 1, D('0'), None))
        with self.assertRaisesMessage(ValidationError,
                                      'no es de esta venta'):
            self.cambio([(otra.detalles.get(), D('1'), False)],
                        medio_pago=f.efectivo(), autorizante=self.encargada)

    def test_lo_no_apto_no_vuelve_al_stock(self):
        self.cambio([(self.renglon, D('1'), False)],
                    medio_pago=f.efectivo(), autorizante=self.encargada)
        self.gaseosa.refresh_from_db()
        self.assertEqual(self.gaseosa.stock_actual, 7)

    def test_si_se_lleva_algo_mas_caro_paga_la_diferencia(self):
        vino = f.producto('Vino', prov=self.prov, precio='5000')
        f.comprar(self.operador, self.prov, (vino, 1, '3000'))
        antes = caja.saldo(self.turno)
        cambio = self.cambio([(self.renglon, D('1'), True)],
                             [(vino, D('1'))], medio_pago=f.efectivo())
        self.assertEqual(cambio.importe_cobrado, D('3875'))
        self.assertEqual(caja.saldo(self.turno), antes + D('3875'))

    def test_el_ticket_original_no_cambia(self):
        total = self.venta.total_venta
        self.cambio([(self.renglon, D('1'), True)],
                    medio_pago=f.efectivo(), autorizante=self.encargada)
        self.venta.refresh_from_db()
        self.assertEqual(self.venta.total_venta, total)

    def test_vencido_el_plazo_no_hay_cambio(self):
        parametros = ParametrosNegocio.vigentes()
        parametros.dias_para_cambios = 0
        parametros.save()
        Venta.objects.filter(pk=self.venta.pk).update(
            fecha_venta=f.en_dias(-1))
        self.venta.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, 'ya no admite'):
            self.cambio([(self.renglon, D('1'), True)],
                        medio_pago=f.efectivo(), autorizante=self.encargada)

    def test_el_libro_de_caja_cuelga_del_cambio(self):
        cambio = self.cambio([(self.renglon, D('1'), True)],
                             medio_pago=f.efectivo(),
                             autorizante=self.encargada)
        movimiento = MovimientoCaja.objects.get(id_cambio=cambio)
        self.assertEqual(movimiento.tipo_movimiento, 'DEVOLUCION')
