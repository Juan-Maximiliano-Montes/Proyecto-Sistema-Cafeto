"""El puesto de trabajo: en qué cajón está operando esta sesión.

En el local hay dos cajones (Cafetería y Minisuper) y varias tablets. La
caja no es de la persona sino del dispositivo en el que está: se elige una
vez al empezar y queda anotada en la SESIÓN del servidor
(``Sesion.id_caja_registradora``), no en una cookie que se pueda editar.

El dispositivo además se acuerda de su cajón en una cookie FIRMADA: al día
siguiente, la tablet de la cafetería vuelve a quedar en la Caja Cafetería
sin que nadie tenga que elegirla. La firma impide fabricar el valor; y aun
así sólo es una preferencia: elegir caja no habilita nada que el perfil no
tenga.
"""
from django.conf import settings

from seguridad.servicios import sesion_del_request
from ventas import caja as servicio_caja
from ventas.models import CajaRegistradora
from nucleo.validaciones import id_valido

COOKIE = 'cafeto_puesto'
SAL = 'ventas.puesto'
DURACION_COOKIE = 60 * 60 * 24 * 365


def caja_de(request):
    """La caja elegida en esta sesión, o None (también si se dio de baja)."""
    if hasattr(request, '_caja_del_puesto'):
        return request._caja_del_puesto
    sesion = sesion_del_request(request)
    caja = None
    if sesion is not None and sesion.id_caja_registradora_id:
        caja = (CajaRegistradora.objects
                .filter(pk=sesion.id_caja_registradora_id, borrado_logico=False)
                .first())
    elif sesion is not None:
        recordada = request.get_signed_cookie(COOKIE, default=None, salt=SAL,
                                              max_age=DURACION_COOKIE)
        if id_valido(recordada):
            caja = (CajaRegistradora.objects
                    .filter(pk=id_valido(recordada), borrado_logico=False).first())
            if caja is not None:
                sesion.id_caja_registradora = caja
                sesion.save(update_fields=['id_caja_registradora'])
    request._caja_del_puesto = caja
    return caja


def turno_de(request):
    """El turno abierto de la caja del puesto, o None."""
    caja = caja_de(request)
    return servicio_caja.turno_abierto(caja) if caja else None


def fijar(request, caja):
    """Anota la caja en la sesión. La vista recuerda el dispositivo con
    ``recordar()`` sobre la respuesta."""
    sesion = sesion_del_request(request)
    if sesion is None:
        return False
    sesion.id_caja_registradora = caja
    sesion.save(update_fields=['id_caja_registradora'])
    request._caja_del_puesto = caja
    return True


def recordar(respuesta, caja, segura=False):
    """Con HTTPS (detrás de Nginx también) la cookie sólo viaja cifrada."""
    segura = segura or settings.SESSION_COOKIE_SECURE
    if caja is None:
        respuesta.delete_cookie(COOKIE, samesite='Lax')
    else:
        respuesta.set_signed_cookie(
            COOKIE, str(caja.pk), salt=SAL, max_age=DURACION_COOKIE,
            httponly=True, samesite='Lax', secure=segura)
    return respuesta
