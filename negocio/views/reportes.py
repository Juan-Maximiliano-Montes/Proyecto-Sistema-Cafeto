"""Reportes: elegir el período y mirar, o bajarlo a Excel/PDF."""
from datetime import date

from django.shortcuts import render
from django.utils import timezone

from negocio.reportes import CONSULTAS, REPORTES
from nucleo.exportar import exportar
from seguridad.decorators import requiere_permiso
from seguridad.servicios import registrar_auditoria


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
    return desde, hasta


def _reporte(request):
    clave = request.GET.get('ver', REPORTES[0].clave)
    return next((r for r in REPORTES if r.clave == clave), REPORTES[0])


@requiere_permiso('ver_reportes', redirigir_a='home')
def reportes(request):
    reporte = _reporte(request)
    desde, hasta = _periodo(request)
    columnas, filas, totales = CONSULTAS[reporte.clave](desde, hasta)
    return render(request, 'negocio/reportes.html', {
        'reportes': REPORTES, 'reporte': reporte, 'desde': desde, 'hasta': hasta,
        'columnas': columnas, 'filas': filas, 'totales': totales,
        'sin_periodo': reporte.clave == 'stock',
    })


@requiere_permiso('ver_reportes', redirigir_a='home')
def exportar_reporte(request):
    reporte = _reporte(request)
    desde, hasta = _periodo(request)
    columnas, filas, totales = CONSULTAS[reporte.clave](desde, hasta)
    if totales:
        filas = list(filas) + [totales]
    periodo = '' if reporte.clave == 'stock' else f'Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}'
    formato = request.GET.get('formato')
    registrar_auditoria(request, 'EXPORTACION', 'REPORTE', modulo='NEGOCIO',
                        detalle=f'Exportó «{reporte.titulo}» {periodo}.'.strip())
    return exportar(formato, reporte.titulo, columnas, filas, periodo)
