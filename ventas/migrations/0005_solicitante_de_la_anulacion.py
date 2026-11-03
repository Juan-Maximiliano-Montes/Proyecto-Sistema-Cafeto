"""Quién pidió la anulación (DD-61).

La anulación suma ``id_usuario_solicitante``: el cajero la pide desde
Cambios y la autoriza otra persona con su usuario y su clave, ahí mismo;
o la hace directamente el Encargado o la Administradora. Las anulaciones
que ya existían las pidió y las autorizó la misma persona, así que se
completan con ese usuario antes de exigir el dato.
"""

import django.db.models.deletion
import nucleo.restricciones
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fidelizacion", "0006_permisos_de_clientes"),
        ("seguridad", "0003_datos_iniciales"),
        ("ventas", "0004_permiso_anular_venta"),
        ("nucleo", "0002_historico_y_vistas"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="venta",
            name="ck_ventas_anulacion",
        ),
        migrations.AddField(
            model_name="venta",
            name="id_usuario_solicitante",
            field=models.ForeignKey(
                blank=True,
                db_column="id_usuario_solicitante",
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="anulaciones_pedidas",
                to="seguridad.usuario",
                verbose_name="Pidió la anulación",
            ),
        ),
        migrations.RunSQL(
            """
            UPDATE ventas SET id_usuario_solicitante = id_usuario_anulacion
             WHERE estado_venta = 'ANULADA' AND id_usuario_solicitante IS NULL;
            ALTER TABLE hist_ventas
                ADD COLUMN IF NOT EXISTS id_usuario_solicitante integer;
            UPDATE hist_ventas SET id_usuario_solicitante = id_usuario_anulacion
             WHERE estado_venta = 'ANULADA' AND id_usuario_solicitante IS NULL;
            """,
            "ALTER TABLE hist_ventas DROP COLUMN IF EXISTS id_usuario_solicitante;"),
        migrations.AddConstraint(
            model_name="venta",
            constraint=nucleo.restricciones.CheckEnBase(
                condition=nucleo.restricciones.CheckSQL(
                    "(estado_venta = 'ANULADA') = (fecha_anulacion IS NOT NULL AND id_usuario_anulacion IS NOT NULL AND id_usuario_solicitante IS NOT NULL AND motivo_anulacion IS NOT NULL) AND (estado_venta = 'ANULADA' OR detalle_anulacion IS NULL)"
                ),
                name="ck_ventas_anulacion",
            ),
        ),
    ]
