"""Los gráficos de los reportes (Hito 5): pandas para los datos, matplotlib
para dibujarlos.

Por qué estas dos y en este orden:

* **pandas** arma la serie a graficar a partir de las mismas filas de la
  tabla del reporte: completa los días sin ventas con cero (``reindex`` con
  ``date_range``), calcula el promedio móvil de 7 días (``rolling``), agrupa
  y ordena (``groupby``, ``nlargest``) y calcula participaciones. Lo que en
  Python puro serían bucles y diccionarios, en pandas es una línea que se
  lee como la operación que es.
* **numpy** es la base numérica de pandas (viene con él) y se usa directo
  para las posiciones de las barras agrupadas.
* **matplotlib** dibuja del lado del servidor y devuelve un **SVG**: se ve
  nítido a cualquier tamaño, pesa poco, no necesita JavaScript ni internet
  (el local puede estar sin conexión) y el mismo gráfico sirve para la
  pantalla y para el PDF.

El gráfico es una vista más de los MISMOS datos de la tabla: no hay una
consulta aparte que pueda dar otro número.

Colores: ámbar Cafeto para la serie principal y coral para la secundaria.
El texto y los ejes salen en ``currentColor``, así el mismo SVG se lee en
el tema oscuro y en el claro.
"""
import io
import os
from decimal import Decimal

# matplotlib escribe su caché de fuentes; en el contenedor el usuario puede
# no tener un HOME escribible.
os.environ.setdefault('MPLCONFIGDIR', '/tmp/matplotlib')

import matplotlib  # noqa: E402

matplotlib.use('Agg')                       # sin pantalla: sólo archivos
# El texto queda como texto (no como trazos): pesa menos, se puede
# seleccionar y toma la tipografía y el color de la página.
matplotlib.rcParams['svg.fonttype'] = 'none'
matplotlib.rcParams['font.family'] = 'sans-serif'
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.dates import DateFormatter  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

AMBAR = '#FAB41F'
CORAL = '#F4796B'
PIZARRA = '#6E7899'
# Color centinela que después se reemplaza por currentColor.
TINTA = '#010203'

# ---------------------------------------------------------------------------
#  Reglas de diseño (DD-74). Están acá, con nombre, para que se puedan
#  discutir y cambiar en un solo lugar.
# ---------------------------------------------------------------------------
# Barras: hasta 10 categorías a la vista. Más que eso no se compara de un
# vistazo; si el gráfico muestra partes de un total, el resto se suma en
# "Otros" para que los porcentajes sigan cerrando en 100 %.
TOPE_BARRAS = 10
# Torta: hasta 6 porciones (las 5 más grandes + "Otros"). El ojo compara
# ángulos peor que largos (Cleveland y McGill, 1984): con más porciones
# ya no se distingue cuál es más grande.
TOPE_TORTA = 6
# Una porción de menos del 5 % no lleva el número adentro: no entra.
MINIMO_ROTULO_TORTA = 0.05
# Evolución en el tiempo: por día hasta 3 meses (barras hasta 31 días,
# línea de ahí en más), por semana hasta un año y por mes después.
DIAS_EN_BARRAS = 31
DIAS_POR_DIA = 92
DIAS_POR_SEMANA = 366
PALETA = (AMBAR, PIZARRA, CORAL, '#8FB996', '#C9A7E8', '#A6B0C3')

ESTILOS = {'barras': ('Barras', 'bi-bar-chart-line'),
           'torta': ('Torta', 'bi-pie-chart')}


# ---------------------------------------------------------------------------
#  Formatos
# ---------------------------------------------------------------------------
def _miles(valor):
    texto = f'{abs(valor):,.0f}'.replace(',', '.')
    return ('−' if valor < 0 else '') + texto


def _pesos_cortos(valor, _pos=None):
    """$ 1,2 M / $ 350 mil / $ 900: los ejes no necesitan centavos."""
    absoluto = abs(valor)
    signo = '−' if valor < 0 else ''
    if absoluto >= 1_000_000:
        return f'{signo}$ {absoluto / 1_000_000:.1f} M'.replace('.', ',')
    if absoluto >= 1_000:
        return f'{signo}$ {absoluto / 1_000:.0f} mil'
    return f'{signo}$ {absoluto:.0f}'


