"""Qué se muestra de cada producto y con qué reglas.

Las reglas son las de Stock, no unas propias de la app:

* **Sección.** CAFETERIA es lo elaborado, lo producido y los combos;
  MINISUPER, la reventa. Un INSUMO no se publica nunca: no se vende.
* **Oferta.** La rebaja por vencimiento de ``Producto.rebaja_vigente()``
  (la del producto manda sobre la del rubro) rige si el lote que sale
  primero por FEFO —el vigente que vence antes— está dentro de esos días.
  Es la regla de ``stock.servicios.en_oferta()`` (las etiquetas de la
  góndola) y de ``planificar_salida()`` (lo que se cobra), con el precio
  redondeado como en la etiqueta.
* **Disponible.** Lo que tiene stock propio, si le queda stock sin vencer
  (lo vencido no se vende); lo que se arma al momento, si alcanzan sus
  componentes: el mismo tope que pone el mostrador
  (``stock.servicios.disponible()``).

Los lotes con stock de cada producto se traen de una vez para toda la
página (``prefetch``), en el orden en que salen.
"""
from decimal import Decimal

from django.db.models import F, Prefetch, Q

from api_movil.respuestas import numero
from fidelizacion.models import (
    Beneficio,
    CategoriaFidelizacion,
    ModoEntrega,
    TipoBeneficio,
)
from nucleo.validaciones import clave_normalizada
from stock import servicios
from stock.models import (
    MARCA_GENERICA,
    ComposicionProducto,
    Lote,
    Producto,
    Rubro,
    TipoProducto,
    UnidadMedida,
)

SECCIONES = {
    'CAFETERIA': (TipoProducto.ELABORADO, TipoProducto.PRODUCIDO,
                  TipoProducto.COMBO),
    'MINISUPER': (TipoProducto.REVENTA,),
}
TIPOS_PUBLICOS = SECCIONES['CAFETERIA'] + SECCIONES['MINISUPER']
SECCION_INVALIDA = 'Elegí Cafetería o Minisuper.'

MAXIMO_DESTACADOS = 8

# Las dos marcas que NO son una marca: «Sin marca», la de lo que se vende
# suelto o se elabora en el local (stock/migrations/0003), y «Genérica», la
# reservada del alta simple y del importador (stock/migrations/0007). A la
# app las dos van como "sin marca".
#
# Acá estaba sólo 'Sin marca', escrita a mano: «Genérica» —que es la que le
# pone el importador a los cientos de artículos que entran de la planilla—
# viajaba como si fuera una marca de verdad y la app la mostraba arriba del
# nombre del producto, mientras el mostrador web la ocultaba
# (``ventas/views/mostrador.py``). El nombre sale de
# ``stock.models.MARCA_GENERICA``, el que usa la base, y no de una copia.
# Es la misma lista que mira ``nucleo/management/commands/auditar_consistencia.py``.
MARCAS_SIN_MARCA = frozenset({clave_normalizada('Sin marca'),
                              clave_normalizada(MARCA_GENERICA)})

# Un ícono de Ionicons por tipo, mientras el producto no tenga foto.
ICONOS = {
    TipoProducto.COMBO: 'fast-food-outline',
    TipoProducto.ELABORADO: 'cafe-outline',
    TipoProducto.PRODUCIDO: 'restaurant-outline',
    TipoProducto.REVENTA: 'basket-outline',
    TipoProducto.INSUMO: 'cube-outline',
}
UNIDADES = {UnidadMedida.G: 'g', UnidadMedida.KG: 'kg',
            UnidadMedida.ML: 'ml', UnidadMedida.L: 'L'}


# ---------------------------------------------------------------------------
#  Las consultas
# ---------------------------------------------------------------------------
def _lotes_con_stock():
    """Los lotes con stock, en el orden de ``servicios.lotes_fefo``."""
    return Prefetch(
        'lotes',
        queryset=Lote.objects.filter(stock_lote__gt=0).order_by(
            F('fecha_vencimiento').asc(), 'fecha_alta', 'id_lote'),
        to_attr='lotes_con_stock')


def _con_detalle(consulta):
    return (consulta.select_related('id_rubro', 'id_marca')
            .prefetch_related(
                _lotes_con_stock(),
                Prefetch('composicion',
                         queryset=ComposicionProducto.objects
                         .select_related('id_componente')
                         .order_by('id_composicion'))))


