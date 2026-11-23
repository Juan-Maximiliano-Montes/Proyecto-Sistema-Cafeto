"""«Sugerencias de mejora»: la bandeja donde una persona aprueba o descarta.

Una pantalla corta a propósito. Cada fila dice tres cosas —qué tiene hoy
el producto, qué se propone y por qué— y ofrece dos botones. No hay
"aplicar todo": la mejora del catálogo se hace de a poco, mirando, que es
justamente lo que no pasó cuando se migró de golpe.

Lo que se aplica pasa por ``stock.sugerencias``, que usa las validaciones
del modelo. Acá sólo se decide y se deja el asiento en el Registro de
Actividad con el nombre de quien decidió.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from seguridad.decorators import requiere_permiso
from seguridad.servicios import instantanea, registrar_auditoria
from stock import sugerencias as servicio
from stock.models import SugerenciaCatalogo, TipoSugerencia


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


@requiere_permiso('editar_producto', redirigir_a='home')
def sugerencias(request):
    """Lo que espera el visto bueno, filtrable por tipo."""
    tipo = (request.GET.get('tipo') or '').upper()
    if tipo not in TipoSugerencia.values:
        tipo = ''
    todas = list(servicio.pendientes())
    cantidades = {t: 0 for t in TipoSugerencia.values}
    for sugerencia in todas:
        cantidades[sugerencia.tipo_sugerencia] += 1
    filas = [s for s in todas if not tipo or s.tipo_sugerencia == tipo]
    vistas = [{'clave': '', 'etiqueta': 'Todas', 'cantidad': len(todas),
               'activa': not tipo, 'url': '?'}]
    vistas += [{'clave': valor, 'etiqueta': etiqueta,
                'cantidad': cantidades[valor], 'activa': tipo == valor,
                'url': f'?tipo={valor}'}
               for valor, etiqueta in TipoSugerencia.choices
               if cantidades[valor]]
    return render(request, 'stock/sugerencias.html', {
        'filas': filas, 'vistas': vistas, 'tipo': tipo, 'total': len(todas),
        'url_productos': reverse('stock:productos_lista'),
    })


def _asiento(request, sugerencia, antes_producto, antes_sugerencia, operacion, detalle):
    registrar_auditoria(
        request, operacion, 'SUGERENCIA DE CATALOGO', instancia=sugerencia,
        anterior={'sugerencia': antes_sugerencia, 'producto': antes_producto},
        nuevo={'sugerencia': instantanea(sugerencia),
               'producto': instantanea(sugerencia.id_producto)},
        modulo='STOCK', detalle=detalle)


@requiere_permiso('editar_producto', redirigir_a='home')
@require_POST
def aplicar(request, pk):
    sugerencia = SugerenciaCatalogo.objects.select_related('id_producto').filter(pk=pk).first()
    antes_sugerencia = instantanea(sugerencia)
    antes_producto = instantanea(sugerencia.id_producto) if sugerencia else None
    try:
        sugerencia, resultado = servicio.aceptar(pk, request.usuario_actual)
    except ValidationError as error:
        messages.error(request, _texto(error))
    else:
        _asiento(request, sugerencia, antes_producto, antes_sugerencia, 'APLICACION',
                 f'Aplicó la sugerencia «{sugerencia.get_tipo_sugerencia_display()}» '
                 f'en "{sugerencia.id_producto}": {resultado}')
        messages.success(request, f'{sugerencia.id_producto}: {resultado}')
    return redirect(_volver(request))


@requiere_permiso('editar_producto', redirigir_a='home')
@require_POST
def descartar(request, pk):
    sugerencia = SugerenciaCatalogo.objects.select_related('id_producto').filter(pk=pk).first()
    antes_sugerencia = instantanea(sugerencia)
    antes_producto = instantanea(sugerencia.id_producto) if sugerencia else None
    try:
        sugerencia = servicio.descartar(pk, request.usuario_actual)
    except ValidationError as error:
        messages.error(request, _texto(error))
    else:
        _asiento(request, sugerencia, antes_producto, antes_sugerencia, 'DESCARTE',
                 f'Descartó la sugerencia «{sugerencia.get_tipo_sugerencia_display()}» '
                 f'en "{sugerencia.id_producto}": no se cambió nada.')
        messages.success(request, 'Sugerencia descartada: el producto queda como está.')
    return redirect(_volver(request))


def _volver(request):
    """Vuelve a la misma pestaña desde la que se resolvió."""
    tipo = (request.POST.get('tipo') or '').upper()
    destino = reverse('stock:sugerencias')
    return f'{destino}?tipo={tipo}' if tipo in TipoSugerencia.values else destino