def _a_numero(valor):
    if valor is None:
        return 0.0
    if isinstance(valor, Decimal):
        return float(valor)
    return float(valor)


def tabla(columnas, filas):
    """Las filas del reporte como DataFrame, con las etiquetas de columna."""
    marco = pd.DataFrame(list(filas), columns=[c.etiqueta for c in columnas])
    for columna in columnas:
        if columna.formato in ('dinero', 'numero'):
            marco[columna.etiqueta] = marco[columna.etiqueta].map(_a_numero)
    return marco


# ---------------------------------------------------------------------------
#  Dibujo
# ---------------------------------------------------------------------------
def _lienzo(alto=3.2):
    figura, eje = plt.subplots(figsize=(9.6, alto), dpi=100)
    figura.patch.set_alpha(0)
    eje.set_facecolor('none')
    for lado in ('top', 'right'):
        eje.spines[lado].set_visible(False)
    for lado in ('left', 'bottom'):
        eje.spines[lado].set_color(TINTA)
        eje.spines[lado].set_alpha(0.35)
    eje.tick_params(colors=TINTA, labelsize=9)
    eje.grid(axis='y', color=TINTA, alpha=0.12, linewidth=0.8)
    eje.set_axisbelow(True)
    return figura, eje


def _svg(figura, descripcion, estilo='barras', ajustar=True):
    if ajustar:
        figura.tight_layout()
    salida = io.StringIO()
    figura.savefig(salida, format='svg', transparent=True)
    plt.close(figura)
    svg = salida.getvalue()
    svg = svg[svg.index('<svg'):]                    # sin la cabecera XML
    svg = svg.replace(TINTA, 'currentColor')
    # Accesible: el lector de pantalla lee la descripción.
    return svg.replace(
        '<svg ', f'<svg role="img" aria-label="{descripcion}" data-estilo="{estilo}" ', 1)


def _leyenda(eje, **opciones):
    leyenda = eje.legend(frameon=False, fontsize=9, **opciones)
    for texto in leyenda.get_texts():
        texto.set_color(TINTA)
    return leyenda


def _formato(dinero):
    return _pesos_cortos if dinero else (lambda v, _p=None: _miles(v))


# ---------------------------------------------------------------------------
#  Partes de un total y rankings
# ---------------------------------------------------------------------------
def con_otros(marco, etiqueta, valor, tope):
    """Las ``tope - 1`` más grandes y el resto sumado en "Otros (n)"."""
    ordenado = (marco.groupby(etiqueta, as_index=False, sort=False)[valor].sum()
                .sort_values(valor, ascending=False, kind='stable'))
    if len(ordenado) <= tope:
        return ordenado.reset_index(drop=True)
    cabeza = ordenado.head(tope - 1)
    resto = ordenado.iloc[tope - 1:]
    otros = pd.DataFrame({etiqueta: [f'Otros ({len(resto)})'], valor: [resto[valor].sum()]})
    return pd.concat([cabeza, otros], ignore_index=True)


def admite_torta(marco, valor):
    """Una torta sólo tiene sentido con partes de un total: nada negativo y
    al menos dos partes con valor (una sola sería un círculo al 100 %)."""
    return bool((marco[valor] >= 0).all()) and int((marco[valor] > 0).sum()) >= 2


def _barras(datos, etiqueta, valor, titulo, color, dinero, porcentaje):
    datos = datos.iloc[::-1]                      # la más grande arriba
    alto = max(2.2, 0.42 * len(datos) + 0.9)
    figura, eje = _lienzo(alto)
    eje.grid(axis='x', color=TINTA, alpha=0.12, linewidth=0.8)
    eje.grid(axis='y', visible=False)
    nombres = [str(n)[:52] for n in datos[etiqueta]]
    barras = eje.barh(nombres, datos[valor], color=color, height=0.62)
    formato = _formato(dinero)
    eje.xaxis.set_major_formatter(FuncFormatter(formato))
    total = datos[valor].sum()
    for barra, cantidad in zip(barras, datos[valor]):
        rotulo = f'  {formato(cantidad)}'
        if porcentaje and total:
            rotulo += f' · {cantidad / total:.0%}'
        eje.text(barra.get_width(), barra.get_y() + barra.get_height() / 2, rotulo,
                 va='center', fontsize=8.5, color=TINTA)
    eje.margins(x=0.22)
    _sin_barras_gordas(eje, len(datos))
    return _svg(figura, titulo)


