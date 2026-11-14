"""Las alertas de stock y de vencimiento, con las reglas de la pestaña Alertas.

    SIN_STOCK    tiene stock mínimo y no le queda nada
    STOCK_BAJO   le queda algo, pero menos que el mínimo
    VENCIDO      un lote con stock ya vencido
    POR_VENCER   un lote con stock que vence dentro del aviso
                 (``ParametrosNegocio.dias_aviso_vencimiento``)

Las dos primeras salen de ``servicios.bajo_minimo()`` y las dos últimas de
``servicios.proximos_a_vencer()``: las mismas listas que muestra la web.
La app sólo las separa en dos, porque no se hace lo mismo con lo que se
terminó que con lo que se está terminando, ni con lo que ya venció que con
lo que hay que mover al sector de ofertas.

Las de stock van una por producto; las de vencimiento, una por lote.
"""
from api_movil import catalogo
from api_movil.respuestas import numero
from nucleo.validaciones import clave_normalizada
from stock import servicios

# De la más urgente a la más leve: es el orden de la lista.
GRAVEDAD = ('VENCIDO', 'SIN_STOCK', 'POR_VENCER', 'STOCK_BAJO')
DE_STOCK = ('SIN_STOCK', 'STOCK_BAJO')
DE_VENCIMIENTO = ('VENCIDO', 'POR_VENCER')
FILTROS = {
    'TODAS': GRAVEDAD,
    'STOCK': DE_STOCK,
    'VENCIMIENTO': DE_VENCIMIENTO,
}
FILTRO_INVALIDO = 'Elegí Todas, Stock o Vencimiento.'


# ---------------------------------------------------------------------------
#  Las reglas
# ---------------------------------------------------------------------------
def de_stock(producto):
    """``SIN_STOCK``, ``STOCK_BAJO`` o ``None``: el corte de ``bajo_minimo()``."""
    if producto.stock_minimo <= 0 or producto.stock_actual >= producto.stock_minimo:
        return None
    return 'SIN_STOCK' if producto.stock_actual <= 0 else 'STOCK_BAJO'


def de_lote(lote, hoy, dias_aviso):
    """``VENCIDO``, ``POR_VENCER`` o ``None``: el corte de ``proximos_a_vencer()``."""
    dias = lote.dias_para_vencer(hoy)
    if dias < 0:
        return 'VENCIDO'
    if dias <= dias_aviso:
        return 'POR_VENCER'
    return None


def del_producto(producto, hoy, dias_aviso):
    """Las alertas de un producto, de la más urgente a la más leve."""
    tipos = {de_stock(producto)}
    tipos.update(de_lote(lote, hoy, dias_aviso)
                 for lote in catalogo.lotes(producto))
    return [tipo for tipo in GRAVEDAD if tipo in tipos]


# ---------------------------------------------------------------------------
#  Cómo se cuentan
# ---------------------------------------------------------------------------
def _producto(producto):
    return {'id': producto.id_producto, 'nombre': producto.nombre_producto,
            'presentacion': catalogo.presentacion(producto),
            'unidad': producto.unidad_medida,
            'cantidad_entera': producto.exige_cantidad_entera,
            'controla_vencimiento': producto.controla_vencimiento,
            'stock_actual': numero(producto.stock_actual)}


def _de_stock(producto, tipo):
    minimo = producto.cantidad_con_unidad(producto.stock_minimo)
    if producto.stock_actual < 0:
        # Sólo un insumo puede quedar por debajo de cero (lo descuenta una
        # receta antes de que se cargue la compra o el conteo). En el local
        # sí está: lo que falta es registrarlo. Por eso no se muestra un
        # número negativo, que se lee como «no hay», sino cuánto se usó de
        # más; con ``stock_actual`` la app lo marca «a revisar».
        detalle = (f'Stock a revisar: se usó '
                   f'{producto.cantidad_con_unidad(-producto.stock_actual)} más de lo '
                   f'cargado. Falta cargar la compra o contarlo. El mínimo es {minimo}.')
    elif tipo == 'SIN_STOCK':
        detalle = f'No queda stock. El mínimo es {minimo}.'
    else:
        detalle = (f'Quedan {producto.cantidad_con_unidad(producto.stock_actual)}; '
                   f'el mínimo es {minimo}.')
    return {'id': f'{tipo}-{producto.id_producto}', 'tipo': tipo,
            'producto': _producto(producto), 'lote': None, 'detalle': detalle,
            'dias': None, 'fecha': None}


def _cuando(dias):
    if dias == 0:
        return 'hoy'
    if dias == 1:
        return 'mañana'
    return f'en {dias} días'


def _de_lote(lote, tipo, hoy):
    producto = lote.id_producto
    dias = lote.dias_para_vencer(hoy)
    cual = (f'El lote {lote.numero_lote}' if lote.numero_lote
            else f'El lote que vence el {lote.fecha_vencimiento:%d/%m/%Y}')
    quedan = producto.cantidad_con_unidad(lote.stock_lote)
    if tipo == 'VENCIDO':
        detalle = (f'{cual} venció con {quedan} en góndola. Retiralo y '
                   'cargalo como merma.')
    else:
        detalle = f'{cual} vence {_cuando(dias)}: quedan {quedan}.'
        rebaja = producto.rebaja_vigente()
        if rebaja is not None and dias <= rebaja[0]:
            detalle += (f' Se vende con {catalogo.texto_de_cantidad(rebaja[1])}% '
                        'de rebaja: va al sector de ofertas.')
    return {
        # Por lote: dos lotes del mismo producto son dos alertas.
        'id': f'{tipo}-{producto.id_producto}-{lote.id_lote}',
        'tipo': tipo,
        'producto': _producto(producto),
        # Con el lote, la app abre «Cargar merma» ya completa.
        'lote': {'id': lote.id_lote, 'numero': lote.numero_lote or '',
                 'cantidad': numero(lote.stock_lote)},
        'detalle': detalle,
        'dias': dias,
        'fecha': lote.fecha_vencimiento.isoformat(),
    }


def listar(tipos, hoy, dias_aviso):
    """Las alertas de hoy de los ``tipos`` pedidos, ya ordenadas."""
    alertas = []
    if set(tipos) & set(DE_STOCK):
        for producto in servicios.bajo_minimo():
            tipo = de_stock(producto)
            if tipo in tipos:
                alertas.append(_de_stock(producto, tipo))
    if set(tipos) & set(DE_VENCIMIENTO):
        for lote in servicios.proximos_a_vencer(dias_aviso, hoy=hoy):
            tipo = de_lote(lote, hoy, dias_aviso)
            if tipo in tipos:
                alertas.append(_de_lote(lote, tipo, hoy))
    alertas.sort(key=lambda alerta: (
        GRAVEDAD.index(alerta['tipo']),
        clave_normalizada(alerta['producto']['nombre']),
        alerta['fecha'] or '', alerta['id']))
    return alertas
