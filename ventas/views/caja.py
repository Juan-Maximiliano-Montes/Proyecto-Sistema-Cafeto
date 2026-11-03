"""El puesto de caja: elegir el cajón, abrir el turno, mover efectivo y cerrar.

Las reglas viven en ``ventas.caja``; acá sólo se pide, se valida el
formulario y se muestra el resultado. Tres cuidados propios de la pantalla:

* **Un solo envío.** Cada formulario lleva un token de un solo uso
  (``nucleo.idempotencia``): un doble toque en la tablet no hace dos
  retiros.
* **Arqueo ciego, de verdad.** El cierre va en dos pasos. Primero se
  declara lo contado sin ver lo esperado; ese número queda fijo y
  auditado. Recién después se muestra la diferencia y se pide la
  explicación. Si se pudiera ver la diferencia y volver a "contar", el
  arqueo ciego no serviría de nada.
* **El saldo del cajón** lo ve sólo quien tiene ``ver_caja``. La cajera ve
  sus movimientos, no cuánto "debería" haber.
"""
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from negocio.models import ParametrosNegocio
from nucleo import idempotencia
from nucleo.paginacion import TAMANIOS_TABLA, paginar
from seguridad.decorators import requiere_alguno, requiere_permiso
from seguridad.servicios import (
    autorizar_con_clave,
    instantanea,
    registrar_acceso_denegado,
    registrar_auditoria,
)
from ventas import caja as servicio_caja
from ventas import puesto
from ventas.forms import (
    AperturaForm,
    ArqueoForm,
    ConfirmacionCierreForm,
    MovimientoEfectivoForm,
)
from ventas.models import CajaRegistradora, MovimientoCaja, TurnoCaja

PERMISOS_DEL_PUESTO = ('vender', 'abrir_caja', 'cerrar_caja',
                       'mover_efectivo', 'ver_caja')
CLAVE_ARQUEO = 'cafeto_arqueo'


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _con_token(formulario_clase, request, **kwargs):
    return formulario_clase(
        initial={'token_operacion': idempotencia.emitir(request), **kwargs})


def _arqueo_pendiente(request, turno):
    """Lo declarado en el paso 1 del cierre de ESTE turno, o None."""
    datos = request.session.get(CLAVE_ARQUEO)
    if not datos or turno is None or datos.get('turno') != turno.pk:
        return None
    if datos.get('usuario') != request.usuario_actual.pk:
        return None
    return datos


# ===========================================================================
#  LA PANTALLA
# ===========================================================================
@requiere_alguno(*PERMISOS_DEL_PUESTO)
def puesto_de_caja(request):
    usuario = request.usuario_actual
    caja = puesto.caja_de(request)
    turno = servicio_caja.turno_abierto(caja) if caja else None

    contexto = {'caja': caja, 'turno': turno}
    if caja is None:
        abiertos = {t.id_caja_registradora_id: t for t in
                    TurnoCaja.objects.filter(estado_turno='ABIERTA')
                    .select_related('id_usuario_apertura')}
        contexto['cajas'] = [
            (c, abiertos.get(c.pk))
            for c in CajaRegistradora.objects.filter(borrado_logico=False)]
        return render(request, 'ventas/caja.html', contexto)

    if turno is None:
        contexto['form_apertura'] = _con_token(
            AperturaForm, request,
            monto_inicial=ParametrosNegocio.vigentes().fondo_caja_sugerido)
        contexto['ultimo_turno'] = (TurnoCaja.objects
                                    .filter(id_caja_registradora=caja)
                                    .exclude(estado_turno='ABIERTA')
                                    .select_related('id_usuario_cierre')
                                    .order_by('-fecha_cierre').first())
        return render(request, 'ventas/caja.html', contexto)

    movimientos = (MovimientoCaja.objects.filter(id_turno_caja=turno)
                   .select_related('id_usuario').order_by('-fecha_hora', '-pk'))
    contexto.update(paginar(request, movimientos, TAMANIOS_TABLA))
    ventas = turno.ventas.filter(estado_venta='COMPLETADA').aggregate(
        cantidad=Count('pk'), total=Sum('total_venta'))
    contexto.update({
        'cantidad_ventas': ventas['cantidad'] or 0,
        've_saldo': usuario.puede('ver_caja'),
        'form_movimiento': _con_token(MovimientoEfectivoForm, request),
        'form_arqueo': _con_token(ArqueoForm, request),
        'arqueo': _arqueo_pendiente(request, turno),
    })
    if contexto['ve_saldo']:
        contexto['saldo'] = servicio_caja.saldo(turno)
        contexto['total_ventas'] = ventas['total'] or Decimal('0')
        contexto['resumen'] = [(e, m) for e, m in servicio_caja.resumen(turno) if m]
    return render(request, 'ventas/caja.html', contexto)


