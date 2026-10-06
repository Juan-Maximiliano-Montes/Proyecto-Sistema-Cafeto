"""Carga inicial v2 — tablas de catálogo de Ubicaciones.

Provincia y Localidad son tablas de referencia del DER: el operador no las
carga desde ninguna pantalla, las elige desde el combo del alta de
proveedores. Por eso las semillas van acá y no en un formulario.

Se crea lo mínimo que el local necesita hoy:

    Provincia   Salta
    Localidades Salta Capital (CP 4400) y Campo Quijano (CP 4401)
"""
from django.db import migrations


PROVINCIA = 'Salta'

# (nombre_localidad, codigo_postal)
LOCALIDADES = [
    ('Salta Capital', '4400'),
    ('Campo Quijano', '4401'),
]


def cargar_datos(apps, schema_editor):
    Provincia = apps.get_model('proveedores', 'Provincia')
    Localidad = apps.get_model('proveedores', 'Localidad')

    salta = Provincia.objects.create(nombre_provincia=PROVINCIA)
    for nombre, codigo_postal in LOCALIDADES:
        Localidad.objects.create(
            id_provincia=salta,
            nombre_localidad=nombre,
            codigo_postal=codigo_postal,
        )


def borrar_datos(apps, schema_editor):
    Provincia = apps.get_model('proveedores', 'Provincia')
    Localidad = apps.get_model('proveedores', 'Localidad')

    # Primero las localidades: la FK a Provincia es PROTECT.
    Localidad.objects.filter(
        id_provincia__nombre_provincia=PROVINCIA).delete()
    Provincia.objects.filter(nombre_provincia=PROVINCIA).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('proveedores', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(cargar_datos, borrar_datos),
    ]
