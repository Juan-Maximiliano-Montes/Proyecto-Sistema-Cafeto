"""Los números del día y los avisos del inicio, según el perfil.

Lo usan el inicio de la web (``negocio.views.inicio``) y el panel de la
app móvil (``api_movil``): las dos pantallas dicen lo mismo porque lo arma
una sola función. Cada bloque aparece si el perfil EFECTIVO tiene el
permiso que lo respalda.

Todo es lectura y son consultas agregadas: el panel tiene que cargar
rápido en la tablet aunque haya un año de ventas.

Cada indicador y cada aviso lleva una ``clave`` que dice de qué trata
(``VENDIDO_HOY``, ``VENCIDOS``...): cada pantalla decide a dónde lleva.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from negocio.models import ParametrosNegocio
from nucleo.templatetags.cafeto import pesos
from nucleo.textos import pluralizar, segun


DIAS_DE_RECONTEOS = 7


def del_dia(usuario, hoy=None):
    """``(indicadores, avisos)`` que le corresponden a ``usuario`` hoy.

    Un indicador es ``{clave, titulo, valor, formato, detalle}`` (formato
    ``pesos``, ``numero`` o ``enlace``); un aviso, ``{clave, tipo, texto}``
    (tipo ``aviso`` o ``error``).
    """
    puede = usuario.puede
    hoy = hoy or timezone.localdate()
    parametros = ParametrosNegocio.vigentes()
    indicadores, avisos = [], []

    if puede('ver_ventas'):
        from ventas.models import Venta
        ventas_hoy = Venta.objects.filter(fecha_venta=hoy, estado_venta='COMPLETADA').aggregate(
            cantidad=Count('pk'), total=Sum('total_venta'))
        ayer = Venta.objects.filter(fecha_venta=hoy - timedelta(days=7),
                                    estado_venta='COMPLETADA').aggregate(
            total=Sum('total_venta'))['total']
        indicadores.append({
            'clave': 'VENDIDO_HOY', 'titulo': 'Vendido hoy',
            'valor': ventas_hoy['total'] or Decimal('0'), 'formato': 'pesos',
            'detalle': pluralizar(ventas_hoy['cantidad'], 'ticket')
                       + (f' · el mismo día de la semana pasada: {pesos(ayer)}' if ayer else '')})

    if puede('ver_caja'):
        from ventas.models import TurnoCaja
        abiertos = TurnoCaja.objects.filter(estado_turno='ABIERTA').count()
        con_diferencia = TurnoCaja.objects.filter(
            fecha_cierre__date=hoy).exclude(diferencia_caja=0).count()
        indicadores.append({
            'clave': 'CAJAS_ABIERTAS', 'titulo': 'Cajas abiertas', 'valor': abiertos,
            'formato': 'numero',
            'detalle': f"{pluralizar(con_diferencia, 'cierre')} de hoy con diferencia" if con_diferencia else 'Sin diferencias hoy'})

    if puede('supervisar_cierre'):
        from ventas import caja
        recontados = caja.turnos_con_reconteo(hoy - timedelta(days=DIAS_DE_RECONTEOS)).count()
        if recontados:
            avisos.append({'clave': 'RECONTEOS', 'tipo': 'aviso',
                           'texto': f"{pluralizar(recontados, 'cierre')} de caja de los últimos "
                                    f"{DIAS_DE_RECONTEOS} días {segun(recontados, 'se contó', 'se contaron')} "
                                    'más de una vez: mirá los conteos.'})

    if puede('ver_stock'):
        from stock import servicios as stock
        faltantes = stock.bajo_minimo().count()
        por_vencer = stock.proximos_a_vencer(parametros.dias_aviso_vencimiento, hoy=hoy)
        vencidos = por_vencer.filter(fecha_vencimiento__lt=hoy).count()
        if faltantes:
            avisos.append({'clave': 'BAJO_MINIMO', 'tipo': 'aviso',
                           'texto': f"{pluralizar(faltantes, 'producto')} por debajo del mínimo."})
        if vencidos:
            avisos.append({'clave': 'VENCIDOS', 'tipo': 'error',
                           'texto': f"{pluralizar(vencidos, 'lote vencido', 'lotes vencidos')} en la góndola: "
                                   f"{segun(vencidos, 'cargalo', 'cargalos')} como merma."})
        elif por_vencer.exists():
            avisos.append({'clave': 'POR_VENCER', 'tipo': 'aviso',
                           'texto': f"{pluralizar(por_vencer.count(), 'lote')} {segun(por_vencer.count(), 'vence', 'vencen')} "
                                   f'en los próximos {parametros.dias_aviso_vencimiento} días.'})

    if puede('autorizar_ajustes'):
        from stock.models import AjusteStock
        pendientes = AjusteStock.objects.filter(estado_ajuste='PENDIENTE').count()
        if pendientes:
            avisos.append({'clave': 'AJUSTES', 'tipo': 'aviso',
                           'texto': f"{pluralizar(pendientes, 'ajuste')} de stock {segun(pendientes, 'espera', 'esperan')} tu autorización."})

    if puede('gestionar_ordenes'):
        from compras.models import OrdenCompra
        con_diferencias = OrdenCompra.objects.filter(estado_orden='CON_DIFERENCIAS').count()
        if con_diferencias:
            # El reporte de diferencias de recepción, a quien lo resuelve (DD-64).
            avisos.append({'clave': 'ORDENES', 'tipo': 'aviso',
                           'texto': f"{pluralizar(con_diferencias, 'orden', 'órdenes')} de compra "
                                    f"{segun(con_diferencias, 'llegó', 'llegaron')} con diferencias: "
                                    f"{segun(con_diferencias, 'revisala', 'revisalas')} con el proveedor."})

    if puede('registrar_compra'):
        from compras import facturas
        recibidas = facturas.pendientes().count()
        if recibidas:
            avisos.append({'clave': 'FACTURAS_RECIBIDAS', 'tipo': 'aviso', 'texto': facturas.aviso(recibidas)})

    if puede('editar_producto'):
        from stock import sugerencias
        propuestas = sugerencias.pendientes().count()
        if propuestas:
            # La mejora del catálogo no corre: avisa y espera. Nada se
            # cambió hasta que alguien entra y lo aprueba.
            avisos.append({'clave': 'SUGERENCIAS_CATALOGO', 'tipo': 'aviso',
                           'texto': sugerencias.aviso(propuestas)})

    if puede('ver_egresos'):
        from compras import egresos
        vencidos = egresos.vencidos(hoy)
        if vencidos['cantidad']:
            # Lo que ya venció va primero y en rojo: no es lo mismo que lo
            # que vence la semana que viene.
            avisos.insert(0, {'clave': 'PAGOS_VENCIDOS', 'tipo': 'error',
                              'texto': f"{pluralizar(vencidos['cantidad'], 'pago')} a proveedores o servicios ya "
                                       f"{segun(vencidos['cantidad'], 'venció', 'vencieron')} (por {pesos(vencidos['monto'])})."})
        proximos = egresos.vencimientos_proximos(hoy + timedelta(days=7), desde=hoy)
        cantidad = proximos['obligaciones'].count() + proximos['compras'].count()
        if cantidad:
            avisos.append({'clave': 'PAGOS', 'tipo': 'aviso',
                           'texto': f"{pluralizar(cantidad, 'pago')} {segun(cantidad, 'vence', 'vencen')} en los próximos 7 días."})
        sin_confirmar = egresos.sin_confirmar(hoy).count()
        if sin_confirmar:
            avisos.append({'clave': 'SIN_ACREDITAR', 'tipo': 'aviso',
                           'texto': f"{pluralizar(sin_confirmar, 'pago enviado', 'pagos enviados')} hace más de "
                                    f"{egresos.DIAS_SIN_CONFIRMAR} días {segun(sin_confirmar, 'sigue', 'siguen')} "
                                    f"sin confirmar ni rechazar: {segun(sin_confirmar, 'miralo', 'miralos')} con el resumen del banco."})

    if puede('cobrar_credito'):
        from fidelizacion import credito
        atrasados = [c for c in credito.deudores(hoy, solo_en_mora=True) if c.vencido > 0]
        if atrasados:
            # Rojo también, después de los pagos vencidos (que son plata que sale).
            delante = 1 if avisos and avisos[0]['clave'] == 'PAGOS_VENCIDOS' else 0
            avisos.insert(delante, {'clave': 'FIADO_VENCIDO', 'tipo': 'error',
                              'texto': f"{pluralizar(len(atrasados), 'cliente')} {segun(len(atrasados), 'tiene', 'tienen')} deuda en cuenta corriente vencida "
                                       f'(por {pesos(sum((c.vencido for c in atrasados), Decimal("0")))}).'})

    if puede('ver_clientes'):
        from fidelizacion.models import Cliente
        repetidos = sum(fila['cantidad'] for fila in Cliente.correos_repetidos())
        if repetidos:
            # La app del club identifica al cliente por su correo: con dos
            # fichas que lo comparten, no sabe cuál mostrarle.
            avisos.append({'clave': 'CORREOS_REPETIDOS', 'tipo': 'aviso',
                           'texto': f"{pluralizar(repetidos, 'cliente')} {segun(repetidos, 'comparte', 'comparten')} el correo: unificalos."})

    if puede('ver_auditoria'):
        from seguridad.servicios import alertas_sin_revisar
        alertas = alertas_sin_revisar(usuario).count()
        if alertas:
            avisos.append({'clave': 'SEGURIDAD', 'tipo': 'error',
                           'texto': f"{pluralizar(alertas, 'alerta')} de seguridad: intentos fallidos de ingreso sin revisar."})

    if puede('supervisar_cierre'):
        from ventas import caja
        a_revisar = list(caja.pendientes_de_revision())
        if a_revisar:
            # Plata que faltó o sobró y nadie aprobó (DD-97): va primera y en
            # rojo hasta que la titular o quien supervisa la revise.
            faltan = -sum((t.diferencia_caja for t in a_revisar if t.diferencia_caja < 0), Decimal('0'))
            sobran = sum((t.diferencia_caja for t in a_revisar if t.diferencia_caja > 0), Decimal('0'))
            cuanto = ' y '.join(x for x in (f'faltan {pesos(faltan)}' if faltan else '',
                                            f'sobran {pesos(sobran)}' if sobran else '') if x)
            avisos.insert(0, {'clave': 'CAJA_A_REVISAR', 'tipo': 'error',
                              'texto': f"{pluralizar(len(a_revisar), 'cierre')} de caja con una diferencia que nadie aprobó "
                                       f"({cuanto}): {segun(len(a_revisar), 'revisalo', 'revisalos')}."})

    if puede('ver_reportes'):
        indicadores.append({'clave': 'REPORTES', 'titulo': 'Reportes', 'valor': None,
                            'formato': 'enlace', 'detalle': 'Ventas, márgenes, caja y stock'})

    return indicadores, avisos
