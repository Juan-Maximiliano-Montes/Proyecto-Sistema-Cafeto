"""Deja disponible, durante todo el pedido, quién está operando.

El sello de ``usuario_modificacion`` lo pone ``save()`` a través de
``ConSelloDeModificacion``, y lo que necesita es el usuario en el contexto
del proceso: ni el modelo ni el mixin reciben el request.

El ``try/finally`` no es decorativo: sin restaurar el token, un hilo
reutilizado del pool arrastraría el usuario del pedido anterior y la
próxima fila quedaría sellada a nombre de otra persona.
"""
from cafeto import contexto


class ContextoDeUsuarioMiddleware:
    """Publica ``request.usuario_actual`` en el contexto del pedido."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'usuario_actual', None)
        token = contexto.fijar(usuario if (usuario and usuario.pk) else None)
        try:
            return self.get_response(request)
        finally:
            contexto.restaurar(token)
