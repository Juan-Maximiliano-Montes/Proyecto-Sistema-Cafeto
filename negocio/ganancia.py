"""Cuánto se ganó: lo vendido contra lo que costó la mercadería (DD-87).

El costo sale de ``DETALLES_VENTA.costo_unitario``, que se congela al
vender: el informe de un mes viejo no cambia porque hoy subió un costo.

Tres cuentas que se usan en la pantalla de ventas, en el resumen del mes y
en la API de automatización, para que den lo mismo en los tres lados.

Los cambios y devoluciones cuentan (REP-01). El ticket que se llevó el
cliente no se anula —dice lo que dice—, así que lo vendido se corrige con el
NETO de sus ``Cambio``: lo que el cliente puso de más suma, lo que se le
reintegró resta (``importe_cobrado - importe_devuelto``), y el costo se
ajusta con lo que dice cada renglón del cambio: lo que se llevó a cambio
cuesta (sale del stock) y lo devuelto en buen estado deja de costar (vuelve
al stock; ver ``costo_de_renglon_de_cambio``). Un cambio se atribuye al período y a la caja de la venta
ORIGINAL, igual que en el consumo por cliente: así lo vendido, lo cobrado y
lo devuelto de un mismo ticket cierran en el mismo lugar.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum

from ventas.models import Cambio, DetalleCambio, DetalleVenta, EstadoVenta, PagoVenta, Venta

CERO = Decimal('0')
CENTAVO = Decimal('0.01')
_IMPORTE = DecimalField(max_digits=16, decimal_places=4)
COSTO_DEL_RENGLON = ExpressionWrapper(F('cantidad') * F('costo_unitario'), output_field=_IMPORTE)


def completadas(desde, hasta):
    return Venta.objects.filter(estado_venta=EstadoVenta.COMPLETADA,
                                fecha_venta__gte=desde, fecha_venta__lte=hasta)


def cambios_de(ventas):
    """Los cambios y devoluciones de las ventas COMPLETADAS de ``ventas``."""
    return Cambio.objects.filter(
        id_venta__in=ventas.filter(estado_venta=EstadoVenta.COMPLETADA).order_by().values('pk'))


def neto_de_cambios(ventas):
    """Plata neta de los cambios: lo cobrado de más menos lo devuelto."""
    cuenta = cambios_de(ventas).order_by().aggregate(
        cobrado=Sum('importe_cobrado'), devuelto=Sum('importe_devuelto'))
    return (cuenta['cobrado'] or CERO) - (cuenta['devuelto'] or CERO)


def renglones_de_cambios(cambios):
    """Los renglones (``DetalleCambio``) de un conjunto de cambios, con lo
    que hace falta para costearlos sin una consulta por fila."""
    return (DetalleCambio.objects
            .filter(id_cambio__in=cambios.order_by().values('pk'))
            .select_related('id_producto', 'id_detalle_venta')
            .prefetch_related('id_producto__composicion__id_componente'))


def costo_de_renglon_de_cambio(renglon):
    """Cuánto cambia el costo por un renglón de cambio (con signo).

    * Lo que se LLEVA a cambio cuesta lo que cuesta hoy esa mercadería
      (``costo_vigente``: un compuesto se costea por sus componentes). El
      renglón del cambio no congela costo, y es lo único que hay.
    * Lo DEVUELTO en buen estado vuelve al stock: deja de costar, al costo
      con que se vendió (el congelado en el renglón de la venta, así se
      deshace exactamente lo que esa venta había sumado).
    * Lo devuelto que NO está apto no reingresa: es pérdida y su costo
      sigue contando, así que no ajusta nada.
    """
    if renglon.sentido == 'ENTREGA':
        return renglon.cantidad * renglon.id_producto.costo_vigente()
    if renglon.reingresa_stock:
        return -renglon.cantidad * renglon.id_detalle_venta.costo_unitario
    return CERO


def costo_de_cambios(ventas):
    """Cuánto cambia el costo de ``ventas`` por los renglones de sus cambios."""
    return sum((costo_de_renglon_de_cambio(r) for r in renglones_de_cambios(cambios_de(ventas))), CERO)


def de_ventas(ventas):
    """Facturado, costo, ganancia y los dos porcentajes de un conjunto de
    ventas (se cuentan sólo las COMPLETADAS).

    * margen sobre el costo: cuánto se le cargó a la mercadería;
    * rentabilidad sobre la venta: de cada $ 100 vendidos, cuántos quedan.

    Lo facturado y el costo son NETOS de cambios y devoluciones (REP-01).

    ``sin_costo`` cuenta los renglones vendidos con costo 0: inflan la
    ganancia, y conviene decirlo en vez de mostrar un número lindo.
    """
    ventas = ventas.filter(estado_venta=EstadoVenta.COMPLETADA)
    facturado = (ventas.order_by().aggregate(t=Sum('total_venta'))['t'] or CERO) + neto_de_cambios(ventas)
    renglones = DetalleVenta.objects.filter(id_venta__in=ventas.order_by().values('pk'))
    cuenta = renglones.aggregate(costo=Sum(COSTO_DEL_RENGLON),
                                 sin_costo=Count('pk', filter=Q(costo_unitario=0)))
    costo = ((cuenta['costo'] or CERO) + costo_de_cambios(ventas)).quantize(CENTAVO)
    ganancia = facturado - costo
    return {
        'facturado': facturado, 'costo': costo, 'ganancia': ganancia,
        'margen': (ganancia / costo * 100).quantize(Decimal('0.1')) if costo else None,
        'rentabilidad': (ganancia / facturado * 100).quantize(Decimal('0.1')) if facturado else None,
        'sin_costo': cuenta['sin_costo'] or 0,
    }


def por_medio_de_pago(ventas):
    """[(medio, total cobrado)] de mayor a menor.

    Lo cobrado por un medio es lo que cubrió MÁS su recargo de financiación
    (``PAGOS_VENTA.monto`` no lo incluye; ``VENTAS.total_venta`` sí). Sumando
    los dos, lo cobrado cierra al centavo con lo vendido.

    Es neto de los cambios: la diferencia que se cobró por un medio suma y
    lo que se reintegró por él resta (REP-01), así que el total sigue
    cerrando con ``de_ventas``.
    """
    completadas = ventas.filter(estado_venta=EstadoVenta.COMPLETADA).order_by().values('pk')
    filas = (PagoVenta.objects
             .filter(id_venta__in=completadas)
             .values('id_medio_pago__nombre_medio_pago')
             .annotate(total=Sum(F('monto') + F('monto_recargo'))))
    por_medio = {f['id_medio_pago__nombre_medio_pago']: f['total'] or CERO for f in filas}
    cambios = (Cambio.objects
               .filter(id_venta__in=completadas, id_medio_pago__isnull=False)
               .values('id_medio_pago__nombre_medio_pago')
               .annotate(cobrado=Sum('importe_cobrado'), devuelto=Sum('importe_devuelto')))
    for f in cambios:
        medio = f['id_medio_pago__nombre_medio_pago']
        por_medio[medio] = por_medio.get(medio, CERO) + (f['cobrado'] or CERO) - (f['devuelto'] or CERO)
    return sorted(por_medio.items(), key=lambda par: par[1], reverse=True)


def renglones(ventas):
    """Los renglones vendidos, uno por fila, con su ganancia."""
    return (DetalleVenta.objects
            .filter(id_venta__in=ventas.filter(estado_venta=EstadoVenta.COMPLETADA).order_by().values('pk'))
            .select_related('id_venta__id_turno_caja__id_caja_registradora', 'id_producto__id_rubro')
            .order_by('id_venta__fecha_venta', 'id_venta__hora_venta', 'id_venta_id', 'pk'))


def periodos(hoy):
    """Los botones rápidos: (etiqueta, desde, hasta)."""
    lunes = hoy - timedelta(days=hoy.weekday())
    return [('Hoy', hoy, hoy), ('Ayer', hoy - timedelta(days=1), hoy - timedelta(days=1)),
            ('Esta semana', lunes, hoy), ('Este mes', hoy.replace(day=1), hoy)]


def mes(anio, numero):
    """Primer y último día de un mes."""
    primero = date(anio, numero, 1)
    siguiente = date(anio + (numero == 12), numero % 12 + 1, 1)
    return primero, siguiente - timedelta(days=1)


def recargos(ventas):
    """Lo que se cobró de más por financiar con tarjeta (ya está dentro de
    lo vendido y de lo cobrado)."""
    return ventas.filter(estado_venta=EstadoVenta.COMPLETADA).order_by().aggregate(
        t=Sum('monto_recargo'))['t'] or CERO
