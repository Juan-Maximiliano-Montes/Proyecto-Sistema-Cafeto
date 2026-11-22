"""Fidelización: beneficios, acumulación, canje y reverso de puntos (DFD 9).

El DFD 9 es el escritor único del libro de puntos (DD-37): ventas y
cambios llaman a estas funciones, nunca escriben MOVIMIENTOS_PUNTOS por su
cuenta. La parte que mueve los saldos y recategoriza vive en
``MovimientoPuntos.save()``, porque tiene que correr aunque el asiento se
escriba desde otro lado.

La distinción que ordena todo el módulo: **disponibles** son los puntos que
al cliente le quedan por gastar; **acumulados** son los que ganó en toda su
historia y deciden su categoría. Un canje baja los disponibles pero no toca
los acumulados — gastar los puntos no borra el mérito de haberlos ganado
(DD-11). Por eso la categoría sólo sube.

Qué beneficios tiene un cliente (DD-12, DD-24):

* **Automáticos** de SU categoría: los da el pertenecer a ella, sin puntos.
  Un DESCUENTO automático se aplica solo en cada venta; uno en ESPECIE
  ("el café gratis al socio Gold") se ofrece y lo agrega el operador si el
  cliente lo quiere, una vez por venta.
* **Canjeables** de su categoría o de una inferior (RN-7.2.4): cuestan
  puntos y se descuentan en la misma transacción que confirma la venta
  (DD-38).

Como la categoría se lee al ARMAR el ticket y la acumulación (y con ella el
ascenso) se escribe al CONFIRMARLO, el beneficio de una categoría nueva se
aplica recién en la venta siguiente al ascenso, nunca en la misma
(RN-7.3.1).
"""
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from fidelizacion.models import (
    Beneficio,
    Cliente,
    ModoEntrega,
    MovimientoPuntos,
    TipoBeneficio,
)

CERO = Decimal('0')


# ===========================================================================
#  QUÉ BENEFICIOS TIENE UN CLIENTE
# ===========================================================================
def _vigentes(hoy):
    return (Beneficio.objects
            .filter(borrado_logico=False, id_categoria__borrado_logico=False,
                    vigencia_desde__lte=hoy)
            .filter(Q(vigencia_hasta__isnull=True) | Q(vigencia_hasta__gte=hoy))
            .select_related('id_categoria')
            .order_by('id_categoria__puntos_minimos', 'descripcion_beneficio'))


def beneficios_del_cliente(cliente, hoy=None):
    """Los beneficios que ``cliente`` puede usar hoy.

    Devuelve ``(descuento_automatico, especie_automaticos, canjeables)``:
    el descuento automático de su categoría (o None), los regalos en
    especie de su categoría y los canjeables de su categoría o inferiores.
    """
    hoy = hoy or timezone.localdate()
    categoria = cliente.id_categoria
    beneficios = _vigentes(hoy)
    automaticos = beneficios.filter(tipo_beneficio=TipoBeneficio.AUTOMATICO,
                                    id_categoria=categoria)
    descuento = automaticos.filter(modo_entrega=ModoEntrega.DESCUENTO).first()
    especie = list(automaticos.filter(modo_entrega=ModoEntrega.ESPECIE))
    canjeables = list(beneficios.filter(
        tipo_beneficio=TipoBeneficio.CANJEABLE,
        id_categoria__puntos_minimos__lte=categoria.puntos_minimos))
    return descuento, especie, canjeables


def exigir_aplicable(cliente, beneficio, hoy=None):
    """El beneficio elegido en el mostrador tiene que ser de este cliente,
    hoy. Devuelve el beneficio leído de la base."""
    if cliente is None:
        raise ValidationError(
            'Para aplicar un beneficio del club, primero identificá al '
            'cliente.')
    _, especie, canjeables = beneficios_del_cliente(cliente, hoy)
    for candidato in especie + canjeables:
        if candidato.pk == getattr(beneficio, 'pk', beneficio):
            return candidato
    raise ValidationError(
        'Ese beneficio no está disponible para este cliente: no está '
        'vigente, es de una categoría superior a la suya o se aplica '
        'solo.')


# ===========================================================================
#  ACUMULAR
# ===========================================================================
@dataclass
class Resultado:
    """Lo que el DFD 9 le contesta a la venta o al cambio, para avisarle
    al cliente y dejarlo en la auditoría."""
    puntos: int = 0
    categoria_anterior: object = None
    categoria_nueva: object = None
    disponibles: int = 0

    @property
    def ascendio(self):
        return (self.categoria_nueva is not None
                and self.categoria_anterior is not None
                and self.categoria_nueva.pk != self.categoria_anterior.pk)


