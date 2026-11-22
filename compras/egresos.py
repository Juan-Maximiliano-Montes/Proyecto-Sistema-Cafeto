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
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
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
    if medio_pago.es_cuenta_corriente:
        raise ValidationError('La cuenta corriente es el fiado de los clientes: con eso no se le paga a nadie.')
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


def _concepto(texto):
    """El concepto dice QUÉ se paga ("Luz de octubre"): sin él, el pago no
    se distingue de otro del mismo tipo. Antes faltaba y salía el aviso
    genérico de Django "Este campo no puede ser nulo", sin decir cuál."""
    concepto = _limpiar(texto, 100)
    if concepto is None or len(concepto) < 3:
        raise ValidationError('Escribí el concepto: qué se paga (por ejemplo, "Luz de octubre").')
    return concepto


# Una obligación puede anotarse ya vencida (la boleta de luz que venció la
# semana pasada y todavía no se pagó): es deuda real y tiene que figurar
# como atrasada. Hasta 30 días; más atrás es casi siempre un año mal
# tipeado, y si no lo es se anota con vencimiento de hoy. Para adelante,
# hasta un año: no hay boleta que venza más lejos.
VENCIMIENTO_ATRAS = timedelta(days=30)
VENCIMIENTO_ADELANTE = timedelta(days=365)


def _exigir_vencimiento_razonable(fecha):
    hoy = timezone.localdate()
    if fecha < hoy - VENCIMIENTO_ATRAS:
        raise ValidationError(
            f'¿Vence el {fecha:%d/%m/%Y}? Es hace más de {VENCIMIENTO_ATRAS.days} '
            'días: revisá el año. Si es una deuda vieja sin pagar, anotala con '
            'vencimiento de hoy.')
    if fecha > hoy + VENCIMIENTO_ADELANTE:
        raise ValidationError(
            f'¿Vence el {fecha:%d/%m/%Y}? Es dentro de más de un año: revisá el año.')


@transaction.atomic
def registrar_obligacion(tipo_egreso, concepto, monto, fecha_vencimiento,
                         destinatario=None, proveedor=None, compra=None,
                         comprobante=None):
    """Anota lo que hay que pagar, antes de pagarlo (DD-32)."""
    if fecha_vencimiento is None:
        raise ValidationError('Indicá cuándo vence.')
    _exigir_vencimiento_razonable(fecha_vencimiento)
    egreso = Egreso(
        id_tipo_egreso=tipo_egreso, id_proveedor=proveedor,
        destinatario=_limpiar(destinatario, 60), id_compra=compra,
        concepto=_concepto(concepto), monto=Decimal(monto),
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
        concepto=_concepto(concepto), fecha_egreso=hoy,
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
    if acreditado:
        if fecha > timezone.localdate():
            raise ValidationError(
                'La fecha de acreditación no puede ser un día que todavía no llegó.')
        if egreso.fecha_egreso and fecha < egreso.fecha_egreso:
            raise ValidationError(
                f'El pago se hizo el {egreso.fecha_egreso:%d/%m/%Y}: no pudo '
                'acreditarse antes de esa fecha.')
    egreso.estado_pago = 'ACREDITADO' if acreditado else 'RECHAZADO'
    egreso.fecha_acreditacion = fecha if acreditado else None
    egreso.referencia_bancaria = _limpiar(referencia_bancaria, 40)
    _exigir_sin_duplicado(egreso)
    egreso.save()
    return egreso


def vencimientos_proximos(hasta, desde=None):
    """Lo que vence hasta esa fecha (y desde ``desde``, si se indica) y
    todavía falta pagar (DD-32). Una factura cubierta por transferencias
    en acreditación ya no figura: no hay nada que pagar."""
    obligaciones = Egreso.objects.filter(
        estado_pago='PENDIENTE_PAGO',
        fecha_vencimiento__isnull=False,
        fecha_vencimiento__lte=hasta).order_by('fecha_vencimiento')
    compras = compras_por_pagar().filter(
        fecha_vencimiento_pago__isnull=False,
        fecha_vencimiento_pago__lte=hasta).order_by('fecha_vencimiento_pago')
    if desde is not None:
        obligaciones = obligaciones.filter(fecha_vencimiento__gte=desde)
        compras = compras.filter(fecha_vencimiento_pago__gte=desde)
    return {'obligaciones': obligaciones, 'compras': compras}


# ---------------------------------------------------------------------------
#  Lo que todavía hay que pagar (una consulta, para la pantalla, Inicio y n8n)
# ---------------------------------------------------------------------------
def compras_por_pagar():
    """Las facturas de compra a las que les falta plata.

    Lo que está «en acreditación» (transferido, esperando al banco) ya
    cubre: una factura pagada entera por transferencia no vuelve a ofrecer
    «Pagar» con Falta $ 0,00, que invita a pagarla dos veces. Si el banco la
    rechaza, ese pago deja de contar y la factura vuelve sola."""
    from django.db.models import DecimalField, F, OuterRef, Subquery, Sum, Value
    from django.db.models.functions import Coalesce

    dinero = DecimalField(max_digits=14, decimal_places=2)
    en_acreditacion = (Egreso.objects.filter(id_compra=OuterRef('pk'), estado_pago='PENDIENTE_ACREDITACION')
                       .order_by().values('id_compra').annotate(t=Sum('monto')).values('t'))
    return (Compra.con_pagado(Compra.objects.exclude(estado_compra='PAGADA'))
            .annotate(en_acreditacion=Coalesce(Subquery(en_acreditacion, output_field=dinero),
                                               Value(Decimal('0'), output_field=dinero)),
                      falta=F('total_compra') - Coalesce(F('pagado_anotado'),
                                                        Value(Decimal('0'), output_field=dinero)))
            .filter(falta__gt=0)
            .select_related('id_proveedor')
            .order_by('fecha_vencimiento_pago', 'fecha_compra', 'pk'))


DIAS_SIN_CONFIRMAR = 3


def vencidos(hoy=None):
    """Facturas y obligaciones impagas con la fecha de pago ya pasada:
    cuántas son y cuánto falta pagar."""
    hoy = hoy or timezone.localdate()
    facturas = [c for c in compras_por_pagar().filter(fecha_vencimiento_pago__lt=hoy)]
    otras = list(Egreso.objects.filter(estado_pago='PENDIENTE_PAGO', fecha_vencimiento__lt=hoy))
    return {'cantidad': len(facturas) + len(otras),
            'monto': sum((c.falta for c in facturas), Decimal('0')) + sum((e.monto for e in otras), Decimal('0'))}


def sin_confirmar(hoy=None, dias=DIAS_SIN_CONFIRMAR):
    """Transferencias que el banco no confirmó ni rechazó después de
    ``dias`` días de enviadas."""
    hoy = hoy or timezone.localdate()
    return Egreso.objects.filter(estado_pago='PENDIENTE_ACREDITACION', fecha_egreso__lt=hoy - timedelta(days=dias))
