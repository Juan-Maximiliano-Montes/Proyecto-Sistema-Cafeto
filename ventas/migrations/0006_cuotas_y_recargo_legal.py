"""Cuotas y recargo según la ley (DD-65).

La Ley 25.065 (art. 37, inc. c) prohíbe cobrar distinto con tarjeta que en
efectivo; la Resolución 51-E/2017 de la Secretaría de Comercio aclara que
vale para el pago en una sola cuota. El recargo queda sólo para un plan de
tarjeta de crédito en más de una cuota.

Datos: la "Tarjeta de crédito" inicial se marca como tarjeta de crédito, y
todo recargo cargado en un medio de un solo pago vuelve a 0 (era el caso
que la ley no permite). Si la dueña quiere un plan en cuotas con recargo,
lo da de alta como un medio de pago nuevo.
"""

import nucleo.restricciones
from decimal import Decimal

from django.db import migrations, models


def ajustar_a_la_ley(apps, schema_editor):
    MedioPago = apps.get_model('ventas', 'MedioPago')
    for medio in MedioPago.objects.all():
        nombre = (medio.nombre_medio_pago or '').lower()
        if 'credito' in nombre.replace('é', 'e') and not medio.afecta_caja:
            medio.es_tarjeta_credito = True
        if medio.recargo_porcentaje and medio.cantidad_cuotas == 1:
            print(f'\n  {medio.nombre_medio_pago}: recargo {medio.recargo_porcentaje} % '
                  '-> 0 (un solo pago no lleva recargo, Ley 25.065 art. 37).')
            medio.recargo_porcentaje = Decimal('0')
        medio.save(update_fields=['es_tarjeta_credito', 'recargo_porcentaje'])


class Migration(migrations.Migration):

    dependencies = [
        ("seguridad", "0004_resultado_de_la_operacion"),
        ("ventas", "0005_solicitante_de_la_anulacion"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="mediopago",
            name="ck_medios_pago_recargo",
        ),
        migrations.AddField(
            model_name="mediopago",
            name="cantidad_cuotas",
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text='1 si es un solo pago. Un plan en cuotas es un medio de pago aparte (por ejemplo, "Crédito 3 cuotas").',
                verbose_name="Cuotas",
            ),
        ),
        migrations.AddField(
            model_name="mediopago",
            name="es_tarjeta_credito",
            field=models.BooleanField(
                default=False,
                help_text="Sólo la tarjeta de crédito admite pagar en cuotas.",
                verbose_name="Es tarjeta de crédito",
            ),
        ),
        migrations.RunPython(ajustar_a_la_ley, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="mediopago",
            constraint=nucleo.restricciones.CheckEnBase(
                condition=nucleo.restricciones.CheckSQL(
                    "recargo_porcentaje >= 0 AND recargo_porcentaje <= 100 AND (recargo_porcentaje = 0 OR cantidad_cuotas > 1)"
                ),
                name="ck_medios_pago_recargo",
            ),
        ),
        migrations.AddConstraint(
            model_name="mediopago",
            constraint=nucleo.restricciones.CheckEnBase(
                condition=nucleo.restricciones.CheckSQL(
                    "cantidad_cuotas BETWEEN 1 AND 36 AND (es_tarjeta_credito OR cantidad_cuotas = 1)"
                ),
                name="ck_medios_pago_cuotas",
            ),
        ),
    ]
