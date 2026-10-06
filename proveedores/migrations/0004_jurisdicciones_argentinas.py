"""Completa el catálogo geográfico con las 24 jurisdicciones (DD-16).

La carga anterior tenía sólo Salta, que es donde está el local. El DER
pide el catálogo completo: un proveedor o un cliente puede ser de
cualquier provincia, y la pantalla de Localidades necesita poder elegir
la provincia antes de dar de alta la localidad.

Es idempotente: ``get_or_create`` por nombre, así que no duplica Salta ni
pisa nada de lo que ya esté cargado.
"""
from django.db import migrations


# Las 24 jurisdicciones: 23 provincias y la Ciudad Autónoma.
JURISDICCIONES = [
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
    'Salta',
    'San Juan',
    'San Luis',
    'Santa Cruz',
    'Santa Fe',
    'Santiago del Estero',
    'Tierra del Fuego, Antártida e Islas del Atlántico Sur',
    'Tucumán',
]


def cargar(apps, schema_editor):
    Provincia = apps.get_model('proveedores', 'Provincia')
    for nombre in JURISDICCIONES:
        Provincia.objects.get_or_create(nombre_provincia=nombre)


def borrar(apps, schema_editor):
    """Saca las que agregó esta migración, dejando Salta y sus localidades."""
    Provincia = apps.get_model('proveedores', 'Provincia')
    Provincia.objects.filter(
        nombre_provincia__in=JURISDICCIONES
    ).exclude(nombre_provincia='Salta').filter(
        localidades__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('proveedores', '0003_alinear_con_el_der'),
        ('operaciones', '0002_columna_generada_y_defaults'),
    ]

    operations = [
        migrations.RunPython(cargar, reverse_code=borrar),
    ]
