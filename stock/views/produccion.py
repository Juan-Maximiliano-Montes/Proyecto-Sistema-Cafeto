"""Producción por tanda: las empanadas, las pizzas, las tortas del día.

Una tanda hace entrar las unidades del producto y sale de los insumos de
su receta (``stock.servicios.registrar_produccion``), con el costo
congelado a lo que valen los insumos hoy.
"""
from datetime import date

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from nucleo import idempotencia
from nucleo.formularios import leer_numero
from nucleo.paginacion import TAMANIOS_TABLA, paginar
from seguridad.decorators import requiere_permiso
from seguridad.servicios import registrar_auditoria
from stock import servicios
from stock.models import Produccion, Producto, TipoProducto


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


@requiere_permiso('registrar_produccion', redirigir_a='home')
def producciones(request):
    productos = (Producto.objects
                 .filter(tipo_producto=TipoProducto.PRODUCIDO, borrado_logico=False)
                 .select_related('id_marca').order_by('nombre_producto'))
    error = None
    if request.method == 'POST':
        token = request.POST.get('token_operacion')
        try:
            producto = productos.filter(pk=request.POST.get('producto') or 0).first()
            if producto is None:
                raise ValidationError('Elegí qué se produjo.')
            cantidad = leer_numero(request.POST.get('cantidad'), 3)
            if cantidad is None:
                raise ValidationError('Escribí cuántas unidades salieron.')
            vence = None
            if request.POST.get('vence'):
                try:
                    vence = date.fromisoformat(request.POST['vence'])
                except ValueError:
                    raise ValidationError('La fecha de vencimiento no es válida.')
            if not idempotencia.consumir(request, token):
                messages.info(request, 'Esa tanda ya se había cargado.')
                return redirect('stock:producciones')
            produccion = servicios.registrar_produccion(
                producto, cantidad, request.usuario_actual, vence,
                request.POST.get('observacion'))
        except ValidationError as error_:
            idempotencia.devolver(request, token)
            error = _texto(error_)
        else:
            registrar_auditoria(
                request, 'ALTA', 'PRODUCCION', instancia=produccion,
                detalle=f'Tanda de {producto.cantidad_con_unidad(produccion.cantidad)} '
                        f'de {producto} a $ {produccion.costo_unitario} c/u.')
            messages.success(request, f'Listo: entraron {producto.cantidad_con_unidad(produccion.cantidad)} de {producto}.')
            return redirect('stock:producciones')

    recientes = (Produccion.objects.select_related('id_producto', 'id_usuario')
                 .order_by('-pk'))
    contexto = paginar(request, recientes, TAMANIOS_TABLA)
    contexto.update({
        'productos': productos, 'error': error, 'datos': request.POST,
        'token': request.POST.get('token_operacion') or idempotencia.emitir(request),
    })
    return render(request, 'stock/producciones.html', contexto)
