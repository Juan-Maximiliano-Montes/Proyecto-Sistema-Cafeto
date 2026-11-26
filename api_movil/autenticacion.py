"""Quién habla desde el celular: el ingreso del equipo, su token y sus permisos.

El celular no tiene la cookie de la web: manda ``Authorization: Token <t>``
en cada pedido, y cada vista vuelve a controlar el token y el permiso.

El token
--------
Va FIRMADO con la ``SECRET_KEY`` (``django.core.signing``), vence a las 12
horas y lleva tres cosas: el usuario, el ``jti`` de su sesión móvil y la
**huella de la clave**.

* La sesión móvil (``SesionMovil``) es una fila aparte de SESIONES, para
  que entrar desde el celular no cierre la web. Como en la web (DD-01) hay
  una sola abierta por usuario: entrar de nuevo cierra la anterior y salir
  la cierra. Un token de una sesión cerrada no vale aunque la firma esté
  bien.
* La huella es la misma idea con la que Django invalida sus sesiones: si
  la clave cambia —la persona la cambió, se la restablecieron o le dieron
  una provisoria—, deja de coincidir y TODOS sus tokens dejan de valer. Dar
  de baja al usuario también los corta.

El ingreso
----------
Pasa por los mismos controles que el login de la web, con los mismos
servicios y el mismo libro (``seguridad.servicios``): bloqueo progresivo
por reintentos, INTENTOS_ACCESO, la alerta a la Administradora y el asiento
del ingreso en el Registro de Actividad. El bloqueo es de la CUENTA y no
del canal: quien prueba claves en el celular queda frenado también en la
web, y al revés.
"""
import re
import secrets
from datetime import timedelta
from functools import wraps

from django.contrib.auth.hashers import make_password
from django.core import signing
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.csrf import csrf_exempt

from api_movil import respuestas
from api_movil.models import SesionMovil
from nucleo import contexto
from seguridad.models import Usuario
from seguridad.servicios import (
    OPERACION_INGRESO_MOVIL,
    describir_espera,
    espera_por_bloqueo,
    intentos_antes_del_bloqueo,
    ip_del_request,
    registrar_acceso_denegado,
    registrar_auditoria,
    registrar_intento_fallido,
)

SAL = 'cafeto.api_movil.token'
DURACION = timedelta(hours=12)
# Cada cuánto se anota el último uso de la sesión móvil: anotarlo en cada
# pedido sería una escritura por pantalla (la web usa un minuto).
RESOLUCION_ULTIMO_USO = timedelta(minutes=5)

# Los permisos que la app usa para mostrar cada tarea. Es sólo la lista que
# se le informa al entrar: el servidor vuelve a controlar en cada pedido.
PERMISOS_MOVIL = (
    'ver_productos', 'ver_stock', 'contar_stock', 'registrar_merma',
    'registrar_traspaso', 'registrar_produccion', 'autorizar_ajustes',
    'imprimir_ofertas', 'registrar_compra',
    'ver_ventas', 'ver_caja', 'gestionar_ordenes', 'ver_egresos',
    'ver_auditoria', 'ver_reportes',
    'ver_marcas', 'crear_marca', 'editar_marca', 'eliminar_marca',
    'ver_rubros', 'crear_rubro', 'editar_rubro', 'eliminar_rubro',
)

# Para ``para_el_equipo``: alcanza con tener la sesión, sin otro permiso.
CON_SESION = ()

# El largo máximo del campo "DNI o Usuario" del login de la web.
LARGO_IDENTIFICADOR = 70

NO_COINCIDEN = 'El usuario o la contraseña no coinciden.'
SESION_VENCIDA = 'Tu sesión venció. Volvé a entrar.'
SIN_PERMISO = 'Tu usuario no tiene permiso para hacer esto.'


