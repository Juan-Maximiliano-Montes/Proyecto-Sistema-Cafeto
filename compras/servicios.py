"""Compras: órdenes al proveedor y recepción de mercadería.

Las reglas de ciclo de vida de la orden (T12) viven en
``OrdenCompra.save()``, porque son invariantes de la fila; acá están los
PROCESOS, que son otra cosa: una secuencia de pasos que tienen que pasar
todos o ninguno.

El ciclo de una orden (DD-44)::

    PENDIENTE ──► EMITIDA ──► RECEPCIONADA
        │             │
        └──────► CANCELADA ◄──┘

La recepción (``recepcionar``) es el ingreso de mercadería: registra la
factura, da de alta los lotes —un renglón puede traer varios, con
vencimientos distintos— y entra el stock, todo en una transacción. Si un
renglón falla no queda media compra cargada ni stock sin su factura.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from compras.models import (
    Compra,
    DetalleCompra,
    DetalleOrdenCompra,
    OrdenCompra,
    TipoComprobante,
)
from stock.models import (
    Lote,
    MovimientoStock,
    Producto,
    TIPOS_CON_PROVEEDOR,
    TipoMovimientoStock,
)
from stock.servicios import exigir_cantidad_valida

CENTAVO = Decimal('0.01')


# ===========================================================================
#  LA ORDEN
# ===========================================================================
@transaction.atomic
@transaction.atomic
def emitir_orden(proveedor, usuario, items):
    """Crea el borrador con sus renglones.

    ``items``: ``[{'producto': <Producto>, 'cantidad': 10,
    'precio_unitario': Decimal('500')}]``. Nace PENDIENTE.
    """
    if not items:
        raise ValidationError('Una orden de compra sin renglones no se emite.')
    if not proveedor.vende_mercaderia:
        raise ValidationError(
            f'"{proveedor}" es un proveedor de servicios: no se le piden '
            'productos.')
    vistos = set()
    for item in items:
        producto = item['producto']
        if producto.borrado_logico:
            raise ValidationError(f'"{producto}" está dado de baja: no se puede pedir.')
        if producto.tipo_producto not in TIPOS_CON_PROVEEDOR:
            raise ValidationError(
                f'"{producto}" se elabora en el local: no se le pide a un proveedor.')
        if producto.pk in vistos:
            raise ValidationError(
                f'"{producto}" está dos veces en el pedido: dejá un solo '
                'renglón con la cantidad total.')
        vistos.add(producto.pk)
    orden = OrdenCompra.objects.create(
        id_proveedor=proveedor, id_usuario=usuario, estado_orden='PENDIENTE')
    for item in items:
        DetalleOrdenCompra.objects.create(
            id_orden_compra=orden, id_producto=item['producto'],
            cantidad_pedida=exigir_cantidad_valida(item['producto'],
                                                   item['cantidad']),
            precio_unitario=item['precio_unitario'])
    return recalcular_total_de_la_orden(orden)


def recalcular_total_de_la_orden(orden):
    """``total_orden`` = suma de los renglones; no se carga a mano."""
    total = (DetalleOrdenCompra.objects.filter(id_orden_compra=orden)
             .aggregate(t=Sum(F('cantidad_pedida') * F('precio_unitario')))
             ['t'] or Decimal('0.00'))
    orden.total_orden = Decimal(total).quantize(CENTAVO)
    orden.save(update_fields=['total_orden'])
    return orden


@transaction.atomic
def enviar_orden(orden):
    """La manda al proveedor: PENDIENTE → EMITIDA."""
    orden = OrdenCompra.objects.select_for_update().get(pk=orden.pk)
    if not DetalleOrdenCompra.objects.filter(id_orden_compra=orden).exists():
        raise ValidationError(
            f'La orden {orden.pk} no tiene renglones: no hay qué pedir.')
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
@dataclass
class LoteRecibido:
    fecha_vencimiento: object
    cantidad: Decimal
    numero_lote: str = None


@dataclass
class RenglonRecibido:
    producto: Producto
    cantidad: Decimal
    costo_unitario: Decimal
    lotes: list = field(default_factory=list)


# Ni las conservas duran tanto: más allá de esto es un año mal tipeado.
VENCIMIENTO_MAXIMO = timedelta(days=3660)

SALTO_DE_COSTO = 3


def costos_que_llaman_la_atencion(renglones):
    """Renglones cuyo costo es más del triple, o menos de un tercio, del anterior.

    Casi siempre es un cero de más o de menos al tipear. No se prohíbe (los
    precios cambian): se pide que quien carga lo mire y lo confirme. Lo usan
    la pantalla de recepción y la API móvil.
    """
    raros = []
    for renglon in renglones:
        anterior = renglon.producto.precio_costo
        try:
            nuevo = Decimal(renglon.costo_unitario)
        except (InvalidOperation, TypeError):
            continue
        if not anterior or anterior <= 0 or nuevo <= 0:
            continue
        if nuevo > anterior * SALTO_DE_COSTO or nuevo * SALTO_DE_COSTO < anterior:
            raros.append({'producto': renglon.producto, 'anterior': anterior, 'nuevo': nuevo})
    return raros


def _validar_renglon(renglon, fecha_compra):
    producto = renglon.producto
    if producto.borrado_logico:
        raise ValidationError(f'"{producto}" está dado de baja.')
    if producto.tipo_producto not in TIPOS_CON_PROVEEDOR:
        raise ValidationError(
            f'"{producto}" se elabora en el local: no se compra.')
    cantidad = exigir_cantidad_valida(producto, renglon.cantidad,
                                      'La cantidad recibida')
    costo = Decimal(renglon.costo_unitario)
    if costo < 0:
        raise ValidationError(
            f'El costo de "{producto}" no puede ser negativo.')
    if producto.controla_vencimiento:
        if not renglon.lotes:
            raise ValidationError(
                f'"{producto}" controla vencimiento: cargá el o los lotes '
                'con su fecha.')
        suma = sum((Decimal(l.cantidad) for l in renglon.lotes), Decimal('0'))
        if suma != cantidad:
            raise ValidationError(
                f'Los lotes de "{producto}" suman '
                f'{producto.cantidad_con_unidad(suma)} y se recibieron '
                f'{producto.cantidad_con_unidad(cantidad)}.')
        vencidos = [l for l in renglon.lotes
                    if l.fecha_vencimiento and l.fecha_vencimiento < fecha_compra]
        if vencidos:
            raise ValidationError(
                f'Un lote de "{producto}" vence el '
                f'{vencidos[0].fecha_vencimiento:%d/%m/%Y}, antes de la fecha '
                'de la compra: la mercadería no se recibe vencida.')
        lejanos = [l for l in renglon.lotes if l.fecha_vencimiento
                   and l.fecha_vencimiento > fecha_compra + VENCIMIENTO_MAXIMO]
        if lejanos:
            raise ValidationError(
                f'Un lote de "{producto}" vence el '
                f'{lejanos[0].fecha_vencimiento:%d/%m/%Y}: es dentro de más de '
                '10 años. Revisá el año.')
        numeros = [(l.numero_lote or '').strip().lower()
                   for l in renglon.lotes if l.numero_lote]
        if len(numeros) != len(set(numeros)):
            raise ValidationError(
                f'"{producto}" tiene un número de lote repetido.')
    elif renglon.lotes:
        raise ValidationError(
            f'"{producto}" no controla vencimiento: no lleva lotes.')
    return cantidad, costo


# Cuánto para atrás puede estar fechado un comprobante que se carga hoy.
DIAS_DE_ATRASO_ADMITIDOS = timedelta(days=30)


@transaction.atomic
def recepcionar(proveedor, usuario, tipo_comprobante, punto_venta,
                numero_comprobante, renglones, fecha_compra=None,
                importe_otros_conceptos=Decimal('0'), orden=None,
                fecha_vencimiento_pago=None):
    """Registra la factura, da de alta los lotes y entra el stock.

    ``renglones`` es una lista de ``RenglonRecibido``. El total de la
    compra es la suma de los renglones más los otros conceptos
    (percepciones, flete): no se tipea, así no puede no cuadrar.
    """
    if proveedor.borrado_logico or not proveedor.vende_mercaderia:
        raise ValidationError(
            f'"{proveedor}" no está vigente como proveedor de mercadería.')
    if not renglones:
        raise ValidationError('La compra no tiene ningún renglón.')
    if tipo_comprobante not in TipoComprobante.values:
        raise ValidationError('Elegí el tipo de comprobante.')
    if orden is not None:
        orden = OrdenCompra.objects.select_for_update().get(pk=orden.pk)
        if (not orden.admite_recepcion
                or orden.id_proveedor_id != proveedor.pk):
            raise ValidationError(
                f'La orden {orden.pk} no está emitida o es de otro '
                'proveedor (DD-44).')

    hoy = timezone.localdate()
    fecha = fecha_compra or hoy
    if fecha > hoy:
        raise ValidationError(
            'La fecha de la compra no puede ser posterior a hoy.')
    # La fecha de la factura puede ser anterior a hoy por dos motivos
    # legítimos: el proveedor la emitió antes de entregar, o se carga con
    # atraso. Más de 30 días para atrás ya no es eso: la mercadería entraría
    # al stock y al costo de un mes cuyos reportes y cierres ya se miraron.
    if fecha < hoy - DIAS_DE_ATRASO_ADMITIDOS:
        raise ValidationError(
            f'La factura es del {fecha:%d/%m/%Y}: tiene más de '
            f'{DIAS_DE_ATRASO_ADMITIDOS.days} días. Revisá la fecha; si de '
            'verdad es una factura vieja, cargala con la fecha de hoy y '
            'aclaralo en la orden o con el proveedor.')
    if fecha_vencimiento_pago is not None and fecha_vencimiento_pago < fecha:
        raise ValidationError(
            'El pago no puede vencer antes de la fecha de la factura.')
    # Las reglas ck_compras_comprobante de la base, dichas antes de chocar
    # contra ella: un 0 terminaba en "ya está cargada", que no era cierto.
    try:
        punto, numero = int(punto_venta), int(numero_comprobante)
    except (TypeError, ValueError):
        raise ValidationError(
            'El punto de venta y el número del comprobante van en números.')
    if not 1 <= punto <= 99999:
        raise ValidationError('El punto de venta va de 1 a 99999.')
    if not 1 <= numero <= 99999999:
        raise ValidationError('El número del comprobante va de 1 a 99999999.')

    vistos = set()
    validados = []
    for renglon in renglones:
        if renglon.producto.pk in vistos:
            raise ValidationError(
                f'"{renglon.producto}" está dos veces: sumá las cantidades '
                'en un renglón (con sus lotes).')
        vistos.add(renglon.producto.pk)
        validados.append((renglon, *_validar_renglon(renglon, fecha)))

    otros = Decimal(importe_otros_conceptos or 0).quantize(CENTAVO)
    if otros < 0:
        raise ValidationError('Los otros conceptos no pueden ser negativos.')
    mercaderia = sum(((c * k).quantize(CENTAVO) for _, c, k in validados),
                     Decimal('0'))
    total = mercaderia + otros
    if total <= 0:
        raise ValidationError('El total de la compra tiene que ser mayor '
                              'que cero.')

    datos = dict(
        id_proveedor=proveedor, id_orden_compra=orden, id_usuario=usuario,
        tipo_comprobante=tipo_comprobante, punto_venta=int(punto_venta),
        numero_comprobante=int(numero_comprobante),
        importe_otros_conceptos=otros, total_compra=total,
        fecha_vencimiento_pago=fecha_vencimiento_pago)
    if fecha_compra is not None:
        datos['fecha_compra'] = fecha_compra
    compra = Compra(**datos)
    compra.full_clean(exclude=['id_usuario'], validate_unique=False,
                      validate_constraints=False)
    if Compra.objects.filter(
            id_proveedor=proveedor, tipo_comprobante=tipo_comprobante,
            punto_venta=compra.punto_venta,
            numero_comprobante=compra.numero_comprobante).exists():
        raise ValidationError(
            f'La {compra.get_tipo_comprobante_display()} '
            f'{compra.numero_formateado} de {proveedor} ya está cargada.')
    try:
        with transaction.atomic():
            compra.save()
    except IntegrityError as error:
        # Sólo el índice único es "ya está cargada" (dos personas cargando
        # la misma factura a la vez). Cualquier otro rechazo de la base no
        # es un duplicado, y decir que lo era confundía.
        if 'uq_compras_comprobante' in str(error):
            raise ValidationError(
                f'La {compra.get_tipo_comprobante_display()} '
                f'{compra.numero_formateado} de {proveedor} ya está cargada.')
        raise ValidationError(
            'La compra no cumple una regla del sistema: revisá las fechas, '
            'el número y los importes.')

    documento = {'id_compra': compra}
    for renglon, cantidad, costo in validados:
        producto = renglon.producto
        detalle = DetalleCompra.objects.create(
            id_compra=compra, id_producto=producto,
            cantidad_recibida=cantidad, precio_costo_unitario=costo)
        if producto.controla_vencimiento:
            for recibido in renglon.lotes:
                lote = Lote(
                    id_producto=producto, id_detalle_compra=detalle,
                    numero_lote=(recibido.numero_lote or '').strip() or None,
                    fecha_vencimiento=recibido.fecha_vencimiento,
                    cantidad_inicial=Decimal(recibido.cantidad))
                lote.full_clean(validate_constraints=False)
                lote.save()
                MovimientoStock.objects.create(
                    id_producto=producto, id_usuario=usuario, id_lote=lote,
                    tipo_movimiento=TipoMovimientoStock.COMPRA,
                    cantidad=Decimal(recibido.cantidad), costo_unitario=costo,
                    **documento)
        else:
            MovimientoStock.objects.create(
                id_producto=producto, id_usuario=usuario,
                tipo_movimiento=TipoMovimientoStock.COMPRA,
                cantidad=cantidad, costo_unitario=costo, **documento)
        # RN-4.2.4: el costo queda en el ÚLTIMO precio facturado.
        Producto.objects.filter(pk=producto.pk).update(precio_costo=costo)

    if orden is not None:
        # La planilla de control (DFD 3): si lo recibido no coincide con lo
        # pedido, la orden queda CON_DIFERENCIAS para que la Administradora
        # lo resuelva con el proveedor (DD-64).
        orden.estado_orden = ('CON_DIFERENCIAS' if orden.hay_diferencias()
                              else 'RECEPCIONADA')
        orden.save(update_fields=['estado_orden'])
    return compra


@transaction.atomic
def cerrar_con_diferencias(orden, observacion):
    """La Administradora acordó la diferencia con el proveedor (nota de
    crédito, faltante que no se repone, sobrante que se queda): la orden se
    da por recibida y deja de esperar mercadería (DD-64)."""
    orden = OrdenCompra.objects.select_for_update().get(pk=orden.pk)
    if orden.estado_orden != 'CON_DIFERENCIAS':
        raise ValidationError(
            f'La orden {orden.pk} no tiene diferencias pendientes.')
    observacion = (observacion or '').strip()
    if len(observacion) < 5:
        raise ValidationError(
            'Contá cómo se resolvió la diferencia con el proveedor.')
    orden.estado_orden = 'RECEPCIONADA'
    orden.save(update_fields=['estado_orden'])
    return orden
