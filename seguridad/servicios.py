"""Servicios transversales de Seguridad: sesiones, bloqueo y auditoría.

Cuatro cosas que todas las pantallas necesitan y que conviene que estén
escritas UNA sola vez:

  * ``abrir_sesion`` / ``cerrar_sesion`` / ``validar_sesion``: la tabla
    SESIONES del DER. No es la sesión de Django (ésa es una cookie): es el
    registro de negocio que dice qué usuario está adentro, desde cuándo y
    desde qué IP. El servidor la revisa en CADA pedido: si venció, si
    pasó demasiado tiempo sin uso o si la revocaron, deja de valer aunque
    el navegador conserve la cookie.
  * ``espera_por_bloqueo``: el bloqueo progresivo por reintentos. Se
    calcula sobre el libro INTENTOS_ACCESO, así que no hay un contador que
    se pueda poner en cero a mano.
  * ``registrar_auditoria``: el único lugar del sistema que escribe en
    LOG_AUDITORIA. Ninguna vista arma el registro a mano.

Quién audita, y quién no (regla 4.4 d): los mixins de ``nucleo/reglas.py``
NO escriben en LOG_AUDITORIA —sellan ``fecha_modificacion`` y
``usuario_modificacion``, y protegen los libros, pero no auditan—. La
auditoría la escribe la aplicación, acá, y no se duplica en ningún lado.
"""
import hashlib
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Max
from django.utils import timezone

from nucleo import contexto


def _duracion_sesion():
    return timedelta(hours=getattr(settings, 'SESION_DURACION_HORAS', 12))


def _inactividad_maxima():
    return timedelta(
        minutes=getattr(settings, 'SESION_INACTIVIDAD_MINUTOS', 60))


# Cada cuánto se graba el "último acceso". Grabarlo en cada pedido sería
# una escritura por clic; con un minuto de resolución alcanza para medir
# la inactividad.
RESOLUCION_ULTIMO_ACCESO = timedelta(seconds=60)


# ---------------------------------------------------------------------------
#  SESIONES
# ---------------------------------------------------------------------------
def ip_del_request(request):
    """IP del cliente.

    ``X-Forwarded-For`` sólo se lee si el sistema está detrás de un proxy
    propio (``CONFIAR_EN_PROXY``). Si no, cualquiera podría escribir ese
    encabezado y elegir la IP con la que queda registrado —y esquivar el
    bloqueo por IP cambiándola en cada intento—.
    """
    if getattr(settings, 'CONFIAR_EN_PROXY', False):
        reenviada = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if reenviada:
            return reenviada.split(',')[0].strip()[:45]
    return (request.META.get('REMOTE_ADDR') or '0.0.0.0')[:45]


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def abrir_sesion(usuario, request=None, ip=None):
    """Abre la sesión de negocio del usuario y devuelve la fila.

    DD-01: un usuario tiene UNA sesión activa a la vez. Si quedó otra
    abierta —se fue la luz, cerró el navegador, entró desde otro equipo—
    se revoca antes, que es lo que además garantiza el índice
    ``uq_sesion_activa_por_usuario``. El otro equipo queda afuera en su
    próximo clic.

    El token se devuelve en ``sesion.token`` (atributo de la instancia, no
    de la base): va a la sesión de Django y en la base queda sólo su hash,
    igual que la clave.
    """
    from seguridad.models import Sesion

    Sesion.objects.filter(id_usuario=usuario, activa=True).update(
        activa=False,
        fecha_cierre=timezone.now(),
        motivo_cierre='REVOCACION',
    )

    ahora = timezone.now()
    token = secrets.token_urlsafe(32)
    sesion = Sesion.objects.create(
        id_usuario=usuario,
        token_hash=_hash(token),
        fecha_inicio=ahora,
        fecha_ultimo_acceso=ahora,
        fecha_expiracion=ahora + _duracion_sesion(),
        ip_origen=ip or (ip_del_request(request) if request else '0.0.0.0'),
        activa=True,
    )
    sesion.token = token
    return sesion


def vincular_sesion(sesion_django, usuario, sesion):
    """Deja en la sesión de Django lo que el middleware va a verificar."""
    sesion_django['usuario_id'] = usuario.pk
    sesion_django['usuario_nombre'] = f'{usuario.nombre} {usuario.apellido}'
    sesion_django['id_sesion_negocio'] = sesion.pk
    sesion_django['token_sesion'] = sesion.token


