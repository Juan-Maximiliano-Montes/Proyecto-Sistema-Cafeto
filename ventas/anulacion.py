"""Anulación de una venta (Hito 4).

Anular no es lo mismo que cambiar (``ventas.cambios``):

* un **cambio** o una **devolución** parte de una venta que existió: el
  cliente se llevó la mercadería y después trae algo. La venta sigue en
  pie y el cambio se registra aparte;
* una **anulación** dice que la venta NO debió existir —el pago fue
  rechazado, se cargó mal, se cobró dos veces, el cliente se arrepintió
  antes de irse—. La venta pasa a ANULADA con fecha, usuario y motivo, y
  todo lo que movió vuelve atrás, asiento por asiento.

Nada se borra (sin baja física): la venta queda con su número y sus
renglones, y cada libro suma un asiento ANULACION que compensa al
original. Así el kárdex, el arqueo y el estado de cuenta del cliente
siguen explicando cada movimiento.

Cuándo se puede, y por qué:

* sólo con el turno de la venta **abierto**: la plata todavía está en ese
  cajón y el arqueo no se cerró. Una vez cerrado el turno, lo que vuelve
  es una devolución (``ventas.cambios``), que sale del turno de hoy;
* sólo si la venta **no tuvo cambios**: si ya volvió algo, anular
  devolvería dos veces lo mismo;
* la AUTORIZA quien tiene el permiso ``anular_venta`` (Encargado y
  Administrador) y **no hizo la venta** (DD-61): quien cobró no puede
  anular su propio cobro, ni siquiera siendo Encargado;
* **salvo el titular** (DD-69): la segregación de funciones protege a la
  dueña del error o el fraude de otro, y nadie se defrauda a sí mismo. El
  titular puede autorizar la anulación de su propia venta, pero tiene que
  confirmarla con su usuario y su clave (una reautenticación, como pide
  NIST SP 800-63B para lo sensible) y el registro lo deja dicho.

Quién la pide y quién la autoriza (DD-61). En el mostrador, el cajero que
se equivocó la PIDE desde Cambios y la encargada la AUTORIZA ahí mismo, con
su usuario y su clave (como un descuento sobre el tope o una devolución de
plata). Desde la consulta de Ventas, la encargada puede pedirla y
autorizarla ella sola, salvo que la venta sea suya: entonces hace falta la
clave de otra persona. La venta guarda los dos nombres.
"""
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from nucleo.templatetags.cafeto import pesos
from stock.models import MovimientoStock, TipoMovimientoStock
from ventas import caja as servicio_caja
from ventas.models import (
    EstadoVenta,
    MotivoAnulacion,
    MovimientoCaja,
    TipoMovimientoCaja,
    TurnoCaja,
    Venta,
)
from nucleo.textos import pluralizar

PERMISO = 'anular_venta'


@dataclass
class Anulacion:
    """Lo que se revirtió, para avisarlo en pantalla y dejarlo en el log."""
    venta: object
    efectivo: object = None             # lo que salió del cajón (positivo)
    deuda_cancelada: object = None      # lo que dejó de deber en cuenta corriente
    renglones_stock: int = 0
    puntos: object = None               # fidelizacion.puntos.Reverso
    medios_externos: list = field(default_factory=list)

    @property
    def resumen(self):
        partes = []
        if self.renglones_stock:
            partes.append(f"volvió al stock lo de {pluralizar(self.renglones_stock, 'asiento')}")
        if self.efectivo:
            partes.append(f'salieron {pesos(self.efectivo)} del cajón')
        if self.deuda_cancelada:
            partes.append(f'se le descontaron {pesos(self.deuda_cancelada)} de la cuenta corriente')
        if self.puntos is not None and self.puntos.hubo:
            if self.puntos.acumulacion:
                partes.append(f'se descontaron {self.puntos.acumulacion} '
                              'puntos ganados')
            if self.puntos.canjes:
                partes.append(f'se devolvieron {self.puntos.canjes} puntos '
                              'canjeados')
        return '; '.join(partes)


