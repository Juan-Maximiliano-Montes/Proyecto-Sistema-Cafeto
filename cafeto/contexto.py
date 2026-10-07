"""Quién está operando, disponible desde cualquier modelo.

El sello de modificación (DD-17) lo pone ``save()``, y ``save()`` no
recibe el request: no tiene de dónde sacar quién está operando.

La pieza que falta es un lugar donde dejar el usuario al empezar el pedido
y leerlo desde el modelo. Se usa ``ContextVar`` y no una variable global ni
un ``threading.local``:

* una global se pisaría entre pedidos concurrentes;
* un thread-local funciona con el servidor por hilos, pero se rompe con
  ASGI/async, donde varias corrutinas comparten hilo.

``ContextVar`` es lo que Python trae para exactamente esto, y es lo que usa
el propio Django para su contexto asíncrono.

Hay que resetear el valor al terminar cada pedido. Por eso ``usar()`` es un
context manager que devuelve el token y lo restaura: un hilo reutilizado
del pool no puede arrastrar el usuario del pedido anterior.
"""
from contextlib import contextmanager
from contextvars import ContextVar


_usuario_actual = ContextVar('cafeto_usuario_actual', default=None)


def fijar(usuario):
    """Deja el usuario en el contexto y devuelve el token para restaurar."""
    return _usuario_actual.set(usuario)


def restaurar(token):
    _usuario_actual.reset(token)


def usuario_actual():
    """El usuario que está operando, o None.

    None es un caso legítimo y frecuente: el shell, un comando de
    ``manage.py``, una migración de datos o un test que crea filas
    directamente. En todos esos casos el sello queda nulo, que es
    exactamente lo que significa "no lo hizo nadie desde la aplicación".
    """
    return _usuario_actual.get()


@contextmanager
def usar(usuario):
    """Corre un bloque como si lo operara ``usuario``.

    Lo usan el middleware, los comandos de ``manage.py`` que escriben en
    nombre de alguien y los tests.
    """
    token = fijar(usuario)
    try:
        yield usuario
    finally:
        restaurar(token)


# ---------------------------------------------------------------------------
#  EL PASAJE A HISTÓRICO
# ---------------------------------------------------------------------------
# Las reglas de inmutabilidad tienen UNA excepción legítima: el proceso que
# archiva datos viejos copia la fila al histórico y recién entonces la borra
# del libro vivo. Esta marca es la que los mixins de `cafeto/reglas.py`
# consultan para dejarlo pasar.
#
# Acá cumple el mismo papel, y por la misma razón se mantiene SEPARADO del
# usuario: no es "alguien con permiso", es "un proceso puntual del sistema".
_en_pasaje_historico = ContextVar('cafeto_pasaje_historico', default=False)


def en_pasaje_historico():
    return _en_pasaje_historico.get()


@contextmanager
def pasaje_historico():
    """Habilita el borrado físico mientras dure el bloque.

    Úsese SÓLO en el proceso de archivado. Fuera de él, un DELETE sobre una
    tabla con baja lógica es un error (RN-8.1.1).
    """
    token = _en_pasaje_historico.set(True)
    try:
        yield
    finally:
        _en_pasaje_historico.reset(token)
