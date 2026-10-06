"""Ventas: registrar un ticket y anularlo.

Reemplaza a ``sp_registrar_venta`` y ``sp_anular_venta``, y a los triggers
T08 (``fn_venta_alta`` / ``fn_venta_modificacion``) y T09
(``fn_detalle_venta_valida`` / ``fn_detalle_venta_efectos``).

Tres ideas que conviene tener claras antes de leer el código:

**El renglón congela lo que se cobró.** El nombre y el precio del producto
se COPIAN al detalle (RN-3.1.3). Si mañana el producto cambia de precio o
de nombre, el ticket de ayer sigue diciendo lo que efectivamente se cobró.
Por eso el descuento y el recargo también son RENGLONES y no columnas: lo
que entró en la cuenta queda escrito, no se recalcula (DD-45).

**El total cuadra por construcción.** ``total_venta`` es la suma de todos
los renglones. No se arma por otro lado ni se escribe a mano.

**Una venta no se borra: se contra-asienta.** Anular deja la venta en
ANULADA y escribe los asientos inversos en los libros de caja, de stock y
de puntos. El ticket original queda (DD-26).
"""
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from maestros.models import Beneficio, BeneficioProducto, MedioPago, Producto

from . import caja as servicio_caja
from .models import (DetalleVenta, Lote, MovimientoStock, TurnoCaja, Venta)


CERO = Decimal('0.00')


