"""Pantallas de Fidelización: categorías, beneficios y clientes (DFD 9)."""
from django.shortcuts import get_object_or_404, render

from compras.models import Proveedor
from fidelizacion.models import (
    Beneficio,
    CategoriaFidelizacion,
    Cliente,
    ModoEntrega,
)
from negocio.models import ParametrosNegocio
from nucleo.crud import Columna, ConfiguracionCRUD, Filtro, registrar
from seguridad.decorators import requiere_permiso
from stock.models import Marca, Rubro

from . import forms


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


CLIENTES = registrar(ConfiguracionCRUD(
    clave='clientes',
    modelo=Cliente,
    formulario=forms.ClienteForm,
    singular='cliente', plural='clientes', articulo='el',
    titulo='Clientes del club', icono='bi-people',
    campo_nombre='nombre_completo',
    nombre_es_identidad=False,
    permiso_singular='cliente',
    columnas=(
        Columna('Cliente', 'nombre_completo'),
        Columna('Categoría', 'id_categoria'),
        Columna('Puntos disponibles', 'puntos_disponibles', 'numero'),
        Columna('Teléfono', 'telefono_cliente'),
        Columna('DNI', 'dni_cliente'),
        Columna('Puntos acumulados', 'puntos_acumulados', 'numero',
                en_tarjeta=False),
        Columna('Código QR', 'qr_cliente', en_tarjeta=False),
        Columna('Email', 'email_cliente', en_tarjeta=False),
        Columna('Alta', 'fecha_alta', en_tarjeta=False),
    ),
    campos_busqueda=('apellido_cliente', 'nombre_cliente', 'dni_cliente',
                     'telefono_cliente', 'email_cliente', 'qr_cliente'),
    filtros=(
        Filtro('categoria', 'Categoría', 'id_categoria', categorias_vigentes),
    ),
    relaciones=('id_categoria',),
    orden=('apellido_cliente', 'nombre_cliente'),
    ficha_extra='fidelizacion/_ficha_cliente.html',
    permiso_exportar='exportar_clientes',
))


@requiere_permiso('ver_clientes', redirigir_a='home')
def tarjeta(request, pk):
    """La tarjeta del club para imprimir: nombre, categoría y el QR con el
    que se identifica en el mostrador (DFD 9: "Código QR y categoría
    asignada")."""
    cliente = get_object_or_404(
        Cliente.objects.select_related('id_categoria'), pk=pk)
    return render(request, 'fidelizacion/tarjeta.html', {
        'cliente': cliente, 'negocio': ParametrosNegocio.vigentes()})
