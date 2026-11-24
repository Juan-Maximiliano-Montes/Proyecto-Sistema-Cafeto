"""Errores que ve la persona: páginas 400/403/404/500 y el manejador de
excepciones de negocio (DD-100).

Regla: nunca un traceback en pantalla. Lo técnico va al log del servidor
(con una referencia corta que también se le muestra a la persona, para
poder encontrarlo); en pantalla, qué pasó y qué hacer.

El manejador corre DESPUÉS de que la transacción del pedido se deshizo
(ATOMIC_REQUESTS envuelve sólo la vista): lo que la vista hubiera escrito
antes de fallar ya no existe cuando se muestra el mensaje.
"""
import decimal
import logging
import uuid

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, InterfaceError, OperationalError
from django.db.models import ProtectedError, RestrictedError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger('cafeto.errores')

TEXTOS = {
    400: ('No entendimos el pedido',
          'Llegó un pedido incompleto o con datos que no se pueden leer. Volvé a la pantalla anterior y probá de nuevo.'),
    403: ('Esto no está habilitado para tu usuario',
          'Tu perfil no tiene permiso para entrar acá, o la página se venció. Si lo necesitás, pedíselo a quien administra.'),
    405: ('Esa acción no se hace así',
          'Se pidió de una forma que esta pantalla no acepta (por ejemplo, recargar una página que guarda algo). '
          'Volvé a la pantalla anterior y usá sus botones.'),
    429: ('Demasiados intentos seguidos',
          'Por seguridad hay que esperar un momento antes de volver a probar. Si no recordás la clave, pedile '
          'una provisoria a quien administra.'),
    404: ('No encontramos esa página',
          'El enlace está mal escrito o eso ya no existe. Volvé al inicio y buscalo desde el menú.'),
    500: ('Algo falló de nuestro lado',
          'No se guardó nada de lo último que hiciste. Probá de nuevo en un momento; si vuelve a pasar, avisá y pasá la referencia.'),
}


def referencia():
    return uuid.uuid4().hex[:8].upper()


# Lo que lee una automatización o el JavaScript de una pantalla: siempre
# JSON, nunca la página HTML (y en DEBUG, nunca la página amarilla de
# Django con la lista de rutas).
TEXTOS_JSON = {
    400: 'El pedido llegó incompleto o con datos que no se pueden leer.',
    401: 'Tenés que iniciar sesión.',
    403: 'Tu perfil no tiene permiso para hacer esto.',
    404: 'Esa dirección no existe.',
    405: 'Esa acción no se pide de esta forma.',
    429: 'Demasiados intentos seguidos: esperá un momento y probá de nuevo.',
    500: 'Algo falló de nuestro lado. No se guardó nada de lo último que se pidió.',
}


def quiere_json(request):
    """El pedido es de la API o de un ``fetch`` de una pantalla."""
    return (request.path.startswith('/api/')
            or request.headers.get('x-requested-with') == 'XMLHttpRequest'
            or 'application/json' in request.headers.get('accept', '')
            or (request.content_type or '').startswith('application/json'))


_quiere_json = quiere_json


def error_json(texto, estado, **extra):
    """El error en JSON con las dos claves que leen los clientes: ``detail``
    (la app y n8n) y ``error`` (las pantallas)."""
    return JsonResponse({'ok': False, 'detail': texto, 'error': texto, **extra}, status=estado)


def _pagina(request, codigo, ref=None, mensaje=None, titulo=None, estado=None):
    titulo_base, texto = TEXTOS[codigo]
    titulo, texto = titulo or titulo_base, mensaje or texto
    contexto = {'codigo': codigo, 'titulo': titulo, 'texto': texto, 'referencia': ref,
                'hora': timezone.localtime(), 'con_sesion': bool(getattr(request, 'usuario_actual', None))}
    try:
        html = render_to_string(f'{codigo}.html', contexto, request=request if codigo not in (500, 503) else None)
    except Exception:                      # la página de error no puede fallar
        logger.exception('No se pudo dibujar la página de error %s', codigo)
        html = (f'<!DOCTYPE html><html lang="es-AR"><meta charset="utf-8"><title>Cafeto</title>'
                f'<h1>{titulo}</h1><p>{texto}</p><p><a href="/">Volver al inicio</a></p></html>')
    return HttpResponse(html, status=estado or codigo)


def pagina_400(request, exception=None):
    if quiere_json(request):
        return error_json(TEXTOS_JSON[400], 400)
    return _pagina(request, 400)


def pagina_403(request, exception=None):
    if quiere_json(request):
        return error_json(TEXTOS_JSON[403], 403)
    return _pagina(request, 403)


def pagina_404(request, exception=None):
    if quiere_json(request):
        return error_json(TEXTOS_JSON[404], 404)
    return _pagina(request, 404)


def pagina_500(request):
    import sys

    if isinstance(sys.exc_info()[1], (OperationalError, InterfaceError)):
        # Se cayó la base antes de llegar a la vista (sesión, permisos).
        return pagina_503(request)
    ref = referencia()
    logger.error('Error 500 [ref %s] en %s %s', ref, request.method, request.path)
    if quiere_json(request):
        return error_json(f'{TEXTOS_JSON[500]} Referencia {ref}.', 500, referencia=ref)
    return _pagina(request, 500, ref)


def pagina_503(request):
    """La base no responde (se cayó la conexión, se reinicia): una página
    que no la necesita y que se vuelve a intentar sola."""
    try:
        html = render_to_string('503.html')
    except Exception:
        html = '<!DOCTYPE html><title>Cafeto</title><h1>Estamos reconectando con la base de datos</h1>'
    respuesta = HttpResponse(html, status=503)
    respuesta['Retry-After'] = '20'
    return respuesta