def _redondear(valor):
    """Dos decimales, con el mismo criterio que usaba ``round()`` en SQL."""
    return Decimal(valor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


# ===========================================================================
#  T09 · EFECTOS DE UN RENGLÓN
# ===========================================================================
def _descontar_stock(renglon, usuario):
    """Saca la mercadería del stock al vender (``fn_detalle_venta_efectos``).

    Un producto que no controla vencimiento descuenta del saldo general.
    Uno que sí lo controla descuenta por lote con criterio **FEFO**:
    *first expired, first out* — primero sale lo que primero vence, que es
    lo que evita que la mercadería se venza en el depósito. Un solo renglón
    puede consumir varios lotes.
    """
    producto = renglon.id_producto
    if producto is None:
        return

    if not producto.controla_vencimiento:
        MovimientoStock.objects.create(
            id_producto=producto, id_usuario=usuario,
            id_venta=renglon.id_venta, tipo_movimiento='SALIDA',
            cantidad=-renglon.cantidad)
        return

    restante = renglon.cantidad
    # FEFO: ascendente por vencimiento, con los lotes SIN vencimiento al
    # final (no son urgentes, salen después de los que vencen).
    lotes = (Lote.objects.select_for_update()
             .filter(id_producto=producto, stock_lote__gt=0)
             .order_by(F('fecha_vencimiento').asc(nulls_last=True),
                       'fecha_alta', 'id_lote'))
    for lote in lotes:
        if restante == 0:
            break
        toma = min(restante, lote.stock_lote)
        MovimientoStock.objects.create(
            id_producto=producto, id_usuario=usuario, id_lote=lote,
            id_venta=renglon.id_venta, tipo_movimiento='SALIDA',
            cantidad=-toma)
        restante -= toma

    if restante > 0:
        raise ValidationError(
            'Stock insuficiente en lotes para "%s": faltan %d unidad(es).'
            % (producto, restante))


def _recalcular_total(venta):
    """``total_venta`` = suma de TODOS los renglones.

    ``monto_descuento`` sale de la línea DESCUENTO y de ninguna otra: desde
    que existe el RECARGO (DD-45), "productos − total" ya no es el
    descuento, porque el recargo también separa esos dos números.
    """
    renglones = DetalleVenta.objects.filter(id_venta=venta)
    total = renglones.aggregate(t=Sum('subtotal'))['t'] or CERO
    descuento = renglones.filter(tipo_linea='DESCUENTO').aggregate(
        t=Sum('subtotal'))['t'] or CERO

    venta.total_venta = total
    venta.monto_descuento = -descuento
    Venta.objects.filter(pk=venta.pk).update(
        total_venta=venta.total_venta, monto_descuento=venta.monto_descuento)
    return venta


def _agregar_renglon(venta, usuario, *, tipo, descripcion, cantidad,
                     precio, producto=None, beneficio=None):
    """Escribe un renglón y dispara sus efectos.

    ``subtotal`` se deriva de cantidad × precio y nunca se recibe de
    afuera: era lo que garantizaba ``ck_detventa_subtotal``.
    """
    renglon = DetalleVenta.objects.create(
        id_venta=venta, id_producto=producto, id_beneficio=beneficio,
        tipo_linea=tipo, descripcion_item=descripcion[:50],
        cantidad=cantidad, precio_unitario=precio,
        subtotal=_redondear(Decimal(cantidad) * Decimal(precio)))

    # PRODUCTO y CORTESIA mueven mercadería; DESCUENTO y RECARGO no: son
    # renglones de importe.
    if tipo in ('PRODUCTO', 'CORTESIA'):
        _descontar_stock(renglon, usuario)

    _recalcular_total(venta)
    return renglon


# ===========================================================================
#  REGISTRAR UNA VENTA
# ===========================================================================
@transaction.atomic
def registrar(turno, usuario, medio_pago, items, cliente=None,
              beneficio=None, monto_recibido=None):
    """Arma el ticket completo (``sp_registrar_venta``).

    ``items`` es una lista de diccionarios::

        [{'producto': <Producto>, 'cantidad': 2},
         {'producto': <Producto>, 'cantidad': 1, 'beneficio': <Beneficio>}]

    El segundo es una **cortesía**: un producto que entra a precio cero
    porque lo cubre un beneficio en especie (DD-18, DD-24).
    """
    turno = TurnoCaja.objects.select_for_update().get(pk=turno.pk)
    if turno.estado_turno != 'ABIERTA':
        raise ValidationError(
            'El turno de caja %s no está abierto (RN-2.2.1).' % turno.pk)

    efectivo = servicio_caja.es_efectivo(medio_pago)
    if monto_recibido is not None and not efectivo:
        raise ValidationError(
            'El monto recibido sólo se registra en ventas en efectivo '
            '(DD-26).')

    venta = Venta.objects.create(
        id_turno_caja=turno, id_usuario=usuario, id_cliente=cliente,
        id_medio_pago=medio_pago, id_beneficio=beneficio,
        estado_venta='COMPLETADA')

    canjes = []

    # ---- los renglones de producto -----------------------------------
    for item in items:
        producto = item['producto']
        if producto.borrado_logico:
            raise ValidationError(
                'El producto "%s" está dado de baja: no se puede vender.'
                % producto)

        cortesia = item.get('beneficio')
        if cortesia is not None:
            if cortesia.modo_entrega != 'ESPECIE' or not (
                    BeneficioProducto.objects.filter(
                        id_beneficio=cortesia, id_producto=producto).exists()):
                raise ValidationError(
                    'El producto "%s" no integra la receta del beneficio '
                    '"%s" (DD-18).' % (producto, cortesia))
            _agregar_renglon(
                venta, usuario, tipo='CORTESIA',
                descripcion=producto.nombre_producto,
                cantidad=item['cantidad'], precio=CERO,
                producto=producto, beneficio=cortesia)
            if cortesia.tipo_beneficio == 'CANJEABLE':
                canjes.append(cortesia)
        else:
            # RN-3.1.3: nombre y precio VIGENTES se copian al renglón.
            _agregar_renglon(
                venta, usuario, tipo='PRODUCTO',
                descripcion=producto.nombre_producto,
                cantidad=item['cantidad'], precio=producto.precio_venta,
                producto=producto)

    # ---- el descuento de cabecera, como renglón negativo (DD-18) -----
    if beneficio is not None:
        if beneficio.modo_entrega != 'DESCUENTO':
            raise ValidationError(
                'El beneficio "%s" no es de descuento.' % beneficio)
        base = (DetalleVenta.objects.filter(id_venta=venta,
                                            tipo_linea='PRODUCTO')
                .aggregate(t=Sum('subtotal'))['t'] or CERO)
        descuento = _redondear(base * beneficio.porcentaje_descuento / 100)
        if descuento > 0:
            _agregar_renglon(
                venta, usuario, tipo='DESCUENTO',
                descripcion=beneficio.descripcion_beneficio,
                cantidad=1, precio=-descuento, beneficio=beneficio)
        if beneficio.tipo_beneficio == 'CANJEABLE':
            canjes.append(beneficio)

    # ---- el recargo del medio de pago, congelado (DD-45) -------------
    porcentaje = medio_pago.recargo_porcentaje or CERO
    if porcentaje > 0:
        base = (DetalleVenta.objects
                .filter(id_venta=venta,
                        tipo_linea__in=('PRODUCTO', 'CORTESIA', 'DESCUENTO'))
                .aggregate(t=Sum('subtotal'))['t'] or CERO)
        recargo = _redondear(base * porcentaje / 100)
        if recargo > 0:
            _agregar_renglon(
                venta, usuario, tipo='RECARGO',
                descripcion='Recargo %s %% %s' % (
                    str(porcentaje).rstrip('0').rstrip('.'),
                    medio_pago.nombre_medio_pago),
                cantidad=1, precio=recargo)

    venta.refresh_from_db()

    # ---- el efectivo entra al cajón (DD-03, DD-46) -------------------
    if efectivo:
        if monto_recibido is not None:
            if monto_recibido < venta.total_venta:
                raise ValidationError(
                    'El efectivo recibido ($ %s) no cubre el total ($ %s).'
                    % (monto_recibido, venta.total_venta))
            Venta.objects.filter(pk=venta.pk).update(
                monto_recibido=monto_recibido)
            venta.refresh_from_db()
        if venta.total_venta > 0:
            servicio_caja.registrar_movimiento(
                turno, usuario, 'VENTA', venta.total_venta,
                'Venta N° %s' % venta.pk, venta=venta)

    # ---- fidelización (DFD 9 es el escritor único del libro, DD-37) ---
    if cliente is not None:
        from . import puntos

        for canjeado in canjes:
            puntos.registrar_canje(venta, canjeado)
        puntos.acumular(venta)

    return venta


# ===========================================================================
#  ANULAR
# ===========================================================================
@transaction.atomic
def anular(venta, usuario, motivo):
    """Contra-asienta la venta; no la borra (``sp_anular_venta``, DD-26).

    Una venta borrada se lleva puesto el arqueo del día y la trazabilidad
    del stock. Anular deja el ticket donde está, lo marca ANULADA y escribe
    los asientos inversos en los tres libros.
    """
    venta = Venta.objects.select_for_update().get(pk=venta.pk)

    if venta.estado_venta == 'ANULADA':
        raise ValidationError('La venta %s ya está anulada.' % venta.pk)

    turno = venta.id_turno_caja
    if turno.estado_turno != 'ABIERTA':
        raise ValidationError(
            'La venta %s pertenece a un turno de caja cerrado: ya no se '
            'anula (DD-26).' % venta.pk)

    if not motivo or not motivo.strip():
        raise ValidationError('La anulación exige declarar el motivo (DD-26).')

    # Stock: vuelve lo que salió, lote por lote.
    for movimiento in MovimientoStock.objects.filter(id_venta=venta):
        MovimientoStock.objects.create(
            id_producto=movimiento.id_producto, id_usuario=usuario,
            id_lote=movimiento.id_lote, id_venta=venta,
            tipo_movimiento='ENTRADA', cantidad=-movimiento.cantidad)

    # Caja: sale del cajón lo que había entrado.
    if servicio_caja.es_efectivo(venta.id_medio_pago) and venta.total_venta > 0:
        servicio_caja.registrar_movimiento(
            turno, usuario, 'ANULACION', -venta.total_venta,
            'Anulación de la venta N° %s' % venta.pk, venta=venta)

    # Puntos: se revierte lo acumulado y lo canjeado.
    if venta.id_cliente_id:
        from . import puntos

        puntos.revertir(venta, usuario)

    Venta.objects.filter(pk=venta.pk).update(
        estado_venta='ANULADA', fecha_anulacion=timezone.now(),
        motivo_anulacion=motivo.strip()[:120], id_usuario_anulacion=usuario)
    venta.refresh_from_db()
    return venta