def _sin_barras_gordas(eje, cantidad):
    """Con una o dos categorías la barra ocuparía todo el alto: se le deja
    el lugar de tres, para que se lea como una barra y no como un bloque."""
    if cantidad < 3:
        eje.set_ylim(-1.5 + (cantidad - 1) * 0.5, cantidad + 0.5 - (cantidad - 1) * 0.5)


def _torta(datos, etiqueta, valor, titulo, dinero):
    """Una dona: el total en el centro y cada porción con su porcentaje.

    El color no es lo único que distingue una porción: la leyenda repite
    el nombre, el importe y el porcentaje de cada una.
    """
    total = float(datos[valor].sum())
    figura, eje = plt.subplots(figsize=(9.6, 3.4), dpi=100)
    figura.patch.set_alpha(0)
    colores = list(PALETA[:len(datos)])
    porciones, _ = eje.pie(
        datos[valor], colors=colores, startangle=90, counterclock=False,
        wedgeprops={'width': 0.38, 'edgecolor': 'none'})
    for porcion, cantidad in zip(porciones, datos[valor]):
        parte = cantidad / total
        if parte >= MINIMO_ROTULO_TORTA:
            angulo = np.deg2rad((porcion.theta1 + porcion.theta2) / 2)
            eje.text(0.81 * np.cos(angulo), 0.81 * np.sin(angulo), f'{parte:.0%}',
                     ha='center', va='center', fontsize=9, fontweight='bold',
                     color='#161616')
    formato = _formato(dinero)
    eje.text(0, 0.08, formato(total), ha='center', va='center', fontsize=12,
             fontweight='bold', color=TINTA)
    eje.text(0, -0.14, 'en total', ha='center', va='center', fontsize=8.5, color=TINTA)
    eje.set_aspect('equal')
    # La dona a la izquierda y la leyenda (que es la que lleva los números)
    # a la derecha, con lugar para nombres largos.
    eje.set_position([0.02, 0.04, 0.34, 0.92])
    rotulos = [f'{str(n)[:34]} · {formato(c)} · {c / total:.0%}'
               for n, c in zip(datos[etiqueta], datos[valor])]
    _leyenda(eje, handles=porciones, labels=rotulos, loc='center left',
             bbox_to_anchor=(1.08, 0.5))
    return _svg(figura, titulo, estilo='torta', ajustar=False)


def comparar(marco, etiqueta, valor, titulo, estilo='barras', dinero=True,
             color=AMBAR, parte_del_todo=True):
    """Categorías comparadas: barras horizontales o, si corresponde, torta.

    ``parte_del_todo``: las barras suman un total con sentido (lo cobrado,
    lo descontado) y llevan su porcentaje, con el resto en "Otros". Si es un
    ranking (los clientes con más puntos) van las 10 primeras y nada más:
    sumar "los demás clientes" no le dice nada a nadie.
    """
    if marco.empty:
        return None
    if estilo == 'torta' and parte_del_todo and admite_torta(marco, valor):
        partes = marco[marco[valor] > 0]           # una porción de 0 no se ve
        return _torta(con_otros(partes, etiqueta, valor, TOPE_TORTA),
                      etiqueta, valor, titulo, dinero)
    if parte_del_todo:
        datos = con_otros(marco, etiqueta, valor, TOPE_BARRAS)
    else:
        datos = marco.nlargest(TOPE_BARRAS, valor)
    return _barras(datos, etiqueta, valor, titulo, color, dinero, parte_del_todo)


