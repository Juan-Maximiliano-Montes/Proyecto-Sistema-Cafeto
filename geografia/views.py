"""Pantallas de provincias y localidades (DFD 10)."""

from geografia.forms import LocalidadForm, ProvinciaForm
from geografia.models import Localidad, Provincia
from nucleo.crud import Columna, ConfiguracionCRUD, Filtro, registrar

# Provincias y localidades son parte del proceso N° 10, pero sus pantallas
# viven acá, que es donde nacieron las tablas y donde las usa el alta de
# proveedores. Usan la misma máquina genérica que el resto de los datos
# CRUD (nucleo.crud): lo único propio es la configuración.
#
# Son un catálogo geográfico, así que NO tienen baja lógica
# (``tiene_baja=False``): sólo alta y modificación.



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
    a_pedido=True,
    modelo=Localidad,
    formulario=LocalidadForm,
    singular='localidad', plural='localidades', articulo='la',
    titulo='Localidades', icono='bi-geo-alt',
    campo_nombre='nombre_localidad',
    permiso_singular='localidad',
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