def desde_el_celular(request):
    """Cómo queda dicho en la auditoría de dónde vino la operación.

    Desde el celular no hay sesión web, así que el asiento no tiene
    ``id_sesion`` (la columna admite NULL justamente para lo que no viene
    de la web): la IP y la sesión móvil van en el detalle.
    """
    sesion = getattr(request, 'sesion_movil', None)
    cual = f', sesión móvil N° {sesion.pk}' if sesion is not None else ''
    return f'Desde la app móvil (IP {ip_del_request(request)}{cual}).'


# ---------------------------------------------------------------------------
#  EL TOKEN Y LA SESIÓN MÓVIL
# ---------------------------------------------------------------------------
def huella(usuario):
    """Un resumen de la clave actual: cambia cuando la clave cambia.

    Es un HMAC del hash (no el hash): sin la ``SECRET_KEY`` no dice nada
    de la clave.
    """
    return salted_hmac(SAL, usuario.clave, algorithm='sha256').hexdigest()[:32]


def abrir_sesion_movil(usuario, ip='0.0.0.0'):
    """Abre la sesión del celular de ``usuario`` y devuelve ``(sesion, token)``.

    Cierra antes la que tuviera abierta (una sola por usuario, como DD-01).
    La fila del usuario se bloquea mientras tanto: dos ingresos simultáneos
    de la misma persona no pueden abrir dos sesiones.
    """
    Usuario.objects.select_for_update().filter(pk=usuario.pk).first()
    ahora = timezone.now()
    SesionMovil.objects.filter(id_usuario=usuario, revocada_en__isnull=True).update(
        revocada_en=ahora, motivo_revocacion='NUEVO_INGRESO')
    sesion = SesionMovil.objects.create(
        id_usuario=usuario, jti=secrets.token_urlsafe(24), creada=ahora,
        ultima_actividad=ahora, ip_origen=(ip or '0.0.0.0')[:45])
    token = signing.dumps({'u': usuario.pk, 'j': sesion.jti, 'h': huella(usuario)},
                          salt=SAL)
    return sesion, token


def emitir_token(usuario, ip='0.0.0.0'):
    """Una sesión móvil nueva para ``usuario`` y su token."""
    return abrir_sesion_movil(usuario, ip)[1]


def sesion_del_token(request):
    """``(usuario, sesion)`` del token del pedido, o ``(None, None)``.

    No vale si falta, si no es ``Token <t>``, si la firma no cierra (lo
    tocaron o lo firmó otro), si pasaron más de 12 horas, si su sesión
    móvil se cerró (salió, o entró de nuevo), si el usuario está dado de
    baja o tiene que cambiar su clave provisoria, o si la clave cambió.
    """
    nada = (None, None)
    esquema, _, token = request.headers.get('Authorization', '').partition(' ')
    token = token.strip()
    if esquema.lower() != 'token' or not token:
        return nada
    try:
        datos = signing.loads(token, salt=SAL, max_age=DURACION)
    except signing.BadSignature:        # incluye el token vencido
        return nada
    if (not isinstance(datos, dict) or type(datos.get('u')) is not int
            or type(datos.get('j')) is not str):
        return nada
    ahora = timezone.now()
    sesion = (SesionMovil.objects
              .select_related('id_usuario__id_perfil', 'id_usuario__id_perfil_temporal')
              .filter(jti=datos['j'], id_usuario_id=datos['u'],
                      revocada_en__isnull=True, creada__gt=ahora - DURACION)
              .first())
    if sesion is None:
        return nada
    usuario = sesion.id_usuario
    if not usuario.activo or usuario.debe_cambiar_clave:
        return nada
    if not constant_time_compare(str(datos.get('h', '')), huella(usuario)):
        return nada
    if ahora - sesion.ultima_actividad >= RESOLUCION_ULTIMO_USO:
        SesionMovil.objects.filter(pk=sesion.pk).update(ultima_actividad=ahora)
        sesion.ultima_actividad = ahora
    return usuario, sesion


