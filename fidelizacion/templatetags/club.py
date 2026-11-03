"""El código QR del cliente, dibujado en el servidor como SVG.

Lo genera ``segno`` (Python puro, sin dependencias): no hace falta un
servicio externo ni JavaScript, y el QR sale igual en pantalla y en papel.
El contenido es el código que generó el sistema (``CAF-`` y 10 caracteres
hexadecimales), nunca un dato tipeado: por eso se puede marcar como seguro.
"""
import re

import segno
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

_CODIGO = re.compile(r'^[A-Za-z0-9-]{1,64}$')


@register.simple_tag
def qr_svg(codigo, escala=5):
    if not codigo or not _CODIGO.match(str(codigo)):
        return ''
    qr = segno.make(str(codigo), error='m', micro=False)
    return mark_safe(qr.svg_inline(scale=int(escala), dark='#161616',
                                   omitsize=False))