def csrf_fallo(request, reason=''):
    """El formulario se venció (la pestaña quedó abierta de ayer)."""
    if quiere_json(request):
        return error_json('La pantalla quedó abierta mucho tiempo y se venció: recargala y probá de nuevo.', 403)
    return _pagina(request, 403, mensaje='La pantalla quedó abierta mucho tiempo y el formulario se venció. '
                                         'Recargala y volvé a cargar los datos.')


# ---------------------------------------------------------------------------
#  Excepciones de negocio
# ---------------------------------------------------------------------------
def mensaje_de(excepcion):
    """``(texto, estado HTTP)`` en castellano, o None si no es de negocio."""
    if isinstance(excepcion, ValidationError):
        textos = getattr(excepcion, 'messages', None) or [str(excepcion)]
        return ' '.join(str(t) for t in textos), 400
    if isinstance(excepcion, (ProtectedError, RestrictedError)):
        relacionados = {obj._meta.verbose_name_plural.lower() for obj in
                        list(getattr(excepcion, 'protected_objects', None)
                             or getattr(excepcion, 'restricted_objects', None) or [])[:20]}
        detalle = f' ({", ".join(sorted(relacionados))})' if relacionados else ''
        return ('No se puede borrar porque tiene movimientos asociados' + detalle +
                '. Dalo de baja: deja de aparecer pero su historia se conserva.'), 409
    if isinstance(excepcion, IntegrityError):
        from nucleo.crud import _mensaje_de_base
        return _mensaje_de_base(excepcion), 409
    if isinstance(excepcion, decimal.DecimalException):
        return ('Un importe o una cantidad no es un número válido (o es demasiado grande). '
                'Revisá lo cargado: usá coma para los decimales.'), 400
    if isinstance(excepcion, (DataError, OverflowError)):
        return ('Un número es demasiado grande o un dato no tiene la forma esperada (por ejemplo, un '
                'código de barras escrito donde va un N° de venta). Revisá lo cargado y probá de nuevo.'), 400
    if isinstance(excepcion, ValueError) and 'NUL (0x00)' in str(excepcion):
        return ('El pedido trae caracteres que no se pueden buscar ni guardar. '
                'Revisá lo cargado y probá de nuevo.'), 400
    return None


class ExcepcionesDeNegocioMiddleware:
    """Lo que una vista no atrapó y es de negocio se muestra como aviso.

    * Pedido de la API o de JavaScript: JSON ``{"ok": false, "error": ...}``.
    * Formulario (POST): vuelve a la pantalla de donde vino, con el aviso.
    * Consulta (GET): una página con el aviso y el camino de vuelta.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        respuesta = self.get_response(request)
        return self._con_pagina_propia(request, respuesta)

    def _con_pagina_propia(self, request, respuesta):
        """Respuestas de error sin cuerpo (405 de require_POST, 429) y el 404
        en modo DEBUG reciben la página de Cafeto. La página amarilla de
        Django para el 404 se puede volver a ver con CAFETO_404_DE_DJANGO=1.
        Para la API y el JavaScript, un JSON (si la vista no dio ya uno)."""
        from django.conf import settings

        codigo = respuesta.status_code
        if codigo not in (404, 405, 429) or getattr(respuesta, 'cafeto', False):
            return respuesta
        if request.path.startswith((settings.STATIC_URL or '/static/', '/media/')):
            return respuesta
        if quiere_json(request):
            if 'json' in respuesta.get('Content-Type', ''):
                return respuesta
            propia = error_json(TEXTOS_JSON[codigo], codigo)
            for cabecera in ('Allow', 'Retry-After'):
                if respuesta.has_header(cabecera):
                    propia[cabecera] = respuesta[cabecera]
            propia.cafeto = True
            return propia
        if codigo == 404 and not (settings.DEBUG and not getattr(settings, 'CAFETO_404_DE_DJANGO', False)):
            return respuesta
        if codigo in (405, 429) and respuesta.content.strip():
            return respuesta
        propia = _pagina(request, codigo)
        for cabecera in ('Allow', 'Retry-After'):
            if respuesta.has_header(cabecera):
                propia[cabecera] = respuesta[cabecera]
        propia.cafeto = True
        return propia

    def process_exception(self, request, excepcion):
        if isinstance(excepcion, (OperationalError, InterfaceError)):
            logger.error('Sin base de datos en %s %s: %r', request.method, request.path, excepcion)
            if quiere_json(request):
                return error_json('Estamos reconectando con la base de datos: probá de nuevo en unos segundos.', 503)
            return pagina_503(request)
        traducido = mensaje_de(excepcion)
        if traducido is None:
            return None                      # lo demás: 500 con su página
        texto, estado = traducido
        ref = referencia()
        logger.warning('Excepción de negocio [ref %s] %s en %s %s: %r', ref, type(excepcion).__name__,
                       request.method, request.path, excepcion)
        if quiere_json(request):
            return error_json(texto, estado, referencia=ref)
        volver = request.META.get('HTTP_REFERER', '')
        if (request.method == 'POST' and volver
                and url_has_allowed_host_and_scheme(volver, {request.get_host()}, request.is_secure())):
            messages.error(request, texto)
            return redirect(volver)
        return _pagina(request, 400, ref, mensaje=texto, titulo='No se pudo completar', estado=estado)