def divergente(marco, etiqueta, valor, titulo):
    """Valores que pueden ser negativos (diferencias de caja): barras a los
    dos lados del cero. Nunca torta: una porción negativa no existe."""
    if marco.empty:
        return None
    datos = marco.reindex(marco[valor].abs().sort_values(ascending=False).index)
    datos = datos.head(TOPE_BARRAS).iloc[::-1]
    figura, eje = _lienzo(max(2.2, 0.42 * len(datos) + 0.9))
    eje.grid(axis='y', visible=False)
    eje.grid(axis='x', color=TINTA, alpha=0.12)
    colores = [CORAL if v < 0 else AMBAR for v in datos[valor]]
    barras = eje.barh([str(n)[:40] for n in datos[etiqueta]], datos[valor], color=colores, height=0.6)
    eje.axvline(0, color=TINTA, alpha=0.5, linewidth=1)
    eje.xaxis.set_major_formatter(FuncFormatter(_pesos_cortos))
    for barra, cantidad in zip(barras, datos[valor]):
        texto = (f'{_pesos_cortos(cantidad)} faltante' if cantidad < 0
                 else f'{_pesos_cortos(cantidad)} sobrante' if cantidad > 0 else 'sin diferencia')
        eje.text(barra.get_width(), barra.get_y() + barra.get_height() / 2, f'  {texto}  ',
                 va='center', ha='left' if cantidad >= 0 else 'right', fontsize=8.5, color=TINTA)
    eje.margins(x=0.3)
    _sin_barras_gordas(eje, len(datos))
    return _svg(figura, titulo)


# ---------------------------------------------------------------------------
#  Evolución en el tiempo
# ---------------------------------------------------------------------------
def evolucion(serie, desde, hasta, titulo, dinero=True, nombre='Vendido'):
    """Una serie diaria (índice de fechas) con la granularidad que conviene.

    pandas completa los días sin movimiento con cero (``reindex``) y, si el
    período es largo, la junta por semana o por mes (``resample``).
    """
    serie = serie.groupby(level=0).sum()
    serie.index = pd.to_datetime(serie.index)
    dias = (hasta - desde).days + 1
    serie = serie.reindex(pd.date_range(desde, hasta, freq='D'), fill_value=0.0)
    figura, eje = _lienzo()
    if dias > DIAS_POR_SEMANA:
        serie = serie.resample('MS').sum()
        eje.bar(serie.index, serie.values, color=AMBAR, width=20, label=f'{nombre} por mes')
        eje.xaxis.set_major_formatter(DateFormatter('%m/%Y'))
    elif dias > DIAS_POR_DIA:
        serie = serie.resample('W-MON', label='left', closed='left').sum()
        eje.bar(serie.index, serie.values, color=AMBAR, width=5, label=f'{nombre} por semana')
        eje.xaxis.set_major_formatter(DateFormatter('%d/%m'))
    else:
        if dias <= DIAS_EN_BARRAS:
            eje.bar(serie.index, serie.values, color=AMBAR, width=0.75, label=f'{nombre} en el día')
        else:
            eje.plot(serie.index, serie.values, color=AMBAR, linewidth=1.6, label=f'{nombre} en el día')
            eje.fill_between(serie.index, serie.values, color=AMBAR, alpha=0.15)
        if dias >= 14:
            promedio = serie.rolling(7, min_periods=1).mean()
            eje.plot(promedio.index, promedio.values, color=CORAL, linewidth=2,
                     label='Promedio de 7 días')
        eje.xaxis.set_major_formatter(DateFormatter('%d/%m'))
    eje.yaxis.set_major_formatter(FuncFormatter(_formato(dinero)))
    figura.autofmt_xdate(rotation=0, ha='center')
    _leyenda(eje, loc='upper left')
    return _svg(figura, titulo)


# ---------------------------------------------------------------------------
#  Los reportes
# ---------------------------------------------------------------------------
# Qué estilos admite cada reporte. La torta sólo donde el dato es una parte
# de un total, con valores positivos. Lo que no está acá va en barras.
ESTILOS_POR_REPORTE = {
    'medios': ('barras', 'torta'), 'vendedores': ('barras', 'torta'),
    'descuentos': ('barras', 'torta'), 'mermas': ('barras', 'torta'),
    'stock': ('barras', 'torta'), 'segmentos': ('barras', 'torta'),
}


def estilos_de(clave):
    return ESTILOS_POR_REPORTE.get(clave, ('barras',))


