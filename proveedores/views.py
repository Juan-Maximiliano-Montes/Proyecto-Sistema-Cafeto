"""Vistas del CRUD de Proveedores.

Convención de borrado lógico (igual que en usuarios):
    borrado_logico = False  -> el proveedor está vigente y se muestra
    borrado_logico = True   -> el proveedor está eliminado y se oculta
"""
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from usuarios.decorators import requiere_permiso

from .forms import ProveedorForm
from .models import Proveedor


def _contexto_listado(request):
    """Arma el queryset del listado aplicando búsqueda y filtros."""

    busqueda = request.GET.get('busqueda', '').strip()
    condicion = request.GET.get('condicion', '')
    estado = request.GET.get('estado', 'activos')

    proveedores = Proveedor.objects.select_related(
        'id_localidad', 'id_localidad__id_provincia', 'usuario_modificacion')

    # --- Filtro de borrado lógico (invertido: activos = NO borrados) ---
    if estado == 'activos':
        proveedores = proveedores.filter(borrado_logico=False)
    elif estado == 'inactivos':
        proveedores = proveedores.filter(borrado_logico=True)

    # --- Barra de búsqueda (lupa) ---
    if busqueda:
        proveedores = proveedores.filter(
            Q(nombre_proveedor__icontains=busqueda) |
            Q(cuit_proveedor__icontains=busqueda) |
            Q(email_proveedor__icontains=busqueda) |
            Q(domicilio__icontains=busqueda) |
            Q(id_localidad__nombre_localidad__icontains=busqueda)
        )

    # --- Filtro por condición de pago ---
    if condicion:
        proveedores = proveedores.filter(condicion_pago=condicion)

    return {
        'proveedores': proveedores,
        'total': proveedores.count(),
        'form': ProveedorForm(),
        'busqueda': busqueda,
        'condicion': condicion,
        'estado': estado,
        'condiciones': Proveedor.CondicionPago.choices,
    }


# Permisos GRANULARES: cada vista exige el suyo. 'ver_proveedores' abre
# el listado en modo lectura y nada más; el alta, la edición, la baja y la
# reactivación piden 'crear_proveedor', 'editar_proveedor',
# 'eliminar_proveedor' y 'reactivar_proveedor' respectivamente.
#
# El decorador corta el acceso directo por URL: un perfil que tenga sólo
# 'ver_proveedores' y pruebe POSTear a /proveedores/crear/ recibe el aviso
# y vuelve al listado. Si tampoco tiene 'ver_proveedores', ese listado lo
# rebota a su ficha personal ('mi_perfil').
@requiere_permiso('ver_proveedores', redirigir_a='mi_perfil')
def lista_proveedores(request):
    """Listado principal: tabla + búsqueda + filtros."""
    return render(request, 'proveedores/lista_proveedores.html',
                  _contexto_listado(request))


@requiere_permiso('crear_proveedor', redirigir_a='proveedores:lista')
def crear_proveedor(request):
    """Alta desde el modal (icono +)."""
    if request.method != 'POST':
        return redirect('proveedores:lista')

    form = ProveedorForm(request.POST)
    if form.is_valid():
        proveedor = form.save(commit=False)
        proveedor.usuario_modificacion = getattr(request, 'usuario_actual', None)
        proveedor.save()
        messages.success(request, f'Proveedor "{proveedor}" creado correctamente.')
        return redirect('proveedores:lista')

    # Hubo errores: volvemos al listado con el modal abierto y los datos escritos
    messages.error(request, 'No se pudo crear el proveedor. Revisá los datos.')
    contexto = _contexto_listado(request)
    contexto['form'] = form
    contexto['abrir_modal_crear'] = True
    return render(request, 'proveedores/lista_proveedores.html', contexto)


@requiere_permiso('editar_proveedor', redirigir_a='proveedores:lista')
def editar_proveedor(request, id_proveedor):
    """Edición desde el modal (icono lápiz)."""
    proveedor = get_object_or_404(Proveedor, pk=id_proveedor)

    if request.method != 'POST':
        return redirect('proveedores:lista')

    form = ProveedorForm(request.POST, instance=proveedor)
    if form.is_valid():
        proveedor = form.save(commit=False)
        proveedor.usuario_modificacion = getattr(request, 'usuario_actual', None)
        proveedor.save()
        messages.success(request, f'Proveedor "{proveedor}" actualizado correctamente.')
        return redirect('proveedores:lista')

    messages.error(request, 'No se pudo actualizar el proveedor. Revisá los datos.')
    contexto = _contexto_listado(request)
    contexto['form_editar'] = form
    contexto['editar_id'] = id_proveedor
    contexto['abrir_modal_editar'] = True
    return render(request, 'proveedores/lista_proveedores.html', contexto)


