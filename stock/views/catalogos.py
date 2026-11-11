"""Pantallas del catálogo de Stock: rubros, marcas y productos.

Las configuraciones de CRUD se registran en ``nucleo.crud``; la búsqueda
de productos (``buscar_productos``) la usan los selectores de todo el
sistema.
"""

from django.http import JsonResponse

from compras.models import Proveedor
from nucleo.busqueda import filtrar_por_texto
from nucleo.crud import Columna, ConfiguracionCRUD, Filtro, registrar
from seguridad.servicios import registrar_acceso_denegado
from stock.models import Marca, Producto, Rubro, TipoProducto

from stock import forms

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

    try:
        pagina = max(1, int(request.GET.get('page', 1)))
    except (TypeError, ValueError):
        pagina = 1

    busqueda = request.GET.get('q', '').strip()
    productos = Producto.objects.filter(
        borrado_logico=False).select_related('id_marca')
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


def proveedores_vigentes():
    return [(p.pk, p.nombre_proveedor)
            for p in Proveedor.objects.filter(borrado_logico=False)]


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
        Columna('Stock', 'stock_para_mostrar', 'numero'),
        Columna('Marca', 'id_marca'),
        Columna('Presentación', 'presentacion'),
        Columna('Rubro', 'id_rubro'),
        Columna('Tipo', 'tipo_corto'),
        Columna('Proveedor', 'id_proveedor', en_tarjeta=False),
        Columna('Stock mínimo', 'stock_minimo', 'numero', en_tarjeta=False),
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
    acciones_extra=(
        ('Importar', 'bi-file-earmark-arrow-up', 'importar', ('productos',),
         'importar_productos'),
        ('Actualizar precios', 'bi-graph-up-arrow', 'stock:precios', (),
         'actualizar_precios'),
    ),
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
