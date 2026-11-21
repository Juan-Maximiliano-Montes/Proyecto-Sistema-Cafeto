"""Autorización a distancia (DD-94): lo que pide la caja, la bandeja de la
dueña y la página del enlace que le llega al celular."""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from nucleo.templatetags.cafeto import pesos
from seguridad.decorators import requiere_alguno, requiere_permiso
from seguridad.servicios import instantanea, registrar_auditoria
from ventas import a_distancia, puesto
from ventas import servicios as ventas
from ventas.models import EstadoSolicitud, SolicitudAutorizacion, ViaAprobacion
from ventas.views.mostrador import _fidelizacion, _leer_pedido, _producto_para_ajuste, _vence_fiado, leer_datos
from nucleo.validaciones import id_valido
from nucleo import json_seguro
from seguridad.servicios import sesion_del_navegador

VISTAS_BANDEJA = (('pendientes', 'Esperando respuesta'), ('aprobadas', 'Aprobadas'),
                  ('rechazadas', 'Rechazadas'), ('anuladas', 'Vencidas o anuladas'), ('todas', 'Todas'))
FILTROS = {
    'pendientes': {'estado_solicitud': EstadoSolicitud.PENDIENTE},
    'aprobadas': {'estado_solicitud__in': (EstadoSolicitud.APROBADA, EstadoSolicitud.USADA)},
    'rechazadas': {'estado_solicitud': EstadoSolicitud.RECHAZADA},
    'anuladas': {'estado_solicitud': EstadoSolicitud.ANULADA},
    'todas': {},
}

# Lo que no se guarda del ticket: credenciales y el identificador del cobro.
NO_SE_GUARDA = ('autoriza', 'remota', 'token')


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _puede_autorizar(solicitud):
    """Vuelve a cotizar el ticket guardado con ``usuario`` como autorizante:
    si no le alcanza (su tope, no maneja el fiado) o el ticket ya no se
    puede vender (se acabó el stock), lo dice antes de aprobar."""
    def control(usuario):
        if usuario is None:
            raise ValidationError('No hay una titular activa en el sistema.')
        items, pagos, datos = leer_datos(dict(solicitud.pedido))
        cliente, beneficios = _fidelizacion(datos)
        ventas.cotizar(items, pagos, operador=solicitud.id_usuario_solicitante,
                       autorizante=usuario, cliente=cliente, beneficios=beneficios)
    return control


def _resumen(solicitud):
    tipo = (solicitud.pedido or {}).get('tipo')
    return {
        'id': solicitud.pk, 'motivo': solicitud.motivo_solicitud, 'es_ajuste': tipo == a_distancia.AJUSTE,
        'es_cierre': tipo == a_distancia.CIERRE,
        'total': pesos(solicitud.monto_solicitud),
        'caja': str(solicitud.id_turno_caja.id_caja_registradora),
        'pidio': f'{solicitud.id_usuario_solicitante.nombre} {solicitud.id_usuario_solicitante.apellido}'.strip(),
        'cuando': timezone.localtime(solicitud.fecha_solicitud),
        'vence': timezone.localtime(solicitud.fecha_vencimiento),
        'renglones': solicitud.pedido.get('resumen') or [],
    }


# ---------------------------------------------------------------------------
#  La caja
# ---------------------------------------------------------------------------
@require_POST
@requiere_permiso('vender', redirigir_a='ventas:caja')
def pedir(request):
    turno = puesto.turno_de(request)
    if turno is None:
        return JsonResponse({'ok': False, 'error': 'Abrí la caja para pedir una autorización.'})
    base = settings.CAFETO_URL_PUBLICA or request.build_absolute_uri('/')
    try:
        crudo = json_seguro.del_pedido(request)
        if json_seguro.campo(crudo, 'tipo', str, 'El tipo de pedido') == a_distancia.AJUSTE:
            # El ajuste de stock desde el mostrador (DD-90).
            producto, cantidad = _producto_para_ajuste(crudo)
            pedido = {'producto': producto.pk, 'cantidad': format(cantidad.normalize(), 'f'),
                      'resumen': [f'{producto.nombre_producto}: de 0 a {producto.cantidad_con_unidad(cantidad)}']}
            solicitud, pin = a_distancia.pedir_ajuste(turno, request.usuario_actual, producto, cantidad,
                                                       pedido, base)
        else:
            items, pagos, datos = _leer_pedido(request)
            if not items:
                raise ValidationError('El ticket está vacío.')
            cliente, beneficios = _fidelizacion(datos)
            pedido = {k: v for k, v in datos.items() if k not in NO_SE_GUARDA}
            pedido['resumen'] = [f'{i.producto.nombre_producto} × {format(i.cantidad.normalize(), "f")}'
                                 for i in items]
            solicitud, pin = a_distancia.pedir(
                turno, request.usuario_actual, items, pagos, cliente, beneficios, _vence_fiado(datos),
                pedido, base)
    except ValidationError as error:
        return JsonResponse({'ok': False, 'error': _texto(error)},
                            status=400 if isinstance(error, json_seguro.PedidoMalArmado) else 200)
    if pin is not None:      # None: ya estaba aprobado y se reutiliza (DD-101)
        registrar_auditoria(
            request, 'SOLICITUD', 'AUTORIZACION A DISTANCIA', instancia=solicitud, modulo='VENTAS',
            detalle=f'{solicitud.motivo_solicitud}. Total del ticket {pesos(solicitud.monto_solicitud)}. '
                    f'Pedido N° {solicitud.pk}, vence a las {timezone.localtime(solicitud.fecha_vencimiento):%H:%M}.')
    personas = a_distancia.candidatos(solicitud, a_distancia.control_de(solicitud))
    return JsonResponse({
        'ok': True, 'id': solicitud.pk, **a_distancia.estado(solicitud),
        'motivo': solicitud.motivo_solicitud,
        'quienes': len(personas),
        'avisos': [x for x in ('correo' if any(p.correo for p in personas) else '',
                               'WhatsApp' if settings.CAFETO_N8N_WEBHOOK_AUTORIZACION
                               and any(p.es_titular for p in personas) else '') if x],
    })


