"""Servicios transversales: sesiones de negocio y auditoría.

Dos cosas que todas las pantallas necesitan y que conviene que estén
escritas UNA sola vez:

  * ``abrir_sesion`` / ``cerrar_sesion``: la tabla SESIONES del DER. No es
    la sesión de Django (esa es una cookie): es el registro de negocio que
    dice qué usuario está adentro, desde cuándo y desde qué IP. La
    auditoría cuelga de ella (``log_auditoria.id_sesion``), así que sin
    sesión abierta no se puede auditar nada.
  * ``registrar_auditoria``: el único lugar del sistema que escribe en
    LOG_AUDITORIA. Ninguna vista arma el registro a mano.

Reparto con la base (regla 4.4 d): ningún trigger de
``02_triggers.sql`` escribe en LOG_AUDITORIA —los triggers sellan
``fecha_modificacion`` y ``usuario_modificacion``, y protegen los libros,
pero no auditan—. Por eso la auditoría de los datos maestros la escribe
la aplicación, acá, y no se duplica en ningún lado.
"""
import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from django.db import models
from django.utils import timezone

# Cuánto dura una sesión sin actividad antes de considerarse vencida.
DURACION_SESION = timedelta(hours=12)

# Módulo con el que se marcan en la auditoría todas las operaciones del
# proceso N° 10 (flujo "Registro de Mantenimiento" del DFD).
MODULO_DATOS_MAESTROS = 'DATOS_MAESTROS'


# ---------------------------------------------------------------------------
#  SESIONES
# ---------------------------------------------------------------------------
def ip_del_request(request):
    """IP del cliente, mirando primero el encabezado del proxy."""
    reenviada = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if reenviada:
        return reenviada.split(',')[0].strip()[:45]
    return (request.META.get('REMOTE_ADDR') or '0.0.0.0')[:45]


def abrir_sesion(usuario, request):
    """Abre la sesión de negocio del usuario y devuelve la fila.

    DD-01: un usuario tiene UNA sesión activa a la vez. Si quedó otra
    abierta —se fue la luz, cerró el navegador— se revoca antes, que es lo
    que además garantiza el índice ``uq_sesion_activa_por_usuario``.
    """
    from .models import Sesion

    Sesion.objects.filter(id_usuario=usuario, activa=True).update(
        activa=False,
        fecha_cierre=timezone.now(),
        motivo_cierre='REVOCACION',
    )

    ahora = timezone.now()
    # El token identifica la sesión; se guarda hasheado, igual que la clave.
    token = secrets.token_urlsafe(32)
    return Sesion.objects.create(
        id_usuario=usuario,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        fecha_inicio=ahora,
        fecha_ultimo_acceso=ahora,
        fecha_expiracion=ahora + DURACION_SESION,
        ip_origen=ip_del_request(request),
        activa=True,
    )


def cerrar_sesion(id_sesion, motivo='CIERRE_MANUAL'):
    """Cierra la sesión de negocio al salir del sistema."""
    from .models import Sesion

    if not id_sesion:
        return
    Sesion.objects.filter(pk=id_sesion, activa=True).update(
        activa=False,
        fecha_cierre=timezone.now(),
        motivo_cierre=motivo,
    )


def sesion_del_request(request):
    """Sesión de negocio asociada al request, o None.

    La vista de login deja su id en la sesión de Django; acá se recupera y
    se comprueba que siga activa (puede haberla cerrado la baja del
    usuario, vía el trigger ``trg_baja_usuario_cierra_sesiones``).
    """
    from .models import Sesion

    id_sesion = request.session.get('id_sesion_negocio')
    if not id_sesion:
        return None
    return Sesion.objects.filter(pk=id_sesion, activa=True).first()


# ---------------------------------------------------------------------------
#  AUDITORÍA
# ---------------------------------------------------------------------------
def instantanea(objeto, campos=None):
    """Foto de una fila lista para guardar como JSONB.

    Convierte a texto lo que JSON no sabe serializar (Decimal, fechas,
    objetos relacionados) y saltea los campos que no son datos de negocio.
    Devuelve ``None`` si no hay objeto: así un alta queda con
    ``registro_anterior = NULL``, que es exactamente lo que significa.
    """
    if objeto is None:
        return None

    datos = {}
    for campo in objeto._meta.fields:
        if campos is not None and campo.name not in campos:
            continue
        valor = getattr(objeto, campo.attname, None)
        if isinstance(valor, Decimal):
            valor = str(valor)
        elif hasattr(valor, 'isoformat'):
            valor = valor.isoformat()
        elif isinstance(valor, models.Model):
            valor = str(valor)
        datos[campo.column] = valor
    return datos


def registrar_auditoria(request, operacion, entidad, anterior=None,
                        nuevo=None, detalle='', modulo=MODULO_DATOS_MAESTROS):
    """Escribe UN registro en LOG_AUDITORIA (flujo "Registro de Mantenimiento").

    ``operacion`` es el verbo y la entidad ("ALTA DE RUBRO"), ``anterior`` y
    ``nuevo`` son las fotos de la fila antes y después (D-04). En un alta
    ``anterior`` va en NULL; en una baja, ``nuevo`` conserva la fila con
    ``borrado_logico`` ya en True, para que se vea qué cambió.

    Si no hay sesión de negocio —por ejemplo, un alta hecha desde un test o
    desde el shell— no se escribe nada y no se rompe: la auditoría
    acompaña a la operación, no la condiciona.
    """
    from .models import LogAuditoria

    sesion = sesion_del_request(request) if request is not None else None
    if sesion is None:
        return None

    return LogAuditoria.objects.create(
        operacion_realizada=f'{operacion} DE {entidad}'[:50].upper(),
        modulo_afectado=modulo[:40],
        detalle_operacion=detalle or None,
        id_sesion=sesion,
        registro_anterior=anterior,
        registro_nuevo=nuevo,
    )
