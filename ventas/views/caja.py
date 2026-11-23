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

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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
    traspasar_sesion,
)
from ventas import a_distancia
from ventas import caja as servicio_caja
from ventas import puesto
from ventas.forms import (
    AperturaForm,
    ArqueoForm,
    ConfirmacionCierreForm,
    CorteXForm,
    MovimientoEfectivoForm,
)
from ventas.models import CajaRegistradora, MovimientoCaja, TipoMovimientoCaja, TurnoCaja
from nucleo.templatetags.cafeto import pesos
from nucleo.validaciones import id_valido
from nucleo import json_seguro

PERMISOS_DEL_PUESTO = ('vender', 'abrir_caja', 'cerrar_caja',
                       'mover_efectivo', 'ver_caja')
CLAVE_ARQUEO = 'cafeto_arqueo'


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _con_token(formulario_clase, request, **kwargs):
    return formulario_clase(
        initial={'token_operacion': idempotencia.emitir(request), **kwargs})


def _nombre(usuario):
    return f'{usuario.nombre} {usuario.apellido}'.strip()


def _modo(arqueo):
    return arqueo.get('modo') or (ArqueoForm.RELEVO if arqueo.get('relevo') else ArqueoForm.CIERRE)


def _arqueo_pendiente(request, turno):
    """Lo declarado en el paso 1 del cierre de ESTE turno, o None."""
    datos = request.session.get(CLAVE_ARQUEO)
    if (datos and turno is not None and datos.get('turno') == turno.pk
            and datos.get('usuario') == request.usuario_actual.pk):
        return datos
    # Un conteo al que ya se le mostró la diferencia sigue en pie aunque se
    # haya cerrado la sesión: se retoma tal cual, no se vuelve a contar.
    fijo = servicio_caja.conteo_revelado(turno)
    if fijo is None:
        return None
    return {'turno': turno.pk, 'usuario': request.usuario_actual.pk,
            'contado': fijo['contado'], 'relevo': fijo['relevo'], 'modo': fijo['modo'],
            'revelado': True}


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
    # Arqueo ciego (RN del cierre): esta es la pantalla del puesto, la que
    # usa quien va a contar el cajón, sea quien sea (el que abrió, el que lo
    # releva o el encargado que cierra). Mientras el turno está abierto no
    # muestra lo esperado a NADIE: ni el saldo, ni el resumen por tipo, ni
    # el importe de cada movimiento (sumándolos se reconstruye el saldo).
    # Quien supervisa lo ve en "Turnos de caja".
    # Quien llega y encuentra el turno de otra persona abierto (se fue sin
    # cerrar) puede cerrarlo por relevo y abrir el suyo (DD-97).
    de_otro = turno.id_usuario_apertura_id != usuario.pk
    ausencia = de_otro and usuario.puede('abrir_caja')
    vale = request.GET.get('vale', '')
    contexto.update({
        'cantidad_ventas': ventas['cantidad'] or 0,
        'form_movimiento': _con_token(MovimientoEfectivoForm, request),
        'form_arqueo': ArqueoForm(ausencia=ausencia,
                                  initial={'token_operacion': idempotencia.emitir(request)}),
        'form_corte': _con_token(CorteXForm, request),
        'arqueo': _arqueo_pendiente(request, turno),
        'de_otro': de_otro,
        'puede_ausencia': ausencia,
        'cortes': servicio_caja.cortes_x(turno),
        'vale': (MovimientoCaja.objects.filter(pk=id_valido(vale), id_turno_caja=turno,
                                               id_usuario=usuario).first()
                 if id_valido(vale) else None),
    })
    return render(request, 'ventas/caja.html', contexto)


@require_POST
@requiere_alguno(*PERMISOS_DEL_PUESTO)
def elegir_caja(request):
    """Esta tablet opera en este cajón (o en ninguno, para cambiarlo)."""
    caja = None
    if request.POST.get('caja'):
        caja = CajaRegistradora.objects.filter(
            pk=id_valido(request.POST.get('caja')), borrado_logico=False).first()
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
        detalle=f'Abrió {caja} con {pesos(turno.monto_inicial)} de fondo.')
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
        detalle=f'{movimiento.get_tipo_movimiento_display()} de {pesos(abs(movimiento.monto))} '
                f'en {turno.id_caja_registradora}: '
                f'{movimiento.concepto}.')
    messages.success(request, f'{movimiento.get_tipo_movimiento_display()} registrado.')
    return redirect(f"{reverse('ventas:caja')}?vale={movimiento.pk}")


