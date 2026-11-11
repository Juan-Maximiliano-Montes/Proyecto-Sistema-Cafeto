"""Servicios de Stock: descontar, reingresar, producir y ajustar.

Todo lo que mueve mercadería pasa por acá y termina en un asiento de
MOVIMIENTOS_STOCK (que es el que mueve el saldo, ver
``MovimientoStock.save``). Ninguna vista escribe un movimiento a mano.

Tres ideas que ordenan el módulo:

**El tipo de producto decide cómo se descuenta.** Lo que tiene stock
propio (reventa, insumo, producido) se descuenta de su saldo; lo que se
arma al momento (elaborado, combo) se descuenta en sus COMPONENTES. Un
combo "café + 2 medialunas" descuenta las medialunas de su stock y el
café... de los insumos de su receta.

**FEFO.** Lo que controla vencimiento sale por lote, primero lo que vence
primero (*first expired, first out*). Es lo que evita que la mercadería
se venza en el depósito, y es lo que hace posible la rebaja automática:
el lote que sale es el que está por vencer.

**Nada se mueve sin autorización.** Una merma, un traspaso o un conteo se
cargan y quedan PENDIENTES; el stock cambia recién cuando alguien con
permiso los aprueba.

Todo corre en ``transaction.atomic()`` y bloquea con
``select_for_update()`` el producto y los lotes antes de leer el saldo:
dos ventas simultáneas de las dos últimas latas no pueden venderlas las
dos.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from nucleo.modelos import es_entero
from stock.models import (
    AjusteStock,
    DetalleAjuste,
    EstadoAjuste,
    Lote,
    MotivoAjuste,
    MovimientoStock,
    Produccion,
    Producto,
    TipoAjuste,
    TipoMovimientoStock,
    TipoProducto,
)

CERO = Decimal('0')


# ===========================================================================
#  CONSULTAS
# ===========================================================================
def lotes_fefo(producto, bloquear=False):
    """Los lotes con stock, del que vence primero al que vence último."""
    lotes = Lote.objects.filter(id_producto=producto, stock_lote__gt=0)
    if bloquear:
        lotes = lotes.select_for_update()
    return lotes.order_by(F('fecha_vencimiento').asc(), 'fecha_alta',
                          'id_lote')


def vencido(producto, hoy=None):
    """Cuánto del saldo de ``producto`` está en lotes ya vencidos.

    Eso no se vende ni se usa para preparar: espera la merma por
    vencimiento (o el conteo) que lo saque del stock.
    """
    if not producto.controla_vencimiento:
        return CERO
    hoy = hoy or timezone.localdate()
    total = (Lote.objects.filter(id_producto=producto, stock_lote__gt=0,
                                 fecha_vencimiento__lt=hoy)
             .aggregate(t=Sum('stock_lote'))['t'])
    return total or CERO


def disponible(producto):
    """Cuánto se puede vender de ``producto`` AHORA.

    * Con stock propio: su saldo, sin lo que está en lotes vencidos (un
      insumo no limita: puede quedar en negativo, así que no se informa
      como tope).
    * Elaborado: lo que permitan sus componentes de reventa (los insumos no
      frenan).
    * Combo: cuántos combos completos se pueden armar.

    Devuelve ``None`` si no hay tope.
    """
    if producto.tipo_producto == TipoProducto.INSUMO:
        return None
    if producto.tiene_stock_propio:
        # De la base: la instancia puede ser de antes de la última venta.
        actual = Producto.objects.values_list('stock_actual', flat=True).get(
            pk=producto.pk)
        return max(actual - vencido(producto), CERO)
    tope = None
    for item in producto.composicion.select_related('id_componente'):
        del_componente = disponible(item.id_componente)
        if del_componente is None:
            continue
        alcanza = (del_componente / item.cantidad).to_integral_value(
            rounding='ROUND_FLOOR')
        tope = alcanza if tope is None else min(tope, alcanza)
    return tope


@dataclass
class Salida:
    """Cómo saldría una cantidad: de qué lote y con qué rebaja."""
    producto: Producto
    cantidad: Decimal
    lote: Lote = None
    rebaja_porcentaje: Decimal = CERO


@dataclass
class PlanDeSalida:
    producto: Producto
    cantidad: Decimal
    tramos: list = field(default_factory=list)

    @property
    def rebaja_ponderada(self):
        """El porcentaje de rebaja promedio del renglón (por cantidad)."""
        if not self.cantidad:
            return CERO
        total = sum((t.cantidad * t.rebaja_porcentaje for t in self.tramos),
                    CERO)
        return (total / self.cantidad).quantize(Decimal('0.01'))


def planificar_salida(producto, cantidad, hoy=None, bloquear=False):
    """Simula FEFO para ``producto`` SIN escribir nada.

    Sirve para dos cosas: decirle al cajero ANTES de cobrar si alcanza el
    stock (el aviso en rojo) y calcular la rebaja automática de los lotes
    que están por vencer. Sólo considera la rebaja de los tramos que salen
    de un lote dentro de la ventana de días.

    Los lotes VENCIDOS no entran en el plan: no se venden ni se usan para
    preparar. Lo que no alcanza con los lotes vigentes queda como tramo sin
    lote, y ``descontar`` lo rechaza.
    """
    hoy = hoy or timezone.localdate()
    cantidad = Decimal(cantidad)
    plan = PlanDeSalida(producto, cantidad)
    if not producto.controla_vencimiento:
        plan.tramos.append(Salida(producto, cantidad))
        return plan

    rebaja = producto.rebaja_vigente()
    restante = cantidad
    for lote in lotes_fefo(producto, bloquear=bloquear):
        if restante <= 0:
            break
        if lote.fecha_vencimiento < hoy:
            continue
        toma = min(restante, lote.stock_lote)
        porcentaje = CERO
        if rebaja is not None and lote.dias_para_vencer(hoy) <= rebaja[0]:
            porcentaje = rebaja[1]
        plan.tramos.append(Salida(producto, toma, lote, porcentaje))
        restante -= toma
    if restante > 0:
        # Lo que falta se informa igual: el control de stock lo da el aviso.
        plan.tramos.append(Salida(producto, restante))
    return plan


def exigir_disponible(producto, cantidad):
    """El aviso en rojo del mostrador: no se vende lo que no hay.

    Un insumo no limita (puede quedar en negativo), salvo que se maneje por
    lote: entonces sale de lotes vigentes, y si no alcanzan se avisa acá,
    antes de cobrar, lo mismo que ``descontar`` va a rechazar al confirmar.
    """
    if producto.admite_negativo and producto.controla_vencimiento:
        vigente = (Lote.objects.filter(id_producto=producto, stock_lote__gt=0,
                                       fecha_vencimiento__gte=timezone.localdate())
                   .aggregate(t=Sum('stock_lote'))['t'] or CERO)
        if Decimal(cantidad) > vigente:
            raise ValidationError(
                f'No hay lotes vigentes de "{producto}" para '
                f'{producto.cantidad_con_unidad(cantidad)} (hay '
                f'{producto.cantidad_con_unidad(vigente)}). Cargá la compra o '
                'el conteo del depósito (y la merma de lo vencido, si lo '
                'hay).')
        return
    tope = disponible(producto)
    if tope is not None and Decimal(cantidad) > tope:
        if producto.tiene_stock_propio:
            vencidas = vencido(producto)
            aviso = (f' (además hay {producto.cantidad_con_unidad(vencidas)} '
                     'vencidas, que no se venden: cargá la merma)'
                     if vencidas > 0 else '')
            raise ValidationError(
                f'Stock insuficiente de "{producto}": hay '
                f'{producto.cantidad_con_unidad(tope)}{aviso} y se piden '
                f'{producto.cantidad_con_unidad(cantidad)}.')
        raise ValidationError(
            f'No alcanzan los componentes para {cantidad} de "{producto}": '
            f'se pueden armar {tope}.')


CANTIDAD_MAXIMA = Decimal('999999')


def exigir_cantidad_valida(producto, cantidad, etiqueta='La cantidad'):
    cantidad = Decimal(cantidad)
    if cantidad <= 0:
        raise ValidationError(
            f'{etiqueta} de "{producto}" tiene que ser mayor que cero.')
    # Tope de cordura: nadie mueve un millón de unidades de algo en un local,
    # y un número más grande desbordaba el campo de la base (error 500).
    if cantidad > CANTIDAD_MAXIMA:
        raise ValidationError(
            f'{etiqueta} de "{producto}" es demasiado grande: revisá el número.')
    # La base guarda las cantidades con 3 decimales (un gramo). Una con más
    # se redondeaba al guardar y dejaba de coincidir con su subtotal.
    if cantidad.as_tuple().exponent < -3:
        raise ValidationError(
            f'{etiqueta} de "{producto}" admite hasta 3 decimales (un gramo).')
    if producto.exige_cantidad_entera and not es_entero(cantidad):
        raise ValidationError(
            f'"{producto}" se vende por unidad: {etiqueta.lower()} tiene que '
            'ser entera.')
    return cantidad


# ===========================================================================
#  DESCONTAR Y REINGRESAR
# ===========================================================================
def descontar(producto, cantidad, usuario, tipo, documento, hoy=None):
    """Saca ``cantidad`` de ``producto`` y devuelve los movimientos.

    ``documento`` es el dict con la FK del documento que origina la salida
    (``{'id_venta': venta}``, ``{'id_cambio': cambio}``,
    ``{'id_produccion': produccion}``). ``tipo`` es el del asiento
    (VENTA, CAMBIO, PRODUCCION).

    Un compuesto se resuelve en sus componentes; lo demás, FEFO por lote o
    del saldo general.
    """
    producto = Producto.objects.get(pk=producto.pk)
    cantidad = Decimal(cantidad)
    movimientos = []

    if not producto.tiene_stock_propio:
        for item in producto.composicion.select_related('id_componente'):
            movimientos += descontar(
                item.id_componente, cantidad * item.cantidad, usuario, tipo,
                documento, hoy)
        return movimientos

    if not producto.controla_vencimiento:
        movimientos.append(MovimientoStock.objects.create(
            id_producto=producto, id_usuario=usuario, tipo_movimiento=tipo,
            cantidad=-cantidad, costo_unitario=producto.precio_costo,
            **documento))
        return movimientos

    plan = planificar_salida(producto, cantidad, hoy=hoy, bloquear=True)
    # Primero se mira que todo el plan salga de lotes vigentes; recién
    # después se escribe.
    for tramo in plan.tramos:
        if tramo.lote is None:
            if producto.admite_negativo:
                # Un insumo con vencimiento que se terminó (o que sólo tiene
                # lotes vencidos): no se puede preparar con él.
                raise ValidationError(
                    f'No hay lotes vigentes de "{producto}" para descontar '
                    f'{producto.cantidad_con_unidad(tramo.cantidad)}. Cargá '
                    'la compra o el conteo del depósito (y la merma de lo '
                    'vencido, si lo hay).')
            raise ValidationError(
                f'Stock insuficiente de "{producto}": faltan '
                f'{producto.cantidad_con_unidad(tramo.cantidad)}.')
    for tramo in plan.tramos:
        movimientos.append(MovimientoStock.objects.create(
            id_producto=producto, id_usuario=usuario, id_lote=tramo.lote,
            tipo_movimiento=tipo, cantidad=-tramo.cantidad,
            costo_unitario=producto.precio_costo, **documento))
    return movimientos


def reingresar(producto, cantidad, usuario, cambio, lotes_de_origen=()):
    """Vuelve al stock lo que el cliente devolvió en buen estado.

    Si el producto se maneja por lote, vuelve al lote del que salió (el
    último de ``lotes_de_origen`` que todavía no venció); si ninguno sirve,
    no se puede reingresar.
    """
    producto = Producto.objects.get(pk=producto.pk)
    if not producto.tiene_stock_propio:
        raise ValidationError(
            f'"{producto}" se prepara al momento: lo devuelto no vuelve al '
            'stock.')
    lote = None
    if producto.controla_vencimiento:
        hoy = timezone.localdate()
        candidatos = [l for l in lotes_de_origen
                      if l is not None and l.fecha_vencimiento >= hoy]
        if not candidatos:
            raise ValidationError(
                f'El lote de "{producto}" ya venció: lo devuelto no puede '
                'volver al stock. Registralo como no apto.')
        lote = max(candidatos, key=lambda l: l.fecha_vencimiento)
    return MovimientoStock.objects.create(
        id_producto=producto, id_usuario=usuario, id_lote=lote,
        id_cambio=cambio, tipo_movimiento=TipoMovimientoStock.DEVOLUCION,
        cantidad=Decimal(cantidad), costo_unitario=producto.precio_costo)


# ===========================================================================
#  PRODUCCIÓN
# ===========================================================================
@transaction.atomic
def registrar_produccion(producto, cantidad, usuario, fecha_vencimiento=None,
                         observacion=None):
    """Una tanda: entran las unidades y salen los insumos de la receta.

    El costo unitario se congela con lo que cuestan los insumos HOY, y pasa
    a ser el costo del producto (lo usa el margen de las ventas).
    """
    producto = Producto.objects.select_for_update().get(pk=producto.pk)
    if producto.tipo_producto != TipoProducto.PRODUCIDO:
        raise ValidationError(
            f'"{producto}" no es un producto que se fabrique por tanda.')
    if producto.borrado_logico:
        raise ValidationError(f'"{producto}" está dado de baja.')
    receta = list(producto.composicion.select_related('id_componente'))
    if not receta:
        raise ValidationError(
            f'"{producto}" no tiene receta cargada: sin ella no se sabe qué '
            'insumos descontar.')
    cantidad = exigir_cantidad_valida(producto, cantidad,
                                      'La cantidad producida')
    if producto.controla_vencimiento and fecha_vencimiento is None:
        raise ValidationError(
            f'"{producto}" controla vencimiento: indicá hasta cuándo vale la '
            'tanda.')
    if fecha_vencimiento and fecha_vencimiento < timezone.localdate():
        raise ValidationError('La fecha de vencimiento ya pasó.')
    if (producto.controla_vencimiento and fecha_vencimiento
            and (fecha_vencimiento - timezone.localdate()).days > 730):
        raise ValidationError(
            f'¿La tanda vence el {fecha_vencimiento:%d/%m/%Y}? Es dentro de más '
            'de dos años para algo hecho en el local: revisá el año.')

    # Lo que es de reventa no puede quedar en negativo: se avisa antes.
    for item in receta:
        exigir_disponible(item.id_componente, cantidad * item.cantidad)

    costo = sum((item.cantidad * item.id_componente.costo_vigente()
                 for item in receta), CERO).quantize(Decimal('0.01'))
    produccion = Produccion.objects.create(
        id_producto=producto, id_usuario=usuario, cantidad=cantidad,
        costo_unitario=costo,
        fecha_vencimiento=(fecha_vencimiento
                           if producto.controla_vencimiento else None),
        observacion=(observacion or '').strip()[:120] or None)

    lote = None
    if producto.controla_vencimiento:
        lote = Lote.objects.create(
            id_producto=producto, id_produccion=produccion,
            fecha_vencimiento=fecha_vencimiento, cantidad_inicial=cantidad,
            numero_lote=f'P{produccion.pk}')
    documento = {'id_produccion': produccion}
    MovimientoStock.objects.create(
        id_producto=producto, id_usuario=usuario, id_lote=lote,
        tipo_movimiento=TipoMovimientoStock.PRODUCCION, cantidad=cantidad,
        costo_unitario=costo, **documento)
    for item in receta:
        descontar(item.id_componente, cantidad * item.cantidad, usuario,
                  TipoMovimientoStock.PRODUCCION, documento)

    Producto.objects.filter(pk=producto.pk).update(precio_costo=costo)
    return produccion


# ===========================================================================
#  AJUSTES: conteo, merma y traspaso
# ===========================================================================
def _exigir_estado(ajuste, *estados):
    if ajuste.estado_ajuste not in estados:
        raise ValidationError(
            f'{ajuste} está {ajuste.get_estado_ajuste_display().lower()}: '
            'no admite esa operación.')


@transaction.atomic
def iniciar_conteo(usuario, motivo=MotivoAjuste.DIFERENCIA_CONTEO):
    """Abre un conteo. Hay uno solo abierto a la vez (índice único)."""
    if AjusteStock.objects.filter(
            tipo_ajuste=TipoAjuste.CONTEO,
            estado_ajuste__in=(EstadoAjuste.EN_CURSO,
                               EstadoAjuste.PENDIENTE)).exists():
        raise ValidationError(
            'Ya hay un conteo abierto: terminalo o pedí que lo resuelvan '
            'antes de empezar otro.')
    ajuste = AjusteStock(tipo_ajuste=TipoAjuste.CONTEO,
                         estado_ajuste=EstadoAjuste.EN_CURSO, motivo=motivo,
                         id_usuario=usuario)
    ajuste.full_clean()
    ajuste.save()
    return ajuste


@transaction.atomic
def cargar_conteo(ajuste, producto, contada, lote=None, nuevo_lote=None):
    """Anota lo contado de un producto (o de un lote).

    El teórico se congela la PRIMERA vez que se carga el renglón; si se
    vuelve a contar, sólo cambia lo contado. ``nuevo_lote`` (dict con
    ``fecha_vencimiento`` y ``numero_lote``) da de alta un lote que el
    sistema no conocía —típico del inventario inicial—, con teórico 0.
    """
    ajuste = AjusteStock.objects.select_for_update().get(pk=ajuste.pk)
    _exigir_estado(ajuste, EstadoAjuste.EN_CURSO)
    if ajuste.tipo_ajuste != TipoAjuste.CONTEO:
        raise ValidationError(f'{ajuste} no es un conteo.')
    contada = Decimal(contada)
    if contada < 0:
        raise ValidationError('Lo contado no puede ser negativo.')
    if nuevo_lote is not None:
        if not producto.controla_vencimiento:
            raise ValidationError(f'"{producto}" no se maneja por lote.')
        if contada <= 0:
            raise ValidationError('Un lote nuevo se carga con lo que tiene.')
        lote = Lote(id_producto=producto, cantidad_inicial=contada,
                    fecha_vencimiento=nuevo_lote['fecha_vencimiento'],
                    numero_lote=(nuevo_lote.get('numero_lote') or None))
        lote.full_clean()
        lote.save()

    renglon = DetalleAjuste.objects.filter(
        id_ajuste=ajuste, id_producto=producto, id_lote=lote).first()
    if renglon is None:
        renglon = DetalleAjuste(id_ajuste=ajuste, id_producto=producto,
                                id_lote=lote, cantidad_contada=contada,
                                cantidad_teorica=CERO)
    else:
        renglon.cantidad_contada = contada
    renglon.full_clean(exclude=['cantidad_teorica', 'costo_unitario'])
    renglon.save()
    return renglon


@transaction.atomic
def enviar_conteo(ajuste):
    ajuste = AjusteStock.objects.select_for_update().get(pk=ajuste.pk)
    _exigir_estado(ajuste, EstadoAjuste.EN_CURSO)
    if not ajuste.detalles.exists():
        raise ValidationError('El conteo no tiene ningún renglón cargado.')
    ajuste.estado_ajuste = EstadoAjuste.PENDIENTE
    ajuste.save(update_fields=['estado_ajuste'])
    return ajuste


def _crear_ajuste(tipo, usuario, motivo, detalle, renglones):
    if not renglones:
        raise ValidationError('Cargá al menos un producto.')
    ajuste = AjusteStock(tipo_ajuste=tipo, motivo=motivo,
                         detalle_motivo=detalle, id_usuario=usuario)
    ajuste.full_clean()
    ajuste.save()
    vistos = set()
    for renglon in renglones:
        clave = (renglon.id_producto_id, renglon.id_lote_id)
        if clave in vistos:
            raise ValidationError(
                f'"{renglon.id_producto}" está cargado dos veces.')
        vistos.add(clave)
        renglon.id_ajuste = ajuste
        renglon.full_clean(exclude=['id_ajuste', 'costo_unitario'])
        renglon.save()
    return ajuste


@transaction.atomic
def registrar_merma(usuario, motivo, renglones, detalle=None,
                    aprobar_ya=False):
    """Carga una merma: ``renglones`` = [(producto, cantidad, lote), ...].

    Queda PENDIENTE. Con ``aprobar_ya`` (sólo para quien tiene permiso de
    autorizar, lo controla la vista) se aprueba en el mismo paso.
    """
    filas = []
    for producto, cantidad, lote in renglones:
        cantidad = exigir_cantidad_valida(producto, cantidad)
        exigir_existencia(producto, cantidad, lote)
        filas.append(DetalleAjuste(id_producto=producto, id_lote=lote,
                                   cantidad=cantidad))
    ajuste = _crear_ajuste(TipoAjuste.MERMA, usuario, motivo, detalle, filas)
    if aprobar_ya:
        ajuste = aprobar(ajuste, usuario)
    return ajuste


@transaction.atomic
def registrar_traspaso(usuario, motivo, renglones, detalle=None,
                       aprobar_ya=False):
    """Pasa mercadería de un producto a otro.

    ``renglones`` = [(origen, cantidad, destino, cantidad_destino, lote)].
    El caso típico: la leche de la góndola del Minisuper que pasa a ser el
    insumo "leche" de la Cafetería (1 sachet = 1 l).
    """
    filas = []
    for origen, cantidad, destino, cantidad_destino, lote in renglones:
        cantidad = exigir_cantidad_valida(origen, cantidad)
        exigir_existencia(origen, cantidad, lote)
        cantidad_destino = exigir_cantidad_valida(destino, cantidad_destino,
                                                  'La cantidad que entra')
        if destino.controla_vencimiento:
            raise ValidationError(
                f'"{destino}" se maneja por lote: un traspaso no puede '
                'inventarle un vencimiento. Cargalo como compra o conteo.')
        filas.append(DetalleAjuste(
            id_producto=origen, id_lote=lote, cantidad=cantidad,
            id_producto_destino=destino, cantidad_destino=cantidad_destino))
    ajuste = _crear_ajuste(TipoAjuste.TRASPASO, usuario, motivo, detalle,
                           filas)
    if aprobar_ya:
        ajuste = aprobar(ajuste, usuario)
    return ajuste


def exigir_existencia(producto, cantidad, lote=None):
    """Una merma o un traspaso no pueden sacar más de lo que hay.

    Es la misma cuenta que hace el movimiento de stock al aprobar
    (``MovimientoStock.save``), pero al CARGAR: antes, una merma de 500
    botellas con 12 en la góndola se cargaba igual, quedaba pendiente y
    reventaba recién cuando alguien la quería aprobar. A diferencia de la
    venta, acá sí cuentan los lotes vencidos: tirarlos es el caso típico.
    """
    cantidad = Decimal(cantidad)
    # Se lee de la base: el objeto que llega puede haberse cargado antes de
    # la última venta o compra.
    if lote is not None:
        if lote.id_producto_id != producto.pk:
            raise ValidationError(f'El lote indicado no es de "{producto}".')
        en_lote = Lote.objects.values_list('stock_lote', flat=True).get(pk=lote.pk)
        if cantidad > en_lote:
            raise ValidationError(
                f'En el lote {lote} de "{producto}" hay '
                f'{producto.cantidad_con_unidad(en_lote)}: no pueden '
                f'salir {producto.cantidad_con_unidad(cantidad)}.')
        return
    hay = Producto.objects.values_list('stock_actual', flat=True).get(pk=producto.pk)
    if not producto.admite_negativo and cantidad > hay:
        raise ValidationError(
            f'De "{producto}" hay {producto.cantidad_con_unidad(hay)}: '
            f'no pueden salir {producto.cantidad_con_unidad(cantidad)}.')


def puede_aprobar_lo_propio(usuario):
    """La Administradora (la dueña) aprueba lo que ella misma cargó; el
    Encargado y el Operador, no (DD-67)."""
    return usuario is not None and usuario.es_administrador


def exigir_otra_persona(ajuste, autorizante):
    """Control por oposición: quien carga un ajuste no lo aprueba, salvo
    la Administradora (DD-67). Es la misma idea que la anulación de una
    venta (DD-61): el que pide no es el que autoriza."""
    if (ajuste.id_usuario_id == getattr(autorizante, 'pk', None)
            and not puede_aprobar_lo_propio(autorizante)):
        raise ValidationError(
            f'Este ajuste ({ajuste.get_tipo_ajuste_display().lower()} '
            f'N° {ajuste.pk}) lo cargaste vos: lo tiene que aprobar otra '
            'persona con permiso para autorizar ajustes.')


@transaction.atomic
def aprobar(ajuste, autorizante, observacion=None):
    """Aprueba y RECIÉN AHÍ mueve el stock.

    * CONTEO: asienta la diferencia (contado - teórico congelado). Se
      asienta la diferencia y no el contado, porque entre el conteo y la
      aprobación se pudo seguir vendiendo.
    * MERMA: sale lo cargado.
    * TRASPASO: sale del origen y entra al destino, al mismo valor.
    """
    ajuste = AjusteStock.objects.select_for_update().get(pk=ajuste.pk)
    _exigir_estado(ajuste, EstadoAjuste.PENDIENTE)
    exigir_otra_persona(ajuste, autorizante)
    documento = {'id_ajuste': ajuste}

    for renglon in ajuste.detalles.select_related(
            'id_producto', 'id_lote', 'id_producto_destino'):
        producto = renglon.id_producto
        if ajuste.tipo_ajuste == TipoAjuste.CONTEO:
            diferencia = renglon.diferencia
            if diferencia:
                MovimientoStock.objects.create(
                    id_producto=producto, id_usuario=autorizante,
                    id_lote=renglon.id_lote,
                    tipo_movimiento=TipoMovimientoStock.AJUSTE,
                    cantidad=diferencia,
                    costo_unitario=renglon.costo_unitario, **documento)
        elif ajuste.tipo_ajuste == TipoAjuste.MERMA:
            MovimientoStock.objects.create(
                id_producto=producto, id_usuario=autorizante,
                id_lote=renglon.id_lote,
                tipo_movimiento=TipoMovimientoStock.MERMA,
                cantidad=-renglon.cantidad,
                costo_unitario=renglon.costo_unitario, **documento)
        else:
            MovimientoStock.objects.create(
                id_producto=producto, id_usuario=autorizante,
                id_lote=renglon.id_lote,
                tipo_movimiento=TipoMovimientoStock.TRASPASO,
                cantidad=-renglon.cantidad,
                costo_unitario=renglon.costo_unitario, **documento)
            valor = renglon.cantidad * renglon.costo_unitario
            costo_destino = (valor / renglon.cantidad_destino).quantize(
                Decimal('0.01'))
            MovimientoStock.objects.create(
                id_producto=renglon.id_producto_destino,
                id_usuario=autorizante,
                tipo_movimiento=TipoMovimientoStock.TRASPASO,
                cantidad=renglon.cantidad_destino,
                costo_unitario=costo_destino, **documento)

    ajuste.estado_ajuste = EstadoAjuste.APROBADO
    ajuste.id_usuario_autorizante = autorizante
    ajuste.fecha_resolucion = timezone.now()
    ajuste.observacion_resolucion = (observacion or '').strip()[:120] or None
    ajuste.save(update_fields=['estado_ajuste', 'id_usuario_autorizante',
                               'fecha_resolucion', 'observacion_resolucion'])
    return ajuste


@transaction.atomic
def rechazar(ajuste, autorizante, observacion):
    """Rechaza: el stock no se toca y queda escrito por qué."""
    ajuste = AjusteStock.objects.select_for_update().get(pk=ajuste.pk)
    _exigir_estado(ajuste, EstadoAjuste.PENDIENTE)
    observacion = (observacion or '').strip()
    if not observacion:
        raise ValidationError('Contá por qué se rechaza.')
    ajuste.estado_ajuste = EstadoAjuste.RECHAZADO
    ajuste.id_usuario_autorizante = autorizante
    ajuste.fecha_resolucion = timezone.now()
    ajuste.observacion_resolucion = observacion[:120]
    ajuste.save(update_fields=['estado_ajuste', 'id_usuario_autorizante',
                               'fecha_resolucion', 'observacion_resolucion'])
    return ajuste


# ===========================================================================
#  ALERTAS
# ===========================================================================
def bajo_minimo():
    """Lo que tiene stock propio y está por debajo del mínimo."""
    return (Producto.objects
            .filter(borrado_logico=False, stock_minimo__gt=0,
                    stock_actual__lt=F('stock_minimo'))
            .select_related('id_marca', 'id_rubro')
            .order_by('id_rubro__nombre_rubro', 'nombre_producto'))


def proximos_a_vencer(dias, hoy=None):
    """Lotes con stock que vencen dentro de ``dias`` (incluye vencidos)."""
    hoy = hoy or timezone.localdate()
    hasta = hoy + timedelta(days=dias)
    return (Lote.objects
            .filter(stock_lote__gt=0, fecha_vencimiento__lte=hasta,
                    id_producto__borrado_logico=False)
            .select_related('id_producto', 'id_producto__id_marca',
                            'id_producto__id_rubro')
            .order_by('fecha_vencimiento', 'id_producto__nombre_producto'))


def en_oferta(hoy=None):
    """Lo que hoy se vende con rebaja automática: el "sector de ofertas".

    Devuelve [(lote, porcentaje, precio_con_rebaja)], para las etiquetas.
    """
    hoy = hoy or timezone.localdate()
    resultado = []
    lotes = (Lote.objects.filter(stock_lote__gt=0,
                                 id_producto__borrado_logico=False)
             .select_related('id_producto', 'id_producto__id_rubro',
                             'id_producto__id_marca')
             .order_by('fecha_vencimiento'))
    for lote in lotes:
        rebaja = lote.id_producto.rebaja_vigente()
        if rebaja is None or lote.fecha_vencimiento < hoy:
            continue
        if lote.dias_para_vencer(hoy) <= rebaja[0]:
            precio = lote.id_producto.precio_venta
            con_rebaja = (precio * (100 - rebaja[1]) / 100).quantize(
                Decimal('1'))
            resultado.append((lote, rebaja[1], con_rebaja))
    return resultado
