"""Toda factura de compra a pagar tiene vencimiento (DD-101).

Las compras cargadas sin «Se paga hasta» toman la fecha de la factura más
el plazo de la condición de pago del proveedor (contado: el mismo día; cuenta
corriente: 30 días), así «A pagar» muestra siempre «Vence…» o «Venció…».
"""
from django.db import migrations

COMPLETAR = """
UPDATE compras c
   SET fecha_vencimiento_pago = c.fecha_compra + (CASE p.condicion_pago
           WHEN '15_DIAS' THEN 15 WHEN '30_DIAS' THEN 30 WHEN '60_DIAS' THEN 60
           WHEN 'CTA_CTE' THEN 30 ELSE 0 END)
  FROM proveedores p
 WHERE p.id_proveedor = c.id_proveedor AND c.fecha_vencimiento_pago IS NULL
"""


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0007_facturas_recibidas_y_compra_ocasional'),
    ]

    operations = [
        migrations.RunSQL(COMPLETAR, migrations.RunSQL.noop),
    ]
