"""Las cuatro visualizaciones del Parcial N° 2 como PNG, desde la base del
sistema: barras comparativas, dona de participación, líneas de evolución
temporal y mapa de calor por día y hora.

    python manage.py generar_graficos_parcial
    python manage.py generar_graficos_parcial --desde 2026-08-01 --hasta 2026-10-31
    python manage.py generar_graficos_parcial --dias 30 --salida /tmp/graficos
    python manage.py generar_graficos_parcial --horas 8-22

Los datos salen de las mismas consultas que la pantalla de Reportes
(``negocio.reportes``) y de las ventas completadas; pandas los arma, numpy
calcula el resumen estadístico que se imprime en la consola (total, media,
desvío y percentiles de lo vendido por día y del ticket) y
``negocio.graficos`` los dibuja con matplotlib. La evolución lleva además
la media y la recta de tendencia.
"""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from django.utils import timezone

from negocio import graficos, reportes

ARCHIVOS = {
    'barras': '01_barras_comparativas_rubros.png',
    'dona': '02_dona_medios_de_pago.png',
    'lineas': '03_lineas_evolucion_ventas.png',
    'calor': '04_heatmap_operaciones_dia_hora.png',
}


def _fecha(texto, opcion):
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise CommandError(f'{opcion}: escribí la fecha como AAAA-MM-DD (por ejemplo 2026-10-31).')


def _franja(texto):
    try:
        primera, ultima = (int(h) for h in texto.split('-'))
    except ValueError:
        raise CommandError('--horas: escribí la franja como 8-22.')
    if not 0 <= primera <= ultima <= 23:
        raise CommandError('--horas: las horas van de 0 a 23 y la primera no puede ser mayor.')
    return range(primera, ultima + 1)


def _numero(valor):
    return float(valor) if isinstance(valor, Decimal) else float(valor or 0)


def vendido_por_rubro(desde, hasta):
    """Lo vendido por rubro (renglones de ventas completadas, ya con su descuento)."""
    from ventas.models import DetalleVenta
    filas = (DetalleVenta.objects
             .filter(id_venta__fecha_venta__range=(desde, hasta), id_venta__estado_venta='COMPLETADA')
             .values('id_producto__id_rubro__nombre_rubro')
             .annotate(vendido=Sum('subtotal')))
    marco = pd.DataFrame.from_records(list(filas), columns=['id_producto__id_rubro__nombre_rubro', 'vendido'])
    marco.columns = ['Rubro', 'Vendido']
    marco['Rubro'] = marco['Rubro'].fillna('Sin rubro')
    marco['Vendido'] = marco['Vendido'].map(_numero)
    return marco


def serie_diaria(dias, desde, hasta):
    """Lo vendido por día, con cero en los días sin ventas: la media y el
    desvío de un local que no abrió un lunes tienen que contarlo."""
    if dias.empty:
        return pd.Series(0.0, index=pd.date_range(desde, hasta, freq='D'))
    serie = pd.Series(dias['Vendido'].values, index=pd.to_datetime(dias['Día']))
    return serie.groupby(level=0).sum().reindex(pd.date_range(desde, hasta, freq='D'), fill_value=0.0)


def importes_de_tickets(desde, hasta):
    from ventas.models import Venta
    return np.array([_numero(t) for t in Venta.objects.filter(
        fecha_venta__range=(desde, hasta), estado_venta='COMPLETADA').values_list('total_venta', flat=True)],
        dtype=float)


def estadisticas(valores):
    """Total, media, desvío estándar (muestral) y percentiles con numpy."""
    valores = np.asarray(valores, dtype=float)
    if valores.size == 0:
        return None
    p25, p50, p75, p90 = np.percentile(valores, [25, 50, 75, 90])
    resultado = {
        'cantidad': int(valores.size),
        'total': float(np.sum(valores)),
        'media': float(np.mean(valores)),
        'desvio': float(np.std(valores, ddof=1)) if valores.size > 1 else 0.0,
        'minimo': float(np.min(valores)),
        'maximo': float(np.max(valores)),
        'p25': float(p25), 'p50': float(p50), 'p75': float(p75), 'p90': float(p90),
    }
    resultado['variacion'] = resultado['desvio'] / resultado['media'] * 100 if resultado['media'] else 0.0
    if valores.size >= 2:
        resultado['tendencia'] = float(np.polyfit(np.arange(valores.size), valores, 1)[0])
    return resultado


