"""Los reportes del negocio, como consultas.

Cada reporte devuelve ``(columnas, filas, totales)``: la pantalla los
dibuja como tabla y la exportación los baja a Excel o PDF con las mismas
columnas. Todo sale de lo CONGELADO en los documentos (el precio y el
costo de cada renglón de venta, el costo de cada movimiento de stock):
cambiar hoy un precio no reescribe los márgenes de ayer.

Cada reporte se muestra también como gráfico (``negocio.graficos``, Hito 5:
pandas y matplotlib), armado con estas mismas filas. Los tres reportes del
club (DFD 9 bloque E) también están acá.

Filtros de dominio (DD-103): además del período, un reporte puede
acotarse a un rubro o a una caja, cuando el dato lo permite. Cada
``Reporte`` dice cuáles admite (``filtros``) y su consulta los recibe como
argumentos con nombre: ``rubro`` y ``caja`` son claves primarias ya
validadas por la vista.
"""
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Max,
    Min,
    Q,
    Sum,
)
from django.db.models.functions import Coalesce

from nucleo.exportar import ColumnaExportable as C

CERO = Decimal('0')
DINERO = DecimalField(max_digits=14, decimal_places=2)


@dataclass
class Reporte:
    clave: str
    titulo: str
    icono: str
    permiso: str = 'ver_reportes'
    filtros: tuple = ()


REPORTES = (
    Reporte('ventas_dia', 'Ventas por día', 'bi-calendar3', filtros=('caja',)),
    Reporte('productos', 'Productos y márgenes', 'bi-box-seam', filtros=('rubro', 'caja')),
    Reporte('medios', 'Medios de pago', 'bi-credit-card', filtros=('caja',)),
    Reporte('vendedores', 'Por vendedor', 'bi-person-badge', filtros=('caja',)),
    Reporte('descuentos', 'Descuentos', 'bi-percent', filtros=('caja',)),
    Reporte('caja', 'Diferencias de caja', 'bi-cash-stack', filtros=('caja',)),
    Reporte('mermas', 'Mermas', 'bi-trash3', filtros=('rubro',)),
    Reporte('stock', 'Stock valorizado', 'bi-boxes', filtros=('rubro',)),
    Reporte('ranking', 'Ranking de clientes', 'bi-trophy'),
    Reporte('consumo', 'Consumo por cliente', 'bi-bag-heart'),
    Reporte('segmentos', 'Segmentación del club', 'bi-diagram-3'),
)

ETIQUETAS_DE_FILTRO = {'rubro': 'Rubro', 'caja': 'Caja'}


def opciones_de_filtro():
    """Los valores que ofrece cada filtro: ``{clave: [(pk, nombre)]}``.
    Un rubro inactivo sigue: tiene historia que mirar."""
    from stock.models import Rubro
    from ventas.models import CajaRegistradora
    return {
        'rubro': list(Rubro.objects.filter(borrado_logico=False).order_by('nombre_rubro')
                      .values_list('pk', 'nombre_rubro')),
        'caja': list(CajaRegistradora.objects.filter(borrado_logico=False)
                     .order_by('nombre_caja_registradora')
                     .values_list('pk', 'nombre_caja_registradora')),
    }


def _filtrar(consulta, valor, ruta):
    return consulta.filter(**{ruta: valor}) if valor else consulta


def _margen(venta, costo):
    if not venta:
        return None
    return ((venta - costo) / venta * 100).quantize(Decimal('0.1'))


