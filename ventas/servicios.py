"""Ventas: cotizar y registrar un ticket.

El ticket se arma en dos pasos que comparten el mismo cálculo:

* ``cotizar()`` no escribe nada: valida, toma los precios VIGENTES, aplica
  la rebaja automática de lo que está por vencer y reparte el cobro entre
  los medios de pago. Es lo que muestra la pantalla antes de cobrar,
  incluido el aviso en rojo si algo no alcanza.
* ``registrar()`` vuelve a cotizar con los lotes bloqueados —el precio
  vale al CONFIRMAR, no al cargar el renglón— y recién ahí escribe la
  venta, sus renglones, sus pagos, el stock y la caja, todo en una
  transacción.

Tres ideas:

**El renglón congela lo que se cobró y lo que costaba** (RN-3.1.3): si
mañana cambia el precio o el costo, el ticket de ayer y su margen siguen
diciendo lo que fue.

**Los importes cuadran por construcción.** El total se arma con los
renglones, se redondea al peso y se reparte en los pagos; los CHECK de la
base verifican que nadie lo arme por otro lado.

**Un doble clic no cobra dos veces.** La pantalla manda un
``token_operacion`` por ticket; si llega dos veces (doble clic, la tablet
que reintenta al volver el wifi), la segunda devuelve la venta ya hecha.
"""
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from negocio.models import ParametrosNegocio
from nucleo.modelos import redondear_importe
from stock import servicios as stock
from stock.models import Producto, TipoMovimientoStock
from ventas import caja as servicio_caja
from ventas.models import (
    DetalleVenta,
    MotivoDescuento,
    PagoVenta,
    TipoMovimientoCaja,
    TurnoCaja,
    Venta,
)
from nucleo.templatetags.cafeto import pesos

CERO = Decimal('0')
CENTAVO = Decimal('0.01')


def _centavos(valor):
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


# ===========================================================================
#  ENTRADA
# ===========================================================================
@dataclass
class Item:
    """Un renglón pedido desde la pantalla. El precio NO viaja: se toma."""
    producto: object
    cantidad: Decimal
    descuento_porcentaje: Decimal = CERO
    motivo_descuento: str = None
    detalle_motivo: str = None


@dataclass
class Pago:
    """Una parte del cobro. ``monto`` vacío = lo que falte cubrir."""
    medio: object
    monto: Decimal = None
    recibido: Decimal = None


# ===========================================================================
#  SALIDA DE LA COTIZACIÓN
# ===========================================================================
@dataclass
class Renglon:
    producto: object
    cantidad: Decimal
    precio_unitario: Decimal
    costo_unitario: Decimal
    descuento: Decimal
    motivo: str
    detalle_motivo: str
    porcentaje: Decimal
    pide_autorizacion: bool = False
    beneficio: object = None        # el regalo en especie de un beneficio

    @property
    def bruto(self):
        return _centavos(self.cantidad * self.precio_unitario)

    @property
    def subtotal(self):
        return self.bruto - self.descuento


@dataclass
class Cobro:
    medio: object
    monto: Decimal
    recargo_porcentaje: Decimal
    recargo: Decimal
    recibido: Decimal = None

    @property
    def importe(self):
        return self.monto + self.recargo

    @property
    def vuelto(self):
        if self.recibido is None:
            return None
        return self.recibido - self.importe


