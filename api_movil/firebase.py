"""Quién es el cliente que pregunta: su cuenta de Firebase, verificada.

Los clientes no tienen usuario en el sistema Cafeto: entran a la app con
Firebase, que es quien guarda su correo y su clave (el sistema nunca la ve).
Para sus endpoints la app manda ``Authorization: Firebase <id_token>``, y
acá se verifica la firma del token con ``firebase-admin`` y la cuenta de
servicio del proyecto. De la cuenta se toman dos cosas:

* el ``uid``, que no cambia nunca: una vez vinculada la ficha, se la
  encuentra por él (``Cliente.uid_firebase``);
* el correo, sólo si está VERIFICADO: si no, cualquiera podría registrar el
  correo de otra persona y quedarse con su ficha y sus puntos.

La ruta del archivo de la cuenta de servicio sale de
``settings.FIREBASE_CREDENCIAL`` (variable ``CAFETO_FIREBASE_CREDENCIAL``;
con docker compose, ``/run/secrets/firebase.json``). Si falta la variable,
el archivo o ``firebase-admin``, se responde 503 con un texto claro y el
resto de la API sigue andando. ``manage.py probar_firebase`` dice qué falta.
"""
import logging
import os
from collections import namedtuple

from django.conf import settings

logger = logging.getLogger(__name__)

NOMBRE_DE_LA_APLICACION = 'cafeto-api-movil'
NO_DISPONIBLE = ('Todavía no podemos mostrar tu cuenta del club: el sistema del '
                 'local no tiene configurada la conexión con las cuentas de la '
                 'app. Probá más tarde.')
SIN_CONFIGURAR = 'CLIENTES_SIN_CONFIGURAR'
TOKEN_INVALIDO = 'Tu sesión en la app venció. Volvé a entrar con tu cuenta.'
SIN_CORREO_VERIFICADO = ('Verificá tu correo para ver tu cuenta del club: te '
                         'mandamos un enlace al registrarte.')

Cuenta = namedtuple('Cuenta', 'uid correo')

_aplicacion = None


class NoDisponible(Exception):
    """El sistema no puede verificar cuentas de clientes ahora (503)."""


class CuentaRechazada(Exception):
    """La cuenta no sirve para lo pedido: con qué estado, qué decir y, si la
    app tiene que hacer algo distinto según el caso, con qué ``codigo``."""

    def __init__(self, estado, detalle, codigo=None, espera=None):
        super().__init__(detalle)
        self.estado = estado
        self.detalle = detalle
        self.codigo = codigo
        self.espera = espera


def _aplicacion_firebase():
    """La aplicación de ``firebase-admin``, iniciada una sola vez."""
    global _aplicacion
    ruta = getattr(settings, 'FIREBASE_CREDENCIAL', '')
    if not ruta:
        raise NoDisponible
    if _aplicacion is not None:
        return _aplicacion
    if not os.path.isfile(ruta):
        logger.error('No está el archivo de la cuenta de servicio de Firebase (%s): '
                     'ver manage.py probar_firebase.', ruta)
        raise NoDisponible
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        logger.error('Falta instalar firebase-admin (ver requirements.txt).')
        raise NoDisponible
    try:
        _aplicacion = firebase_admin.get_app(NOMBRE_DE_LA_APLICACION)
    except ValueError:
        try:
            _aplicacion = firebase_admin.initialize_app(
                credentials.Certificate(ruta), name=NOMBRE_DE_LA_APLICACION)
        except (OSError, ValueError):
            logger.exception('No se pudo leer la credencial de Firebase (%s).', ruta)
            raise NoDisponible
    return _aplicacion


def cuenta_verificada(id_token):
    """La ``Cuenta`` (uid y correo verificado, en minúsculas) dueña del token."""
    aplicacion = _aplicacion_firebase()
    from firebase_admin import auth, exceptions

    try:
        datos = auth.verify_id_token(id_token, app=aplicacion)
    except auth.CertificateFetchError:
        # Firebase no contestó: no es culpa de la cuenta.
        logger.exception('No se pudieron bajar los certificados de Firebase.')
        raise NoDisponible
    except (ValueError, exceptions.FirebaseError):
        raise CuentaRechazada(401, TOKEN_INVALIDO)
    uid = str(datos.get('uid') or datos.get('sub') or '').strip()
    if not uid:
        raise CuentaRechazada(401, TOKEN_INVALIDO)
    correo = (datos.get('email') or '').strip().lower()
    if not correo or datos.get('email_verified') is not True:
        raise CuentaRechazada(403, SIN_CORREO_VERIFICADO)
    return Cuenta(uid[:128], correo)


def cuenta_del_pedido(request):
    """La ``Cuenta`` verificada de quien manda el pedido.

    Levanta ``CuentaRechazada`` (401/403) o ``NoDisponible`` (503).
    """
    esquema, _, token = request.headers.get('Authorization', '').partition(' ')
    token = token.strip()
    if esquema.lower() != 'firebase' or not token:
        raise CuentaRechazada(401, 'Entrá con tu cuenta para ver tu club.')
    return cuenta_verificada(token)
