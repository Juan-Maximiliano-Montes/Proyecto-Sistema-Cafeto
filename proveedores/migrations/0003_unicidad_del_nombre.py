"""La razón social del proveedor, única por clave normalizada (DD-50).

PROVEEDORES era la única maestra sin esta regla: tenía el UNIQUE del CUIT
—la identidad fiscal— y nada sobre el nombre. Alcanzaba para que dos
altas del mismo proveedor con el CUIT mal tipeado entraran las dos, y
quedaran "Arcor SA" y "arcor sa" compitiendo en el combo de productos y
en el de compras.

Se usa ``ClaveNormalizada`` (``f_clave_normalizada``: minúsculas, sin
acentos y con los espacios internos colapsados) y no ``Lower``, por dos
razones: es la misma función que usan las otras diez maestras, así la
regla es una sola en todo el sistema; y además de las mayúsculas atrapa
los acentos y los espacios de más, que ``Lower`` dejaría pasar
("Café del Valle" vs. "cafe del valle").

Antes de crear el índice se revisa que no haya duplicados, porque si los
hay PostgreSQL corta con un IntegrityError que no dice cuáles son. No se
arreglan solos a propósito: elegir qué proveedor sobrevive y a cuál se le
cambia el nombre es una decisión del negocio, no de una migración.
"""
from collections import defaultdict

import cafeto.restricciones
from django.db import migrations, models


def exigir_que_no_haya_duplicados(apps, schema_editor):
    """Aborta con la lista de choques, en vez de dejar que explote el índice."""
    from cafeto.validaciones import clave_normalizada

    Proveedor = apps.get_model('proveedores', 'Proveedor')

    por_clave = defaultdict(list)
    for proveedor in Proveedor.objects.all().only(
            'id_proveedor', 'nombre_proveedor', 'cuit_proveedor'):
        por_clave[clave_normalizada(proveedor.nombre_proveedor)].append(
            proveedor)

    choques = {clave: filas for clave, filas in por_clave.items()
               if len(filas) > 1}
    if not choques:
        return

    detalle = []
    for filas in choques.values():
        detalle.append('  · ' + '  |  '.join(
            '%s (id %s, CUIT %s)' % (f.nombre_proveedor, f.id_proveedor,
                                     f.cuit_proveedor)
            for f in filas))

    raise RuntimeError(
        'Hay proveedores cuya razón social es la misma al normalizarla '
        '(no se distinguen mayúsculas, acentos ni espacios de más), así '
        'que el índice único no se puede crear:\n'
        + '\n'.join(detalle)
        + '\n\nResolvelos antes de migrar: dejá uno y cambiale el nombre o '
          'dale de baja al otro desde la pantalla de Proveedores.')


def sin_vuelta_atras(apps, schema_editor):
    """Al revertir no hay nada que deshacer: el chequeo no escribió nada."""


class Migration(migrations.Migration):

    dependencies = [
        ('proveedores', '0002_datos_iniciales'),
    ]

    operations = [
        migrations.RunPython(exigir_que_no_haya_duplicados, sin_vuelta_atras),
        migrations.AddConstraint(
            model_name='proveedor',
            constraint=models.UniqueConstraint(
                cafeto.restricciones.ClaveNormalizada('nombre_proveedor'),
                name='uq_proveedores_nombre_norm'),
        ),
    ]