def motivo_de(venta):
    """El motivo legible, con el detalle si lo hay."""
    if not venta.anulada:
        return ''
    texto = venta.get_motivo_anulacion_display()
    if venta.detalle_anulacion:
        texto += f': {venta.detalle_anulacion}'
    return texto


def puede_autorizar(usuario, venta):
    """Si ``usuario`` alcanza para anular ``venta`` sin pedirle la clave a
    nadie: tiene el permiso y no es quien la vendió."""
    return usuario.puede(PERMISO) and usuario.pk != venta.id_usuario_id


def es_titular_sobre_su_venta(usuario, venta):
    """El titular frente a una venta suya: la puede anular, confirmando con
    su clave (DD-69)."""
    return (usuario is not None and getattr(usuario, 'es_titular', False)
            and usuario.pk == venta.id_usuario_id)


class AnulacionRechazada(ValidationError):
    """La anulación chocó con un control, no con un dato mal cargado.

    Turno cerrado, venta con cambios, quien autoriza sin permiso o siendo
    quien vendió: eso se asienta en el Registro de Actividad con resultado
    RECHAZADA (DD-62). Un motivo sin elegir es un error de carga y no.
    """


def por_que_no(venta):
    """``None`` si la venta se puede anular; si no, la razón en castellano.

    Mira la venta, no a la persona (eso lo resuelve quién autoriza). La
    pantalla la usa para mostrar u ocultar el formulario; ``anular`` la
    vuelve a mirar con la venta bloqueada.
    """
    if venta.anulada:
        return 'La venta ya está anulada.'
    if not venta.id_turno_caja.abierto:
        return (f'El turno de la venta ({venta.id_turno_caja.id_caja_registradora}'
                f', N° {venta.id_turno_caja_id}) ya se cerró: si el cliente '
                'devuelve algo, registralo como cambio o devolución.')
    if venta.cambios.exists():
        return ('La venta ya tuvo cambios o devoluciones: lo que quede se '
                'resuelve con otra devolución, no anulándola.')
    return None


def anular(venta, solicitante, motivo, detalle=None, autorizante=None):
    """Anula la venta y revierte stock, caja y puntos. Devuelve ``Anulacion``.

    ``solicitante`` es quien la pide (el usuario de la sesión).
    ``autorizante`` es quien puso su usuario y su clave en ese equipo
    (``seguridad.servicios.autorizar_con_clave``); sin él, autoriza el
    mismo solicitante, si puede.

    Todo en una transacción: si cualquier reverso falla (por ejemplo, ya
    no queda en el cajón el efectivo que hay que devolver), la venta queda
    como estaba.
    """
    with transaction.atomic():
        return _anular(venta, solicitante, motivo, detalle, autorizante)