@require_POST
@requiere_alguno(*PERMISOS_DEL_PUESTO)
def elegir_caja(request):
    """Esta tablet opera en este cajón (o en ninguno, para cambiarlo)."""
    caja = None
    if request.POST.get('caja'):
        caja = CajaRegistradora.objects.filter(
            pk=request.POST.get('caja'), borrado_logico=False).first()
        if caja is None:
            messages.error(request, 'Esa caja no existe o está dada de baja.')
            return redirect('ventas:caja')
    puesto.fijar(request, caja)
    if caja is not None:
        messages.success(request, f'Este dispositivo opera en {caja}.')
    return puesto.recordar(redirect('ventas:caja'), caja,
                           segura=request.is_secure())


# ===========================================================================
#  APERTURA Y EFECTIVO
# ===========================================================================
@require_POST
@requiere_permiso('abrir_caja', redirigir_a='ventas:caja')
def abrir_turno(request):
    caja = puesto.caja_de(request)
    form = AperturaForm(request.POST)
    if caja is None:
        messages.error(request, 'Primero elegí en qué caja opera este dispositivo.')
        return redirect('ventas:caja')
    if not form.is_valid():
        messages.error(request, _texto(ValidationError(
            [e for errores in form.errors.values() for e in errores])))
        return redirect('ventas:caja')
    token = form.cleaned_data['token_operacion']
    if not idempotencia.consumir(request, token):
        messages.info(request, 'Esa apertura ya se había procesado.')
        return redirect('ventas:caja')
    try:
        turno = servicio_caja.abrir(caja, request.usuario_actual,
                                    form.cleaned_data['monto_inicial'])
    except ValidationError as error:
        messages.error(request, _texto(error))
        return redirect('ventas:caja')
    registrar_auditoria(
        request, 'APERTURA', 'TURNO DE CAJA', instancia=turno,
        nuevo=instantanea(turno),
        detalle=f'Abrió {caja} con $ {turno.monto_inicial} de fondo.')
    messages.success(request, f'{caja} abierta. ¡Buen turno!')
    return redirect('ventas:caja')


@require_POST
@requiere_permiso('mover_efectivo', redirigir_a='ventas:caja')
def mover_efectivo(request):
    turno = puesto.turno_de(request)
    form = MovimientoEfectivoForm(request.POST)
    if turno is None:
        messages.error(request, 'La caja de este dispositivo no tiene un turno abierto.')
        return redirect('ventas:caja')
    if not form.is_valid():
        messages.error(request, _texto(ValidationError(
            [e for errores in form.errors.values() for e in errores])))
        return redirect('ventas:caja')
    if not idempotencia.consumir(request, form.cleaned_data['token_operacion']):
        messages.info(request, 'Ese movimiento ya se había registrado.')
        return redirect('ventas:caja')
    datos = form.cleaned_data
    hacer = servicio_caja.refuerzo if datos['tipo'] == 'REFUERZO' else servicio_caja.retiro
    try:
        movimiento = hacer(turno, request.usuario_actual, datos['monto'],
                           datos['concepto'])
    except ValidationError as error:
        messages.error(request, _texto(error))
        return redirect('ventas:caja')
    registrar_auditoria(
        request, datos['tipo'], 'EFECTIVO', instancia=movimiento,
        nuevo=instantanea(movimiento),
        detalle=f'{movimiento.get_tipo_movimiento_display()} de $ '
                f'{abs(movimiento.monto)} en {turno.id_caja_registradora}: '
                f'{movimiento.concepto}.')
    messages.success(request, f'{movimiento.get_tipo_movimiento_display()} registrado.')
    return redirect('ventas:caja')


