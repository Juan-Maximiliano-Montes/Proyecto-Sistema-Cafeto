"""Las respuestas de la API: todas en JSON y con la misma forma.

El contrato con la app (``docs/API-movil.md`` del proyecto móvil) es corto,
y la app no confía en lo que llega: descarta lo que no tiene la forma
esperada. Por eso las formas salen de un solo lugar:

* un error lleva ``detail`` (el texto que la app muestra tal cual) o
  ``errores`` (una lista de textos por campo; la app muestra el primero).
  Cuando la app tiene que hacer algo distinto según el caso (pedir el DNI,
  mandar a la caja, confirmar costos), además lleva un ``codigo`` fijo;
* una lista es ``{"resultados": [...], "hay_mas": bool}``, de a 20.
"""
import json
from decimal import Decimal

from django.http import JsonResponse

POR_PAGINA = 20
# Más allá no hay catálogo: un número enorme sólo se convertiría en un
# OFFSET que la base recorre para nada.
PAGINA_MAXIMA = 10_000

DATOS_INVALIDOS = 'El pedido no trae datos válidos.'


def error(detalle, estado, codigo=None, **extra):
    """``{"detail": ...}``, con ``codigo`` y lo que haga falta si se pide."""
    cuerpo = {'detail': detalle}
    if codigo:
        cuerpo['codigo'] = codigo
    cuerpo.update(extra)
    return JsonResponse(cuerpo, status=estado)


def errores(por_campo):
    return JsonResponse({'errores': por_campo}, status=400)


def metodo_no_permitido(permitidos):
    respuesta = error('Ese pedido no se puede hacer acá.', 405)
    respuesta['Allow'] = ', '.join(permitidos)
    return respuesta


def cuerpo(request):
    """El cuerpo JSON como diccionario; ``None`` si no es un objeto JSON."""
    if not request.body:
        return {}
    try:
        datos = json.loads(request.body)
    except ValueError:      # incluye JSON mal formado y bytes que no son UTF-8
        return None
    return datos if isinstance(datos, dict) else None


def numero(valor):
    """Un ``Decimal`` como número de JSON: 4000 y no "4000.00" ni 4000.0."""
    if valor is None:
        return None
    valor = valor if isinstance(valor, Decimal) else Decimal(str(valor))
    return int(valor) if valor == valor.to_integral_value() else float(valor)


def numero_de_pagina(request):
    try:
        pagina = int(request.GET.get('pagina', 1))
    except (TypeError, ValueError):
        return 1
    return min(PAGINA_MAXIMA, max(1, pagina))


def lista(resultados, hay_mas=False, **extra):
    return JsonResponse({'resultados': resultados, 'hay_mas': hay_mas, **extra})


def paginar(request, consulta, convertir):
    """La página pedida de ``consulta``, convertida fila por fila.

    Se pide UNA fila de más: si viene, hay otra página. Así no hace falta
    contar el total en cada pedido (lo mismo que el buscador de productos
    de la web).
    """
    desde = (numero_de_pagina(request) - 1) * POR_PAGINA
    filas = list(consulta[desde:desde + POR_PAGINA + 1])
    return lista([convertir(fila) for fila in filas[:POR_PAGINA]],
                 hay_mas=len(filas) > POR_PAGINA)
