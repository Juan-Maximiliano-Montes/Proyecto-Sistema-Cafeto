"""Egresos: obligaciones, pagos, gastos de caja y conciliación bancaria.

Las reglas de transición de estado (T11) y el "no pagar de más" viven en
``Egreso.save()``, porque son invariantes de la fila; acá están los
procesos que la mueven.

El ciclo de un egreso (DD-06)::

    PENDIENTE_PAGO ─► PENDIENTE_ACREDITACION ─► ACREDITADO
          │                      │
          └──► ACREDITADO        └──► RECHAZADO

Lo que se paga con un medio que entra al cajón (el efectivo) salta
derecho a ACREDITADO y sale del cajón en el mismo paso: la plata ya no
está. Una transferencia queda PENDIENTE_ACREDITACION hasta que el resumen
bancario la confirme; si vuelve RECHAZADA, el reintento es un egreso NUEVO.

El **gasto de caja** es el caso del día a día: se compra hielo o se le
paga al plomero con plata del cajón. Es un egreso pagado en el acto con
efectivo de un turno abierto.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from compras.models import Compra, Egreso
from nucleo.restricciones import ClaveNormalizada
from nucleo.validaciones import clave_normalizada
from ventas import caja as servicio_caja
from ventas.models import TipoMovimientoCaja


def _exigir_sin_duplicado(egreso):
    """El mismo comprobante del mismo proveedor, o la misma transferencia,
    no se registran dos veces. Lo garantiza un índice único; esto es para
    decirlo en castellano antes de chocar contra él."""
    vivos = Egreso.objects.exclude(pk=egreso.pk).exclude(
        estado_pago='RECHAZADO')
    if egreso.comprobante and egreso.id_proveedor_id:
        if vivos.annotate(k=ClaveNormalizada('comprobante')).filter(
                id_proveedor=egreso.id_proveedor_id,
                k=clave_normalizada(egreso.comprobante)).exists():
            raise ValidationError(
                f'El comprobante "{egreso.comprobante}" de este proveedor ya '
                'tiene un egreso registrado.')
    if egreso.referencia_bancaria:
        if vivos.annotate(k=ClaveNormalizada('referencia_bancaria')).filter(
                id_medio_pago=egreso.id_medio_pago_id,
                k=clave_normalizada(egreso.referencia_bancaria)).exists():
            raise ValidationError(
                f'La operación "{egreso.referencia_bancaria}" ya cancela otro '
                'egreso.')


def _validar_medio(medio_pago, turno):
    if medio_pago is None or medio_pago.borrado_logico:
        raise ValidationError('Elegí un medio de pago vigente.')
    if medio_pago.afecta_caja and turno is None:
        raise ValidationError(
            'Un pago en efectivo sale de un cajón: elegí la caja con turno '
            'abierto de la que sale la plata.')


def _sale_del_cajon(egreso, turno, usuario):
    servicio_caja.registrar_movimiento(
        turno, usuario, TipoMovimientoCaja.GASTO, -egreso.monto,
        egreso.concepto, id_egreso=egreso)


def _limpiar(texto, largo):
    return (texto or '').strip()[:largo] or None


@transaction.atomic
def registrar_obligacion(tipo_egreso, concepto, monto, fecha_vencimiento,
                         destinatario=None, proveedor=None, compra=None,
                         comprobante=None):
    """Anota lo que hay que pagar, antes de pagarlo (DD-32)."""
    if fecha_vencimiento is None:
        raise ValidationError('Indicá cuándo vence.')
    egreso = Egreso(
        id_tipo_egreso=tipo_egreso, id_proveedor=proveedor,
        destinatario=_limpiar(destinatario, 60), id_compra=compra,
        concepto=_limpiar(concepto, 100), monto=Decimal(monto),
        fecha_vencimiento=fecha_vencimiento,
        comprobante=_limpiar(comprobante, 50), estado_pago='PENDIENTE_PAGO')
    egreso.full_clean(validate_constraints=False)
    _exigir_sin_duplicado(egreso)
    egreso.save()
    return egreso


@transaction.atomic
def pagar_obligacion(egreso, medio_pago, usuario=None, turno=None,
                     comprobante=None, fecha=None):
    """Paga una obligación ya registrada."""
    egreso = Egreso.objects.select_for_update().get(pk=egreso.pk)
    if egreso.estado_pago != 'PENDIENTE_PAGO':
        raise ValidationError(
            f'El egreso {egreso.pk} no está pendiente de pago (está '
            f'{egreso.get_estado_pago_display().lower()}).')
    _validar_medio(medio_pago, turno)
    hoy = fecha or timezone.localdate()
    egreso.id_medio_pago = medio_pago
    egreso.fecha_egreso = hoy
    egreso.comprobante = _limpiar(comprobante, 50) or egreso.comprobante
    egreso.estado_pago = ('ACREDITADO' if medio_pago.afecta_caja
                          else 'PENDIENTE_ACREDITACION')
    egreso.fecha_acreditacion = hoy if medio_pago.afecta_caja else None
    _exigir_sin_duplicado(egreso)
    egreso.save()
    if medio_pago.afecta_caja:
        _sale_del_cajon(egreso, turno, usuario)
    return egreso


@transaction.atomic
def registrar_egreso(tipo_egreso, medio_pago, monto, concepto,
                     comprobante=None, proveedor=None, destinatario=None,
                     compra=None, turno=None, usuario=None, fecha=None):
    """Egreso pagado en el acto: la compra contra entrega, el gasto de caja."""
    _validar_medio(medio_pago, turno)
    hoy = fecha or timezone.localdate()
    egreso = Egreso(
        id_tipo_egreso=tipo_egreso, id_proveedor=proveedor,
        destinatario=_limpiar(destinatario, 60), id_compra=compra,
        id_medio_pago=medio_pago, monto=Decimal(monto),
        concepto=_limpiar(concepto, 100), fecha_egreso=hoy,
        comprobante=_limpiar(comprobante, 50),
        estado_pago=('ACREDITADO' if medio_pago.afecta_caja
                     else 'PENDIENTE_ACREDITACION'),
        fecha_acreditacion=hoy if medio_pago.afecta_caja else None)
    egreso.full_clean(validate_constraints=False)
    _exigir_sin_duplicado(egreso)
    egreso.save()
    if medio_pago.afecta_caja:
        _sale_del_cajon(egreso, turno, usuario)
    return egreso


def gasto_de_caja(turno, usuario, tipo_egreso, monto, concepto,
                  medio_efectivo, destinatario=None, proveedor=None,
                  comprobante=None):
    """Plata que sale del cajón para un gasto del local."""
    if not medio_efectivo.afecta_caja:
        raise ValidationError('Un gasto de caja se paga con efectivo.')
    return registrar_egreso(
        tipo_egreso, medio_efectivo, monto, concepto, comprobante=comprobante,
        proveedor=proveedor, destinatario=destinatario, turno=turno,
        usuario=usuario)


@transaction.atomic
def conciliar(egreso, acreditado, fecha, referencia_bancaria=None):
    """Cierra el pago contra el resumen bancario (DD-06)."""
    egreso = Egreso.objects.select_for_update().get(pk=egreso.pk)
    if egreso.estado_pago != 'PENDIENTE_ACREDITACION':
        raise ValidationError(
            f'El egreso {egreso.pk} no está pendiente de acreditación.')
    egreso.estado_pago = 'ACREDITADO' if acreditado else 'RECHAZADO'
    egreso.fecha_acreditacion = fecha if acreditado else None
    egreso.referencia_bancaria = _limpiar(referencia_bancaria, 40)
    _exigir_sin_duplicado(egreso)
    egreso.save()
    return egreso


def vencimientos_proximos(hasta):
    """Lo que vence hasta esa fecha y todavía no se pagó (DD-32)."""
    obligaciones = Egreso.objects.filter(
        estado_pago='PENDIENTE_PAGO',
        fecha_vencimiento__isnull=False,
        fecha_vencimiento__lte=hasta).order_by('fecha_vencimiento')
    compras = Compra.objects.filter(
        ~Q(estado_compra='PAGADA'),
        fecha_vencimiento_pago__isnull=False,
        fecha_vencimiento_pago__lte=hasta).order_by('fecha_vencimiento_pago')
    return {'obligaciones': obligaciones, 'compras': compras}
