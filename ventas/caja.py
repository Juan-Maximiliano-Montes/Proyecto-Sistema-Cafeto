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
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from negocio.models import ParametrosNegocio
from ventas.models import (
    CajaRegistradora,
    MedioPago,
    MovimientoCaja,
    TipoCierre,
    TipoMovimientoCaja,
    TurnoCaja,
)
from nucleo.templatetags.cafeto import pesos

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


# En el resumen del cajón se dice de dónde vino cada peso: lo vendido en
# el turno y lo que pagaron de cuentas viejas no se mezclan (DD-94).
ETIQUETAS_DEL_RESUMEN = {
    TipoMovimientoCaja.VENTA: 'Ventas en efectivo del turno',
    TipoMovimientoCaja.COBRO_CUENTA: 'Cobros de cuentas corrientes en efectivo',
}


def resumen(turno):
    """Lo que entró y salió del cajón, por tipo de movimiento."""
    filas = (MovimientoCaja.objects.filter(id_turno_caja=turno)
             .values('tipo_movimiento').annotate(total=Sum('monto')))
    por_tipo = {f['tipo_movimiento']: f['total'] for f in filas}
    return [(ETIQUETAS_DEL_RESUMEN.get(valor, etiqueta), por_tipo.get(valor, CERO))
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
    # Fiado (DD-91): lo que se cobró en este turno de cuentas corrientes
    # entra por su medio; lo vendido "a cuenta" no entró: va en su fila.
    from fidelizacion.models import MovimientoCuenta

    cuentas = (MovimientoCuenta.objects.filter(id_turno_caja=turno, tipo_movimiento__in=('COBRO', 'INTERES'))
               .values('id_medio_pago__nombre_medio_pago').annotate(total=Sum('monto')))
    for c in cuentas:
        f = fila(c['id_medio_pago__nombre_medio_pago'])
        f['cuentas'] = f.get('cuentas', CERO) + (c['total'] or CERO)
    for f in filas.values():
        f['cuentas'] = f.get('cuentas', CERO)
        f['total'] = f['cobrado'] + f['cambios'] + f['cuentas']
        f['fiado'] = f['medio'] == MedioPago.NOMBRE_CUENTA_CORRIENTE
    return sorted(filas.values(), key=lambda f: (f['fiado'], f['medio']))


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
        # Sin decir cuánto hay: con ese número cualquiera averiguaba el
        # saldo pidiendo un retiro absurdo, y el arqueo dejaba de ser ciego.
        raise ValidationError(
            f'No hay suficiente efectivo en {turno.id_caja_registradora} para '
            f'sacar {pesos(-monto)}.')
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
def abrir(caja, usuario, monto_inicial, concepto='Fondo de cambio inicial', anterior=None):
    """Abre el turno del cajón y asienta el fondo de cambio. ``anterior``
    es el turno que se relevó (DD-97)."""
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
                monto_inicial=monto_inicial, id_turno_anterior=anterior)
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
def en_palabras(diferencia):
    """«un faltante de $ 12.000,00» / «un sobrante de $ 500,00»: nunca un
    «-$ 12.000,00» con el signo pegado, que se lee mal y asusta (DD-101)."""
    diferencia = Decimal(diferencia or 0)
    if diferencia == 0:
        return 'la caja justa'
    return f'un {"sobrante" if diferencia > 0 else "faltante"} de {pesos(abs(diferencia))}'


# Un motivo para cerrar sin que nadie apruebe la diferencia tiene que decir
# algo: «.» o «x» no le sirven a la titular cuando lo revise.
MOTIVO_MINIMO = 10