def traspasar_sesion(request, entrante, detalle_salida, detalle_entrada):
    """Pasa el navegador de quien opera a otra persona, sin pasar por el login.

    Es para el relevo de caja (DD-97): quien entra ya puso su usuario y su
    clave en la tablet (``autorizar_con_clave``, con el bloqueo por
    reintentos del login). Se hace lo mismo que al salir y al entrar, en ese
    orden y con su rastro:

    1. La salida de quien se va queda en el Registro de Actividad, firmada
       con SU sesión, y esa sesión se cierra.
    2. La sesión de Django se tira entera (``flush``: llave nueva y nada de
       lo de la otra persona, OWASP) y se rota el token CSRF.
    3. Se abre la sesión de negocio de quien entra (DD-01: revoca la que
       tuviera en otro equipo), en el mismo puesto, y su entrada queda
       firmada con su sesión nueva.
    """
    from django.middleware.csrf import rotate_token

    from seguridad.models import Sesion

    saliente = request.usuario_actual
    anterior = sesion_del_request(request)
    caja = getattr(anterior, 'id_caja_registradora', None)
    if anterior is not None:
        antes = instantanea(anterior)
        cerrar_sesion(anterior.pk)
        registrar_auditoria(
            request, 'CIERRE', 'SESION', instancia=anterior, anterior=antes,
            nuevo=instantanea(Sesion.objects.get(pk=anterior.pk)),
            modulo='SEGURIDAD', detalle=detalle_salida)
    request.session.flush()
    rotate_token(request)
    sesion = abrir_sesion(entrante, request)
    if caja is not None:
        sesion.id_caja_registradora = caja
        sesion.save(update_fields=['id_caja_registradora'])
    vincular_sesion(request.session, entrante, sesion)
    request.usuario_actual = entrante
    request.sesion_negocio = sesion
    registrar_auditoria(
        request, 'INICIO', 'SESION', instancia=sesion, nuevo=instantanea(sesion),
        modulo='SEGURIDAD', detalle=detalle_entrada)
    return saliente, sesion


def cerrar_sesion(id_sesion, motivo='CIERRE_MANUAL'):
    """Cierra la sesión de negocio al salir del sistema."""
    from seguridad.models import Sesion

    if not id_sesion:
        return
    Sesion.objects.filter(pk=id_sesion, activa=True).update(
        activa=False,
        fecha_cierre=timezone.now(),
        motivo_cierre=motivo,
    )


# Lo que se le dice a la persona cuando su sesión deja de valer.
AVISOS_DE_CIERRE = {
    'EXPIRACION': 'Tu sesión venció. Volvé a ingresar.',
    'INACTIVIDAD': 'Tu sesión se cerró por inactividad. Volvé a ingresar.',
    'REVOCACION': ('Tu sesión se cerró porque se ingresó con tu usuario desde '
                   'otro equipo, o porque un administrador la cerró.'),
}


def validar_sesion(request, usuario):
    """Comprueba en el servidor que la sesión siga valiendo.

    Devuelve ``(sesion, None)`` si vale, o ``(None, aviso)`` con el motivo
    para mostrarle a la persona. Si la sesión venció la cierra con su
    motivo, así el Registro de Actividad sabe por qué terminó.
    """
    from seguridad.models import Sesion

    id_sesion = request.session.get('id_sesion_negocio')
    token = request.session.get('token_sesion') or ''
    sesion = (Sesion.objects.filter(pk=id_sesion, id_usuario=usuario).first()
              if id_sesion else None)
    if sesion is None or not secrets.compare_digest(
            sesion.token_hash, _hash(token)):
        return None, AVISOS_DE_CIERRE['REVOCACION']
    if not sesion.activa:
        return None, AVISOS_DE_CIERRE.get(
            sesion.motivo_cierre, AVISOS_DE_CIERRE['REVOCACION'])

    ahora = timezone.now()
    motivo = sesion.vencio(ahora, _inactividad_maxima())
    if motivo:
        cerrar_sesion(sesion.pk, motivo)
        return None, AVISOS_DE_CIERRE[motivo]

    if ahora - sesion.fecha_ultimo_acceso >= RESOLUCION_ULTIMO_ACCESO:
        Sesion.objects.filter(pk=sesion.pk).update(fecha_ultimo_acceso=ahora)
        sesion.fecha_ultimo_acceso = ahora
    return sesion, None