@dataclass
class Cotizacion:
    renglones: list = field(default_factory=list)
    cobros: list = field(default_factory=list)
    subtotal: Decimal = CERO
    descuento: Decimal = CERO       # el de cabecera: beneficio del club
    redondeo: Decimal = CERO
    recargo: Decimal = CERO
    # Fidelización (DFD 4 bloque C): el cliente, lo que se le ofrece y lo
    # que se aplicó. ``canjes`` son los que cuestan puntos.
    cliente: object = None
    beneficio_cabecera: object = None
    porcentaje_cabecera: Decimal = CERO
    base_cabecera: Decimal = CERO
    ofrecidos: list = field(default_factory=list)
    aplicados: list = field(default_factory=list)
    canjes: list = field(default_factory=list)
    resultado_puntos: object = None
    # Fiado (DD-91): cuánto va a cuenta corriente, quién firmó la excepción
    # (si hizo falta) y el asiento que quedó en la cuenta del cliente.
    fiado: Decimal = CERO
    firma_fiado: object = None
    movimiento_fiado: object = None

    @property
    def puntos_a_canjear(self):
        return sum((b.puntos_requeridos for b in self.canjes), 0)

    @property
    def a_cobrar(self):
        """Lo que hay que repartir entre los medios (sin recargos)."""
        return self.subtotal - self.descuento + self.redondeo

    @property
    def total(self):
        return self.a_cobrar + self.recargo

    @property
    def pide_autorizacion(self):
        return (any(r.pide_autorizacion for r in self.renglones)
                or self.firma_fiado is not None)

    @property
    def vuelto(self):
        return sum((c.vuelto or CERO for c in self.cobros), CERO)

    @property
    def efectivo_que_entra(self):
        return sum((c.importe for c in self.cobros
                    if c.medio.afecta_caja), CERO)


# ===========================================================================
#  REGLAS DE UN RENGLÓN
# ===========================================================================
def _exigir_vendible(producto):
    if producto.borrado_logico:
        raise ValidationError(
            f'"{producto}" está dado de baja: no se puede vender.')
    if not producto.es_vendible:
        raise ValidationError(
            f'"{producto}" es un insumo: se usa para preparar, no se vende.')
    if producto.es_compuesto and not producto.composicion.exists():
        raise ValidationError(
            f'"{producto}" no tiene cargada su receta o su contenido: no se '
            'puede vender hasta completarla.')


def _tope_de(usuario):
    """Tope de descuento del perfil con que opera (None = sin tope)."""
    if usuario is None:
        return CERO
    return usuario.perfil_efectivo.tope_descuento_porcentaje


def _cubre(tope, porcentaje):
    return tope is None or porcentaje <= tope


def _unificar(items):
    """El mismo producto dos veces se suma en un renglón.

    Así la rebaja por vencimiento y el control de stock miran la cantidad
    total. Si los dos renglones traen descuentos distintos no se pueden
    sumar: se avisa.
    """
    unificados = {}
    for item in items:
        clave = item.producto.pk
        if clave in unificados:
            previo = unificados[clave]
            if (previo.descuento_porcentaje != item.descuento_porcentaje
                    or previo.motivo_descuento != item.motivo_descuento):
                raise ValidationError(
                    f'"{item.producto}" está dos veces con descuentos '
                    'distintos: dejalo en un solo renglón.')
            previo.cantidad += Decimal(item.cantidad)
        else:
            unificados[clave] = Item(
                item.producto, Decimal(item.cantidad),
                Decimal(item.descuento_porcentaje or 0),
                item.motivo_descuento, item.detalle_motivo)
    return list(unificados.values())


def _exigir_stock_total(items):
    """Suma la demanda de cada producto con stock, abriendo los compuestos.

    Un combo "café + 2 medialunas" y dos medialunas sueltas piden cuatro
    medialunas: el aviso tiene que salir aunque ningún renglón, por sí
    solo, las agote.
    """
    demanda = {}

    def sumar(producto, cantidad):
        if producto.tiene_stock_propio:
            actual = demanda.get(producto.pk, (producto, CERO))
            demanda[producto.pk] = (producto, actual[1] + cantidad)
            return
        for item in producto.composicion.select_related('id_componente'):
            sumar(item.id_componente, cantidad * item.cantidad)

    for item in items:
        sumar(item.producto, item.cantidad)
    for producto, cantidad in demanda.values():
        stock.exigir_disponible(producto, cantidad)


