"""Catálogo de puesta en marcha de Compras: los tipos de egreso.

Las altas son IDEMPOTENTES (``get_or_create`` por la clave natural).
"""
from django.db import migrations

TIPOS_EGRESO = [
    ('Proveedores', 'Pago de facturas de mercadería'),
    ('Servicios', 'Energía, agua, gas e internet'),
    ('Impuestos', 'Ingresos Brutos, IVA, Monotributo / ARCA'),
    ('Sueldos', 'Sueldos y retiros del personal'),
    ('Gastos varios', 'Mantenimiento, papelería y otros gastos menores'),
]


def cargar(apps, schema_editor):
    TipoEgreso = apps.get_model('compras', 'TipoEgreso')
    for nombre, descripcion in TIPOS_EGRESO:
        TipoEgreso.objects.get_or_create(
            nombre_tipo_egreso=nombre,
            defaults={'descripcion_tipo_egreso': descripcion})


def borrar(apps, schema_editor):
    """Deshace la carga (ver el borrar de seguridad/0003)."""
    apps.get_model('compras', 'TipoEgreso')._base_manager.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0003_initial'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
