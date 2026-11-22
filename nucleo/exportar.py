"""Exportación de listados a Excel y PDF.

Lo que se exporta es lo que se está mirando: los mismos filtros y la misma
búsqueda de la pantalla, pero TODAS las páginas (quien exporta quiere la
lista entera, no los doce de la primera página). Con un tope, para que un
clic no deje al servidor del local armando un PDF de cien mil renglones.

Dos cuidados que no se ven:

* **Inyección de fórmulas.** Un texto que empieza con ``=``, ``+``, ``-``,
  ``@`` o un tabulador, Excel lo toma como fórmula. Si alguien carga un
  proveedor llamado ``=HYPERLINK("http://...")``, la planilla que abre la
  dueña ejecutaría eso. Esos textos salen con un apóstrofo adelante (la
  recomendación de OWASP) y se ven igual.
* **Los números van como números**, no como texto: la planilla tiene que
  poder sumar la columna de precios sin que nadie la convierta a mano.
"""
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO

from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify

TOPE_DE_FILAS = 10000
PELIGROSOS = ('=', '+', '-', '@', '\t', '\r')


@dataclass(frozen=True)
class ColumnaExportable:
    etiqueta: str
    # 'texto' | 'numero' | 'dinero' | 'si_no' | 'fecha' | 'fecha_hora'
    formato: str = 'texto'
    conteo: bool = False

    @property
    def alineacion(self):
        if self.conteo:
            return 'centro'
        if self.formato in ('numero', 'dinero'):
            return 'derecha'
        return 'izquierda'


def texto_seguro(valor):
    texto = '' if valor is None else str(valor)
    if texto.startswith(PELIGROSOS):
        return "'" + texto
    return texto


def normalizar(valor, formato, para_planilla=True):
    """El valor listo para una celda: número, booleano legible o texto.

    El apóstrofo contra la inyección de fórmulas sólo hace falta en la
    planilla; en el PDF se vería tal cual.
    """
    if valor is None or valor == '':
        return None
    if formato == 'si_no':
        return 'Sí' if valor else 'No'
    if formato in ('numero', 'dinero'):
        try:
            return Decimal(str(valor))
        except Exception:
            return texto_seguro(valor) if para_planilla else str(valor)
    if formato == 'fecha_hora' and hasattr(valor, 'astimezone'):
        return timezone.localtime(valor).replace(tzinfo=None)
    if formato in ('fecha', 'fecha_hora'):
        return valor
    return texto_seguro(valor) if para_planilla else str(valor)


def metadatos():
    from nucleo import contexto
    usuario = contexto.usuario_actual()
    texto = f'Generado el {timezone.localtime():%d/%m/%Y %H:%M}'
    if usuario is not None:
        texto += f' por {usuario.apellido}, {usuario.nombre} ({usuario.nombre_usuario})'
    return texto


def _de_la_fila_de_totales(valor, formato):
    if isinstance(valor, str) and formato in ('numero', 'dinero', 'fecha', 'fecha_hora', 'si_no'):
        return 'texto'
    return formato


def _ancho_a_la_vista(dato, formato):
    if dato is None:
        return 0
    if isinstance(dato, Decimal) and formato == 'dinero':
        return len(f'{abs(dato):,.2f}') + 3
    if isinstance(dato, Decimal):
        return len(f'{abs(dato):,.3f}'.rstrip('0').rstrip('.')) + 1
    if formato == 'fecha':
        return 10
    if formato == 'fecha_hora':
        return 16
    return len(str(dato))


def _encabezado_del_comercio():
    from negocio.models import ParametrosNegocio
    try:
        parametros = ParametrosNegocio.vigentes()
    except Exception:
        return 'Cafeto Las Lomitas'
    return parametros.nombre_fantasia or parametros.razon_social


def _nombre_de_archivo(titulo, extension):
    sello = timezone.localtime().strftime('%Y%m%d-%H%M')
    return f'cafeto-{slugify(titulo) or "listado"}-{sello}.{extension}'


def _respuesta(contenido, tipo, nombre):
    respuesta = HttpResponse(contenido, content_type=tipo)
    respuesta['Content-Disposition'] = f'attachment; filename="{nombre}"'
    respuesta['Cache-Control'] = 'no-store'
    return respuesta


