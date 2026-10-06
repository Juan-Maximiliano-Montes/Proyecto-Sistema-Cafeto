"""Data Migration — el catálogo de permisos pasa a SÓLO LECTURA.

Se quitaron del sistema las pantallas de alta, edición y baja de permisos,
así que los tres códigos que las protegían se quedaron sin ninguna vista
detrás. Un permiso que no custodia nada sólo ensucia el catálogo y, peor,
confunde: aparece tildable en el modal de Perfiles como si habilitara algo.

Permisos que elimina:

    crear_permiso
    editar_permiso
    eliminar_permiso

Al borrarlos, PostgreSQL se lleva en cascada sus filas de PERFIL_PERMISO
(las tenía el Administrador). El catálogo queda en 16 permisos.

Por qué el catálogo ya no se administra desde la pantalla: cada código
existe porque una vista lo exige con ``@requiere_permiso``. Crear uno desde
el navegador produce una fila que nadie consulta, y renombrar un código
deja al decorador pidiendo algo que no está en la tabla — con lo cual esa
vista queda inaccesible para todos salvo el Administrador, que pasa por
diseño. El alta de un permiso nuevo es, y tiene que ser, una migración de
datos acompañando al decorador nuevo.

Se conserva ``ver_permisos``: el panel sigue existiendo para consultar el
catálogo, y la asignación a perfiles se hace desde la pantalla de Perfiles.
"""
from django.db import migrations


# (codigo, nombre, descripcion, modulo) — los datos completos para poder
# recrearlos si alguien revierte la migración.
PERMISOS_RETIRADOS = [
    ('crear_permiso',    'Crear permisos',    'Alta de permisos nuevos',                        'PERMISOS'),
    ('editar_permiso',   'Editar permisos',   'Modificar código, nombre, módulo o descripción', 'PERMISOS'),
    ('eliminar_permiso', 'Eliminar permisos', 'Borrar un permiso que no sea del sistema',       'PERMISOS'),
]


def retirar(apps, schema_editor):
    Permiso = apps.get_model('usuarios', 'Permiso')

    codigos = [codigo for codigo, _, _, _ in PERMISOS_RETIRADOS]
    borrados = Permiso.objects.filter(codigo__in=codigos).delete()[0]
    if borrados:
        print(f'  [-] {borrados} fila(s) eliminada(s): los permisos de '
              f'escritura del catálogo ya no tienen vista que proteger.')


def revertir(apps, schema_editor):
    """Recrea los tres permisos y se los devuelve al Administrador.

    No reconstruye las asignaciones que hubiera tenido cualquier otro
    perfil: esas filas se fueron en cascada y no quedó registro de ellas.
    """
    Perfil = apps.get_model('usuarios', 'Perfil')
    Permiso = apps.get_model('usuarios', 'Permiso')
    PerfilPermiso = apps.get_model('usuarios', 'PerfilPermiso')

    administrador = Perfil.objects.filter(nombre='Administrador').first()
    for codigo, nombre, descripcion, modulo in PERMISOS_RETIRADOS:
        permiso, _ = Permiso.objects.get_or_create(
            codigo=codigo,
            defaults={'nombre': nombre, 'descripcion': descripcion,
                      'modulo': modulo},
        )
        if administrador:
            PerfilPermiso.objects.get_or_create(
                perfil=administrador, permiso=permiso)


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0003_permisos_granulares'),
    ]

    operations = [
        migrations.RunPython(retirar, revertir),
    ]
