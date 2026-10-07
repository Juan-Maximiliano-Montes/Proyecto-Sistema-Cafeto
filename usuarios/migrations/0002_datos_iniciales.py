"""Datos de puesta en marcha de seguridad: permisos, perfiles y el admin.

Son los datos sin los que el sistema no se puede usar: sin permisos
ninguna vista deja pasar, sin perfil no hay a quién asignarlos y sin el
usuario inicial nadie puede entrar a cargar el resto.

El catálogo de PERMISOS es el que consultan los @requiere_permiso de
las vistas y el exige() de maestros/crud.py: cada código existe
porque hay una vista que lo pide. Se carga completo, con su módulo, para
que la matriz de la pantalla de Perfiles diga la verdad.

Los PERFILES siguen el Principio de Menor Privilegio:

    Administrador  -> el catálogo entero, y es_sistema=True
    Cajero         -> ninguno; se le tilda lo que necesite desde la
                      pantalla, permiso por permiso

es_sistema es la marca que protege al perfil administrador: le da
acceso total y lo vuelve imposible de renombrar o dar de baja. Antes eso
se deducía de que el perfil se llamara "Administrador", un texto que el
propio administrador puede editar — ver Perfil.es_sistema.

El usuario inicial nace con debe_cambiar_clave=True: la primera vez
que entre, el middleware lo manda a poner una clave propia.

    DNI:   30000000
    Clave: cafeto2025

Las altas son IDEMPOTENTES (get_or_create por la clave natural), así
que volver a aplicar la migración no duplica nada.
"""
from django.contrib.auth.hashers import make_password
from django.db import migrations

CLAVE_POR_DEFECTO = 'cafeto2025'
DNI_ADMINISTRADOR = '30000000'

