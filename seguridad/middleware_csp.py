"""Content-Security-Policy de todas las respuestas (DD-109)."""
from django.conf import settings


def armar_politica(directivas):
    return '; '.join(f'{nombre} {" ".join(fuentes)}'.strip() for nombre, fuentes in directivas.items())


class PoliticaDeContenidoMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response
        self.politica = armar_politica(settings.CAFETO_CSP)

    def __call__(self, request):
        respuesta = self.get_response(request)
        respuesta.headers.setdefault('Content-Security-Policy', self.politica)
        return respuesta