# ===========================================================================
#  CIERRE EN DOS PASOS
# ===========================================================================
@require_POST
@requiere_permiso('cerrar_caja', redirigir_a='ventas:caja')
def declarar_arqueo(request):
    """Paso 1: lo contado queda fijo. Después ya no se cambia."""
    turno = puesto.turno_de(request)
    form = ArqueoForm(request.POST, ausencia=True)
    if turno is None:
        messages.error(request, 'La caja de este dispositivo no tiene un turno abierto.')
        return redirect('ventas:caja')
    if _arqueo_pendiente(request, turno):
        return redirect('ventas:cierre')
    if not form.is_valid():
        messages.error(request, _texto(ValidationError(
            [e for errores in form.errors.values() for e in errores])))
        return redirect('ventas:caja')
    usuario = request.usuario_actual
    modo = form.cleaned_data['modo']
    if modo == ArqueoForm.AUSENCIA and turno.id_usuario_apertura_id == usuario.pk:
        messages.error(request, 'Ese turno lo abriste vos: cerralo normalmente o hacé el '
                                'cambio de turno con quien entra.')
        return redirect('ventas:caja')
    if modo == ArqueoForm.AUSENCIA and not usuario.puede('abrir_caja'):
        messages.error(request, 'Para cerrar el turno anterior y abrir el tuyo tu perfil tiene '
                                'que poder abrir la caja.')
        return redirect('ventas:caja')
    if not idempotencia.consumir(request, form.cleaned_data['token_operacion']):
        return redirect('ventas:cierre')
    contado = form.cleaned_data['monto_contado']
    relevo = modo == ArqueoForm.RELEVO
    request.session[CLAVE_ARQUEO] = {
        'turno': turno.pk, 'usuario': usuario.pk,
        'contado': str(contado), 'relevo': relevo, 'modo': modo,
        'cuando': timezone.now().isoformat()}
    # Lo declarado queda escrito ANTES de ver la diferencia.
    registrar_auditoria(
        request, 'ARQUEO', 'TURNO DE CAJA', instancia=turno,
        anterior=instantanea(turno),
        nuevo={**instantanea(turno), 'arqueo_declarado': {
            'monto_contado': str(contado), 'relevo': relevo, 'modo': modo}},
        detalle=f'Declaró {pesos(contado)} contados en {turno.id_caja_registradora}'
                + (f' para cerrar por relevo el turno de {_nombre(turno.id_usuario_apertura)}, '
                   'que se fue sin cerrar' if modo == ArqueoForm.AUSENCIA else '')
                + '.')
    return redirect('ventas:cierre')


def _modos_posibles(turno, usuario):
    """Qué puede hacer con el turno quien está en la pantalla (DD-101).

    Cerrar, siempre. Cambio de turno, siempre (quien sigue firma que recibe
    el cajón). Cerrar el turno de otra persona y abrir el propio, sólo si el
    turno lo abrió otra persona y quien opera puede abrir la caja."""
    modos = [ArqueoForm.CIERRE, ArqueoForm.RELEVO]
    if turno.id_usuario_apertura_id != usuario.pk and usuario.puede('abrir_caja'):
        modos.append(ArqueoForm.AUSENCIA)
    return modos


def _es_quien_opera(identificador, usuario):
    identificador = (identificador or '').strip().lower()
    return bool(identificador) and identificador in {
        (usuario.dni_usuario or '').lower(), (usuario.nombre_usuario or '').lower()}


SIGUE_EL_MISMO = ('En un cambio de turno, quien recibe el cajón tiene que ser el compañero que entra '
                  '(si seguís vos o cerrás por hoy, elegí "Cerrar la caja").')


def _datos_del_cierre(request, turno, arqueo):
    contado = Decimal(arqueo['contado'])
    esperado = servicio_caja.saldo(turno)
    diferencia = contado - esperado
    tolerancia = ParametrosNegocio.vigentes().tolerancia_arqueo
    pide_supervisor = abs(diferencia) > tolerancia
    return contado, esperado, diferencia, tolerancia, pide_supervisor


