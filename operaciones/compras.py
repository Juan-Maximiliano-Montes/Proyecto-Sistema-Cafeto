"""Compras: órdenes al proveedor y recepción de mercadería.

Las reglas de ciclo de vida de la orden (T12) viven en
``OrdenCompra.save()``, porque son invariantes de la fila; acá están los
PROCESOS, que son otra cosa: una secuencia de pasos que tienen que pasar
todos o ninguno.

El ciclo de una orden (DD-44)::

    PENDIENTE ──► EMITIDA ──► RECEPCIONADA
        │             │
        └──────► CANCELADA ◄──┘

El borrador se corrige libremente. Una vez EMITIDA ya comprometió al
proveedor, así que su contenido se congela: sólo se recepciona o se
cancela.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum

from maestros.models import Producto

from .models import (Compra, DetalleCompra, DetalleOrdenCompra, Lote,
                     MovimientoStock, OrdenCompra)


# ===========================================================================
#  LA ORDEN
# ===========================================================================
@transaction.atomic
def emitir_orden(proveedor, usuario, items):
    """Crea el borrador con sus renglones.

    ``items``: ``[{'producto': <Producto>, 'cantidad': 10,
    'precio_unitario': Decimal('500')}]``

    Nace PENDIENTE: todavía no se le mandó nada al proveedor.
    """
    if not items:
        raise ValidationError('Una orden de compra sin renglones no se emite.')

    orden = OrdenCompra.objects.create(
        id_proveedor=proveedor, id_usuario=usuario, estado_orden='PENDIENTE')

    for item in items:
        DetalleOrdenCompra.objects.create(
            id_orden_compra=orden, id_producto=item['producto'],
            cantidad_pedida=item['cantidad'],
            precio_unitario=item['precio_unitario'])

    return recalcular_total_de_la_orden(orden)


def recalcular_total_de_la_orden(orden):
    """``total_orden`` = suma de los renglones; no se carga a mano."""
    total = (DetalleOrdenCompra.objects.filter(id_orden_compra=orden)
             .aggregate(t=Sum(F('cantidad_pedida') * F('precio_unitario')))
             ['t'] or Decimal('0.00'))
    orden.total_orden = total
    orden.save(update_fields=['total_orden'])
    return orden


@transaction.atomic
def enviar_orden(orden):
    """La manda al proveedor: PENDIENTE → EMITIDA.

    Es el momento en que deja de ser un borrador editable (DD-44).
    """
    orden = OrdenCompra.objects.select_for_update().get(pk=orden.pk)
    if not DetalleOrdenCompra.objects.filter(id_orden_compra=orden).exists():
        raise ValidationError(
            'La orden %s no tiene renglones: no hay qué pedir.' % orden.pk)
    orden.estado_orden = 'EMITIDA'
    orden.save(update_fields=['estado_orden'])
    return orden


@transaction.atomic
def cancelar_orden(orden):
    """PENDIENTE o EMITIDA → CANCELADA."""
    orden = OrdenCompra.objects.select_for_update().get(pk=orden.pk)
    orden.estado_orden = 'CANCELADA'
    orden.save(update_fields=['estado_orden'])
    return orden


# ===========================================================================
#  LA RECEPCIÓN
# ===========================================================================
@transaction.atomic
def recepcionar(proveedor, usuario, numero_factura, items, orden=None,
                fecha_vencimiento_pago=None):
    """Registra la factura, da de alta los lotes y entra el stock.

    ``items``::

        [{'producto': <Producto>, 'cantidad': 10,
          'precio_costo': Decimal('450'),
          'numero_lote': 'L-2026-04',             # si controla vencimiento
          'fecha_vencimiento': date(2027, 1, 1)}]

    Todo en una transacción: si un renglón falla, no queda media compra
    cargada ni stock que entró sin su factura.
    """
    if orden is not None:
        # DD-44: sólo se recepciona lo que ya se pidió.
        orden = OrdenCompra.objects.select_for_update().get(pk=orden.pk)
        if orden.estado_orden != 'EMITIDA' or orden.id_proveedor_id != proveedor.pk:
            raise ValidationError(
                'La orden %s no está emitida o es de otro proveedor '
                '(RN-4.2.1, DD-44).' % orden.pk)

    total = sum(Decimal(i['cantidad']) * Decimal(i['precio_costo'])
                for i in items)

    compra = Compra.objects.create(
        id_proveedor=proveedor, id_orden_compra=orden, id_usuario=usuario,
        numero_factura=numero_factura, total_compra=total,
        fecha_vencimiento_pago=fecha_vencimiento_pago)

    for item in items:
        producto = item['producto']
        detalle = DetalleCompra.objects.create(
            id_compra=compra, id_producto=producto,
            cantidad_recibida=item['cantidad'],
            precio_costo_unitario=item['precio_costo'])

        lote = None
        if producto.controla_vencimiento:
            if not item.get('numero_lote'):
                raise ValidationError(
                    'El producto "%s" se gestiona por lote: la recepción '
                    'exige el número de lote (DD-20).' % producto)
            lote = Lote.objects.create(
                id_producto=producto, id_detalle_compra=detalle,
                numero_lote=item['numero_lote'],
                fecha_vencimiento=item.get('fecha_vencimiento'),
                cantidad_inicial=item['cantidad'])

        # El asiento mueve el stock: lo hace MovimientoStock.save().
        MovimientoStock.objects.create(
            id_producto=producto, id_usuario=usuario, id_lote=lote,
            id_compra=compra, tipo_movimiento='ENTRADA',
            cantidad=item['cantidad'])

        # RN-4.2.4: el costo queda en el ÚLTIMO precio facturado.
        Producto.objects.filter(pk=producto.pk).update(
            precio_costo=item['precio_costo'])

    if orden is not None:
        orden.estado_orden = 'RECEPCIONADA'
        orden.save(update_fields=['estado_orden'])

    return compra
