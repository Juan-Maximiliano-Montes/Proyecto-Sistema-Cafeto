"""Permisos granulares de "Mantener datos maestros" (DFD 10).

Un permiso por ACCIÓN y por entidad, no uno por módulo: así un perfil
puede tener "ver rubros" sin poder crearlos, que es exactamente lo que
pide el Estudio Previo para el perfil Operador.

Convención de códigos, la misma que ya usaba el sistema y que leen los
decoradores ``@requiere_permiso``:

    ver_<entidades>      (plural)    abre el listado en modo lectura
    crear_<entidad>      (singular)  alta
    editar_<entidad>     (singular)  modificación
    eliminar_<entidad>   (singular)  baja lógica
    reactivar_<entidad>  (singular)  deshacer la baja lógica

PROVINCIAS y LOCALIDADES no llevan ``eliminar`` ni ``reactivar``: son un
catálogo geográfico sin baja lógica (una provincia no deja de existir).
Un permiso sin vista detrás sólo ensuciaría el catálogo.

Asignación: TODOS van al Administrador. El Operador no recibe ninguno —
por menor privilegio: lo que necesite se le tilda a mano desde la
pantalla de Perfiles.
"""
from django.db import migrations


# (entidad_singular, entidad_plural, etiqueta_singular, etiqueta_plural,
#  modulo, tiene_baja)
ENTIDADES = [
    ('rubro', 'rubros', 'rubro', 'rubros', 'DATOS_MAESTROS', True),
    ('marca', 'marcas', 'marca', 'marcas', 'DATOS_MAESTROS', True),
    ('producto', 'productos', 'producto', 'productos', 'DATOS_MAESTROS', True),
    ('tipo_egreso', 'tipos_egreso', 'tipo de egreso', 'tipos de egreso',
     'DATOS_MAESTROS', True),
    ('medio_pago', 'medios_pago', 'medio de pago', 'medios de pago',
     'DATOS_MAESTROS', True),
    ('caja_registradora', 'cajas_registradoras', 'caja registradora',
     'cajas registradoras', 'DATOS_MAESTROS', True),
    ('categoria', 'categorias', 'categoría de fidelización',
     'categorías de fidelización', 'DATOS_MAESTROS', True),
    ('beneficio', 'beneficios', 'beneficio', 'beneficios',
     'DATOS_MAESTROS', True),
    ('provincia', 'provincias', 'provincia', 'provincias',
     'UBICACIONES', False),
    ('localidad', 'localidades', 'localidad', 'localidades',
     'UBICACIONES', False),
]


def catalogo():
    """Arma la lista (codigo, nombre, descripcion, modulo) de los permisos."""
    permisos = []
    for singular, plural, etiqueta, etiquetas, modulo, tiene_baja in ENTIDADES:
        permisos.append((
            f'ver_{plural}',
            f'Ver {etiquetas}'[:50],
            f'Acceder al listado de {etiquetas} en modo lectura'[:120],
            modulo))
        permisos.append((
            f'crear_{singular}',
            f'Crear {etiquetas}'[:50],
            f'Dar de alta {etiquetas}'[:120],
            modulo))
        permisos.append((
            f'editar_{singular}',
            f'Editar {etiquetas}'[:50],
            f'Modificar los datos de un/a {etiqueta}'[:120],
            modulo))
        if tiene_baja:
            permisos.append((
                f'eliminar_{singular}',
                f'Eliminar {etiquetas}'[:50],
                f'Dar de baja lógica un/a {etiqueta}'[:120],
                modulo))
            permisos.append((
                f'reactivar_{singular}',
                f'Reactivar {etiquetas}'[:50],
                f'Deshacer la baja lógica de un/a {etiqueta}'[:120],
                modulo))
    return permisos


def cargar(apps, schema_editor):
    Perfil = apps.get_model('usuarios', 'Perfil')
    Permiso = apps.get_model('usuarios', 'Permiso')
    PerfilPermiso = apps.get_model('usuarios', 'PerfilPermiso')

    administrador = Perfil.objects.filter(nombre='Administrador').first()

    for codigo, nombre, descripcion, modulo in catalogo():
        permiso, _ = Permiso.objects.get_or_create(
            codigo=codigo,
            defaults={'nombre': nombre, 'descripcion': descripcion,
                      'modulo': modulo})
        if administrador is not None:
            PerfilPermiso.objects.get_or_create(
                perfil=administrador, permiso=permiso)


def borrar(apps, schema_editor):
    Permiso = apps.get_model('usuarios', 'Permiso')
    codigos = [codigo for codigo, _, _, _ in catalogo()]
    with schema_editor.connection.cursor() as cursor:
        # trg_sin_baja_fisica prohíbe el DELETE en PERMISOS durante la
        # operación normal; al revertir la migración sí corresponde.
        cursor.execute("SET LOCAL cafeto.pasaje_historico = 'on'")
        Permiso.objects.filter(codigo__in=codigos).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('maestros', '0002_datos_iniciales'),
        ('usuarios', '0006_alinear_con_el_der'),
    ]

    operations = [
        migrations.RunPython(cargar, reverse_code=borrar),
    ]
