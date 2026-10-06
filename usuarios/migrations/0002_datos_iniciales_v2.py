"""Carga inicial v2 — catálogo de permisos clasificado por módulo, perfiles
base y el usuario Administrador del sistema.

Reemplaza a la vieja cadena 0002..0010, que había quedado como un parche
sobre otro: permisos con dos convenciones de nombres conviviendo, un perfil
"Mozo" que después se borraba, descripciones con notas de implementación.
Acá se carga UNA sola vez lo que el sistema tiene que tener, y nada más.

Qué crea, en orden:

  1. El catálogo de PERMISOS, cada uno con su ``modulo`` (Usuarios,
     Perfiles, Permisos, Proveedores). Son los mismos códigos que usan
     los decoradores ``@requiere_permiso`` de las vistas.
  2. Los PERFILES base, siguiendo el Principio de Menor Privilegio:
        Administrador -> todos los permisos
        Cajero        -> ninguno de seguridad; ni siquiera proveedores
  3. El usuario administrador inicial, con la clave por defecto y
     ``debe_cambiar_clave = True``: la primera vez que entre, el
     interceptor del middleware lo obliga a poner una clave propia.

        DNI:   30000000
        Clave: cafeto2025
"""
from django.contrib.auth.hashers import make_password
from django.db import migrations


CLAVE_POR_DEFECTO = 'cafeto2025'
DNI_ADMINISTRADOR = '30000000'

# (codigo, nombre, descripcion, modulo)
PERMISOS = [
    # --- Módulo Usuarios ---
    ('ver_usuarios',     'Ver usuarios',      'Acceder al directorio general de usuarios', 'USUARIOS'),
    ('crear_usuario',    'Crear usuarios',    'Alta de usuarios nuevos',                   'USUARIOS'),
    ('editar_usuario',   'Editar usuarios',   'Modificar el correo de un usuario',         'USUARIOS'),
    ('baja_usuario',     'Baja de usuarios',  'Dar de baja lógica y reactivar usuarios',   'USUARIOS'),
    ('restablecer_clave', 'Restablecer claves', 'Generar una clave provisoria nueva',      'USUARIOS'),

    # --- Módulo Perfiles ---
    ('ver_perfiles',     'Ver perfiles',      'Acceder al CRUD de perfiles',               'PERFILES'),
    ('crear_perfil',     'Crear perfiles',    'Alta de perfiles nuevos',                   'PERFILES'),
    ('editar_perfil',    'Editar perfiles',   'Modificar un perfil y sus permisos',        'PERFILES'),
    ('eliminar_perfil',  'Eliminar perfiles', 'Borrar un perfil que no tenga usuarios',    'PERFILES'),

    # --- Módulo Permisos ---
    ('ver_permisos',     'Ver permisos',      'Acceder al CRUD de permisos',               'PERMISOS'),
    ('crear_permiso',    'Crear permisos',    'Alta de permisos nuevos',                   'PERMISOS'),
    ('editar_permiso',   'Editar permisos',   'Modificar código, nombre, módulo o descripción', 'PERMISOS'),
    ('eliminar_permiso', 'Eliminar permisos', 'Borrar un permiso que no sea del sistema',  'PERMISOS'),

    # --- Módulo Proveedores ---
    ('ver_proveedores',  'Ver proveedores',   'Acceder al módulo de proveedores',          'PROVEEDORES'),
]

PERFILES = [
    ('Administrador', 'Acceso total al sistema'),
    ('Cajero',        'Operación diaria del local'),
]


def cargar_datos(apps, schema_editor):
    Perfil = apps.get_model('usuarios', 'Perfil')
    Permiso = apps.get_model('usuarios', 'Permiso')
    PerfilPermiso = apps.get_model('usuarios', 'PerfilPermiso')
    Usuario = apps.get_model('usuarios', 'Usuario')

    # 1) Catálogo de permisos, clasificado por módulo ---------------------
    permisos = {}
    for codigo, nombre, descripcion, modulo in PERMISOS:
        permisos[codigo] = Permiso.objects.create(
            codigo=codigo, nombre=nombre, descripcion=descripcion,
            modulo=modulo)

    # 2) Perfiles base ---------------------------------------------------
    perfiles = {}
    for nombre, descripcion in PERFILES:
        perfiles[nombre] = Perfil.objects.create(
            nombre=nombre, descripcion=descripcion)

    # El Administrador tiene TODOS los permisos cargados explícitamente
    # (además, Usuario.tiene_permiso() lo deja pasar por diseño).
    for permiso in permisos.values():
        PerfilPermiso.objects.create(
            perfil=perfiles['Administrador'], permiso=permiso)

    # El Cajero arranca SIN permisos: menor privilegio. Lo que necesite se
    # le tilda desde la pantalla de Perfiles, permiso por permiso.

    # 3) Usuario administrador inicial -----------------------------------
    Usuario.objects.create(
        dni_usuario=DNI_ADMINISTRADOR,
        apellido='Montes',
        nombre='Juan',
        correo='admin@cafeto.com',
        id_perfil=perfiles['Administrador'],
        nombre_usuario='montesj',
        clave=make_password(CLAVE_POR_DEFECTO),
        borrado_logico=False,
        debe_cambiar_clave=True,
    )


def borrar_datos(apps, schema_editor):
    apps.get_model('usuarios', 'Usuario').objects.filter(
        dni_usuario=DNI_ADMINISTRADOR).delete()
    apps.get_model('usuarios', 'PerfilPermiso').objects.all().delete()
    apps.get_model('usuarios', 'Perfil').objects.all().delete()
    apps.get_model('usuarios', 'Permiso').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(cargar_datos, borrar_datos),
    ]