def _publicables():
    """Lo que puede ver cualquiera: vigente, vendible, con precio y en un
    rubro vigente. Un insumo, nunca."""
    return Producto.objects.filter(
        borrado_logico=False, precio_venta__gt=0,
        tipo_producto__in=TIPOS_PUBLICOS, id_rubro__borrado_logico=False)


def publicables():
    return _con_detalle(_publicables())


def de_la_seccion(seccion):
    return publicables().filter(tipo_producto__in=SECCIONES[seccion])


def del_equipo():
    """Todo lo vigente, insumos incluidos: la consulta es para el equipo."""
    return _con_detalle(Producto.objects.filter(borrado_logico=False))


def rubros_de(seccion):
    """Los rubros vigentes con algo publicado en la sección (los filtros)."""
    usados = _publicables().filter(
        tipo_producto__in=SECCIONES[seccion]).values('id_rubro')
    return Rubro.objects.filter(borrado_logico=False,
                                id_rubro__in=usados).order_by('nombre_rubro', 'pk')


# ---------------------------------------------------------------------------
#  Las reglas de cada producto
# ---------------------------------------------------------------------------
def lotes(producto):
    """Los lotes con stock del producto, del que sale primero al último."""
    if not producto.controla_vencimiento:
        return []
    if not hasattr(producto, 'lotes_con_stock'):
        producto.lotes_con_stock = list(servicios.lotes_fefo(producto))
    return producto.lotes_con_stock


def proximo_lote(producto, hoy):
    """El lote vigente que sale primero (FEFO). Los vencidos no salen."""
    return next((lote for lote in lotes(producto)
                 if lote.fecha_vencimiento >= hoy), None)


def vencido(producto, hoy):
    """Cuánto del stock está en lotes ya vencidos (``servicios.vencido``)."""
    return sum((lote.stock_lote for lote in lotes(producto)
                if lote.fecha_vencimiento < hoy), Decimal('0'))


def oferta(producto, hoy):
    """``(precio con rebaja, porcentaje)`` si hoy se vende rebajado; si no,
    ``(None, None)``."""
    if not producto.es_vendible:
        return None, None
    rebaja = producto.rebaja_vigente()
    lote = proximo_lote(producto, hoy) if rebaja else None
    if lote is None or lote.dias_para_vencer(hoy) > rebaja[0]:
        return None, None
    precio = (producto.precio_venta * (100 - rebaja[1]) / 100).quantize(
        Decimal('1'))
    if precio >= producto.precio_venta:
        # Redondeado al peso, un precio ínfimo puede quedar igual: no hay
        # rebaja que anunciar (y la app la descartaría).
        return None, None
    return precio, rebaja[1]


def disponible(producto, hoy):
    if producto.tiene_stock_propio:
        return producto.stock_actual - vencido(producto, hoy) > 0
    tope = servicios.disponible(producto)
    return tope is None or tope > 0


def seccion(producto):
    return next((clave for clave, tipos in SECCIONES.items()
                 if producto.tipo_producto in tipos), None)


def texto_de_cantidad(valor):
    """2 -> '2'; 1.5 -> '1,5': como se lee en pantalla."""
    return format(Decimal(valor).normalize(), 'f').replace('.', ',')


def presentacion(producto):
    """'Combo', 'Por kg', 'Unidad', '6 unidades', '500 ml', '1,5 L'."""
    if producto.tipo_producto == TipoProducto.COMBO:
        return 'Combo'
    if producto.fraccionable:
        return ('Por kg' if producto.unidad_medida == UnidadMedida.KG
                else 'Por litro')
    contenido = texto_de_cantidad(producto.contenido)
    if producto.unidad_medida == UnidadMedida.UNIDAD:
        return 'Unidad' if producto.contenido == 1 else f'{contenido} unidades'
    unidad = UNIDADES.get(producto.unidad_medida, producto.unidad_medida.lower())
    return f'{contenido} {unidad}'


def descripcion(producto):
    """Lo que trae un combo: "Café con leche y Medialuna ×2".

    ``Producto`` no tiene descripción propia; la de un combo sale de su
    contenido, que es lo que el cliente quiere saber.
    """
    if producto.tipo_producto != TipoProducto.COMBO:
        return ''
    partes = [item.id_componente.nombre_producto
              + ('' if item.cantidad == 1
                 else f' ×{texto_de_cantidad(item.cantidad)}')
              for item in producto.composicion.all()]
    if len(partes) < 2:
        return ''.join(partes)
    return ', '.join(partes[:-1]) + ' y ' + partes[-1]


def marca(producto):
    if clave_normalizada(producto.id_marca.nombre_marca) in MARCAS_SIN_MARCA:
        return None
    return {'id': producto.id_marca_id, 'nombre': producto.id_marca.nombre_marca}