def sesion_del_request(request):
    """Sesión de negocio asociada al request, o None.

    El middleware la deja validada en ``request.sesion_negocio``. Fuera
    del ciclo normal (tests que arman el request a mano) se la busca por
    la sesión de Django.
    """
    from seguridad.models import Sesion

    sesion = getattr(request, 'sesion_negocio', None)
    if sesion is not None:
        return sesion
    session = getattr(request, 'session', None)
    id_sesion = session.get('id_sesion_negocio') if session else None
    if not id_sesion:
        return None
    return Sesion.objects.filter(pk=id_sesion, activa=True).first()


# ---------------------------------------------------------------------------
#  BLOQUEO PROGRESIVO POR REINTENTOS
# ---------------------------------------------------------------------------
# (intentos fallidos seguidos, duración del bloqueo). Escalona: a los 5 se
# frena un minuto —alcanza para el que se equivocó tipeando—, y si sigue,
# el freno crece hasta volver inútil probar claves a mano o con un script.
ESCALONES_DE_BLOQUEO = (
    (5, timedelta(minutes=1)),
    (8, timedelta(minutes=5)),
    (10, timedelta(minutes=15)),
    (15, timedelta(hours=1)),
)
# Sólo cuentan los intentos de las últimas 24 h, y nunca los anteriores
# al último ingreso correcto.
VENTANA_DE_INTENTOS = timedelta(hours=24)

# Freno por IP, para el que prueba muchos usuarios distintos con la misma
# clave: no llega a bloquear a ninguno, pero sí a sí mismo.
INTENTOS_POR_IP = 30
VENTANA_POR_IP = timedelta(minutes=15)

# El ingreso desde la app móvil (api_movil) no abre una fila en SESIONES: la
# sesión web es única (DD-01) y la del celular la cerraría. Lo que lo deja
# asentado es este asiento de LOG_AUDITORIA, y para el bloqueo cuenta como
# cualquier ingreso correcto.
OPERACION_INGRESO_MOVIL = 'INICIO DE SESION MOVIL'


def _fallos(identificador, usuario, ip, desde):
    from seguridad.models import IntentoAcceso, LogAuditoria

    intentos = IntentoAcceso.objects.filter(fecha_hora__gt=desde).exclude(
        motivo_rechazo='CUENTA_BLOQUEADA')
    if usuario is not None:
        # Un ingreso correcto —en la web o en el celular— o una clave nueva
        # ponen la cuenta en cero.
        ultimo_ingreso = usuario.sesiones.aggregate(
            m=Max('fecha_inicio'))['m']
        ultimo_ingreso_movil = LogAuditoria.objects.filter(
            id_usuario=usuario,
            operacion_realizada=OPERACION_INGRESO_MOVIL).aggregate(
            m=Max('fecha_hora'))['m']
        for corte in (ultimo_ingreso, ultimo_ingreso_movil,
                      usuario.fecha_cambio_clave):
            if corte and corte > desde:
                desde = corte
        return intentos.filter(id_usuario=usuario, fecha_hora__gt=desde)
    # Un identificador que no existe se cuenta junto con la IP: así se lo
    # trata igual que a uno que existe y nadie puede averiguar cuáles
    # usuarios existen mirando si se bloquean o no.
    return intentos.filter(
        id_usuario__isnull=True, ip_origen=ip,
        nombre_usuario_ingresado__iexact=(identificador or '')[:35])


def espera_por_bloqueo(identificador, usuario, ip):
    """Cuánto falta para poder volver a intentar, o None si no hay bloqueo."""
    from seguridad.models import IntentoAcceso

    ahora = timezone.now()

    por_ip = IntentoAcceso.objects.filter(
        ip_origen=ip, fecha_hora__gt=ahora - VENTANA_POR_IP).exclude(
        motivo_rechazo='CUENTA_BLOQUEADA')
    if por_ip.count() >= INTENTOS_POR_IP:
        ultimo = por_ip.aggregate(m=Max('fecha_hora'))['m']
        return ultimo + VENTANA_POR_IP - ahora

    fallos = _fallos(identificador, usuario, ip, ahora - VENTANA_DE_INTENTOS)
    cantidad = fallos.count()
    duracion = None
    for umbral, tramo in ESCALONES_DE_BLOQUEO:
        if cantidad >= umbral:
            duracion = tramo
    if duracion is None:
        return None
    ultimo = fallos.aggregate(m=Max('fecha_hora'))['m']
    restante = ultimo + duracion - ahora
    return restante if restante > timedelta(0) else None


