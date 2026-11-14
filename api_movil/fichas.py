"""Cómo viajan a la app los productos, los lotes, los conteos y los ajustes.

Una forma por cosa, para que todas las pantallas la reciban igual.

Lo único que depende de QUIÉN pregunta es el conteo: es **a ciegas** (las
pantallas de la web lo cuidan igual). Quien cuenta no ve el teórico ni el
stock del producto ni lo que dice el lote: eso lo ve recién quien autoriza
(``ajuste``).
"""
from django.utils import timezone

from api_movil import catalogo
from api_movil.respuestas import numero
from stock import servicios
from stock.models import AjusteStock, EstadoAjuste, TipoAjuste


def producto(producto_, con_stock=True):
    """Lo que la app necesita de un producto para cargar una cantidad."""
    return {
        'id': producto_.pk,
        'nombre': producto_.nombre_producto,
        'presentacion': catalogo.presentacion(producto_),
        'tipo': producto_.tipo_producto,
        'unidad': producto_.unidad_medida,
        'cantidad_entera': producto_.exige_cantidad_entera,
        'controla_vencimiento': producto_.controla_vencimiento,
        'codigo_barras': producto_.codigo_barras,
        'stock_actual': (numero(producto_.stock_actual)
                         if con_stock and producto_.tiene_stock_propio else None),
    }


def lote(lote_, hoy, con_cantidad=True):
    datos = {
        'id': lote_.pk,
        'numero': lote_.numero_lote or '',
        'vence': lote_.fecha_vencimiento.isoformat(),
        'dias': lote_.dias_para_vencer(hoy),
    }
    if con_cantidad:
        datos['cantidad'] = numero(lote_.stock_lote)
    return datos


def _quien(usuario):
    return f'{usuario.nombre} {usuario.apellido}'


def _cuando(momento):
    return timezone.localtime(momento).isoformat(timespec='seconds')


# ---------------------------------------------------------------------------
#  Conteo y ajustes
# ---------------------------------------------------------------------------
def conteo_abierto():
    """El conteo que se está haciendo o que espera autorización (hay uno solo)."""
    return (AjusteStock.objects
            .filter(tipo_ajuste=TipoAjuste.CONTEO,
                    estado_ajuste__in=(EstadoAjuste.EN_CURSO, EstadoAjuste.PENDIENTE))
            .select_related('id_usuario').first())


def conteo(ajuste, hoy):
    """El conteo como lo ve quien cuenta: lo que cargó, sin el teórico."""
    if ajuste is None:
        return None
    renglones = (ajuste.detalles.select_related('id_producto', 'id_lote')
                 .order_by('-pk'))
    return {
        'id': ajuste.pk,
        'estado': ajuste.estado_ajuste,
        'motivo': ajuste.motivo,
        'iniciado_por': _quien(ajuste.id_usuario),
        'fecha': _cuando(ajuste.fecha_ajuste),
        'renglones': [{
            'producto': producto(r.id_producto, con_stock=False),
            'lote': lote(r.id_lote, hoy, con_cantidad=False) if r.id_lote_id else None,
            'contada': numero(r.cantidad_contada),
        } for r in renglones],
    }


def ajuste(ajuste_, usuario, hoy):
    """Un ajuste como lo ve quien autoriza: con el teórico y la diferencia.

    ``propio`` y ``puede_aprobar`` son la regla DD-67: quien carga un
    ajuste no lo aprueba, salvo la Administradora.
    """
    propio = ajuste_.id_usuario_id == usuario.pk
    return {
        'id': ajuste_.pk,
        'tipo': ajuste_.tipo_ajuste,
        'estado': ajuste_.estado_ajuste,
        'motivo': ajuste_.motivo,
        'detalle': ajuste_.detalle_motivo,
        'cargado_por': _quien(ajuste_.id_usuario),
        'fecha': _cuando(ajuste_.fecha_ajuste),
        'propio': propio,
        'puede_aprobar': not propio or servicios.puede_aprobar_lo_propio(usuario),
        'renglones': [{
            'producto': producto(r.id_producto),
            'lote': lote(r.id_lote, hoy) if r.id_lote_id else None,
            'cantidad': numero(r.cantidad),
            'teorica': numero(r.cantidad_teorica),
            'contada': numero(r.cantidad_contada),
            'diferencia': numero(r.diferencia),
            'destino': (producto(r.id_producto_destino)
                        if r.id_producto_destino_id else None),
            'cantidad_destino': numero(r.cantidad_destino),
        } for r in ajuste_.detalles.all()],
    }


def resultado(ajuste_):
    """Lo que vuelve al cargar o resolver un ajuste."""
    return {'id': ajuste_.pk, 'tipo': ajuste_.tipo_ajuste,
            'estado': ajuste_.estado_ajuste}


# ---------------------------------------------------------------------------
#  Órdenes de compra
# ---------------------------------------------------------------------------
def orden(orden_, renglones=None):
    """El resumen de una orden; con ``renglones``, el detalle para recibirla."""
    datos = {
        'id': orden_.pk,
        'proveedor': {'id': orden_.id_proveedor_id,
                      'nombre': orden_.id_proveedor.nombre_proveedor},
        'fecha': orden_.fecha_orden.isoformat(),
        'estado': orden_.estado_orden,
        'renglones': (renglones if renglones is not None
                      else getattr(orden_, 'cantidad_renglones', None)),
        'total': numero(orden_.total_orden),
    }
    return datos


def renglones_de_la_orden(orden_):
    """Lo pedido contra lo ya recibido (``OrdenCompra.diferencias``)."""
    precios = {d.id_producto_id: d.precio_unitario for d in orden_.detalles.all()}
    return [{
        'producto': producto(fila['producto']),
        'pedido': numero(fila['pedido']),
        'recibido': numero(fila['recibido']),
        'precio_unitario': numero(precios.get(fila['producto'].pk)),
    } for fila in orden_.diferencias()]
