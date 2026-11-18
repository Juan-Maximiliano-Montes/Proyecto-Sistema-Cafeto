"""Leer el JSON que manda el navegador o un servicio sin confiar en su forma
(DD-103).

``json.loads`` acepta cualquier valor JSON: ``123``, ``"texto"``,
``[1, 2]``. Una vista que espera un objeto y hace ``datos.get(...)`` sobre
un número termina en un 500. Acá se controla la forma una sola vez y lo que
no la cumple sale como ``PedidoMalArmado``: un ``ValidationError`` que las
vistas devuelven con HTTP 400 (los avisos de negocio, como «no alcanza el
stock», siguen con su código de siempre).
"""
import json

from django.core.exceptions import ValidationError

MAL_ARMADO = ('El pedido llegó mal armado: se esperaba un objeto JSON ({…}). '
              'Recargá la página y probá de nuevo.')

NOMBRES = {str: 'un texto', list: 'una lista', dict: 'un objeto ({…})', bool: 'sí o no',
           int: 'un número entero'}


class PedidoMalArmado(ValidationError):
    """El cuerpo o un campo no tienen la forma que la vista espera."""


def objeto(crudo, mensaje=MAL_ARMADO):
    """Un JSON (bytes o texto) que tiene que ser un objeto -> ``dict``. Vacío
    es ``{}``."""
    if crudo in (None, b'', ''):
        return {}
    try:
        datos = json.loads(crudo)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise PedidoMalArmado(mensaje)
    if not isinstance(datos, dict):
        raise PedidoMalArmado(mensaje)
    return datos


FORMULARIOS = ('multipart/form-data', 'application/x-www-form-urlencoded')


def del_pedido(request):
    """El cuerpo del pedido como objeto. Un formulario común (sin JSON,
    como el botón que pide una autorización) se lee como sus campos."""
    if (request.content_type or '').lower() in FORMULARIOS:
        return request.POST.dict()
    return objeto(request.body)


def lista(crudo, etiqueta='La lista'):
    """Un JSON que tiene que ser una lista (los renglones de un formulario)."""
    if crudo in (None, b'', ''):
        return []
    try:
        datos = json.loads(crudo)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise PedidoMalArmado(f'{etiqueta} llegó mal armada. Recargá la página y probá de nuevo.')
    if not isinstance(datos, list):
        raise PedidoMalArmado(f'{etiqueta} llegó mal armada. Recargá la página y probá de nuevo.')
    return datos


def campo(datos, clave, tipo, etiqueta=None, defecto=None):
    """``datos[clave]`` si es del ``tipo`` pedido; ``defecto`` si no vino (o
    vino vacío); ``PedidoMalArmado`` si vino con otro tipo. Un booleano no
    pasa por número aunque Python lo considere ``int``."""
    valor = datos.get(clave) if isinstance(datos, dict) else None
    if valor is None or valor == '':
        return defecto
    tipos = tipo if isinstance(tipo, tuple) else (tipo,)
    if (isinstance(valor, bool) and bool not in tipos) or not isinstance(valor, tipos):
        esperado = ' o '.join(NOMBRES.get(t, t.__name__) for t in tipos)
        raise PedidoMalArmado(f'{etiqueta or f"El campo «{clave}»"} tiene que ser {esperado}.')
    return valor
