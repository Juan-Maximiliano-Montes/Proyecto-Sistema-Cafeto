"""CAJAS pasa a llamarse TURNOS_CAJA.

El modelo tenía dos cosas distintas con el mismo nombre: el **mueble** donde
se cobra y el **período de trabajo** sobre ese mueble. DD-56 ya le había
puesto nombre propio al mueble (`CAJAS_REGISTRADORAS`); lo que quedaba
llamándose `CAJAS` era el turno, y el nombre no lo decía.

Ahora cada concepto tiene su palabra:

| Entidad | Qué es | Una fila por |
|---|---|---|
| `CAJAS_REGISTRADORAS` | el mueble | cada caja del local |
| `TURNOS_CAJA` | el período operativo | cada apertura y cierre |

Se renombra, no se recrea: `ALTER TABLE ... RENAME` conserva las filas, los
identificadores y la secuencia. Importa porque de `TURNOS_CAJA` cuelgan
`VENTAS`, `MOVIMIENTOS_CAJA` y `SESIONES`: un `CREATE TABLE` nuevo habría
dejado huérfano todo el historial de caja.

Orden de las operaciones, que acá importa:

1. Se quitan las restricciones cuyo texto nombra `estado_caja`. PostgreSQL
   no reescribe el cuerpo de un CHECK cuando se renombra la columna.
2. Se renombran las columnas, el modelo y la tabla.
3. Se vuelven a crear con el nombre nuevo.

Las claves foráneas de `VENTAS`, `MOVIMIENTOS_CAJA` y `SESIONES` las renombra
la migración 0013, que depende de ésta; los nombres de las restricciones en
la base los rehace la 0014 leyendo `01_esquema.sql`.
"""
import cafeto.restricciones
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0011_validadores_toleran_separadores'),
    ]

    operations = [
        # 1. Fuera lo que nombra la columna vieja.
        migrations.RemoveConstraint(
            model_name='caja', name='ck_cajas_estado'),
        migrations.RemoveConstraint(
            model_name='caja', name='ck_cajas_montos'),
        migrations.RemoveConstraint(
            model_name='caja', name='ck_cajas_cierre'),
        migrations.RemoveConstraint(
            model_name='caja', name='ck_cajas_justificacion'),
        migrations.RemoveConstraint(
            model_name='caja', name='ck_cajas_fechas'),
        migrations.RemoveConstraint(
            model_name='caja', name='ck_cajas_textos'),
        migrations.RemoveConstraint(
            model_name='caja', name='uq_caja_abierta_por_usuario'),
        migrations.RemoveConstraint(
            model_name='caja', name='uq_turno_abierto_por_caja'),

        # 2. Columnas, modelo y tabla.
        migrations.RenameField(
            model_name='caja', old_name='id_caja',
            new_name='id_turno_caja'),
        migrations.RenameField(
            model_name='caja', old_name='estado_caja',
            new_name='estado_turno'),
        migrations.RenameModel(old_name='Caja', new_name='TurnoCaja'),
        migrations.AlterModelTable(
            name='turnocaja', table='turnos_caja'),
        migrations.AlterModelOptions(
            name='turnocaja',
            options={
                'ordering': ['-fecha_apertura'],
                'verbose_name': 'Turno de caja',
                'verbose_name_plural': 'Turnos de caja',
            }),
        migrations.AlterField(
            model_name='turnocaja', name='estado_turno',
            field=models.CharField(
                choices=[('ABIERTA', 'Abierta'), ('CERRADA', 'Cerrada')],
                default='ABIERTA', max_length=10, verbose_name='Estado')),

        # Los related_name hablaban de "cajas"; ahora de turnos. Es lo que
        # se escribe en el código: usuario.turnos_abiertos.
        migrations.AlterField(
            model_name='turnocaja', name='id_usuario_apertura',
            field=models.ForeignKey(
                db_column='id_usuario_apertura',
                on_delete=django.db.models.deletion.RESTRICT,
                related_name='turnos_abiertos', to='usuarios.usuario',
                verbose_name='Abrió')),
        migrations.AlterField(
            model_name='turnocaja', name='id_usuario_cierre',
            field=models.ForeignKey(
                blank=True, db_column='id_usuario_cierre', null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name='turnos_cerrados', to='usuarios.usuario',
                verbose_name='Cerró')),

        # 3. Las mismas reglas, con el nombre nuevo.
        migrations.AddConstraint(
            model_name='turnocaja',
            constraint=cafeto.restricciones.CheckEnBase(
                check=cafeto.restricciones.CheckSQL(
                    "estado_turno IN ('ABIERTA','CERRADA')"),
                name='ck_turnos_caja_estado')),
        migrations.AddConstraint(
            model_name='turnocaja',
            constraint=cafeto.restricciones.CheckEnBase(
                check=cafeto.restricciones.CheckSQL(
                    'monto_inicial >= 0 AND '
                    '(monto_final IS NULL OR monto_final >= 0)'),
                name='ck_turnos_caja_montos')),
        migrations.AddConstraint(
            model_name='turnocaja',
            constraint=cafeto.restricciones.CheckEnBase(
                check=cafeto.restricciones.CheckSQL(
                    "(estado_turno = 'ABIERTA' AND fecha_cierre IS NULL AND "
                    "id_usuario_cierre IS NULL AND diferencia_caja IS NULL) OR "
                    "(estado_turno = 'CERRADA' AND fecha_cierre IS NOT NULL AND "
                    "id_usuario_cierre IS NOT NULL AND monto_final IS NOT NULL "
                    "AND diferencia_caja IS NOT NULL)"),
                name='ck_turnos_caja_cierre')),
        migrations.AddConstraint(
            model_name='turnocaja',
            constraint=cafeto.restricciones.CheckEnBase(
                check=cafeto.restricciones.CheckSQL(
                    'diferencia_caja IS NULL OR diferencia_caja = 0 OR '
                    'observacion_diferencia IS NOT NULL'),
                name='ck_turnos_caja_justificacion')),
        migrations.AddConstraint(
            model_name='turnocaja',
            constraint=cafeto.restricciones.CheckEnBase(
                check=cafeto.restricciones.CheckSQL(
                    'fecha_cierre IS NULL OR fecha_cierre >= fecha_apertura'),
                name='ck_turnos_caja_fechas')),
        migrations.AddConstraint(
            model_name='turnocaja',
            constraint=cafeto.restricciones.CheckEnBase(
                check=cafeto.restricciones.CheckSQL(
                    "trim(estado_turno) <> ''"),
                name='ck_turnos_caja_textos')),
        migrations.AddConstraint(
            model_name='turnocaja',
            constraint=models.UniqueConstraint(
                condition=models.Q(('estado_turno', 'ABIERTA')),
                fields=('id_usuario_apertura',),
                name='uq_turno_abierto_por_usuario')),
        migrations.AddConstraint(
            model_name='turnocaja',
            constraint=models.UniqueConstraint(
                condition=models.Q(('estado_turno', 'ABIERTA')),
                fields=('id_caja_registradora',),
                name='uq_turno_abierto_por_caja')),
    ]
