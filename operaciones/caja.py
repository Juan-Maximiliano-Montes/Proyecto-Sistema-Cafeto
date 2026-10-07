"""Caja: apertura, movimientos de dinero, arqueo y cierre.

Los tres procesos del turno —abrir, declarar el arqueo y cerrar— más los
controles del asiento de caja (T06) y del turno (T07), todo en Python.

Dos ideas ordenan todo el módulo:

**El libro manda.** ``MOVIMIENTOS_CAJA`` es un libro de sólo agregado, y el
efectivo que hay en el cajón es la SUMA de sus asientos. No hay una columna
"saldo" que alguien pueda desincronizar: si el saldo y el libro no
coinciden, es porque el saldo no existe como dato aparte.

**El turno es inmutable una vez cerrado.** El arqueo es ciego —se declara lo
contado sin ver lo esperado— y esa declaración no se puede repetir. Si se
pudiera, dejaría de ser un control.

Todo lo que escribe corre dentro de ``transaction.atomic()`` y toma los
bloqueos con ``select_for_update()``: sin eso, dos cajeros que cobran al
mismo tiempo pueden leer el mismo saldo y sacar plata de más.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from maestros.models import CajaRegistradora, MedioPago

from .models import MovimientoCaja, Sesion, TurnoCaja


CERO = Decimal('0.00')


# ===========================================================================
#  CONSULTAS
# ===========================================================================
def es_efectivo(medio_pago):
    """True si el medio de pago mueve dinero del cajón.

    El efectivo es el único medio cuyo cobro entra físicamente a la caja;
    una tarjeta acredita en la cuenta y no afecta el arqueo.
    """
    if medio_pago is None:
        return False
    nombre = getattr(medio_pago, 'nombre_medio_pago', medio_pago)
    return str(nombre).strip().upper().startswith('EFECTIVO')


def saldo(turno):
    """El efectivo que hay en el cajón.

    Es la suma del libro, no una columna. Un asiento de salida se guarda
    con monto negativo, así que sumar alcanza.
    """
    total = (MovimientoCaja.objects
             .filter(id_turno_caja=turno)
             .aggregate(total=Sum('monto'))['total'])
    return total if total is not None else CERO


def turno_abierto_de(usuario):
    return TurnoCaja.objects.filter(
        id_usuario_apertura=usuario, estado_turno='ABIERTA').first()


# ===========================================================================
#  T06 · EL ASIENTO DEL LIBRO DE CAJA
# ===========================================================================
@transaction.atomic
def registrar_movimiento(turno, usuario, tipo, monto, concepto,
                         venta=None, egreso=None):
    """Escribe un asiento, con los tres controles del libro de caja (T06).

    ``monto`` va con signo: positivo si entra plata, negativo si sale.

    Se bloquea el turno con ``select_for_update()`` antes de leer el saldo.
    Sin ese bloqueo, dos egresos simultáneos leerían el mismo saldo y los
    dos se creerían autorizados: el cajón terminaría en negativo.
    """
    turno = TurnoCaja.objects.select_for_update().get(pk=turno.pk)

    if turno.estado_turno != 'ABIERTA':
        raise ValidationError(
            'El turno de caja %s no está abierto: no admite movimientos de '
            'dinero (RN-2.2.1).' % turno.pk)

    if tipo in ('EGRESO', 'ANULACION') and saldo(turno) + monto < 0:
        raise ValidationError(
            'Saldo insuficiente en la caja: hay $ %s y se intenta retirar '
            '$ %s (DD-05).' % (saldo(turno), -monto))

    if tipo == 'APERTURA' and MovimientoCaja.objects.filter(
            id_turno_caja=turno, tipo_movimiento='APERTURA').exists():
        raise ValidationError(
            'El turno de caja %s ya tiene su movimiento de apertura (DD-02).'
            % turno.pk)

    return MovimientoCaja.objects.create(
        id_turno_caja=turno, id_usuario=usuario, id_venta=venta,
        id_egreso=egreso, tipo_movimiento=tipo, monto=monto,
        concepto=concepto)


# ===========================================================================
#  APERTURA
# ===========================================================================
@transaction.atomic
def abrir(usuario, caja_registradora, monto_inicial):
    """Abre el turno y asienta el fondo de cambio.

    Tres reglas, y las tres existen por el mismo motivo: que el arqueo del
    final del día tenga con qué compararse.

    * un usuario no abre dos turnos a la vez (RN-2.1.1);
    * la caja registradora tiene que estar vigente (DD-42);
    * una caja registradora no tiene dos turnos abiertos (DD-42).
    """
    if turno_abierto_de(usuario) is not None:
        raise ValidationError(
            'El usuario %s ya tiene un turno de caja abierto (RN-2.1.1).'
            % usuario)

    caja = (CajaRegistradora.objects.select_for_update()
            .filter(pk=getattr(caja_registradora, 'pk', caja_registradora))
            .first())
    if caja is None or caja.borrado_logico:
        raise ValidationError(
            'La caja registradora no existe o está dada de baja (DD-42).')

    if TurnoCaja.objects.filter(id_caja_registradora=caja,
                                estado_turno='ABIERTA').exists():
        raise ValidationError(
            'La caja registradora "%s" ya tiene un turno abierto (DD-42).'
            % caja)

    turno = TurnoCaja.objects.create(
        id_caja_registradora=caja, id_usuario_apertura=usuario,
        monto_inicial=monto_inicial)

    # DD-02: la apertura es el primer asiento del libro.
    registrar_movimiento(turno, usuario, 'APERTURA', monto_inicial,
                         'Fondo de cambio inicial')

    # DD-01 / DD-35: la sesión queda enmarcada en el turno.
    Sesion.objects.filter(id_usuario=usuario, activa=True).update(
        id_turno_caja=turno)

    return turno


# ===========================================================================
#  T07 · ARQUEO Y CIERRE
# ===========================================================================
def _exigir_turno_modificable(turno):
    """Un turno cerrado no se toca, ni se reabre (RN-2.3.2)."""
    if turno.estado_turno == 'CERRADA':
        raise ValidationError(
            'El turno de caja %s está cerrado: no admite modificaciones ni '
            'reapertura (RN-2.3.2).' % turno.pk)


@transaction.atomic
def declarar_arqueo(turno, monto_contado):
    """Registra el efectivo contado, UNA sola vez.

    Es un arqueo **ciego**: quien cuenta no ve lo que el sistema esperaba.
    Por eso la declaración es irreversible — si se pudiera corregir después
    de ver la diferencia, el control no controlaría nada (DD-07).
    """
    turno = TurnoCaja.objects.select_for_update().get(pk=turno.pk)
    _exigir_turno_modificable(turno)

    if turno.monto_final is not None:
        raise ValidationError(
            'La declaración del efectivo contado es irreversible: este turno '
            'ya declaró $ %s (DD-07).' % turno.monto_final)

    turno.monto_final = monto_contado
    turno.save(update_fields=['monto_final'])
    return turno


@transaction.atomic
def cerrar(turno, usuario, observacion=None):
    """Cierra el turno y deja escrita la diferencia.

    La diferencia es *contado − esperado*. Si no da cero hay que explicar
    por qué: un faltante sin justificación es exactamente lo que el arqueo
    viene a detectar (``ck_turnos_caja_justificacion``).
    """
    turno = TurnoCaja.objects.select_for_update().get(pk=turno.pk)
    _exigir_turno_modificable(turno)

    if turno.monto_final is None:
        raise ValidationError(
            'Antes de cerrar hay que declarar el efectivo contado (DD-07).')

    diferencia = turno.monto_final - saldo(turno)
    if diferencia != 0 and not observacion:
        raise ValidationError(
            'La caja cierra con una diferencia de $ %s: hace falta explicar '
            'a qué se debe.' % diferencia)

    turno.diferencia_caja = diferencia
    turno.observacion_diferencia = observacion or None
    turno.id_usuario_cierre = usuario
    turno.fecha_cierre = timezone.now()
    turno.estado_turno = 'CERRADA'
    turno.save(update_fields=[
        'diferencia_caja', 'observacion_diferencia', 'id_usuario_cierre',
        'fecha_cierre', 'estado_turno'])
    return turno
