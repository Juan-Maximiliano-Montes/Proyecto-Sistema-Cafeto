"""El permiso de la pantalla Registro de Actividad.

Uno solo, y es a propósito: ``ver_auditoria``. Las otras cuatro acciones del
catálogo —crear, editar, eliminar, reactivar— no existen para LOG_AUDITORIA
porque el libro es append-only. Un permiso sin vista detrás sólo ensucia la
pantalla de Perfiles y hace creer que la operación es posible.

Se le asigna al Administrador. El Operador no lo recibe: quién hizo qué es
información de control, y el control lo ejerce la dueña del local.
"""
from django.db import migrations


CODIGO = 'ver_auditoria'
MODULO = 'AUDITORIA'
DESCRIPCION = ('Acceder al Registro de Actividad (auditoría) en modo '
               'lectura')
NOMBRE = 'Ver el registro de actividad'


def cargar(apps, schema_editor):
    Permiso = apps.get_model('usuarios', 'Permiso')
    Perfil = apps.get_model('usuarios', 'Perfil')
    PerfilPermiso = apps.get_model('usuarios', 'PerfilPermiso')

    permiso, _ = Permiso.objects.get_or_create(
        codigo=CODIGO,
        defaults={'nombre': NOMBRE, 'modulo': MODULO,
                  'descripcion': DESCRIPCION})

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
        ('operaciones', '0014_realinear_tras_turnos_caja'),
        ('usuarios', '0008_permiso_reactivar_perfil'),
    ]

    operations = [
        migrations.RunPython(cargar, reverse_code=borrar),
    ]
