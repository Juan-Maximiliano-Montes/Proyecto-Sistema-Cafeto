"""Egresos: obligaciones, pagos y conciliación bancaria.

Las reglas de transición de estado (T11) viven en ``Egreso.save()``,
porque son invariantes de la fila; acá están los procesos que la mueven.

El ciclo de un egreso (DD-06)::

    PENDIENTE_PAGO ─► PENDIENTE_ACREDITACION ─► ACREDITADO
          │                      │
          └──► ACREDITADO        └──► RECHAZADO

El efectivo salta derecho a ACREDITADO: la plata ya salió del cajón, no hay
nada que esperar. Una transferencia queda PENDIENTE_ACREDITACION hasta que
el resumen bancario la confirme — y si vuelve RECHAZADA, el reintento es un
egreso NUEVO, no una vuelta atrás del anterior.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import caja as servicio_caja
from .models import Compra, Egreso


def _efectivo_sale_del_cajon(egreso, turno, usuario):
    """Asienta la salida de plata.

    El control de saldo suficiente y de turno abierto lo hace
    ``caja.registrar_movimiento``: es el único lugar donde se toca el libro
    de caja, así que es el único que tiene que saber esas reglas.
    """
    if turno is None:
        raise ValidationError(
            'Un egreso en efectivo exige un turno de caja abierto (DD-05).')
    servicio_caja.registrar_movimiento(
        turno, usuario, 'EGRESO', -egreso.monto,
        (egreso.concepto or '')[:100], egreso=egreso)


@transaction.atomic
def registrar_obligacion(tipo_egreso, concepto, monto, fecha_vencimiento,
                         destinatario=None, proveedor=None):
    """Anota lo que hay que pagar, antes de pagarlo.

    Separar la obligación del pago es lo que permite avisar de un
    vencimiento antes de que ocurra (DD-32).
    """
    return Egreso.objects.create(
        id_tipo_egreso=tipo_egreso, id_proveedor=proveedor,
        destinatario=destinatario, concepto=concepto, monto=monto,
        fecha_vencimiento=fecha_vencimiento, estado_pago='PENDIENTE_PAGO')


@transaction.atomic
def pagar_obligacion(egreso, medio_pago, comprobante, turno=None,
                     usuario=None):
    """Paga una obligación ya registrada."""
    egreso = Egreso.objects.select_for_update().get(pk=egreso.pk)
    if egreso.estado_pago != 'PENDIENTE_PAGO':
        raise ValidationError(
            'El egreso %s no es una obligación pendiente de pago (está %s).'
            % (egreso.pk, egreso.estado_pago))

    efectivo = servicio_caja.es_efectivo(medio_pago)
    hoy = timezone.localdate()

    egreso.id_medio_pago = medio_pago
    egreso.fecha_egreso = hoy
    egreso.comprobante = comprobante
    egreso.estado_pago = 'ACREDITADO' if efectivo else 'PENDIENTE_ACREDITACION'
    egreso.fecha_acreditacion = hoy if efectivo else None
    egreso.save()

    if efectivo:
        _efectivo_sale_del_cajon(egreso, turno, usuario)
    return egreso


@transaction.atomic
def registrar_egreso(tipo_egreso, medio_pago, monto, concepto, comprobante,
                     proveedor=None, destinatario=None, compra=None,
                     turno=None, usuario=None):
    """Egreso pagado en el acto.

    Es el caso de la compra que se paga contra entrega: no hay una
    obligación previa que anotar.
    """
    efectivo = servicio_caja.es_efectivo(medio_pago)
    hoy = timezone.localdate()

    egreso = Egreso.objects.create(
        id_tipo_egreso=tipo_egreso, id_proveedor=proveedor,
        destinatario=destinatario, id_compra=compra,
        id_medio_pago=medio_pago, monto=monto, concepto=concepto,
        fecha_egreso=hoy, comprobante=comprobante,
        estado_pago='ACREDITADO' if efectivo else 'PENDIENTE_ACREDITACION',
        fecha_acreditacion=hoy if efectivo else None)

    if efectivo:
        _efectivo_sale_del_cajon(egreso, turno, usuario)
    return egreso


@transaction.atomic
def conciliar(egreso, acreditado, fecha, referencia_bancaria):
    """Cierra el pago contra el resumen bancario (DD-06)."""
    egreso = Egreso.objects.select_for_update().get(pk=egreso.pk)
    if egreso.estado_pago != 'PENDIENTE_ACREDITACION':
        raise ValidationError(
            'El egreso %s no está pendiente de acreditación (está %s).'
            % (egreso.pk, egreso.estado_pago))

    egreso.estado_pago = 'ACREDITADO' if acreditado else 'RECHAZADO'
    egreso.fecha_acreditacion = fecha if acreditado else None
    egreso.referencia_bancaria = referencia_bancaria
    egreso.save()
    return egreso


def vencimientos_proximos(hasta):
    """Lo que vence hasta esa fecha y todavía no se pagó (DD-32).

    Dos fuentes: las obligaciones
    sueltas —servicios, impuestos— y las compras a crédito que siguen
    impagas.
    """
    obligaciones = Egreso.objects.filter(
        estado_pago='PENDIENTE_PAGO',
        fecha_vencimiento__isnull=False,
        fecha_vencimiento__lte=hasta).order_by('fecha_vencimiento')

    compras = Compra.objects.filter(
        ~Q(estado_compra='PAGADA'),
        fecha_vencimiento_pago__isnull=False,
        fecha_vencimiento_pago__lte=hasta).order_by('fecha_vencimiento_pago')

    return {'obligaciones': obligaciones, 'compras': compras}
