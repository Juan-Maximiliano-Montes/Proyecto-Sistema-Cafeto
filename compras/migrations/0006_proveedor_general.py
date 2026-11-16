"""El proveedor reservado «Proveedor General (Por asignar)» (DD-86).

Alta idempotente, por el CUIT reservado.
"""
from django.db import migrations

NOMBRE = 'Proveedor General (Por asignar)'
CUIT = '30-00000000-7'


def cargar(apps, schema_editor):
    Proveedor = apps.get_model('compras', 'Proveedor')
    proveedor, _ = Proveedor._base_manager.get_or_create(
        cuit_proveedor=CUIT,
        defaults={'nombre_proveedor': NOMBRE, 'tipo_proveedor': 'MIXTO'})
    if proveedor.borrado_logico:
        proveedor.borrado_logico = False
        proveedor.save(update_fields=['borrado_logico'])


def nada(apps, schema_editor):
    """No se borra al deshacer: puede tener productos."""


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0005_orden_con_diferencias'),
    ]

    operations = [
        migrations.RunPython(cargar, nada),
    ]