@requiere_permiso('cerrar_caja', redirigir_a='ventas:caja')
def cierre(request):
    """Paso 2: la diferencia a la vista, la explicación y, si hace falta,
    la aprobación de otra persona.

    Dos firmas que no se mezclan (DD-97):

    * **Custodia.** En un relevo, quien entra pone su clave para decir
      «recibí este cajón con este fondo». Nada más: su clave nunca aprueba
      una diferencia (dos cajeros no pueden ponerse de acuerdo para tapar
      un faltante).
    * **Perdón.** Una diferencia que supera lo tolerado la aprueba la
      titular o quien supervisa cierres: al lado de la caja con su clave, a
      distancia (enlace, PIN o bandeja), o —para no trabar la fila— se
      cierra igual con un motivo y el turno queda pendiente de revisión.
    """
    usuario = request.usuario_actual
    turno = puesto.turno_de(request)
    arqueo = _arqueo_pendiente(request, turno)
    if arqueo is None:
        messages.error(request, 'Primero declarás lo que contaste en el cajón.')
        return redirect('ventas:caja')

    contado, esperado, diferencia, tolerancia, pide_supervisor = _datos_del_cierre(request, turno, arqueo)
    modos = _modos_posibles(turno, usuario)
    modo = _modo(arqueo)
    if modo not in modos:
        modo = ArqueoForm.CIERRE
    if request.method == 'POST' and request.POST.get('modo') in modos:
        modo = request.POST['modo']
    relevo = modo == ArqueoForm.RELEVO
    ausencia = modo == ArqueoForm.AUSENCIA
    abrio_otra_persona = turno.id_usuario_apertura_id != usuario.pk
    huella = a_distancia.huella_cierre(turno, contado)
    # La titular no necesita que otra persona le supervise el cierre: lo
    # confirma con su propia clave (DD-80).
    titular_sola = pide_supervisor and usuario.es_titular

    # Arqueo ciego con recuento (DD-81). Mientras no se muestre la
    # diferencia, quien cuenta sólo sabe si coincide o no, y puede volver a
    # contar. La diferencia se muestra cuando el conteo ya no se puede
    # cambiar: al segundo que no coincide, o si elige cerrar igual. A la
    # titular se le muestra siempre: es la dueña de la plata.
    if (not arqueo.get('revelado') and pide_supervisor and (
            usuario.es_titular
            or servicio_caja.conteos_seguidos(turno) >= servicio_caja.CONTEOS_A_CIEGAS
            or (request.method == 'POST' and request.POST.get('cerrar_igual')))):
        arqueo = _revelar(request, turno, arqueo)
        if request.method == 'POST' and request.POST.get('cerrar_igual'):
            return redirect('ventas:cierre')
    if arqueo.get('revelado'):
        etapa = 'REVELADA'
    elif pide_supervisor:
        etapa = 'NO_COINCIDE'
    else:
        etapa = 'COINCIDE'

    if request.method == 'POST' and etapa == 'NO_COINCIDE':
        return redirect('ventas:cierre')
    if request.method == 'POST':
        form = ConfirmacionCierreForm(request.POST, modos=modos)
        # Si el formulario no está bien, no se toca nada: ni el token de
        # idempotencia ni el turno.
        #
        # Antes se llamaba a ``form.is_valid()`` y se seguía de largo con
        # ``cleaned_data`` incompleto. Con una observación más larga de lo
        # que entra en la columna, el token se consumía igual, el cierre
        # fallaba y el segundo intento salía con «ese cierre ya se había
        # procesado»: el turno quedaba abierto, sin diferencia explicada y
        # sin forma de cerrarlo salvo volviendo a contar el cajón.
        if form.is_valid():
            datos = form.cleaned_data
            if etapa == 'COINCIDE' and diferencia != 0 and not datos.get('observacion'):
                datos['observacion'] = 'Diferencia dentro de lo tolerado'
            forma = datos.get('forma') or 'ACA'

            token = datos.get('token_operacion')
            if not idempotencia.consumir(request, token):
                messages.info(request, 'Ese cierre ya se había procesado.')
                return redirect('ventas:caja')
            antes = instantanea(turno)
            solicitud = entrante = None
            if relevo and _es_quien_opera(datos.get('entrante_usuario'), usuario):
                if abrio_otra_persona and ArqueoForm.AUSENCIA in modos:
                    # El turno era de otra persona y sigue quien está acá: es
                    # cerrar el turno anterior y abrir el propio, sin pedirle la
                    # clave a un tercero (DD-101).
                    modo, relevo, ausencia = ArqueoForm.AUSENCIA, False, True
            try:
                supervisor, a_revisar = None, False
                if titular_sola:
                    autorizar_con_clave(
                        request, usuario.nombre_usuario,
                        datos.get('autoriza_clave'), 'supervisar_cierre',
                        para='cerrar con esta diferencia')
                elif pide_supervisor and forma == 'REVISAR':
                    a_revisar = True
                elif pide_supervisor and forma != 'DISTANCIA':
                    supervisor = autorizar_con_clave(
                        request, datos.get('autoriza_usuario'),
                        datos.get('autoriza_clave'), 'supervisar_cierre',
                        distinto_de=usuario, para='cerrar con esta diferencia')
                if relevo and _es_quien_opera(datos.get('entrante_usuario'), usuario):
                    raise ValidationError(SIGUE_EL_MISMO)
                if relevo:
                    # Custodia: firma que recibió el cajón. No aprueba nada.
                    entrante = autorizar_con_clave(
                        request, datos.get('entrante_usuario'),
                        datos.get('entrante_clave'), 'abrir_caja',
                        distinto_de=usuario, para='recibir el cajón',
                        titular_se_confirma=False)
                with transaction.atomic():
                    if pide_supervisor and not titular_sola and forma == 'DISTANCIA':
                        solicitud = a_distancia.aprobada_para(
                            datos.get('remota'), usuario, huella, bloquear=True, turno=turno)
                        supervisor = solicitud.id_usuario_autorizante
                    if relevo or ausencia:
                        cerrado, nuevo = servicio_caja.relevar(
                            turno, usuario, contado, entrante or usuario,
                            datos.get('observacion'), supervisor=supervisor,
                            confirmo_titular=titular_sola, a_revisar=a_revisar,
                            en_ausencia=ausencia or (relevo and abrio_otra_persona))
                    else:
                        cerrado = servicio_caja.cerrar(
                            turno, usuario, contado, datos.get('observacion'),
                            supervisor=supervisor, confirmo_titular=titular_sola,
                            a_revisar=a_revisar)
                        nuevo = None
                    if solicitud is not None:
                        a_distancia.marcar_usada(solicitud)
                    # Lo que se pidió a distancia y no se usó ya no tiene a qué
                    # aplicarse: no queda esperando en la bandeja de nadie.
                    a_distancia.anular_pedidos_de_cierre(turno)
            except ValidationError as error:
                idempotencia.devolver(request, token)
                form.add_error(None, _texto(error))
            except IntegrityError as error:
                # Una regla de la base: se explica en la misma pantalla, sin
                # perder lo que se cargó (DD-100/101).
                from nucleo.errores import mensaje_de
                idempotencia.devolver(request, token)
                form.add_error(None, mensaje_de(error)[0])
            else:
                request.session.pop(CLAVE_ARQUEO, None)
                abrio = cerrado.id_usuario_apertura
                registrar_auditoria(
                    request, 'CIERRE', 'TURNO DE CAJA', instancia=cerrado,
                    anterior=antes,
                    nuevo={**instantanea(cerrado),
                           **({'turno_siguiente': instantanea(nuevo)} if nuevo else {})},
                    detalle=(f'Cerró {cerrado.id_caja_registradora}: contado {pesos(cerrado.monto_final)}, '
                             f'esperado {pesos(cerrado.monto_esperado)}, '
                             f'{servicio_caja.en_palabras(cerrado.diferencia_caja)}'
                             + (f'; aprobó la diferencia {supervisor}'
                                + (f' a distancia (pedido N° {solicitud.pk})' if solicitud else '')
                                if supervisor else '')
                             + ('; la titular lo confirmó con su clave, sin otra persona'
                                if titular_sola else '')
                             + ('; nadie aprobó la diferencia: queda pendiente de revisión'
                                if cerrado.requiere_revision else '')
                             + (f'; cerrado en relevo por {_nombre(usuario)} en ausencia de {_nombre(abrio)}'
                                if ausencia else '')
                             + (f'; sigue {nuevo.id_usuario_apertura} (turno {nuevo.pk})' if nuevo else '')
                             + '.'))
                if relevo:
                    caja = cerrado.id_caja_registradora
                    traspasar_sesion(
                        request, entrante,
                        detalle_salida=(f'Salió por cambio de turno: dejó {caja} a {_nombre(entrante)} '
                                        f'(turno N° {nuevo.pk}).'),
                        detalle_entrada=(f'Entró por cambio de turno: recibió {caja} de {_nombre(usuario)} '
                                         f'con {pesos(nuevo.monto_inicial)} de fondo (turno N° {nuevo.pk}).'))
                    messages.success(request, f'Turno relevado: ahora estás operando con la cuenta de '
                                              f'{_nombre(entrante)}.')
                elif ausencia:
                    messages.success(request, f'Cerraste el turno de {_nombre(abrio)} en su ausencia y abriste '
                                              f'el tuyo con {pesos(nuevo.monto_inicial)} de fondo.')
                else:
                    messages.success(request, f'{cerrado.id_caja_registradora} cerrada. ¡Gracias!')
                if cerrado.requiere_revision:
                    messages.warning(request, 'La diferencia quedó pendiente de revisión de la titular.')
                # El comprobante del turno que se acaba de cerrar.
                return redirect('ventas:recaudacion', pk=cerrado.pk)
    else:
        form = ConfirmacionCierreForm(modos=modos, initial={
            'token_operacion': idempotencia.emitir(request), 'modo': modo})
    # La titular ya aprobó este mismo conteo (lo pidió esta u otra persona):
    # se usa esa aprobación, no se pide otra (DD-101).
    aprobada = (a_distancia.ya_aprobada(turno, huella)
                if pide_supervisor and not titular_sola else None)
    if aprobada is not None and request.method != 'POST':
        form.initial.update({'forma': 'DISTANCIA', 'remota': aprobada.pk})
    # Un pedido que quedó esperando respuesta: al recargar la página se
    # RETOMA, en vez de empezar de cero y anularlo al volver a pedir (el PIN
    # que la titular ya dictó dejaba de servir).
    pendiente_remota = (a_distancia.pendiente_de_cierre(turno)
                        if aprobada is None and pide_supervisor and not titular_sola else None)
    if pendiente_remota is not None and request.method != 'POST':
        form.initial.update({'forma': 'DISTANCIA'})

    return render(request, 'ventas/cierre.html', {
        'turno': turno, 'contado': contado, 'esperado': esperado,
        'diferencia': diferencia, 'diferencia_abs': abs(diferencia), 'tolerancia': tolerancia,
        'pide_supervisor': pide_supervisor, 'relevo': relevo, 'ausencia': ausencia,
        'modo': modo, 'modos': modos, 'form': form, 'aprobada': aprobada,
        'pendiente_remota': pendiente_remota,
        'pendiente_restan': (a_distancia.estado(pendiente_remota)['restan']
                             if pendiente_remota is not None else 0),
        'abrio': turno.id_usuario_apertura, 'abrio_otra_persona': abrio_otra_persona,
        'titular_sola': titular_sola, 'etapa': etapa,
        'motivo_minimo': servicio_caja.MOTIVO_MINIMO,
        # De dónde sale lo que tiene que haber: se muestra recién cuando el
        # conteo ya coincidió o ya quedó fijo (el arqueo sigue siendo ciego).
        'composicion': ([(e, m) for e, m in servicio_caja.resumen(turno) if m]
                        if etapa in ('REVELADA', 'COINCIDE') else []),
    })


