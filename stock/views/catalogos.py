"""Pantallas del catálogo de Stock: rubros, marcas y productos.

Las configuraciones de CRUD se registran en ``nucleo.crud``; la búsqueda
de productos (``buscar_productos``) la usan los selectores de todo el
sistema.
"""

from django.db.models import Q, Sum
from django.http import JsonResponse
from django.utils import timezone

from compras.models import Proveedor
from nucleo.busqueda import filtrar_por_texto
from nucleo.crud import Columna, ConfiguracionCRUD, Filtro, registrar
from seguridad.servicios import registrar_acceso_denegado
from stock.models import (
    COMPONENTES_ADMITIDOS,
    TIPOS_VENDIBLES,
    Marca,
    Producto,
    Rubro,
    TipoProducto,
)

from stock import forms
from nucleo.validaciones import id_valido

# Qué tipos de producto ofrece cada selector (``?para=``). Se arma con las
# mismas reglas del modelo, así que el buscador y la validación no pueden
# discrepar: lo que el buscador no ofrece, el modelo tampoco lo acepta.
TIPOS_PARA_BUSQUEDA = {
    # La receta de un elaborado o producido: sólo insumos.
    'receta': (COMPONENTES_ADMITIDOS[TipoProducto.ELABORADO]
               | COMPONENTES_ADMITIDOS[TipoProducto.PRODUCIDO]),
    # El contenido de un combo: lo vendible, sin insumos ni otros combos.
    'combo': COMPONENTES_ADMITIDOS[TipoProducto.COMBO],
    # El regalo de un beneficio en especie: algo que se pueda vender.
    'beneficio': TIPOS_VENDIBLES,
}

# ---------------------------------------------------------------------------
#  BÚSQUEDA DE PRODUCTOS (para la receta de los beneficios)
# ---------------------------------------------------------------------------
#  Cuántos productos entran en cada página del desplegable. El catálogo
#  completo SE PUEDE recorrer: Select2 pide la página siguiente cuando el
#  usuario llega al final de la lista. Lo que no pasa es que lleguen los
#  5.000 de una, que es lo que trababa el navegador.
POR_PAGINA = 25


def buscar_productos(request):
    """Página del catálogo de productos, en JSON, para el combo de la receta.

    Es el back del selector de productos de un beneficio en especie. Habla
    el protocolo que espera Select2::

        GET  ?q=cafe&page=2
        200  {"results": [{"id": 7, "text": "La Morenita · Café · 500 G"}],
              "pagination": {"more": true}}

    Por qué existe
    --------------
    El ``<select>`` dibujaba un ``<option>`` por producto, y el formset lo
    repetía por renglón más el molde de los nuevos. Con 5.000 productos la
    pantalla de Beneficios pesaba 705 KB y tardaba 8,4 segundos: el
    navegador quedaba clavado parseando HTML. Medido, no estimado.

    Lo que NO hace es recortar el catálogo: con ``more`` el desplegable
    sigue trayendo páginas mientras se scrollea, así que el usuario llega a
    cualquier producto. Lo único que cambia es CUÁNDO viaja cada fila.

    Permiso: ``ver_productos``, el mismo que para ver el listado, así que
    no expone nada que el usuario no pudiera mirar entrando por la
    pantalla. Se comprueba en el SERVIDOR: esconder el control no alcanza.

    Sólo devuelve productos VIGENTES (V-G7): una receta no se arma con
    algo que está dado de baja.
    """
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return JsonResponse({'detail': 'Sesión no iniciada.'}, status=401)
    if not usuario.tiene_permiso('ver_productos'):
        registrar_acceso_denegado(request, 'ver_productos')
        return JsonResponse({'detail': 'Sin permiso.'}, status=403)

    pagina = id_valido(request.GET.get('page')) or 1

    busqueda = request.GET.get('q', '').strip()
    productos = Producto.objects.filter(
        borrado_logico=False).select_related('id_marca')
    # ?para=receta|combo|beneficio: lo decide el SERVIDOR qué tipos puede
    # elegir cada pantalla (STK-01 / FID-01). Un cliente que mande otro
    # ?tipos= o ?excluir= sólo puede achicar la lista, nunca ampliarla.
    para = request.GET.get('para', '')
    if para:
        permitidos = TIPOS_PARA_BUSQUEDA.get(para)
        if permitidos is None:
            return JsonResponse({'detail': 'Ese selector de productos no existe.'}, status=400)
        productos = productos.filter(tipo_producto__in=permitidos)
    # ?excluir=COMBO,INSUMO  /  ?tipos=REVENTA,INSUMO: el selector de una
    # receta no ofrece combos; el del mostrador no ofrece insumos.
    excluir = [t for t in request.GET.get('excluir', '').split(',') if t]
    tipos = [t for t in request.GET.get('tipos', '').split(',') if t]
    if excluir:
        productos = productos.exclude(tipo_producto__in=excluir)
    if tipos:
        productos = productos.filter(tipo_producto__in=tipos)
    if busqueda:
        # El código de barras se compara tal cual (lo tipea un lector); el
        # nombre y la marca, sin acentos ni mayúsculas.
        por_codigo = productos.filter(codigo_barras=busqueda)
        productos = (por_codigo if por_codigo.exists() else
                     filtrar_por_texto(productos, ('nombre_producto',
                                                   'id_marca__nombre_marca'),
                                       busqueda))
    productos = productos.order_by('nombre_producto', 'pk')

    desde = (pagina - 1) * POR_PAGINA
    # Se pide UNA fila de más: si viene, hay página siguiente. Así se evita
    # el COUNT(*) sobre el catálogo entero en cada tecla.
    tramo = list(productos[desde:desde + POR_PAGINA + 1])
    hay_mas = len(tramo) > POR_PAGINA

    return JsonResponse({
        'results': [{'id': p.pk, 'text': str(p)}
                    for p in tramo[:POR_PAGINA]],
        'pagination': {'more': hay_mas},
    })



