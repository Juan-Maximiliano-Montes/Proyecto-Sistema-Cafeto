"""El panel de inicio: lo que cada perfil tiene que mirar hoy.

No hay un panel "del administrador" y otro "del operador": hay bloques, y
cada uno aparece si el perfil EFECTIVO tiene el permiso que lo respalda.
La cajera ve sus accesos directos; la encargada, además, lo que espera su
autorización; la dueña, los números del día.

Todo es lectura y son consultas agregadas: el panel tiene que cargar
rápido en la tablet aunque haya un año de ventas.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from negocio.models import ParametrosNegocio


def inicio(request):
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return redirect('seguridad:login')
    puede = usuario.puede
    hoy = timezone.localdate()
    parametros = ParametrosNegocio.vigentes()

    from ventas import puesto
    turno = puesto.turno_de(request)
    caja = puesto.caja_de(request)

    acciones = []
    if puede('vender'):
        if turno:
            acciones.append(('Vender', 'bi-cart3', reverse('ventas:mostrador'), f'en {turno.id_caja_registradora}'))
        elif puede('abrir_caja'):
            acciones.append(('Abrir la caja', 'bi-unlock', reverse('ventas:caja'),
                             str(caja) if caja else 'Elegí el cajón'))
    if puede('registrar_cambio'):
        acciones.append(('Cambio o devolución', 'bi-arrow-repeat', reverse('ventas:cambios'), 'Con el ticket'))
    if puede('registrar_compra'):
        acciones.append(('Recibir mercadería', 'bi-box-arrow-in-down', reverse('compras:recepcion'), 'Con la factura'))
    if puede('registrar_produccion'):
        acciones.append(('Cargar producción', 'bi-fire', reverse('stock:producciones'), 'Una tanda'))
    if puede('registrar_merma'):
        acciones.append(('Cargar merma', 'bi-trash3', reverse('stock:merma'), 'Roto o vencido'))
    if puede('contar_stock'):
        acciones.append(('Contar stock', 'bi-clipboard-check', reverse('stock:conteo'), 'Inventario'))

    indicadores = []
    avisos = []

    if puede('ver_ventas'):
        from ventas.models import Venta
        ventas_hoy = Venta.objects.filter(fecha_venta=hoy, estado_venta='COMPLETADA').aggregate(
            cantidad=Count('pk'), total=Sum('total_venta'))
        ayer = Venta.objects.filter(fecha_venta=hoy - timedelta(days=7),
                                    estado_venta='COMPLETADA').aggregate(
            total=Sum('total_venta'))['total']
        total = ventas_hoy['total'] or Decimal('0')
        indicadores.append({
            'titulo': 'Vendido hoy', 'valor': total, 'formato': 'pesos',
            'detalle': f"{ventas_hoy['cantidad']} ticket(s)"
                       + (f' · el mismo día de la semana pasada: $ {ayer:,.0f}'.replace(',', '.') if ayer else ''),
            'url': reverse('ventas:ventas'), 'destacado': True})

    if puede('ver_caja'):
        from ventas.models import TurnoCaja
        abiertos = TurnoCaja.objects.filter(estado_turno='ABIERTA').count()
        con_diferencia = TurnoCaja.objects.filter(
            fecha_cierre__date=hoy).exclude(diferencia_caja=0).count()
        indicadores.append({'titulo': 'Cajas abiertas', 'valor': abiertos, 'formato': 'numero',
                            'detalle': f'{con_diferencia} cierre(s) de hoy con diferencia' if con_diferencia else 'Sin diferencias hoy',
                            'url': reverse('ventas:turnos')})

    if puede('ver_stock'):
        from stock import servicios as stock
        faltantes = stock.bajo_minimo().count()
        por_vencer = stock.proximos_a_vencer(parametros.dias_aviso_vencimiento, hoy=hoy)
        vencidos = por_vencer.filter(fecha_vencimiento__lt=hoy).count()
        if faltantes:
            avisos.append(('aviso', 'bi-graph-down-arrow', f'{faltantes} producto(s) por debajo del mínimo.',
                           reverse('stock:alertas')))
        if vencidos:
            avisos.append(('error', 'bi-calendar-x', f'{vencidos} lote(s) vencido(s) en la góndola: cargalos como merma.',
                           f"{reverse('stock:alertas')}?ver=vencimientos"))
        elif por_vencer.exists():
            avisos.append(('aviso', 'bi-calendar-event', f'{por_vencer.count()} lote(s) vencen en los próximos {parametros.dias_aviso_vencimiento} días.',
                           f"{reverse('stock:alertas')}?ver=vencimientos"))

    if puede('autorizar_ajustes'):
        from stock.models import AjusteStock
        pendientes = AjusteStock.objects.filter(estado_ajuste='PENDIENTE').count()
        if pendientes:
            avisos.append(('aviso', 'bi-clipboard-check', f'{pendientes} ajuste(s) de stock esperan tu autorización.',
                           reverse('stock:ajustes')))

    if puede('ver_egresos'):
        from compras import egresos
        proximos = egresos.vencimientos_proximos(hoy + timedelta(days=7))
        cantidad = proximos['obligaciones'].count() + proximos['compras'].count()
        if cantidad:
            avisos.append(('aviso', 'bi-wallet2', f'{cantidad} pago(s) vencen en los próximos 7 días.',
                           reverse('compras:egresos')))

    if puede('ver_auditoria'):
        from seguridad.servicios import alertas_sin_revisar
        alertas = alertas_sin_revisar(usuario).count()
        if alertas:
            avisos.append(('error', 'bi-shield-exclamation',
                           f'{alertas} alerta(s) de seguridad: intentos fallidos de ingreso sin revisar.',
                           f"{reverse('seguridad:actividad')}#alertas"))

    if puede('ver_reportes'):
        indicadores.append({'titulo': 'Reportes', 'valor': None, 'formato': 'enlace',
                            'detalle': 'Ventas, márgenes, caja y stock', 'url': reverse('negocio:reportes')})

    return render(request, 'negocio/inicio.html', {
        'acciones': acciones, 'indicadores': indicadores, 'avisos': avisos,
        'turno': turno, 'caja': caja, 'hoy': hoy,
    })