@require_POST
@requiere_permiso('cerrar_caja', redirigir_a='ventas:caja')
def pedir_autorizacion_cierre(request):
    """Opción A de DD-97: la diferencia la aprueba alguien que no está."""
    usuario = request.usuario_actual
    turno = puesto.turno_de(request)
    arqueo = _arqueo_pendiente(request, turno)
    if arqueo is None or not arqueo.get('revelado'):
        return JsonResponse({'ok': False, 'error': 'Primero terminá de contar: la diferencia tiene que estar a la vista.'})
    contado, esperado, diferencia, _tolerancia, pide_supervisor = _datos_del_cierre(request, turno, arqueo)
    if not pide_supervisor:
        return JsonResponse({'ok': False, 'error': 'Esta diferencia está dentro de lo tolerado: no hace falta pedir nada.'})
    base = settings.CAFETO_URL_PUBLICA or request.build_absolute_uri('/')
    try:
        pedido_modo = json_seguro.campo(json_seguro.del_pedido(request), 'modo', str, 'La forma de cerrar')
        modo = pedido_modo if pedido_modo in _modos_posibles(turno, usuario) else _modo(arqueo)
        solicitud, pin = a_distancia.pedir_cierre(turno, usuario, contado, esperado, modo, base)
    except ValidationError as error:
        return JsonResponse({'ok': False, 'error': _texto(error)},
                            status=400 if isinstance(error, json_seguro.PedidoMalArmado) else 200)
    if pin is None:
        # Ya estaba aprobado: no se pide de nuevo.
        return JsonResponse({'ok': True, 'id': solicitud.pk, **a_distancia.estado(solicitud),
                             'quienes': 0, 'avisos': [], 'ya_aprobada': True})
    registrar_auditoria(
        request, 'SOLICITUD', 'AUTORIZACION A DISTANCIA', instancia=solicitud, modulo='VENTAS',
        detalle=f'{solicitud.motivo_solicitud}. Pedido N° {solicitud.pk}, vence a las '
                f'{timezone.localtime(solicitud.fecha_vencimiento):%H:%M}.')
    personas = a_distancia.candidatos(solicitud, a_distancia.control_de(solicitud))
    return JsonResponse({
        'ok': True, 'id': solicitud.pk, **a_distancia.estado(solicitud),
        'quienes': len(personas),
        'avisos': [x for x in ('correo' if any(p.correo for p in personas) else '',
                               'WhatsApp' if settings.CAFETO_N8N_WEBHOOK_AUTORIZACION
                               and any(p.es_titular for p in personas) else '') if x],
    })