# ===========================================================================
#  COTIZAR
# ===========================================================================
def cotizar(items, pagos=None, operador=None, autorizante=None,
            bloquear=False, hoy=None, cliente=None, beneficios=()):
    """Arma el ticket sin escribir nada.

    Devuelve una ``Cotizacion``. Si algo no se puede vender, levanta
    ``ValidationError`` con el mensaje para mostrar en rojo.
    """
    if not items:
        raise ValidationError('El ticket no tiene ningún producto.')
    parametros = ParametrosNegocio.vigentes()
    items = _unificar(items)
    cotizacion = Cotizacion()

    for item in items:
        # El precio y el stock se leen de la BASE en este momento: el
        # precio vale al confirmar, no cuando se cargó el renglón.
        producto = Producto.objects.select_related(
            'id_rubro', 'id_marca').get(pk=item.producto.pk)
        item.producto = producto
        _exigir_vendible(producto)
        cantidad = stock.exigir_cantidad_valida(producto, item.cantidad)
        precio = producto.precio_venta
        bruto = _centavos(cantidad * precio)

        # Rebaja automática por vencimiento: sale del plan FEFO.
        porcentaje_auto = CERO
        if producto.controla_vencimiento:
            plan = stock.planificar_salida(producto, cantidad, hoy=hoy,
                                           bloquear=bloquear)
            porcentaje_auto = plan.rebaja_ponderada

        porcentaje = Decimal(item.descuento_porcentaje or 0)
        if porcentaje < 0 or porcentaje > 100:
            raise ValidationError(
                f'El descuento de "{producto}" tiene que estar entre 0 y '
                '100 %.')
        motivo, detalle = item.motivo_descuento, item.detalle_motivo
        pide_autorizacion = False
        if porcentaje_auto and porcentaje:
            raise ValidationError(
                f'"{producto}" ya tiene la rebaja por vencimiento '
                f'({porcentaje_auto} %): no se le suma otro descuento.')
        if porcentaje_auto:
            porcentaje, motivo, detalle = (
                porcentaje_auto, MotivoDescuento.VENCIMIENTO, None)
        elif porcentaje:
            if motivo not in MotivoDescuento.values:
                raise ValidationError(
                    f'Elegí el motivo del descuento de "{producto}".')
            if motivo in (MotivoDescuento.VENCIMIENTO,
                          MotivoDescuento.BENEFICIO):
                raise ValidationError(
                    f'El motivo "{MotivoDescuento(motivo).label}" lo pone el '
                    'sistema, no se elige a mano.')
            detalle = (detalle or '').strip() or None
            if motivo == MotivoDescuento.OTRO and not detalle:
                raise ValidationError(
                    f'Contá el motivo del descuento de "{producto}".')
            if not _cubre(_tope_de(operador), porcentaje):
                pide_autorizacion = True
                if autorizante is None:
                    raise ValidationError(
                        f'El descuento del {porcentaje} % en "{producto}" '
                        'supera tu tope: lo tiene que autorizar alguien con '
                        'un tope mayor.')
                if not _cubre(_tope_de(autorizante), porcentaje):
                    raise ValidationError(
                        f'{autorizante.nombre} tampoco puede autorizar un '
                        f'descuento del {porcentaje} %.')
        else:
            motivo = detalle = None

        descuento = _centavos(bruto * porcentaje / 100)
        if descuento == 0:
            motivo = detalle = None
        cotizacion.renglones.append(Renglon(
            producto=producto, cantidad=cantidad, precio_unitario=precio,
            costo_unitario=producto.costo_vigente(), descuento=descuento,
            motivo=motivo, detalle_motivo=detalle, porcentaje=porcentaje,
            pide_autorizacion=pide_autorizacion))

    regalos = _aplicar_fidelizacion(cotizacion, cliente, beneficios, hoy)
    _exigir_stock_total(items + regalos)

    cotizacion.subtotal = sum((r.subtotal for r in cotizacion.renglones),
                              CERO)
    _descuento_de_cabecera(cotizacion)
    base = cotizacion.subtotal - cotizacion.descuento
    cotizacion.redondeo = (redondear_importe(base, parametros.redondeo_ventas)
                           - base)
    _repartir_pagos(cotizacion, pagos or [])
    _revisar_credito(cotizacion, operador, autorizante)
    return cotizacion