# (codigo, nombre, descripcion, modulo)
PERMISOS = [
    ('ver_usuarios', 'Ver usuarios',
     'Acceder al directorio general de usuarios', 'USUARIOS'),
    ('crear_usuario', 'Crear usuarios',
     'Alta de usuarios nuevos', 'USUARIOS'),
    ('editar_usuario', 'Editar usuarios',
     'Modificar el correo de un usuario', 'USUARIOS'),
    ('baja_usuario', 'Baja de usuarios',
     'Dar de baja lógica un usuario', 'USUARIOS'),
    ('restablecer_clave', 'Restablecer claves',
     'Generar una clave provisoria nueva', 'USUARIOS'),
    ('ver_perfiles', 'Ver perfiles',
     'Acceder al CRUD de perfiles', 'PERFILES'),
    ('crear_perfil', 'Crear perfiles',
     'Alta de perfiles nuevos', 'PERFILES'),
    ('editar_perfil', 'Editar perfiles',
     'Modificar un perfil y sus permisos', 'PERFILES'),
    ('eliminar_perfil', 'Eliminar perfiles',
     'Borrar un perfil que no tenga usuarios', 'PERFILES'),
    ('ver_permisos', 'Ver permisos',
     'Acceder al CRUD de permisos', 'PERMISOS'),
    ('ver_proveedores', 'Ver proveedores',
     'Acceder al listado de proveedores en modo lectura', 'PROVEEDORES'),
    ('reactivar_usuario', 'Reactivar usuarios',
     'Deshacer la baja lógica de un usuario', 'USUARIOS'),
    ('crear_proveedor', 'Crear proveedores',
     'Alta de proveedores nuevos', 'PROVEEDORES'),
    ('editar_proveedor', 'Editar proveedores',
     'Modificar los datos de un proveedor', 'PROVEEDORES'),
    ('eliminar_proveedor', 'Eliminar proveedores',
     'Dar de baja lógica un proveedor', 'PROVEEDORES'),
    ('reactivar_proveedor', 'Reactivar proveedores',
     'Deshacer la baja lógica de un proveedor', 'PROVEEDORES'),
    ('reactivar_perfil', 'Reactivar perfiles',
     'Deshacer la baja lógica de un perfil', 'PERFILES'),
    ('ver_rubros', 'Ver rubros',
     'Acceder al listado de rubros en modo lectura', 'DATOS_MAESTROS'),
    ('crear_rubro', 'Crear rubros',
     'Dar de alta rubros', 'DATOS_MAESTROS'),
    ('editar_rubro', 'Editar rubros',
     'Modificar los datos de un/a rubro', 'DATOS_MAESTROS'),
    ('eliminar_rubro', 'Eliminar rubros',
     'Dar de baja lógica un/a rubro', 'DATOS_MAESTROS'),
    ('reactivar_rubro', 'Reactivar rubros',
     'Deshacer la baja lógica de un/a rubro', 'DATOS_MAESTROS'),
    ('ver_marcas', 'Ver marcas',
     'Acceder al listado de marcas en modo lectura', 'DATOS_MAESTROS'),
    ('crear_marca', 'Crear marcas',
     'Dar de alta marcas', 'DATOS_MAESTROS'),
    ('editar_marca', 'Editar marcas',
     'Modificar los datos de un/a marca', 'DATOS_MAESTROS'),
    ('eliminar_marca', 'Eliminar marcas',
     'Dar de baja lógica un/a marca', 'DATOS_MAESTROS'),
    ('reactivar_marca', 'Reactivar marcas',
     'Deshacer la baja lógica de un/a marca', 'DATOS_MAESTROS'),
    ('ver_productos', 'Ver productos',
     'Acceder al listado de productos en modo lectura', 'DATOS_MAESTROS'),
    ('crear_producto', 'Crear productos',
     'Dar de alta productos', 'DATOS_MAESTROS'),
    ('editar_producto', 'Editar productos',
     'Modificar los datos de un/a producto', 'DATOS_MAESTROS'),
    ('eliminar_producto', 'Eliminar productos',
     'Dar de baja lógica un/a producto', 'DATOS_MAESTROS'),
    ('reactivar_producto', 'Reactivar productos',
     'Deshacer la baja lógica de un/a producto', 'DATOS_MAESTROS'),
    ('ver_tipos_egreso', 'Ver tipos de egreso',
     'Acceder al listado de tipos de egreso en modo lectura', 'DATOS_MAESTROS'),
    ('crear_tipo_egreso', 'Crear tipos de egreso',
     'Dar de alta tipos de egreso', 'DATOS_MAESTROS'),
    ('editar_tipo_egreso', 'Editar tipos de egreso',
     'Modificar los datos de un/a tipo de egreso', 'DATOS_MAESTROS'),
    ('eliminar_tipo_egreso', 'Eliminar tipos de egreso',
     'Dar de baja lógica un/a tipo de egreso', 'DATOS_MAESTROS'),
    ('reactivar_tipo_egreso', 'Reactivar tipos de egreso',
     'Deshacer la baja lógica de un/a tipo de egreso', 'DATOS_MAESTROS'),
    ('ver_medios_pago', 'Ver medios de pago',
     'Acceder al listado de medios de pago en modo lectura', 'DATOS_MAESTROS'),
    ('crear_medio_pago', 'Crear medios de pago',
     'Dar de alta medios de pago', 'DATOS_MAESTROS'),
    ('editar_medio_pago', 'Editar medios de pago',
     'Modificar los datos de un/a medio de pago', 'DATOS_MAESTROS'),
    ('eliminar_medio_pago', 'Eliminar medios de pago',
     'Dar de baja lógica un/a medio de pago', 'DATOS_MAESTROS'),
    ('reactivar_medio_pago', 'Reactivar medios de pago',
     'Deshacer la baja lógica de un/a medio de pago', 'DATOS_MAESTROS'),
    ('ver_cajas_registradoras', 'Ver cajas registradoras',
     'Acceder al listado de cajas registradoras en modo lectura', 'DATOS_MAESTROS'),
    ('crear_caja_registradora', 'Crear cajas registradoras',
     'Dar de alta cajas registradoras', 'DATOS_MAESTROS'),
    ('editar_caja_registradora', 'Editar cajas registradoras',
     'Modificar los datos de un/a caja registradora', 'DATOS_MAESTROS'),
    ('eliminar_caja_registradora', 'Eliminar cajas registradoras',
     'Dar de baja lógica un/a caja registradora', 'DATOS_MAESTROS'),
    ('reactivar_caja_registradora', 'Reactivar cajas registradoras',
     'Deshacer la baja lógica de un/a caja registradora', 'DATOS_MAESTROS'),
    ('ver_categorias', 'Ver categorías de fidelización',
     'Acceder al listado de categorías de fidelización en modo lectura', 'DATOS_MAESTROS'),
    ('crear_categoria', 'Crear categorías de fidelización',
     'Dar de alta categorías de fidelización', 'DATOS_MAESTROS'),
    ('editar_categoria', 'Editar categorías de fidelización',
     'Modificar los datos de un/a categoría de fidelización', 'DATOS_MAESTROS'),
    ('eliminar_categoria', 'Eliminar categorías de fidelización',
     'Dar de baja lógica un/a categoría de fidelización', 'DATOS_MAESTROS'),
    ('reactivar_categoria', 'Reactivar categorías de fidelización',
     'Deshacer la baja lógica de un/a categoría de fidelización', 'DATOS_MAESTROS'),
    ('ver_beneficios', 'Ver beneficios',
     'Acceder al listado de beneficios en modo lectura', 'DATOS_MAESTROS'),
    ('crear_beneficio', 'Crear beneficios',
     'Dar de alta beneficios', 'DATOS_MAESTROS'),
    ('editar_beneficio', 'Editar beneficios',
     'Modificar los datos de un/a beneficio', 'DATOS_MAESTROS'),
    ('eliminar_beneficio', 'Eliminar beneficios',
     'Dar de baja lógica un/a beneficio', 'DATOS_MAESTROS'),
    ('reactivar_beneficio', 'Reactivar beneficios',
     'Deshacer la baja lógica de un/a beneficio', 'DATOS_MAESTROS'),
    ('ver_provincias', 'Ver provincias',
     'Acceder al listado de provincias en modo lectura', 'UBICACIONES'),
    ('crear_provincia', 'Crear provincias',
     'Dar de alta provincias', 'UBICACIONES'),
    ('editar_provincia', 'Editar provincias',
     'Modificar los datos de un/a provincia', 'UBICACIONES'),
    ('ver_localidades', 'Ver localidades',
     'Acceder al listado de localidades en modo lectura', 'UBICACIONES'),
    ('crear_localidad', 'Crear localidades',
     'Dar de alta localidades', 'UBICACIONES'),
    ('editar_localidad', 'Editar localidades',
     'Modificar los datos de un/a localidad', 'UBICACIONES'),
    ('ver_auditoria', 'Ver el registro de actividad',
     'Acceder al Registro de Actividad (auditoría) en modo lectura', 'AUDITORIA'),
]

