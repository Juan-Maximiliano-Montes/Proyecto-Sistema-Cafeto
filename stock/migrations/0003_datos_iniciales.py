"""Catálogos de puesta en marcha de Stock: los rubros y la marca genérica.

No se puede dar de alta un producto sin un rubro y sin una marca.

**Sin marca** es la marca de los productos a granel y de elaboración
propia. La identidad de un producto es la terna (marca, contenido,
unidad), DD-55, así que todo producto necesita una marca —incluso el café
que se vende suelto.

Las altas son IDEMPOTENTES (``get_or_create`` por la clave natural).
"""
from django.db import migrations

RUBROS = [
    ('Cafetería', 'Cafés, infusiones y bebidas preparadas'),
    ('Minisuper', 'Almacén, bebidas envasadas y artículos de conveniencia'),
]

MARCAS = [
    ('Sin marca', 'Productos a granel y de elaboración propia'),
]


def cargar(apps, schema_editor):
    Rubro = apps.get_model('stock', 'Rubro')
    Marca = apps.get_model('stock', 'Marca')
    for nombre, descripcion in RUBROS:
        Rubro.objects.get_or_create(
            nombre_rubro=nombre, defaults={'descripcion_rubro': descripcion})
    for nombre, descripcion in MARCAS:
        Marca.objects.get_or_create(
            nombre_marca=nombre, defaults={'descripcion_marca': descripcion})


def borrar(apps, schema_editor):
    """Deshace la carga (ver el borrar de seguridad/0003)."""
    for modelo in ('Marca', 'Rubro'):
        apps.get_model('stock', modelo)._base_manager.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0002_initial'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