# ===========================================================================
#  CIERRE EN DOS PASOS
# ===========================================================================
@require_POST
@requiere_permiso('cerrar_caja', redirigir_a='ventas:caja')
def declarar_arqueo(request):
    """Paso 1: lo contado queda fijo. Después ya no se cambia."""
    turno = puesto.turno_de(request)
    form = ArqueoForm(request.POST)
    if turno is None:
        messages.error(request, 'La caja de este dispositivo no tiene un turno abierto.')
        return redirect('ventas:caja')
    if _arqueo_pendiente(request, turno):
        return redirect('ventas:cierre')
    if not form.is_valid():
        messages.error(request, _texto(ValidationError(
            [e for errores in form.errors.values() for e in errores])))
        return redirect('ventas:caja')
    if not idempotencia.consumir(request, form.cleaned_data['token_operacion']):
        return redirect('ventas:cierre')
    contado = form.cleaned_data['monto_contado']
    request.session[CLAVE_ARQUEO] = {
        'turno': turno.pk, 'usuario': request.usuario_actual.pk,
        'contado': str(contado), 'relevo': form.cleaned_data['relevo'],
        'cuando': timezone.now().isoformat()}
    # Lo declarado queda escrito ANTES de ver la diferencia.
    registrar_auditoria(
        request, 'ARQUEO', 'TURNO DE CAJA', instancia=turno,
        detalle=f'Declaró $ {contado} contados en {turno.id_caja_registradora}.')
    return redirect('ventas:cierre')


@requiere_permiso('cerrar_caja', redirigir_a='ventas:caja')
def cierre(request):
    """Paso 2: la diferencia a la vista, la explicación y, si hace falta,
    la autorización de otra persona."""
    turno = puesto.turno_de(request)
    arqueo = _arqueo_pendiente(request, turno)
    if arqueo is None:
        messages.error(request, 'Primero declarás lo que contaste en el cajón.')
        return redirect('ventas:caja')

    contado = Decimal(arqueo['contado'])
    esperado = servicio_caja.saldo(turno)
    diferencia = contado - esperado
    tolerancia = ParametrosNegocio.vigentes().tolerancia_arqueo
    pide_supervisor = abs(diferencia) > tolerancia
    relevo = arqueo['relevo']

    if request.method == 'POST':
        form = ConfirmacionCierreForm(request.POST)
        form.is_valid()
        datos = form.cleaned_data
        token = datos.get('token_operacion')
        if not idempotencia.consumir(request, token):
            messages.info(request, 'Ese cierre ya se había procesado.')
            return redirect('ventas:caja')
        try:
            supervisor = None
            if pide_supervisor:
                supervisor = autorizar_con_clave(
                    request, datos.get('autoriza_usuario'),
                    datos.get('autoriza_clave'), 'supervisar_cierre',
                    distinto_de=request.usuario_actual,
                    para='cerrar con esta diferencia')
            if relevo:
                entrante = autorizar_con_clave(
                    request, datos.get('entrante_usuario'),
                    datos.get('entrante_clave'), 'abrir_caja',
                    distinto_de=request.usuario_actual,
                    para='tomar el turno')
                cerrado, nuevo = servicio_caja.relevar(
                    turno, request.usuario_actual, contado, entrante,
                    datos.get('observacion'), supervisor=supervisor)
            else:
                cerrado = servicio_caja.cerrar(
                    turno, request.usuario_actual, contado,
                    datos.get('observacion'), supervisor=supervisor)
                nuevo = None
        except ValidationError as error:
            idempotencia.devolver(request, token)
            form.add_error(None, _texto(error))
        else:
            request.session.pop(CLAVE_ARQUEO, None)
            registrar_auditoria(
                request, 'CIERRE', 'TURNO DE CAJA', instancia=cerrado,
                nuevo=instantanea(cerrado),
                detalle=(f'Cerró {cerrado.id_caja_registradora}: contado $ '
                         f'{cerrado.monto_final}, esperado $ '
                         f'{cerrado.monto_esperado}, diferencia $ '
                         f'{cerrado.diferencia_caja}'
                         + (f'; supervisó {supervisor}' if supervisor else '')
                         + (f'; sigue {nuevo.id_usuario_apertura}' if nuevo else '')
                         + '.'))
            if nuevo:
                messages.success(request, f'Relevo hecho: el turno sigue a nombre de {nuevo.id_usuario_apertura}.')
            else:
                messages.success(request, f'{cerrado.id_caja_registradora} cerrada. ¡Gracias!')
            # El documento de recaudación del turno que se acaba de cerrar.
            return redirect('ventas:recaudacion', pk=cerrado.pk)
    else:
        form = _con_token(ConfirmacionCierreForm, request)

    return render(request, 'ventas/cierre.html', {
        'turno': turno, 'contado': contado, 'esperado': esperado,
        'diferencia': diferencia, 'tolerancia': tolerancia,
        'pide_supervisor': pide_supervisor, 'relevo': relevo, 'form': form,
    })