def usuario_del_token(request):
    """El usuario dueño del token del pedido, o ``None`` si el token no vale."""
    return sesion_del_token(request)[0]


def cerrar_sesion_movil(request):
    """Cierra la sesión móvil del token, si todavía estaba abierta."""
    _, sesion = sesion_del_token(request)
    if sesion is not None:
        SesionMovil.objects.filter(pk=sesion.pk, revocada_en__isnull=True).update(
            revocada_en=timezone.now(), motivo_revocacion='SALIDA')
    return sesion


# ---------------------------------------------------------------------------
#  EL INGRESO
# ---------------------------------------------------------------------------
class IngresoRechazado(Exception):
    """El ingreso no se concede: con qué estado HTTP y qué decirle a la persona.

    ``espera`` (un ``timedelta``) viene cuando el rechazo es por bloqueo:
    la vista la manda en ``Retry-After``.
    """

    def __init__(self, estado, detalle, espera=None):
        super().__init__(detalle)
        self.estado = estado
        self.detalle = detalle
        self.espera = espera


def _bloqueado(espera):
    return IngresoRechazado(
        429, 'Demasiados intentos fallidos. Por seguridad, el ingreso queda '
             f'bloqueado: probá de nuevo en {describir_espera(espera)}.',
        espera=espera)


def ingresar(request, identificador, clave):
    """Comprueba el usuario y la clave, abre la sesión móvil y devuelve
    ``(usuario, token)``.

    Mismo orden y mismos servicios que ``seguridad.views.login_view``:

    * se busca por DNI **o** por nombre de usuario, como en la web;
    * mientras dura un bloqueo, la clave ni se compara;
    * "no existe" y "clave mala" dan el mismo mensaje y el mismo trabajo
      (se hashea igual): el celular no sirve para averiguar qué usuarios
      existen;
    * cada rechazo queda en INTENTOS_ACCESO, que es de dónde sale el
      bloqueo y la alerta a la Administradora.

    Levanta ``IngresoRechazado`` si no corresponde dejarlo entrar.
    """
    ip = ip_del_request(request)
    dni = (re.sub(r'\D', '', identificador)
           if re.fullmatch(r'[\d.\s]+', identificador) else None)
    if dni and 7 <= len(dni) <= 8:
        identificador = dni
    usuario = (Usuario.objects.select_related('id_perfil', 'id_perfil_temporal')
               .filter(Q(dni_usuario=identificador)
                       | Q(nombre_usuario__iexact=identificador))
               .first())

    espera = espera_por_bloqueo(identificador, usuario, ip)
    if espera is not None:
        registrar_intento_fallido(request, identificador, usuario,
                                  'CUENTA_BLOQUEADA')
        raise _bloqueado(espera)

    if usuario is None:
        make_password(clave)
        correcta = False
    else:
        correcta = usuario.verificar_clave(clave)

    if not correcta:
        registrar_intento_fallido(request, identificador, usuario,
                                  'CLAVE_INCORRECTA')
        espera = espera_por_bloqueo(identificador, usuario, ip)
        if espera is not None:
            # Este intento fue el que activó el bloqueo: se dice ya.
            raise _bloqueado(espera)
        aviso = NO_COINCIDEN
        quedan = intentos_antes_del_bloqueo(identificador, usuario, ip)
        if 0 < quedan <= 2:
            plural = quedan > 1
            aviso += (f' Te queda{"n" if plural else ""} {quedan} '
                      f'intento{"s" if plural else ""} antes de un bloqueo '
                      'temporal.')
        raise IngresoRechazado(401, aviso)

    if not usuario.activo:
        registrar_intento_fallido(request, identificador, usuario,
                                  'USUARIO_INACTIVO')
        raise IngresoRechazado(
            403, 'Tu usuario está dado de baja. Consultá con el administrador.')
    if usuario.clave_provisoria_vencida:
        # Clave correcta pero provisoria y de hace más de 24 horas: tampoco
        # abre la web. No es un intento fallido, no suma al bloqueo.
        raise IngresoRechazado(
            403, 'Esa contraseña provisoria venció (dura 24 horas). Pedile al '
                 'administrador que te la restablezca.')
    if usuario.debe_cambiar_clave:
        raise IngresoRechazado(
            403, 'Tenés que cambiar tu contraseña provisoria desde la web '
                 'antes de entrar desde el celular.')

    request.usuario_actual = usuario
    sesion, token = abrir_sesion_movil(usuario, ip)
    request.sesion_movil = sesion
    # La foto es la de la sesión que se abre: desde dónde y hasta cuándo
    # vale (nunca el token).
    registrar_auditoria(request, OPERACION_INGRESO_MOVIL, None,
                        modulo='SEGURIDAD',
                        nuevo={'canal': 'APP_MOVIL',
                               'id_sesion_movil': sesion.pk,
                               'ip_origen': sesion.ip_origen,
                               'fecha_inicio': sesion.creada.isoformat(),
                               'fecha_expiracion': (sesion.creada + DURACION).isoformat()},
                        detalle=f'Ingresó desde la app móvil (IP {ip}, sesión '
                                f'móvil N° {sesion.pk}).')
    return usuario, token


