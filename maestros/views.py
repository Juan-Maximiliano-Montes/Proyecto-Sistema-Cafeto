"""Las siete pantallas de datos maestros de la app ``maestros``.

Acá no hay lógica: cada entidad declara QUÉ es (modelo, formulario, cómo
se llama en castellano, qué columnas muestra, por qué se busca y por qué
se filtra) y la máquina de ``crud.py`` pone el CÓMO, que es idéntico para
todas.

Provincias y localidades no están acá: viven en la app ``proveedores``,
donde nacieron y donde las usa el alta de proveedores.
"""
from django.db.models import Q
from django.http import JsonResponse

from maestros.crud import Columna, ConfiguracionCRUD, Filtro, registrar
from proveedores.models import Proveedor

from . import forms
from .models import (
    Beneficio,
    CategoriaFidelizacion,
    Marca,
    MedioPago,
    ModoEntrega,
    Producto,
    CajaRegistradora,
    Rubro,
    TipoEgreso,
)


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
        return JsonResponse({'detail': 'Sin permiso.'}, status=403)

    try:
        pagina = max(1, int(request.GET.get('page', 1)))
    except (TypeError, ValueError):
        pagina = 1

    busqueda = request.GET.get('q', '').strip()
    productos = Producto.objects.filter(
        borrado_logico=False).select_related('id_marca')
    if busqueda:
        productos = productos.filter(
            Q(nombre_producto__icontains=busqueda)
            | Q(codigo_barras__icontains=busqueda)
            | Q(id_marca__nombre_marca__icontains=busqueda))
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


def categorias_vigentes():
    return [(c.pk, c.nombre_categoria)
            for c in CategoriaFidelizacion.objects.filter(borrado_logico=False)]


# ===========================================================================
#  CONFIGURACIONES
# ===========================================================================
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
        Columna('Marca', 'id_marca'),
        Columna('Presentación', 'presentacion'),
        Columna('Rubro', 'id_rubro'),
        Columna('Proveedor', 'id_proveedor'),
        Columna('Precio de venta', 'precio_venta', 'dinero'),
        Columna('Stock', 'stock_actual', 'numero'),
        Columna('Stock mínimo', 'stock_minimo', 'numero'),
        Columna('Vencimiento', 'controla_vencimiento', 'si_no'),
    ),
    campos_busqueda=('nombre_producto', 'codigo_barras',
                     'id_marca__nombre_marca',
                     'id_rubro__nombre_rubro',
                     'id_proveedor__nombre_proveedor'),
    filtros=(
        Filtro('marca', 'Marca', 'id_marca', marcas_vigentes),
        Filtro('rubro', 'Rubro', 'id_rubro', rubros_vigentes),
        Filtro('proveedor', 'Proveedor', 'id_proveedor', proveedores_vigentes),
    ),
    relaciones=('id_rubro', 'id_proveedor', 'id_marca'),
    orden=('id_marca__nombre_marca', 'nombre_producto'),
    # La identidad del producto es la TERNA: dos artículos con el mismo
    # nombre son distintos si cambian de marca o de presentación (DD-55).
    ambito_unico=('id_marca', 'contenido', 'unidad_medida'),
))


TIPOS_EGRESO = registrar(ConfiguracionCRUD(
    clave='tipos_egreso',
    modelo=TipoEgreso,
    formulario=forms.TipoEgresoForm,
    singular='tipo de egreso', plural='tipos de egreso', articulo='el',
    titulo='Tipos de egreso', icono='bi-arrow-down-circle',
    campo_nombre='nombre_tipo_egreso',
    permiso_singular='tipo_egreso',
    columnas=(
        Columna('Tipo de egreso', 'nombre_tipo_egreso'),
        Columna('Descripción', 'descripcion_tipo_egreso'),
    ),
    campos_busqueda=('nombre_tipo_egreso', 'descripcion_tipo_egreso'),
    orden=('nombre_tipo_egreso',),
))


