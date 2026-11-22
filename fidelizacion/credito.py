"""La cuenta corriente de los clientes: el fiado (DD-91).

Idea central: el sistema propone y avisa, pero la que decide es la dueña.
Los frenos (cliente sin habilitar, suspendido, en mora, sobre el tope) se
levantan con la autorización de quien puede gestionar el fiado; lo único
que no se levanta es fiarle a un anónimo o a alguien dado por incobrable.

Todo lo que mueve plata o deuda pasa por acá, en una transacción y con el
cliente bloqueado: dos cajas cobrándole a la misma persona se esperan.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone

from fidelizacion.models import Cliente, EstadoCredito, MovimientoCuenta, MovimientoPuntos
from nucleo.templatetags.cafeto import pesos
from nucleo.textos import pluralizar

CERO = Decimal('0')
CENTAVO = Decimal('0.01')
DIAS_POR_DEFECTO = 30
PERMISO_GESTION = 'gestionar_credito'
FIADO, COBRO, INTERES, CANCELACION, INCOBRABLE = 'FIADO', 'COBRO', 'INTERES', 'CANCELACION', 'INCOBRABLE'


def _fiados(cliente):
    return MovimientoCuenta.objects.filter(id_cliente=cliente, tipo_movimiento=FIADO)


def pendientes(cliente):
    """Los fiados que todavía deben algo, del más viejo al más nuevo."""
    return _fiados(cliente).filter(saldo_pendiente__gt=0).order_by('fecha_vencimiento', 'id_movimiento_cuenta')


def saldo(cliente):
    return _fiados(cliente).aggregate(t=Sum('saldo_pendiente'))['t'] or CERO


def saldo_antes_de(id_cliente, id_movimiento):
    """Lo que debía el cliente justo antes de ese movimiento de su cuenta.

    El libro es de sólo agregado y cada movimiento del mismo cliente se
    escribe con el cliente bloqueado: lo que había antes es lo fiado menos
    lo que se achicó (cobros, cancelaciones, incobrables) en los movimientos
    anteriores. Sirve para reimprimir un ticket o un recibo y que diga lo
    mismo que el día que salió."""
    previos = MovimientoCuenta.objects.filter(id_cliente_id=id_cliente, pk__lt=id_movimiento)
    totales = previos.aggregate(
        fiado=Sum('monto', filter=Q(tipo_movimiento=FIADO)),
        bajo=Sum('monto', filter=Q(tipo_movimiento__in=(COBRO, CANCELACION, INCOBRABLE))))
    return (totales['fiado'] or CERO) - (totales['bajo'] or CERO)


# Lo que se cobró en una misma operación sale junto en el recibo. El libro no
# tiene N° de recibo: el recibo es el primer COBRO y los que lo siguen en la
# cuenta de ese cliente, de la misma persona y medio, en el mismo instante.
SEGUNDOS_DE_UN_COBRO = 5


def mismo_cobro(anterior, siguiente):
    """¿Estos dos movimientos (en orden) salieron de la misma operación de cobro?"""
    from datetime import timedelta
    return (anterior is not None and anterior.tipo_movimiento == COBRO
            and siguiente.tipo_movimiento in (COBRO, INTERES)
            and anterior.id_usuario_id == siguiente.id_usuario_id
            and anterior.id_medio_pago_id == siguiente.id_medio_pago_id
            and abs(siguiente.fecha_movimiento - anterior.fecha_movimiento)
            <= timedelta(seconds=SEGUNDOS_DE_UN_COBRO))


def ancla(cobro):
    """El primer COBRO de la operación a la que pertenece ``cobro``."""
    while True:
        previo = (MovimientoCuenta.objects.filter(id_cliente_id=cobro.id_cliente_id, pk__lt=cobro.pk)
                  .order_by('-pk').first())
        if not mismo_cobro(previo, cobro):
            return cobro
        cobro = previo


def recibo(cobro):
    """Los renglones de un cobro (``cobro`` es su primer COBRO) y sus totales."""
    siguientes = (MovimientoCuenta.objects
                  .filter(id_cliente_id=cobro.id_cliente_id, pk__gte=cobro.pk)
                  .select_related('id_fiado__id_venta').order_by('pk')[:200])
    renglones, interes = [], []
    limite = cobro.fecha_movimiento + timedelta(seconds=SEGUNDOS_DE_UN_COBRO)
    for m in siguientes:
        if (m.tipo_movimiento not in (COBRO, INTERES) or m.id_usuario_id != cobro.id_usuario_id
                or m.id_medio_pago_id != cobro.id_medio_pago_id or m.fecha_movimiento > limite):
            break
        if m.tipo_movimiento == COBRO:
            if interes:
                break       # un COBRO después del interés ya es otro cobro
            renglones.append(m)
        else:
            interes.append(m)
            break           # el interés es lo último que escribe un cobro
    pagado = sum((r.monto for r in renglones), CERO)
    recargo = sum((i.monto for i in interes), CERO)
    anterior = saldo_antes_de(cobro.id_cliente_id, cobro.pk)
    ventas = [r.id_fiado.id_venta_id for r in renglones if r.id_fiado_id]
    puntos = (MovimientoPuntos.objects
              .filter(id_cliente_id=cobro.id_cliente_id, id_venta_id__in=ventas,
                      fecha_movimiento__gte=cobro.fecha_movimiento - timedelta(seconds=1),
                      fecha_movimiento__lte=limite)
              .aggregate(t=Sum('puntos'))['t'] or 0) if ventas else 0
    return {'renglones': renglones, 'interes': interes[0] if interes else None,
            'pagado': pagado, 'recargo': recargo, 'total': pagado + recargo,
            'anterior': anterior, 'nuevo': anterior - pagado, 'puntos': puntos}


def situacion(cliente, hoy=None):
    """Lo que hace falta saber de un cliente para decidir, en una consulta."""
    hoy = hoy or timezone.localdate()
    cuenta = _fiados(cliente).filter(saldo_pendiente__gt=0).aggregate(
        saldo=Sum('saldo_pendiente'),
        vencido=Sum('saldo_pendiente', filter=Q(fecha_vencimiento__lt=hoy)),
        desde=Min('fecha_vencimiento', filter=Q(fecha_vencimiento__lt=hoy)),
        proximo=Min('fecha_vencimiento'))
    vencido = cuenta['vencido'] or CERO
    perdido = (MovimientoCuenta.objects.filter(id_cliente=cliente, tipo_movimiento=INCOBRABLE)
               .aggregate(t=Sum('monto'))['t'] or CERO)
    return {
        'estado': cliente.estado_credito, 'saldo': cuenta['saldo'] or CERO, 'vencido': vencido,
        'dias_de_atraso': (hoy - cuenta['desde']).days if cuenta['desde'] else 0,
        'proximo_vencimiento': cuenta['proximo'],
        'limite': cliente.limite_credito, 'nota': cliente.nota_credito or '',
        'plazo': cliente.plazo_credito_dias,
        'en_mora': vencido > 0 or cliente.estado_credito == EstadoCredito.SANCIONADO,
        'perdido': perdido,
    }


def puede_gestionar(usuario):
    return usuario is not None and (getattr(usuario, 'es_titular', False) or usuario.tiene_permiso(PERMISO_GESTION))


def _quien_gestiona(operador, autorizante):
    """El que firma una excepción: el operador si puede, o quien autorizó."""
    for persona in (operador, autorizante):
        if puede_gestionar(persona):
            return persona
    return None


def nombre(cliente):
    """Como se lo nombra en la caja: «Elsa Gómez»."""
    return f'{cliente.nombre_cliente} {cliente.apellido_cliente}'.strip()


def dias(cantidad):
    return f'{cantidad} día' if cantidad == 1 else f'{cantidad} días'


def aviso_de_mora(cliente, hoy=None, s=None):
    """La frase para la alerta roja del mostrador, o '' si está al día."""
    s = s or situacion(cliente, hoy)
    if cliente.estado_credito == EstadoCredito.INCOBRABLE:
        return f'{nombre(cliente)} quedó como incobrable: no se le fía.'
    if s['vencido'] > 0:
        return (f'{nombre(cliente)} debe {pesos(s["vencido"])} vencidos hace {dias(s["dias_de_atraso"])}'
                + (f' (en total, {pesos(s["saldo"])})' if s['saldo'] > s['vencido'] else '') + '.')
    if cliente.estado_credito == EstadoCredito.SANCIONADO:
        return f'{nombre(cliente)} tiene la cuenta corriente suspendida' + (
            f' y debe {pesos(s["saldo"])}.' if s['saldo'] else '.')
    return ''


def exigir_que_pueda_canjear(cliente, operador, autorizante):
    """Con deuda vencida o suspendido no se canjean puntos ni beneficios,
    salvo que lo autorice quien gestiona el fiado."""
    if situacion(cliente)['en_mora'] and _quien_gestiona(operador, autorizante) is None:
        raise ValidationError(
            aviso_de_mora(cliente) + ' No puede usar puntos ni beneficios hasta ponerse al día. '
            'Si la dueña lo decide, que lo autorice con su usuario y su clave.')


SIN_CLIENTE = ('Para fiar hay que saber a quién: identificá al cliente (o dalo de alta) antes de '
               'cobrar con Cuenta corriente.')
PEDIR_FIRMA = (' Si la dueña está de acuerdo, que lo autorice ella (o quien maneje el fiado) '
               'con su usuario y su clave, o pedile la autorización a distancia.')


def revisar_fiado(cliente, monto, operador, autorizante=None, hoy=None):
    """¿Se le puede fiar ``monto`` a ``cliente``? Levanta ``ValidationError``
    con lo que hay que hacer si no. Devuelve quién firma la excepción (o
    None si no hizo falta ninguna). No escribe nada: la usa la cotización
    del mostrador para avisar ANTES de cobrar, y la venta para decidir."""
    if cliente is None:
        raise ValidationError(SIN_CLIENTE)
    hoy = hoy or timezone.localdate()
    firma = _quien_gestiona(operador, autorizante)
    pedir = PEDIR_FIRMA
    s = situacion(cliente, hoy)
    if cliente.estado_credito == EstadoCredito.INCOBRABLE:
        raise ValidationError(f'{cliente} quedó como incobrable: no se le puede fiar. '
                              'Si corresponde, la dueña lo rehabilita desde Cuentas corrientes.')
    if cliente.borrado_logico:
        raise ValidationError(f'{cliente} está dado de baja: no se le puede fiar.')
    if s['en_mora'] and firma is None:
        raise ValidationError(aviso_de_mora(cliente, hoy) + ' No se le fía hasta que se ponga al día.' + pedir)
    if cliente.estado_credito == EstadoCredito.SIN_CREDITO and firma is None:
        raise ValidationError(f'{cliente} todavía no tiene cuenta corriente: para fiarle por primera vez '
                              'hace falta la autorización de quien maneja el fiado.' + pedir)
    if cliente.limite_credito and s['saldo'] + monto > cliente.limite_credito and firma is None:
        raise ValidationError(
            f'Con esta compra {cliente} pasaría a deber {pesos(s["saldo"] + monto)} y su tope es '
            f'{pesos(cliente.limite_credito)}.' + pedir)
    necesita = (cliente.estado_credito == EstadoCredito.SIN_CREDITO or s['en_mora']
                or bool(cliente.limite_credito and s['saldo'] + monto > cliente.limite_credito))
    return firma if necesita else None


def registrar_fiado(venta, monto, operador, autorizante=None, vence=None):
    """Anota la parte de ``venta`` que queda a pagar. Se llama DENTRO de la
    transacción de la venta: si esto falla, la venta no existe.

    Con el cliente bloqueado se vuelve a revisar todo (entre la cotización
    y el cobro pudo fiarle otra caja). Si estaba sin cuenta corriente y
    alguien con permiso lo autorizó, queda habilitado en el mismo paso.
    """
    if venta.id_cliente_id is None:
        raise ValidationError(SIN_CLIENTE)
    cliente = Cliente.objects.select_for_update().get(pk=venta.id_cliente_id)
    hoy = timezone.localdate()
    vence = vence or hoy + timedelta(days=cliente.plazo_credito_dias or DIAS_POR_DEFECTO)
    if vence < hoy:
        raise ValidationError('La fecha en que va a pagar no puede ser anterior a hoy.')
    if vence > hoy + timedelta(days=366):
        raise ValidationError('La fecha en que va a pagar no puede pasar de un año.')
    venta.credito_firmado_por = revisar_fiado(cliente, monto, operador, autorizante, hoy)
    venta.credito_habilitado = cliente.estado_credito == EstadoCredito.SIN_CREDITO
    if venta.credito_habilitado:
        Cliente.objects.filter(pk=cliente.pk).update(estado_credito=EstadoCredito.HABILITADO)
    return MovimientoCuenta.objects.create(
        id_cliente=cliente, tipo_movimiento=FIADO, monto=monto, id_venta=venta, id_usuario=operador,
        fecha_vencimiento=vence, saldo_pendiente=monto)


def _achicar(fiado, monto, tipo, usuario, motivo=None, medio=None, turno=None):
    fiado = MovimientoCuenta.objects.select_for_update().get(pk=fiado.pk)
    monto = min(monto, fiado.saldo_pendiente)
    if monto <= 0:
        return CERO
    MovimientoCuenta.objects.create(
        id_cliente_id=fiado.id_cliente_id, tipo_movimiento=tipo, monto=monto, id_fiado=fiado,
        id_venta_id=fiado.id_venta_id, id_usuario=usuario, motivo=motivo, id_medio_pago=medio,
        id_turno_caja=turno)
    MovimientoCuenta.objects.filter(pk=fiado.pk).update(saldo_pendiente=fiado.saldo_pendiente - monto)
    return monto


def bloquear_cliente_de(venta):
    """Bloquea al cliente de una venta fiada (o nada, si no fue fiada).

    Quien va a decidir algo según lo que se debe de esa venta (anularla,
    devolver parte) lo llama ANTES de mirar la deuda: así no se cruza con
    un cobro que otra caja le está haciendo al mismo cliente.
    """
    cliente = (MovimientoCuenta.objects.filter(id_venta=venta, tipo_movimiento=FIADO)
               .values_list('id_cliente', flat=True).first())
    if cliente is not None:
        Cliente.objects.select_for_update().get(pk=cliente)
    return cliente


def cancelar_por_venta(venta, usuario, monto=None, motivo='Anulación de la venta'):
    """La venta se anuló (o se devolvió parte): su deuda baja sola.

    Devuelve cuánto se canceló. Con ``monto`` (una devolución) cancela hasta
    ese importe; sin él (una anulación), todo lo que falte.
    """
    fiado = MovimientoCuenta.objects.filter(id_venta=venta, tipo_movimiento=FIADO).first()
    if fiado is None:
        return CERO
    Cliente.objects.select_for_update().get(pk=fiado.id_cliente_id)
    return _achicar(fiado, fiado.saldo_pendiente if monto is None else monto, CANCELACION, usuario, motivo)


def medios_de_cobro_de(venta):
    """Los medios con que pagó la deuda de esta venta."""
    return list(MovimientoCuenta.objects.filter(id_venta=venta, tipo_movimiento=COBRO)
                .values_list('id_medio_pago', flat=True).distinct())


def cobrado_de(venta, medio):
    """Cuánto pagó de la deuda de esta venta con ``medio``."""
    return (MovimientoCuenta.objects.filter(id_venta=venta, tipo_movimiento=COBRO, id_medio_pago=medio)
            .aggregate(t=Sum('monto'))['t'] or CERO)


def lo_que_falta_de(venta):
    """Cuánto de esta venta sigue sin pagarse (0 si no fue fiada)."""
    return (MovimientoCuenta.objects.filter(id_venta=venta, tipo_movimiento=FIADO)
            .values_list('saldo_pendiente', flat=True).first() or CERO)


@transaction.atomic
def cobrar(cliente, monto, medio, usuario, turno=None, interes_porcentaje=CERO, acreditar_puntos=None):
    """Registra un pago del cliente. Devuelve un dict con lo que pasó.

    * ``monto``: lo que paga de deuda. Se aplica del fiado más viejo al más
      nuevo.
    * ``interes_porcentaje``: recargo por mora, sobre lo que se cobra ahora.
      0 por defecto. Se cobra en el momento: no agranda la deuda.
    * ``acreditar_puntos``: None = lo que corresponde (sí si paga en
      término, no si pagó tarde); True o False = lo que decidió la dueña.
      Los puntos de una compra se acreditan cuando esa compra queda paga.
    * Si el medio entra al cajón (efectivo), hace falta ``turno`` abierto.
    """
    from ventas import caja
    from ventas.models import TipoMovimientoCaja

    cliente = Cliente.objects.select_for_update().get(pk=cliente.pk)
    monto = Decimal(monto).quantize(CENTAVO)
    interes_porcentaje = Decimal(interes_porcentaje or 0)
    debe = saldo(cliente)
    if debe <= 0:
        raise ValidationError(f'{cliente} no debe nada.')
    if monto <= 0:
        raise ValidationError('Escribí cuánto paga.')
    if monto > debe:
        raise ValidationError(f'{cliente} debe {pesos(debe)}: no se le puede cobrar {pesos(monto)}.')
    if not 0 <= interes_porcentaje <= 100:
        raise ValidationError('El recargo va entre 0 y 100 %.')
    if medio is None or medio.es_cuenta_corriente or medio.borrado_logico:
        raise ValidationError('Elegí con qué paga.')
    if medio.afecta_caja and (turno is None or not turno.abierto):
        raise ValidationError('Para cobrar en efectivo tenés que tener la caja abierta.')
    if turno is not None and not turno.abierto:
        turno = None        # el turno sólo dice dónde se cobró; uno cerrado no recibe nada
    hoy = timezone.localdate()
    interes = (monto * interes_porcentaje / 100).quantize(CENTAVO)
    resto, saldados, puntos = monto, [], 0
    ultimo = MovimientoCuenta.objects.order_by('-pk').values_list('pk', flat=True).first() or 0
    for fiado in pendientes(cliente):
        if resto <= 0:
            break
        aplicado = _achicar(fiado, resto, COBRO, usuario, medio=medio, turno=turno)
        resto -= aplicado
        fiado.refresh_from_db()
        if fiado.saldo_pendiente == 0:
            saldados.append(fiado)
    for fiado in saldados:
        if fiado.puntos_acreditados or fiado.id_venta.estado_venta != 'COMPLETADA':
            continue
        corresponde = fiado.fecha_vencimiento >= hoy if acreditar_puntos is None else acreditar_puntos
        if corresponde:
            from fidelizacion import puntos as libro_de_puntos
            # Se acreditan los de la compra entera y, si devolvió parte,
            # se descuenta lo devuelto: queda lo que se habría ganado
            # pagando al contado lo que se quedó.
            ganados = libro_de_puntos.acumular(fiado.id_venta).puntos or 0
            ganados -= libro_de_puntos.revertir_por_devolucion(fiado.id_venta).acumulacion or 0
            puntos += ganados
            MovimientoCuenta.objects.filter(pk=fiado.pk).update(puntos_acreditados=True)
    if interes > 0:
        MovimientoCuenta.objects.create(
            id_cliente=cliente, tipo_movimiento=INTERES, monto=interes, id_usuario=usuario,
            id_medio_pago=medio, id_turno_caja=turno,
            motivo=f'{interes_porcentaje.normalize():f} % sobre {pesos(monto)}')
    if medio.afecta_caja:
        caja.registrar_movimiento(
            turno, usuario, TipoMovimientoCaja.COBRO_CUENTA, monto + interes,
            f'Cobro de cuenta corriente a {cliente}'[:120])
    recibo_n = (MovimientoCuenta.objects.filter(id_cliente=cliente, tipo_movimiento=COBRO, pk__gt=ultimo)
                .order_by('pk').values_list('pk', flat=True).first())
    return {'cobrado': monto, 'interes': interes, 'total': monto + interes, 'queda': debe - monto,
            'puntos': puntos, 'compras_saldadas': len(saldados), 'recibo': recibo_n}


@transaction.atomic
def cambiar_estado(cliente, estado, usuario, limite=None, nota=None, plazo=None):
    """Habilitar, suspender o rehabilitar; y de paso el tope, la nota y
    los días que se le dan para pagar (lo que la caja propone)."""
    if not puede_gestionar(usuario):
        raise ValidationError('Eso lo decide quien gestiona el fiado.')
    cliente = Cliente.objects.select_for_update().get(pk=cliente.pk)
    # INCOBRABLE no se elige a mano, pero quien ya lo es puede seguir
    # siéndolo mientras se le cambia el tope o la nota.
    sigue_igual = estado == EstadoCredito.INCOBRABLE == cliente.estado_credito
    if not sigue_igual and estado not in (EstadoCredito.SIN_CREDITO, EstadoCredito.HABILITADO,
                                          EstadoCredito.SANCIONADO):
        raise ValidationError('Ese estado no se elige a mano.')
    if estado == EstadoCredito.SIN_CREDITO and saldo(cliente) > 0:
        raise ValidationError(f'{cliente} todavía debe {pesos(saldo(cliente))}: no se le puede sacar la cuenta.')
    cambios = {'estado_credito': estado}
    if limite is not None:
        limite = Decimal(limite)
        if limite < 0:
            raise ValidationError('El tope no puede ser negativo.')
        cambios['limite_credito'] = limite
    if nota is not None:
        cambios['nota_credito'] = (nota.strip()[:120] or None)
    if plazo is not None:
        try:
            plazo = int(plazo)
        except (TypeError, ValueError):
            raise ValidationError('Los días para pagar tienen que ser un número entero.')
        if not 1 <= plazo <= 366:
            raise ValidationError('Los días para pagar van de 1 a 366.')
        cambios['plazo_credito_dias'] = plazo
    Cliente.objects.filter(pk=cliente.pk).update(**cambios)
    cliente.refresh_from_db()
    return cliente


@transaction.atomic
def quitar_puntos(cliente, puntos, usuario):
    """Sanción a voluntad de la dueña: descuenta puntos del Club."""
    if not puede_gestionar(usuario):
        raise ValidationError('Eso lo decide quien gestiona el fiado.')
    cliente = Cliente.objects.select_for_update().get(pk=cliente.pk)
    try:
        puntos = int(puntos)
    except (TypeError, ValueError):
        raise ValidationError('Escribí cuántos puntos se le quitan.')
    if puntos <= 0:
        raise ValidationError('Escribí cuántos puntos se le quitan.')
    if puntos > cliente.puntos_disponibles:
        raise ValidationError(f"{cliente} tiene {pluralizar(cliente.puntos_disponibles, 'punto')}: no se le pueden quitar {puntos}.")
    MovimientoPuntos.objects.create(id_cliente=cliente, tipo_movimiento='AJUSTE', puntos=-puntos)
    cliente.refresh_from_db()
    return cliente


@transaction.atomic
def declarar_incobrable(cliente, motivo, usuario):
    """Da por perdido todo lo que debe. El cliente queda INCOBRABLE."""
    if not puede_gestionar(usuario):
        raise ValidationError('Eso lo decide quien gestiona el fiado.')
    cliente = Cliente.objects.select_for_update().get(pk=cliente.pk)
    motivo = ' '.join((motivo or '').split())
    if len(motivo) < 5:
        raise ValidationError('Contá por qué se da por perdida (queda escrito).')
    perdido = CERO
    for fiado in pendientes(cliente):
        perdido += _achicar(fiado, fiado.saldo_pendiente, INCOBRABLE, usuario, motivo[:120])
    if perdido <= 0:
        raise ValidationError(f'{cliente} no debe nada.')
    Cliente.objects.filter(pk=cliente.pk).update(estado_credito=EstadoCredito.INCOBRABLE)
    return perdido


# ---------------------------------------------------------------------------
#  Listados: una consulta cada uno, sin una por cliente.
# ---------------------------------------------------------------------------
def deudores(hoy=None, solo_en_mora=False):
    """Los clientes con deuda (o suspendidos), con lo que deben y el atraso."""
    hoy = hoy or timezone.localdate()
    fiado = Q(movimientos_cuenta__tipo_movimiento=FIADO, movimientos_cuenta__saldo_pendiente__gt=0)
    vencido = fiado & Q(movimientos_cuenta__fecha_vencimiento__lt=hoy)
    consulta = (Cliente.objects
                .annotate(debe=Sum('movimientos_cuenta__saldo_pendiente', filter=fiado),
                          vencido=Sum('movimientos_cuenta__saldo_pendiente', filter=vencido),
                          vencio_el=Min('movimientos_cuenta__fecha_vencimiento', filter=vencido),
                          proximo=Min('movimientos_cuenta__fecha_vencimiento', filter=fiado),
                          compras=Count('movimientos_cuenta', filter=fiado))
                .filter(Q(debe__gt=0) | Q(estado_credito=EstadoCredito.SANCIONADO)))
    if solo_en_mora:
        consulta = consulta.filter(Q(vencido__gt=0) | Q(estado_credito=EstadoCredito.SANCIONADO))
    filas = []
    for cliente in consulta.order_by('vencio_el', 'apellido_cliente'):
        cliente.debe = cliente.debe or CERO
        cliente.vencido = cliente.vencido or CERO
        cliente.dias_de_atraso = (hoy - cliente.vencio_el).days if cliente.vencio_el else 0
        filas.append(cliente)
    return filas


VISTAS = (('deuda', 'Con deuda'), ('mora', 'Vencidos / Suspendidos'),
          ('incobrables', 'Incobrables'), ('todos', 'Todos'))


def listado(hoy=None):
    """Todos los clientes que alguna vez tuvieron cuenta corriente, con lo
    que deben, lo vencido y lo dado por perdido. Una consulta; las vistas
    del listado (con deuda, en mora, incobrables, todos) se arman de acá
    para que los contadores y las filas salgan de lo mismo."""
    hoy = hoy or timezone.localdate()
    fiado = Q(movimientos_cuenta__tipo_movimiento=FIADO, movimientos_cuenta__saldo_pendiente__gt=0)
    vencido = fiado & Q(movimientos_cuenta__fecha_vencimiento__lt=hoy)
    perdida = Q(movimientos_cuenta__tipo_movimiento=INCOBRABLE)
    consulta = (Cliente.objects
                .annotate(debe=Sum('movimientos_cuenta__saldo_pendiente', filter=fiado),
                          vencido=Sum('movimientos_cuenta__saldo_pendiente', filter=vencido),
                          vencio_el=Min('movimientos_cuenta__fecha_vencimiento', filter=vencido),
                          proximo=Min('movimientos_cuenta__fecha_vencimiento', filter=fiado),
                          perdido=Sum('movimientos_cuenta__monto', filter=perdida),
                          perdido_el=Max('movimientos_cuenta__fecha_movimiento', filter=perdida),
                          movimientos=Count('movimientos_cuenta'))
                .filter(~Q(estado_credito=EstadoCredito.SIN_CREDITO) | Q(movimientos__gt=0)))
    filas = []
    for cliente in consulta.order_by('vencio_el', 'apellido_cliente', 'nombre_cliente'):
        cliente.debe = cliente.debe or CERO
        cliente.vencido = cliente.vencido or CERO
        cliente.perdido = cliente.perdido or CERO
        cliente.dias_de_atraso = (hoy - cliente.vencio_el).days if cliente.vencio_el else 0
        cliente.vistas = {'todos'}
        if cliente.debe > 0:
            cliente.vistas.add('deuda')
        if cliente.vencido > 0 or cliente.estado_credito == EstadoCredito.SANCIONADO:
            cliente.vistas.add('mora')
        if cliente.estado_credito == EstadoCredito.INCOBRABLE or cliente.perdido > 0:
            cliente.vistas.add('incobrables')
        filas.append(cliente)
    return filas


def del_periodo(desde, hasta):
    """Lo que pasó en la cuenta corriente entre dos fechas, para el resumen
    del mes: fiado, cobrado por medio, recargos, cancelado y perdido."""
    base = MovimientoCuenta.objects.filter(fecha_movimiento__date__gte=desde, fecha_movimiento__date__lte=hasta)
    por_tipo = dict(base.values_list('tipo_movimiento').annotate(t=Sum('monto')))
    cobros = [(fila['id_medio_pago__nombre_medio_pago'], fila['t'])
              for fila in base.filter(tipo_movimiento__in=(COBRO, INTERES))
                              .values('id_medio_pago__nombre_medio_pago').annotate(t=Sum('monto')).order_by('-t')]
    return {'fiado': por_tipo.get(FIADO, CERO), 'cobrado': por_tipo.get(COBRO, CERO),
            'interes': por_tipo.get(INTERES, CERO), 'cancelado': por_tipo.get(CANCELACION, CERO),
            'incobrable': por_tipo.get(INCOBRABLE, CERO), 'cobros_por_medio': cobros,
            'saldo_total': MovimientoCuenta.objects.filter(tipo_movimiento=FIADO)
                           .aggregate(t=Sum('saldo_pendiente'))['t'] or CERO}


def alertas(hoy=None, dias=3):
    """Para el aviso de la mañana: quién está atrasado y qué vence pronto."""
    hoy = hoy or timezone.localdate()
    atrasados = [{'cliente': f'{c.nombre_cliente} {c.apellido_cliente}', 'id': c.pk,
                  'debe': str(c.debe), 'vencido': str(c.vencido), 'dias_de_atraso': c.dias_de_atraso,
                  'nota': c.nota_credito or '', 'suspendido': c.estado_credito == EstadoCredito.SANCIONADO}
                 for c in deudores(hoy, solo_en_mora=True)]
    proximos = [{'cliente': f'{f.id_cliente.nombre_cliente} {f.id_cliente.apellido_cliente}', 'id': f.id_cliente_id,
                 'venta': f.id_venta_id, 'falta': str(f.saldo_pendiente), 'vence': f.fecha_vencimiento.isoformat()}
                for f in (MovimientoCuenta.objects
                          .filter(tipo_movimiento=FIADO, saldo_pendiente__gt=0,
                                  fecha_vencimiento__gte=hoy, fecha_vencimiento__lte=hoy + timedelta(days=dias))
                          .select_related('id_cliente').order_by('fecha_vencimiento')[:50])]
    total = MovimientoCuenta.objects.filter(tipo_movimiento=FIADO).aggregate(t=Sum('saldo_pendiente'))['t'] or CERO
    return {'te_deben': str(total), 'atrasados': atrasados[:50], 'vencen_pronto': proximos}