# ---------------------------------------------------------------------------
#  Opciones de los filtros naturales (se evalúan en cada pedido, para que
#  reflejen lo que está vigente ahora)
# ---------------------------------------------------------------------------
def rubros_vigentes():
    return [(r.pk, r.nombre_rubro)
            for r in Rubro.objects.filter(borrado_logico=False)]


PRIMER_CODIGO_INTERNO = 1000


def siguiente_codigo_interno():
    """El primer número libre de cuatro dígitos (o más, si se acabaron).

    Arranca en 1000 y no en 0001: una planilla de cálculo se come los ceros
    de adelante y el código dejaría de coincidir al reimportar.
    """
    usados = set(Producto.objects.filter(codigo_barras__regex=r'^[0-9]{4,6}$')
                 .values_list('codigo_barras', flat=True))
    numero = PRIMER_CODIGO_INTERNO
    while str(numero) in usados:
        numero += 1
    return str(numero)


def codigo_interno(request):
    """El próximo código propio, para el botón del alta de productos."""
    usuario = request.usuario_actual
    if not (usuario.tiene_permiso('crear_producto') or usuario.tiene_permiso('editar_producto')):
        registrar_acceso_denegado(request, 'crear_producto')
        return JsonResponse({'error': 'Tu perfil no puede cargar productos.'}, status=403)
    return JsonResponse({'codigo': siguiente_codigo_interno()})



def proveedores_vigentes():
    # Sólo los que venden mercadería: la luz o el agua no proveen productos.
    return [(p.pk, p.nombre_proveedor)
            for p in Proveedor.objects.filter(borrado_logico=False)
            .exclude(tipo_proveedor='SERVICIOS').order_by('nombre_proveedor')]


def marcas_vigentes():
    return [(m.pk, m.nombre_marca)
            for m in Marca.objects.filter(borrado_logico=False)]


def tipos_de_producto():
    return [(valor, etiqueta.split(' (')[0])
            for valor, etiqueta in TipoProducto.choices]





RUBROS = registrar(ConfiguracionCRUD(
    clave='rubros',
    modelo=Rubro,
    formulario=forms.RubroForm,
    singular='rubro', plural='rubros', articulo='el',
    titulo='Rubros', icono='bi-tags',
    campo_nombre='nombre_rubro',
    permiso_singular='rubro',
    columnas=(
        Columna('Rubro', 'nombre_rubro'),
        Columna('Descripción', 'descripcion_rubro'),
        Columna('Se ofrece en productos', 'activo', 'si_no'),
        Columna('Rebaja por vencer (días)', 'dias_rebaja_vencimiento',
                'numero'),
        Columna('Rebaja (%)', 'porcentaje_rebaja_vencimiento', 'numero'),
    ),
    campos_busqueda=('nombre_rubro', 'descripcion_rubro'),
    orden=('nombre_rubro',),
))