@transaction.atomic
def cerrar(turno, usuario, monto_contado, observacion=None,
           supervisor=None, relevo=False, confirmo_titular=False,
           a_revisar=False, en_ausencia=False):
    """Cierra el turno con lo contado y deja escrita la diferencia.

    * diferencia = contado - esperado (el saldo del libro).
    * Si no da cero, hay que explicar por qué.
    * Si supera la tolerancia, el cierre es SUPERVISADO: ``supervisor`` es
      quien lo autorizó (ya re-autenticado por la vista o a distancia) y no
      puede ser quien cierra.
    * Si no hay nadie que pueda supervisar y la fila no puede esperar
      (``a_revisar``), cierra igual con un motivo que diga algo, y el turno
      queda PENDIENTE DE REVISIÓN de la titular o la encargada (DD-97). La
      clave de otro cajero nunca aprueba una diferencia: sólo firma que
      recibió el cajón.
    * ``en_ausencia``: lo cierra quien llega porque quien abrió se fue sin
      cerrar; queda marcado y tiene que ser otra persona.
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
            f'La caja cierra con {en_palabras(diferencia)}: contá a qué se debe.')
    if supervisor is not None and supervisor.pk == usuario.pk:
        raise ValidationError(
            'Quien supervisa el cierre tiene que ser otra persona.')
    if en_ausencia and turno.id_usuario_apertura_id == usuario.pk:
        raise ValidationError(
            'Ese turno lo abriste vos: cerralo normalmente o hacé el relevo con quien entra.')
    # La titular cierra sola aunque haya diferencia (DD-80): la vista ya le
    # pidió su clave. Queda como cierre NORMAL, sin supervisor, porque en la
    # base "supervisó" es siempre OTRA persona.
    titular_sola = confirmo_titular and getattr(usuario, 'es_titular', False)
    fuera_de_tolerancia = abs(diferencia) > tolerancia and supervisor is None and not titular_sola
    if fuera_de_tolerancia and not a_revisar:
        raise ValidationError(
            f'{en_palabras(diferencia).capitalize()} supera lo tolerado '
            f'({pesos(tolerancia)}): el cierre lo tiene que supervisar alguien '
            'con permiso, pedir la autorización a distancia o dejarlo a revisar.')
    if fuera_de_tolerancia and len(observacion) < MOTIVO_MINIMO:
        raise ValidationError(
            'Para cerrar sin que nadie apruebe la diferencia, contá qué pasó '
            f'(al menos {MOTIVO_MINIMO} letras): la titular lo va a revisar.')

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
    turno.requiere_revision = fuera_de_tolerancia
    turno.relevo_en_ausencia = en_ausencia
    turno.save()
    return turno


@transaction.atomic
def relevar(turno, saliente, monto_contado, entrante, observacion=None,
            supervisor=None, confirmo_titular=False, a_revisar=False,
            en_ausencia=False):
    """Cambio de turno: cierra con lo contado y abre con eso mismo.

    El efectivo no se mueve del cajón: lo que se contó es el fondo del que
    llega, que firma haberlo recibido (custodia, no perdón: su clave no
    aprueba ninguna diferencia). Si hubo diferencia, queda en el turno que
    cierra. ``en_ausencia``: quien llega cuenta y cierra el turno de quien
    se fue sin cerrar (saliente y entrante son la misma persona).
    """
    cerrado = cerrar(turno, saliente, monto_contado, observacion,
                     supervisor=supervisor, relevo=supervisor is None,
                     confirmo_titular=confirmo_titular, a_revisar=a_revisar,
                     en_ausencia=en_ausencia)
    nuevo = abrir(cerrado.id_caja_registradora, entrante,
                  cerrado.monto_final,
                  concepto=f'Relevo del turno {cerrado.pk}', anterior=cerrado)
    return cerrado, nuevo


def puede_revisar(usuario):
    return usuario is not None and (usuario.es_titular or usuario.puede('supervisar_cierre'))


@transaction.atomic
def revisar(turno, usuario):
    """La titular o la encargada miró la diferencia que quedó sin aprobar y
    la da por revisada (con su nombre y la hora)."""
    turno = TurnoCaja.objects.select_for_update().get(pk=turno.pk)
    if not turno.pendiente_de_revision:
        raise ValidationError('Ese turno no tiene una diferencia pendiente de revisión.')
    if not puede_revisar(usuario):
        raise ValidationError('Revisar diferencias de caja lo hace la titular o quien supervisa cierres.')
    if usuario.pk == turno.id_usuario_cierre_id:
        raise ValidationError('La diferencia la revisa otra persona que la que cerró.')
    turno.id_usuario_revision = usuario
    turno.fecha_revision = timezone.now()
    turno.save(update_fields=['id_usuario_revision', 'fecha_revision'])
    return turno


def pendientes_de_revision():
    return (TurnoCaja.objects.filter(requiere_revision=True, id_usuario_revision__isnull=True)
            .select_related('id_caja_registradora', 'id_usuario_cierre').order_by('fecha_cierre'))


# ===========================================================================
#  ARQUEO PARCIAL (CONTROL SIN CERRAR)
# ===========================================================================
OPERACION_CORTE_X = 'ARQUEO PARCIAL DE TURNO DE CAJA'


def cortes_x(turno):
    """Los arqueos parciales del turno (no lo cierran), en orden."""
    from seguridad.models import LogAuditoria
    return (LogAuditoria.objects
            .filter(tabla_afectada=turno._meta.db_table[:40], id_registro=turno.pk,
                    operacion_realizada=OPERACION_CORTE_X)
            .select_related('id_usuario').order_by('fecha_hora', 'pk'))


def foto_del_cajon(turno):
    """Lo que el libro dice del cajón ahora: cada renglón con su cantidad e
    importe, para los comprobantes Z y X."""
    filas = (MovimientoCaja.objects.filter(id_turno_caja=turno)
             .values('tipo_movimiento').annotate(total=Sum('monto'), cantidad=Count('pk')))
    por_tipo = {f['tipo_movimiento']: f for f in filas}

    def de(tipo):
        f = por_tipo.get(tipo) or {}
        return {'monto': f.get('total') or CERO, 'cantidad': f.get('cantidad') or 0}

    return {
        'fondo': de(TipoMovimientoCaja.APERTURA),
        'ventas': de(TipoMovimientoCaja.VENTA),
        'cobros_cuenta': de(TipoMovimientoCaja.COBRO_CUENTA),
        'cobros_cambio': de(TipoMovimientoCaja.COBRO_CAMBIO),
        'refuerzos': de(TipoMovimientoCaja.REFUERZO),
        'gastos': de(TipoMovimientoCaja.GASTO),
        'retiros': de(TipoMovimientoCaja.RETIRO),
        'devoluciones': de(TipoMovimientoCaja.DEVOLUCION),
        'anulaciones': de(TipoMovimientoCaja.ANULACION),
        'esperado': saldo(turno),
    }


def otros_medios(turno):
    """Lo informativo del comprobante: lo que no es efectivo, el fiado, y las
    anulaciones y cambios del turno. No suma al cajón."""
    from ventas.models import Cambio, Venta

    medios = []
    for f in recaudacion(turno):
        if clave_efectivo(f['medio']):
            continue
        medios.append(f)
    anuladas = Venta.objects.filter(id_turno_caja=turno, estado_venta='ANULADA').aggregate(
        cantidad=Count('pk'), total=Sum('total_venta'))
    cambios = Cambio.objects.filter(id_turno_caja=turno).aggregate(
        cantidad=Count('pk'), devuelto=Sum('importe_devuelto'), cobrado=Sum('importe_cobrado'))
    return {
        'medios': medios,
        'anuladas': {'cantidad': anuladas['cantidad'] or 0, 'monto': anuladas['total'] or CERO},
        'cambios': {'cantidad': cambios['cantidad'] or 0, 'devuelto': cambios['devuelto'] or CERO,
                    'cobrado': cambios['cobrado'] or CERO},
    }


def clave_efectivo(nombre):
    from nucleo.validaciones import clave_normalizada
    return clave_normalizada(nombre or '') == clave_normalizada(MedioPago.NOMBRE_PROTEGIDO)


def desglose_del_cajon(foto):
    """Los renglones del efectivo, con su signo, en el orden del comprobante.
    Siempre suman lo esperado: lo que no tiene etiqueta propia (cambios,
    anulaciones) va en su renglón, y sólo si hubo."""
    def renglon(etiqueta, clave, signo, siempre=False):
        dato = foto[clave]
        monto = abs(dato['monto'])
        if not siempre and not dato['cantidad']:
            return None
        return {'etiqueta': etiqueta, 'signo': signo, 'monto': monto, 'cantidad': dato['cantidad']}

    filas = [
        renglon('Fondo inicial', 'fondo', '+', siempre=True),
        renglon('Ventas en efectivo', 'ventas', '+', siempre=True),
        renglon('Cobros de cuentas corrientes', 'cobros_cuenta', '+', siempre=True),
        renglon('Ingresos manuales (refuerzos)', 'refuerzos', '+', siempre=True),
        renglon('Diferencias cobradas en cambios', 'cobros_cambio', '+'),
        renglon('Gastos pagados por caja', 'gastos', '−', siempre=True),
        renglon('Retiros a tesorería', 'retiros', '−', siempre=True),
        renglon('Devoluciones en cambios', 'devoluciones', '−'),
        renglon('Ventas anuladas', 'anulaciones', '−'),
    ]
    return [f for f in filas if f is not None]


def responsables(turno):
    """Quién hizo qué en el turno, para el bloque de responsables del Z."""
    from ventas.models import EstadoSolicitud, SolicitudAutorizacion

    remota = None
    if turno.id_usuario_supervisor_id:
        remota = (SolicitudAutorizacion.objects
                  .filter(id_turno_caja=turno, estado_solicitud=EstadoSolicitud.USADA,
                          pedido__tipo='cierre', id_usuario_autorizante=turno.id_usuario_supervisor_id)
                  .order_by('-pk').first())
    siguiente = turno.siguiente
    return {
        'anterior': turno.id_turno_anterior,
        'siguiente': siguiente,
        'otro_cerro': (turno.id_usuario_cierre_id is not None
                       and turno.id_usuario_cierre_id != turno.id_usuario_apertura_id),
        'remota': remota,
        'titular_sola': (turno.diferencia_caja and not turno.id_usuario_supervisor_id
                         and not turno.requiere_revision and turno.id_usuario_cierre is not None
                         and turno.id_usuario_cierre.es_titular),
    }


def tipo_de_comprobante(turno):
    if turno.abierto:
        return 'ARQUEO PARCIAL'
    if turno.relevo_en_ausencia or turno.tipo_cierre == TipoCierre.RELEVO or turno.siguiente is not None:
        return 'CIERRE POR RELEVO'
    return 'CIERRE DE TURNO'


def foto_serializable(foto):
    return {clave: (str(valor) if not isinstance(valor, dict)
                    else {'monto': str(valor['monto']), 'cantidad': valor['cantidad']})
            for clave, valor in foto.items()}


def foto_desde(datos):
    return {clave: (Decimal(valor) if not isinstance(valor, dict)
                    else {'monto': Decimal(valor['monto']), 'cantidad': valor['cantidad']})
            for clave, valor in datos.items()}


OPERACION_ARQUEO = 'ARQUEO DE TURNO DE CAJA'
OPERACION_DESCARTE = 'ARQUEO DESCARTADO DE TURNO DE CAJA'
OPERACION_REVELADO = 'ARQUEO REVELADO DE TURNO DE CAJA'
# Cuántas veces se puede contar sin que el sistema muestre la diferencia.
CONTEOS_A_CIEGAS = 2


def _asientos_de_arqueo(turno, *operaciones):
    from seguridad.models import LogAuditoria
    return (LogAuditoria.objects
            .filter(tabla_afectada=turno._meta.db_table[:40], id_registro=turno.pk,
                    operacion_realizada__in=operaciones))


def conteos_seguidos(turno):
    """Cuántos conteos se declararon desde el último movimiento del cajón.

    Un conteo viejo, de antes de seguir vendiendo, no cuenta: fue un "cerrar"
    apretado por error en medio del turno."""
    ultimo = turno.movimientos.order_by('-fecha_hora').values_list('fecha_hora', flat=True).first()
    asientos = _asientos_de_arqueo(turno, OPERACION_ARQUEO)
    if ultimo is not None:
        asientos = asientos.filter(fecha_hora__gt=ultimo)
    return asientos.count()


def conteo_revelado(turno):
    """El conteo de este turno al que ya se le mostró la diferencia, o None.

    Desde ese momento el número queda fijo, se cierre la sesión o no: quien
    ya vio cuánto tenía que haber no puede volver a contar (DD-81)."""
    if turno is None or not turno.abierto:
        return None
    asiento = _asientos_de_arqueo(turno, OPERACION_REVELADO).order_by('-fecha_hora', '-pk').first()
    if asiento is None:
        return None
    declarado = (asiento.registro_nuevo or {}).get('arqueo_declarado') or {}
    if declarado.get('monto_contado') is None:
        return None
    relevo = bool(declarado.get('relevo'))
    return {'contado': str(declarado['monto_contado']), 'relevo': relevo,
            'modo': declarado.get('modo') or ('RELEVO' if relevo else 'CIERRE')}


def conteos_declarados(turno):
    """Cada vez que alguien declaró lo contado para cerrar este turno.

    El cierre deja volver atrás (uno se puede equivocar contando), pero
    quien vuelve atrás ya vio cuánto tenía que haber. Por eso cada conteo
    queda asentado ANTES de mostrar la diferencia, en LOG_AUDITORIA (un
    libro que la base no deja modificar), y acá se leen todos para el
    documento de recaudación: si hubo más de uno, la dueña lo ve en el
    papel sin tener que ir a buscarlo (DD-80).

    Cada elemento dice cuándo, quién, cuánto y cómo terminó ese conteo:
    ``CIERRE`` (con ese se cerró), ``DESCARTADO`` (volvió atrás),
    ``ABANDONADO`` (salió del sistema sin cerrar ni volver atrás) o
    ``PENDIENTE`` (el turno sigue abierto y es el último).
    ``otra_sesion`` avisa que entre el conteo anterior y éste la persona
    cerró la sesión y volvió a entrar.
    """
    from seguridad.models import LogAuditoria

    asientos = list(
        LogAuditoria.objects
        .filter(tabla_afectada=turno._meta.db_table[:40], id_registro=turno.pk,
                operacion_realizada__in=(OPERACION_ARQUEO, OPERACION_DESCARTE))
        .select_related('id_usuario').order_by('fecha_hora', 'pk'))
    conteos = []
    for asiento in asientos:
        if asiento.operacion_realizada == OPERACION_DESCARTE:
            if conteos and conteos[-1]['destino'] is None:
                conteos[-1]['destino'] = 'DESCARTADO'
            continue
        declarado = (asiento.registro_nuevo or {}).get('arqueo_declarado') or {}
        try:
            monto = Decimal(str(declarado.get('monto_contado')))
        except (InvalidOperation, TypeError, ValueError):
            monto = None
        if conteos and conteos[-1]['destino'] is None:
            conteos[-1]['destino'] = 'ABANDONADO'
        conteos.append({
            'cuando': asiento.fecha_hora, 'usuario': asiento.id_usuario,
            'monto': monto, 'destino': None, 'sesion': asiento.id_sesion_id,
            'otra_sesion': bool(conteos) and conteos[-1]['sesion'] != asiento.id_sesion_id,
        })
    if conteos and conteos[-1]['destino'] is None:
        conteos[-1]['destino'] = 'PENDIENTE' if turno.abierto else 'CIERRE'
    return conteos


def turnos_con_reconteo(desde):
    """Los turnos cerrados desde ``desde`` en los que se contó más de una vez.

    Es el aviso para la dueña: un reconteo no prueba nada por sí solo, pero
    es lo primero que conviene mirar (DD-81)."""
    from seguridad.models import LogAuditoria

    cerrados = TurnoCaja.objects.filter(estado_turno='CERRADA', fecha_cierre__date__gte=desde)
    repetidos = (LogAuditoria.objects
                 .filter(tabla_afectada=TurnoCaja._meta.db_table[:40],
                         operacion_realizada=OPERACION_ARQUEO,
                         id_registro__in=cerrados.values('pk'))
                 .values('id_registro').annotate(veces=Count('pk')).filter(veces__gt=1)
                 .values_list('id_registro', flat=True))
    return cerrados.filter(pk__in=list(repetidos)).order_by('-fecha_cierre')

