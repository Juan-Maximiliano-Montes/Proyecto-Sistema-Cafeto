"""Resultado de cada operación del registro de auditoría (DD-62).

LOG_AUDITORIA suma ``resultado_operacion``: EXITOSA para lo que se hizo,
RECHAZADA para lo que el sistema frenó por un control de seguridad (un
acceso sin permiso, una autorización que no correspondía, una anulación
fuera de regla). Es el dato "resultado del evento" que pide NIST SP 800-53
AU-3. Todo lo que ya estaba asentado se hizo, así que nace EXITOSA.

El histórico (``hist_log_auditoria``) recibe la misma columna, para que
el archivado siga copiando el asiento completo.
"""

import nucleo.restricciones
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("seguridad", "0003_datos_iniciales"),
        ("nucleo", "0002_historico_y_vistas"),
    ]

    operations = [
        migrations.AddField(
            model_name="logauditoria",
            name="resultado_operacion",
            field=models.CharField(
                choices=[("EXITOSA", "Exitosa"), ("RECHAZADA", "Rechazada")],
                db_default="EXITOSA",
                default="EXITOSA",
                max_length=10,
                verbose_name="Resultado",
            ),
        ),
        migrations.RunSQL(
            """
            ALTER TABLE hist_log_auditoria
                ADD COLUMN IF NOT EXISTS resultado_operacion varchar(10)
                NOT NULL DEFAULT 'EXITOSA';
            """,
            "ALTER TABLE hist_log_auditoria DROP COLUMN IF EXISTS resultado_operacion;"),
        migrations.AddIndex(
            model_name="logauditoria",
            index=models.Index(
                fields=["resultado_operacion", "-fecha_hora"],
                name="ix_log_auditoria_resultado",
            ),
        ),
        migrations.AddConstraint(
            model_name="logauditoria",
            constraint=nucleo.restricciones.CheckEnBase(
                condition=nucleo.restricciones.CheckSQL(
                    "resultado_operacion IN ('EXITOSA','RECHAZADA') AND (resultado_operacion = 'EXITOSA' OR (registro_anterior IS NULL AND registro_nuevo IS NULL))"
                ),
                name="ck_log_auditoria_resultado",
            ),
        ),
    ]
