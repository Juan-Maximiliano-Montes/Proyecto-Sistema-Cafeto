"""El panel de inicio: lo que cada perfil tiene que mirar hoy.

No hay un panel "del administrador" y otro "del operador": hay bloques, y
cada uno aparece si el perfil EFECTIVO tiene el permiso que lo respalda.
La cajera ve sus accesos directos; la encargada, además, lo que espera su
autorización; la dueña, los números del día.

Los números y los avisos los arma ``negocio.panel``, que también usa el
panel de la app móvil: acá se les pone el ícono y el enlace de la web.
"""
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from negocio import panel

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

    return render(request, 'negocio/inicio.html', {
        'acciones': acciones, 'indicadores': indicadores, 'avisos': avisos,
        'turno': turno, 'caja': caja, 'hoy': hoy,
    })
