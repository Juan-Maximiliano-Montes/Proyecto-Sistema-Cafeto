"""Los números del tablero interactivo de Reportes y del Resumen del mes
(DD-100).

Mismas definiciones que el Resumen del mes (``negocio.mensual``), día por
día, para que la suma de las barras dé lo mismo que el resultado del mes:

    ingresos = vendido (ventas COMPLETADAS) + recargos por mora cobrados
    egresos  = costo de lo vendido + gastos pagados + mermas + incobrables
    ganancia neta = ingresos − egresos

Los pagos a proveedores de mercadería NO son egresos acá: ese costo ya está
en el costo de lo vendido (contarlo dos veces es el error clásico). Los
importes viajan como texto con dos decimales: el navegador los muestra, no
hace cuentas con ellos.
"""
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Abs, ExtractHour, TruncDate

from compras.models import Egreso
from fidelizacion.models import MovimientoCuenta
from negocio import ganancia
from negocio.mensual import PAGADOS
from stock.models import MovimientoStock, TipoMovimientoStock
from ventas.models import DetalleVenta, Venta

CERO = Decimal('0')
CENTAVO = Decimal('0.01')
TOP = 10
DIAS_POR_DIA = 62          # hasta dos meses, una barra por día
DIAS_POR_SEMANA = 400      # hasta un año y monedas, una por semana; después, por mes
CAMPOS_DIA = ('vendido', 'interes', 'costo', 'gastos', 'mermas', 'incobrable')
_IMPORTE = DecimalField(max_digits=16, decimal_places=4)


def _t(valor):
    return str((valor or CERO).quantize(CENTAVO))


def _por_dia(consulta, campo_fecha, campo_valor, destino, clave):
    for fila in consulta:
        dia = fila[campo_fecha]
        if dia in destino:
            destino[dia][clave] += fila[campo_valor] or CERO


def _dias(desde, hasta):
    dias = OrderedDict()
    dia = desde
    while dia <= hasta:
        dias[dia] = {c: CERO for c in CAMPOS_DIA}
        dia += timedelta(days=1)
    ventas = ganancia.completadas(desde, hasta)
    _por_dia(ventas.order_by().values('fecha_venta').annotate(t=Sum('total_venta')),
             'fecha_venta', 't', dias, 'vendido')
    _por_dia(DetalleVenta.objects.filter(id_venta__in=ventas.order_by().values('pk'))
             .values('id_venta__fecha_venta').annotate(t=Sum(ganancia.COSTO_DEL_RENGLON)).order_by(),
             'id_venta__fecha_venta', 't', dias, 'costo')
    _por_dia(Egreso.objects.filter(estado_pago__in=PAGADOS, id_compra__isnull=True,
                                   fecha_egreso__gte=desde, fecha_egreso__lte=hasta)
             .values('fecha_egreso').annotate(t=Sum('monto')).order_by(),
             'fecha_egreso', 't', dias, 'gastos')
    _por_dia(MovimientoStock.objects.filter(tipo_movimiento=TipoMovimientoStock.MERMA,
                                            fecha_movimiento__date__gte=desde, fecha_movimiento__date__lte=hasta)
             .annotate(dia=TruncDate('fecha_movimiento')).values('dia')
             .annotate(t=Sum(ExpressionWrapper(Abs(F('cantidad')) * F('costo_unitario'), output_field=_IMPORTE)))
             .order_by(), 'dia', 't', dias, 'mermas')
    cuenta = (MovimientoCuenta.objects.filter(fecha_movimiento__date__gte=desde, fecha_movimiento__date__lte=hasta,
                                              tipo_movimiento__in=('INTERES', 'INCOBRABLE'))
              .annotate(dia=TruncDate('fecha_movimiento')).values('dia', 'tipo_movimiento')
              .annotate(t=Sum('monto')).order_by())
    for fila in cuenta:
        if fila['dia'] in dias:
            dias[fila['dia']]['interes' if fila['tipo_movimiento'] == 'INTERES' else 'incobrable'] += fila['t']
    return dias


def _agrupar(dias, desde, hasta):
    """Por día, semana o mes según el largo del período."""
    largo = (hasta - desde).days + 1
    if largo <= DIAS_POR_DIA:
        return 'día', [(d, d, d.strftime('%d/%m'), v) for d, v in dias.items()]
    grupos = OrderedDict()
    for dia, valores in dias.items():
        if largo <= DIAS_POR_SEMANA:
            inicio = dia - timedelta(days=dia.weekday())
        else:
            inicio = dia.replace(day=1)
        grupo = grupos.setdefault(inicio, {'desde': dia, 'hasta': dia, **{c: CERO for c in CAMPOS_DIA}})
        grupo['hasta'] = dia
        for campo in CAMPOS_DIA:
            grupo[campo] += valores[campo]
    meses = ('ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic')
    if largo <= DIAS_POR_SEMANA:
        return 'semana', [(g['desde'], g['hasta'], f"{g['desde']:%d/%m}", g) for g in grupos.values()]
    return 'mes', [(g['desde'], g['hasta'], f"{meses[g['desde'].month - 1]} {g['desde']:%y}", g)
                   for g in grupos.values()]


