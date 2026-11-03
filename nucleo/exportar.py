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
def a_excel(titulo, columnas, filas, descripcion=''):
    """``filas`` es un iterable de listas, en el orden de ``columnas``."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    libro = Workbook()
    hoja = libro.active
    hoja.title = (titulo or 'Listado')[:31]

    ambar = PatternFill('solid', fgColor='FAB41F')
    linea = Side(style='thin', color='E6E2D8')

    hoja['A1'] = texto_seguro(_encabezado_del_comercio())
    hoja['A1'].font = Font(bold=True, size=14)
    hoja['A2'] = texto_seguro(titulo)
    hoja['A2'].font = Font(bold=True, size=12)
    hoja['A3'] = texto_seguro(
        f'Generado el {timezone.localtime():%d/%m/%Y %H:%M}'
        + (f' · {descripcion}' if descripcion else ''))
    hoja['A3'].font = Font(italic=True, color='5B6078')

    fila_titulos = 5
    for indice, columna in enumerate(columnas, start=1):
        celda = hoja.cell(row=fila_titulos, column=indice,
                          value=texto_seguro(columna.etiqueta))
        celda.font = Font(bold=True, color='161616')
        celda.fill = ambar
        celda.alignment = Alignment(vertical='center')
        celda.border = Border(bottom=linea)

    anchos = [len(c.etiqueta) for c in columnas]
    cantidad = 0
    for cantidad, fila in enumerate(filas, start=1):
        if cantidad > TOPE_DE_FILAS:
            break
        for indice, (columna, valor) in enumerate(zip(columnas, fila), start=1):
            dato = normalizar(valor, columna.formato)
            celda = hoja.cell(row=fila_titulos + cantidad, column=indice, value=dato)
            if columna.formato == 'dinero':
                celda.number_format = '"$" #,##0.00'
            elif columna.formato == 'numero':
                celda.number_format = '#,##0.###'
            elif columna.formato == 'fecha':
                celda.number_format = 'DD/MM/YYYY'
            elif columna.formato == 'fecha_hora':
                celda.number_format = 'DD/MM/YYYY HH:MM'
            largo = len(str(dato)) if dato is not None else 0
            anchos[indice - 1] = max(anchos[indice - 1], min(largo, 60))

    for indice, ancho in enumerate(anchos, start=1):
        hoja.column_dimensions[get_column_letter(indice)].width = ancho + 3
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


def a_pdf(titulo, columnas, filas, descripcion='', plantilla='parciales/exportar_pdf.html'):
    from weasyprint import HTML

    renglones = []
    for cantidad, fila in enumerate(filas, start=1):
        if cantidad > TOPE_DE_FILAS:
            break
        renglones.append([
            (columna.formato, normalizar(valor, columna.formato, False))
            for columna, valor in zip(columnas, fila)])

    html = render_to_string(plantilla, {
        'comercio': _encabezado_del_comercio(),
        'titulo': titulo,
        'descripcion': descripcion,
        'generado': timezone.localtime(),
        'columnas': columnas,
        'renglones': renglones,
        'apaisado': len(columnas) > 6,
        'fuente_inter': _ruta_estatica('cafeto/fuentes/inter-latin-400-normal.woff2'),
        'fuente_inter_negrita': _ruta_estatica('cafeto/fuentes/inter-latin-600-normal.woff2'),
        'fuente_lora': _ruta_estatica('cafeto/fuentes/lora-latin-600-normal.woff2'),
        'logo': _ruta_estatica('cafeto/marca/cafeto-horizontal-positivo.svg'),
    })
    return _respuesta(HTML(string=html).write_pdf(), 'application/pdf',
                      _nombre_de_archivo(titulo, 'pdf'))


def exportar(formato, titulo, columnas, filas, descripcion=''):
    if formato == 'pdf':
        return a_pdf(titulo, columnas, filas, descripcion)
    return a_excel(titulo, columnas, filas, descripcion)
