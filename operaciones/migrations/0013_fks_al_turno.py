"""Las tres claves foráneas que apuntan al turno siguen el renombre.

`VENTAS`, `MOVIMIENTOS_CAJA` y `SESIONES` guardaban la columna como
`id_caja`. Con el mueble llamándose `CAJAS_REGISTRADORAS` y el período
`TURNOS_CAJA`, esa columna era justo la que podía confundirse: pasa a
`id_turno_caja`.

Van en una migración aparte de la 0012 porque Django tiene que terminar de
renombrar el modelo apuntado antes de que estas tres lo referencien por su
nombre nuevo.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0012_turnos_caja'),
    ]

    operations = [
        migrations.RenameField(
            model_name='venta', old_name='id_caja',
            new_name='id_turno_caja'),
        migrations.AlterField(
            model_name='venta', name='id_turno_caja',
            field=models.ForeignKey(
                db_column='id_turno_caja',
                on_delete=django.db.models.deletion.RESTRICT,
                related_name='ventas', to='operaciones.turnocaja',
                verbose_name='Turno de caja')),

        migrations.RenameField(
            model_name='movimientocaja', old_name='id_caja',
            new_name='id_turno_caja'),
        migrations.AlterField(
            model_name='movimientocaja', name='id_turno_caja',
            field=models.ForeignKey(
                db_column='id_turno_caja',
                on_delete=django.db.models.deletion.RESTRICT,
                related_name='movimientos', to='operaciones.turnocaja',
                verbose_name='Turno de caja')),

        migrations.RenameField(
            model_name='sesion', old_name='id_caja',
            new_name='id_turno_caja'),
        migrations.AlterField(
            model_name='sesion', name='id_turno_caja',
            field=models.ForeignKey(
                blank=True, db_column='id_turno_caja', null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='sesiones', to='operaciones.turnocaja',
                verbose_name='Turno de caja')),
    ]