def _revelar(request, turno, arqueo):
    """Deja el conteo fijo y asentado, justo antes de mostrar la diferencia."""
    arqueo = {**arqueo, 'revelado': True}
    request.session[CLAVE_ARQUEO] = arqueo
    if servicio_caja.conteo_revelado(turno) is None:
        registrar_auditoria(
            request, 'ARQUEO REVELADO', 'TURNO DE CAJA', instancia=turno,
            anterior=instantanea(turno),
            nuevo={**instantanea(turno), 'arqueo_declarado': {
                'monto_contado': arqueo['contado'], 'relevo': arqueo['relevo'],
                'modo': _modo(arqueo)}},
            detalle=f'Se le mostró la diferencia del conteo de {pesos(Decimal(arqueo["contado"]))}: '
                    'desde ahora ese conteo no se cambia.')
    return arqueo


@require_POST
@requiere_permiso('cerrar_caja', redirigir_a='ventas:caja')
def descartar_arqueo(request):
    """Vuelve atrás del paso 2 SIN borrar lo declarado de la auditoría.

    Sirve si se apretó "cerrar" por error en medio del turno. Para cerrar
    hay que volver a contar, y el primer conteo ya quedó asentado.
    """
    turno = puesto.turno_de(request)
    pendiente = _arqueo_pendiente(request, turno)
    if pendiente and pendiente.get('revelado'):
        messages.error(request, 'Ya viste la diferencia de ese conteo: no se puede '
                                'volver a contar. Terminá el cierre.')
        return redirect('ventas:cierre')
    if pendiente:
        declarado = request.session.pop(CLAVE_ARQUEO, None) or {}
        registrar_auditoria(
            request, 'ARQUEO DESCARTADO', 'TURNO DE CAJA', instancia=turno,
            anterior={**instantanea(turno), 'arqueo_declarado': {
                'monto_contado': declarado.get('contado'),
                'relevo': declarado.get('relevo')}},
            nuevo=instantanea(turno),
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
    caja = id_valido(request.GET.get('caja'))
    if caja:
        turnos = turnos.filter(id_caja_registradora_id=caja)
    estado = request.GET.get('estado', '')
    if estado == 'con_diferencia':
        turnos = turnos.exclude(diferencia_caja=0).filter(diferencia_caja__isnull=False)
    elif estado == 'abiertos':
        turnos = turnos.filter(estado_turno='ABIERTA')
    elif estado == 'a_revisar':
        turnos = turnos.filter(requiere_revision=True, id_usuario_revision__isnull=True)
    elif estado == 'recontados':
        turnos = turnos.filter(pk__in=servicio_caja.turnos_con_reconteo(
            date(2000, 1, 1)).values('pk'))
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
            'id_usuario_supervisor', 'id_usuario_revision'), pk=pk)
    # Quien supervisa ve lo esperado de los cajones de OTROS. El de su
    # propio turno abierto, no: lo va a contar él, y el arqueo es ciego.
    propio_abierto = (turno.abierto
                      and turno.id_usuario_apertura_id == request.usuario_actual.pk)
    return render(request, 'ventas/_ficha_turno.html', {
        'turno': turno,
        'propio_abierto': propio_abierto,
        'conteos': [] if propio_abierto else servicio_caja.conteos_declarados(turno),
        'resumen': ([] if propio_abierto
                    else [(e, m) for e, m in servicio_caja.resumen(turno) if m]),
        'movimientos': turno.movimientos.select_related('id_usuario')
                       .order_by('fecha_hora', 'pk')[:200],
        'ventas': turno.ventas.aggregate(
            cantidad=Count('pk', filter=Q(estado_venta='COMPLETADA')),
            total=Sum('total_venta', filter=Q(estado_venta='COMPLETADA')),
            anuladas=Count('pk', filter=Q(estado_venta='ANULADA'))),
        'recaudacion': [] if propio_abierto else servicio_caja.recaudacion(turno),
        'cortes': servicio_caja.cortes_x(turno),
        'puede_revisar': (turno.pendiente_de_revision and servicio_caja.puede_revisar(request.usuario_actual)
                          and request.usuario_actual.pk != turno.id_usuario_cierre_id),
    })