def _pesos(valor):
    return '$ ' + f'{valor:,.2f}'.replace(',', '§').replace('.', ',').replace('§', '.')


def resumen_estadistico(por_dia, tickets):
    """Las líneas que se imprimen en la consola."""
    dia = estadisticas(por_dia)
    lineas = ['Resumen estadístico (numpy):']
    con_ventas = int(np.count_nonzero(np.asarray(por_dia, dtype=float)))
    variacion = f'{dia["variacion"]:.1f}'.replace('.', ',')
    lineas += [
        f'  Total vendido (np.sum): {_pesos(dia["total"])} en {dia["cantidad"]} días '
        f'({con_ventas} con ventas)',
        f'  Media por día (np.mean): {_pesos(dia["media"])}',
        f'  Desvío estándar por día (np.std): {_pesos(dia["desvio"])} '
        f'(coeficiente de variación {variacion} %)',
        f'  Percentiles por día (np.percentile) P25 / P50 / P75 / P90: '
        f'{_pesos(dia["p25"])} / {_pesos(dia["p50"])} / {_pesos(dia["p75"])} / {_pesos(dia["p90"])}',
        f'  Día más bajo / más alto: {_pesos(dia["minimo"])} / {_pesos(dia["maximo"])}',
    ]
    if 'tendencia' in dia:
        signo = '+' if dia['tendencia'] >= 0 else '−'
        lineas.append(f'  Tendencia (np.polyfit): {signo}{_pesos(abs(dia["tendencia"]))} por día')
    ticket = estadisticas(tickets)
    if ticket:
        lineas += [
            f'  Tickets: {ticket["cantidad"]} · ticket promedio {_pesos(ticket["media"])} · '
            f'mediana {_pesos(ticket["p50"])} · P90 {_pesos(ticket["p90"])} · '
            f'desvío {_pesos(ticket["desvio"])}',
        ]
    else:
        lineas.append('  Tickets: no hubo ventas en el período.')
    return lineas


def operaciones_por_dia_y_hora(desde, hasta):
    """Una fila por día de la semana y hora, con los tickets completados."""
    from ventas.models import Venta
    filas = (Venta.objects.filter(fecha_venta__range=(desde, hasta), estado_venta='COMPLETADA')
             .values_list('fecha_venta', 'hora_venta'))
    marco = pd.DataFrame.from_records(list(filas), columns=['fecha', 'hora_venta'])
    if marco.empty:
        return pd.DataFrame(columns=['dia', 'hora', 'tickets'])
    marco['dia'] = pd.to_datetime(marco['fecha']).dt.dayofweek
    marco['hora'] = marco['hora_venta'].map(lambda h: h.hour)
    return marco.groupby(['dia', 'hora'], as_index=False).size().rename(columns={'size': 'tickets'})


