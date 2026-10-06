"""La clave foránea del turno sigue al renombre de la maestra.

``CAJAS`` es el turno; ``CAJAS_REGISTRADORAS``, el mueble. La columna que
los une pasa de ``id_puesto_caja`` a ``id_caja_registradora``, y con ella el
único parcial que impide dos turnos abiertos sobre la misma caja (DD-42).

El parcial se quita antes de renombrar porque su predicado nombra la
columna; después se vuelve a crear ya con el nombre nuevo.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0007_reinstalar_logica_de_la_base'),
        ('maestros', '0006_cajas_registradoras'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='caja',
            name='uq_turno_abierto_por_puesto'),
        migrations.RenameField(
            model_name='caja',
            old_name='id_puesto_caja',
            new_name='id_caja_registradora'),
        migrations.AlterField(
            model_name='caja',
            name='id_caja_registradora',
            field=models.ForeignKey(
                db_column='id_caja_registradora',
                on_delete=django.db.models.deletion.RESTRICT,
                related_name='turnos', to='maestros.cajaregistradora',
                verbose_name='Caja registradora')),
        migrations.AddConstraint(
            model_name='caja',
            constraint=models.UniqueConstraint(
                condition=models.Q(('estado_caja', 'ABIERTA')),
                fields=('id_caja_registradora',),
                name='uq_turno_abierto_por_caja')),
    ]