def _revisar_credito(cotizacion, operador, autorizante):
    """El fiado y el Club ante una deuda (DD-91), ya en la cotización: así
    el mostrador avisa y pide la autorización ANTES de cobrar.

    * Con deuda vencida o la cuenta suspendida no se canjean puntos ni
      beneficios, salvo que lo autorice quien gestiona el fiado.
    * Lo que se paga con «Cuenta corriente» pasa por ``revisar_fiado``.
    """
    from fidelizacion import credito

    cliente = cotizacion.cliente
    usa_club = bool(cotizacion.canjes or cotizacion.beneficio_cabecera
                    or any(r.beneficio for r in cotizacion.renglones))
    if cliente is not None and usa_club:
        credito.exigir_que_pueda_canjear(cliente, operador, autorizante)
    cotizacion.fiado = sum((c.monto for c in cotizacion.cobros if c.medio.es_cuenta_corriente), CERO)
    cotizacion.firma_fiado = None
    if cotizacion.fiado > 0:
        cotizacion.firma_fiado = credito.revisar_fiado(cliente, cotizacion.fiado, operador, autorizante)


# ===========================================================================
#  FIDELIZACIÓN EN EL TICKET (DFD 4 bloque C)
# ===========================================================================
def _aplicar_fidelizacion(cotizacion, cliente, beneficios, hoy):
    """Identifica al cliente, arma lo que se le ofrece y aplica lo elegido.

    * El DESCUENTO automático de su categoría se aplica solo (ver
      ``_descuento_de_cabecera``).
    * Lo elegido en el mostrador (``beneficios``, ids): un regalo en
      especie de su categoría o un canjeable. Un regalo agrega sus
      productos como renglones al 100 % con motivo BENEFICIO; un canjeable
      de descuento reemplaza al automático.

    Devuelve los ``Item`` de los regalos, para el control de stock.
    """
    from fidelizacion import puntos
    from fidelizacion.models import Cliente, ModoEntrega, TipoBeneficio

    regalos = []
    elegidos_ids = []
    for b in beneficios or ():
        clave = getattr(b, 'pk', b)
        if clave in elegidos_ids:
            raise ValidationError('El mismo beneficio está dos veces.')
        elegidos_ids.append(clave)
    if cliente is None:
        if elegidos_ids:
            raise ValidationError(
                'Para aplicar un beneficio del club, primero identificá al '
                'cliente.')
        return regalos

    cliente = Cliente.objects.select_related('id_categoria').get(
        pk=getattr(cliente, 'pk', cliente))
    if cliente.borrado_logico:
        raise ValidationError(
            f'{cliente} está dado de baja en el club: reactivalo para '
            'identificarlo.')
    cotizacion.cliente = cliente
    automatico, especie, canjeables = puntos.beneficios_del_cliente(
        cliente, hoy)
    cotizacion.ofrecidos = ([automatico] if automatico else []) + especie \
        + canjeables

    elegidos = [puntos.exigir_aplicable(cliente, b, hoy) for b in elegidos_ids]
    descuentos = [b for b in elegidos
                  if b.modo_entrega == ModoEntrega.DESCUENTO]
    if len(descuentos) > 1:
        raise ValidationError(
            'Un solo beneficio de descuento por venta: elegí uno.')
    if descuentos:
        canjeado = descuentos[0]
        if automatico and (canjeado.porcentaje_descuento
                           <= automatico.porcentaje_descuento):
            raise ValidationError(
                f'{cliente.nombre_cliente} ya tiene '
                f'{automatico.porcentaje_descuento:g} % por ser '
                f'{cliente.id_categoria}: canjear "{canjeado}" no le suma '
                'nada.')
        cotizacion.beneficio_cabecera = canjeado
    elif automatico:
        cotizacion.beneficio_cabecera = automatico

    for beneficio in elegidos:
        if beneficio.modo_entrega != ModoEntrega.ESPECIE:
            continue
        for renglon in beneficio.renglones.select_related('id_producto'):
            producto = Producto.objects.select_related(
                'id_rubro', 'id_marca').get(pk=renglon.id_producto_id)
            _exigir_vendible(producto)
            cantidad = Decimal(renglon.cantidad)
            precio = producto.precio_venta
            bruto = _centavos(cantidad * precio)
            cotizacion.renglones.append(Renglon(
                producto=producto, cantidad=cantidad, precio_unitario=precio,
                costo_unitario=producto.costo_vigente(), descuento=bruto,
                motivo=MotivoDescuento.BENEFICIO, detalle_motivo=None,
                porcentaje=Decimal('100'), beneficio=beneficio))
            regalos.append(Item(producto, cantidad))
    cotizacion.aplicados = elegidos
    cotizacion.canjes = [b for b in elegidos
                         if b.tipo_beneficio == TipoBeneficio.CANJEABLE]
    if cotizacion.puntos_a_canjear > cliente.puntos_disponibles:
        raise ValidationError(
            f'Puntos insuficientes: {cliente.nombre_cliente} tiene '
            f'{cliente.puntos_disponibles} y lo elegido cuesta '
            f'{cotizacion.puntos_a_canjear}.')
    return regalos


