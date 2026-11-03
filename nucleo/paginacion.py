"""Paginación de los listados: de a poco y a elección de quien mira.

Ninguna pantalla dibuja la tabla entera. Un listado de tarjetas se ve de a
12, 24, 48 o 96 (múltiplos de 2, 3 y 4, así la grilla siempre cierra
pareja); una tabla de renglones, de a 10, 25, 50 o 100.

La cantidad elegida viaja en la URL (``?por_pagina=``) junto con los
filtros, así un enlace copiado muestra lo mismo y el botón "atrás" del
navegador funciona. Un valor que no está en la lista se ignora: nadie pide
``?por_pagina=1000000`` y tumba el servidor.
"""
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

TAMANIOS_TARJETAS = (12, 24, 48, 96)
TAMANIOS_TABLA = (10, 25, 50, 100)


def paginar(request, consulta, tamanios=TAMANIOS_TARJETAS, por_defecto=None):
    """Devuelve el contexto del paginador para ``consulta``.

    ``pagina``       la página pedida (o la última, si se pidió de más)
    ``filas``        lo que hay que dibujar
    ``total``        cuántos resultados hay en total
    ``por_pagina``   el tamaño en uso
    ``tamanios``     las opciones del selector
    ``parametros``   la query string SIN ``pagina``, para armar los enlaces
    ``paginas``      números a mostrar, con ``None`` donde va "…"
    """
    por_defecto = por_defecto or tamanios[0]
    try:
        por_pagina = int(request.GET.get('por_pagina', por_defecto))
    except (TypeError, ValueError):
        por_pagina = por_defecto
    if por_pagina not in tamanios:
        por_pagina = por_defecto

    paginador = Paginator(consulta, por_pagina)
    numero = request.GET.get('pagina', 1)
    try:
        pagina = paginador.page(numero)
    except PageNotAnInteger:
        pagina = paginador.page(1)
    except EmptyPage:
        pagina = paginador.page(paginador.num_pages)

    parametros = request.GET.copy()
    parametros.pop('pagina', None)
    return {
        'pagina': pagina,
        'filas': pagina.object_list,
        'total': paginador.count,
        'por_pagina': por_pagina,
        'tamanios': tamanios,
        'parametros': parametros.urlencode(),
        'paginas': ventana_de_paginas(pagina.number, paginador.num_pages),
    }


def ventana_de_paginas(actual, ultima, alrededor=2):
    """1 … 4 5 [6] 7 8 … 20: siempre la primera, la última y las vecinas."""
    numeros = []
    for n in range(1, ultima + 1):
        if n in (1, ultima) or abs(n - actual) <= alrededor:
            numeros.append(n)
        elif numeros and numeros[-1] is not None:
            numeros.append(None)
    return numeros
