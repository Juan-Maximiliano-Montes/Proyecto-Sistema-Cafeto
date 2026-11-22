"""Lo que hay que mirar hoy: faltantes, vencimientos y el sector de ofertas.

Nada de esto escribe. Las alertas salen de ``stock.servicios`` y la
cantidad de días de aviso de ``PARAMETROS_NEGOCIO`` (la dueña decide con
cuánta anticipación quiere enterarse).

Las etiquetas de oferta se imprimen en PDF: una hoja A4 con un cartelito
por lote, para el sector de "próximos a vencer" de la góndola.
"""
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from negocio.models import ParametrosNegocio
from nucleo.exportar import ColumnaExportable, exportar
from nucleo.paginacion import TAMANIOS_TABLA, paginar
from seguridad.decorators import requiere_alguno, requiere_permiso
from seguridad.servicios import registrar_auditoria
from stock import servicios
from stock.models import Lote, Producto
from nucleo.validaciones import id_valido

PESTANIAS = (
    ('faltantes', 'Bajo el mínimo', 'bi-graph-down-arrow'),
    ('vencimientos', 'Por vencer', 'bi-calendar-x'),
    ('ofertas', 'En oferta hoy', 'bi-tags'),
)


def _datos(pestania, hoy):
    if pestania == 'vencimientos':
        dias = ParametrosNegocio.vigentes().dias_aviso_vencimiento
        return servicios.proximos_a_vencer(dias, hoy=hoy), dias
    if pestania == 'ofertas':
        return servicios.en_oferta(hoy=hoy), None
    return servicios.bajo_minimo(), None


@requiere_permiso('ver_stock', redirigir_a='home')
def alertas(request):
    hoy = timezone.localdate()
    pestania = request.GET.get('ver', 'faltantes')
    if pestania not in dict((p[0], p) for p in PESTANIAS):
        pestania = 'faltantes'
    consulta, dias = _datos(pestania, hoy)
    contexto = paginar(request, consulta, TAMANIOS_TABLA, por_defecto=25)
    contexto.update({
        'pestania': pestania, 'pestanias': PESTANIAS, 'hoy': hoy, 'dias': dias,
    })
    cantidades = {
        'faltantes': servicios.bajo_minimo().count(),
        'vencimientos': servicios.proximos_a_vencer(
            ParametrosNegocio.vigentes().dias_aviso_vencimiento, hoy=hoy).count(),
        'ofertas': len(servicios.en_oferta(hoy=hoy)),
    }
    contexto['vistas'] = [{'clave': clave, 'etiqueta': etiqueta, 'cantidad': cantidades[clave],
                           'activa': clave == pestania, 'url': f'?ver={clave}'}
                          for clave, etiqueta, _icono in PESTANIAS]
    return render(request, 'stock/alertas.html', contexto)


@requiere_permiso('ver_stock', redirigir_a='home')
def ofertas(request):
    """Atajo del menú al sector de ofertas."""
    return redirect(f"{reverse('stock:alertas')}?ver=ofertas")


@requiere_permiso('ver_stock', redirigir_a='home')
def exportar_alertas(request):
    hoy = timezone.localdate()
    pestania = request.GET.get('ver', 'faltantes')
    consulta, _ = _datos(pestania, hoy)
    if pestania == 'vencimientos':
        titulo = 'Productos por vencer'
        columnas = [ColumnaExportable('Producto'), ColumnaExportable('Lote'),
                    ColumnaExportable('Vence', 'fecha'), ColumnaExportable('Días', 'numero'),
                    ColumnaExportable('Cantidad', 'numero')]
        filas = ([str(l.id_producto), l.numero_lote or '', l.fecha_vencimiento,
                  l.dias_para_vencer(hoy), l.stock_lote] for l in consulta)
    elif pestania == 'ofertas':
        titulo = 'Sector de ofertas'
        columnas = [ColumnaExportable('Producto'), ColumnaExportable('Vence', 'fecha'),
                    ColumnaExportable('Rebaja %', 'numero'), ColumnaExportable('Precio', 'dinero'),
                    ColumnaExportable('Precio con rebaja', 'dinero'), ColumnaExportable('Cantidad', 'numero')]
        filas = ([str(l.id_producto), l.fecha_vencimiento, porcentaje,
                  l.id_producto.precio_venta, precio, l.stock_lote]
                 for l, porcentaje, precio in consulta)
    else:
        titulo = 'Productos bajo el mínimo'
        columnas = [ColumnaExportable('Producto'), ColumnaExportable('Rubro'),
                    ColumnaExportable('Stock', 'numero'), ColumnaExportable('Mínimo', 'numero'),
                    ColumnaExportable('Faltan', 'numero')]
        filas = ([str(p), str(p.id_rubro), p.stock_apto, p.stock_minimo,
                  p.stock_minimo - p.stock_apto] for p in consulta)
    formato = request.GET.get('formato')
    respuesta = exportar(formato, titulo, columnas, filas)
    registrar_auditoria(request, 'EXPORTACION', titulo, modulo='STOCK',
                        detalle=f'Exportó "{titulo}".')
    return respuesta


