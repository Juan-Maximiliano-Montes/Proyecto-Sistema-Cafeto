"""Filtros del template genérico de datos maestros."""
from django import template

register = template.Library()


@register.filter
def valor_de(objeto, ruta):
    """Lee un atributo cuyo nombre se decide en tiempo de ejecución.

    El template de datos maestros no sabe qué columnas va a mostrar: se
    las dice la configuración de la entidad. Django no deja escribir
    ``{{ fila.campo }}`` con ``campo`` variable, así que la resolución va
    acá.

    Admite navegar por relaciones con puntos (``id_rubro.nombre_rubro``) y
    llama a lo que sea invocable, para poder mostrar un
    ``get_unidad_medida_display`` o un ``localidades.count``.
    """
    valor = objeto
    for parte in ruta.split('.'):
        if valor is None:
            return None
        try:
            valor = getattr(valor, parte)
        except AttributeError:
            return None
        if callable(valor):
            try:
                valor = valor()
            except TypeError:
                # No todo lo invocable se puede invocar sin argumentos. El
                # RelatedManager de Django lo es --define
                # `__call__(self, *, manager)`-- y llamarlo pelado explota.
                # Antes acá se devolvía None, y por eso "productos.count"
                # mostraba un guión en vez del número: el tramo "productos"
                # se moría antes de llegar a "count". Si no se puede llamar,
                # se sigue caminando sobre el objeto tal cual.
                pass
    return valor


@register.filter
def alta_rapida(campo):
    """La URL del alta rápida del campo, o '' (DD-70)."""
    try:
        return campo.field.widget.attrs.get('data-alta-rapida', '')
    except AttributeError:
        return ''


@register.filter
def alta_titulo(campo):
    try:
        return campo.field.widget.attrs.get('data-alta-titulo', '')
    except AttributeError:
        return ''


@register.simple_tag(takes_context=True)
def boton_alta_rapida(context, clave, destino):
    """El "+" de alta rápida para un <select> armado a mano (DD-70).

    ``{% boton_alta_rapida 'proveedores' 'proveedor' %}`` junto al select
    con id ``proveedor``, y ``{% panel_alta_rapida 'proveedor' %}`` debajo.
    Sólo aparece si quien opera puede dar de alta esa tabla.
    """
    from django.utils.html import format_html

    from nucleo.crud import REGISTRO
    configuracion = REGISTRO.get(clave)
    usuario = context.get('usuario_actual') or getattr(
        context.get('request'), 'usuario_actual', None)
    if configuracion is None or usuario is None or not usuario.puede(
            configuracion.permiso('crear')):
        return ''
    return format_html(
        '<button type="button" class="btn btn-secundario js-alta-rapida" '
        'data-url="{}?rapido=1" data-destino="{}" title="{} sin salir de acá" '
        'aria-label="{}"><i class="bi bi-plus-lg"></i></button>',
        configuracion.url('crear'), destino, configuracion.nuevo,
        configuracion.nuevo)


@register.simple_tag
def panel_alta_rapida(destino):
    from django.utils.html import format_html
    return format_html('<div class="alta-rapida-panel" data-para="{}" hidden></div>',
                       destino)
