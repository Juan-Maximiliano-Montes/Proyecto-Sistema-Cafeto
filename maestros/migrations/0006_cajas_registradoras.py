"""PUESTOS_CAJA pasa a llamarse CAJAS_REGISTRADORAS.

"Puesto de caja" es vocabulario interno: quien usa el sistema le dice
*caja registradora* al mueble donde cobra. El nombre de la entidad, de su
tabla, de sus columnas y de sus permisos se alinean con esa palabra.

Se renombra, no se recrea. Un ``CREATE TABLE`` nuevo más un ``INSERT``
perdería las cajas registradoras ya cargadas y los turnos de ``CAJAS`` que
las referencian; ``ALTER TABLE ... RENAME`` conserva las filas, los
identificadores y la secuencia de la clave primaria.

Orden de las operaciones, que acá importa:

1. Se quitan las tres restricciones, porque su condición nombra la columna
   vieja y PostgreSQL no renombra el texto de un CHECK.
2. Se renombran las columnas y después el modelo y la tabla.
3. Se vuelven a crear las restricciones con el nombre nuevo.

La clave foránea ``cajas.id_caja_registradora`` la renombra la migración
``operaciones/0008``, que depende de ésta. El nombre de la restricción en
la base lo rehace ``operaciones/0009`` leyendo ``01_esquema.sql``, igual
que hizo la 0006 cuando entró MARCAS.
"""
import cafeto.restricciones
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('maestros', '0005_permisos_de_marcas'),
    ]

    operations = [
        # 1. Fuera las restricciones que nombran la columna vieja.
        migrations.RemoveConstraint(
            model_name='puestocaja',
            name='ck_puestos_nombre'),
        migrations.RemoveConstraint(
            model_name='puestocaja',
            name='ck_puestos_caja_textos'),
        migrations.RemoveConstraint(
            model_name='puestocaja',
            name='uq_puestos_caja_nombre_norm'),

        # 2. Columnas, modelo y tabla.
        migrations.RenameField(
            model_name='puestocaja',
            old_name='id_puesto_caja',
            new_name='id_caja_registradora'),
        migrations.RenameField(
            model_name='puestocaja',
            old_name='nombre_puesto',
            new_name='nombre_caja_registradora'),
        migrations.RenameModel(
            old_name='PuestoCaja',
            new_name='CajaRegistradora'),
        migrations.AlterModelTable(
            name='cajaregistradora',
            table='cajas_registradoras'),
        migrations.AlterModelOptions(
            name='cajaregistradora',
            options={
                'ordering': ['nombre_caja_registradora'],
                'verbose_name': 'Caja registradora',
                'verbose_name_plural': 'Cajas registradoras',
            }),
        migrations.AlterField(
            model_name='cajaregistradora',
            name='nombre_caja_registradora',
            field=models.CharField(max_length=20,
                                   verbose_name='Caja registradora')),
        migrations.AlterField(
            model_name='cajaregistradora',
            name='usuario_modificacion',
            field=models.ForeignKey(
                blank=True, db_column='usuario_modificacion',
                db_constraint=False, editable=False, null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name='cajas_registradoras_modificadas',
                to='usuarios.usuario', verbose_name='Modificado por')),

        # 3. Las mismas reglas, con el nombre nuevo.
        migrations.AddConstraint(
            model_name='cajaregistradora',
            constraint=cafeto.restricciones.CheckEnBase(
                check=cafeto.restricciones.CheckSQL(
                    'length(trim(nombre_caja_registradora)) >= 3'),
                name='ck_cajas_registradoras_nombre')),
        migrations.AddConstraint(
            model_name='cajaregistradora',
            constraint=cafeto.restricciones.CheckEnBase(
                check=cafeto.restricciones.CheckSQL(
                    "trim(nombre_caja_registradora) <> ''"),
                name='ck_cajas_registradoras_textos')),
        migrations.AddConstraint(
            model_name='cajaregistradora',
            constraint=models.UniqueConstraint(
                cafeto.restricciones.ClaveNormalizada(
                    'nombre_caja_registradora'),
                name='uq_cajas_registradoras_nombre_norm')),
    ]