@requiere_alguno('cerrar_caja', 'ver_caja', 'abrir_caja')
def recaudacion(request, pk):
    """El comprobante de cierre del turno (Z, o de relevo): quién abrió,
    quién cerró, quién recibió, quién aprobó la diferencia, el desglose del
    efectivo y lo informativo de los otros medios (DFD 6).

    Lo ve quien cerró ese turno (sale al terminar el cierre, para imprimir,
    firmar y guardar con la plata), quien lo recibió en el relevo (firma
    también) y quien supervisa la caja.
    """
    turno = get_object_or_404(
        TurnoCaja.objects.select_related(
            'id_caja_registradora', 'id_usuario_apertura', 'id_usuario_cierre',
            'id_usuario_supervisor', 'id_usuario_revision',
            'id_turno_anterior__id_usuario_apertura'), pk=pk)
    usuario = request.usuario_actual
    siguiente = turno.siguiente
    if not (usuario.puede('ver_caja') or turno.id_usuario_cierre_id == usuario.pk
            or (siguiente is not None and siguiente.id_usuario_apertura_id == usuario.pk)):
        registrar_acceso_denegado(request, 'ver_caja')
        messages.error(request, 'Ese turno lo cerró otra persona.')
        return redirect('ventas:caja')
    if turno.abierto:
        messages.info(request, 'El turno todavía está abierto: el comprobante de cierre '
                               'sale al cerrarlo. Para un control sin cerrar, hacé un arqueo parcial.')
        return redirect('ventas:caja')
    foto = servicio_caja.foto_del_cajon(turno)
    return render(request, 'ventas/recaudacion.html', {
        'turno': turno, 'tipo': servicio_caja.tipo_de_comprobante(turno),
        'desglose': servicio_caja.desglose_del_cajon(foto),
        'otros': servicio_caja.otros_medios(turno),
        'quienes': servicio_caja.responsables(turno),
        'conteos': servicio_caja.conteos_declarados(turno),
        'cortes': servicio_caja.cortes_x(turno),
        'negocio': ParametrosNegocio.vigentes(),
        'impreso': timezone.now(),
        'puede_revisar': turno.pendiente_de_revision and servicio_caja.puede_revisar(usuario)
                         and usuario.pk != turno.id_usuario_cierre_id,
    })