def ventas_por_dia(desde, hasta, caja=None):
    from ventas.models import Venta
    ventas = _filtrar(Venta.objects.filter(fecha_venta__range=(desde, hasta), estado_venta='COMPLETADA'),
                      caja, 'id_turno_caja__id_caja_registradora')
    filas = (ventas
             .values('fecha_venta').order_by('fecha_venta')
             .annotate(tickets=Count('pk'), total=Sum('total_venta'),
                       recargos=Sum('monto_recargo')))
    columnas = [C('Día', 'fecha'), C('Tickets', 'numero'), C('Vendido', 'dinero'),
                C('Ticket promedio', 'dinero'), C('Recargos', 'dinero')]
    datos = [[f['fecha_venta'], f['tickets'], f['total'],
              (f['total'] / f['tickets']).quantize(Decimal('0.01')), f['recargos']]
             for f in filas]
    total = sum((f[2] for f in datos), CERO)
    tickets = sum((f[1] for f in datos), 0)
    return columnas, datos, ['Total', tickets, total,
                             (total / tickets).quantize(Decimal('0.01')) if tickets else None,
                             sum((f[4] for f in datos), CERO)]


def productos(desde, hasta, rubro=None, caja=None):
    from ventas.models import DetalleVenta
    costo = ExpressionWrapper(F('cantidad') * F('costo_unitario'), output_field=DINERO)
    renglones = DetalleVenta.objects.filter(id_venta__fecha_venta__range=(desde, hasta),
                                            id_venta__estado_venta='COMPLETADA')
    renglones = _filtrar(renglones, rubro, 'id_producto__id_rubro')
    renglones = _filtrar(renglones, caja, 'id_venta__id_turno_caja__id_caja_registradora')
    filas = (renglones
             .values('id_producto', 'descripcion_item')
             .annotate(cantidad_total=Sum('cantidad'), vendido=Sum('subtotal'),
                       costo_total=Sum(costo), descuentos=Sum('monto_descuento_linea'))
             .order_by('-vendido'))
    columnas = [C('Producto'), C('Cantidad', 'numero'), C('Vendido', 'dinero'),
                C('Costo', 'dinero'), C('Ganancia', 'dinero'), C('Margen %', 'numero'),
                C('Descuentos', 'dinero')]
    datos = [[f['descripcion_item'], f['cantidad_total'], f['vendido'], f['costo_total'],
              f['vendido'] - f['costo_total'], _margen(f['vendido'], f['costo_total']),
              f['descuentos']] for f in filas]
    vendido = sum((f[2] for f in datos), CERO)
    costo_total = sum((f[3] for f in datos), CERO)
    return columnas, datos, ['Total', None, vendido, costo_total, vendido - costo_total,
                             _margen(vendido, costo_total), sum((f[6] for f in datos), CERO)]


def medios(desde, hasta, caja=None):
    from ventas.models import PagoVenta
    pagos = _filtrar(PagoVenta.objects.filter(id_venta__fecha_venta__range=(desde, hasta),
                                              id_venta__estado_venta='COMPLETADA'),
                     caja, 'id_venta__id_turno_caja__id_caja_registradora')
    filas = (pagos
             .values('id_medio_pago__nombre_medio_pago')
             .annotate(pagos=Count('pk'), cobrado=Sum('monto'), recargos=Sum('monto_recargo'))
             .order_by('-cobrado'))
    columnas = [C('Medio de pago'), C('Pagos', 'numero'), C('Cobrado', 'dinero'),
                C('Recargos', 'dinero'), C('Total', 'dinero')]
    datos = [[f['id_medio_pago__nombre_medio_pago'], f['pagos'], f['cobrado'], f['recargos'],
              f['cobrado'] + f['recargos']] for f in filas]
    return columnas, datos, ['Total', sum((f[1] for f in datos), 0),
                             sum((f[2] for f in datos), CERO), sum((f[3] for f in datos), CERO),
                             sum((f[4] for f in datos), CERO)]


def vendedores(desde, hasta, caja=None):
    from ventas.models import Venta
    ventas = _filtrar(Venta.objects.filter(fecha_venta__range=(desde, hasta), estado_venta='COMPLETADA'),
                      caja, 'id_turno_caja__id_caja_registradora')
    filas = (ventas
             .values('id_usuario__apellido', 'id_usuario__nombre')
             .annotate(tickets=Count('pk'), total=Sum('total_venta'))
             .order_by('-total'))
    columnas = [C('Vendió'), C('Tickets', 'numero'), C('Vendido', 'dinero'), C('Ticket promedio', 'dinero')]
    datos = [[f"{f['id_usuario__apellido']}, {f['id_usuario__nombre']}", f['tickets'], f['total'],
              (f['total'] / f['tickets']).quantize(Decimal('0.01'))] for f in filas]
    return columnas, datos, None