def _descuento_de_cabecera(cotizacion):
    """El beneficio de descuento va sobre el total (DD-18), pero sólo sobre
    los renglones que no traen otro descuento: igual que la rebaja por
    vencimiento, no se suma a otro (ni a un regalo, que ya es gratis)."""
    beneficio = cotizacion.beneficio_cabecera
    if beneficio is None:
        return
    base = sum((r.subtotal for r in cotizacion.renglones
                if r.motivo is None and r.beneficio is None), CERO)
    monto = _centavos(base * beneficio.porcentaje_descuento / 100)
    if monto <= 0:
        if beneficio in cotizacion.canjes:
            raise ValidationError(
                f'"{beneficio}" no tiene sobre qué aplicarse: todos los '
                'productos del ticket ya tienen otro descuento.')
        cotizacion.beneficio_cabecera = None
        return
    cotizacion.descuento = monto
    cotizacion.base_cabecera = base
    cotizacion.porcentaje_cabecera = beneficio.porcentaje_descuento


def _repartir_pagos(cotizacion, pagos):
    """Reparte lo que hay que cobrar entre los medios, con su recargo."""
    a_cobrar = cotizacion.a_cobrar
    if not pagos:
        return
    if a_cobrar == 0:
        raise ValidationError(
            'El ticket no tiene nada que cobrar: no lleva medio de pago.')
    vistos = set()
    restante = a_cobrar
    for indice, pago in enumerate(pagos):
        medio = pago.medio
        if medio is None or medio.borrado_logico:
            raise ValidationError('Uno de los medios de pago no está vigente.')
        if medio.pk in vistos:
            raise ValidationError(
                f'"{medio}" está dos veces: sumá los importes en uno.')
        vistos.add(medio.pk)
        ultimo = indice == len(pagos) - 1
        monto = (Decimal(pago.monto) if pago.monto not in (None, '')
                 else None)
        if monto is None:
            if not ultimo:
                raise ValidationError(
                    f'Indicá cuánto se paga con "{medio}".')
            monto = restante
        monto = _centavos(monto)
        if monto <= 0:
            raise ValidationError(
                f'El importe con "{medio}" tiene que ser mayor que cero.')
        if monto > restante:
            raise ValidationError(
                f'Con "{medio}" se cubren {pesos(monto)} y sólo faltan '
                f'{pesos(restante)}.')
        restante -= monto
        porcentaje = medio.recargo_porcentaje or CERO
        recargo = redondear_importe(monto * porcentaje / 100, Decimal('1'))
        recibido = None
        if medio.afecta_caja:
            recibido = (_centavos(pago.recibido)
                        if pago.recibido not in (None, '')
                        else monto + recargo)
            if recibido < monto + recargo:
                raise ValidationError(
                    f'El efectivo recibido ({pesos(recibido)}) no alcanza para '
                    f'cubrir {pesos(monto + recargo)}.')
        elif pago.recibido not in (None, ''):
            raise ValidationError(
                f'"{medio}" no entra al cajón: no lleva monto recibido ni '
                'vuelto.')
        cotizacion.cobros.append(Cobro(medio, monto, porcentaje, recargo,
                                       recibido))
    if restante != 0:
        raise ValidationError(
            f'Falta cubrir {pesos(restante)} del total: agregá otro medio o '
            'ajustá los importes.')
    cotizacion.recargo = sum((c.recargo for c in cotizacion.cobros), CERO)


