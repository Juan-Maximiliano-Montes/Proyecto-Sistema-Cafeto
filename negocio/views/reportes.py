"""Reportes: elegir el período y mirar, o bajarlo a Excel/PDF."""
import logging
from datetime import date, timedelta

from django.shortcuts import render
from django.utils import timezone

from negocio.graficos import estilo_pedido, estilos_de, opciones_de_estilo
from negocio.graficos import grafico as armar_grafico
from negocio import tablero as tablero_
from negocio.reportes import CONSULTAS, REPORTES
from nucleo.exportar import exportar
from seguridad.decorators import requiere_permiso
from seguridad.servicios import registrar_auditoria

logger = logging.getLogger(__name__)


PERIODO_MAXIMO_DIAS = 5 * 366


def _periodo(request):
    hoy = timezone.localdate()
    try:
        desde = date.fromisoformat(request.GET.get('desde', ''))
    except ValueError:
        desde = hoy.replace(day=1)
    try:
        hasta = date.fromisoformat(request.GET.get('hasta', ''))
    except ValueError:
        hasta = hoy
    if desde > hasta:
        desde, hasta = hasta, desde
    # Nada posterior a hoy ni un período de siglos (año 0001 a 9999 dejaba
    # al gráfico sin poder dibujarse): como mucho, cinco años.
    hasta = min(hasta, hoy)
    desde = min(desde, hasta)
    if (hasta - desde).days > PERIODO_MAXIMO_DIAS:
        desde = hasta - timedelta(days=PERIODO_MAXIMO_DIAS)
    return desde, hasta


def _reporte(request):
    clave = request.GET.get('ver', REPORTES[0].clave)
    return next((r for r in REPORTES if r.clave == clave), REPORTES[0])


def _grafico(reporte, columnas, filas, desde, hasta, estilo):
    """El SVG, o None. Un gráfico que no se puede dibujar no tumba el
    reporte: la tabla se sigue viendo y el error queda en el log."""
    try:
        return armar_grafico(reporte.clave, columnas, filas, desde, hasta, estilo)
    except Exception:                                   # pragma: no cover
        logger.exception('No se pudo dibujar el gráfico de %s', reporte.clave)
        return None


@requiere_permiso('ver_reportes', redirigir_a='home')
def reportes(request):
    reporte = _reporte(request)
    desde, hasta = _periodo(request)
    columnas, filas, totales = CONSULTAS[reporte.clave](desde, hasta)
    filas = list(filas)
    permitidos = estilos_de(reporte.clave)
    estilo = estilo_pedido(request, permitidos)
    # El gráfico es otra vista de las MISMAS filas (pandas + matplotlib).
    grafico = _grafico(reporte, columnas, filas, desde, hasta, estilo)
    hoy = timezone.localdate()
    periodos = [(etiqueta, f'?ver={reporte.clave}&desde={d:%Y-%m-%d}&hasta={h:%Y-%m-%d}&grafico={estilo}',
                 (d, h) == (desde, hasta))
                for etiqueta, d, h in tablero_.periodos_rapidos(hoy)]
    return render(request, 'negocio/reportes.html', {
        'tablero': tablero_.del_periodo(desde, hasta), 'periodos': periodos,
        'reportes': REPORTES, 'reporte': reporte, 'desde': desde, 'hasta': hasta,
        'columnas': columnas, 'filas': filas, 'totales': totales,
        'sin_periodo': reporte.clave == 'stock',
        'grafico': grafico, 'estilo': estilo,
        'estilos': opciones_de_estilo(request, permitidos),
        # Pidió torta y los datos no la admiten (una sola parte, o nada
        # positivo): se dibuja en barras y se dice por qué.
        'torta_no_aplica': bool(grafico) and estilo == 'torta'
                           and 'data-estilo="torta"' not in grafico,
        'ultimos_30': (hoy - timedelta(days=29), hoy),
    })


@requiere_permiso('ver_reportes', redirigir_a='home')
def exportar_reporte(request):
    reporte = _reporte(request)
    desde, hasta = _periodo(request)
    columnas, filas, totales = CONSULTAS[reporte.clave](desde, hasta)
    filas = list(filas)
    formato = request.GET.get('formato')
    estilo = estilo_pedido(request, estilos_de(reporte.clave))
    grafico = (_grafico(reporte, columnas, filas, desde, hasta, estilo)
               if formato == 'pdf' else None)
    if totales:
        filas = filas + [totales]
    periodo = '' if reporte.clave == 'stock' else f'Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}'
    registrar_auditoria(request, 'EXPORTACION', 'REPORTE', modulo='NEGOCIO',
                        detalle=f'Exportó «{reporte.titulo}» {periodo}.'.strip())
    return exportar(formato, reporte.titulo, columnas, filas, periodo, grafico=grafico)


@requiere_permiso('ver_reportes', redirigir_a='home')
def resumen_del_mes(request):
    """Lo que entró, lo que salió y lo que queda por pagar, de un mes."""
    from negocio import mensual

    hoy = timezone.localdate()
    try:
        anio, numero = (int(parte) for parte in request.GET.get('mes', '').split('-'))
        if not (2000 <= anio <= hoy.year + 1 and 1 <= numero <= 12):
            raise ValueError
    except ValueError:
        anio, numero = hoy.year, hoy.month
    contexto = mensual.del_mes(anio, numero)
    contexto['tablero'] = tablero_.del_periodo(contexto['desde'], max(contexto['desde'], min(contexto['hasta'], hoy)))
    contexto['mes'] = f'{anio:04d}-{numero:02d}'
    contexto['mes_actual'] = f'{hoy.year:04d}-{hoy.month:02d}'
    return render(request, 'negocio/resumen_del_mes.html', contexto)
