"""Cambios y devoluciones: lo que pasa DESPUÉS de una venta.

El cambio no anula la venta —el ticket que se llevó el cliente dice lo que
dice—. Si algo vuelve, se registra un CAMBIO que referencia la venta
original (la anulación, para una venta que no debió existir, está en
``ventas.anulacion`` y sólo vale con el turno abierto):

* lo que el cliente DEVUELVE sale de un renglón de esa venta (no se puede
  devolver más de lo que se compró, ni dos veces lo mismo) y se valoriza a
  lo que efectivamente pagó por unidad, descuento incluido;
* lo que se LLEVA a cambio se cobra a precio de hoy y sale del stock;
* la diferencia se cobra o se devuelve. Devolver dinero lo autoriza
  alguien con permiso, y queda escrito.

Lo devuelto en buen estado vuelve al stock (al lote del que salió); lo
que no está apto —la lata abollada, el yogur vencido— no reingresa y queda
como pérdida en el renglón.
"""
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from negocio.models import ParametrosNegocio
from nucleo.modelos import redondear_importe
from stock import servicios as stock
from stock.models import MovimientoStock, TipoMovimientoStock
from ventas import caja as servicio_caja
from ventas.models import (
    Cambio,
    DetalleCambio,
    DetalleVenta,
    MotivoCambio,
    TipoMovimientoCaja,
    TurnoCaja,
    Venta,
)

CERO = Decimal('0')
CENTAVO = Decimal('0.01')


@dataclass
class Devuelto:
    detalle_venta: object
    cantidad: Decimal
    reingresa: bool


@dataclass
class Entregado:
    producto: object
    cantidad: Decimal


def ya_devuelto(detalle_venta):
    return (DetalleCambio.objects.filter(id_detalle_venta=detalle_venta)
            .aggregate(t=Sum('cantidad'))['t'] or CERO)


def devolvible(detalle_venta):
    return detalle_venta.cantidad - ya_devuelto(detalle_venta)


def balance(venta, devueltos, entregados):
    """(total devuelto, total entregado, saldo redondeado).

    saldo > 0: el cliente paga la diferencia; < 0: se le devuelve.
    """
    valor_devuelto = CERO
    for item in devueltos:
        if item.detalle_venta.id_venta_id != venta.pk:
            raise ValidationError(
                'Uno de los renglones devueltos no es de esta venta.')
        valor_devuelto += (Decimal(item.cantidad)
                           * item.detalle_venta.precio_reintegrable)
    valor_entregado = sum(
        (Decimal(e.cantidad) * e.producto.precio_venta for e in entregados),
        CERO)
    paso = ParametrosNegocio.vigentes().redondeo_ventas
    saldo = redondear_importe(valor_entregado - valor_devuelto, paso)
    return (valor_devuelto.quantize(CENTAVO),
            valor_entregado.quantize(CENTAVO), saldo)


def registrar(turno, usuario, venta, motivo, devueltos, entregados=(),
              detalle_motivo=None, medio_pago=None, autorizante=None,
              token=None):
    """Registra el cambio o la devolución. Devuelve el ``Cambio``.

    Igual que la venta, es idempotente por ``token``.
    """
    token = token or uuid.uuid4()
    existente = Cambio.objects.filter(token_operacion=token).first()
    if existente is not None:
        return existente
    try:
        with transaction.atomic():
            return _registrar(turno, usuario, venta, motivo, devueltos,
                              list(entregados), detalle_motivo, medio_pago,
                              autorizante, token)
    except IntegrityError:
        existente = Cambio.objects.filter(token_operacion=token).first()
        if existente is not None:
            return existente
        raise


