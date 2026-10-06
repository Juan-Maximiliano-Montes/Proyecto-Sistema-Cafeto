"""Deja disponible, durante todo el pedido, quién está operando.

Antes esto publicaba ``cafeto.id_usuario`` en la conexión de PostgreSQL,
porque los triggers de ``02_triggers.sql`` lo leían con
``current_setting()`` para poder sellar ``usuario_modificacion``.

Con la lógica movida a Python, el sello lo pone ``save()`` a través de
``ConSelloDeModificacion``, y lo que necesita es el usuario en el contexto
del proceso, no en la sesión de la base.

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
