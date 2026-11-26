"""Endpoints del equipo: salud LAN, entrar, salir y el ABM liviano de marcas y rubros.

    GET    /salud/               alcance real al servidor (público, sin consultas)
    POST   /sesion/              entrar (mismo usuario o DNI sin puntos y clave que la web)
    DELETE /sesion/              salir
    GET    /marcas/  /rubros/    listar los vigentes, de a 20
    POST   /marcas/  /rubros/    dar de alta
    PATCH  /marcas/<id>/ ...     modificar lo que llega
    DELETE /marcas/<id>/ ...     baja lógica

Lo pesado (ventas, compras, stock, precios, usuarios) queda en la web.
"""
import math

from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from api_movil import abm, respuestas
from api_movil.autenticacion import (
    LARGO_IDENTIFICADOR,
    IngresoRechazado,
    cerrar_sesion_movil,
    datos_de_sesion,
    ingresar,
    para_el_equipo,
    publica,
)

FALTAN_DATOS = 'Escribí tu usuario y tu contraseña.'


@transaction.non_atomic_requests
@never_cache
@publica
def salud(request):
    """Healthcheck liviano para verificar alcance al servidor en la LAN del local."""
    return JsonResponse({'ok': True})


@csrf_exempt
def sesion(request):
    if request.method == 'POST':
        return _entrar(request)
    if request.method == 'DELETE':
        return _salir(request)
    return respuestas.metodo_no_permitido(('POST', 'DELETE'))


def _entrar(request):
    datos = respuestas.cuerpo(request)
    if datos is None:
        return respuestas.error(respuestas.DATOS_INVALIDOS, 400)
    identificador, clave = datos.get('usuario'), datos.get('clave')
    if not isinstance(identificador, str) or not isinstance(clave, str):
        return respuestas.error(FALTAN_DATOS, 400)
    # Como el formulario de la web, que recorta los dos campos: una clave
    # que entra en la web tiene que entrar en el celular.
    identificador, clave = identificador.strip(), clave.strip()
    if not identificador or not clave:
        return respuestas.error(FALTAN_DATOS, 400)
    if len(identificador) > LARGO_IDENTIFICADOR:
        return respuestas.error(respuestas.DATOS_INVALIDOS, 400)

    try:
        usuario, token = ingresar(request, identificador, clave)
    except IngresoRechazado as rechazo:
        respuesta = respuestas.error(rechazo.detalle, rechazo.estado)
        if rechazo.espera is not None:
            # Cuántos segundos esperar antes de volver a probar (RFC 9110).
            respuesta['Retry-After'] = str(
                max(1, math.ceil(rechazo.espera.total_seconds())))
        return respuesta
    return JsonResponse(datos_de_sesion(usuario, token))


def _salir(request):
    """Cierra la sesión móvil del token: desde ahí el token ya no vale.

    Como en la web, la salida no es un asiento del Registro de Actividad:
    queda en la sesión (``SesionMovil.revocada_en``). Salir nunca falla,
    aunque el token ya no valga.
    """
    cerrar_sesion_movil(request)
    return HttpResponse(status=204)


# ---------------------------------------------------------------------------
#  Marcas
# ---------------------------------------------------------------------------
@para_el_equipo(GET='ver_marcas', POST='crear_marca')
def marcas(request):
    if request.method == 'GET':
        return abm.listar(request, abm.MARCAS)
    return abm.crear(request, abm.MARCAS)


@para_el_equipo(PATCH='editar_marca', DELETE='eliminar_marca')
def marca(request, pk):
    if request.method == 'PATCH':
        return abm.editar(request, abm.MARCAS, pk)
    return abm.dar_de_baja(request, abm.MARCAS, pk)


# ---------------------------------------------------------------------------
#  Rubros
# ---------------------------------------------------------------------------
@para_el_equipo(GET='ver_rubros', POST='crear_rubro')
def rubros(request):
    if request.method == 'GET':
        return abm.listar(request, abm.RUBROS)
    return abm.crear(request, abm.RUBROS)


@para_el_equipo(PATCH='editar_rubro', DELETE='eliminar_rubro')
def rubro(request, pk):
    if request.method == 'PATCH':
        return abm.editar(request, abm.RUBROS, pk)
    return abm.dar_de_baja(request, abm.RUBROS, pk)
