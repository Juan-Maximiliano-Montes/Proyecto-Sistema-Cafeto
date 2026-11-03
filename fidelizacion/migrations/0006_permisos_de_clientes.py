"""Los permisos de la pantalla de Clientes del club (DFD 9).

DD-39: el catálogo de permisos lo define el código y se carga por
migración, junto con la pantalla que lo usa; el Administrador recibe el
permiso nuevo en la misma operación.

El Operador y el Encargado registran e identifican clientes en el
mostrador (DFD 9 bloque A: "a cargo del Operador"); la baja y la
reactivación quedan para la Administradora, y también la exportación del
padrón: identificar a un cliente en el mostrador no es lo mismo que
llevarse la lista entera con sus DNI y teléfonos (Ley 25.326, art. 9:
seguridad y confidencialidad de los datos personales).
"""
from django.db import migrations

PERMISOS = [
    ('ver_clientes', 'Ver clientes',
     'Consultar los clientes del club, sus puntos y su tarjeta'),
    ('crear_cliente', 'Crear clientes', 'Registrar un cliente en el club'),
    ('editar_cliente', 'Editar clientes',
     'Modificar los datos de un cliente'),
    ('eliminar_cliente', 'Eliminar clientes',
     'Dar de baja lógica un cliente'),
    ('reactivar_cliente', 'Reactivar clientes',
     'Deshacer la baja lógica de un cliente'),
    ('exportar_clientes', 'Exportar clientes',
     'Bajar el padrón de clientes (con DNI y teléfono) a Excel o PDF'),
]
DEL_MOSTRADOR = ('ver_clientes', 'crear_cliente', 'editar_cliente')
POR_PERFIL = {
    'Administrador': [c for c, _, _ in PERMISOS],
    'Encargado': DEL_MOSTRADOR,
    'Operador': DEL_MOSTRADOR,
}


def cargar(apps, schema_editor):
    Permiso = apps.get_model('seguridad', 'Permiso')
    Perfil = apps.get_model('seguridad', 'Perfil')
    PerfilPermiso = apps.get_model('seguridad', 'PerfilPermiso')
    permisos = {}
    for codigo, nombre, descripcion in PERMISOS:
        permisos[codigo], _ = Permiso.objects.get_or_create(
            codigo=codigo,
            defaults={'nombre': nombre, 'descripcion': descripcion,
                      'modulo': 'FIDELIZACION'})
    for nombre_perfil, codigos in POR_PERFIL.items():
        perfil = Perfil.objects.filter(nombre=nombre_perfil).first()
        if perfil is None:
            continue
        for codigo in codigos:
            PerfilPermiso.objects.get_or_create(
                perfil=perfil, permiso=permisos[codigo])


def borrar(apps, schema_editor):
    Permiso = apps.get_model('seguridad', 'Permiso')
    PerfilPermiso = apps.get_model('seguridad', 'PerfilPermiso')
    codigos = [c for c, _, _ in PERMISOS]
    PerfilPermiso._base_manager.filter(permiso__codigo__in=codigos).delete()
    Permiso._base_manager.filter(codigo__in=codigos).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('fidelizacion', '0005_anulacion_por_tramos'),
        ('seguridad', '0003_datos_iniciales'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