PERFIL_DE_SISTEMA = 'Administrador'

# (nombre, descripcion, es_sistema)
PERFILES = [
    ('Administrador', 'Acceso total al sistema', True),
    ('Cajero', 'Operación diaria del local', False),
]


def cargar(apps, schema_editor):
    Perfil = apps.get_model('usuarios', 'Perfil')
    Permiso = apps.get_model('usuarios', 'Permiso')
    PerfilPermiso = apps.get_model('usuarios', 'PerfilPermiso')
    Usuario = apps.get_model('usuarios', 'Usuario')

    # 1) El catálogo de permisos ----------------------------------------
    permisos = []
    for codigo, nombre, descripcion, modulo in PERMISOS:
        permiso, _ = Permiso.objects.get_or_create(
            codigo=codigo,
            defaults={'nombre': nombre, 'descripcion': descripcion,
                      'modulo': modulo})
        permisos.append(permiso)

    # 2) Los perfiles base ----------------------------------------------
    perfiles = {}
    for nombre, descripcion, es_sistema in PERFILES:
        perfiles[nombre], _ = Perfil.objects.get_or_create(
            nombre=nombre,
            defaults={'descripcion': descripcion, 'es_sistema': es_sistema})

    # El perfil de sistema lleva el catálogo COMPLETO. Además
    # Usuario.tiene_permiso() lo deja pasar siempre por la marca, pero
    # la matriz de la pantalla tiene que mostrar la verdad.
    administrador = perfiles[PERFIL_DE_SISTEMA]
    for permiso in permisos:
        PerfilPermiso.objects.get_or_create(
            perfil=administrador, permiso=permiso)

    # 3) El usuario administrador inicial -------------------------------
    Usuario.objects.get_or_create(
        dni_usuario=DNI_ADMINISTRADOR,
        defaults={
            'apellido': 'Montes',
            'nombre': 'Juan',
            'correo': 'admin@cafeto.com',
            'id_perfil': administrador,
            'nombre_usuario': 'montesj',
            'clave': make_password(CLAVE_POR_DEFECTO),
            'debe_cambiar_clave': True,
        })


def borrar(apps, schema_editor):
    """Deshace la carga.

    Va contra _base_manager y con un DELETE directo: en operación
    normal el mixin SinBajaFisica prohíbe el borrado físico de estas
    tablas, pero al revertir una migración lo que se quiere es dejar la
    base como estaba.
    """
    for modelo in ('Usuario', 'PerfilPermiso', 'Perfil', 'Permiso'):
        apps.get_model('usuarios', modelo)._base_manager.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
