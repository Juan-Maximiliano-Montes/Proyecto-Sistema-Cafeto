"""Consultas de ventas: los tickets emitidos, para quien supervisa.

Una venta no se edita. Lo que vuelve después se registra como un cambio
(``ventas.cambios``), que queda colgado del ticket original. Una venta que
no debió existir se ANULA desde su ficha, con el turno todavía abierto y
por quien tiene el permiso (``ventas.anulacion``): queda en la lista,
marcada, con quién, cuándo y por qué.
"""
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from nucleo import idempotencia
from nucleo.exportar import ColumnaExportable, exportar
from nucleo.paginacion import TAMANIOS_TABLA, paginar
from nucleo.templatetags.cafeto import pesos
from seguridad.decorators import requiere_alguno, requiere_permiso
from seguridad.models import Usuario
from seguridad.servicios import (
    autorizar_con_clave,
    instantanea,
    registrar_auditoria,
    registrar_rechazo,
)
from negocio import ganancia
from ventas import anulacion
from ventas.models import (
    CajaRegistradora,
    EstadoVenta,
    MedioPago,
    MotivoAnulacion,
    Venta,
)


def _fecha(texto):
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


def ventas_filtradas(request):
    """Las ventas con los filtros de la URL. Sin fechas, las de hoy."""
    filtro = request.GET
    ventas = (Venta.objects
              .select_related('id_usuario', 'id_turno_caja__id_caja_registradora')
              .annotate(renglones=Count('detalles', distinct=True))
              .order_by('-fecha_venta', '-hora_venta', '-pk'))
    numero = filtro.get('numero', '').strip()
    if numero.isdigit():
        return ventas.filter(pk=int(numero))
    desde, hasta = _fecha(filtro.get('desde')), _fecha(filtro.get('hasta'))
    if desde is None and hasta is None:
        desde = hasta = timezone.localdate()
    if desde:
        ventas = ventas.filter(fecha_venta__gte=desde)
    if hasta:
        ventas = ventas.filter(fecha_venta__lte=hasta)
    if filtro.get('caja', '').isdigit():
        ventas = ventas.filter(id_turno_caja__id_caja_registradora_id=filtro['caja'])
    if filtro.get('vendedor', '').isdigit():
        ventas = ventas.filter(id_usuario_id=filtro['vendedor'])
    if filtro.get('medio', '').isdigit():
        ventas = ventas.filter(pagos__id_medio_pago_id=filtro['medio']).distinct()
    if filtro.get('con_cambios') == '1':
        ventas = ventas.filter(cambios__isnull=False).distinct()
    if filtro.get('estado') in EstadoVenta.values:
        ventas = ventas.filter(estado_venta=filtro['estado'])
    return ventas


@requiere_permiso('ver_ventas', redirigir_a='home')
def ventas(request):
    consulta = ventas_filtradas(request)
    # Los totales son de lo vendido: las anuladas se listan (si el filtro
    # las incluye) pero no suman.
    completadas = Q(estado_venta=EstadoVenta.COMPLETADA)
    totales = Venta.objects.filter(pk__in=consulta.values('pk')).aggregate(
        cantidad=Count('pk', filter=completadas),
        total=Sum('total_venta', filter=completadas),
        anuladas=Count('pk', filter=~completadas))
    contexto = paginar(request, consulta, TAMANIOS_TABLA, por_defecto=25)
    hoy = timezone.localdate().isoformat()
    contexto.update({
        'filtro': request.GET,
        'desde': request.GET.get('desde') or (hoy if not request.GET.get('hasta') else ''),
        'hasta': request.GET.get('hasta') or (hoy if not request.GET.get('desde') else ''),
        'cajas': CajaRegistradora.objects.all(),
        'medios': MedioPago.objects.all(),
        'estados': EstadoVenta.choices,
        'anuladas': totales['anuladas'] or 0,
        'vendedores': Usuario.objects.filter(ventas__isnull=False).distinct()
                                     .order_by('apellido', 'nombre'),
        'cantidad': totales['cantidad'] or 0,
        'facturado': totales['total'] or Decimal('0'),
        'promedio': ((totales['total'] or Decimal('0')) / totales['cantidad']
                     if totales['cantidad'] else None),
        'periodos': [(etiqueta, d.isoformat(), h.isoformat())
                     for etiqueta, d, h in ganancia.periodos(timezone.localdate())],
    })
    # La ganancia deja ver los costos: es de quien mira los reportes, no de
    # cualquiera que consulte ventas.
    if request.usuario_actual.tiene_permiso('ver_reportes'):
        contexto['ganancia'] = ganancia.de_ventas(Venta.objects.filter(pk__in=consulta.values('pk')))
    return render(request, 'ventas/ventas.html', contexto)


