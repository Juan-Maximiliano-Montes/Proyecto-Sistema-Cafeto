"""Que un doble clic, un F5 o el botón "atrás" no repitan una operación.

Cada formulario que mueve plata o mercadería lleva un ``token_operacion``
de un solo uso. El servidor lo emite al dibujar el formulario y lo da por
usado la primera vez que llega: el segundo envío del mismo formulario se
reconoce y no hace nada.

La venta, además, guarda su token en la base (``Venta.token_operacion`` es
UNIQUE): ahí la garantía es de la base de datos y no depende de la sesión.
Esto cubre todo lo demás (aperturas, retiros, mermas...), donde un
duplicado no rompe ninguna regla pero sí descuadra la caja o el stock.
"""
import uuid

CLAVE = 'cafeto_tokens'
MAXIMO = 200


def emitir(request):
    """Un token nuevo, anotado como pendiente en la sesión."""
    token = uuid.uuid4().hex
    pendientes = request.session.get(CLAVE, [])
    pendientes.append(token)
    request.session[CLAVE] = pendientes[-MAXIMO:]
    return token


def consumir(request, token):
    """True la primera vez que llega ``token``; False si ya se usó o es ajeno."""
    pendientes = request.session.get(CLAVE, [])
    if not token or token not in pendientes:
        return False
    pendientes.remove(token)
    request.session[CLAVE] = pendientes
    return True


def devolver(request, token):
    """La operación falló por una regla: el mismo formulario puede reintentarse."""
    if token:
        pendientes = request.session.get(CLAVE, [])
        if token not in pendientes:
            pendientes.append(token)
            request.session[CLAVE] = pendientes[-MAXIMO:]
