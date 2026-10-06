"""Reinstala los triggers, el histórico y los procedimientos.

MARCAS es una maestra más: le corresponden el sello de modificación de
DD-17 (``trg_sello_modificacion``) y la prohibición de borrado físico de
RN-8.1.1 (``trg_sin_baja_fisica``). Los dos se crean en
``02_triggers.sql``, pero ese guion ya se había corrido en la migración
0003, y ``CREATE TRIGGER`` no se puede repetir sobre un trigger existente.

Así que se vuelve a instalar la lógica completa: se borra lo que instalan
los tres guiones y se los ejecuta de nuevo. Las tablas no se tocan.

De acá en adelante, cada vez que el DER sume una tabla con baja lógica
alcanza con agregar otra migración que llame a ``instalar_logica``.
"""
from django.db import migrations

from operaciones.esquema import desinstalar_logica, instalar_logica


def reinstalar(apps, schema_editor):
    # Historica: los guiones que lee son los de HOY, y esta migracion
    # corre en su punto del historial. Lo que nombre algo que el DER
    # renombro despues se saltea; la migracion que introdujo ese
    # renombre reinstala todo, y esa si corre en modo estricto.
    instalar_logica(schema_editor, tolerante=True)


def desinstalar(apps, schema_editor):
    desinstalar_logica(schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0006_resincronizar_con_el_esquema'),
    ]

    operations = [
        migrations.RunPython(reinstalar, reverse_code=desinstalar),
    ]
