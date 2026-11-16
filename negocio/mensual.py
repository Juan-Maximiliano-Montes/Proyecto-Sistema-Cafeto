"""El resumen del mes: lo que entró, lo que salió y lo que queda por pagar.

Reemplaza la planilla que la dueña llenaba a mano. No guarda nada: es una
lectura de lo que ya está registrado (ventas, egresos, mermas, compras).

Dos decisiones que conviene conocer (DD-87):

* El RESULTADO parte de la ganancia bruta (ventas − costo de lo vendido) y
  le resta los gastos y las mermas. Los pagos de MERCADERÍA no se restan:
  ese costo ya está adentro de la ganancia bruta, y restarlo de nuevo lo
  contaría dos veces. Se muestran igual, porque son plata que salió.
* Un gasto cuenta en el mes en que se PAGÓ (fecha del pago), no en el que
  venció. Lo que todavía no se pagó va en "a pagar".
* Lo fiado (DD-91) es venta del mes pero no plata del mes: lo vendido se
  parte en "cobrado en el momento" + "fiado", y lo que entró suma lo que
  los clientes pagaron de sus cuentas corrientes (de este mes o de antes).
  El recargo por mora es ganancia del mes; lo dado por incobrable, pérdida.
"""
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Abs

from compras.models import Compra, Egreso
from negocio import ganancia
from stock.models import MovimientoStock, TipoMovimientoStock

CERO = Decimal('0')
PAGADOS = ('PENDIENTE_ACREDITACION', 'ACREDITADO')


def del_mes(anio, numero):
    desde, hasta = ganancia.mes(anio, numero)
    ventas = ganancia.completadas(desde, hasta)
    bruta = ganancia.de_ventas(ventas)
    from fidelizacion import credito
    from ventas.models import MedioPago

    medios = ganancia.por_medio_de_pago(ventas)
    fiado = sum((m for nombre, m in medios if nombre == MedioPago.NOMBRE_CUENTA_CORRIENTE), CERO)
    entro = [(nombre, m) for nombre, m in medios if nombre != MedioPago.NOMBRE_CUENTA_CORRIENTE]
    en_el_momento = sum((m for _, m in entro), CERO)
    cuenta = credito.del_periodo(desde, hasta)
    cobrado_de_cuentas = cuenta['cobrado'] + cuenta['interes']

    pagos = Egreso.objects.filter(estado_pago__in=PAGADOS, fecha_egreso__gte=desde, fecha_egreso__lte=hasta)
    mercaderia = pagos.filter(id_compra__isnull=False).aggregate(t=Sum('monto'))['t'] or CERO
    gastos = [(f['id_tipo_egreso__nombre_tipo_egreso'], f['total'])
              for f in pagos.filter(id_compra__isnull=True)
                            .values('id_tipo_egreso__nombre_tipo_egreso')
                            .annotate(total=Sum('monto')).order_by('-total')]
    total_gastos = sum((monto for _, monto in gastos), CERO)
    mermas = (MovimientoStock.objects
              .filter(tipo_movimiento=TipoMovimientoStock.MERMA,
                      fecha_movimiento__date__gte=desde, fecha_movimiento__date__lte=hasta)
              .aggregate(t=Sum(ExpressionWrapper(
                  Abs(F('cantidad')) * F('costo_unitario'),
                  output_field=DecimalField(max_digits=16, decimal_places=4))))['t'] or CERO
              ).quantize(Decimal('0.01'))

    a_pagar = [{'quien': str(e.id_proveedor or e.destinatario or e.id_tipo_egreso),
                'que': e.concepto, 'vence': e.fecha_vencimiento, 'monto': e.monto}
               for e in Egreso.objects.filter(estado_pago='PENDIENTE_PAGO')
                                      .select_related('id_proveedor', 'id_tipo_egreso')
                                      .order_by('fecha_vencimiento')]
    deuda = {}
    for compra in Compra.con_pagado(Compra.objects.exclude(estado_compra='PAGADA')
                                    .select_related('id_proveedor')):
        saldo = compra.saldo_pendiente
        if saldo > 0:
            fila = deuda.setdefault(compra.id_proveedor_id, {
                'quien': str(compra.id_proveedor), 'que': 'Facturas de mercadería sin pagar',
                'vence': compra.fecha_vencimiento_pago, 'monto': CERO, 'facturas': 0})
            fila['monto'] += saldo
            fila['facturas'] += 1
            if compra.fecha_vencimiento_pago and (fila['vence'] is None
                                                  or compra.fecha_vencimiento_pago < fila['vence']):
                fila['vence'] = compra.fecha_vencimiento_pago
    a_pagar += sorted(deuda.values(), key=lambda f: -f['monto'])

    return {
        'desde': desde, 'hasta': hasta, 'bruta': bruta,
        'entro': entro, 'en_el_momento': en_el_momento, 'fiado': fiado,
        'cobros_de_cuentas': cuenta['cobros_por_medio'], 'cobrado_de_cuentas': cobrado_de_cuentas,
        'interes': cuenta['interes'], 'incobrable': cuenta['incobrable'], 'te_deben': cuenta['saldo_total'],
        'total_entro': en_el_momento + cobrado_de_cuentas,
        'cuadra': en_el_momento + fiado == bruta['facturado'],
        'recargos': ganancia.recargos(ventas),
        'mercaderia': mercaderia, 'gastos': gastos, 'total_gastos': total_gastos, 'mermas': mermas,
        'total_salio': mercaderia + total_gastos,
        'gastos_y_mermas': total_gastos + mermas + cuenta['incobrable'],
        'a_pagar': a_pagar, 'total_a_pagar': sum((f['monto'] for f in a_pagar), CERO),
        'resultado': bruta['ganancia'] + cuenta['interes'] - total_gastos - mermas - cuenta['incobrable'],
    }