def descuentos(desde, hasta, caja=None):
    from ventas.models import DetalleVenta, MotivoDescuento
    renglones = _filtrar(DetalleVenta.objects.filter(
        id_venta__fecha_venta__range=(desde, hasta), id_venta__estado_venta='COMPLETADA',
        monto_descuento_linea__gt=0), caja, 'id_venta__id_turno_caja__id_caja_registradora')
    filas = (renglones
             .values('motivo_descuento', 'id_usuario_autorizante__apellido',
                     'id_usuario_autorizante__nombre')
             .annotate(renglones=Count('pk'), monto=Sum('monto_descuento_linea'))
             .order_by('-monto'))
    etiquetas = dict(MotivoDescuento.choices)
    columnas = [C('Motivo'), C('Autorizó'), C('Renglones o tickets', 'numero'), C('Descontado', 'dinero')]
    datos = [[etiquetas.get(f['motivo_descuento'], f['motivo_descuento']),
              (f"{f['id_usuario_autorizante__apellido']}, {f['id_usuario_autorizante__nombre']}"
               if f['id_usuario_autorizante__apellido'] else 'Dentro del tope'),
              f['renglones'], f['monto']] for f in filas]
    # El beneficio de descuento del club va en la cabecera (DD-18).
    from ventas.models import Venta
    cabecera = (_filtrar(Venta.objects.filter(fecha_venta__range=(desde, hasta), estado_venta='COMPLETADA',
                                              monto_descuento__gt=0),
                         caja, 'id_turno_caja__id_caja_registradora')
                .values('id_beneficio__descripcion_beneficio')
                .annotate(ventas=Count('pk'), monto=Sum('monto_descuento'))
                .order_by('-monto'))
    datos += [[f"Beneficio del club: {f['id_beneficio__descripcion_beneficio']}",
               'Por categoría o canje', f['ventas'], f['monto']]
              for f in cabecera]
    return columnas, datos, ['Total', None, sum((f[2] for f in datos), 0),
                             sum((f[3] for f in datos), CERO)]


def caja(desde, hasta, caja=None):
    from ventas.models import TurnoCaja
    filas = (_filtrar(TurnoCaja.objects.filter(fecha_cierre__date__range=(desde, hasta)),
                      caja, 'id_caja_registradora')
             .values('id_usuario_cierre__apellido', 'id_usuario_cierre__nombre')
             .annotate(turnos=Count('pk'),
                       con_diferencia=Count('pk', filter=~Q(diferencia_caja=0)),
                       faltante=Coalesce(Sum('diferencia_caja', filter=Q(diferencia_caja__lt=0)), CERO, output_field=DINERO),
                       sobrante=Coalesce(Sum('diferencia_caja', filter=Q(diferencia_caja__gt=0)), CERO, output_field=DINERO),
                       neto=Sum('diferencia_caja'))
             .order_by('neto'))
    columnas = [C('Cerró'), C('Turnos', 'numero'), C('Con diferencia', 'numero'),
                C('Faltantes', 'dinero'), C('Sobrantes', 'dinero'), C('Neto', 'dinero')]
    datos = [[f"{f['id_usuario_cierre__apellido']}, {f['id_usuario_cierre__nombre']}", f['turnos'],
              f['con_diferencia'], f['faltante'], f['sobrante'], f['neto']] for f in filas]
    return columnas, datos, ['Total', sum((f[1] for f in datos), 0), sum((f[2] for f in datos), 0),
                             sum((f[3] for f in datos), CERO), sum((f[4] for f in datos), CERO),
                             sum((f[5] for f in datos), CERO)]


