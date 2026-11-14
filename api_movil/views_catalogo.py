"""Endpoints del catálogo (públicos) y de la consulta de stock (del equipo).

    GET /catalogo/destacados/      combos y ofertas del día (máximo 8)
    GET /catalogo/productos/       ?seccion=CAFETERIA|MINISUPER&rubro=&q=&pagina=
    GET /catalogo/rubros/          ?seccion=  (los filtros de la pantalla)
    GET /catalogo/beneficios/      los del club vigentes hoy
    GET /stock/productos/          ?q=&codigo=&pagina=   permiso ver_productos
    GET /stock/alertas/            ?tipo=TODAS|STOCK|VENCIMIENTO   permiso ver_stock

Lo público nunca muestra un insumo (``catalogo.publicables``). Las listas
que la app pide enteras —destacados, rubros, beneficios y alertas— van con
``hay_mas`` en falso.
"""
from django.utils import timezone

from api_movil import alertas as reglas_de_alertas
from api_movil import catalogo, respuestas
from api_movil.autenticacion import para_el_equipo, publica
from negocio.models import ParametrosNegocio
from nucleo.busqueda import filtrar_por_texto


def _seccion(request):
    seccion = request.GET.get('seccion', '').strip().upper()
    return seccion if seccion in catalogo.SECCIONES else None


@publica
def destacados(request):
    return respuestas.lista(catalogo.destacados(timezone.localdate()))


@publica
def productos(request):
    seccion = _seccion(request)
    if seccion is None:
        return respuestas.error(catalogo.SECCION_INVALIDA, 400)
    consulta = catalogo.de_la_seccion(seccion)
    rubro = request.GET.get('rubro', '').strip()
    if rubro.isdigit():
        consulta = consulta.filter(id_rubro_id=int(rubro))
    consulta = filtrar_por_texto(
        consulta, ('nombre_producto', 'id_marca__nombre_marca',
                   'id_rubro__nombre_rubro'),
        request.GET.get('q', '').strip())
    hoy = timezone.localdate()
    return respuestas.paginar(
        request, consulta.order_by('nombre_producto', 'pk'),
        lambda producto: catalogo.a_json(producto, hoy))


@publica
def rubros(request):
    seccion = _seccion(request)
    if seccion is None:
        return respuestas.error(catalogo.SECCION_INVALIDA, 400)
    return respuestas.lista([{'id': rubro.id_rubro, 'nombre': rubro.nombre_rubro}
                             for rubro in catalogo.rubros_de(seccion)])


@publica
def beneficios(request):
    """Los beneficios de hoy y, arriba, las categorías que existen.

    Las categorías van aparte para que la app no dependa de una lista fija
    (hoy son cuatro, con Platinum) y las ordene por sus puntos.
    """
    return respuestas.lista(
        [catalogo.beneficio_a_json(beneficio)
         for beneficio in catalogo.beneficios_vigentes(timezone.localdate())],
        categorias=[{'nombre': c.nombre_categoria, 'puntos_minimos': c.puntos_minimos}
                    for c in catalogo.categorias_vigentes()])


@para_el_equipo(GET='ver_productos')
def consulta(request):
    """Consultar un producto: por código (el escáner) o por texto.

    Con ``codigo`` la búsqueda es EXACTA: es lo que manda el escáner, y un
    código parecido es otro producto. Incluye insumos: es para el equipo.
    """
    hoy = timezone.localdate()
    dias_aviso = ParametrosNegocio.vigentes().dias_aviso_vencimiento
    filas = catalogo.del_equipo()
    codigo = request.GET.get('codigo', '').strip()
    if codigo:
        filas = filas.filter(codigo_barras=codigo)
    else:
        filas = filtrar_por_texto(
            filas, ('nombre_producto', 'id_marca__nombre_marca',
                    'id_rubro__nombre_rubro', 'codigo_barras'),
            request.GET.get('q', '').strip())

    def ficha(producto):
        datos = catalogo.a_json(producto, hoy)
        lote = catalogo.proximo_lote(producto, hoy)
        con_stock = producto.tiene_stock_propio
        datos.update({
            'codigo_barras': producto.codigo_barras,
            # Con esto la app valida una cantidad como exigir_cantidad_valida.
            'unidad': producto.unidad_medida,
            'cantidad_entera': producto.exige_cantidad_entera,
            # Lo elaborado al momento y los combos no tienen stock propio:
            # depende de sus componentes.
            'stock_actual': respuestas.numero(producto.stock_actual) if con_stock else None,
            'stock_minimo': respuestas.numero(producto.stock_minimo) if con_stock else None,
            'controla_vencimiento': producto.controla_vencimiento,
            'proximo_vencimiento': {
                'fecha': lote.fecha_vencimiento.isoformat(),
                'dias': lote.dias_para_vencer(hoy),
                'cantidad': respuestas.numero(lote.stock_lote),
                'lote': lote.numero_lote or '',
            } if lote else None,
            'alertas': reglas_de_alertas.del_producto(producto, hoy, dias_aviso),
        })
        return datos

    return respuestas.paginar(request, filas.order_by('nombre_producto', 'pk'),
                              ficha)


@para_el_equipo(GET='ver_stock')
def alertas(request):
    tipo = request.GET.get('tipo', '').strip().upper() or 'TODAS'
    tipos = reglas_de_alertas.FILTROS.get(tipo)
    if tipos is None:
        return respuestas.error(reglas_de_alertas.FILTRO_INVALIDO, 400)
    return respuestas.lista(reglas_de_alertas.listar(
        tipos, timezone.localdate(),
        ParametrosNegocio.vigentes().dias_aviso_vencimiento))
