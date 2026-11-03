"""Filtros de presentación comunes a todas las pantallas.

Los números se muestran como se escriben en Argentina: punto para los
miles y coma para los decimales ($ 12.345,50). Se arma a mano y no con la
localización de Django para que salga igual en la pantalla, en el PDF y en
el ticket, sin depender de cómo esté configurado cada template.
"""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _numero(valor, decimales):
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None
    texto = f'{numero:,.{decimales}f}'
    return texto.replace(',', '§').replace('.', ',').replace('§', '.')


@register.filter
def pesos(valor):
    """1234.5 -> '$ 1.234,50'. ``None`` -> '—'."""
    if valor is None or valor == '':
        return '—'
    texto = _numero(valor, 2)
    if texto is None:
        return valor
    if texto.startswith('-'):
        return f'−$ {texto[1:]}'
    return f'$ {texto}'


@register.filter
def cantidad(valor):
    """12.000 -> '12'; 1.250 -> '1,25'. Hasta tres decimales, sin ceros de más."""
    if valor is None or valor == '':
        return '—'
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return valor
    if numero == numero.to_integral_value():
        return _numero(numero, 0)
    texto = _numero(numero, 3).rstrip('0')
    return texto.rstrip(',')


@register.filter
def iniciales(usuario):
    nombre = (getattr(usuario, 'nombre', '') or '')[:1]
    apellido = (getattr(usuario, 'apellido', '') or '')[:1]
    return (nombre + apellido).upper() or '?'


@register.filter
def contiene(coleccion, elemento):
    """``{% with puede=permisos_usuario|contiene:'crear_rubro' %}``."""
    try:
        return elemento in coleccion
    except TypeError:
        return False


@register.filter
def dias_para_vencer(lote, hoy=None):
    """``{{ lote|dias_para_vencer:hoy }}``: negativo si ya venció."""
    try:
        return lote.dias_para_vencer(hoy)
    except Exception:
        return None


@register.filter
def index(secuencia, posicion):
    """``{{ columnas|index:forloop.counter0 }}``."""
    try:
        return secuencia[int(posicion)]
    except (IndexError, TypeError, ValueError):
        return None