def imagen(producto):
    """La foto como dirección completa, o None.

    Una foto de otro servidor ya es ``https://…``. Una subida al sistema
    necesita el dominio adelante: la app no comparte origen con el servidor,
    así que se arma con ``CAFETO_URL_PUBLICA``."""
    from django.conf import settings
    from stock import imagenes

    url = imagenes.url_de(producto)
    if url and url.startswith('/'):
        url = settings.CAFETO_URL_PUBLICA + url
    return url


def a_json(producto, hoy, motivo=None):
    """Un producto como lo muestra el catálogo de la app."""
    precio_oferta, porcentaje = oferta(producto, hoy)
    datos = {
        'id': producto.id_producto,
        'nombre': producto.nombre_producto,
        'descripcion': descripcion(producto),
        'presentacion': presentacion(producto),
        'precio': numero(producto.precio_venta),
        'precio_oferta': numero(precio_oferta),
        'porcentaje_oferta': numero(porcentaje),
        'seccion': seccion(producto),
        'tipo': producto.tipo_producto,
        'rubro': {'id': producto.id_rubro_id,
                  'nombre': producto.id_rubro.nombre_rubro},
        'marca': marca(producto),
        # Una dirección completa o None; con None la app muestra el ícono.
        'imagen': imagen(producto),
        'icono': ICONOS.get(producto.tipo_producto),
        'disponible': disponible(producto, hoy),
    }
    if motivo:
        datos['motivo'] = motivo
    return datos


# ---------------------------------------------------------------------------
#  Lo que arma cada pantalla
# ---------------------------------------------------------------------------
def destacados(hoy):
    """El carrusel del Inicio: los combos y después las ofertas del día.

    Las ofertas van de la rebaja más grande a la más chica. Nada agotado,
    y no más de ``MAXIMO_DESTACADOS``.
    """
    elegidos = []
    for producto in publicables().filter(
            tipo_producto=TipoProducto.COMBO).order_by('nombre_producto', 'pk'):
        datos = a_json(producto, hoy, 'COMBO')
        if datos['disponible']:
            elegidos.append(datos)
        if len(elegidos) == MAXIMO_DESTACADOS:
            return elegidos

    ofertas = []
    # Sólo lo que controla vencimiento puede tener rebaja por vencimiento.
    for producto in publicables().filter(controla_vencimiento=True).order_by(
            'nombre_producto', 'pk'):
        datos = a_json(producto, hoy, 'OFERTA')
        if datos['precio_oferta'] is not None and datos['disponible']:
            ofertas.append(datos)
    ofertas.sort(key=lambda datos: -datos['porcentaje_oferta'])
    return (elegidos + ofertas)[:MAXIMO_DESTACADOS]


def categorias_vigentes():
    """Las categorías del club, de la de ingreso a la más alta."""
    return CategoriaFidelizacion.objects.filter(borrado_logico=False).order_by(
        'puntos_minimos')


def beneficios_vigentes(hoy):
    """Los beneficios del club que valen hoy, por categoría y por puntos."""
    return (Beneficio.objects
            .filter(borrado_logico=False, id_categoria__borrado_logico=False,
                    vigencia_desde__lte=hoy)
            .filter(Q(vigencia_hasta__isnull=True) | Q(vigencia_hasta__gte=hoy))
            .select_related('id_categoria')
            .order_by('id_categoria__puntos_minimos',
                      F('puntos_requeridos').asc(nulls_first=True),
                      'descripcion_beneficio', 'pk'))


def beneficio_a_json(beneficio):
    if beneficio.modo_entrega == ModoEntrega.DESCUENTO:
        porcentaje = (f'{texto_de_cantidad(beneficio.porcentaje_descuento)}% '
                      'de descuento')
        detalle = (f'{porcentaje}: se aplica solo al pagar'
                   if beneficio.tipo_beneficio == TipoBeneficio.AUTOMATICO
                   else f'{porcentaje} en tu compra, canjeando tus puntos')
        icono = 'pricetag-outline'
    else:
        detalle = 'Retirás en el mostrador'
        icono = 'gift-outline'
    return {
        'id': beneficio.id_beneficio,
        'titulo': beneficio.descripcion_beneficio,
        'detalle': detalle,
        'tipo': beneficio.tipo_beneficio,
        'puntos': beneficio.puntos_requeridos or 0,
        'categoria': beneficio.id_categoria.nombre_categoria,
        'icono': icono,
    }
