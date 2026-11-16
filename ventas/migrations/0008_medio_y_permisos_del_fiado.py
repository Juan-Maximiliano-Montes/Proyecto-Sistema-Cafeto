"""El medio reservado «Cuenta corriente» y los dos permisos del fiado (DD-91).

* ``cobrar_credito``: ver las cuentas corrientes y cobrarlas. Lo tienen los
  tres perfiles: quien está en la caja tiene que poder cobrarle al que viene
  a pagar.
* ``gestionar_credito``: habilitar a un cliente, ponerle tope, suspenderlo,
  quitarle puntos y dar una deuda por perdida. Sólo la Administración (y la
  titular, que pasa siempre).
"""
from django.db import migrations

MEDIO = 'Cuenta corriente'
PERMISOS = [
    ('cobrar_credito', 'Cobrar cuentas corrientes',
     'Ver lo que debe cada cliente y registrar sus pagos', ('Administrador', 'Encargado', 'Operador')),
    ('gestionar_credito', 'Gestionar el fiado',
     'Habilitar o suspender a un cliente, ponerle tope, quitarle puntos y dar una deuda por perdida',
     ('Administrador',)),
]


def cargar(apps, schema_editor):
    MedioPago = apps.get_model('ventas', 'MedioPago')
    medio = MedioPago._base_manager.filter(nombre_medio_pago__iexact=MEDIO).first()
    if medio is None:
        MedioPago._base_manager.create(nombre_medio_pago=MEDIO, recargo_porcentaje=0, afecta_caja=False)
    else:
        medio.nombre_medio_pago, medio.recargo_porcentaje, medio.afecta_caja = MEDIO, 0, False
        medio.es_tarjeta_credito, medio.borrado_logico = False, False
        medio.save()
    Permiso = apps.get_model('seguridad', 'Permiso')
    Perfil = apps.get_model('seguridad', 'Perfil')
    PerfilPermiso = apps.get_model('seguridad', 'PerfilPermiso')
    for codigo, nombre, descripcion, perfiles in PERMISOS:
        permiso, _ = Permiso.objects.get_or_create(
            codigo=codigo, defaults={'nombre': nombre, 'descripcion': descripcion, 'modulo': 'FIDELIZACION'})
        for perfil in Perfil.objects.filter(nombre__in=perfiles):
            PerfilPermiso.objects.get_or_create(perfil=perfil, permiso=permiso)


def borrar(apps, schema_editor):
    for codigo, *_ in PERMISOS:
        schema_editor.execute(
            'DELETE FROM perfiles_permisos WHERE id_permiso IN '
            '(SELECT id_permiso FROM permisos WHERE codigo_permiso = %s)', [codigo])
        schema_editor.execute('DELETE FROM permisos WHERE codigo_permiso = %s', [codigo])


class Migration(migrations.Migration):

    dependencies = [
        ('ventas', '0007_cobro_de_cuenta_corriente'),
        ('seguridad', '0004_resultado_de_la_operacion'),
        ('fidelizacion', '0008_cuenta_corriente'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