def mermas(desde, hasta, rubro=None):
    from stock.models import DetalleAjuste, MotivoAjuste
    valor = ExpressionWrapper(F('cantidad') * F('costo_unitario'), output_field=DINERO)
    filas = (_filtrar(DetalleAjuste.objects
                      .filter(id_ajuste__tipo_ajuste='MERMA', id_ajuste__estado_ajuste='APROBADO',
                              id_ajuste__fecha_resolucion__date__range=(desde, hasta)),
                      rubro, 'id_producto__id_rubro')
             .values('id_ajuste__motivo', 'id_producto__nombre_producto')
             .annotate(cantidad_total=Sum('cantidad'), valor=Sum(valor))
             .order_by('-valor'))
    etiquetas = dict(MotivoAjuste.choices)
    columnas = [C('Motivo'), C('Producto'), C('Cantidad', 'numero'), C('Valor al costo', 'dinero')]
    datos = [[etiquetas.get(f['id_ajuste__motivo']), f['id_producto__nombre_producto'],
              f['cantidad_total'], f['valor']] for f in filas]
    return columnas, datos, ['Total', None, None, sum((f[3] for f in datos), CERO)]


def stock_valorizado(desde=None, hasta=None, rubro=None):
    from stock.models import TIPOS_CON_STOCK, Producto
    valor = ExpressionWrapper(F('stock_actual') * F('precio_costo'), output_field=DINERO)
    venta = ExpressionWrapper(F('stock_actual') * F('precio_venta'), output_field=DINERO)
    filas = (_filtrar(Producto.objects.filter(borrado_logico=False, tipo_producto__in=TIPOS_CON_STOCK,
                                              stock_actual__gt=0), rubro, 'id_rubro')
             .values('id_rubro__nombre_rubro')
             .annotate(productos=Count('pk'), al_costo=Sum(valor), a_la_venta=Sum(venta))
             .order_by('-al_costo'))
    columnas = [C('Rubro'), C('Productos', 'numero'), C('Al costo', 'dinero'),
                C('A precio de venta', 'dinero')]
    datos = [[f['id_rubro__nombre_rubro'], f['productos'], f['al_costo'], f['a_la_venta']]
             for f in filas]
    return columnas, datos, ['Total', sum((f[1] for f in datos), 0),
                             sum((f[2] for f in datos), CERO), sum((f[3] for f in datos), CERO)]


# ===========================================================================
#  CLUB CAFETO (DFD 9 bloque E)
# ===========================================================================
def _consumo_por_cliente(desde, hasta):
    """Ventas, gasto, ticket promedio y última compra de cada cliente en
    el período, con lo devuelto ya descontado."""
    from ventas.models import Cambio, Venta
    ventas = {f['id_cliente']: f for f in (
        Venta.objects.filter(fecha_venta__range=(desde, hasta), estado_venta='COMPLETADA',
                             id_cliente__isnull=False)
        .values('id_cliente')
        .annotate(compras=Count('pk'), gastado=Sum('total_venta'),
                  ultima=Max('fecha_venta'), primera=Min('fecha_venta')))}
    devuelto = {f['id_venta__id_cliente']: f['t'] for f in (
        Cambio.objects.filter(id_venta__fecha_venta__range=(desde, hasta), id_venta__estado_venta='COMPLETADA',
                              id_venta__id_cliente__isnull=False)
        .values('id_venta__id_cliente').annotate(t=Sum('importe_devuelto')))}
    for clave, fila in ventas.items():
        fila['neto'] = fila['gastado'] - (devuelto.get(clave) or CERO)
    return ventas


