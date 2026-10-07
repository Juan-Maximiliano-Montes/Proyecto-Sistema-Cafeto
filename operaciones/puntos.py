"""Fidelización: acumular, canjear y revertir puntos.

Los controles del asiento de puntos (T10) y los tres procesos que lo
escriben: acumular, canjear y revertir.
La parte que mueve los saldos y recategoriza vive en
``MovimientoPuntos.save()``, porque tiene que correr aunque el asiento se
escriba desde otro lado.

La distinción que ordena todo el módulo: **disponibles** son los puntos que
al cliente le quedan por gastar; **acumulados** son los que ganó en toda su
historia y deciden su categoría. Un canje baja los disponibles pero no toca
los acumulados — gastar los puntos no borra el mérito de haberlos ganado
(DD-33). Por eso la categoría sólo sube (DD-11).
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import MovimientoPuntos, Venta


def _validar(cliente, venta, tipo, beneficio=None):
    """Los controles del asiento de puntos (T10)."""
    if venta is not None:
        if venta.id_cliente_id != getattr(cliente, 'pk', cliente):
            raise ValidationError(
                'El movimiento de puntos no corresponde al cliente de la '
                'venta %s.' % venta.pk)
        if tipo in ('ACUMULACION', 'CANJE') and venta.estado_venta != 'COMPLETADA':
            raise ValidationError(
                'La venta %s está anulada: no acumula ni honra canjes.'
                % venta.pk)
        if tipo == 'ANULACION' and venta.estado_venta != 'ANULADA':
            raise ValidationError(
                'Sólo se revierten puntos de una venta anulada (DD-33).')


@transaction.atomic
def acumular(venta):
    """Suma los puntos que la venta generó.

    DD-49: hacen falta ``importe_por_punto`` pesos para ganar un punto, y
    el divisor es el de la categoría del cliente. La división se trunca
    hacia abajo: se premia el punto COMPLETO, no la fracción.
    """
    cliente = venta.id_cliente
    if cliente is None:
        return 0

    importe_por_punto = cliente.id_categoria.importe_por_punto
    if not importe_por_punto:
        return 0

    puntos = int(venta.total_venta // importe_por_punto)
    if puntos <= 0:
        return 0

    _validar(cliente, venta, 'ACUMULACION')
    MovimientoPuntos.objects.create(
        id_cliente=cliente, id_venta=venta,
        tipo_movimiento='ACUMULACION', puntos=puntos)
    return puntos


@transaction.atomic
def registrar_canje(venta, beneficio):
    """Descuenta los puntos que cuesta el beneficio.

    Cuatro controles, todos de T10. El del saldo va último y con
    el cliente bloqueado: si no, dos canjes simultáneos leerían el mismo
    saldo y el cliente gastaría puntos que no tiene.
    """
    from .models import Cliente

    cliente = venta.id_cliente
    _validar(cliente, venta, 'CANJE', beneficio)

    if beneficio.tipo_beneficio != 'CANJEABLE' or beneficio.borrado_logico:
        raise ValidationError(
            'El beneficio "%s" no es canjeable.' % beneficio)

    hoy = timezone.localdate()
    if hoy < beneficio.vigencia_desde or (
            beneficio.vigencia_hasta and hoy > beneficio.vigencia_hasta):
        raise ValidationError(
            'El beneficio "%s" no está vigente.' % beneficio)

    if beneficio.id_categoria.puntos_minimos > cliente.id_categoria.puntos_minimos:
        raise ValidationError(
            'El beneficio "%s" pertenece a una categoría superior a la del '
            'cliente.' % beneficio)

    bloqueado = Cliente.objects.select_for_update().get(pk=cliente.pk)
    if bloqueado.puntos_disponibles < beneficio.puntos_requeridos:
        raise ValidationError(
            'Puntos insuficientes: dispone de %d y el beneficio requiere %d '
            '(DD-11).' % (bloqueado.puntos_disponibles,
                          beneficio.puntos_requeridos))

    return MovimientoPuntos.objects.create(
        id_cliente=cliente, id_venta=venta, id_beneficio=beneficio,
        tipo_movimiento='CANJE', puntos=-beneficio.puntos_requeridos)


@transaction.atomic
def revertir(venta, usuario=None):
    """Deshace los puntos de una venta anulada.

    Cada movimiento se revierte **una sola vez** (DD-33): si la anulación
    se reintentara, el cliente terminaría con puntos de regalo. Por eso se
    salta lo que ya tiene su contra-asiento.
    """
    revertidos = 0
    originales = MovimientoPuntos.objects.filter(
        id_venta=venta, tipo_movimiento__in=('ACUMULACION', 'CANJE'))

    for movimiento in originales:
        ya_revertido = MovimientoPuntos.objects.filter(
            id_venta=venta, tipo_movimiento='ANULACION',
            id_beneficio=movimiento.id_beneficio).exists()
        if ya_revertido:
            continue
        MovimientoPuntos.objects.create(
            id_cliente=movimiento.id_cliente, id_venta=venta,
            id_beneficio=movimiento.id_beneficio,
            tipo_movimiento='ANULACION', puntos=-movimiento.puntos)
        revertidos += 1

    return revertidos