# ===========================================================================
#  REGISTRAR
# ===========================================================================
def registrar(turno, usuario, items, pagos, token=None, autorizante=None,
              cliente=None, beneficios=(), vence_fiado=None):
    """Emite el ticket. Devuelve ``(venta, cotizacion)``.

    Si el ``token`` ya se usó, devuelve la venta existente sin tocar nada:
    es el mismo pedido que llegó dos veces.
    """
    token = token or uuid.uuid4()
    existente = Venta.objects.filter(token_operacion=token).first()
    if existente is not None:
        return existente, None
    try:
        with transaction.atomic():
            return _registrar(turno, usuario, items, pagos, token,
                              autorizante, cliente, beneficios, vence_fiado)
    except IntegrityError:
        existente = Venta.objects.filter(token_operacion=token).first()
        if existente is not None:
            return existente, None
        raise


def _registrar(turno, usuario, items, pagos, token, autorizante, cliente,
               beneficios=(), vence_fiado=None):
    turno = TurnoCaja.objects.select_for_update().get(pk=turno.pk)
    if not turno.abierto:
        raise ValidationError(
            f'El turno de {turno.id_caja_registradora} está cerrado: abrí '
            'la caja para vender.')
    cotizacion = cotizar(items, pagos, operador=usuario,
                         autorizante=autorizante, bloquear=True,
                         cliente=cliente, beneficios=beneficios)
    if cotizacion.a_cobrar > 0 and not cotizacion.cobros:
        raise ValidationError('Indicá cómo se paga.')
    from fidelizacion import credito
    fiado = cotizacion.fiado

    venta = Venta.objects.create(
        id_turno_caja=turno, id_usuario=usuario,
        id_cliente=cotizacion.cliente,
        id_beneficio=cotizacion.beneficio_cabecera,
        token_operacion=token, subtotal=cotizacion.subtotal,
        monto_descuento=cotizacion.descuento,
        ajuste_redondeo=cotizacion.redondeo,
        monto_recargo=cotizacion.recargo, total_venta=cotizacion.total)

    for renglon in cotizacion.renglones:
        DetalleVenta.objects.create(
            id_venta=venta, id_producto=renglon.producto,
            id_beneficio=renglon.beneficio,
            id_usuario_autorizante=(autorizante
                                    if renglon.pide_autorizacion else None),
            descripcion_item=str(renglon.producto)[:80],
            cantidad=renglon.cantidad,
            precio_unitario=renglon.precio_unitario,
            costo_unitario=renglon.costo_unitario,
            monto_descuento_linea=renglon.descuento,
            motivo_descuento=renglon.motivo,
            detalle_motivo=renglon.detalle_motivo,
            subtotal=renglon.subtotal)
        stock.descontar(renglon.producto, renglon.cantidad, usuario,
                        TipoMovimientoStock.VENTA, {'id_venta': venta})

    for cobro in cotizacion.cobros:
        PagoVenta.objects.create(
            id_venta=venta, id_medio_pago=cobro.medio, monto=cobro.monto,
            recargo_porcentaje=cobro.recargo_porcentaje,
            monto_recargo=cobro.recargo, monto_recibido=cobro.recibido)

    efectivo = cotizacion.efectivo_que_entra
    if efectivo > 0:
        servicio_caja.registrar_movimiento(
            turno, usuario, TipoMovimientoCaja.VENTA, efectivo,
            f'Venta N° {venta.pk}', id_venta=venta)

    if fiado > 0:
        # DD-91: lo fiado no entra al cajón (el medio no "afecta caja") y
        # queda como deuda del cliente. Si no se le puede fiar, se cae la
        # venta entera.
        cotizacion.movimiento_fiado = credito.registrar_fiado(
            venta, fiado, usuario, autorizante, vence_fiado)

    if cotizacion.cliente is not None:
        from fidelizacion import puntos

        # DD-38: el canje se descuenta en esta misma transacción, ANTES de
        # acumular: los puntos que gana esta compra no pagan sus propios
        # canjes. Si el saldo no alcanza, se cae la venta entera.
        for beneficio in cotizacion.canjes:
            puntos.registrar_canje(venta, beneficio)
        # Una compra fiada suma sus puntos cuando se paga, no antes (DD-91).
        cotizacion.resultado_puntos = (puntos.acumular(venta) if fiado <= 0
                                       else puntos.Resultado())
    return venta, cotizacion
