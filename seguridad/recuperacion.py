"""Elegir la clave por un enlace al correo: primer ingreso y olvido.

Dos usos, el mismo mecanismo:

* **Activar** (alta de usuario): el sistema le manda a la persona un enlace
  para que elija su clave. Nadie más la conoce, ni quien le dio el alta.
* **Recuperar** (¿te olvidaste la clave?): la persona escribe su DNI o su
  usuario y recibe un enlace en el correo de su ficha.

El enlace lleva un token firmado (``django.core.signing``) con:

* el usuario y el propósito, para que un enlace de activación no sirva
  como uno de recuperación;
* una **huella** de la clave actual: al elegir la clave nueva la huella
  cambia y el mismo enlace ya no sirve (un solo uso);
* la hora de emisión, para que venza: 30 minutos para recuperar, 48 horas
  para activar (OWASP, Forgot Password Cheat Sheet).

No se guarda nada en la base: el token se verifica recalculándolo con la
SECRET_KEY. Lo que sí queda es el asiento en el Registro de Actividad.
"""
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

SAL = 'cafeto.seguridad.elegir-clave'
VIGENCIA = {
    'recuperar': timedelta(minutes=30),
    'activar': timedelta(hours=48),
}
# Cuántos correos de recuperación por cuenta en una hora. Frena a quien
# use el formulario para llenarle la casilla a otro.
SOLICITUDES_POR_HORA = 3
OPERACION_SOLICITUD = 'SOLICITUD DE RECUPERACION DE CLAVE'


def huella(usuario):
    """Cambia cuando cambia la clave: es lo que vuelve el enlace de un uso."""
    material = f'{usuario.pk}|{usuario.clave}|{usuario.fecha_cambio_clave.isoformat()}|{usuario.correo}'
    return salted_hmac(SAL, material).hexdigest()[:24]


def token(usuario, proposito):
    return signing.dumps({'u': usuario.pk, 'h': huella(usuario), 'p': proposito}, salt=SAL)


def leer(token_recibido, proposito):
    """El usuario del enlace, o None si es falso, venció, ya se usó o es de
    otro propósito. Nunca dice cuál de esas fue: no hace falta para la
    persona y le ahorra pistas a quien prueba enlaces."""
    from seguridad.models import Usuario

    try:
        datos = signing.loads(token_recibido, salt=SAL,
                              max_age=VIGENCIA[proposito].total_seconds())
    except (signing.BadSignature, KeyError):
        return None
    if datos.get('p') != proposito:
        return None
    usuario = Usuario.objects.filter(pk=datos.get('u'), activo=True).first()
    if usuario is None or datos.get('h') != huella(usuario):
        return None
    return usuario


def enlace(request, usuario, proposito):
    return request.build_absolute_uri(
        reverse('seguridad:elegir_clave', args=[proposito, token(usuario, proposito)]))


def ofuscar(correo):
    """m•••••@gmail.com: alcanza para que la persona reconozca su casilla."""
    nombre, _, dominio = (correo or '').partition('@')
    if not dominio:
        return '•••'
    return f'{nombre[:1]}{"•" * max(len(nombre) - 1, 3)}@{dominio}'


def enviar(request, usuario, proposito):
    """Manda el enlace. Devuelve True si el servidor de correo lo aceptó.

    Si falla (sin internet en el local, servidor mal configurado) devuelve
    False y quien llama ofrece el camino en persona.
    """
    from negocio.models import ParametrosNegocio

    contexto = {
        'persona': usuario,
        'enlace': enlace(request, usuario, proposito),
        'vigencia': VIGENCIA[proposito],
        'negocio': ParametrosNegocio.vigentes(),
        'proposito': proposito,
    }
    asunto = ('Elegí tu clave del Sistema Cafeto' if proposito == 'activar'
              else 'Recuperar tu clave del Sistema Cafeto')
    try:
        send_mail(asunto,
                  render_to_string('seguridad/correo_clave.txt', contexto),
                  settings.DEFAULT_FROM_EMAIL, [usuario.correo],
                  html_message=render_to_string('seguridad/correo_clave.html', contexto))
    except Exception:                                   # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('No se pudo mandar el correo a %s', usuario.pk)
        return False
    return True


def solicitudes_recientes(usuario):
    from seguridad.models import LogAuditoria

    return LogAuditoria.objects.filter(
        operacion_realizada=OPERACION_SOLICITUD,
        id_registro=usuario.pk,
        tabla_afectada=usuario._meta.db_table,
        fecha_hora__gt=timezone.now() - timedelta(hours=1)).count()
