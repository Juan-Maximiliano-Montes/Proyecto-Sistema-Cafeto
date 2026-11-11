"""Aumento masivo de precios, con vista previa."""
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from compras.models import Proveedor
from nucleo import idempotencia
from nucleo.formularios import leer_numero
from seguridad.decorators import requiere_permiso
from seguridad.servicios import fotos, registrar_auditoria
from stock import precios
from stock.models import Marca, Producto, Rubro
from nucleo.templatetags.cafeto import pesos

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
    # Un redondeo que no es un número ("abc") daba error 500: cae en un valor
    # que la regla de precios rechaza con su mensaje ("Elegí cómo se redondea").
    paso = leer_numero(datos.get('redondeo') or '10', 2) or Decimal('0')
    contexto = {
        'rubros': Rubro.objects.filter(borrado_logico=False).order_by('nombre_rubro'),
        'marcas': Marca.objects.filter(borrado_logico=False).order_by('nombre_marca'),
        'proveedores': Proveedor.objects.filter(borrado_logico=False).order_by('nombre_proveedor'),
        'redondeos': precios.REDONDEOS, 'datos': datos, 'paso': paso,
    }
    if datos.get('porcentaje'):
        productos = precios.alcanzados(**filtros)
        try:
            bajo_costo = precios.quedan_bajo_costo(productos, porcentaje, paso)
            contexto['bajo_costo'] = bajo_costo
            confirmado = (request.method == 'POST'
                          and (not bajo_costo or request.POST.get('confirmo_bajo_costo')))
            if request.method == 'POST' and not confirmado:
                contexto['error'] = (
                    f'Con este cambio, {len(bajo_costo)} producto(s) quedan por debajo '
                    'del costo. Si es a propósito, tildá la confirmación de abajo.')
            if confirmado:
                if not idempotencia.consumir(request, request.POST.get('token_operacion')):
                    return redirect('stock:productos_lista')
                lista = list(productos)
                campos = ('id_producto', 'nombre_producto', 'precio_venta')
                antes = fotos(lista, campos)
                cantidad = precios.aplicar(lista, porcentaje, paso)
                despues = fotos(Producto.objects.filter(pk__in=[p.pk for p in lista])
                                .order_by('pk'), campos)
                previos = {f['id_producto']: f for f in antes}
                cambiaron = [f for f in despues
                             if previos[f['id_producto']]['precio_venta'] != f['precio_venta']]
                antes = [previos[f['id_producto']] for f in cambiaron]
                alcance = ', '.join(f'{k}: {v}' for k, v in filtros.items() if v) or 'todos los productos'
                registrar_auditoria(
                    request, 'AUMENTO', 'PRECIOS', modulo='STOCK',
                    anterior=antes, nuevo=cambiaron,
                    detalle=f'{porcentaje} % ({alcance}), redondeo a {pesos(paso)}: {cantidad} precio(s) cambiados.')
                messages.success(request, f'Listo: se actualizaron {cantidad} precio(s).')
                return redirect('stock:productos_lista')
            filas = precios.calcular(productos[:VISTA_PREVIA], porcentaje, paso)
            contexto.update({'filas': filas, 'total': productos.count(),
                             'token': idempotencia.emitir(request)})
        except ValidationError as error:
            contexto['error'] = ' '.join(error.messages)
    return render(request, 'stock/precios.html', contexto)
