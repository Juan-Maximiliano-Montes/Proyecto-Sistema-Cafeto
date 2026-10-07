"""Jurisdicciones argentinas: las 23 provincias y la CABA.

El alta de un proveedor pide localidad, y una localidad pertenece a una
provincia: sin este catálogo no se puede cargar el primer proveedor.

Son datos de referencia de la República Argentina, no del negocio: no se
dan de alta desde ninguna pantalla porque no cambian. Salta va primera
por ser la del local; el resto, alfabético.

Las dos localidades son las del arranque. Las demás se cargan desde la
pantalla de Localidades, a medida que aparecen proveedores.

Las altas son IDEMPOTENTES (``get_or_create`` por la clave natural).
"""
from django.db import migrations

PROVINCIAS = [
    'Salta',
    'Buenos Aires',
    'Catamarca',
    'Chaco',
    'Chubut',
    'Ciudad Autónoma de Buenos Aires',
    'Córdoba',
    'Corrientes',
    'Entre Ríos',
    'Formosa',
    'Jujuy',
    'La Pampa',
    'La Rioja',
    'Mendoza',
    'Misiones',
    'Neuquén',
    'Río Negro',
    'San Juan',
    'San Luis',
    'Santa Cruz',
    'Santa Fe',
    'Santiago del Estero',
    'Tierra del Fuego, Antártida e Islas del Atlántico Sur',
    'Tucumán',
]

# (localidad, provincia)
LOCALIDADES = [
    ('Salta Capital', 'Salta'),
    ('Campo Quijano', 'Salta'),
]


def cargar(apps, schema_editor):
    Provincia = apps.get_model('proveedores', 'Provincia')
    Localidad = apps.get_model('proveedores', 'Localidad')

    provincias = {}
    for nombre in PROVINCIAS:
        provincias[nombre], _ = Provincia.objects.get_or_create(
            nombre_provincia=nombre)

    for localidad, provincia in LOCALIDADES:
        Localidad.objects.get_or_create(
            nombre_localidad=localidad,
            id_provincia=provincias[provincia])


def borrar(apps, schema_editor):
    """Deshace la carga (ver el borrar de usuarios/0002)."""
    for modelo in ('Localidad', 'Provincia'):
        apps.get_model('proveedores', modelo)._base_manager.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('proveedores', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
