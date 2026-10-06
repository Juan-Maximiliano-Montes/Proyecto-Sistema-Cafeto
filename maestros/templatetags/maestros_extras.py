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