# ===========================================================================
#  ARQUEO PARCIAL (CONTROL SIN CERRAR)
# ===========================================================================
@require_POST
@requiere_permiso('cerrar_caja', redirigir_a='ventas:caja')
def corte_x(request):
    """Se cuenta el cajón sin cerrar el turno: un control a mitad del día.

    Lo contado y la foto del libro en ese instante quedan en el Registro de
    Actividad (no se pueden cambiar), así el comprobante reimpreso dice lo
    mismo que el original aunque después se siga vendiendo."""
    turno = puesto.turno_de(request)
    form = CorteXForm(request.POST)
    if turno is None:
        messages.error(request, 'La caja de este dispositivo no tiene un turno abierto.')
        return redirect('ventas:caja')
    if not form.is_valid():
        messages.error(request, _texto(ValidationError(
            [e for errores in form.errors.values() for e in errores])))
        return redirect('ventas:caja')
    if not idempotencia.consumir(request, form.cleaned_data['token_operacion']):
        messages.info(request, 'Ese arqueo parcial ya se había registrado.')
        return redirect('ventas:caja')
    contado = form.cleaned_data['monto_contado']
    foto = servicio_caja.foto_del_cajon(turno)
    otros = servicio_caja.otros_medios(turno)
    asiento = registrar_auditoria(
        request, 'ARQUEO PARCIAL', 'TURNO DE CAJA', instancia=turno,
        anterior=instantanea(turno),
        nuevo={**instantanea(turno), 'corte_x': {
            'monto_contado': str(contado),
            'observacion': (form.cleaned_data.get('observacion') or '').strip()[:120],
            'foto': servicio_caja.foto_serializable(foto),
            'medios': [{'medio': f['medio'], 'ventas': f['ventas'], 'total': str(f['total']),
                        'fiado': f['fiado']} for f in otros['medios']],
            'anuladas': {'cantidad': otros['anuladas']['cantidad'], 'monto': str(otros['anuladas']['monto'])},
            'cambios': {'cantidad': otros['cambios']['cantidad'],
                        'devuelto': str(otros['cambios']['devuelto']),
                        'cobrado': str(otros['cambios']['cobrado'])},
        }},
        detalle=f'Arqueo parcial (control sin cerrar) de {turno.id_caja_registradora}, turno N° {turno.pk}: '
                f'contó {pesos(contado)} sin cerrar el turno.')
    messages.success(request, 'Arqueo parcial registrado. El turno sigue abierto.')
    return redirect('ventas:comprobante_x', pk=asiento.pk)