@requiere_permiso('eliminar_proveedor', redirigir_a='proveedores:lista')
def eliminar_proveedor(request, id_proveedor):
    """BORRADO LÓGICO (icono tacho).

    No se ejecuta un DELETE: se hace un UPDATE poniendo borrado_logico = True.
    La fila sigue en la base y el historial de compras no se rompe.
    """
    proveedor = get_object_or_404(Proveedor, pk=id_proveedor)

    if request.method == 'POST':
        proveedor.borrado_logico = True
        proveedor.usuario_modificacion = getattr(request, 'usuario_actual', None)
        proveedor.save(update_fields=['borrado_logico', 'usuario_modificacion',
                                      'fecha_modificacion'])
        messages.success(request, f'El proveedor "{proveedor}" fue dado de baja.')

    return redirect('proveedores:lista')


@requiere_permiso('reactivar_proveedor', redirigir_a='proveedores:lista')
def reactivar_proveedor(request, id_proveedor):
    """Deshace el borrado lógico: vuelve a poner borrado_logico = False."""
    proveedor = get_object_or_404(Proveedor, pk=id_proveedor)

    if request.method == 'POST':
        proveedor.borrado_logico = False
        proveedor.usuario_modificacion = getattr(request, 'usuario_actual', None)
        proveedor.save(update_fields=['borrado_logico', 'usuario_modificacion',
                                      'fecha_modificacion'])
        messages.success(request, f'El proveedor "{proveedor}" fue reactivado.')

    return redirect('proveedores:lista')


# ===========================================================================
#  UBICACIONES — pantallas de "Mantener datos maestros" (DFD 10)
# ===========================================================================
# Provincias y localidades son parte del proceso N° 10, pero sus pantallas
# viven acá, que es donde nacieron las tablas y donde las usa el alta de
# proveedores. Usan la misma máquina genérica que el resto de los datos
# maestros: lo único propio es la configuración.
#
# Son un catálogo geográfico, así que NO tienen baja lógica
# (``tiene_baja=False``): sólo alta y modificación.
from maestros.crud import Columna, ConfiguracionCRUD, Filtro, registrar

from .forms import LocalidadForm, ProvinciaForm
from .models import Localidad, Provincia


def todas_las_provincias():
    return [(p.pk, p.nombre_provincia) for p in Provincia.objects.all()]


PROVINCIAS = registrar(ConfiguracionCRUD(
    clave='provincias',
    modelo=Provincia,
    formulario=ProvinciaForm,
    singular='provincia', plural='provincias', articulo='la',
    titulo='Provincias', icono='bi-map',
    campo_nombre='nombre_provincia',
    permiso_singular='provincia',
    espacio_urls='proveedores',
    columnas=(
        Columna('Provincia', 'nombre_provincia'),
        Columna('Localidades', 'localidades.count', 'numero'),
    ),
    campos_busqueda=('nombre_provincia',),
    orden=('nombre_provincia',),
    tiene_baja=False,
))


LOCALIDADES = registrar(ConfiguracionCRUD(
    clave='localidades',
    modelo=Localidad,
    formulario=LocalidadForm,
    singular='localidad', plural='localidades', articulo='la',
    titulo='Localidades', icono='bi-geo-alt',
    campo_nombre='nombre_localidad',
    permiso_singular='localidad',
    espacio_urls='proveedores',
    columnas=(
        Columna('Localidad', 'nombre_localidad'),
        Columna('Provincia', 'id_provincia'),
        Columna('Código postal', 'codigo_postal'),
    ),
    campos_busqueda=('nombre_localidad', 'codigo_postal',
                     'id_provincia__nombre_provincia'),
    filtros=(
        Filtro('provincia', 'Provincia', 'id_provincia', todas_las_provincias),
    ),
    relaciones=('id_provincia',),
    orden=('id_provincia__nombre_provincia', 'nombre_localidad'),
    tiene_baja=False,
    # El nombre es único DENTRO de la provincia: hay homónimas en
    # jurisdicciones distintas.
    ambito_unico=('id_provincia',),
))