def datos_de_sesion(usuario, token):
    """Lo que la app guarda al entrar: el token y a quién representa."""
    return {
        'token': token,
        'usuario': {
            'nombre': usuario.nombre,
            'apellido': usuario.apellido,
            'perfil': usuario.perfil_efectivo.nombre,
            'es_administrador': usuario.es_administrador,
            'es_titular': usuario.es_titular,
            'permisos': [codigo for codigo in PERMISOS_MOVIL
                         if usuario.puede(codigo)],
        },
    }


# ---------------------------------------------------------------------------
#  LAS PUERTAS DE CADA VISTA
# ---------------------------------------------------------------------------
# Las vistas de la API no usan cookies (el token viaja en un encabezado que
# el navegador no agrega solo), así que el CSRF no aplica: se eximen todas.
def publica(vista):
    """Vista del catálogo público: sólo lectura, sin token."""
    @csrf_exempt
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if request.method != 'GET':
            return respuestas.metodo_no_permitido(('GET',))
        return vista(request, *args, **kwargs)
    return envoltura


def para_el_equipo(**permiso_por_metodo):
    """Vista del equipo: token válido y el permiso que pide cada método.

    ``@para_el_equipo(GET='ver_marcas', POST='crear_marca')``. Una tupla
    pide ALGUNO de esos permisos (como ``requiere_alguno`` en la web), y
    ``CON_SESION`` sólo pide estar adentro. En orden:

    * un método que no está en la lista: 405;
    * sin token válido: 401 (la app cierra la sesión del equipo);
    * sin el permiso: 403, y el intento queda RECHAZADO en el Registro de
      Actividad, igual que en la web (DD-62).

    La vista corre con el usuario en el contexto del pedido
    (``contexto.usar``): lo que guarde queda sellado a su nombre (DD-17).
    """
    permitidos = tuple(permiso_por_metodo)

    def decorador(vista):
        @csrf_exempt
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if request.method not in permiso_por_metodo:
                return respuestas.metodo_no_permitido(permitidos)
            codigos = permiso_por_metodo[request.method]
            if isinstance(codigos, str):
                codigos = (codigos,)
            usuario, sesion = sesion_del_token(request)
            if usuario is None:
                return respuestas.error(SESION_VENCIDA, 401)
            request.usuario_actual = usuario
            request.sesion_movil = sesion
            if codigos and not any(usuario.tiene_permiso(c) for c in codigos):
                registrar_acceso_denegado(request, *codigos)
                return respuestas.error(SIN_PERMISO, 403)
            with contexto.usar(usuario):
                return vista(request, *args, **kwargs)
        return envoltura
    return decorador
