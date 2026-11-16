"""Autorización a distancia: la caja pide, la dueña contesta del celular (DD-94).

El problema: hay ventas que necesitan la firma de quien manda (fiarle a
alguien por primera vez, pasar su tope, un descuento mayor al de la
cajera). Si la dueña no está al lado de la caja, la fila se traba.

El camino:

1. La caja arma el ticket como siempre y toca «Pedir autorización a
   distancia». Queda una SOLICITUD con el ticket tal cual, vigente 10
   minutos.
2. A quien puede autorizarlo le llega un correo (a la titular y a cada
   persona activa cuyo perfil tenga el permiso que hace falta y le alcance
   para lo que se pide; y a la titular, si n8n está configurado, un
   WhatsApp) con qué se pide, quién lo pide, el total, un PIN de 4 dígitos
   y un enlace. El PIN y el enlace son de cada persona: el sistema sabe
   quién aprobó.
3. Ella contesta de una de tres formas:
   * abre el enlace y toca «Aprobar» (el enlace solo NO aprueba: los
     programas de correo abren los enlaces para revisarlos, y eso no puede
     contar como un sí);
   * le dicta el PIN a la cajera por mensaje o por teléfono;
   * entra al sistema y aprueba desde la bandeja (también puede hacerlo
     otra persona que tenga el permiso, con su sesión).
4. La caja, que pregunta cada 2 segundos, se entera y cobra el MISMO
   ticket. Si el ticket cambió (otro producto, otro monto), la aprobación
   no sirve: la huella del ticket es parte de lo que se aprobó.

Lo que se puede pedir así: un descuento sobre el tope, un fiado que pide
firma (sin cuenta, en mora, sobre el tope), usar el Club estando en mora y
el ajuste de stock de un producto que figura en 0 desde el mostrador.

Del enlace y del PIN se guarda sólo un hash con la clave del servidor: quien
copie la base no puede aprobar nada (el enlace y el PIN de cada persona se
derivan del de la solicitud con esa misma clave). El PIN admite 5 intentos; al quinto
error la solicitud se anula.
"""
import hashlib
import json
import logging
import secrets
import threading
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from nucleo.templatetags.cafeto import pesos
from ventas.models import EstadoSolicitud, SolicitudAutorizacion, ViaAprobacion

log = logging.getLogger(__name__)

SAL_PIN = 'cafeto.ventas.autorizacion-a-distancia.pin'
SAL_PERSONA = 'cafeto.ventas.autorizacion-a-distancia.persona'
# Quien tenga alguno de estos permisos puede contestar (además de la
# titular); si le alcanza para ESTE pedido se decide volviendo a cotizar.
PERMISOS_PARA_AUTORIZAR = ('gestionar_credito', 'autorizar_ajustes')
AJUSTE = 'ajuste'
# Una vez aprobada, la caja tiene este margen extra para cobrar (por si la
# aprobación llegó en el último segundo de los 10 minutos).
GRACIA = timedelta(minutes=5)
POR_HORA = 10


# ---------------------------------------------------------------------------
#  Huellas
# ---------------------------------------------------------------------------
def _numero(valor):
    if valor is None:
        return ''
    return format(Decimal(valor).normalize(), 'f')