MEDIOS_PAGO = registrar(ConfiguracionCRUD(
    clave='medios_pago',
    modelo=MedioPago,
    formulario=forms.MedioPagoForm,
    singular='medio de pago', plural='medios de pago', articulo='el',
    titulo='Medios de pago', icono='bi-credit-card',
    campo_nombre='nombre_medio_pago',
    permiso_singular='medio_pago',
    columnas=(
        Columna('Medio de pago', 'nombre_medio_pago'),
        Columna('Recargo', 'recargo_porcentaje', 'numero'),
    ),
    campos_busqueda=('nombre_medio_pago',),
    orden=('nombre_medio_pago',),
))


CAJAS_REGISTRADORAS = registrar(ConfiguracionCRUD(
    clave='cajas_registradoras',
    modelo=CajaRegistradora,
    formulario=forms.CajaRegistradoraForm,
    singular='caja registradora', plural='cajas registradoras', articulo='la',
    titulo='Cajas registradoras', icono='bi-shop',
    campo_nombre='nombre_caja_registradora',
    permiso_singular='caja_registradora',
    columnas=(
        Columna('Caja registradora', 'nombre_caja_registradora'),
    ),
    campos_busqueda=('nombre_caja_registradora',),
    orden=('nombre_caja_registradora',),
))


CATEGORIAS = registrar(ConfiguracionCRUD(
    clave='categorias',
    modelo=CategoriaFidelizacion,
    formulario=forms.CategoriaFidelizacionForm,
    singular='categoría', plural='categorías', articulo='la',
    titulo='Categorías de fidelización', icono='bi-award',
    campo_nombre='nombre_categoria',
    permiso_singular='categoria',
    columnas=(
        Columna('Categoría', 'nombre_categoria'),
        Columna('Puntos mínimos', 'puntos_minimos', 'numero'),
        Columna('Pesos por punto', 'importe_por_punto', 'dinero'),
    ),
    campos_busqueda=('nombre_categoria',),
    orden=('puntos_minimos',),
))


BENEFICIOS = registrar(ConfiguracionCRUD(
    clave='beneficios',
    modelo=Beneficio,
    formulario=forms.BeneficioForm,
    singular='beneficio', plural='beneficios', articulo='el',
    titulo='Beneficios', icono='bi-gift',
    campo_nombre='descripcion_beneficio',
    permiso_singular='beneficio',
    columnas=(
        Columna('Beneficio', 'descripcion_beneficio'),
        Columna('Categoría', 'id_categoria'),
        Columna('Tipo', 'get_tipo_beneficio_display'),
        Columna('Entrega', 'get_modo_entrega_display'),
        Columna('Descuento', 'porcentaje_descuento', 'numero'),
        Columna('Puntos', 'puntos_requeridos', 'numero'),
        Columna('Vigente desde', 'vigencia_desde'),
        Columna('Vigente hasta', 'vigencia_hasta'),
    ),
    campos_busqueda=('descripcion_beneficio',
                     'id_categoria__nombre_categoria'),
    filtros=(
        Filtro('categoria', 'Categoría', 'id_categoria', categorias_vigentes),
    ),
    relaciones=('id_categoria',),
    orden=('id_categoria__puntos_minimos', 'descripcion_beneficio'),
    # La receta: qué productos se le entregan al cliente cuando el
    # beneficio es EN ESPECIE. Se carga en el mismo modal que el
    # beneficio y se guarda en la misma transacción, porque un beneficio
    # en especie sin receta no se puede entregar (ver validar_receta).
    formset=forms.RecetaFormSet,
    formset_titulo='Receta del beneficio (productos que se entregan)',
    formset_vacio='Todavía no hay productos en la receta. Agregá al menos '
                  'uno: es lo que se le entrega al cliente.',
    formset_agregar='Agregar producto',
    formset_depende_de='modo_entrega',
    formset_visible_si=ModoEntrega.ESPECIE,
))