def intentos_antes_del_bloqueo(identificador, usuario, ip):
    """Cuántos intentos quedan antes del próximo escalón de bloqueo."""
    ahora = timezone.now()
    cantidad = _fallos(identificador, usuario, ip,
                       ahora - VENTANA_DE_INTENTOS).count()
    for umbral, _ in ESCALONES_DE_BLOQUEO:
        if cantidad < umbral:
            return umbral - cantidad
    return 0


def registrar_intento_fallido(request, identificador, usuario, motivo):
    """Deja el intento rechazado en el libro INTENTOS_ACCESO.

    Si con este intento los fallos cruzan un umbral, además deja la alerta
    para la Administradora (ver ``alertar_intentos_fallidos``).
    """
    from seguridad.models import IntentoAcceso

    if usuario is None and motivo != 'CUENTA_BLOQUEADA':
        motivo = 'USUARIO_INEXISTENTE'
    IntentoAcceso.objects.create(
        id_usuario=usuario,
        nombre_usuario_ingresado=(identificador or '')[:35],
        ip_origen=ip_del_request(request),
        motivo_rechazo=motivo,
    )
    # Los rebotes DURANTE un bloqueo no cuentan como fallos nuevos (ver
    # _fallos), así que tampoco pueden disparar alertas repetidas.
    if motivo != 'CUENTA_BLOQUEADA':
        alertar_intentos_fallidos(request, identificador, usuario)


# ---------------------------------------------------------------------------
#  ALERTA DE INTENTOS FALLIDOS
# ---------------------------------------------------------------------------
# El bloqueo progresivo FRENA al que prueba claves; la alerta hace que una
# PERSONA se entere. Sin ella, un ataque sostenido contra la cuenta de la
# dueña quedaría escrito en INTENTOS_ACCESO sin que nadie lo mire.
#
# Se alerta al CRUZAR un umbral, no en cada intento: el fallo N° 5, 8, 10 o
# 15 de una cuenta (los escalones del bloqueo) y el N° 30 desde una misma IP
# en 15 minutos. Así la Administradora recibe un aviso por escalón y no
# cuarenta por un mismo ataque.
#
# La alerta es un asiento de LOG_AUDITORIA, no una tabla nueva: queda en el
# mismo libro inmutable que el resto de la auditoría, con la cuenta atacada
# como registro afectado. "Revisarla" también es un asiento (quién la vio y
# cuándo): nada se marca ni se borra.
OPERACION_ALERTA = 'ALERTA DE INTENTOS FALLIDOS'
OPERACION_REVISION = 'REVISIÓN DE ALERTAS DE SEGURIDAD'


def alertar_intentos_fallidos(request, identificador, usuario):
    """Si el último fallo cruzó un umbral, asienta la alerta. Devuelve los
    textos de las alertas asentadas (lista vacía si no hubo)."""
    from seguridad.models import IntentoAcceso

    ip = ip_del_request(request)
    ahora = timezone.now()
    textos = []

    cantidad = _fallos(identificador, usuario, ip,
                       ahora - VENTANA_DE_INTENTOS).count()
    escalones = dict(ESCALONES_DE_BLOQUEO)
    if cantidad in escalones:
        if usuario is not None:
            quien = f'la cuenta "{usuario.nombre_usuario}"'
        else:
            quien = f'un usuario inexistente ("{(identificador or "")[:35]}")'
        textos.append(
            f'{cantidad} intentos fallidos seguidos sobre {quien} desde la IP '
            f'{ip}. El ingreso queda bloqueado '
            f'{describir_espera(escalones[cantidad])}.')

    por_ip = IntentoAcceso.objects.filter(
        ip_origen=ip, fecha_hora__gt=ahora - VENTANA_POR_IP).exclude(
        motivo_rechazo='CUENTA_BLOQUEADA').count()
    if por_ip == INTENTOS_POR_IP:
        textos.append(
            f'{por_ip} intentos fallidos desde la IP {ip} en '
            f'{describir_espera(VENTANA_POR_IP)}, con uno o varios usuarios. '
            'La IP queda frenada.')

    for texto in textos:
        # Sin request: el que falla no tiene sesión, y la alerta no la
        # "hace" nadie. El registro afectado es la cuenta atacada.
        registrar_auditoria(None, 'ALERTA', 'INTENTOS FALLIDOS',
                            detalle=texto, modulo='SEGURIDAD',
                            instancia=usuario)
    return textos


