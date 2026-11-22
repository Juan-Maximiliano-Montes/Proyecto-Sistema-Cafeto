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
from nucleo.validaciones import id_valido
from nucleo.textos import pluralizar, segun

VISTA_PREVIA = 300


def _filtros(datos):
    def buscar(modelo, clave):
        valor = datos.get(clave, '')
        return modelo.objects.filter(pk=id_valido(valor)).first() if id_valido(valor) else None
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
    # En los filtros sólo va lo que tiene algo para remarcar. Un proveedor
    # de servicios (la luz, el agua) no tiene productos a la venta: ofrecerlo
    # acá sólo sirve para elegirlo por error y no ver nada.
    vendibles = precios.alcanzados()
    contexto = {
        'rubros': Rubro.objects.filter(pk__in=vendibles.values('id_rubro')).order_by('nombre_rubro'),
        'marcas': Marca.objects.filter(pk__in=vendibles.values('id_marca')).order_by('nombre_marca'),
        'proveedores': Proveedor.objects.filter(
            pk__in=vendibles.exclude(id_proveedor=None).values('id_proveedor')).order_by('nombre_proveedor'),
        'redondeos': precios.REDONDEOS, 'datos': datos, 'paso': paso,
    }
    if datos.get('porcentaje'):
        productos = precios.alcanzados(**filtros)
        try:
            # Una sola pasada: de acá salen la vista previa, lo que queda
            # bajo el costo y lo que el redondeo deja igual.
            todas = precios.calcular(productos, porcentaje, paso)
            bajo_costo = [p for p, _antes, despues in todas
                          if p.precio_costo is not None and despues < p.precio_costo]
            contexto['bajo_costo'] = bajo_costo
            confirmado = (request.method == 'POST'
                          and (not bajo_costo or request.POST.get('confirmo_bajo_costo')))
            if request.method == 'POST' and not confirmado:
                contexto['error'] = (
                    f"Con este cambio, {pluralizar(len(bajo_costo), 'producto')} "
                    f"{segun(len(bajo_costo), 'queda', 'quedan')} por debajo del costo. "
                    'Si es a propósito, tildá la confirmación de abajo.')
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
                    detalle=f'{porcentaje} % ({alcance}), redondeo a {pesos(paso)}: '
                           f"{pluralizar(cantidad, 'precio cambiado', 'precios cambiados')}.")
                messages.success(request, f"Listo: {segun(cantidad, 'se actualizó', 'se actualizaron')} {pluralizar(cantidad, 'precio')}.")
                return redirect('stock:productos_lista')
            filas = todas[:VISTA_PREVIA]
            total = len(todas)
            if not total:
                elegidos = [str(v) for v in filtros.values() if v]
                contexto['sin_productos'] = (
                    'No hay ningún producto a la venta que sea de '
                    + ' y de '.join(f'"{e}"' for e in elegidos)
                    + ' a la vez. Probá dejando alguno de los filtros en "Todos".'
                    if elegidos else 'Todavía no hay productos a la venta para remarcar.')
            sin_cambio = sum(1 for _p, antes, despues in todas if antes == despues)
            contexto.update({'filas': filas, 'total': total, 'sin_cambio': sin_cambio,
                             'token': idempotencia.emitir(request)})
        except ValidationError as error:
            contexto['error'] = ' '.join(error.messages)
    return render(request, 'stock/precios.html', contexto)
