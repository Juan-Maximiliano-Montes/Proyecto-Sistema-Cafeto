"""Archivo histórico y retención legal (DD-99): consultar y exportar, de
cualquier año, lo vigente y lo archivado juntos. Sólo lectura."""
from datetime import date

from django.shortcuts import render
from django.utils import timezone

from negocio import archivo
from nucleo.exportar import exportar
from nucleo.paginacion import TAMANIOS_TABLA, paginar
from seguridad.decorators import requiere_permiso
from seguridad.servicios import registrar_auditoria
from nucleo.validaciones import id_valido
from nucleo.textos import pluralizar

TOPE_EXCEL = 50000
PRIMER_ANIO = 2000
# La pantalla se recorre de a páginas (25 por defecto, como la Actividad);
# lo que se puede recorrer tiene el mismo techo que el Excel. Las descargas
# no pasan por acá: siguen bajando el período completo.
TOPE_EN_PANTALLA = TOPE_EXCEL


def _periodo(request):
    """El período pedido: un año entero (``anio``) o desde/hasta."""
    hoy = timezone.localdate()
    anio = _anio(request)
    if anio:
        return date(anio, 1, 1), min(date(anio, 12, 31), hoy)
    try:
        desde = date.fromisoformat(request.GET.get('desde', ''))
    except ValueError:
        desde = hoy.replace(month=1, day=1)
    try:
        hasta = date.fromisoformat(request.GET.get('hasta', ''))
    except ValueError:
        hasta = hoy
    if desde > hasta:
        desde, hasta = hasta, desde
    hasta = min(hasta, hoy)
    desde = max(min(desde, hasta), date(PRIMER_ANIO, 1, 1))
    return desde, hasta


def _anio(request):
    anio = request.GET.get('anio', '')
    hoy = timezone.localdate()
    numero = id_valido(anio)
    return numero if numero and PRIMER_ANIO <= numero <= hoy.year else None


@requiere_permiso('ver_reportes', redirigir_a='home')
def archivo_historico(request):
    libro = archivo.libro(request.GET.get('libro', ''))
    desde, hasta = _periodo(request)
    columnas, filas, hay_mas = archivo.consultar(libro, desde, hasta, tope=TOPE_EN_PANTALLA)
    # paginar() deja en parametros la query string sin pagina: el
    # libro, el año o las fechas y el tamaño viajan en cada enlace de página.
    contexto = paginar(request, filas, TAMANIOS_TABLA, por_defecto=25)
    contexto.update({
        'libros': archivo.LIBROS, 'libro': libro, 'desde': desde, 'hasta': hasta,
        'anios': archivo.por_anio(), 'columnas': columnas, 'hay_mas': hay_mas,
        'tope': TOPE_EN_PANTALLA, 'conservacion': archivo.ANIOS_DE_CONSERVACION,
        'anio': _anio(request), 'hoy': timezone.localdate(),
    })
    return render(request, 'negocio/archivo.html', contexto)


@requiere_permiso('ver_reportes', redirigir_a='home')
def exportar_archivo(request):
    libro = archivo.libro(request.GET.get('libro', ''))
    desde, hasta = _periodo(request)
    formato = 'csv' if request.GET.get('formato') == 'csv' else 'xlsx'
    tope = None if formato == 'csv' else TOPE_EXCEL
    columnas, filas, hay_mas = archivo.consultar(libro, desde, hasta, tope=tope)
    periodo = f'Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}'
    descripcion = periodo + ' · vigentes y archivadas · sólo lectura'
    if hay_mas:
        descripcion += f' · primeras {TOPE_EXCEL:,} filas: para el período completo bajá el CSV'.replace(',', '.')
    registrar_auditoria(request, 'EXPORTACION', 'ARCHIVO HISTORICO', modulo='NEGOCIO',
                        detalle=f'Exportó "{libro.titulo}" del archivo histórico ({formato.upper()}), '
                                f"{periodo.lower()}: {pluralizar(len(filas), 'fila')}.")
    return exportar(formato, f'Archivo {libro.titulo}', columnas, filas, descripcion, tope=TOPE_EXCEL)
