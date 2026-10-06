"""Permisos de la pantalla de Marcas.

El catálogo de la migración 0003 se amplió con la entidad MARCAS, pero esa
migración ya corrió: hay que volver a aplicarla. ``cargar`` es idempotente
—usa ``get_or_create``— así que lo único que hace en una base ya migrada es
agregar los cinco permisos que faltan y asignárselos al Administrador.
"""
from django.db import migrations

from maestros.migrations import __name__ as _paquete  # noqa: F401


def cargar(apps, schema_editor):
    from importlib import import_module
    modulo = import_module('maestros.migrations.0003_permisos_datos_maestros')
    modulo.cargar(apps, schema_editor)


def borrar(apps, schema_editor):
    Permiso = apps.get_model('usuarios', 'Permiso')
    codigos = ['ver_marcas', 'crear_marca', 'editar_marca',
               'eliminar_marca', 'reactivar_marca']
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET LOCAL cafeto.pasaje_historico = 'on'")
        Permiso.objects.filter(codigo__in=codigos).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('maestros', '0004_marcas_y_presentacion'),
    ]

    operations = [
        migrations.RunPython(cargar, reverse_code=borrar),
    ]
