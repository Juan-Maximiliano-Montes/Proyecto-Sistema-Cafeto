"""Orden recibida con diferencias (DD-64).

La recepción contra una orden compara lo pedido con lo recibido: si no
coinciden, la orden queda CON_DIFERENCIAS hasta que llegue el resto o la
Administradora la cierre con lo acordado con el proveedor.
"""

import nucleo.restricciones
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0004_datos_iniciales"),
        ("seguridad", "0004_resultado_de_la_operacion"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="ordencompra",
            name="ck_ordenes_estado",
        ),
        migrations.AlterField(
            model_name="ordencompra",
            name="estado_orden",
            field=models.CharField(
                choices=[
                    ("PENDIENTE", "Pendiente"),
                    ("EMITIDA", "Emitida"),
                    ("CON_DIFERENCIAS", "Recibida con diferencias"),
                    ("RECEPCIONADA", "Recepcionada"),
                    ("CANCELADA", "Cancelada"),
                ],
                default="PENDIENTE",
                max_length=15,
                verbose_name="Estado",
            ),
        ),
        migrations.AddConstraint(
            model_name="ordencompra",
            constraint=nucleo.restricciones.CheckEnBase(
                condition=nucleo.restricciones.CheckSQL(
                    "estado_orden IN ('PENDIENTE','EMITIDA','CON_DIFERENCIAS','RECEPCIONADA','CANCELADA')"
                ),
                name="ck_ordenes_estado",
            ),
        ),
    ]
