"""El proveedor habitual de un producto se mantiene solo (DD-89).

``PRODUCTOS.id_proveedor`` es a quién se le pide normalmente ese producto:
arma el pedido y los filtros. No es "a quién se le compró la última vez":
una compra de urgencia en otro lado no tiene que desarmar el pedido del
martes. Pero si las compras cambiaron de verdad, tampoco tiene que quedar
apuntando a alguien a quien ya no se le compra.

Reglas, al recibir una compra de un producto a un proveedor que no es el
habitual:

1. El habitual es el «Proveedor General (Por asignar)» -> pasa a ser éste
   (si la casilla de la recepción está tildada).
2. Las últimas TRES compras del producto, con ésta, fueron a este mismo
   proveedor -> es el nuevo habitual.
3. Al habitual se le compró este producto alguna vez, pero hace más de 180
   días -> es el nuevo habitual.

Un habitual al que NUNCA se le compró el producto (alguien lo eligió a mano
ayer) no cuenta como inactivo: lo decidió una persona y sólo lo cambia la
regla 2.
"""
from datetime import timedelta

from django.db.models import Exists, Max, OuterRef
from django.utils import timezone

from compras.models import Compra, DetalleCompra, Egreso, Proveedor
from stock.models import Producto

SEGUIDAS = 3
DIAS_DE_INACTIVIDAD = 180


def _compras_de(producto):
    return (DetalleCompra.objects.filter(id_producto=producto)
            .exclude(id_compra__estado_compra='ANULADA')
            .order_by('-id_compra__fecha_compra', '-id_compra_id'))


def al_recibir(producto, proveedor, vincular=True, hoy=None):
    """Decide si ``proveedor`` pasa a ser el habitual de ``producto`` y, si
    corresponde, lo cambia. Se llama con la compra ya registrada.

    Devuelve None o el motivo: 'SIN_ASIGNAR', 'FRECUENCIA' o 'INACTIVIDAD'.
    """
    actual = producto.id_proveedor
    if actual is None or actual.pk == proveedor.pk or proveedor.es_el_general:
        return None
    hoy = hoy or timezone.localdate()
    motivo = None
    if actual.es_el_general:
        motivo = 'SIN_ASIGNAR' if vincular else None
    else:
        ultimas = list(_compras_de(producto).values_list('id_compra__id_proveedor', flat=True)[:SEGUIDAS])
        if len(ultimas) == SEGUIDAS and set(ultimas) == {proveedor.pk}:
            motivo = 'FRECUENCIA'
        else:
            ultima_al_actual = _compras_de(producto).filter(
                id_compra__id_proveedor=actual).values_list('id_compra__fecha_compra', flat=True).first()
            if ultima_al_actual and ultima_al_actual < hoy - timedelta(days=DIAS_DE_INACTIVIDAD):
                motivo = 'INACTIVIDAD'
    if motivo:
        Producto.objects.filter(pk=producto.pk).update(id_proveedor=proveedor)
        producto.id_proveedor = proveedor
    return motivo


def inactivos(hoy=None):
    """Proveedores vigentes sin movimiento hace más de 180 días: ni una
    compra, ni un pago, ni nada pendiente de pagar. Los recién dados de alta
    no cuentan (todavía no tuvieron tiempo de moverse)."""
    hoy = hoy or timezone.localdate()
    limite = hoy - timedelta(days=DIAS_DE_INACTIVIDAD)
    compras = Compra.objects.filter(id_proveedor=OuterRef('pk')).exclude(estado_compra='ANULADA')
    egresos = Egreso.objects.filter(id_proveedor=OuterRef('pk'))
    return (Proveedor.objects.filter(borrado_logico=False, fecha_alta__lt=limite)
            .annotate(
                compro=Exists(compras.filter(fecha_compra__gte=limite)),
                pago=Exists(egresos.filter(fecha_egreso__gte=limite)),
                debe=Exists(egresos.filter(estado_pago='PENDIENTE_PAGO')),
                sin_saldar=Exists(compras.exclude(estado_compra='PAGADA')),
                ultima_compra=Max('compras__fecha_compra'))
            .filter(compro=False, pago=False, debe=False, sin_saldar=False)
            .order_by('nombre_proveedor'))


def reasignar_los_de_inactivos(hoy=None):
    """Los productos que apuntan a un proveedor inactivo pasan al último al
    que sí se les compró en los últimos 180 días. Si no hubo ninguno y el
    producto no tiene stock, vuelven al Proveedor General. Si tiene stock y
    nadie se lo vendió en seis meses, se deja como está: no hay a quién
    pasarlo con fundamento.

    Devuelve [(producto, anterior, nuevo)].
    """
    from stock.reservados import proveedor_general

    hoy = hoy or timezone.localdate()
    limite = hoy - timedelta(days=DIAS_DE_INACTIVIDAD)
    general = proveedor_general()
    cambios = []
    quietos = [p.pk for p in inactivos(hoy) if not p.es_el_general]
    for producto in Producto.objects.filter(
            borrado_logico=False, id_proveedor__in=quietos).select_related('id_proveedor'):
        reciente = (_compras_de(producto)
                    .filter(id_compra__fecha_compra__gte=limite,
                            id_compra__id_proveedor__borrado_logico=False)
                    .exclude(id_compra__id_proveedor=producto.id_proveedor)
                    .select_related('id_compra__id_proveedor').first())
        nuevo = (reciente.id_compra.id_proveedor if reciente
                 else general if producto.stock_actual <= 0 else None)
        if nuevo is None or nuevo.pk == producto.id_proveedor_id:
            continue
        anterior = producto.id_proveedor
        Producto.objects.filter(pk=producto.pk).update(id_proveedor=nuevo)
        cambios.append((producto, anterior, nuevo))
    return cambios
