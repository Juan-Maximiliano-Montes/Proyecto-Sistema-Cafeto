"""Catálogo de puesta en marcha de Fidelización: las categorías.

**Bronze** es la categoría de ingreso (umbral 0): es la puerta por la que
entra todo cliente nuevo, así que siempre tiene que existir una con umbral
0. Lo defiende ``CategoriaFidelizacion.clean()``.

Las altas son IDEMPOTENTES (``get_or_create`` por la clave natural).
"""
from decimal import Decimal

from django.db import migrations

# (nombre, puntos_minimos, importe_por_punto). Desde DD-49 la regla se
# expresa en PESOS POR PUNTO, que es como la piensa la dueña: la categoría
# de ingreso da 1 punto cada $100 y cada escalón pide menos pesos por el
# mismo punto.
CATEGORIAS = [
    ('Bronze', 0, Decimal('100.00')),
    ('Silver', 1000, Decimal('83.33')),
    ('Gold', 5000, Decimal('66.67')),
    ('Platinum', 15000, Decimal('50.00')),
]


def cargar(apps, schema_editor):
    Categoria = apps.get_model('fidelizacion', 'CategoriaFidelizacion')
    for nombre, minimos, importe in CATEGORIAS:
        Categoria.objects.get_or_create(
            nombre_categoria=nombre,
            defaults={'puntos_minimos': minimos,
                      'importe_por_punto': importe})


def borrar(apps, schema_editor):
    """Deshace la carga (ver el borrar de seguridad/0003)."""
    apps.get_model(
        'fidelizacion', 'CategoriaFidelizacion')._base_manager.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('fidelizacion', '0003_initial'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