@requiere_permiso('imprimir_ofertas', redirigir_a='home')
def etiquetas_de_oferta(request):
    """Los carteles del sector de ofertas, listos para imprimir y recortar."""
    from weasyprint import HTML

    from nucleo.exportar import _respuesta, _ruta_estatica

    hoy = timezone.localdate()
    html = render_to_string('stock/etiquetas_oferta.html', {
        'ofertas': servicios.en_oferta(hoy=hoy), 'hoy': hoy,
        'negocio': ParametrosNegocio.vigentes(),
        'fuente_caveat': _ruta_estatica('cafeto/fuentes/caveat-latin-700-normal.woff2'),
        'fuente_inter': _ruta_estatica('cafeto/fuentes/inter-latin-600-normal.woff2'),
        'fuente_lora': _ruta_estatica('cafeto/fuentes/lora-latin-600-normal.woff2'),
    })
    registrar_auditoria(request, 'IMPRESION', 'ETIQUETAS DE OFERTA', modulo='STOCK',
                        detalle='Imprimió las etiquetas del sector de ofertas.')
    respuesta = _respuesta(HTML(string=html).write_pdf(), 'application/pdf',
                           f'cafeto-ofertas-{hoy:%Y%m%d}.pdf')
    respuesta['Content-Disposition'] = respuesta['Content-Disposition'].replace(
        'attachment', 'inline')
    return respuesta


@requiere_alguno('ver_stock', 'contar_stock', 'registrar_merma',
                 'registrar_traspaso', 'registrar_produccion',
                 'registrar_compra', 'gestionar_ordenes')
def lotes_de_producto(request):
    """Los lotes con stock de un producto, para elegir en un renglón."""
    pk = id_valido(request.GET.get('producto'))
    if pk is None:
        return JsonResponse({'ok': False, 'error': 'El producto pedido no es un número de producto válido.',
                             'lotes': [], 'controla': False}, status=400)
    producto = Producto.objects.filter(pk=pk).first()
    if producto is None:
        return JsonResponse({'ok': False, 'error': 'Ese producto no existe.', 'lotes': [], 'controla': False},
                            status=404)
    # Lo que necesita un renglón de compra o de ajuste para dibujarse.
    hoy = timezone.localdate()
    lotes = Lote.objects.filter(id_producto=producto, stock_lote__gt=0).order_by(
        'fecha_vencimiento')
    return JsonResponse({
        'controla': producto.controla_vencimiento,
        'nombre': str(producto),
        'costo': str(producto.precio_costo),
        'fraccionable': not producto.exige_cantidad_entera,
        'unidad': 'u.' if producto.exige_cantidad_entera else producto.unidad_medida,
        'stock': str(producto.stock_actual),
        'lotes': [{'id': l.pk, 'texto': f'Vence {l.fecha_vencimiento:%d/%m/%Y}'
                   + (f' · lote {l.numero_lote}' if l.numero_lote else '')
                   + f' · quedan {l.stock_lote.normalize():f}'
                   + (' · VENCIDO' if l.fecha_vencimiento < hoy else '')}
                  for l in lotes],
    })


MAXIMO_DE_ETIQUETAS = 300


@requiere_permiso('ver_productos', redirigir_a='home')
def etiquetas_de_gondola(request):
    """Los precios para pegar en la góndola: una hoja A4 para recortar.

    Se eligen por rubro, por lo que se tocó hoy o en la semana (el precio u
    otro dato: es la fecha de última modificación del producto) o buscando.
    """
    from datetime import timedelta

    from django.db.models import Q

    from stock.models import TIPOS_VENDIBLES, Producto, Rubro

    filtro = request.GET
    productos = (Producto.objects.filter(borrado_logico=False, tipo_producto__in=TIPOS_VENDIBLES)
                 .select_related('id_marca', 'id_rubro').order_by('id_rubro__nombre_rubro', 'nombre_producto'))
    elegido = any(filtro.get(clave) for clave in ('rubro', 'cambios', 'q'))
    if id_valido(filtro.get('rubro')):
        productos = productos.filter(id_rubro_id=id_valido(filtro['rubro']))
    if filtro.get('cambios') in ('1', '7'):
        desde = timezone.localdate() - timedelta(days=int(filtro['cambios']) - 1)
        productos = productos.filter(fecha_modificacion__date__gte=desde)
    busqueda = filtro.get('q', '').strip()
    if busqueda:
        productos = productos.filter(Q(nombre_producto__icontains=busqueda)
                                     | Q(codigo_barras__startswith=busqueda)
                                     | Q(id_marca__nombre_marca__icontains=busqueda))
    total = productos.count() if elegido else 0
    return render(request, 'stock/etiquetas_gondola.html', {
        'filtro': filtro, 'elegido': elegido, 'total': total, 'maximo': MAXIMO_DE_ETIQUETAS,
        'productos': productos[:MAXIMO_DE_ETIQUETAS] if elegido else [],
        'rubros': Rubro.objects.filter(borrado_logico=False).order_by('nombre_rubro'),
        'hoy': timezone.localdate(),
    })
