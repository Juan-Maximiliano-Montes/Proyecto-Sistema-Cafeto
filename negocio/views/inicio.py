"""El panel de inicio: lo que cada perfil tiene que mirar hoy.

No hay un panel "del administrador" y otro "del operador": hay bloques, y
cada uno aparece si el perfil EFECTIVO tiene el permiso que lo respalda.
La cajera ve sus accesos directos; la encargada, además, lo que espera su
autorización; la dueña, los números del día.

Los números y los avisos los arma ``negocio.panel``, que también usa el
panel de la app móvil: acá se les pone el ícono y el enlace de la web.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from negocio import panel


def _pulso_ventas_7_dias(hoy):
    """Serie diaria de los últimos 7 días (hoy incluido) para el mini-gráfico
    de la tarjeta Reportes en Inicio. Una sola consulta agregada, sin tocar
    ``negocio.panel`` (que comparte contrato con la app móvil)."""
    from ventas.models import Venta

    desde = hoy - timedelta(days=6)
    por_dia = {
        fila['fecha_venta']: fila['total'] or Decimal('0')
        for fila in (
            Venta.objects
            .filter(fecha_venta__range=(desde, hoy), estado_venta='COMPLETADA')
            .values('fecha_venta')
            .annotate(total=Sum('total_venta'))
        )
    }
    dias_cortos = ('Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom')
    serie = []
    maximo = Decimal('0')
    total_semana = Decimal('0')
    for offset in range(7):
        fecha = desde + timedelta(days=offset)
        monto = por_dia.get(fecha, Decimal('0'))
        if monto > maximo:
            maximo = monto
        total_semana += monto
        serie.append({
            'fecha': fecha,
            'etiqueta': 'Hoy' if offset == 6 else dias_cortos[fecha.weekday()],
            'monto': monto,
            'es_hoy': offset == 6,
        })

    barras = []
    for idx, item in enumerate(serie):
        if maximo > 0 and item['monto'] > 0:
            alto = max(5, round(float(item['monto'] / maximo) * 40))
        else:
            alto = 3
        x = 4 + idx * 30
        barras.append({
            **item,
            'x': x,
            'y': 46 - alto,
            'alto': alto,
            'cx': x + 11,
            'con_ventas': item['monto'] > 0,
        })

    return {
        'barras': barras,
        'total': total_semana,
        'hay_datos': maximo > 0,
    }

# A dónde lleva cada indicador del panel en la web, y si va destacado.
DESTINO_DEL_INDICADOR = {
    'VENDIDO_HOY': ('ventas:ventas', True),
    'CAJAS_ABIERTAS': ('ventas:turnos', False),
    'REPORTES': ('negocio:reportes', False),
}

# El ícono y el enlace de cada aviso.
AVISOS = {
    'BAJO_MINIMO': ('bi-graph-down-arrow', 'stock:alertas', ''),
    'VENCIDOS': ('bi-calendar-x', 'stock:alertas', '?ver=vencimientos'),
    'POR_VENCER': ('bi-calendar-event', 'stock:alertas', '?ver=vencimientos'),
    'AJUSTES': ('bi-clipboard-check', 'stock:ajustes', ''),
    'ORDENES': ('bi-box-seam', 'compras:ordenes', '?estado=CON_DIFERENCIAS'),
    'PAGOS': ('bi-wallet2', 'compras:egresos', '?ver=a_pagar'),
    'PAGOS_VENCIDOS': ('bi-exclamation-octagon', 'compras:egresos', '?ver=a_pagar'),
    'SIN_ACREDITAR': ('bi-bank', 'compras:egresos', '?ver=por_acreditar'),
    'FIADO_VENCIDO': ('bi-journal-x', 'fidelizacion:cuentas', '?estado=vencidos'),
    'RECONTEOS': ('bi-cash-coin', 'ventas:turnos', '?estado=recontados'),
    'CAJA_A_REVISAR': ('bi-cash-stack', 'ventas:turnos', '?estado=a_revisar'),
    'FACTURAS_RECIBIDAS': ('bi-inbox', 'compras:compras', ''),
    'SUGERENCIAS_CATALOGO': ('bi-magic', 'stock:sugerencias', ''),
    'CORREOS_REPETIDOS': ('bi-people', 'fidelizacion:clientes_lista', ''),
    'SEGURIDAD': ('bi-shield-exclamation', 'seguridad:actividad', '#alertas'),
}


def inicio(request):
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return redirect('seguridad:login')
    puede = usuario.puede
    hoy = timezone.localdate()

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

    del_dia, avisos_del_dia = panel.del_dia(usuario, hoy)
    indicadores = []
    for indicador in del_dia:
        nombre_url, destacado = DESTINO_DEL_INDICADOR[indicador['clave']]
        indicadores.append(dict(indicador, url=reverse(nombre_url),
                                destacado=destacado))
    avisos = []
    for aviso in avisos_del_dia:
        icono, nombre_url, sufijo = AVISOS[aviso['clave']]
        avisos.append((aviso['tipo'], icono, aviso['texto'],
                       reverse(nombre_url) + sufijo))

    puesta_en_marcha = None
    if puede('editar_parametros'):
        from negocio.models import ParametrosNegocio
        estado_local = ParametrosNegocio.vigentes().estado_configuracion()
        if estado_local['pendiente']:
            puesta_en_marcha = estado_local

    pulso_ventas = _pulso_ventas_7_dias(hoy) if puede('ver_reportes') else None

    return render(request, 'negocio/inicio.html', {
        'acciones': acciones, 'indicadores': indicadores, 'avisos': avisos,
        'puesta_en_marcha': puesta_en_marcha, 'pulso_ventas': pulso_ventas,
        'turno': turno, 'caja': caja, 'hoy': hoy,
    })