def evolucion(desde, hasta):
    dias = _dias(desde, hasta)
    unidad, grupos = _agrupar(dias, desde, hasta)
    puntos = []
    totales = {c: CERO for c in CAMPOS_DIA}
    for inicio, fin, etiqueta, v in grupos:
        ingresos = v['vendido'] + v['interes']
        egresos = v['costo'] + v['gastos'] + v['mermas'] + v['incobrable']
        for campo in CAMPOS_DIA:
            totales[campo] += v[campo]
        puntos.append({
            'etiqueta': etiqueta, 'desde': inicio.isoformat(), 'hasta': fin.isoformat(),
            'ingresos': _t(ingresos), 'egresos': _t(egresos), 'neta': _t(ingresos - egresos),
            'desglose': {c: _t(v[c]) for c in CAMPOS_DIA},
        })
    ingresos = totales['vendido'] + totales['interes']
    egresos = totales['costo'] + totales['gastos'] + totales['mermas'] + totales['incobrable']
    return {'unidad': unidad, 'puntos': puntos,
            'total': {'ingresos': _t(ingresos), 'egresos': _t(egresos), 'neta': _t(ingresos - egresos),
                      'desglose': {c: _t(totales[c]) for c in CAMPOS_DIA}}}


def medios_de_pago(desde, hasta):
    filas = ganancia.por_medio_de_pago(ganancia.completadas(desde, hasta))
    total = sum((m for _n, m in filas), CERO)
    return {'total': _t(total), 'partes': [
        {'nombre': nombre, 'monto': _t(monto),
         'porcentaje': str((monto / total * 100).quantize(Decimal('0.1'))) if total else '0.0'}
        for nombre, monto in filas]}


def _ranking(filas):
    lista = []
    for f in filas:
        vendido, costo = f['vendido'] or CERO, f['costo'] or CERO
        lista.append({'nombre': f['nombre'] or 'Sin rubro', 'monto': vendido, 'unidades': f['unidades'] or CERO,
                      'ganancia': vendido - costo,
                      'margen': ((vendido - costo) / vendido * 100).quantize(Decimal('0.1')) if vendido else None})
    rankings = {}
    for criterio in ('monto', 'unidades', 'ganancia'):
        tope = sorted(lista, key=lambda x: x[criterio], reverse=True)[:TOP]
        rankings[criterio] = [{'nombre': x['nombre'], 'monto': _t(x['monto']),
                               'unidades': format(x['unidades'].normalize(), 'f') if x['unidades'] % 1 else str(int(x['unidades'])),
                               'ganancia': _t(x['ganancia']),
                               'margen': None if x['margen'] is None else str(x['margen'])} for x in tope]
    return rankings


def top(desde, hasta):
    renglones = DetalleVenta.objects.filter(id_venta__fecha_venta__range=(desde, hasta),
                                            id_venta__estado_venta='COMPLETADA')
    suma = {'vendido': Sum('subtotal'), 'costo': Sum(ganancia.COSTO_DEL_RENGLON), 'unidades': Sum('cantidad')}
    # Un producto puede haber cambiado de nombre: se agrupa por id y se
    # muestra la última descripción vendida.
    por_id = OrderedDict()
    for fila in renglones.values('id_producto', 'descripcion_item').annotate(**suma).order_by('id_producto'):
        actual = por_id.setdefault(fila['id_producto'], {'nombre': fila['descripcion_item'], 'vendido': CERO,
                                                         'costo': CERO, 'unidades': CERO})
        actual['nombre'] = fila['descripcion_item']
        for campo in ('vendido', 'costo', 'unidades'):
            actual[campo] += fila[campo] or CERO
    rubros = renglones.values(nombre=F('id_producto__id_rubro__nombre_rubro')).annotate(**suma).order_by()
    return {'productos': _ranking(por_id.values()), 'rubros': _ranking(rubros)}


def franjas(desde, hasta):
    filas = (ganancia.completadas(desde, hasta).annotate(hora=ExtractHour('hora_venta')).values('hora')
             .annotate(tickets=Count('pk'), monto=Sum('total_venta')).order_by('hora'))
    por_hora = {f['hora']: f for f in filas}
    if not por_hora:
        return {'horas': []}
    primera, ultima = min(por_hora), max(por_hora)
    return {'horas': [{'hora': h, 'etiqueta': f'{h:02d} h', 'tickets': por_hora.get(h, {}).get('tickets', 0),
                       'monto': _t(por_hora.get(h, {}).get('monto'))}
                      for h in range(primera, ultima + 1)]}


def del_periodo(desde, hasta):
    return {'desde': desde.isoformat(), 'hasta': hasta.isoformat(),
            'evolucion': evolucion(desde, hasta), 'medios': medios_de_pago(desde, hasta),
            'top': top(desde, hasta), 'franjas': franjas(desde, hasta)}


def periodos_rapidos(hoy):
    """(etiqueta, desde, hasta) para los botones de período."""
    primero = hoy.replace(day=1)
    ultimo_anterior = primero - timedelta(days=1)
    return [('Hoy', hoy, hoy), ('7 días', hoy - timedelta(days=6), hoy),
            ('Este mes', primero, hoy), ('Mes anterior', ultimo_anterior.replace(day=1), ultimo_anterior),
            ('90 días', hoy - timedelta(days=89), hoy), ('Este año', hoy.replace(month=1, day=1), hoy)]