# ---------------------------------------------------------------------------
#  Excel
# ---------------------------------------------------------------------------
def a_excel(titulo, columnas, filas, descripcion='', tope=TOPE_DE_FILAS, totales=None):
    """``filas`` es un iterable de listas, en el orden de ``columnas``.

    ``totales`` va aparte: no cuenta como registro, no entra en el filtro de
    la planilla y sale en negrita, con una línea arriba.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    from nucleo.textos import pluralizar

    libro = Workbook()
    hoja = libro.active
    hoja.title = (titulo or 'Listado')[:31]

    ambar = PatternFill('solid', fgColor='FAB41F')
    tenue = PatternFill('solid', fgColor='FDF3D8')
    linea = Side(style='thin', color='E6E2D8')
    linea_total = Side(style='medium', color='161616')
    horizontal = {'izquierda': 'left', 'centro': 'center', 'derecha': 'right'}
    formatos = {'dinero': '"$" #,##0.00', 'numero': '#,##0.###',
                'fecha': 'DD/MM/YYYY', 'fecha_hora': 'DD/MM/YYYY HH:MM'}

    hoja['A1'] = texto_seguro(_encabezado_del_comercio())
    hoja['A1'].font = Font(bold=True, size=14)
    hoja['A2'] = texto_seguro(titulo)
    hoja['A2'].font = Font(bold=True, size=12)

    fila_titulos = 5
    for indice, columna in enumerate(columnas, start=1):
        celda = hoja.cell(row=fila_titulos, column=indice,
                          value=texto_seguro(columna.etiqueta))
        celda.font = Font(bold=True, color='161616')
        celda.fill = ambar
        celda.alignment = Alignment(horizontal=horizontal[columna.alineacion], vertical='center')
        celda.border = Border(bottom=linea)

    anchos = [len(c.etiqueta) for c in columnas]

    def escribir(numero_de_fila, fila, es_total=False):
        for indice, (columna, valor) in enumerate(zip(columnas, fila), start=1):
            formato = _de_la_fila_de_totales(valor, columna.formato) if es_total else columna.formato
            dato = normalizar(valor, formato)
            celda = hoja.cell(row=numero_de_fila, column=indice, value=dato)
            celda.number_format = ('#,##0' if columna.conteo and formato == 'numero'
                                   else formatos.get(formato, '@' if es_total else 'General'))
            alineacion = 'izquierda' if formato == 'texto' and es_total else columna.alineacion
            celda.alignment = Alignment(horizontal=horizontal[alineacion], vertical='center')
            if es_total:
                celda.font = Font(bold=True)
                celda.fill = tenue
                celda.border = Border(top=linea_total)
            anchos[indice - 1] = max(anchos[indice - 1], min(_ancho_a_la_vista(dato, formato), 60))

    cantidad = 0
    for fila in filas:
        if cantidad >= tope:
            break
        cantidad += 1
        escribir(fila_titulos + cantidad, fila)
    if totales and cantidad:
        escribir(fila_titulos + cantidad + 1, totales, es_total=True)

    hoja['A3'] = texto_seguro(' · '.join(
        parte for parte in (metadatos(), descripcion, pluralizar(cantidad, 'registro')) if parte))
    hoja['A3'].font = Font(italic=True, color='5B6078')

    for indice, (columna, ancho) in enumerate(zip(columnas, anchos), start=1):
        minimo = 16 if columna.formato == 'dinero' else 8
        hoja.column_dimensions[get_column_letter(indice)].width = max(ancho + 4, minimo)
    if columnas:
        ultima = get_column_letter(len(columnas))
        hoja.auto_filter.ref = f'A{fila_titulos}:{ultima}{fila_titulos + max(cantidad, 1)}'
    hoja.freeze_panes = hoja.cell(row=fila_titulos + 1, column=1)

    salida = BytesIO()
    libro.save(salida)
    return _respuesta(
        salida.getvalue(),
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        _nombre_de_archivo(titulo, 'xlsx'))


# ---------------------------------------------------------------------------
#  PDF
# ---------------------------------------------------------------------------
def _ruta_estatica(relativa):
    ruta = finders.find(relativa)
    return f'file://{ruta}' if ruta else ''


def _renglon_pdf(columnas, fila, es_total=False):
    renglon = []
    for columna, valor in zip(columnas, fila):
        formato = _de_la_fila_de_totales(valor, columna.formato) if es_total else columna.formato
        alineacion = 'izquierda' if es_total and formato == 'texto' else columna.alineacion
        renglon.append((formato, alineacion, normalizar(valor, formato, False)))
    return renglon


def es_apaisado(columnas):
    return len(columnas) > 6


def a_pdf(titulo, columnas, filas, descripcion='', plantilla='parciales/exportar_pdf.html',
          grafico=None, totales=None):
    from weasyprint import HTML

    renglones = []
    for fila in filas:
        if len(renglones) >= TOPE_DE_FILAS:
            break
        renglones.append(_renglon_pdf(columnas, fila))

    html = render_to_string(plantilla, {
        'comercio': _encabezado_del_comercio(),
        'titulo': titulo,
        'descripcion': descripcion,
        'grafico': grafico,               # SVG ya armado (reportes, Hito 5)
        'metadatos': metadatos(),
        'columnas': columnas,
        'renglones': renglones,
        'total': _renglon_pdf(columnas, totales, es_total=True) if totales and renglones else None,
        'apaisado': es_apaisado(columnas),
        'fuente_inter': _ruta_estatica('cafeto/fuentes/inter-latin-400-normal.woff2'),
        'fuente_inter_negrita': _ruta_estatica('cafeto/fuentes/inter-latin-600-normal.woff2'),
        'fuente_lora': _ruta_estatica('cafeto/fuentes/lora-latin-600-normal.woff2'),
        'logo': _ruta_estatica('cafeto/marca/cafeto-horizontal-positivo.svg'),
    })
    return _respuesta(HTML(string=html).write_pdf(), 'application/pdf',
                      _nombre_de_archivo(titulo, 'pdf'))


# ---------------------------------------------------------------------------
#  CSV (sin tope: se arma a medida que se manda)
# ---------------------------------------------------------------------------
def _celda_csv(valor, formato):
    dato = normalizar(valor, formato)
    if dato is None:
        return ''
    if isinstance(dato, Decimal):
        # Como lo lee el Excel en castellano: coma decimal, sin miles.
        return format(dato, 'f').replace('.', ',')
    if formato == 'fecha_hora' and hasattr(dato, 'strftime'):
        return dato.strftime('%d/%m/%Y %H:%M')
    if formato == 'fecha' and hasattr(dato, 'strftime'):
        return dato.strftime('%d/%m/%Y')
    return str(dato)


def a_csv(titulo, columnas, filas, totales=None):
    """CSV separado por punto y coma, con BOM para que Excel respete los
    acentos. Las filas se escriben de a una: sirve para años enteros."""
    import csv
    from io import StringIO

    from django.http import StreamingHttpResponse

    def renglones():
        buffer = StringIO()
        escritor = csv.writer(buffer, delimiter=';', lineterminator='\r\n')
        escritor.writerow([texto_seguro(c.etiqueta) for c in columnas])
        yield '\ufeff' + buffer.getvalue()
        for fila in filas:
            buffer.seek(0)
            buffer.truncate()
            escritor.writerow([_celda_csv(v, c.formato) for c, v in zip(columnas, fila)])
            yield buffer.getvalue()
        if totales:
            buffer.seek(0)
            buffer.truncate()
            escritor.writerow([_celda_csv(v, _de_la_fila_de_totales(v, c.formato))
                               for c, v in zip(columnas, totales)])
            yield buffer.getvalue()

    respuesta = StreamingHttpResponse(renglones(), content_type='text/csv; charset=utf-8')
    respuesta['Content-Disposition'] = f'attachment; filename="{_nombre_de_archivo(titulo, "csv")}"'
    respuesta['Cache-Control'] = 'no-store'
    return respuesta


def exportar(formato, titulo, columnas, filas, descripcion='', grafico=None, tope=TOPE_DE_FILAS,
             totales=None):
    if formato == 'csv':
        return a_csv(titulo, columnas, filas, totales)
    if formato == 'pdf':
        return a_pdf(titulo, columnas, filas, descripcion, grafico=grafico, totales=totales)
    return a_excel(titulo, columnas, filas, descripcion, tope=tope, totales=totales)
