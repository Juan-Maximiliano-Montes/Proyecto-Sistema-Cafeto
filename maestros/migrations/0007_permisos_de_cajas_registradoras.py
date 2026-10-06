"""Renombra los cinco permisos de la pantalla, sin perder las asignaciones.

Los códigos viejos (``ver_puestos_caja``, ``crear_puesto_caja``, …) ya están
tildados en los perfiles. Borrarlos y crearlos de nuevo con el nombre nuevo
borraría esas asignaciones de ``PERFILES_PERMISOS`` por la acción en cascada,
y el Administrador se quedaría sin acceso a la pantalla hasta que alguien
volviera a tildarla a mano.

Así que se renombra el ``codigo`` de la fila existente: la clave primaria no
cambia, y los perfiles siguen apuntando al mismo permiso. El catálogo de la
migración 0003 ya quedó escrito con los nombres nuevos, por lo que en una
base recién creada esta migración no encuentra nada que renombrar y no hace
nada.
"""
from django.db import migrations


RENOMBRES = [
    ('ver_puestos_caja',       'ver_cajas_registradoras'),
    ('crear_puesto_caja',      'crear_caja_registradora'),
    ('editar_puesto_caja',     'editar_caja_registradora'),
    ('eliminar_puesto_caja',   'eliminar_caja_registradora'),
    ('reactivar_puesto_caja',  'reactivar_caja_registradora'),
]

# Las descripciones las arma la 0003 con la etiqueta de la entidad, así que
# acá se reescriben con la misma redacción que generaría ese catálogo.
DESCRIPCIONES = {
    'ver_cajas_registradoras':
        'Acceder al listado de cajas registradoras en modo lectura',
    'crear_caja_registradora':
        'Dar de alta cajas registradoras',
    'editar_caja_registradora':
        'Modificar los datos de un/a caja registradora',
    'eliminar_caja_registradora':
        'Dar de baja lógica un/a caja registradora',
    'reactivar_caja_registradora':
        'Deshacer la baja lógica de un/a caja registradora',
}


def _renombrar(apps, schema_editor, pares):
    Permiso = apps.get_model('usuarios', 'Permiso')
    for viejo, nuevo in pares:
        if Permiso.objects.filter(codigo=nuevo).exists():
            continue
        fila = Permiso.objects.filter(codigo=viejo).first()
        if fila is None:
            continue
        fila.codigo = nuevo
        if nuevo in DESCRIPCIONES:
            fila.descripcion = DESCRIPCIONES[nuevo]
        fila.save()


def renombrar(apps, schema_editor):
    _renombrar(apps, schema_editor, RENOMBRES)


def deshacer(apps, schema_editor):
    _renombrar(apps, schema_editor,
               [(nuevo, viejo) for viejo, nuevo in RENOMBRES])


class Migration(migrations.Migration):

    dependencies = [
        ('maestros', '0006_cajas_registradoras'),
        ('usuarios', '0008_permiso_reactivar_perfil'),
    ]

    operations = [
        migrations.RunPython(renombrar, reverse_code=deshacer),
    ]