@requiere_permiso('ver_ventas', redirigir_a='home')
def ficha_venta(request, pk):
    venta = get_object_or_404(
        Venta.objects.select_related('id_usuario',
                                     'id_turno_caja__id_caja_registradora',
                                     'id_usuario_anulacion',
                                     'id_usuario_solicitante'),
        pk=pk)
    contexto = {
        'venta': venta,
        'motivo_anulacion': anulacion.motivo_de(venta),
        'renglones': venta.detalles.select_related('id_usuario_autorizante'),
        'pagos': venta.pagos.select_related('id_medio_pago'),
        'cambios': venta.cambios.select_related('id_usuario', 'id_medio_pago',
                                                'id_usuario_autorizante')
                                .prefetch_related('detalles'),
    }
    contexto.update(contexto_anulacion(request, venta))
    return render(request, 'ventas/_ficha_venta.html', contexto)


def contexto_anulacion(request, venta):
    """Lo que necesita el formulario de anulación, en Ventas y en Cambios.

    ``pide_clave``: quien opera no alcanza para autorizarla sola (no tiene
    el permiso, o la venta es suya), así que el formulario pide el usuario
    y la clave de otra persona.
    """
    if venta.anulada:
        return {}
    razon = anulacion.por_que_no(venta)
    if razon:
        return {'no_se_anula': razon}
    return {
        'token_anulacion': idempotencia.emitir(request),
        'motivos_anulacion': MotivoAnulacion.choices,
        'pide_clave': not anulacion.puede_autorizar(request.usuario_actual, venta),
        # La encargada que vendió: tiene el permiso, pero no para esta venta.
        'venta_propia': (venta.id_usuario_id == request.usuario_actual.pk
                         and request.usuario_actual.puede(anulacion.PERMISO)),
        # El titular sí puede, confirmando con su clave (DD-69).
        'titular_propia': (anulacion.es_titular_sobre_su_venta(
            request.usuario_actual, venta)
            and request.usuario_actual.puede(anulacion.PERMISO)),
    }


@require_POST
@requiere_alguno('ver_ventas', 'registrar_cambio')
def anular_venta(request, pk):
    """Anula la venta y vuelve a la pantalla de donde se pidió.

    La pide quien opera (el cajero desde Cambios, la encargada desde
    Ventas). Si no le alcanza para autorizarla sola, la autoriza otra
    persona con su usuario y su clave en este mismo equipo (DD-61).
    """
    venta = get_object_or_404(Venta, pk=pk)
    solicitante = request.usuario_actual
    if request.POST.get('desde') == 'cambios':
        volver = redirect(f"{reverse('ventas:cambios')}?venta={venta.pk}")
    else:
        volver = redirect(f"{reverse('ventas:ventas')}?numero={venta.pk}")
    token = request.POST.get('token_operacion')
    if not idempotencia.consumir(request, token):
        messages.info(request, f'La anulación de la venta N° {venta.pk} ya se '
                               'había procesado.')
        return volver
    antes = instantanea(venta)
    try:
        autorizante = None
        if not anulacion.puede_autorizar(solicitante, venta):
            # Si la clave no va, el rechazo lo asienta autorizar_con_clave.
            # El titular sobre su propia venta se confirma a sí mismo con
            # su clave (DD-69); cualquier otro necesita a otra persona.
            titular = anulacion.es_titular_sobre_su_venta(solicitante, venta)
            autorizante = autorizar_con_clave(
                request, request.POST.get('autoriza_usuario'),
                request.POST.get('autoriza_clave'), anulacion.PERMISO,
                distinto_de=None if titular else solicitante,
                para=f'anular la venta N° {venta.pk}')
        resultado = anulacion.anular(
            venta, solicitante, request.POST.get('motivo'),
            request.POST.get('detalle'), autorizante=autorizante)
    except anulacion.AnulacionRechazada as error:
        # Chocó con un control (DD-62): queda en el Registro de Actividad.
        idempotencia.devolver(request, token)
        texto = ' '.join(error.messages)
        registrar_rechazo(
            request, 'ANULACION', 'VENTA',
            f'Se pidió anular la venta N° {venta.pk} de '
            f'{pesos(venta.total_venta)} (pidió {solicitante.nombre} '
            f'{solicitante.apellido}'
            + (f', autorizaba {autorizante.nombre} {autorizante.apellido}'
               if autorizante else '')
            + f') y se rechazó: {texto}', instancia=venta)
        messages.error(request, texto)
        return volver
    except ValidationError as error:
        idempotencia.devolver(request, token)
        messages.error(request, ' '.join(error.messages))
        return volver
    venta = resultado.venta
    detalle = (f'Anuló la venta N° {venta.pk} de {pesos(venta.total_venta)} '
               f'({anulacion.motivo_de(venta)}). Vendió {venta.id_usuario}; '
               f'pidió {venta.id_usuario_solicitante}; autorizó '
               f'{venta.id_usuario_anulacion}.')
    if resultado.resumen:
        detalle += f' Se revirtió: {resultado.resumen}.'
    if anulacion.es_titular_sobre_su_venta(venta.id_usuario_anulacion, venta):
        detalle += (' El titular autorizó la anulación de su propia venta, '
                    'confirmando con su clave (DD-69).')
    registrar_auditoria(
        request, 'ANULACION', 'VENTA', instancia=venta, anterior=antes,
        nuevo=instantanea(venta), detalle=detalle)
    aviso = f'Venta N° {venta.pk} anulada.'
    if resultado.efectivo:
        aviso += f' Devolvé {pesos(resultado.efectivo)} en efectivo al cliente.'
    if resultado.medios_externos:
        aviso += (' Lo cobrado con ' + ', '.join(resultado.medios_externos)
                  + ' se revierte en el posnet o el banco.')
    messages.success(request, aviso)
    return volver


