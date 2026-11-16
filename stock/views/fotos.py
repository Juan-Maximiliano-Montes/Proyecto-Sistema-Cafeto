"""Sacar, cambiar o quitar la foto de un producto."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from seguridad.decorators import requiere_permiso
from seguridad.servicios import registrar_auditoria
from stock import imagenes
from stock.models import Producto


@require_POST
@requiere_permiso('editar_producto', redirigir_a='stock:productos_lista')
def foto_de_producto(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    antes = producto.imagen
    try:
        if request.POST.get('quitar'):
            imagenes.quitar(producto)
            hecho = 'Se quitó la foto'
        elif request.FILES.get('foto'):
            imagenes.guardar_foto(producto, request.FILES['foto'])
            hecho = 'Foto guardada'
        else:
            imagenes.poner_url(producto, request.POST.get('url'))
            hecho = 'Foto guardada'
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        registrar_auditoria(
            request, 'MODIFICACION', 'PRODUCTO', instancia=producto,
            anterior={'id_producto': producto.pk, 'imagen': antes},
            nuevo={'id_producto': producto.pk, 'imagen': producto.imagen},
            detalle=f'{hecho} de {producto}.')
        messages.success(request, f'{hecho} de {producto}.')
    return redirect(request.POST.get('volver') or 'stock:productos_lista')