def _propia(request, pk):
    """El pedido de quien opera, o uno de CIERRE del turno de esta caja (lo
    pudo pedir otra persona que después dejó el cierre a medias, DD-101)."""
    solicitud = get_object_or_404(SolicitudAutorizacion.objects.select_related('id_usuario_autorizante'), pk=pk)
    if not a_distancia.es_de(solicitud, request.usuario_actual, puesto.turno_de(request)):
        raise Http404
    return solicitud


@require_GET
@requiere_alguno('vender', 'cerrar_caja')
def estado(request, pk):
    return JsonResponse({'ok': True, **a_distancia.estado(_propia(request, pk))})


@require_POST
@requiere_alguno('vender', 'cerrar_caja')
def pin(request, pk):
    solicitud = _propia(request, pk)
    antes = instantanea(solicitud)
    try:
        datos = json_seguro.del_pedido(request)
        pin_escrito = json_seguro.campo(datos, 'pin', (str, int), 'El PIN')
    except json_seguro.PedidoMalArmado as e:
        return JsonResponse({'ok': False, 'error': _texto(e), **a_distancia.estado(solicitud)}, status=400)
    try:
        ya_estaba = solicitud.estado_solicitud == EstadoSolicitud.APROBADA
        aprobada, error = a_distancia.verificar_pin(
            solicitud, request.usuario_actual, None if pin_escrito is None else str(pin_escrito),
            a_distancia.control_de(solicitud),
            turno=puesto.turno_de(request))
    except ValidationError as e:
        return JsonResponse({'ok': False, 'error': _texto(e), **a_distancia.estado(solicitud)})
    if error:
        solicitud.refresh_from_db()
        registrar_auditoria(
            request, 'PIN INCORRECTO', 'AUTORIZACION A DISTANCIA', instancia=solicitud, modulo='VENTAS',
            resultado='RECHAZADA',
            detalle=f'Pedido N° {solicitud.pk}: PIN incorrecto ({solicitud.intentos_pin} de '
                    f'{SolicitudAutorizacion.INTENTOS_DE_PIN} intentos).')
        return JsonResponse({'ok': False, 'error': error, **a_distancia.estado(solicitud)})
    if ya_estaba:
        return JsonResponse({'ok': True, **a_distancia.estado(aprobada)})
    registrar_auditoria(
        request, 'APROBACION', 'AUTORIZACION A DISTANCIA', instancia=aprobada, modulo='VENTAS',
        anterior=antes, nuevo=instantanea(aprobada),
        detalle=f'Pedido N° {aprobada.pk} aprobado por {aprobada.id_usuario_autorizante} con el PIN '
                f'que le dictó a la caja: {aprobada.motivo_solicitud}.')
    return JsonResponse({'ok': True, **a_distancia.estado(aprobada)})


@require_POST
@requiere_alguno('vender', 'cerrar_caja')
def cancelar(request, pk):
    solicitud = _propia(request, pk)
    (SolicitudAutorizacion.objects.filter(pk=solicitud.pk, estado_solicitud__in=(
        EstadoSolicitud.PENDIENTE, EstadoSolicitud.APROBADA))
     .update(estado_solicitud=EstadoSolicitud.ANULADA, fecha_resolucion=timezone.now(),
             id_usuario_autorizante=None, via_aprobacion=None))
    return JsonResponse({'ok': True})