@requiere_permiso('ver_ventas', redirigir_a='home')
def exportar_ventas(request):
    columnas = [
        ColumnaExportable('N°', 'numero'), ColumnaExportable('Fecha', 'fecha'),
        ColumnaExportable('Hora'), ColumnaExportable('Caja'),
        ColumnaExportable('Vendió'), ColumnaExportable('Renglones', 'numero'),
        ColumnaExportable('Subtotal', 'dinero'), ColumnaExportable('Recargo', 'dinero'),
        ColumnaExportable('Redondeo', 'dinero'), ColumnaExportable('Total', 'dinero'),
        ColumnaExportable('Estado'), ColumnaExportable('Motivo de anulación'),
    ]
    filas = ([v.pk, v.fecha_venta, v.hora_venta.strftime('%H:%M'),
              str(v.id_turno_caja.id_caja_registradora), str(v.id_usuario),
              v.renglones, v.subtotal, v.monto_recargo, v.ajuste_redondeo,
              v.total_venta, v.get_estado_venta_display(),
              anulacion.motivo_de(v)]
             for v in ventas_filtradas(request).iterator(chunk_size=500))
    formato = request.GET.get('formato')
    respuesta = exportar(formato, 'Ventas', columnas, filas)
    registrar_auditoria(request, 'EXPORTACION', 'VENTAS',
                        detalle='Exportó el listado de ventas.', modulo='VENTAS')
    return respuesta


@requiere_permiso('ver_reportes', redirigir_a='home')
def exportar_renglones(request):
    """Los renglones vendidos con su ganancia, uno por fila: la planilla que
    la dueña sacaba del sistema anterior para mirar el día."""
    columnas = [
        ColumnaExportable('Fecha', 'fecha'), ColumnaExportable('Hora'),
        ColumnaExportable('Ticket', 'numero'), ColumnaExportable('Caja'),
        ColumnaExportable('Código'), ColumnaExportable('Descripción'), ColumnaExportable('Rubro'),
        ColumnaExportable('Cantidad', 'numero'), ColumnaExportable('Unitario', 'dinero'),
        ColumnaExportable('Subtotal', 'dinero'), ColumnaExportable('Costo', 'dinero'),
        ColumnaExportable('% Margen', 'numero'), ColumnaExportable('Ganancia', 'dinero'),
    ]

    def filas():
        consulta = Venta.objects.filter(pk__in=ventas_filtradas(request).values('pk'))
        for r in ganancia.renglones(consulta).iterator(chunk_size=500):
            costo = (r.cantidad * r.costo_unitario).quantize(Decimal('0.01'))
            utilidad = r.subtotal - costo
            producto = r.id_producto
            yield [r.id_venta.fecha_venta, r.id_venta.hora_venta.strftime('%H:%M'), r.id_venta_id,
                   str(r.id_venta.id_turno_caja.id_caja_registradora),
                   (producto.codigo_barras or '') if producto else '', r.descripcion_item,
                   str(producto.id_rubro) if producto else '', r.cantidad, r.precio_unitario,
                   r.subtotal, costo,
                   (utilidad / costo * 100).quantize(Decimal('0.1')) if costo else '', utilidad]

    respuesta = exportar('xlsx', 'Renglones con ganancia', columnas, filas())
    registrar_auditoria(request, 'EXPORTACION', 'VENTAS', modulo='VENTAS',
                        detalle='Exportó los renglones vendidos con su ganancia.')
    return respuesta