@requiere_alguno('cerrar_caja', 'ver_caja')
def comprobante_x(request, pk):
    """El comprobante del arqueo parcial (control sin cerrar), tal como quedó en su momento.

    El arqueo sigue siendo ciego: lo esperado y la diferencia los ve quien
    supervisa la caja y no es quien la abrió, o cualquiera una vez cerrado
    el turno. A quien va a seguir vendiendo y cerrar le sale lo que contó,
    las cantidades y los otros medios."""
    from seguridad.models import LogAuditoria

    asiento = get_object_or_404(
        LogAuditoria.objects.select_related('id_usuario'),
        pk=pk, operacion_realizada=servicio_caja.OPERACION_CORTE_X,
        tabla_afectada=TurnoCaja._meta.db_table)
    turno = get_object_or_404(
        TurnoCaja.objects.select_related('id_caja_registradora', 'id_usuario_apertura'),
        pk=asiento.id_registro)
    usuario = request.usuario_actual
    if asiento.id_usuario_id != usuario.pk and not usuario.puede('ver_caja'):
        registrar_acceso_denegado(request, 'ver_caja')
        messages.error(request, 'Ese arqueo parcial lo hizo otra persona.')
        return redirect('ventas:caja')
    datos = (asiento.registro_nuevo or {}).get('corte_x') or {}
    foto = servicio_caja.foto_desde(datos.get('foto') or {})
    contado = Decimal(datos.get('monto_contado') or '0')
    ve_esperado = (not turno.abierto) or (
        usuario.puede('ver_caja') and turno.id_usuario_apertura_id != usuario.pk)
    return render(request, 'ventas/comprobante_x.html', {
        'turno': turno, 'asiento': asiento, 'contado': contado,
        'observacion': datos.get('observacion') or '',
        'desglose': servicio_caja.desglose_del_cajon(foto) if foto else [],
        'esperado': foto.get('esperado'),
        'diferencia': contado - foto['esperado'] if foto.get('esperado') is not None else None,
        'medios': [{**m, 'total': Decimal(m['total'])} for m in datos.get('medios') or []],
        'anuladas': datos.get('anuladas') or {},
        'cambios': datos.get('cambios') or {},
        've_esperado': ve_esperado,
        'negocio': ParametrosNegocio.vigentes(),
        'impreso': timezone.now(),
    })


# ===========================================================================
#  REVISIÓN DE UNA DIFERENCIA QUE NADIE APROBÓ (DD-97)
# ===========================================================================
@require_POST
@requiere_alguno('supervisar_cierre', 'ver_caja', redirigir_a='ventas:turnos')
def revisar_turno(request, pk):
    turno = get_object_or_404(TurnoCaja, pk=pk)
    antes = instantanea(turno)
    try:
        revisado = servicio_caja.revisar(turno, request.usuario_actual)
    except ValidationError as error:
        messages.error(request, _texto(error))
    else:
        registrar_auditoria(
            request, 'REVISION', 'TURNO DE CAJA', instancia=revisado, anterior=antes,
            nuevo=instantanea(revisado),
            detalle=f'Revisó la diferencia de {pesos(revisado.diferencia_caja)} del turno N° '
                    f'{revisado.pk} de {revisado.id_caja_registradora}, que cerró '
                    f'{revisado.id_usuario_cierre} sin aprobación. Motivo que dejó: '
                    f'{revisado.observacion_diferencia}.')
        messages.success(request, f'Diferencia del turno N° {revisado.pk} marcada como revisada.')
    destino = request.POST.get('volver') or ''
    if destino == 'comprobante':
        return redirect('ventas:recaudacion', pk=pk)
    return redirect(f"{reverse('ventas:turnos')}?estado=a_revisar")


# ===========================================================================
#  VALE DE MOVIMIENTO DE CAJA (refuerzo, retiro, gasto)
# ===========================================================================
@requiere_alguno(*PERMISOS_DEL_PUESTO, 'registrar_gasto_caja', 'registrar_egreso')
def vale_movimiento(request, pk):
    """El papel que se deja en el cajón cuando entra o sale plata sin venta."""
    movimiento = get_object_or_404(
        MovimientoCaja.objects.select_related(
            'id_usuario', 'id_turno_caja__id_caja_registradora',
            'id_egreso__id_proveedor', 'id_egreso__id_tipo_egreso'),
        pk=pk, tipo_movimiento__in=(TipoMovimientoCaja.REFUERZO, TipoMovimientoCaja.RETIRO,
                                    TipoMovimientoCaja.GASTO))
    usuario = request.usuario_actual
    if movimiento.id_usuario_id != usuario.pk and not usuario.puede('ver_caja'):
        registrar_acceso_denegado(request, 'ver_caja')
        messages.error(request, 'Ese movimiento lo registró otra persona.')
        return redirect('ventas:caja')
    return render(request, 'ventas/vale_movimiento.html', {
        'm': movimiento, 'egreso': movimiento.id_egreso,
        'monto': abs(movimiento.monto),
        'titulo': {TipoMovimientoCaja.REFUERZO: 'VALE DE INGRESO DE EFECTIVO',
                   TipoMovimientoCaja.RETIRO: 'VALE DE RETIRO DE EFECTIVO',
                   TipoMovimientoCaja.GASTO: 'VALE DE GASTO PAGADO POR CAJA'}[movimiento.tipo_movimiento],
        'negocio': ParametrosNegocio.vigentes(),
        'impreso': timezone.now(),
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