# ---------------------------------------------------------------------------
#  La dueña: bandeja en el sistema y enlace del celular
# ---------------------------------------------------------------------------
def _contestar(request, solicitud, usuario, aprobar, via):
    """Aprueba o rechaza y lo deja en el Registro de Actividad."""
    antes = instantanea(solicitud)
    resuelta = a_distancia.resolver(solicitud, usuario, aprobar, via, a_distancia.control_de(solicitud))
    registrar_auditoria(
        request, 'APROBACION' if aprobar else 'RECHAZO', 'AUTORIZACION A DISTANCIA',
        instancia=resuelta, modulo='VENTAS', usuario=usuario,
        anterior=antes, nuevo=instantanea(resuelta),
        detalle=(f'Pedido N° {resuelta.pk} {"aprobado" if aprobar else "rechazado"} por {usuario} '
                 f'({ViaAprobacion(via).label.lower()}): {resuelta.motivo_solicitud}.'))
    return resuelta


@requiere_alguno(*a_distancia.PERMISOS_PARA_AUTORIZAR, redirigir_a='home')
def bandeja(request):
    if request.method == 'POST':
        solicitud = get_object_or_404(SolicitudAutorizacion, pk=id_valido(request.POST.get('solicitud')))
        aprobar = request.POST.get('accion') == 'aprobar'
        try:
            _contestar(request, solicitud, request.usuario_actual, aprobar, ViaAprobacion.BANDEJA)
        except ValidationError as error:
            messages.error(request, _texto(error))
        else:
            messages.success(request, f'Aprobado: la caja ya puede {a_distancia.que_sigue(solicitud)}.'
                             if aprobar else 'Rechazado: la caja ya lo ve.')
        return redirect('ventas:autorizaciones')
    a_distancia.vencer_las_viejas()
    ver = request.GET.get('ver')
    if ver not in dict(VISTAS_BANDEJA):
        ver = 'pendientes'
    todas = SolicitudAutorizacion.objects.select_related('id_usuario_solicitante', 'id_usuario_autorizante', 'id_venta')
    pendientes = [_resumen(s) for s in a_distancia.pendientes()]
    cantidades = {clave: (len(pendientes) if clave == 'pendientes' else todas.filter(**FILTROS[clave]).count())
                  for clave, _ in VISTAS_BANDEJA}
    return render(request, 'ventas/autorizaciones.html', {
        'ver': ver, 'pendientes': pendientes,
        'recientes': (todas.filter(**FILTROS[ver]).order_by('-fecha_solicitud')[:50]
                      if ver != 'pendientes' else todas.exclude(estado_solicitud=EstadoSolicitud.PENDIENTE)
                      .order_by('-fecha_solicitud')[:15]),
        'vistas': [{'clave': clave, 'etiqueta': etiqueta, 'cantidad': cantidades[clave], 'activa': clave == ver,
                    'url': f'?ver={clave}'} for clave, etiqueta in VISTAS_BANDEJA],
    })


def enlace(request, token):
    """Lo que abre la dueña desde el correo o el WhatsApp. Sin sesión: el
    enlace es la credencial, y vale sólo para esa solicitud y 10 minutos.
    Abrirlo no aprueba nada; hay que tocar el botón.

    Si el navegador tiene abierta la sesión de quien aprueba, o ninguna,
    alcanza con el botón (y esa sesión sigue: la caja no se traba). Si es
    la de OTRA persona, se avisa y se pide la clave o el PIN de quien
    aprueba antes de aceptar la respuesta."""
    solicitud, persona = a_distancia.por_token(token)
    if solicitud is None or persona is None:
        return render(request, 'ventas/autorizar_a_distancia.html', {'invalido': True}, status=404)
    a_distancia.estado(solicitud)          # la da por vencida si pasó la hora
    solicitud.refresh_from_db()
    abierta = sesion_del_navegador(request)
    otra = abierta.id_usuario if abierta is not None and abierta.id_usuario_id != persona.pk else None
    hecho = None
    if request.method == 'POST':
        aprobar = request.POST.get('accion') == 'aprobar'
        error = None
        if otra is not None and solicitud.estado_solicitud == EstadoSolicitud.PENDIENTE:
            error = a_distancia.confirmar_identidad(request, solicitud, persona, request.POST.get('confirmacion'))
        if error:
            messages.error(request, error)
            solicitud.refresh_from_db()
        else:
            try:
                solicitud = _contestar(request, solicitud, persona, aprobar, ViaAprobacion.ENLACE)
                hecho = 'aprobado' if aprobar else 'rechazado'
            except ValidationError as error:
                messages.error(request, _texto(error))
    return render(request, 'ventas/autorizar_a_distancia.html', {
        'solicitud': solicitud, 'resumen': _resumen(solicitud), 'hecho': hecho,
        'pendiente': solicitud.estado_solicitud == EstadoSolicitud.PENDIENTE, 'persona': persona,
        'otra': otra, 'sigue': a_distancia.que_sigue(solicitud),
    })