@require_POST
@requiere_permiso('cerrar_caja', redirigir_a='ventas:caja')
def descartar_arqueo(request):
    """Vuelve atrás del paso 2 SIN borrar lo declarado de la auditoría.

    Sirve si se apretó "cerrar" por error en medio del turno. Para cerrar
    hay que volver a contar, y el primer conteo ya quedó asentado.
    """
    turno = puesto.turno_de(request)
    if _arqueo_pendiente(request, turno):
        request.session.pop(CLAVE_ARQUEO, None)
        registrar_auditoria(
            request, 'ARQUEO DESCARTADO', 'TURNO DE CAJA', instancia=turno,
            detalle='Volvió atrás del cierre sin cerrar el turno.')
    return redirect('ventas:caja')


# ===========================================================================
#  TURNOS (para quien supervisa)
# ===========================================================================
def _turnos_filtrados(request):
    turnos = (TurnoCaja.objects
              .select_related('id_caja_registradora', 'id_usuario_apertura',
                              'id_usuario_cierre', 'id_usuario_supervisor')
              .order_by('-fecha_apertura'))
    caja = request.GET.get('caja', '')
    if caja.isdigit():
        turnos = turnos.filter(id_caja_registradora_id=caja)
    estado = request.GET.get('estado', '')
    if estado == 'con_diferencia':
        turnos = turnos.exclude(diferencia_caja=0).filter(diferencia_caja__isnull=False)
    elif estado == 'abiertos':
        turnos = turnos.filter(estado_turno='ABIERTA')
    for clave, filtro in (('desde', 'fecha_apertura__date__gte'),
                          ('hasta', 'fecha_apertura__date__lte')):
        valor = request.GET.get(clave, '')
        if not valor:
            continue
        try:
            turnos = turnos.filter(**{filtro: date.fromisoformat(valor)})
        except ValueError:
            pass        # una fecha mal escrita se ignora, no rompe la pantalla
    return turnos


@requiere_permiso('ver_caja', redirigir_a='ventas:caja')
def turnos(request):
    consulta = _turnos_filtrados(request)
    contexto = paginar(request, consulta, TAMANIOS_TABLA)
    contexto.update({
        'cajas': CajaRegistradora.objects.all(),
        'filtro': request.GET,
        'diferencia_total': consulta.aggregate(t=Sum('diferencia_caja'))['t'] or Decimal('0'),
    })
    return render(request, 'ventas/turnos.html', contexto)