def _validar(cliente, venta):
    """El movimiento tiene que ser del cliente de la venta (T10)."""
    if venta is not None:
        if venta.id_cliente_id != getattr(cliente, 'pk', cliente):
            raise ValidationError(
                'El movimiento de puntos no corresponde al cliente de la '
                'venta %s.' % venta.pk)


@transaction.atomic
def acumular(venta):
    """Suma los puntos que la venta generó y recategoriza.

    DD-49: hacen falta ``importe_por_punto`` pesos para ganar un punto, y
    el divisor es el de la categoría del cliente. La división se trunca
    hacia abajo: se premia el punto COMPLETO, no la fracción.
    """
    resultado = Resultado()
    if venta.id_cliente_id is None:
        return resultado
    cliente = Cliente.objects.select_related('id_categoria').get(
        pk=venta.id_cliente_id)
    resultado.categoria_anterior = cliente.id_categoria

    importe_por_punto = cliente.id_categoria.importe_por_punto
    if importe_por_punto:
        puntos = int((venta.total_venta / importe_por_punto).to_integral_value(
            rounding=ROUND_FLOOR))
        if puntos > 0:
            _validar(cliente, venta)
            MovimientoPuntos.objects.create(
                id_cliente=cliente, id_venta=venta,
                tipo_movimiento='ACUMULACION', puntos=puntos)
            resultado.puntos = puntos

    cliente.refresh_from_db()
    resultado.categoria_nueva = cliente.id_categoria
    resultado.disponibles = cliente.puntos_disponibles
    return resultado


# ===========================================================================
#  CANJEAR
# ===========================================================================
@transaction.atomic
def registrar_canje(venta, beneficio):
    """Descuenta los puntos que cuesta el beneficio (DD-38).

    Cuatro controles, todos de T10. El del saldo va último y con
    el cliente bloqueado: si no, dos canjes simultáneos leerían el mismo
    saldo y el cliente gastaría puntos que no tiene.
    """
    cliente = venta.id_cliente
    _validar(cliente, venta)

    if beneficio.tipo_beneficio != 'CANJEABLE' or beneficio.borrado_logico:
        raise ValidationError(
            'El beneficio "%s" no es canjeable.' % beneficio)

    hoy = timezone.localdate()
    if hoy < beneficio.vigencia_desde or (
            beneficio.vigencia_hasta and hoy > beneficio.vigencia_hasta):
        raise ValidationError(
            'El beneficio "%s" no está vigente.' % beneficio)

    bloqueado = Cliente.objects.select_for_update().select_related(
        'id_categoria').get(pk=cliente.pk)
    if beneficio.id_categoria.puntos_minimos > bloqueado.id_categoria.puntos_minimos:
        raise ValidationError(
            'El beneficio "%s" pertenece a una categoría superior a la del '
            'cliente.' % beneficio)
    if bloqueado.puntos_disponibles < beneficio.puntos_requeridos:
        raise ValidationError(
            'Puntos insuficientes: %s tiene %d y "%s" cuesta %d.'
            % (bloqueado.nombre_cliente, bloqueado.puntos_disponibles,
               beneficio, beneficio.puntos_requeridos))

    return MovimientoPuntos.objects.create(
        id_cliente=bloqueado, id_venta=venta, id_beneficio=beneficio,
        tipo_movimiento='CANJE', puntos=-beneficio.puntos_requeridos)


# ===========================================================================
#  REVERSO POR DEVOLUCIÓN (DFD 9 bloque D)
# ===========================================================================
@dataclass
class Reverso:
    acumulacion: int = 0        # puntos ganados que se descuentan
    canjes: int = 0             # puntos de canje que se le devuelven
    disponibles: int = 0

    @property
    def hubo(self):
        return bool(self.acumulacion or self.canjes)


