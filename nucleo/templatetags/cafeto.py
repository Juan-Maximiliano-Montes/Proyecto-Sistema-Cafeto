"""Filtros de presentación comunes a todas las pantallas.

Los números se muestran como se escriben en Argentina: punto para los
miles y coma para los decimales ($ 12.345,50). Se arma a mano y no con la
localización de Django para que salga igual en la pantalla, en el PDF y en
el ticket, sin depender de cómo esté configurado cada template.
"""
from decimal import Decimal, InvalidOperation

from django import template
from django.utils import timezone

register = template.Library()


def _numero(valor, decimales):
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None
    texto = f'{numero:,.{decimales}f}'
    return texto.replace(',', '§').replace('.', ',').replace('§', '.')


@register.filter
def sin_signo(valor):
    """El valor absoluto: el signo ya lo dice la etiqueta (FALTANTE / SOBRANTE)."""
    try:
        return abs(Decimal(str(valor)))
    except (InvalidOperation, TypeError, ValueError):
        return valor


@register.filter
def diferencia_caja(valor):
    """«Faltan $ 12.000,00» / «Sobran $ 500,00» / «Justa»: nunca «-$» (DD-101)."""
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return valor
    if numero == 0:
        return 'Justa'
    return f'{"Sobran" if numero > 0 else "Faltan"} {pesos(abs(numero))}'


@register.filter
def pesos(valor):
    """1234.5 -> '$ 1.234,50'. ``None`` -> '—'.

    Entre el signo y el número va un espacio DE NO SEPARACIÓN (U+00A0): el
    importe no se parte nunca en dos renglones, esté donde esté (DD-91).
    """
    if valor is None or valor == '':
        return '—'
    texto = _numero(valor, 2)
    if texto is None:
        return valor
    if texto.startswith('-'):
        return f'−$\u00a0{texto[1:]}'
    return f'$\u00a0{texto}'


@register.filter
def cantidad_de(valor, producto):
    """La cantidad como se cuenta ese producto: 6 si va por unidad, 0,350
    si va por peso o volumen o es un insumo (la misma regla que el libro de
    stock: ``exige_cantidad_entera``)."""
    if valor is None or valor == '':
        return '—'
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return valor
    if getattr(producto, 'exige_cantidad_entera', True):
        return cantidad(numero)
    return _numero(numero, 3)


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
def persona(usuario):
    """«Nombre Apellido @usuario»: como se firma en un comprobante."""
    if not usuario:
        return ''
    nombre = f'{getattr(usuario, "nombre", "")} {getattr(usuario, "apellido", "")}'.strip()
    alias = getattr(usuario, 'nombre_usuario', '')
    return f'{nombre} @{alias}' if alias else nombre


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
def pesos_juntos(texto):
    """En un texto libre que nombra importes («pide fiarle $ 5.000,00…»),
    el signo no se separa del número al cortar la línea."""
    import re
    return re.sub(r'\$\s+(?=[\d−-])', '$\u00a0', str(texto or ''))


@register.filter
def vencimiento(fecha, hoy=None):
    """«venció el 11/11», «vence hoy» o «vence el 20/11»: una fecha pasada
    nunca se lee en futuro."""
    if not fecha:
        return ''
    hoy = hoy or timezone.localdate()
    if fecha < hoy:
        return f'venció el {fecha:%d/%m}'
    if fecha == hoy:
        return 'vence hoy'
    return f'vence el {fecha:%d/%m}'


@register.filter
def dias_desde(fecha, hoy=None):
    """Días que pasaron desde ``fecha`` hasta hoy (0 si es hoy o futura)."""
    if not fecha:
        return 0
    hoy = hoy or timezone.localdate()
    return max((hoy - fecha).days, 0)


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


@register.filter
def foto_de(producto):
    """La dirección de la foto de un producto, o None."""
    from stock import imagenes
    return imagenes.url_de(producto)


@register.filter
def a_json(valor):
    """JSON plano (sin ``mark_safe``): Django escapa ``<``, ``>`` y ``&`` al
    volcarlo dentro de ``<template>``, así ningún texto cierra la etiqueta ni
    inyecta HTML, y ``htmx`` no lo borra con ``allowScriptTags: false``."""
    import json
    return json.dumps(valor, ensure_ascii=False)