def _registrar(turno, usuario, venta, motivo, devueltos, entregados,
               detalle_motivo, medio_pago, autorizante, token):
    turno = TurnoCaja.objects.select_for_update().get(pk=turno.pk)
    if not turno.abierto:
        raise ValidationError('El turno de caja está cerrado.')
    # La venta se bloquea antes de mirar cuánto queda por devolver: dos
    # cajas que devuelven el mismo renglón a la vez no pueden leer las dos
    # el mismo saldo y devolver, entre las dos, más de lo que se compró.
    venta = Venta.objects.select_for_update().get(pk=venta.pk)
    if venta.anulada:
        raise ValidationError(
            f'La venta N° {venta.pk} está anulada: no admite cambios ni '
            'devoluciones (lo que se llevó ya volvió con la anulación).')
    renglones = DetalleVenta.objects.in_bulk(
        [item.detalle_venta.pk for item in devueltos])
    for item in devueltos:
        renglon = renglones.get(item.detalle_venta.pk)
        if renglon is None or renglon.id_venta_id != venta.pk:
            raise ValidationError(
                'Uno de los renglones devueltos no es de esta venta.')
        item.detalle_venta = renglon
    if motivo not in MotivoCambio.values:
        raise ValidationError('Elegí el motivo del cambio.')
    detalle_motivo = (detalle_motivo or '').strip() or None
    if motivo == MotivoCambio.OTRO and not detalle_motivo:
        raise ValidationError('Contá el motivo del cambio.')
    if not devueltos:
        raise ValidationError(
            'Indicá qué devuelve el cliente: un cambio siempre parte de algo '
            'que se compró.')

    dias = ParametrosNegocio.vigentes().dias_para_cambios
    if venta.fecha_venta < timezone.localdate() - timedelta(days=dias):
        raise ValidationError(
            f'La venta N° {venta.pk} es del {venta.fecha_venta:%d/%m/%Y}: '
            f'pasaron más de {dias} días y ya no admite cambios.')

    # Lo que se devuelve: nunca más de lo comprado.
    for item in devueltos:
        renglon = item.detalle_venta
        cantidad = stock.exigir_cantidad_valida(
            renglon.id_producto, item.cantidad, 'La cantidad devuelta')
        queda = devolvible(renglon)
        if cantidad > queda:
            raise ValidationError(
                f'De "{renglon.descripcion_item}" se compraron '
                f'{renglon.cantidad} y ya se devolvieron '
                f'{renglon.cantidad - queda}: se pueden devolver {queda}.')
        if item.reingresa and not renglon.id_producto.tiene_stock_propio:
            raise ValidationError(
                f'"{renglon.descripcion_item}" se prepara al momento: lo '
                'devuelto no vuelve al stock.')
    for item in entregados:
        if item.producto.borrado_logico or not item.producto.es_vendible:
            raise ValidationError(f'"{item.producto}" no se puede entregar.')
        stock.exigir_cantidad_valida(item.producto, item.cantidad)
        stock.exigir_disponible(item.producto, Decimal(item.cantidad))

    _, _, saldo = balance(venta, devueltos, entregados)
    importe_cobrado = saldo if saldo > 0 else CERO
    importe_devuelto = -saldo if saldo < 0 else CERO
    if saldo and medio_pago is None:
        raise ValidationError(
            f'Hay una diferencia de $ {abs(saldo)}: indicá con qué medio '
            f'se {"cobra" if saldo > 0 else "devuelve"}.')
    if not saldo:
        medio_pago = None
    if importe_devuelto and autorizante is None:
        raise ValidationError(
            f'Devolver $ {importe_devuelto} al cliente lo tiene que '
            'autorizar alguien con permiso.')

    cambio = Cambio.objects.create(
        id_venta=venta, id_turno_caja=turno, id_usuario=usuario,
        id_usuario_autorizante=autorizante if importe_devuelto else None,
        id_medio_pago=medio_pago, token_operacion=token, motivo=motivo,
        detalle_motivo=detalle_motivo, importe_devuelto=importe_devuelto,
        importe_cobrado=importe_cobrado)

    for item in devueltos:
        renglon = item.detalle_venta
        precio = renglon.precio_reintegrable
        cantidad = Decimal(item.cantidad)
        DetalleCambio.objects.create(
            id_cambio=cambio, sentido='DEVUELVE',
            id_producto=renglon.id_producto, id_detalle_venta=renglon,
            cantidad=cantidad, precio_unitario=precio,
            subtotal=(cantidad * precio).quantize(CENTAVO),
            reingresa_stock=bool(item.reingresa))
        if item.reingresa:
            lotes = [m.id_lote for m in MovimientoStock.objects.filter(
                id_venta=venta, id_producto=renglon.id_producto)
                .select_related('id_lote')]
            stock.reingresar(renglon.id_producto, cantidad, usuario, cambio,
                             lotes)

    for item in entregados:
        cantidad = Decimal(item.cantidad)
        precio = item.producto.precio_venta
        DetalleCambio.objects.create(
            id_cambio=cambio, sentido='ENTREGA', id_producto=item.producto,
            cantidad=cantidad, precio_unitario=precio,
            subtotal=(cantidad * precio).quantize(CENTAVO))
        stock.descontar(item.producto, cantidad, usuario,
                        TipoMovimientoStock.CAMBIO, {'id_cambio': cambio})

    # DFD 9 bloque D: los puntos de la compra se ajustan a lo que el
    # cliente se quedó (escritor único del libro de puntos, DD-37).
    cambio.reverso_puntos = None
    if venta.id_cliente_id:
        from fidelizacion import puntos

        cambio.reverso_puntos = puntos.revertir_por_devolucion(venta)

    if medio_pago is not None and medio_pago.afecta_caja:
        if importe_cobrado:
            servicio_caja.registrar_movimiento(
                turno, usuario, TipoMovimientoCaja.COBRO_CAMBIO,
                importe_cobrado, f'Cambio N° {cambio.pk} (venta '
                f'N° {venta.pk})', id_cambio=cambio)
        else:
            servicio_caja.registrar_movimiento(
                turno, usuario, TipoMovimientoCaja.DEVOLUCION,
                -importe_devuelto, f'Devolución N° {cambio.pk} (venta '
                f'N° {venta.pk})', id_cambio=cambio)
    return cambio
