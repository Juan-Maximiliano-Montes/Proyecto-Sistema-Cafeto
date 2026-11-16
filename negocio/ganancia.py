"""Cuánto se ganó: lo vendido contra lo que costó la mercadería (DD-87).

El costo sale de ``DETALLES_VENTA.costo_unitario``, que se congela al
vender: el informe de un mes viejo no cambia porque hoy subió un costo.

Tres cuentas que se usan en la pantalla de ventas, en el resumen del mes y
en la API de automatización, para que den lo mismo en los tres lados.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum

from ventas.models import DetalleVenta, EstadoVenta, PagoVenta, Venta

CERO = Decimal('0')
CENTAVO = Decimal('0.01')
_IMPORTE = DecimalField(max_digits=16, decimal_places=4)
COSTO_DEL_RENGLON = ExpressionWrapper(F('cantidad') * F('costo_unitario'), output_field=_IMPORTE)


def completadas(desde, hasta):
    return Venta.objects.filter(estado_venta=EstadoVenta.COMPLETADA,
                                fecha_venta__gte=desde, fecha_venta__lte=hasta)


def de_ventas(ventas):
    """Facturado, costo, ganancia y los dos porcentajes de un conjunto de
    ventas (se cuentan sólo las COMPLETADAS).

    * margen sobre el costo: cuánto se le cargó a la mercadería;
    * rentabilidad sobre la venta: de cada $ 100 vendidos, cuántos quedan.

    ``sin_costo`` cuenta los renglones vendidos con costo 0: inflan la
    ganancia, y conviene decirlo en vez de mostrar un número lindo.
    """
    ventas = ventas.filter(estado_venta=EstadoVenta.COMPLETADA)
    facturado = ventas.order_by().aggregate(t=Sum('total_venta'))['t'] or CERO
    renglones = DetalleVenta.objects.filter(id_venta__in=ventas.order_by().values('pk'))
    cuenta = renglones.aggregate(costo=Sum(COSTO_DEL_RENGLON),
                                 sin_costo=Count('pk', filter=Q(costo_unitario=0)))
    costo = (cuenta['costo'] or CERO).quantize(CENTAVO)
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
    """
    filas = (PagoVenta.objects
             .filter(id_venta__in=ventas.filter(estado_venta=EstadoVenta.COMPLETADA).order_by().values('pk'))
             .values('id_medio_pago__nombre_medio_pago')
             .annotate(total=Sum(F('monto') + F('monto_recargo'))).order_by('-total'))
    return [(f['id_medio_pago__nombre_medio_pago'], f['total'] or CERO) for f in filas]


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