class Command(BaseCommand):
    help = ('Genera en reportes_png/ los 4 gráficos del Parcial N° 2 (barras comparativas, dona, '
            'líneas de evolución y heatmap por día y hora) con los datos de la base.')

    def add_arguments(self, parser):
        parser.add_argument('--desde', help='Primer día del período (AAAA-MM-DD).')
        parser.add_argument('--hasta', help='Último día del período (AAAA-MM-DD). Por defecto, hoy.')
        parser.add_argument('--dias', type=int, default=30,
                            help='Largo del período si no se indica --desde (por defecto 30 días, '
                                 'comparados con los 30 anteriores).')
        parser.add_argument('--horas',
                            help='Franja fija del mapa de calor, por ejemplo 8-22. Por defecto, de la '
                                 'primera a la última hora con ventas (como el tablero).')
        parser.add_argument('--salida', default=str(Path(settings.BASE_DIR) / 'reportes_png'),
                            help='Carpeta donde se guardan los PNG (por defecto reportes_png/ del proyecto).')

    def handle(self, *args, **opciones):
        hasta = _fecha(opciones['hasta'], '--hasta') if opciones['hasta'] else timezone.localdate()
        if opciones['desde']:
            desde = _fecha(opciones['desde'], '--desde')
        else:
            if opciones['dias'] < 1:
                raise CommandError('--dias tiene que ser 1 o más.')
            desde = hasta - timedelta(days=opciones['dias'] - 1)
        if desde > hasta:
            raise CommandError('--desde no puede ser posterior a --hasta.')
        horas = _franja(opciones['horas']) if opciones['horas'] else None
        largo = (hasta - desde).days + 1
        hasta_anterior = desde - timedelta(days=1)
        desde_anterior = hasta_anterior - timedelta(days=largo - 1)
        salida = Path(opciones['salida']).resolve()
        salida.mkdir(parents=True, exist_ok=True)
        periodo = f'del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}'
        self.stdout.write(f'Período: {periodo} ({largo} días). Comparado con el {desde_anterior:%d/%m/%Y} '
                          f'al {hasta_anterior:%d/%m/%Y}.')

        creados, faltan = [], []

        def generar(clave, dibujar, explicacion):
            ruta = salida / ARCHIVOS[clave]
            with graficos.a_png(ruta):
                resultado = dibujar()
            if resultado is None:
                faltan.append(f'{ARCHIVOS[clave]}: {explicacion}')
            else:
                creados.append(ruta)
                self.stdout.write(self.style.SUCCESS(f'PNG creado: {ruta}'))

        ahora = vendido_por_rubro(desde, hasta)
        antes = vendido_por_rubro(desde_anterior, hasta_anterior)
        rubros = (ahora.merge(antes, on='Rubro', how='outer', suffixes=(' ahora', ' antes'))
                  .fillna(0.0))
        if antes.empty and not ahora.empty:
            self.stdout.write(f'Aviso: no hubo ventas del {desde_anterior:%d/%m/%Y} al {hasta_anterior:%d/%m/%Y}; '
                              'en las barras comparativas cada rubro figura como «nuevo».')
        generar('barras', lambda: graficos.comparar_periodos(
            rubros, 'Rubro', 'Vendido ahora', 'Vendido antes',
            f'Vendido por rubro: {periodo} contra los {largo} días anteriores',
            nombres=('Este período', 'Período anterior'), colores=graficos.PAPEL[:2]),
            'no hubo ventas en ninguno de los dos períodos.')

        columnas, filas, _ = reportes.medios(desde, hasta)
        medios = graficos.tabla(columnas, filas)
        generar('dona', lambda: graficos.participacion(
            medios, 'Medio de pago', 'Total', f'Participación de cada medio de pago en lo cobrado, {periodo}',
            colores=graficos.PAPEL),
            'hacen falta cobros con al menos dos medios de pago distintos.')

        columnas, filas, _ = reportes.ventas_por_dia(desde, hasta)
        dias = graficos.tabla(columnas, filas)
        por_dia = serie_diaria(dias, desde, hasta)
        for linea in resumen_estadistico(por_dia.values, importes_de_tickets(desde, hasta)):
            self.stdout.write(linea)
        generar('lineas', lambda: None if dias.empty else graficos.evolucion(
            por_dia, desde, hasta,
            f'Evolución de lo vendido por día, {periodo}', forma='linea',
            colores=graficos.PAPEL[:3], estadisticas=True),
            'no hubo ventas en el período.')

        operaciones = operaciones_por_dia_y_hora(desde, hasta)
        if horas is not None:
            fuera = operaciones[~operaciones['hora'].isin(list(horas))]
            if not fuera.empty:
                self.stderr.write(self.style.WARNING(
                    f'{int(fuera["tickets"].sum())} ticket(s) quedan fuera de la franja {opciones["horas"]}.'))
            operaciones = operaciones[operaciones['hora'].isin(list(horas))]
        generar('calor', lambda: graficos.mapa_de_calor(
            operaciones, f'Tickets por día de la semana y hora, {periodo}', horas=horas),
            'no hubo ventas en el período.')

        if faltan:
            self.stderr.write(self.style.WARNING(
                'No se generó:\n  ' + '\n  '.join(faltan) + '\nProbá con un período más largo '
                '(--desde / --dias) o cargá datos de prueba con «python manage.py cargar_datos_demo».'))
        if not creados:
            raise CommandError('No hay datos para graficar en ese período.')
        self.stdout.write(f'Listo: {len(creados)} de {len(ARCHIVOS)} gráficos en {salida}')
