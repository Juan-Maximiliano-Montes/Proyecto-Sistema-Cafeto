"""Caja: apertura, movimientos de efectivo, arqueo y cierre, por cajón.

Dos ideas ordenan todo el módulo:

**El libro manda.** MOVIMIENTOS_CAJA es un libro de sólo agregado, y el
efectivo del cajón es la SUMA de sus asientos. No hay una columna "saldo"
que alguien pueda desincronizar.

**El turno es del cajón.** Lo abre quien llega primero, venden todos los
habilitados y lo cierra quien hace el arqueo. Un relevo cierra y abre en
el mismo paso, con lo contado como fondo del turno que sigue.

El arqueo es **ciego**: quien cuenta declara lo que hay sin ver lo que el
sistema esperaba. Si la diferencia supera la tolerancia que fijó la dueña
(PARAMETROS_NEGOCIO), el cierre lo tiene que supervisar alguien con
permiso, que queda escrito.

Todo lo que escribe corre en ``transaction.atomic()`` y bloquea el turno
con ``select_for_update()`` antes de leer el saldo: dos retiros
simultáneos no pueden leer el mismo saldo y sacar plata de más.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from negocio.models import ParametrosNegocio
from ventas.models import (
    CajaRegistradora,
    MovimientoCaja,
    TipoCierre,
    TipoMovimientoCaja,
    TurnoCaja,
)

CERO = Decimal('0.00')


# ===========================================================================
#  CONSULTAS
# ===========================================================================
def saldo(turno):
    """El efectivo que hay en el cajón: la suma del libro."""
    total = (MovimientoCaja.objects.filter(id_turno_caja=turno)
             .aggregate(total=Sum('monto'))['total'])
    return total if total is not None else CERO


def turno_abierto(caja):
    """El turno abierto del cajón, o None."""
    return TurnoCaja.objects.filter(id_caja_registradora=caja,
                                    estado_turno='ABIERTA').first()


def resumen(turno):
    """Lo que entró y salió del cajón, por tipo de movimiento."""
    filas = (MovimientoCaja.objects.filter(id_turno_caja=turno)
             .values('tipo_movimiento').annotate(total=Sum('monto')))
    por_tipo = {f['tipo_movimiento']: f['total'] for f in filas}
    return [(etiqueta, por_tipo.get(valor, CERO))
            for valor, etiqueta in TipoMovimientoCaja.choices]


def recaudacion(turno):
    """Lo cobrado en el turno, por medio de pago: el documento de
    recaudación del cierre (DFD 6).

    Por medio: cuántas ventas lo usaron y cuánto entró con él (importe más
    recargo); y lo que movieron los cambios con ese medio (lo cobrado de
    diferencia menos lo devuelto). Las ventas anuladas no recaudaron. El
    efectivo de esta cuenta es el mismo que el libro de la caja tiene como
    VENTA, COBRO_CAMBIO y DEVOLUCION, menos las ANULACION.
    """
    from ventas.models import Cambio, PagoVenta

    filas = {}

    def fila(nombre):
        return filas.setdefault(nombre, {
            'medio': nombre, 'ventas': 0, 'cobrado': CERO, 'cambios': CERO})

    pagos = (PagoVenta.objects.filter(id_venta__id_turno_caja=turno,
                                     id_venta__estado_venta='COMPLETADA')
             .values('id_medio_pago__nombre_medio_pago')
             .annotate(ventas=Count('id_venta', distinct=True),
                       monto=Sum(F('monto') + F('monto_recargo'))))
    for p in pagos:
        f = fila(p['id_medio_pago__nombre_medio_pago'])
        f['ventas'] = p['ventas']
        f['cobrado'] = p['monto'] or CERO
    cambios = (Cambio.objects.filter(id_turno_caja=turno,
                                     id_medio_pago__isnull=False)
               .values('id_medio_pago__nombre_medio_pago')
               .annotate(cobrado=Sum('importe_cobrado'),
                         devuelto=Sum('importe_devuelto')))
    for c in cambios:
        f = fila(c['id_medio_pago__nombre_medio_pago'])
        f['cambios'] = (c['cobrado'] or CERO) - (c['devuelto'] or CERO)
    for f in filas.values():
        f['total'] = f['cobrado'] + f['cambios']
    return sorted(filas.values(), key=lambda f: f['medio'])


# ===========================================================================
#  EL ASIENTO
# ===========================================================================
@transaction.atomic
def registrar_movimiento(turno, usuario, tipo, monto, concepto, **documento):
    """Escribe un asiento con sus controles.

    ``monto`` va con signo: positivo si entra plata, negativo si sale. Una
    salida nunca deja el cajón en negativo.
    """
    turno = TurnoCaja.objects.select_for_update().get(pk=turno.pk)
    if not turno.abierto:
        raise ValidationError(
            f'El turno {turno.pk} de {turno.id_caja_registradora} está '
            'cerrado: no admite movimientos de dinero.')
    monto = Decimal(monto)
    if monto < 0 and saldo(turno) + monto < 0:
        raise ValidationError(
            f'No hay suficiente efectivo en {turno.id_caja_registradora}: '
            f'hay $ {saldo(turno)} y se quieren sacar $ {-monto}.')
    concepto = (concepto or '').strip()
    if not concepto:
        raise ValidationError('Indicá el concepto del movimiento.')
    return MovimientoCaja.objects.create(
        id_turno_caja=turno, id_usuario=usuario, tipo_movimiento=tipo,
        monto=monto, concepto=concepto[:100], **documento)


# ===========================================================================
#  APERTURA
# ===========================================================================
@transaction.atomic
def abrir(caja, usuario, monto_inicial, concepto='Fondo de cambio inicial'):
    """Abre el turno del cajón y asienta el fondo de cambio."""
    caja = (CajaRegistradora.objects.select_for_update()
            .filter(pk=getattr(caja, 'pk', caja)).first())
    if caja is None or caja.borrado_logico:
        raise ValidationError(
            'La caja registradora no existe o está dada de baja.')
    monto_inicial = Decimal(monto_inicial)
    if monto_inicial < 0:
        raise ValidationError('El fondo inicial no puede ser negativo.')
    if turno_abierto(caja) is not None:
        raise ValidationError(
            f'{caja} ya tiene un turno abierto: se vende en ese turno.')
    try:
        with transaction.atomic():
            turno = TurnoCaja.objects.create(
                id_caja_registradora=caja, id_usuario_apertura=usuario,
                monto_inicial=monto_inicial)
    except IntegrityError:
        # Dos aperturas al mismo tiempo: la segunda choca con el índice.
        raise ValidationError(
            f'{caja} ya tiene un turno abierto: se vende en ese turno.')
    registrar_movimiento(turno, usuario, TipoMovimientoCaja.APERTURA,
                         monto_inicial, concepto)
    return turno


# ===========================================================================
#  EFECTIVO QUE ENTRA O SALE SIN VENTA
# ===========================================================================
def refuerzo(turno, usuario, monto, concepto):
    """Se trae cambio al cajón (monedas, billetes chicos)."""
    monto = Decimal(monto)
    if monto <= 0:
        raise ValidationError('El refuerzo tiene que ser mayor que cero.')
    return registrar_movimiento(turno, usuario, TipoMovimientoCaja.REFUERZO,
                                monto, concepto)


def retiro(turno, usuario, monto, concepto):
    """Se saca efectivo del cajón (para depositar, para la caja fuerte)."""
    monto = Decimal(monto)
    if monto <= 0:
        raise ValidationError('El retiro tiene que ser mayor que cero.')
    return registrar_movimiento(turno, usuario, TipoMovimientoCaja.RETIRO,
                                -monto, concepto)


# ===========================================================================
#  ARQUEO Y CIERRE
# ===========================================================================
@transaction.atomic
def cerrar(turno, usuario, monto_contado, observacion=None,
           supervisor=None, relevo=False):
    """Cierra el turno con lo contado y deja escrita la diferencia.

    * diferencia = contado - esperado (el saldo del libro).
    * Si no da cero, hay que explicar por qué.
    * Si supera la tolerancia, el cierre es SUPERVISADO: ``supervisor`` es
      quien lo autorizó (ya re-autenticado por la vista) y no puede ser
      quien cierra.
    """
    turno = TurnoCaja.objects.select_for_update().get(pk=turno.pk)
    if not turno.abierto:
        raise ValidationError(
            f'El turno {turno.pk} ya está cerrado: un arqueo no se repite.')
    contado = Decimal(monto_contado)
    if contado < 0:
        raise ValidationError('Lo contado no puede ser negativo.')

    esperado = saldo(turno)
    diferencia = contado - esperado
    observacion = (observacion or '').strip()
    tolerancia = ParametrosNegocio.vigentes().tolerancia_arqueo

    if diferencia != 0 and not observacion:
        raise ValidationError(
            f'La caja cierra con una diferencia de $ {diferencia}: contá a '
            'qué se debe.')
    if supervisor is not None and supervisor.pk == usuario.pk:
        raise ValidationError(
            'Quien supervisa el cierre tiene que ser otra persona.')
    if abs(diferencia) > tolerancia and supervisor is None:
        raise ValidationError(
            f'La diferencia de $ {diferencia} supera lo tolerado '
            f'($ {tolerancia}): el cierre lo tiene que supervisar alguien '
            'con permiso.')

    if supervisor is not None:
        tipo = TipoCierre.SUPERVISADO
    elif relevo:
        tipo = TipoCierre.RELEVO
    else:
        tipo = TipoCierre.NORMAL

    turno.monto_final = contado
    turno.monto_esperado = esperado
    turno.diferencia_caja = diferencia
    turno.observacion_diferencia = observacion[:120] or None
    turno.id_usuario_cierre = usuario
    turno.id_usuario_supervisor = supervisor
    turno.tipo_cierre = tipo
    turno.fecha_cierre = timezone.now()
    turno.estado_turno = 'CERRADA'
    turno.save()
    return turno


@transaction.atomic
def relevar(turno, saliente, monto_contado, entrante, observacion=None,
            supervisor=None):
    """Cambio de turno: cierra con lo contado y abre con eso mismo.

    El efectivo no se mueve del cajón: lo que el que se va contó es el
    fondo del que llega. Si hubo diferencia, queda en el turno que cierra.
    """
    cerrado = cerrar(turno, saliente, monto_contado, observacion,
                     supervisor=supervisor, relevo=supervisor is None)
    nuevo = abrir(cerrado.id_caja_registradora, entrante,
                  cerrado.monto_final,
                  concepto=f'Relevo del turno {cerrado.pk}')
    return cerrado, nuevo