def ranking(desde, hasta):
    """Los clientes frecuentes, por puntos acumulados (su historia) y con
    lo que compraron en el período."""
    from fidelizacion.models import Cliente
    consumo = _consumo_por_cliente(desde, hasta)
    clientes = (Cliente.objects.filter(borrado_logico=False)
                .select_related('id_categoria')
                .order_by('-puntos_acumulados', 'apellido_cliente')[:100])
    columnas = [C('Puesto', 'numero'), C('Cliente'), C('Categoría'),
                C('Puntos acumulados', 'numero'), C('Puntos disponibles', 'numero'),
                C('Compras del período', 'numero'), C('Gastado en el período', 'dinero')]
    datos = []
    for puesto, c in enumerate(clientes, start=1):
        f = consumo.get(c.pk, {})
        datos.append([puesto, c.nombre_completo, str(c.id_categoria),
                      c.puntos_acumulados, c.puntos_disponibles,
                      f.get('compras', 0), f.get('neto', CERO)])
    return columnas, datos, None


def consumo(desde, hasta):
    """Comportamiento de consumo: cada cuánto compra y cuánto gasta."""
    from fidelizacion.models import Cliente
    filas = _consumo_por_cliente(desde, hasta)
    nombres = {c.pk: c for c in Cliente.objects.filter(pk__in=filas)
               .select_related('id_categoria')}
    columnas = [C('Cliente'), C('Categoría'), C('Compras', 'numero'),
                C('Gastado (neto de devoluciones)', 'dinero'),
                C('Ticket promedio', 'dinero'), C('Días entre compras', 'numero'),
                C('Última compra', 'fecha')]
    datos = []
    for clave, f in sorted(filas.items(), key=lambda kv: -kv[1]['neto']):
        cliente = nombres[clave]
        cada = None
        if f['compras'] > 1:
            cada = (Decimal((f['ultima'] - f['primera']).days)
                    / (f['compras'] - 1)).quantize(Decimal('0.1'))
        datos.append([cliente.nombre_completo, str(cliente.id_categoria),
                      f['compras'], f['neto'],
                      (f['neto'] / f['compras']).quantize(Decimal('0.01')),
                      cada, f['ultima']])
    compras = sum((d[2] for d in datos), 0)
    neto = sum((d[3] for d in datos), CERO)
    return columnas, datos, ['Total', None, compras, neto,
                             (neto / compras).quantize(Decimal('0.01')) if compras else None,
                             None, None]


def segmentos(desde, hasta):
    """Para las promociones: cuántos clientes hay en cada categoría, cuántos
    compraron en el período y cuánto gastaron."""
    from fidelizacion.models import CategoriaFidelizacion, Cliente
    consumo_ = _consumo_por_cliente(desde, hasta)
    columnas = [C('Categoría'), C('Clientes', 'numero'),
                C('Compraron en el período', 'numero'),
                C('Sin compras en el período', 'numero'),
                C('Gastado en el período', 'dinero'), C('Ticket promedio', 'dinero')]
    datos = []
    for categoria in CategoriaFidelizacion.objects.filter(
            borrado_logico=False).order_by('puntos_minimos'):
        ids = list(Cliente.objects.filter(
            borrado_logico=False, id_categoria=categoria).values_list('pk', flat=True))
        activos = [consumo_[i] for i in ids if i in consumo_]
        gastado = sum((a['neto'] for a in activos), CERO)
        compras = sum((a['compras'] for a in activos), 0)
        datos.append([categoria.nombre_categoria, len(ids), len(activos),
                      len(ids) - len(activos), gastado,
                      (gastado / compras).quantize(Decimal('0.01')) if compras else None])
    return columnas, datos, ['Total', sum(d[1] for d in datos),
                             sum(d[2] for d in datos), sum(d[3] for d in datos),
                             sum((d[4] for d in datos), CERO), None]


CONSULTAS = {
    'ventas_dia': ventas_por_dia, 'productos': productos, 'medios': medios,
    'vendedores': vendedores, 'descuentos': descuentos, 'caja': caja,
    'mermas': mermas, 'stock': stock_valorizado,
    'ranking': ranking, 'consumo': consumo, 'segmentos': segmentos,
}
