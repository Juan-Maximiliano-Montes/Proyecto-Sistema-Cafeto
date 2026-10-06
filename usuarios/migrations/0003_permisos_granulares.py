"""Data Migration — permisos granulares por acción.

Hasta acá el módulo de Proveedores entero se abría con UN solo permiso
(``ver_proveedores``): quien podía mirar el listado también podía crear,
editar y dar de baja. Y en Usuarios, ``baja_usuario`` habilitaba a la vez
la baja y la reactivación. Esta migración parte esas acciones en permisos
independientes, para que un perfil pueda tener "ver" sin tener "eliminar".

Permisos que agrega:

    USUARIOS      reactivar_usuario   (antes iba pegado a baja_usuario)
    PROVEEDORES   crear_proveedor
                  editar_proveedor
                  eliminar_proveedor
                  reactivar_proveedor

Por qué no hay ``reactivar_perfil`` ni ``reactivar_permiso``: Perfil y
Permiso se borran FÍSICAMENTE (no tienen ``borrado_logico``), así que no
existe ninguna vista de reactivación que proteger. Un permiso sin vista
detrás sólo ensuciaría el catálogo.

Se mantiene la convención de códigos que ya usaba el sistema
(``crear_*`` / ``editar_*`` / ``eliminar_*`` / ``reactivar_*`` en singular,
``ver_*`` en plural), la misma que leen los decoradores
``@requiere_permiso`` de las vistas.

Asignación:
  * Administrador -> recibe TODOS los permisos nuevos.
  * Cajero        -> no recibe ninguno. Sigue en cero permisos, por menor
                     privilegio: lo que necesite se le tilda a mano desde
                     la pantalla de Perfiles.
"""
from django.db import migrations


# (codigo, nombre, descripcion, modulo)
PERMISOS_GRANULARES = [
    ('reactivar_usuario',   'Reactivar usuarios',   'Deshacer la baja lógica de un usuario',     'USUARIOS'),
    ('crear_proveedor',     'Crear proveedores',    'Alta de proveedores nuevos',                'PROVEEDORES'),
    ('editar_proveedor',    'Editar proveedores',   'Modificar los datos de un proveedor',       'PROVEEDORES'),
    ('eliminar_proveedor',  'Eliminar proveedores', 'Dar de baja lógica un proveedor',           'PROVEEDORES'),
    ('reactivar_proveedor', 'Reactivar proveedores', 'Deshacer la baja lógica de un proveedor',  'PROVEEDORES'),
]

# 'baja_usuario' dejaba de ser genérico: ahora sólo cubre la baja.
DESCRIPCION_BAJA_VIEJA = 'Dar de baja lógica y reactivar usuarios'
DESCRIPCION_BAJA_NUEVA = 'Dar de baja lógica un usuario'

# Idem 'ver_proveedores': ya no es la llave de todo el módulo.
DESCRIPCION_VER_PROV_NUEVA = 'Acceder al listado de proveedores en modo lectura'


def cargar(apps, schema_editor):
    Perfil = apps.get_model('usuarios', 'Perfil')
    Permiso = apps.get_model('usuarios', 'Permiso')
    PerfilPermiso = apps.get_model('usuarios', 'PerfilPermiso')

    administrador = Perfil.objects.filter(nombre='Administrador').first()

    for codigo, nombre, descripcion, modulo in PERMISOS_GRANULARES:
        permiso, _ = Permiso.objects.get_or_create(
            codigo=codigo,
            defaults={'nombre': nombre, 'descripcion': descripcion,
                      'modulo': modulo},
        )
        if administrador:
            PerfilPermiso.objects.get_or_create(
                perfil=administrador, permiso=permiso)

    # Al Cajero NO se le asigna nada: queda en cero permisos.

    # Las descripciones de los dos permisos que dejaron de ser genéricos
    Permiso.objects.filter(
        codigo='baja_usuario', descripcion=DESCRIPCION_BAJA_VIEJA
    ).update(descripcion=DESCRIPCION_BAJA_NUEVA)
    Permiso.objects.filter(codigo='ver_proveedores').update(
        descripcion=DESCRIPCION_VER_PROV_NUEVA)


def revertir(apps, schema_editor):
    Permiso = apps.get_model('usuarios', 'Permiso')
    # Al borrar el permiso, PostgreSQL se lleva en cascada sus filas de
    # perfil_permiso.
    Permiso.objects.filter(
        codigo__in=[codigo for codigo, _, _, _ in PERMISOS_GRANULARES]
    ).delete()
    Permiso.objects.filter(codigo='baja_usuario').update(
        descripcion=DESCRIPCION_BAJA_VIEJA)


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0002_datos_iniciales_v2'),
    ]

    operations = [
        migrations.RunPython(cargar, revertir),
    ]
