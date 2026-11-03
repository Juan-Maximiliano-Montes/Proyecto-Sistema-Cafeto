"""El permiso de anular una venta (Hito 4).

DD-39: el catálogo de permisos lo define el código y viaja en una
migración, junto con la pantalla que lo usa. Lo tienen el Administrador y
el Encargado; el Operador vende pero no anula: si se equivocó, la
anulación la hace alguien con permiso, y queda su nombre.
"""
from django.db import migrations

CODIGO = 'anular_venta'
PERFILES = ('Administrador', 'Encargado')


def cargar(apps, schema_editor):
    Permiso = apps.get_model('seguridad', 'Permiso')
    Perfil = apps.get_model('seguridad', 'Perfil')
    PerfilPermiso = apps.get_model('seguridad', 'PerfilPermiso')
    permiso, _ = Permiso.objects.get_or_create(
        codigo=CODIGO,
        defaults={'nombre': 'Anular ventas',
                  'descripcion': 'Anular una venta del turno abierto, con '
                                 'motivo: vuelven el stock, la plata y los '
                                 'puntos',
                  'modulo': 'VENTAS'})
    for nombre in PERFILES:
        perfil = Perfil.objects.filter(nombre=nombre).first()
        if perfil is not None:
            PerfilPermiso.objects.get_or_create(perfil=perfil, permiso=permiso)


def borrar(apps, schema_editor):
    # Por SQL y no por el ORM: al deshacer varias migraciones juntas, los
    # modelos históricos de seguridad pueden venir de estados distintos y
    # el borrado en cascada del ORM se confunde de clase.
    schema_editor.execute(
        'DELETE FROM perfiles_permisos WHERE id_permiso IN '
        '(SELECT id_permiso FROM permisos WHERE codigo_permiso = %s)', [CODIGO])
    schema_editor.execute(
        'DELETE FROM permisos WHERE codigo_permiso = %s', [CODIGO])


class Migration(migrations.Migration):

    dependencies = [
        ('ventas', '0003_anulacion_de_venta'),
        ('seguridad', '0003_datos_iniciales'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
