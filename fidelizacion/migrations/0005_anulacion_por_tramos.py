"""El reverso de puntos de una devolución puede ir en tramos.

La venta ya no se anula: lo que se devuelve es un cambio, y puede haber
varios (una devolución parcial, después otra). El reverso de la
ACUMULACION se recalcula en cada uno y se asienta la diferencia, así que
la venta puede tener varios ANULACION sin beneficio; el tope (nunca más de
lo acumulado) lo controla ``fidelizacion.puntos.revertir_por_devolucion``.
El reverso de un CANJE sigue siendo uno solo por beneficio y venta.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fidelizacion", "0004_datos_iniciales"),
        ("ventas", "0002_datos_iniciales"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="movimientopuntos",
            name="uq_anulacion_puntos",
        ),
        migrations.AddConstraint(
            model_name="movimientopuntos",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("id_beneficio__isnull", False), ("tipo_movimiento", "ANULACION")
                ),
                fields=("id_venta", "id_beneficio"),
                name="uq_anulacion_canje",
            ),
        ),
    ]