def alertas_sin_revisar(usuario):
    """Alertas de seguridad posteriores a la última revisión de ``usuario``."""
    from seguridad.models import LogAuditoria

    ultima = LogAuditoria.objects.filter(
        operacion_realizada=OPERACION_REVISION,
        id_usuario=usuario).aggregate(m=Max('fecha_hora'))['m']
    alertas = LogAuditoria.objects.filter(operacion_realizada=OPERACION_ALERTA)
    if ultima is not None:
        alertas = alertas.filter(fecha_hora__gt=ultima)
    return alertas.order_by('-fecha_hora', '-pk')


def marcar_alertas_revisadas(request):
    """Asienta que el usuario logueado leyó las alertas pendientes."""
    usuario = getattr(request, 'usuario_actual', None)
    cantidad = alertas_sin_revisar(usuario).count()
    if not cantidad:
        return None
    return registrar_auditoria(
        request, 'REVISIÓN', 'ALERTAS DE SEGURIDAD',
        detalle=f'Revisó {cantidad} alerta(s) de intentos fallidos.',
        modulo='SEGURIDAD')


def describir_espera(restante):
    """'3 minutos', '1 hora': redondeando siempre para arriba."""
    minutos = max(1, int(-(-restante.total_seconds() // 60)))
    if minutos >= 60:
        horas = -(-minutos // 60)
        return f'{horas} hora' + ('s' if horas > 1 else '')
    return f'{minutos} minuto' + ('s' if minutos > 1 else '')


# ---------------------------------------------------------------------------
#  AUTORIZACIÓN EN EL MOSTRADOR
# ---------------------------------------------------------------------------
def autorizar_con_clave(request, identificador, clave, permiso,
                        distinto_de=None, para='', auditar=True,
                        titular_se_confirma=True):
    """La encargada pone su usuario y su clave en la tablet de la cajera.

    Para lo que el perfil de quien opera no alcanza —un descuento por
    encima de su tope, devolver plata, cerrar una caja con diferencia—, se
    pide que lo autorice OTRA persona que sí tenga el permiso, ahí mismo.
    Devuelve ese usuario, o levanta ``ValidationError`` con el motivo.

    Pasa por los mismos controles que el login: el bloqueo progresivo por
    reintentos y el libro de intentos fallidos. Sin eso, la pantalla de
    autorizar sería una forma de adivinar claves sin que nadie se entere.

    Además, cada autorización RECHAZADA queda en el Registro de Actividad
    con resultado RECHAZADA (DD-62): qué se quiso autorizar, quién lo pidió
    y por qué no se aceptó. ``auditar=False`` es para la vista previa del
    ticket, que se recalcula con cada tecla: el intento que cuenta es el
    del cobro.

    **La titular no necesita a otra persona** (DD-80). El control por
    oposición protege a la dueña de los demás, no de sí misma: cuando quien
    opera es la titular, alcanza con que vuelva a poner SU clave. La clave se
    pide igual, por si dejó la sesión abierta y se fue. Sólo vale para ella:
    otro Administrador sigue necesitando a otra persona.
    ``titular_se_confirma=False`` es para cuando hace falta una persona
    distinta de verdad (quien recibe el cajón en un relevo).
    """
    from django.core.exceptions import ValidationError
    from django.db.models import Q

    from seguridad.models import Usuario

    identificador = (identificador or '').strip()
    if not identificador or not clave:
        raise ValidationError(
            f'Para {para or "esto"} hace falta que alguien autorice con su '
            'usuario y su clave.')
    usuario = (Usuario.objects.select_related('id_perfil', 'id_perfil_temporal')
               .filter(Q(dni_usuario=identificador) |
                       Q(nombre_usuario__iexact=identificador)).first())
    ip = ip_del_request(request) if request is not None else None

    def rechazar(mensaje, motivo):
        if auditar:
            registrar_autorizacion_rechazada(
                request, permiso, para, identificador, motivo)
        raise ValidationError(mensaje)

    espera = espera_por_bloqueo(identificador, usuario, ip)
    if espera is not None:
        registrar_intento_fallido(request, identificador, usuario,
                                  'CUENTA_BLOQUEADA')
        rechazar('Demasiados intentos fallidos. Probá de nuevo en '
                 f'{describir_espera(espera)}.',
                 'la cuenta de quien autoriza está bloqueada por reintentos')
    if usuario is None or not usuario.verificar_clave(clave):
        registrar_intento_fallido(request, identificador, usuario,
                                  'CLAVE_INCORRECTA')
        rechazar('El usuario o la clave de quien autoriza no son correctos.',
                 'el usuario o la clave no son correctos')
    if not usuario.activo:
        registrar_intento_fallido(request, identificador, usuario,
                                  'USUARIO_INACTIVO')
        rechazar('Quien autoriza está dado de baja.',
                 'quien autoriza está dado de baja')
    se_confirma = titular_se_confirma and getattr(usuario, 'es_titular', False)
    if distinto_de is not None and usuario.pk == distinto_de.pk and not se_confirma:
        rechazar('La autorización la tiene que dar otra persona, no quien opera.',
                 'quiso autorizarse a sí mismo')
    if not usuario.tiene_permiso(permiso):
        rechazar(f'{usuario.nombre} {usuario.apellido} no tiene permiso para '
                 f'{para or "autorizar esto"}.',
                 f'{usuario.nombre} {usuario.apellido} no tiene ese permiso')
    return usuario


# ---------------------------------------------------------------------------
#  AUDITORÍA
# ---------------------------------------------------------------------------
def instantanea(objeto, campos=None):
    """Foto de una fila lista para guardar como JSONB.

    Convierte a texto lo que JSON no sabe serializar (Decimal, fechas,
    objetos relacionados) y saltea los campos que no son datos de negocio.
    Devuelve ``None`` si no hay objeto: así un alta queda con
    ``registro_anterior = NULL``, que es exactamente lo que significa.

    Nunca guarda el hash de la clave ni el del token: el registro de
    actividad no es un lugar para llevarse copias de credenciales.
    """
    if objeto is None:
        return None

    datos = {}
    for campo in objeto._meta.fields:
        if campos is not None and campo.name not in campos:
            continue
        if campo.column in ('contrasena_hash', 'token_hash', 'pin_hash'):
            continue
        valor = getattr(objeto, campo.attname, None)
        if isinstance(valor, (Decimal, uuid.UUID)):
            valor = str(valor)
        elif hasattr(valor, 'isoformat'):
            valor = valor.isoformat()
        elif isinstance(valor, models.Model):
            valor = str(valor)
        datos[campo.column] = valor
    return datos


def foto(objeto, **hijos):
    """La foto de una operación con renglones: la cabecera y sus hijos.

    Una venta no es sólo su fila de VENTAS: son también sus renglones y sus
    pagos. ``foto(venta, renglones=venta.detalles.all(),
    pagos=venta.pagos.all())`` guarda las tres cosas en el mismo asiento,
    para que el Registro de Actividad muestre la operación completa.
    """
    datos = instantanea(objeto)
    if datos is None:
        return None
    for nombre, filas in hijos.items():
        datos[nombre] = [instantanea(fila) for fila in filas]
    return datos


def fotos(filas, campos=None):
    """La foto de varias filas a la vez (un aumento de precios, una
    importación): una lista, en el orden en que vienen. ``campos`` acota
    cada foto a lo que la operación toca."""
    return [instantanea(fila, campos) for fila in filas]


def registrar_auditoria(request, operacion, entidad, anterior=None,
                        nuevo=None, detalle='', modulo=None, instancia=None,
                        resultado=None, usuario=None):
    """Escribe UN registro en LOG_AUDITORIA.

    ``operacion`` es el verbo y la entidad ("ALTA DE RUBRO"), ``anterior`` y
    ``nuevo`` son las fotos de la fila antes y después (D-04). En un alta
    ``anterior`` va en NULL; en una baja, ``nuevo`` conserva la fila con
    ``borrado_logico`` ya en True, para que se vea qué cambió.

    ``instancia`` es la fila afectada: de ella salen la tabla, su PK y el
    módulo (la app del modelo), así nadie los tipea a mano.

    Siempre se escribe. Si no hay sesión web —un comando en el servidor—
    el asiento queda sin sesión, pero queda: una operación sin rastro es
    justo lo que la auditoría existe para impedir.

    ``entidad`` vacía deja la operación tal cual ("ACCESO DENEGADO").
    ``resultado`` es EXITOSA salvo que se diga otra cosa; para los rechazos
    está ``registrar_rechazo`` (DD-62).
    ``usuario`` es quien actuó cuando no hay sesión que lo diga: la titular
    que aprueba desde el enlace que le llegó al celular (DD-94).
    """
    from seguridad.models import LogAuditoria, ResultadoOperacion

    sesion = sesion_del_request(request) if request is not None else None
    usuario = usuario or (getattr(request, 'usuario_actual', None) if request else None)
    if usuario is None and sesion is not None:
        usuario = sesion.id_usuario
    if usuario is None:
        usuario = contexto.usuario_actual()

    if modulo is None:
        modulo = (instancia._meta.app_label.upper() if instancia is not None
                  else 'GENERAL')

    texto = f'{operacion} DE {entidad}' if entidad else operacion
    return LogAuditoria.objects.create(
        operacion_realizada=texto[:50].upper(),
        resultado_operacion=resultado or ResultadoOperacion.EXITOSA,
        modulo_afectado=modulo[:40],
        detalle_operacion=detalle or None,
        id_sesion=sesion,
        id_usuario=usuario,
        id_perfil_efectivo=usuario.perfil_efectivo if usuario else None,
        tabla_afectada=(instancia._meta.db_table[:40]
                        if instancia is not None else None),
        id_registro=instancia.pk if instancia is not None else None,
        registro_anterior=anterior,
        registro_nuevo=nuevo,
    )


# ---------------------------------------------------------------------------
#  RECHAZOS (DD-62)
# ---------------------------------------------------------------------------
# El Registro de Actividad no sólo cuenta lo que se hizo: también lo que el
# sistema FRENÓ por un control de seguridad (OWASP Logging Cheat Sheet:
# "access control failures"; NIST SP 800-53 AU-3: el resultado del evento).
# Se asienta con resultado RECHAZADA y sin fotos, porque no cambió nada.
#
# Lo que NO se asienta: los errores comunes de carga (un campo vacío, una
# cantidad mal escrita, un stock que no alcanza). No son un intento de
# saltear un control, y una cajera los comete varias veces por día: si
# entraran al libro, taparían los rechazos que sí hay que mirar.
def registrar_rechazo(request, operacion, entidad, motivo, modulo=None,
                      instancia=None):
    """Asienta un intento que el sistema rechazó por un control."""
    from seguridad.models import ResultadoOperacion

    return registrar_auditoria(
        request, operacion, entidad, detalle=motivo, modulo=modulo,
        instancia=instancia, resultado=ResultadoOperacion.RECHAZADA)


def _permiso(codigo):
    from seguridad.models import Permiso

    return (Permiso.objects.filter(codigo=codigo)
            .values('nombre', 'modulo').first())


def registrar_acceso_denegado(request, *codigos):
    """Alguien quiso entrar a una pantalla o acción que su perfil no tiene.

    Lo llaman los decoradores de permisos y cada control puntual que
    devuelve "acceso denegado". El detalle dice qué quiso hacer, desde qué
    dirección y con qué perfil.
    """
    usuario = getattr(request, 'usuario_actual', None)
    datos = _permiso(codigos[0]) if codigos else None
    que = (datos['nombre'] if datos else None) or ', '.join(codigos)
    perfil = usuario.perfil_efectivo if usuario is not None else None
    return registrar_rechazo(
        request, 'ACCESO DENEGADO', None,
        f'Quiso {que[:1].lower() + que[1:]} ({", ".join(codigos)}) en '
        f'{request.path}; el perfil {perfil or "sin perfil"} no lo tiene '
        'habilitado.',
        modulo=(datos['modulo'] if datos else 'SEGURIDAD'))


def registrar_autorizacion_rechazada(request, permiso, para, identificador,
                                     motivo):
    """Una autorización con usuario y clave que no se aceptó.

    Nunca se guarda la clave tipeada: sólo quién pidió, para qué, el
    usuario que se escribió y el motivo del rechazo.
    """
    datos = _permiso(permiso)
    pidio = getattr(request, 'usuario_actual', None) if request else None
    return registrar_rechazo(
        request, 'AUTORIZACION', 'MOSTRADOR',
        f'Se pidió autorización para {para or permiso}'
        + (f' (pidió {pidio.nombre} {pidio.apellido})' if pidio is not None else '')
        + f'; se escribió el usuario "{(identificador or "")[:35]}" y se '
        f'rechazó: {motivo}.',
        modulo=(datos['modulo'] if datos else 'SEGURIDAD'))