@transaction.atomic
def revertir_por_devolucion(venta):
    """Ajusta los puntos de una venta a lo que el cliente se quedó.

    La venta no se anula: lo que se devuelve es un CAMBIO. Por eso el
    reverso es PROPORCIONAL a la plata reintegrada, y se recalcula entero
    en cada devolución (no se suma por tramos):

        conserva = piso(acumulados_de_la_venta × (total − reintegrado) / total)

    y se asienta sólo la diferencia con lo que ya se había revertido. Así,
    dos devoluciones parciales seguidas llegan al mismo número que una sola
    por el total, y nunca se revierte más de lo que la venta acumuló.

    Un cambio que no devuelve plata (se llevó otra cosa del mismo valor o
    pagó la diferencia) no toca los puntos: la compra sigue en pie.

    Los puntos de un CANJE se devuelven sólo si la venta se devolvió
    entera, una vez por beneficio (``uq_anulacion_canje``): mientras el
    cliente se quede con algo de la compra, el beneficio se usó.

    Si el cliente ya había gastado los puntos que ahora se revierten, sus
    disponibles quedan en negativo y los cubren sus próximas compras: es
    la misma cuenta que si la compra devuelta no hubiera existido.

    Quien llama tiene bloqueada la venta (``ventas.cambios``), así que dos
    devoluciones simultáneas no pueden leer el mismo saldo.
    """
    from ventas.models import Cambio, DetalleCambio

    reverso = Reverso()
    if venta.id_cliente_id is None or venta.total_venta <= 0:
        return reverso
    movimientos = MovimientoPuntos.objects.filter(id_venta=venta)

    acumulados = movimientos.filter(tipo_movimiento='ACUMULACION').aggregate(
        t=Sum('puntos'))['t'] or 0
    ya_revertidos = -(movimientos.filter(
        tipo_movimiento='ANULACION', id_beneficio__isnull=True).aggregate(
        t=Sum('puntos'))['t'] or 0)
    reintegrado = Cambio.objects.filter(id_venta=venta).aggregate(
        t=Sum('importe_devuelto'))['t'] or CERO
    reintegrado = min(reintegrado, venta.total_venta)
    conserva = int((Decimal(acumulados) * (venta.total_venta - reintegrado)
                    / venta.total_venta).to_integral_value(rounding=ROUND_FLOOR))
    a_revertir = acumulados - ya_revertidos - conserva
    if a_revertir > 0:
        MovimientoPuntos.objects.create(
            id_cliente_id=venta.id_cliente_id, id_venta=venta,
            tipo_movimiento='ANULACION', puntos=-a_revertir)
        reverso.acumulacion = a_revertir

    devuelta_entera = all(
        (DetalleCambio.objects.filter(id_detalle_venta=d).aggregate(
            t=Sum('cantidad'))['t'] or CERO) >= d.cantidad
        for d in venta.detalles.all())
    if devuelta_entera:
        for canje in movimientos.filter(tipo_movimiento='CANJE'):
            if movimientos.filter(tipo_movimiento='ANULACION',
                                  id_beneficio=canje.id_beneficio).exists():
                continue
            MovimientoPuntos.objects.create(
                id_cliente_id=venta.id_cliente_id, id_venta=venta,
                id_beneficio=canje.id_beneficio,
                tipo_movimiento='ANULACION', puntos=-canje.puntos)
            reverso.canjes += -canje.puntos

    reverso.disponibles = Cliente.objects.values_list(
        'puntos_disponibles', flat=True).get(pk=venta.id_cliente_id)
    return reverso


# ===========================================================================
#  REVERSO POR ANULACIÓN DE LA VENTA
# ===========================================================================
@transaction.atomic
def revertir_por_anulacion(venta):
    """Deja los puntos como si la venta no hubiera existido.

    A diferencia de la devolución (que es proporcional y puede ir en
    tramos), la anulación revierte TODO de una vez:

    * lo que la venta acumuló y todavía no se revirtió (baja disponibles y
      acumulados; la categoría no baja, DD-11);
    * cada canje, una vez por beneficio (``uq_anulacion_canje``): el
      cliente recupera los puntos que había entregado, porque el
      beneficio no se usó.

    Si el cliente ya gastó en otra compra los puntos que esta le dio, sus
    disponibles quedan en negativo y los cubren sus próximas compras —la
    misma cuenta que si esta venta no hubiera existido—. Con la anulación
    limitada al turno abierto, el caso es raro.

    Quien llama tiene bloqueada la venta (``ventas.anulacion``).
    """
    reverso = Reverso()
    if venta.id_cliente_id is None:
        return reverso
    movimientos = MovimientoPuntos.objects.filter(id_venta=venta)

    acumulados = movimientos.filter(tipo_movimiento='ACUMULACION').aggregate(
        t=Sum('puntos'))['t'] or 0
    ya_revertidos = -(movimientos.filter(
        tipo_movimiento='ANULACION', id_beneficio__isnull=True).aggregate(
        t=Sum('puntos'))['t'] or 0)
    a_revertir = acumulados - ya_revertidos
    if a_revertir > 0:
        MovimientoPuntos.objects.create(
            id_cliente_id=venta.id_cliente_id, id_venta=venta,
            tipo_movimiento='ANULACION', puntos=-a_revertir)
        reverso.acumulacion = a_revertir

    for canje in movimientos.filter(tipo_movimiento='CANJE'):
        if movimientos.filter(tipo_movimiento='ANULACION',
                              id_beneficio=canje.id_beneficio).exists():
            continue
        MovimientoPuntos.objects.create(
            id_cliente_id=venta.id_cliente_id, id_venta=venta,
            id_beneficio=canje.id_beneficio,
            tipo_movimiento='ANULACION', puntos=-canje.puntos)
        reverso.canjes += -canje.puntos

    reverso.disponibles = Cliente.objects.values_list(
        'puntos_disponibles', flat=True).get(pk=venta.id_cliente_id)
    return reverso
