"""Anulación de venta (DD-26, Hito 4).

La venta nace COMPLETADA y puede quedar ANULADA con fecha, usuario y
motivo. El libro de caja suma el tipo ANULACION (salida de la plata que
la venta había entrado) y cada venta puede tener a lo sumo un asiento de
cada tipo.

El histórico ``hist_ventas`` (nucleo 0002) recibe las mismas columnas:
como se agregan al final, el archivado las copia nombrándolas (ver
``pasar_a_historico``). Lo ya archivado era, por definición, venta
completada.
"""

import django.db.models.deletion
import nucleo.restricciones
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0004_datos_iniciales"),
        ("fidelizacion", "0006_permisos_de_clientes"),
        ("seguridad", "0003_datos_iniciales"),
        ("ventas", "0002_datos_iniciales"),
        ("nucleo", "0002_historico_y_vistas"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="movimientocaja",
            name="ck_movcaja_tipo",
        ),
        migrations.RemoveConstraint(
            model_name="movimientocaja",
            name="ck_movcaja_signo",
        ),
        migrations.RemoveConstraint(
            model_name="movimientocaja",
            name="ck_movcaja_documento",
        ),
        migrations.RemoveConstraint(
            model_name="movimientocaja",
            name="uq_movcaja_por_venta",
        ),
        migrations.AddField(
            model_name="venta",
            name="detalle_anulacion",
            field=models.CharField(
                blank=True,
                max_length=120,
                null=True,
                verbose_name="Detalle de la anulación",
            ),
        ),
        migrations.AddField(
            model_name="venta",
            name="estado_venta",
            field=models.CharField(
                choices=[("COMPLETADA", "Completada"), ("ANULADA", "Anulada")],
                default="COMPLETADA",
                max_length=15,
                verbose_name="Estado",
            ),
        ),
        migrations.AddField(
            model_name="venta",
            name="fecha_anulacion",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="Anulada el"
            ),
        ),
        migrations.AddField(
            model_name="venta",
            name="id_usuario_anulacion",
            field=models.ForeignKey(
                blank=True,
                db_column="id_usuario_anulacion",
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="ventas_anuladas",
                to="seguridad.usuario",
                verbose_name="Anuló",
            ),
        ),
        migrations.AddField(
            model_name="venta",
            name="motivo_anulacion",
            field=models.CharField(
                blank=True,
                choices=[
                    ("PAGO_RECHAZADO", "El pago fue rechazado"),
                    ("ERROR_DE_CARGA", "Se cargó mal (producto, cantidad o precio)"),
                    ("COBRO_DUPLICADO", "Se cobró dos veces"),
                    ("CLIENTE_DESISTIO", "El cliente desistió antes de irse"),
                    ("OTRO", "Otro (explicar)"),
                ],
                max_length=20,
                null=True,
                verbose_name="Motivo de la anulación",
            ),
        ),
        migrations.AlterField(
            model_name="movimientocaja",
            name="tipo_movimiento",
            field=models.CharField(
                choices=[
                    ("APERTURA", "Fondo inicial"),
                    ("VENTA", "Venta"),
                    ("COBRO_CAMBIO", "Diferencia cobrada en un cambio"),
                    ("REFUERZO", "Refuerzo de cambio"),
                    ("RETIRO", "Retiro de efectivo"),
                    ("GASTO", "Gasto pagado con la caja"),
                    ("DEVOLUCION", "Devolución de dinero"),
                    ("ANULACION", "Anulación de venta"),
                ],
                max_length=12,
                verbose_name="Tipo",
            ),
        ),
        migrations.AddConstraint(
            model_name="movimientocaja",
            constraint=nucleo.restricciones.CheckEnBase(
                condition=nucleo.restricciones.CheckSQL(
                    "tipo_movimiento IN ('APERTURA', 'VENTA', 'COBRO_CAMBIO', 'REFUERZO', 'RETIRO', 'GASTO', 'DEVOLUCION', 'ANULACION')"
                ),
                name="ck_movcaja_tipo",
            ),
        ),
        migrations.AddConstraint(
            model_name="movimientocaja",
            constraint=nucleo.restricciones.CheckEnBase(
                condition=nucleo.restricciones.CheckSQL(
                    "(tipo_movimiento = 'APERTURA' AND monto >= 0) OR (tipo_movimiento IN ('VENTA', 'COBRO_CAMBIO', 'REFUERZO') AND monto > 0) OR (tipo_movimiento IN ('RETIRO', 'GASTO', 'DEVOLUCION', 'ANULACION') AND monto < 0)"
                ),
                name="ck_movcaja_signo",
            ),
        ),
        migrations.AddConstraint(
            model_name="movimientocaja",
            constraint=nucleo.restricciones.CheckEnBase(
                condition=nucleo.restricciones.CheckSQL(
                    "(id_venta IS NOT NULL) = (tipo_movimiento IN ('VENTA','ANULACION')) AND (id_cambio IS NOT NULL) = (tipo_movimiento IN ('COBRO_CAMBIO','DEVOLUCION')) AND (id_egreso IS NOT NULL) = (tipo_movimiento = 'GASTO')"
                ),
                name="ck_movcaja_documento",
            ),
        ),
        migrations.AddConstraint(
            model_name="movimientocaja",
            constraint=models.UniqueConstraint(
                condition=models.Q(("id_venta__isnull", False)),
                fields=("id_venta", "tipo_movimiento"),
                name="uq_movcaja_por_venta",
            ),
        ),
        migrations.AddConstraint(
            model_name="venta",
            constraint=nucleo.restricciones.CheckEnBase(
                condition=nucleo.restricciones.CheckSQL(
                    "estado_venta IN ('COMPLETADA','ANULADA')"
                ),
                name="ck_ventas_estado",
            ),
        ),
        migrations.AddConstraint(
            model_name="venta",
            constraint=nucleo.restricciones.CheckEnBase(
                condition=nucleo.restricciones.CheckSQL(
                    "(estado_venta = 'ANULADA') = (fecha_anulacion IS NOT NULL AND id_usuario_anulacion IS NOT NULL AND motivo_anulacion IS NOT NULL) AND (estado_venta = 'ANULADA' OR detalle_anulacion IS NULL)"
                ),
                name="ck_ventas_anulacion",
            ),
        ),
        migrations.AddConstraint(
            model_name="venta",
            constraint=nucleo.restricciones.CheckEnBase(
                condition=nucleo.restricciones.CheckSQL(
                    "(motivo_anulacion IS NULL OR motivo_anulacion IN ('PAGO_RECHAZADO','ERROR_DE_CARGA','COBRO_DUPLICADO','CLIENTE_DESISTIO','OTRO')) AND (detalle_anulacion IS NULL OR trim(detalle_anulacion) <> '') AND (motivo_anulacion IS DISTINCT FROM 'OTRO' OR detalle_anulacion IS NOT NULL)"
                ),
                name="ck_ventas_motivo_anulacion",
            ),
        ),
        migrations.RunSQL(
            """
            ALTER TABLE hist_ventas
                ADD COLUMN IF NOT EXISTS estado_venta varchar(15),
                ADD COLUMN IF NOT EXISTS fecha_anulacion timestamptz,
                ADD COLUMN IF NOT EXISTS id_usuario_anulacion integer,
                ADD COLUMN IF NOT EXISTS motivo_anulacion varchar(20),
                ADD COLUMN IF NOT EXISTS detalle_anulacion varchar(120);
            UPDATE hist_ventas SET estado_venta = 'COMPLETADA'
             WHERE estado_venta IS NULL;
            -- La vista del libro completo suma el estado al final (CREATE
            -- OR REPLACE sólo admite columnas nuevas al final): un reporte
            -- que cruce el corte del archivado también excluye anuladas.
            CREATE OR REPLACE VIEW v_ventas_completo AS
                SELECT id_venta, id_turno_caja, id_usuario, id_cliente,
                       fecha_venta, hora_venta, subtotal, monto_descuento,
                       ajuste_redondeo, monto_recargo, total_venta,
                       estado_venta FROM ventas
                UNION ALL
                SELECT id_venta, id_turno_caja, id_usuario, id_cliente,
                       fecha_venta, hora_venta, subtotal, monto_descuento,
                       ajuste_redondeo, monto_recargo, total_venta,
                       estado_venta FROM hist_ventas;
            """,
            """
            DROP VIEW IF EXISTS v_ventas_completo;
            CREATE VIEW v_ventas_completo AS
                SELECT id_venta, id_turno_caja, id_usuario, id_cliente,
                       fecha_venta, hora_venta, subtotal, monto_descuento,
                       ajuste_redondeo, monto_recargo, total_venta FROM ventas
                UNION ALL
                SELECT id_venta, id_turno_caja, id_usuario, id_cliente,
                       fecha_venta, hora_venta, subtotal, monto_descuento,
                       ajuste_redondeo, monto_recargo, total_venta
                  FROM hist_ventas;
            ALTER TABLE hist_ventas
                DROP COLUMN IF EXISTS estado_venta,
                DROP COLUMN IF EXISTS fecha_anulacion,
                DROP COLUMN IF EXISTS id_usuario_anulacion,
                DROP COLUMN IF EXISTS motivo_anulacion,
                DROP COLUMN IF EXISTS detalle_anulacion;
            """),
    ]
