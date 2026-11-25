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
from collections import OrderedDict
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


def _neto_de_cambios_por(ventas, agrupar):
    """``{valor: cobrado - devuelto}`` de los cambios de ``ventas``, agrupados
    por ``agrupar`` (un campo de la venta original: el día, el cliente...).

    Los cambios se atribuyen a la venta ORIGINAL (REP-01): el ticket no se
    anula, se corrige con lo que se cobró de más y lo que se reintegró.
    """
    from negocio import ganancia
    filas = (ganancia.cambios_de(ventas)
             .values(agrupar).order_by()
             .annotate(cobrado=Sum('importe_cobrado'), devuelto=Sum('importe_devuelto')))
    return {f[agrupar]: (f['cobrado'] or CERO) - (f['devuelto'] or CERO) for f in filas}


def ventas_por_dia(desde, hasta, caja=None):
    from ventas.models import Venta
    ventas = _filtrar(Venta.objects.filter(fecha_venta__range=(desde, hasta), estado_venta='COMPLETADA'),
                      caja, 'id_turno_caja__id_caja_registradora')
    filas = (ventas
             .values('fecha_venta').order_by('fecha_venta')
             .annotate(tickets=Count('pk'), total=Sum('total_venta'),
                       recargos=Sum('monto_recargo')))
    # «Vendido» es NETO de los cambios y devoluciones del día de la venta
    # (REP-01); los tickets y los recargos son los de las ventas.
    neto = _neto_de_cambios_por(ventas, 'id_venta__fecha_venta')
    columnas = [C('Día', 'fecha'), C('Tickets', 'numero', conteo=True), C('Vendido', 'dinero'),
                C('Ticket promedio', 'dinero'), C('Recargos', 'dinero')]
    datos = []
    for f in filas:
        vendido = f['total'] + neto.get(f['fecha_venta'], CERO)
        datos.append([f['fecha_venta'], f['tickets'], vendido,
                      (vendido / f['tickets']).quantize(Decimal('0.01')), f['recargos']])
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
    #  Un producto que cambió de nombre tiene un renglón de venta por cada
    #  nombre con el que se vendió (la descripción queda CONGELADA en el
    #  renglón, a propósito): agrupando por nombre, el reporte lo mostraba
    #  como DOS productos distintos, cada uno con una parte de las unidades
    #  y de la ganancia, y el ranking de «lo más vendido» salía mal. Se
    #  agrupa por id_producto, igual que negocio/tablero.py, y se muestra la
    #  ÚLTIMA descripción con la que se vendió (la del renglón más nuevo).
    crudas = (renglones
              .values('id_producto', 'descripcion_item')
              .annotate(cantidad_total=Sum('cantidad'), vendido=Sum('subtotal'),
                        costo_total=Sum(costo), descuentos=Sum('monto_descuento_linea'),
                        ultimo=Max('pk'))
              .order_by('id_producto', 'ultimo'))
    por_id = OrderedDict()
    for fila in crudas:
        actual = por_id.setdefault(fila['id_producto'], {
            'descripcion_item': fila['descripcion_item'], 'cantidad_total': CERO,
            'vendido': CERO, 'costo_total': CERO, 'descuentos': CERO})
        actual['descripcion_item'] = fila['descripcion_item']
        for campo in ('cantidad_total', 'vendido', 'costo_total', 'descuentos'):
            actual[campo] += fila[campo] or CERO
    # REP-01: los renglones de los cambios corrigen a cada producto. Lo que
    # el cliente devuelve resta unidades y plata; lo que se lleva a cambio las
    # suma. El costo se ajusta igual que en ``negocio.ganancia``.
    from negocio import ganancia
    from ventas.models import DetalleCambio
    ajustes = DetalleCambio.objects.filter(id_cambio__id_venta__fecha_venta__range=(desde, hasta),
                                           id_cambio__id_venta__estado_venta='COMPLETADA')
    ajustes = _filtrar(ajustes, rubro, 'id_producto__id_rubro')
    ajustes = _filtrar(ajustes, caja, 'id_cambio__id_venta__id_turno_caja__id_caja_registradora')
    for renglon in (ajustes.select_related('id_producto', 'id_detalle_venta')
                    .prefetch_related('id_producto__composicion__id_componente')
                    .order_by('pk')):
        signo = 1 if renglon.sentido == 'ENTREGA' else -1
        actual = por_id.setdefault(renglon.id_producto_id, {
            'descripcion_item': renglon.id_producto.nombre_producto, 'cantidad_total': CERO,
            'vendido': CERO, 'costo_total': CERO, 'descuentos': CERO})
        actual['cantidad_total'] += signo * renglon.cantidad
        actual['vendido'] += signo * renglon.subtotal
        actual['costo_total'] += ganancia.costo_de_renglon_de_cambio(renglon)
    filas = sorted(por_id.values(), key=lambda f: f['vendido'], reverse=True)
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
    from negocio import ganancia
    from ventas.models import PagoVenta, Venta
    pagos = _filtrar(PagoVenta.objects.filter(id_venta__fecha_venta__range=(desde, hasta),
                                              id_venta__estado_venta='COMPLETADA'),
                     caja, 'id_venta__id_turno_caja__id_caja_registradora')
    filas = (pagos
             .values('id_medio_pago__nombre_medio_pago')
             .annotate(pagos=Count('pk'), cobrado=Sum('monto'), recargos=Sum('monto_recargo'))
             .order_by('-cobrado'))
    columnas = [C('Medio de pago'), C('Pagos', 'numero', conteo=True), C('Cobrado', 'dinero'),
                C('Recargos', 'dinero'), C('Total', 'dinero')]
    por_medio = OrderedDict(
        (f['id_medio_pago__nombre_medio_pago'], [f['pagos'], f['cobrado'], f['recargos']]) for f in filas)
    # REP-01: la diferencia que se cobró por un medio al hacer un cambio suma
    # a lo cobrado, y lo que se reintegró por él resta. No son «pagos» (no
    # cuentan en esa columna) ni llevan recargo propio: el reintegro ya trae
    # su parte del recargo (DD-66), así que el neto va en «Cobrado».
    ventas = _filtrar(Venta.objects.filter(fecha_venta__range=(desde, hasta), estado_venta='COMPLETADA'),
                      caja, 'id_turno_caja__id_caja_registradora')
    cambios = (ganancia.cambios_de(ventas).filter(id_medio_pago__isnull=False)
               .values('id_medio_pago__nombre_medio_pago').order_by()
               .annotate(cobrado=Sum('importe_cobrado'), devuelto=Sum('importe_devuelto')))
    for f in cambios:
        fila = por_medio.setdefault(f['id_medio_pago__nombre_medio_pago'], [0, CERO, CERO])
        fila[1] += (f['cobrado'] or CERO) - (f['devuelto'] or CERO)
    datos = sorted(([medio, pagos_, cobrado, recargos, cobrado + recargos]
                    for medio, (pagos_, cobrado, recargos) in por_medio.items()),
                   key=lambda fila: fila[2], reverse=True)
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
    columnas = [C('Vendió'), C('Tickets', 'numero', conteo=True), C('Vendido', 'dinero'), C('Ticket promedio', 'dinero')]
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
    columnas = [C('Motivo'), C('Autorizó'), C('Renglones o tickets', 'numero', conteo=True), C('Descontado', 'dinero')]
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
    columnas = [C('Cerró'), C('Turnos', 'numero', conteo=True), C('Con diferencia', 'numero', conteo=True),
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
    """Lo que hay en el local, por rubro.

    * «Al costo»: todo el stock, lo apto y lo vencido (sigue siendo plata
      puesta hasta que se dé de baja con una merma), insumos incluidos.
    * «A precio de venta» (REP-02): sólo lo que de verdad se puede vender,
      es decir el stock APTO (sin los lotes vencidos) de los productos
      VENDIBLES. Un insumo no se vende y lo vencido tampoco: valorizarlos a
      precio de góndola inflaba lo que el local cree tener para vender.
    """
    from django.utils import timezone

    from stock.models import TIPOS_CON_STOCK, TIPOS_VENDIBLES, Lote, Producto
    productos_ = list(_filtrar(Producto.objects.filter(
        borrado_logico=False, tipo_producto__in=TIPOS_CON_STOCK, stock_actual__gt=0),
        rubro, 'id_rubro').values('pk', 'id_rubro__nombre_rubro', 'tipo_producto', 'controla_vencimiento',
                                  'stock_actual', 'precio_costo', 'precio_venta'))
    # Lo vencido, por producto y en UNA consulta (como ``Producto.stock_vencido``:
    # sólo aplica a lo que controla vencimiento).
    vencido = {f['id_producto']: f['t'] or CERO for f in (
        Lote.objects.filter(id_producto__in=[p['pk'] for p in productos_ if p['controla_vencimiento']],
                            stock_lote__gt=0, fecha_vencimiento__lt=timezone.localdate())
        .values('id_producto').order_by().annotate(t=Sum('stock_lote')))}
    por_rubro = {}
    for p in productos_:
        fila = por_rubro.setdefault(p['id_rubro__nombre_rubro'], {'productos': 0, 'al_costo': CERO,
                                                                  'a_la_venta': CERO})
        fila['productos'] += 1
        fila['al_costo'] += p['stock_actual'] * p['precio_costo']
        if p['tipo_producto'] in TIPOS_VENDIBLES:
            apto = max(p['stock_actual'] - vencido.get(p['pk'], CERO), CERO)
            fila['a_la_venta'] += apto * p['precio_venta']
    filas = [dict(rubro=nombre, **cuenta) for nombre, cuenta in
             sorted(por_rubro.items(), key=lambda par: par[1]['al_costo'], reverse=True)]
    columnas = [C('Rubro'), C('Productos', 'numero', conteo=True), C('Al costo', 'dinero'),
                C('A precio de venta', 'dinero')]
    datos = [[f['rubro'], f['productos'], f['al_costo'].quantize(Decimal('0.01')),
              f['a_la_venta'].quantize(Decimal('0.01'))] for f in filas]
    return columnas, datos, ['Total', sum((f[1] for f in datos), 0),
                             sum((f[2] for f in datos), CERO), sum((f[3] for f in datos), CERO)]


# ===========================================================================
#  CLUB CAFETO (DFD 9 bloque E)
# ===========================================================================
def _consumo_por_cliente(desde, hasta):
    """Ventas, gasto, ticket promedio y última compra de cada cliente en
    el período, neto de cambios y devoluciones (REP-01): lo que pagó de más
    en un cambio suma y lo que se le reintegró resta."""
    from ventas.models import Cambio, Venta
    ventas = {f['id_cliente']: f for f in (
        Venta.objects.filter(fecha_venta__range=(desde, hasta), estado_venta='COMPLETADA',
                             id_cliente__isnull=False)
        .values('id_cliente')
        .annotate(compras=Count('pk'), gastado=Sum('total_venta'),
                  ultima=Max('fecha_venta'), primera=Min('fecha_venta')))}
    cambios = {f['id_venta__id_cliente']: (f['cobrado'] or CERO) - (f['devuelto'] or CERO) for f in (
        Cambio.objects.filter(id_venta__fecha_venta__range=(desde, hasta), id_venta__estado_venta='COMPLETADA',
                              id_venta__id_cliente__isnull=False)
        .values('id_venta__id_cliente').order_by()
        .annotate(cobrado=Sum('importe_cobrado'), devuelto=Sum('importe_devuelto')))}
    for clave, fila in ventas.items():
        fila['neto'] = fila['gastado'] + cambios.get(clave, CERO)
    return ventas


def ranking(desde, hasta):
    """Los clientes frecuentes, por puntos acumulados (su historia) y con
    lo que compraron en el período."""
    from fidelizacion.models import Cliente
    consumo = _consumo_por_cliente(desde, hasta)
    clientes = (Cliente.objects.filter(borrado_logico=False)
                .select_related('id_categoria')
                .order_by('-puntos_acumulados', 'apellido_cliente')[:100])
    columnas = [C('Puesto', 'numero', conteo=True), C('Cliente'), C('Categoría'),
                C('Puntos acumulados', 'numero'), C('Puntos disponibles', 'numero'),
                C('Compras del período', 'numero', conteo=True), C('Gastado en el período', 'dinero')]
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
    columnas = [C('Cliente'), C('Categoría'), C('Compras', 'numero', conteo=True),
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
    columnas = [C('Categoría'), C('Clientes', 'numero', conteo=True),
                C('Compraron en el período', 'numero', conteo=True),
                C('Sin compras en el período', 'numero', conteo=True),
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
