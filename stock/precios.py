"""Actualización de precios en masa: el aumento de la lista del proveedor.

Se elige a qué productos alcanza (un rubro, una marca, un proveedor) y en
qué porcentaje, y a cuánto se redondea el precio nuevo ($10, $50...). La
vista previa muestra cada precio antes y después; recién al confirmar se
escribe, todo junto.
"""
from django.utils import timezone
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from stock.models import TIPOS_VENDIBLES, Producto

REDONDEOS = ((Decimal('0.01'), 'Exacto'), (Decimal('1'), 'Al peso'),
             (Decimal('10'), 'A $10'), (Decimal('50'), 'A $50'),
             (Decimal('100'), 'A $100'))
LIMITE_PORCENTAJE = Decimal('300')


def alcanzados(rubro=None, marca=None, proveedor=None):
    productos = (Producto.objects
                 .filter(borrado_logico=False, tipo_producto__in=TIPOS_VENDIBLES)
                 .select_related('id_marca', 'id_rubro').order_by('nombre_producto'))
    if rubro:
        productos = productos.filter(id_rubro=rubro)
    if marca:
        productos = productos.filter(id_marca=marca)
    if proveedor:
        productos = productos.filter(id_proveedor=proveedor)
    return productos


def nuevo_precio(precio, porcentaje, paso):
    bruto = precio * (Decimal('100') + porcentaje) / Decimal('100')
    redondeado = (bruto / paso).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * paso
    return max(redondeado, paso).quantize(Decimal('0.01'))


def quedan_bajo_costo(productos, porcentaje, paso):
    """Los productos cuyo precio nuevo queda por debajo de lo que costaron."""
    return [p for p, _antes, despues in calcular(productos, porcentaje, paso)
            if p.precio_costo is not None and despues < p.precio_costo]


def calcular(productos, porcentaje, paso):
    if porcentaje is None or porcentaje == 0:
        raise ValidationError('Indicá el porcentaje de aumento (o de rebaja, con signo menos).')
    if porcentaje <= -100 or porcentaje > LIMITE_PORCENTAJE:
        raise ValidationError(f'El porcentaje tiene que estar entre -99 % y {LIMITE_PORCENTAJE} %.')
    if paso not in dict(REDONDEOS):
        raise ValidationError('Elegí cómo se redondea.')
    return [(p, p.precio_venta, nuevo_precio(p.precio_venta, porcentaje, paso)) for p in productos]


@transaction.atomic
def aplicar(productos, porcentaje, paso):
    """Escribe los precios nuevos. Devuelve cuántos cambiaron."""
    ids = [p.pk for p in productos]
    bloqueados = Producto.objects.select_for_update().filter(pk__in=ids)
    cambiados = 0
    for producto, antes, despues in calcular(bloqueados, porcentaje, paso):
        if despues != antes:
            Producto.objects.filter(pk=producto.pk).update(
                precio_venta=despues, fecha_modificacion=timezone.now())
            cambiados += 1
    return cambiados
