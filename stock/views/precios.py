"""Aumento masivo de precios, con vista previa."""
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from compras.models import Proveedor
from nucleo import idempotencia
from nucleo.formularios import leer_numero
from seguridad.decorators import requiere_permiso
from seguridad.servicios import registrar_auditoria
from stock import precios
from stock.models import Marca, Rubro

VISTA_PREVIA = 300


def _filtros(datos):
    def buscar(modelo, clave):
        valor = datos.get(clave, '')
        return modelo.objects.filter(pk=valor).first() if valor.isdigit() else None
    return {'rubro': buscar(Rubro, 'rubro'), 'marca': buscar(Marca, 'marca'),
            'proveedor': buscar(Proveedor, 'proveedor')}


@requiere_permiso('actualizar_precios', redirigir_a='stock:productos_lista')
def actualizar_precios(request):
    datos = request.POST if request.method == 'POST' else request.GET
    filtros = _filtros(datos)
    porcentaje = leer_numero(datos.get('porcentaje'), 2)
    paso = Decimal(datos.get('redondeo') or '10')
    contexto = {
        'rubros': Rubro.objects.filter(borrado_logico=False).order_by('nombre_rubro'),
        'marcas': Marca.objects.filter(borrado_logico=False).order_by('nombre_marca'),
        'proveedores': Proveedor.objects.filter(borrado_logico=False).order_by('nombre_proveedor'),
        'redondeos': precios.REDONDEOS, 'datos': datos, 'paso': paso,
    }
    if datos.get('porcentaje'):
        productos = precios.alcanzados(**filtros)
        try:
            if request.method == 'POST':
                if not idempotencia.consumir(request, request.POST.get('token_operacion')):
                    return redirect('stock:productos_lista')
                cantidad = precios.aplicar(list(productos), porcentaje, paso)
                alcance = ', '.join(f'{k}: {v}' for k, v in filtros.items() if v) or 'todos los productos'
                registrar_auditoria(
                    request, 'AUMENTO', 'PRECIOS', modulo='STOCK',
                    detalle=f'{porcentaje} % ({alcance}), redondeo a $ {paso}: {cantidad} precio(s) cambiados.')
                messages.success(request, f'Listo: se actualizaron {cantidad} precio(s).')
                return redirect('stock:productos_lista')
            filas = precios.calcular(productos[:VISTA_PREVIA], porcentaje, paso)
            contexto.update({'filas': filas, 'total': productos.count(),
                             'token': idempotencia.emitir(request)})
        except ValidationError as error:
            contexto['error'] = ' '.join(error.messages)
    return render(request, 'stock/precios.html', contexto)