MARCAS = registrar(ConfiguracionCRUD(
    clave='marcas',
    modelo=Marca,
    formulario=forms.MarcaForm,
    singular='marca', plural='marcas', articulo='la',
    titulo='Marcas', icono='bi-bookmark-star',
    campo_nombre='nombre_marca',
    permiso_singular='marca',
    columnas=(
        Columna('Marca', 'nombre_marca'),
        Columna('Descripción', 'descripcion_marca'),
        Columna('Productos', 'productos.count', 'numero'),
    ),
    campos_busqueda=('nombre_marca', 'descripcion_marca'),
    orden=('nombre_marca',),
))


def con_stock_vencido(productos):
    """Lo que hay en lotes ya vencidos, por producto y en la misma
    consulta: eso no se vende y no cuenta como stock apto."""
    return productos.annotate(vencido_en_lotes=Sum('lotes__stock_lote', filter=Q(
        lotes__stock_lote__gt=0, lotes__fecha_vencimiento__lt=timezone.localdate())))


PRODUCTOS = registrar(ConfiguracionCRUD(
    clave='productos',
    a_pedido=True,
    modelo=Producto,
    formulario=forms.ProductoForm,
    singular='producto', plural='productos', articulo='el',
    titulo='Productos', icono='bi-box-seam',
    campo_nombre='nombre_producto',
    permiso_singular='producto',
    # El nombre va pegado al ID: con la marca en el medio, el ID se leía
    # como si fuera el de la marca y no el del producto.
    columnas=(
        Columna('Producto', 'nombre_producto'),
        Columna('Precio de venta', 'precio_para_mostrar', 'dinero'),
        Columna('Stock apto', 'stock_para_mostrar', 'numero', aclaracion='aclaracion_de_stock',
                de_producto=True),
        Columna('Marca', 'id_marca'),
        Columna('Presentación', 'presentacion'),
        Columna('Rubro', 'id_rubro'),
        Columna('Tipo', 'tipo_corto'),
        Columna('Proveedor', 'id_proveedor', en_tarjeta=False),
        Columna('Stock mínimo', 'stock_minimo', 'numero', en_tarjeta=False, de_producto=True),
        Columna('Vencimiento', 'controla_vencimiento', 'si_no', en_tarjeta=False),
    ),
    campos_busqueda=('nombre_producto', 'codigo_barras',
                     'id_marca__nombre_marca',
                     'id_rubro__nombre_rubro',
                     'id_proveedor__nombre_proveedor'),
    filtros=(
        Filtro('tipo', 'Tipo', 'tipo_producto', tipos_de_producto),
        Filtro('marca', 'Marca', 'id_marca', marcas_vigentes),
        Filtro('rubro', 'Rubro', 'id_rubro', rubros_vigentes),
        Filtro('proveedor', 'Proveedor', 'id_proveedor', proveedores_vigentes),
    ),
    relaciones=('id_rubro', 'id_proveedor', 'id_marca'),
    anotar=con_stock_vencido,
    acciones_extra=(
        ('Importar', 'bi-file-earmark-arrow-up', 'importar', ('productos',),
         'importar_productos'),
        ('Actualizar precios', 'bi-graph-up-arrow', 'stock:precios', (),
         'actualizar_precios'),
        ('Etiquetas de góndola', 'bi-tags', 'stock:etiquetas_gondola', (),
         'ver_productos'),
    ),
    ficha_extra='stock/_foto_producto.html',
    orden=('id_marca__nombre_marca', 'nombre_producto'),
    # La identidad del producto es la TERNA: dos artículos con el mismo
    # nombre son distintos si cambian de marca o de presentación (DD-55).
    ambito_unico=('id_marca', 'contenido', 'unidad_medida'),
    # La receta de un elaborado o producido y el contenido de un combo se
    # cargan en el mismo modal del producto.
    formset=forms.ComposicionFormSet,
    formset_titulo='Receta o contenido del combo',
    formset_vacio='Todavía no tiene componentes.',
    formset_agregar='Agregar componente',
    formset_depende_de='tipo_producto',
    formset_visible_si=','.join((TipoProducto.ELABORADO,
                                 TipoProducto.PRODUCIDO, TipoProducto.COMBO)),
))