def grafico(clave, columnas, filas, desde=None, hasta=None, estilo='barras'):
    """El SVG del reporte ``clave``, o None si no hay nada que graficar."""
    filas = list(filas)
    if not filas:
        return None
    if estilo not in estilos_de(clave):
        estilo = estilos_de(clave)[0]
    marco = tabla(columnas, filas)

    if clave == 'ventas_dia':
        serie = pd.Series(marco['Vendido'].values, index=marco['Día'])
        return evolucion(serie, desde, hasta, 'Ventas por día')
    if clave == 'productos':
        return comparar(marco, 'Producto', 'Ganancia',
                        'Los 10 productos que más ganancia dejaron', parte_del_todo=False)
    if clave == 'medios':
        return comparar(marco, 'Medio de pago', 'Total', 'Cobrado por medio de pago', estilo)
    if clave == 'vendedores':
        return comparar(marco, 'Vendió', 'Vendido', 'Vendido por persona', estilo)
    if clave == 'descuentos':
        return comparar(marco, 'Motivo', 'Descontado', 'Descuentos por motivo', estilo, color=CORAL)
    if clave == 'mermas':
        return comparar(marco, 'Motivo', 'Valor al costo', 'Mermas por motivo, al costo',
                        estilo, color=CORAL)
    if clave == 'caja':
        return divergente(marco, 'Cerró', 'Neto', 'Diferencia neta de caja por persona')
    if clave == 'stock':
        if estilo == 'torta' and admite_torta(marco, 'Al costo'):
            return comparar(marco, 'Rubro', 'Al costo', 'Stock al costo por rubro', 'torta')
        figura, eje = _lienzo()
        datos = marco.sort_values('Al costo', ascending=False, kind='stable')
        if len(datos) > TOPE_BARRAS:                 # el resto, sumado
            resto = datos.iloc[TOPE_BARRAS - 1:]
            otros = {'Rubro': f'Otros ({len(resto)})', 'Al costo': resto['Al costo'].sum(),
                     'A precio de venta': resto['A precio de venta'].sum()}
            datos = pd.concat([datos.head(TOPE_BARRAS - 1), pd.DataFrame([otros])],
                              ignore_index=True)
        posiciones = np.arange(len(datos))
        for indice, (columna, color) in enumerate((('Al costo', AMBAR), ('A precio de venta', PIZARRA))):
            eje.bar(posiciones + (indice - 0.5) * 0.4, datos[columna], width=0.4, color=color, label=columna)
        eje.set_xticks(posiciones, [str(n)[:16] for n in datos['Rubro']])
        eje.yaxis.set_major_formatter(FuncFormatter(_pesos_cortos))
        _leyenda(eje)
        return _svg(figura, 'Stock valorizado por rubro, al costo y a precio de venta')
    if clave == 'ranking':
        return comparar(marco, 'Cliente', 'Puntos acumulados',
                        'Los 10 clientes con más puntos', dinero=False, parte_del_todo=False)
    if clave == 'consumo':
        return comparar(marco, 'Cliente', 'Gastado (neto de devoluciones)',
                        'Los 10 clientes que más gastaron', parte_del_todo=False)
    if clave == 'segmentos':
        if estilo == 'torta' and admite_torta(marco, 'Clientes'):
            return comparar(marco, 'Categoría', 'Clientes', 'Clientes por categoría',
                            'torta', dinero=False)
        figura, eje = _lienzo()
        posiciones = np.arange(len(marco))
        compraron = marco['Compraron en el período']
        eje.bar(posiciones, compraron, color=AMBAR, label='Compraron en el período')
        eje.bar(posiciones, marco['Sin compras en el período'], bottom=compraron,
                color=PIZARRA, label='Sin compras en el período')
        eje.set_xticks(posiciones, marco['Categoría'].astype(str))
        eje.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: _miles(v)))
        _leyenda(eje)
        return _svg(figura, 'Clientes por categoría: con y sin compras en el período')
    return None


# ---------------------------------------------------------------------------
#  El selector de estilo
# ---------------------------------------------------------------------------
def opciones_de_estilo(request, permitidos, parametro='grafico'):
    """Los botones Barras / Torta con su URL (los demás filtros se conservan)."""
    if len(permitidos) < 2:
        return []
    actual = estilo_pedido(request, permitidos, parametro)
    opciones = []
    for estilo in permitidos:
        consulta = request.GET.copy()
        consulta[parametro] = estilo
        etiqueta, icono = ESTILOS[estilo]
        opciones.append({'estilo': estilo, 'etiqueta': etiqueta, 'icono': icono,
                         'url': f'?{consulta.urlencode()}', 'activo': estilo == actual})
    return opciones


def estilo_pedido(request, permitidos, parametro='grafico'):
    estilo = request.GET.get(parametro, permitidos[0])
    return estilo if estilo in permitidos else permitidos[0]