@requiere_permiso('ver_caja', redirigir_a='ventas:caja')
def ficha_turno(request, pk):
    turno = get_object_or_404(
        TurnoCaja.objects.select_related(
            'id_caja_registradora', 'id_usuario_apertura', 'id_usuario_cierre',
            'id_usuario_supervisor'), pk=pk)
    return render(request, 'ventas/_ficha_turno.html', {
        'turno': turno,
        'resumen': [(e, m) for e, m in servicio_caja.resumen(turno) if m],
        'movimientos': turno.movimientos.select_related('id_usuario')
                       .order_by('fecha_hora', 'pk')[:200],
        'ventas': turno.ventas.aggregate(
            cantidad=Count('pk', filter=Q(estado_venta='COMPLETADA')),
            total=Sum('total_venta', filter=Q(estado_venta='COMPLETADA')),
            anuladas=Count('pk', filter=Q(estado_venta='ANULADA'))),
        'recaudacion': servicio_caja.recaudacion(turno),
    })


@requiere_alguno('cerrar_caja', 'ver_caja')
def recaudacion(request, pk):
    """Documento de recaudación de un turno cerrado (DFD 6): lo que se
    contó, lo esperado, la diferencia y lo cobrado por medio de pago.

    Lo ve quien cerró ese turno (sale al terminar el cierre, para imprimir
    y guardar con la plata) y quien supervisa la caja.
    """
    turno = get_object_or_404(
        TurnoCaja.objects.select_related(
            'id_caja_registradora', 'id_usuario_apertura', 'id_usuario_cierre',
            'id_usuario_supervisor'), pk=pk)
    usuario = request.usuario_actual
    if not (usuario.puede('ver_caja') or turno.id_usuario_cierre_id == usuario.pk):
        registrar_acceso_denegado(request, 'ver_caja')
        messages.error(request, 'Ese turno lo cerró otra persona.')
        return redirect('ventas:caja')
    if turno.abierto:
        messages.info(request, 'El turno todavía está abierto: el documento '
                               'sale al cerrarlo.')
        return redirect('ventas:caja')
    filas = servicio_caja.recaudacion(turno)
    return render(request, 'ventas/recaudacion.html', {
        'turno': turno, 'filas': filas,
        'total': sum((f['total'] for f in filas), Decimal('0')),
        'resumen': [(e, m) for e, m in servicio_caja.resumen(turno) if m],
        'negocio': ParametrosNegocio.vigentes(),
    })


@requiere_permiso('ver_caja', redirigir_a='ventas:caja')
def exportar_turnos(request):
    from nucleo.exportar import ColumnaExportable, exportar

    columnas = [
        ColumnaExportable('N°', 'numero'), ColumnaExportable('Caja'),
        ColumnaExportable('Apertura', 'fecha_hora'), ColumnaExportable('Abrió'),
        ColumnaExportable('Cierre', 'fecha_hora'), ColumnaExportable('Cerró'),
        ColumnaExportable('Fondo', 'dinero'), ColumnaExportable('Esperado', 'dinero'),
        ColumnaExportable('Contado', 'dinero'), ColumnaExportable('Diferencia', 'dinero'),
        ColumnaExportable('Tipo de cierre'), ColumnaExportable('Supervisó'),
        ColumnaExportable('Observación'),
    ]
    filas = ([t.pk, str(t.id_caja_registradora), t.fecha_apertura,
              str(t.id_usuario_apertura), t.fecha_cierre,
              str(t.id_usuario_cierre or ''), t.monto_inicial, t.monto_esperado,
              t.monto_final, t.diferencia_caja,
              t.get_tipo_cierre_display() if t.tipo_cierre else 'Abierto',
              str(t.id_usuario_supervisor or ''), t.observacion_diferencia or '']
             for t in _turnos_filtrados(request).iterator(chunk_size=500))
    formato = request.GET.get('formato')
    respuesta = exportar(formato, 'Turnos de caja', columnas, filas)
    registrar_auditoria(request, 'EXPORTACION', 'TURNOS DE CAJA',
                        detalle='Exportó los turnos de caja.', modulo='VENTAS')
    return respuesta
