"""Permiso ``reactivar_perfil``.

Cuando se armó el catálogo granular, PERFILES se borraba FÍSICAMENTE: no
había baja que deshacer, así que el permiso no existía. Desde la Fase 4 la
baja de un perfil es lógica (RN-8.1.1) y la pantalla tiene su botón de
reactivar, que necesita su propio permiso para poder darse por separado.
"""
from django.db import migrations

CODIGO = 'reactivar_perfil'


def cargar(apps, schema_editor):
    Perfil = apps.get_model('usuarios', 'Perfil')
    Permiso = apps.get_model('usuarios', 'Permiso')
    PerfilPermiso = apps.get_model('usuarios', 'PerfilPermiso')

    permiso, _ = Permiso.objects.get_or_create(
        codigo=CODIGO,
        defaults={'nombre': 'Reactivar perfiles',
                  'descripcion': 'Deshacer la baja lógica de un perfil',
                  'modulo': 'PERFILES'})
    administrador = Perfil.objects.filter(nombre='Administrador').first()
    if administrador is not None:
        PerfilPermiso.objects.get_or_create(
            perfil=administrador, permiso=permiso)


def borrar(apps, schema_editor):
    Permiso = apps.get_model('usuarios', 'Permiso')
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET LOCAL cafeto.pasaje_historico = 'on'")
        Permiso.objects.filter(codigo=CODIGO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0007_nombres_de_cincuenta'),
    ]

    operations = [
        migrations.RunPython(cargar, reverse_code=borrar),
    ]
