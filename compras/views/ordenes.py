"""Órdenes de compra: el pedido al proveedor, antes de que llegue.

PENDIENTE (borrador) → EMITIDA (se mandó) → RECEPCIONADA (llegó, la cierra
la recepción) o CANCELADA. El PDF es lo que se le manda al proveedor.
"""
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST

from compras import servicios
from compras.models import OrdenCompra, Proveedor
from negocio.models import ParametrosNegocio
from nucleo import idempotencia
from nucleo.formularios import leer_numero
from nucleo.paginacion import TAMANIOS_TABLA, paginar
from seguridad.decorators import requiere_permiso
from seguridad.servicios import foto, instantanea, registrar_auditoria
from stock.models import Producto
from nucleo.templatetags.cafeto import pesos


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


@requiere_permiso('gestionar_ordenes', redirigir_a='home')
def ordenes(request):
    consulta = (OrdenCompra.objects.select_related('id_proveedor', 'id_usuario')
                .order_by('-fecha_orden', '-pk'))
    estado = request.GET.get('estado', '')
    if estado in dict(OrdenCompra.ESTADOS):
        consulta = consulta.filter(estado_orden=estado)
    contexto = paginar(request, consulta)
    contexto.update({'estado': estado, 'estados': OrdenCompra.ESTADOS})
    return render(request, 'compras/ordenes.html', contexto)


@requiere_permiso('gestionar_ordenes', redirigir_a='home')
def nueva_orden(request):
    proveedores = Proveedor.objects.filter(borrado_logico=False).exclude(
        tipo_proveedor='SERVICIOS').order_by('nombre_proveedor')
    contexto = {'proveedores': proveedores, 'datos': request.POST,
                'renglones_json': request.POST.get('renglones') or '[]',
                'catalogo': reverse('stock:buscar_productos') + '?tipos=REVENTA,INSUMO'}
    if request.method == 'POST':
        token = request.POST.get('token_operacion')
        try:
            proveedor = proveedores.filter(pk=request.POST.get('proveedor') or 0).first()
            if proveedor is None:
                raise ValidationError('Elegí a qué proveedor se le pide.')
            try:
                crudos = json.loads(request.POST.get('renglones') or '[]')
            except ValueError:
                raise ValidationError('Los renglones llegaron mal armados.')
            productos = Producto.objects.in_bulk(
                [c.get('producto') for c in crudos if isinstance(c, dict)])
            items = []
            for crudo in crudos:
                producto = productos.get(crudo.get('producto'))
                cantidad = leer_numero(crudo.get('cantidad'), 3)
                precio = leer_numero(crudo.get('costo') or '0', 2)
                if producto is None or cantidad is None or precio is None or precio < 0:
                    raise ValidationError('Revisá las cantidades y los precios de los renglones.')
                items.append({'producto': producto, 'cantidad': cantidad,
                              'precio_unitario': precio})
            if not idempotencia.consumir(request, token):
                return redirect('compras:ordenes')
            orden = servicios.emitir_orden(proveedor, request.usuario_actual, items)
        except ValidationError as error:
            idempotencia.devolver(request, token)
            contexto['error'] = _texto(error)
        else:
            registrar_auditoria(request, 'ALTA', 'ORDEN DE COMPRA', instancia=orden,
                                nuevo=foto(orden, renglones=orden.detalles.all()),
                                detalle=f'Orden N° {orden.pk} a {proveedor} por {pesos(orden.total_orden)}.')
            messages.success(request, f'Orden N° {orden.pk} guardada como borrador.')
            return redirect('compras:orden', pk=orden.pk)
    contexto['token'] = request.POST.get('token_operacion') or idempotencia.emitir(request)
    return render(request, 'compras/nueva_orden.html', contexto)


@requiere_permiso('gestionar_ordenes', redirigir_a='home')
def orden(request, pk):
    orden = get_object_or_404(OrdenCompra.objects.select_related('id_proveedor', 'id_usuario'), pk=pk)
    return render(request, 'compras/orden.html', {
        'orden': orden,
        'diferencias': (orden.diferencias()
                        if orden.estado_orden in ('CON_DIFERENCIAS', 'RECEPCIONADA') else None),
        'renglones': orden.detalles.select_related('id_producto'),
        'compras': orden.compras.all(),
    })


@require_POST
@requiere_permiso('gestionar_ordenes', redirigir_a='home')
def cambiar_estado(request, pk):
    orden = get_object_or_404(OrdenCompra, pk=pk)
    antes = instantanea(orden)
    accion = request.POST.get('accion')
    try:
        if accion == 'enviar':
            orden = servicios.enviar_orden(orden)
        elif accion == 'cancelar':
            orden = servicios.cancelar_orden(orden)
        elif accion == 'cerrar_diferencias':
            orden = servicios.cerrar_con_diferencias(
                orden, request.POST.get('observacion'))
        else:
            raise ValidationError('Acción desconocida.')
    except ValidationError as error:
        messages.error(request, _texto(error))
    else:
        operacion = {'enviar': 'ENVIO', 'cancelar': 'CANCELACION',
                     'cerrar_diferencias': 'CIERRE CON DIFERENCIAS'}[accion]
        detalle = f'Orden N° {orden.pk}: {orden.get_estado_orden_display()}.'
        if accion == 'cerrar_diferencias':
            detalle += ' ' + request.POST.get('observacion', '').strip()
        registrar_auditoria(request, operacion, 'ORDEN DE COMPRA', instancia=orden,
                            anterior=antes, nuevo=instantanea(orden), detalle=detalle)
        messages.success(request, f'Orden N° {orden.pk}: {orden.get_estado_orden_display().lower()}.')
    return redirect('compras:orden', pk=pk)


@requiere_permiso('gestionar_ordenes', redirigir_a='home')
def orden_pdf(request, pk):
    """La orden para mandarle al proveedor."""
    from weasyprint import HTML

    from nucleo.exportar import _respuesta, _ruta_estatica

    orden = get_object_or_404(OrdenCompra.objects.select_related('id_proveedor'), pk=pk)
    html = render_to_string('compras/orden_pdf.html', {
        'orden': orden, 'renglones': orden.detalles.select_related('id_producto'),
        'negocio': ParametrosNegocio.vigentes(),
        'logo': _ruta_estatica('cafeto/marca/cafeto-horizontal-positivo.svg'),
        'fuente_inter': _ruta_estatica('cafeto/fuentes/inter-latin-400-normal.woff2'),
        'fuente_lora': _ruta_estatica('cafeto/fuentes/lora-latin-600-normal.woff2'),
    })
    respuesta = _respuesta(HTML(string=html).write_pdf(), 'application/pdf',
                           f'orden-de-compra-{orden.pk}.pdf')
    respuesta['Content-Disposition'] = respuesta['Content-Disposition'].replace('attachment', 'inline')
    return respuesta