def huella(items, pagos, cliente, beneficios, vence_fiado=None):
    """Un resumen del ticket: si cambia cualquier cosa que se cobra, cambia."""
    canon = {
        'c': cliente.pk if cliente is not None else None,
        'i': [[i.producto.pk, _numero(i.cantidad), _numero(i.descuento_porcentaje),
               i.motivo_descuento or ''] for i in items],
        'p': [[p.medio.pk, _numero(p.monto)] for p in pagos],
        'b': sorted(beneficios or []),
        'v': vence_fiado.isoformat() if vence_fiado else '',
    }
    return hashlib.sha256(json.dumps(canon, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_pin(solicitud_token_hash, pin):
    return salted_hmac(SAL_PIN, f'{solicitud_token_hash}|{pin}', algorithm='sha256').hexdigest()


def huella_ajuste(producto, cantidad):
    """El «ticket» de un ajuste desde el mostrador: qué producto y cuánto."""
    canon = {'a': producto.pk, 'q': _numero(cantidad)}
    return hashlib.sha256(json.dumps(canon, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _firma(token_hash, usuario_pk):
    return salted_hmac(SAL_PERSONA, f'{token_hash}|{usuario_pk}', algorithm='sha256').hexdigest()[:24]


def pin_de(token_hash, usuario_pk):
    """El PIN de 4 dígitos de una persona para una solicitud. No se guarda:
    se vuelve a calcular al validar."""
    return f'{int(_firma(token_hash, f"pin|{usuario_pk}"), 16) % 10000:04d}'


def enlace_de(token, usuario_pk):
    return f'{token}.{usuario_pk}.{_firma(_hash_token(token), usuario_pk)}'


def puede_contestar(usuario):
    """Ve la bandeja y recibe los pedidos: la titular y quien tenga alguno
    de los permisos para autorizar."""
    return usuario is not None and usuario.activo and (
        usuario.es_titular or any(usuario.puede(p) for p in PERMISOS_PARA_AUTORIZAR))


def candidatos(solicitud, puede_autorizar):
    """Las personas que pueden aprobar ESTA solicitud: activas, que no son
    quien la pidió (salvo la titular) y a quienes les alcanza."""
    from seguridad.models import Usuario

    lista = []
    for usuario in Usuario.objects.filter(activo=True).select_related('id_perfil').order_by('-es_titular', 'pk'):
        if not puede_contestar(usuario):
            continue
        if usuario.pk == solicitud.id_usuario_solicitante_id and not usuario.es_titular:
            continue
        try:
            puede_autorizar(usuario)
        except ValidationError:
            continue
        lista.append(usuario)
    return lista


def titular():
    from seguridad.models import Usuario

    return Usuario.objects.filter(es_titular=True, activo=True).first()


# ---------------------------------------------------------------------------
#  Qué hace falta autorizar
# ---------------------------------------------------------------------------
def que_hace_falta(items, pagos, operador, cliente, beneficios):
    """Qué de este ticket necesita la firma de otra persona, en palabras.

    Se cotiza como si autorizara la titular: si ni así se puede (sin stock,
    un incobrable), el error sale acá y no se molesta a nadie.
    """
    from fidelizacion import credito
    from fidelizacion.models import EstadoCredito
    from ventas import servicios as ventas

    dueña = titular()
    if dueña is None:
        raise ValidationError('No hay una titular activa en el sistema para pedirle autorización.')
    cotizacion = ventas.cotizar(items, pagos, operador=operador, autorizante=dueña,
                                cliente=cliente, beneficios=beneficios)
    motivos = []
    if any(r.pide_autorizacion for r in cotizacion.renglones):
        motivos.append('un descuento por encima de su tope')
    if cotizacion.firma_fiado is not None:
        s = credito.situacion(cliente)
        a_quien = credito.nombre(cliente)
        if cliente.estado_credito == EstadoCredito.SIN_CREDITO:
            motivos.append(f'fiarle {pesos(cotizacion.fiado)} a {a_quien} por primera vez')
        elif s['en_mora']:
            motivos.append(f'fiarle {pesos(cotizacion.fiado)} a {a_quien}, que está en mora '
                           f'({credito.aviso_de_mora(cliente, s=s)})')
        else:
            motivos.append(f'fiarle {pesos(cotizacion.fiado)} a {a_quien}, que pasaría a deber '
                           f'{pesos(s["saldo"] + cotizacion.fiado)} con un tope de {pesos(cliente.limite_credito)}')
    usa_club = bool(cotizacion.canjes or cotizacion.beneficio_cabecera
                    or any(r.beneficio for r in cotizacion.renglones))
    if (cliente is not None and usa_club and credito.situacion(cliente)['en_mora']
            and not credito.puede_gestionar(operador)):
        motivos.append(f'que {credito.nombre(cliente)} use puntos o beneficios del Club estando en mora')
    return cotizacion, motivos


# ---------------------------------------------------------------------------
#  Pedir
# ---------------------------------------------------------------------------
@transaction.atomic
def pedir(turno, operador, items, pagos, cliente, beneficios, vence_fiado, pedido, enlace_base):
    """Deja la solicitud y avisa. Devuelve ``(solicitud, pin)``; el PIN es
    para el aviso a la titular, NUNCA para la pantalla de la caja."""
    cotizacion, motivos = que_hace_falta(items, pagos, operador, cliente, beneficios)
    if not motivos:
        raise ValidationError('Este ticket no necesita autorización: ya lo podés cobrar.')
    ahora = timezone.now()
    recientes = SolicitudAutorizacion.objects.filter(
        id_usuario_solicitante=operador, fecha_solicitud__gte=ahora - timedelta(hours=1)).count()
    if recientes >= POR_HORA:
        raise ValidationError('Pediste muchas autorizaciones en la última hora. Esperá un rato o llamala.')
    # Un pedido abierto por caja: el nuevo reemplaza al anterior.
    (SolicitudAutorizacion.objects
     .filter(id_usuario_solicitante=operador, estado_solicitud=EstadoSolicitud.PENDIENTE)
     .update(estado_solicitud=EstadoSolicitud.ANULADA, fecha_resolucion=ahora))
    token = secrets.token_urlsafe(32)
    pin = f'{secrets.randbelow(10000):04d}'
    token_hash = _hash_token(token)
    quien = f'{operador.nombre} {operador.apellido}'.strip()
    motivo = (f'{quien} pide ' + ' y '.join(motivos))[:200]
    solicitud = SolicitudAutorizacion.objects.create(
        id_usuario_solicitante=operador, id_turno_caja=turno, id_cliente=cliente,
        motivo_solicitud=motivo, monto_solicitud=cotizacion.total, pedido=pedido,
        huella_operacion=huella(items, pagos, cliente, beneficios, vence_fiado),
        token_hash=token_hash, pin_hash=_hash_pin(token_hash, pin),
        fecha_solicitud=ahora,
        fecha_vencimiento=ahora + timedelta(minutes=SolicitudAutorizacion.VIGENCIA_MINUTOS))
    renglones = _renglones(cotizacion)
    transaction.on_commit(lambda: avisar(solicitud, token, pin, enlace_base, renglones))
    return solicitud, pin


@transaction.atomic
def pedir_ajuste(turno, operador, producto, cantidad, pedido, enlace_base):
    """Lo mismo para el ajuste de stock desde el mostrador (DD-90): el
    producto figura en 0, la cajera lo tiene en la mano y quien puede
    aprobar ajustes no está."""
    ahora = timezone.now()
    recientes = SolicitudAutorizacion.objects.filter(
        id_usuario_solicitante=operador, fecha_solicitud__gte=ahora - timedelta(hours=1)).count()
    if recientes >= POR_HORA:
        raise ValidationError('Pediste muchas autorizaciones en la última hora. Esperá un rato o llamala.')
    (SolicitudAutorizacion.objects
     .filter(id_usuario_solicitante=operador, estado_solicitud=EstadoSolicitud.PENDIENTE)
     .update(estado_solicitud=EstadoSolicitud.ANULADA, fecha_resolucion=ahora))
    token = secrets.token_urlsafe(32)
    pin = f'{secrets.randbelow(10000):04d}'
    token_hash = _hash_token(token)
    quien = f'{operador.nombre} {operador.apellido}'.strip()
    motivo = (f'{quien} pide cargar {producto.cantidad_con_unidad(cantidad)} de {producto.nombre_producto}, '
              'que figura sin stock y está en la góndola')[:200]
    solicitud = SolicitudAutorizacion.objects.create(
        id_usuario_solicitante=operador, id_turno_caja=turno, id_cliente=None,
        motivo_solicitud=motivo, monto_solicitud=Decimal('0'), pedido=dict(pedido, tipo=AJUSTE),
        huella_operacion=huella_ajuste(producto, cantidad),
        token_hash=token_hash, pin_hash=_hash_pin(token_hash, pin), fecha_solicitud=ahora,
        fecha_vencimiento=ahora + timedelta(minutes=SolicitudAutorizacion.VIGENCIA_MINUTOS))
    renglones = [f'{producto.nombre_producto}: de 0 a {producto.cantidad_con_unidad(cantidad)}']
    transaction.on_commit(lambda: avisar(solicitud, token, pin, enlace_base, renglones))
    return solicitud, pin


def control_de(solicitud):
    """Cómo se decide si a una persona le alcanza para ESTA solicitud."""
    pedido = solicitud.pedido or {}
    if pedido.get('tipo') == AJUSTE:
        def control(usuario):
            if usuario is None or not (usuario.es_titular or usuario.puede('autorizar_ajustes')):
                raise ValidationError('Para esto hace falta poder aprobar ajustes de stock.')
        return control
    from ventas.views.autorizaciones import _puede_autorizar
    return _puede_autorizar(solicitud)


def _renglones(cotizacion):
    return [f'{r.producto.nombre_producto} × {_numero(r.cantidad)} = {pesos(r.subtotal)}'
            for r in cotizacion.renglones]


def avisar(solicitud, token, pin, enlace_base, renglones):
    """Un correo a cada persona que puede aprobar (con SU enlace y SU PIN)
    y, si está configurado, el webhook de n8n para el WhatsApp de la
    titular.

    Nunca frena a la caja: si un correo no sale, la cajera igual puede
    llamar y que aprueben desde la bandeja o le dicten el PIN.
    """
    from django.core.mail import send_mail
    from django.template.loader import render_to_string

    vence = timezone.localtime(solicitud.fecha_vencimiento)
    base = enlace_base.rstrip('/')
    titular_pin, titular_enlace = None, None
    for persona in candidatos(solicitud, control_de(solicitud)):
        if persona.es_titular:
            su_pin, su_token = pin, token
            titular_pin = pin
        else:
            su_pin, su_token = pin_de(solicitud.token_hash, persona.pk), enlace_de(token, persona.pk)
        enlace = base + reverse('ventas:autorizar_a_distancia', args=[su_token])
        if persona.es_titular:
            titular_enlace = enlace
        if not persona.correo:
            continue
        contexto = {'solicitud': solicitud, 'pin': su_pin, 'enlace': enlace, 'vence': vence,
                    'renglones': renglones, 'titular': persona}
        asunto = f'Cafeto · ¿Autorizás? {pesos(solicitud.monto_solicitud) if solicitud.monto_solicitud else "Ajuste de stock"} — PIN {su_pin}'
        texto = render_to_string('ventas/correo_autorizacion.txt', contexto)
        html = render_to_string('ventas/correo_autorizacion.html', contexto)

        def mandar(asunto=asunto, texto=texto, html=html, destino=persona.correo):
            try:
                send_mail(asunto, texto, settings.DEFAULT_FROM_EMAIL, [destino], html_message=html)
            except Exception:                               # noqa: BLE001
                log.exception('No se pudo mandar el pedido de autorización N° %s', solicitud.pk)
        # El servidor de correo puede tardar varios segundos: la caja no espera.
        en_segundo_plano(mandar)
    url = getattr(settings, 'CAFETO_N8N_WEBHOOK_AUTORIZACION', '')
    if url and titular_pin:
        datos = {'solicitud': solicitud.pk, 'pedido': solicitud.motivo_solicitud,
                 'total': pesos(solicitud.monto_solicitud), 'pin': titular_pin, 'enlace': titular_enlace,
                 'vence': vence.strftime('%H:%M'), 'renglones': renglones}
        en_segundo_plano(_webhook, url, datos)


def en_segundo_plano(funcion, *args):
    threading.Thread(target=funcion, args=args, daemon=True).start()


def _webhook(url, datos):
    import urllib.request

    pedido = urllib.request.Request(
        url, data=json.dumps(datos).encode(), method='POST',
        headers={'Content-Type': 'application/json',
                 'X-Cafeto-Token': getattr(settings, 'CAFETO_N8N_TOKEN', '')})
    try:
        urllib.request.urlopen(pedido, timeout=5).close()
    except Exception:                                       # noqa: BLE001
        log.warning('n8n no recibió el pedido de autorización %s', datos.get('solicitud'))


# ---------------------------------------------------------------------------
#  Contestar
# ---------------------------------------------------------------------------
def por_token(token):
    """``(solicitud, persona)`` del enlace, o ``(None, None)``. El enlace de
    la titular es el token solo; el de otra persona lleva su número y una
    firma que sólo puede calcular el servidor."""
    from seguridad.models import Usuario

    if not token or len(token) > 160:
        return None, None
    crudo, _, resto = token.partition('.')
    solicitud = (SolicitudAutorizacion.objects.select_related('id_usuario_solicitante', 'id_cliente')
                 .filter(token_hash=_hash_token(crudo)).first())
    if solicitud is None:
        return None, None
    if not resto:
        return solicitud, titular()
    numero, _, firma = resto.partition('.')
    if not numero.isdigit() or not constant_time_compare(firma, _firma(solicitud.token_hash, int(numero))):
        return None, None
    persona = Usuario.objects.filter(pk=int(numero), activo=True).first()
    return (solicitud, persona) if persona is not None else (None, None)


def _vencer_si_corresponde(solicitud):
    if solicitud.estado_solicitud == EstadoSolicitud.PENDIENTE and solicitud.vencida:
        SolicitudAutorizacion.objects.filter(pk=solicitud.pk).update(
            estado_solicitud=EstadoSolicitud.ANULADA, fecha_resolucion=timezone.now())
        solicitud.estado_solicitud = EstadoSolicitud.ANULADA
    return solicitud


def estado(solicitud):
    """Lo que la caja necesita saber cuando pregunta."""
    solicitud = _vencer_si_corresponde(solicitud)
    restan = max(0, int((solicitud.fecha_vencimiento - timezone.now()).total_seconds()))
    quien = solicitud.id_usuario_autorizante
    return {'estado': solicitud.estado_solicitud, 'restan': restan,
            'autorizo': f'{quien.nombre} {quien.apellido}'.strip() if quien else None,
            'via': solicitud.get_via_aprobacion_display() if solicitud.via_aprobacion else None}


@transaction.atomic
def resolver(solicitud, usuario, aprobar, via, puede_autorizar):
    """Aprueba o rechaza. ``puede_autorizar(usuario)`` levanta
    ``ValidationError`` si esa persona no alcanza para lo que se pide (se
    vuelve a cotizar el ticket con ella como autorizante)."""
    solicitud = SolicitudAutorizacion.objects.select_for_update().get(pk=solicitud.pk)
    _vencer_si_corresponde(solicitud)
    if solicitud.estado_solicitud != EstadoSolicitud.PENDIENTE:
        raise ValidationError(_ya_no_esta(solicitud))
    if (usuario.pk == solicitud.id_usuario_solicitante_id and not usuario.es_titular):
        raise ValidationError('La autorización la tiene que dar otra persona, no quien la pidió.')
    ahora = timezone.now()
    if aprobar:
        puede_autorizar(usuario)
        solicitud.estado_solicitud = EstadoSolicitud.APROBADA
        solicitud.id_usuario_autorizante = usuario
        solicitud.via_aprobacion = via
    else:
        solicitud.estado_solicitud = EstadoSolicitud.RECHAZADA
    solicitud.fecha_resolucion = ahora
    solicitud.save(update_fields=['estado_solicitud', 'id_usuario_autorizante', 'via_aprobacion',
                                  'fecha_resolucion'])
    return solicitud


def _ya_no_esta(solicitud):
    return {
        EstadoSolicitud.APROBADA: 'Este pedido ya estaba aprobado.',
        EstadoSolicitud.USADA: 'Este pedido ya se usó en una venta.',
        EstadoSolicitud.RECHAZADA: 'Este pedido ya se había rechazado.',
    }.get(solicitud.estado_solicitud, 'Este pedido venció o la caja lo canceló: que lo pidan de nuevo.')


def verificar_pin(solicitud, operador, pin, puede_autorizar):
    """La cajera escribe el PIN que le dictó la dueña. Aprueba en nombre de
    la titular. Cada error cuenta; al quinto la solicitud se anula.

    No es atómica a propósito: el intento fallido se tiene que guardar
    aunque quien llama devuelva un error.
    """
    with transaction.atomic():
        solicitud = SolicitudAutorizacion.objects.select_for_update().get(pk=solicitud.pk)
        if solicitud.id_usuario_solicitante_id != operador.pk:
            raise ValidationError('Ese pedido no es tuyo.')
        _vencer_si_corresponde(solicitud)
        if solicitud.estado_solicitud != EstadoSolicitud.PENDIENTE:
            raise ValidationError(_ya_no_esta(solicitud))
        pin = ''.join(c for c in str(pin or '') if c.isdigit())
        quien = None
        if len(pin) == 4 and constant_time_compare(_hash_pin(solicitud.token_hash, pin), solicitud.pin_hash):
            quien = titular()
        elif len(pin) == 4:
            quien = next((p for p in candidatos(solicitud, puede_autorizar)
                          if not p.es_titular and constant_time_compare(pin_de(solicitud.token_hash, p.pk), pin)),
                         None)
        if quien is None:
            solicitud.intentos_pin += 1
            cambios = ['intentos_pin']
            if solicitud.intentos_pin >= SolicitudAutorizacion.INTENTOS_DE_PIN:
                solicitud.estado_solicitud = EstadoSolicitud.ANULADA
                solicitud.fecha_resolucion = timezone.now()
                cambios += ['estado_solicitud', 'fecha_resolucion']
            solicitud.save(update_fields=cambios)
            quedan = SolicitudAutorizacion.INTENTOS_DE_PIN - solicitud.intentos_pin
            if quedan <= 0:
                return None, 'El PIN no es correcto y ya se probó 5 veces: el pedido se anuló. Pedí uno nuevo.'
            return None, f'El PIN no es correcto. Te quedan {quedan} intento{"s" if quedan != 1 else ""}.'
    return resolver(solicitud, quien, True, ViaAprobacion.PIN, puede_autorizar), None


# ---------------------------------------------------------------------------
#  Usar en la venta
# ---------------------------------------------------------------------------
def aprobada_para(id_solicitud, operador, huella_del_ticket, bloquear=False):
    """La solicitud aprobada que cubre este ticket, o ``ValidationError``."""
    consulta = SolicitudAutorizacion.objects.select_related('id_usuario_autorizante')
    if bloquear:
        consulta = consulta.select_for_update(of=('self',))
    solicitud = consulta.filter(pk=id_solicitud if isinstance(id_solicitud, int) else 0).first()
    if solicitud is None or solicitud.id_usuario_solicitante_id != operador.pk:
        raise ValidationError('La autorización a distancia no corresponde a esta caja.')
    if solicitud.estado_solicitud != EstadoSolicitud.APROBADA:
        raise ValidationError(_ya_no_esta(solicitud) if solicitud.estado_solicitud != EstadoSolicitud.PENDIENTE
                              else 'Todavía no contestaron el pedido de autorización.')
    if timezone.now() > solicitud.fecha_vencimiento + GRACIA:
        raise ValidationError('La autorización venció: pedí una nueva.')
    if solicitud.huella_operacion != huella_del_ticket:
        raise ValidationError('El ticket cambió después de que lo autorizaran: '
                              'volvé a dejarlo como estaba o pedí una autorización nueva.')
    return solicitud


def marcar_usada(solicitud, venta=None):
    """Una aprobación se usa una sola vez: en la venta o en el ajuste."""
    SolicitudAutorizacion.objects.filter(pk=solicitud.pk).update(
        estado_solicitud=EstadoSolicitud.USADA, id_venta=venta)


def vencer_las_viejas():
    """Las pendientes a las que se les pasó la hora sin que nadie
    preguntara quedan ANULADAS: así no se esconden ni como pendientes ni
    como contestadas."""
    return (SolicitudAutorizacion.objects
            .filter(estado_solicitud=EstadoSolicitud.PENDIENTE, fecha_vencimiento__lte=timezone.now())
            .update(estado_solicitud=EstadoSolicitud.ANULADA, fecha_resolucion=F('fecha_vencimiento')))


def pendientes():
    """Lo que está esperando respuesta, para la bandeja."""
    ahora = timezone.now()
    return (SolicitudAutorizacion.objects
            .filter(estado_solicitud=EstadoSolicitud.PENDIENTE, fecha_vencimiento__gt=ahora)
            .select_related('id_usuario_solicitante', 'id_cliente', 'id_turno_caja__id_caja_registradora')
            .order_by('fecha_solicitud'))
