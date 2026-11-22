"""Reportes: elegir el período y mirar, o bajarlo a Excel/PDF."""
import logging
from datetime import date, timedelta
from urllib.parse import urlencode

from django.shortcuts import render
from django.utils import timezone

from negocio.graficos import estilo_pedido, estilos_de, opciones_de_estilo
from negocio.graficos import grafico as armar_grafico
from negocio import tablero as tablero_
from negocio.reportes import CONSULTAS, ETIQUETAS_DE_FILTRO, REPORTES, opciones_de_filtro
from nucleo.exportar import exportar
from nucleo.validaciones import id_valido
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


class Filtros:
    """Rubro y caja elegidos (DD-103). Sólo vale un valor que el filtro
    ofrece; los que no aplican al reporte se conservan en los enlaces (al
    volver a un reporte que los usa, siguen puestos) pero no filtran."""

    def __init__(self, request, reporte):
        self.opciones = opciones_de_filtro()
        self.elegidos = {}
        for clave, opciones in self.opciones.items():
            pk = id_valido(request.GET.get(clave))
            nombre = dict(opciones).get(pk)
            if nombre is not None:
                self.elegidos[clave] = (pk, nombre)
        self.reporte = reporte

    @property
    def aplicados(self):
        return {clave: self.elegidos[clave][0] for clave in self.reporte.filtros if clave in self.elegidos}

    @property
    def consulta(self):
        """Para sumar a un enlace: ``&rubro=3&caja=1`` (o vacío)."""
        pares = {clave: pk for clave, (pk, _nombre) in self.elegidos.items()}
        return f'&{urlencode(pares)}' if pares else ''

    @property
    def disponibles(self):
        return [{'clave': clave, 'etiqueta': ETIQUETAS_DE_FILTRO[clave], 'opciones': self.opciones[clave],
                 'elegido': self.elegidos.get(clave, (None, ''))[0]}
                for clave in self.reporte.filtros]

    @property
    def descripcion(self):
        partes = [f'{ETIQUETAS_DE_FILTRO[clave]}: {self.elegidos[clave][1]}'
                  for clave in self.reporte.filtros if clave in self.elegidos]
        return ' · '.join(partes) if partes else 'Sin filtros'


def _consultar(reporte, desde, hasta, filtros):
    return CONSULTAS[reporte.clave](desde, hasta, **filtros.aplicados)


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
    filtros = Filtros(request, reporte)
    columnas, filas, totales = _consultar(reporte, desde, hasta, filtros)
    filas = list(filas)
    permitidos = estilos_de(reporte.clave)
    estilo = estilo_pedido(request, permitidos)
    # El gráfico es otra vista de las MISMAS filas (pandas + matplotlib).
    grafico = _grafico(reporte, columnas, filas, desde, hasta, estilo)
    hoy = timezone.localdate()
    periodos = [(etiqueta, f'?ver={reporte.clave}&desde={d:%Y-%m-%d}&hasta={h:%Y-%m-%d}&grafico={estilo}'
                 f'{filtros.consulta}', (d, h) == (desde, hasta))
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
        'filtros': filtros.disponibles, 'con_filtros': filtros.consulta,
        'filtros_aplicados': filtros.descripcion if filtros.aplicados else '',
    })


@requiere_permiso('ver_reportes', redirigir_a='home')
def exportar_reporte(request):
    reporte = _reporte(request)
    desde, hasta = _periodo(request)
    filtros = Filtros(request, reporte)
    columnas, filas, totales = _consultar(reporte, desde, hasta, filtros)
    filas = list(filas)
    formato = request.GET.get('formato')
    estilo = estilo_pedido(request, estilos_de(reporte.clave))
    grafico = (_grafico(reporte, columnas, filas, desde, hasta, estilo)
               if formato == 'pdf' else None)
    periodo = 'Al día de hoy' if reporte.clave == 'stock' else f'Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}'
    registrar_auditoria(request, 'EXPORTACION', 'REPORTE', modulo='NEGOCIO',
                        detalle=f'Exportó "{reporte.titulo}" {periodo} ({filtros.descripcion}).')
    return exportar(formato, reporte.titulo, columnas, filas,
                    _descripcion(periodo, filtros), grafico=grafico, totales=totales)


def _descripcion(periodo, filtros):
    """El período y los filtros; quién lo generó y cuándo lo agrega el exportador."""
    return f'{periodo} · Filtros: {filtros.descripcion}'


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
