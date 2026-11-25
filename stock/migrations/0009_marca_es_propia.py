"""``Marca.es_propia``: las marcas del propio local (idempotente).

Los elaborados y producidos sólo admiten marcas propias. Se marcan las tres
que el local ya usa si existen: «Genérica», «Sin marca» y «Cafeto». Correrla
dos veces deja todo igual.
"""
from django.db import migrations, models

NOMBRES = ('Genérica', 'Sin marca', 'Cafeto')


def marcar(apps, schema_editor):
    Marca = apps.get_model('stock', 'Marca')
    for nombre in NOMBRES:
        Marca._base_manager.filter(nombre_marca__iexact=nombre).update(
            es_propia=True)


def nada(apps, schema_editor):
    """Al deshacer, el campo se borra con la migración de esquema."""


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0008_sugerencias_de_catalogo'),
    ]

    operations = [
        migrations.AddField(
            model_name='marca',
            name='es_propia',
            field=models.BooleanField(
                default=False, verbose_name='Marca propia del local'),
        ),
        migrations.RunPython(marcar, nada),
    ]