def _anular(venta, solicitante, motivo, detalle, autorizante):
    # Mismo orden de bloqueo que la venta y el cambio (turno, venta): dos
    # operaciones simultáneas sobre la misma venta se esperan en lugar de
    # trabarse.
    turno = TurnoCaja.objects.select_for_update().get(pk=venta.id_turno_caja_id)
    venta = Venta.objects.select_for_update().get(pk=venta.pk)
    venta.id_turno_caja = turno

    if motivo not in MotivoAnulacion.values:
        raise ValidationError('Elegí el motivo de la anulación.')
    detalle = (detalle or '').strip() or None
    if detalle and len(detalle) > 120:
        raise ValidationError('El detalle admite hasta 120 caracteres.')
    if motivo == MotivoAnulacion.OTRO and not detalle:
        raise ValidationError('Contá por qué se anula la venta.')
    razon = por_que_no(venta)
    if razon:
        raise AnulacionRechazada(razon)
    usuario = autorizante or solicitante
    if not usuario.puede(PERMISO):
        if autorizante is None:
            raise AnulacionRechazada(
                'Anular una venta lo autoriza el Encargado o la '
                'Administración, con su usuario y su clave.')
        raise AnulacionRechazada(
            f'{usuario.nombre} {usuario.apellido} no tiene permiso para '
            'anular ventas.')
    if usuario.pk == venta.id_usuario_id and not (
            autorizante is not None and es_titular_sobre_su_venta(usuario, venta)):
        raise AnulacionRechazada(
            'La anulación la autoriza otra persona, no quien hizo la venta: '
            'pedí que la autorice alguien más con su usuario y su clave.')

    resultado = Anulacion(venta=venta)

    # 1. Stock: cada salida de la venta vuelve al mismo lote, por la misma
    #    cantidad y al mismo costo. Un compuesto se descontó en sus
    #    componentes, así que vuelve en sus componentes.
    for salida in (MovimientoStock.objects
                   .filter(id_venta=venta,
                           tipo_movimiento=TipoMovimientoStock.VENTA)
                   .order_by('id_movimiento_stock')):
        MovimientoStock.objects.create(
            id_producto_id=salida.id_producto_id, id_usuario=usuario,
            id_lote_id=salida.id_lote_id, id_venta=venta,
            tipo_movimiento=TipoMovimientoStock.ANULACION,
            cantidad=-salida.cantidad, costo_unitario=salida.costo_unitario)
        resultado.renglones_stock += 1

    # 2. Caja: sale el efectivo que la venta había entrado (ya neto del
    #    vuelto). Lo cobrado con tarjeta o transferencia no pasa por el
    #    cajón: su reverso se hace en el posnet o el banco.
    entrada = MovimientoCaja.objects.filter(
        id_venta=venta, tipo_movimiento=TipoMovimientoCaja.VENTA).first()
    if entrada is not None:
        servicio_caja.registrar_movimiento(
            turno, usuario, TipoMovimientoCaja.ANULACION, -entrada.monto,
            f'Anulación de la venta N° {venta.pk}', id_venta=venta)
        resultado.efectivo = entrada.monto
    resultado.medios_externos = sorted({
        str(p.id_medio_pago) for p in venta.pagos.select_related('id_medio_pago')
        if not p.id_medio_pago.afecta_caja and not p.id_medio_pago.es_cuenta_corriente})

    # 2 bis. Cuenta corriente (DD-91): la deuda de esta venta se cancela
    #    sola. Si ya se había cobrado algo, no se anula: esa plata entró y
    #    hay que devolverla con una devolución, que deja su rastro.
    from fidelizacion import credito
    from fidelizacion.models import MovimientoCuenta

    credito.bloquear_cliente_de(venta)
    if MovimientoCuenta.objects.filter(id_venta=venta, tipo_movimiento='COBRO').exists():
        raise AnulacionRechazada(
            f'El cliente ya pagó parte de la venta N° {venta.pk}, que era fiada: no se puede anular. '
            'Registrá una devolución.')
    resultado.deuda_cancelada = credito.cancelar_por_venta(venta, usuario)

    # 3. Puntos: el DFD 9 es el único que escribe su libro (DD-37).
    if venta.id_cliente_id:
        from fidelizacion import puntos

        resultado.puntos = puntos.revertir_por_anulacion(venta)

    # 4. La venta queda ANULADA: cuándo, por qué, quién la pidió y quién
    #    la autorizó.
    venta.estado_venta = EstadoVenta.ANULADA
    venta.fecha_anulacion = timezone.now()
    venta.id_usuario_anulacion = usuario
    venta.id_usuario_solicitante = solicitante
    venta.motivo_anulacion = motivo
    venta.detalle_anulacion = detalle
    venta.save(update_fields=['estado_venta', 'fecha_anulacion',
                              'id_usuario_anulacion', 'id_usuario_solicitante',
                              'motivo_anulacion', 'detalle_anulacion'])
    return resultado
