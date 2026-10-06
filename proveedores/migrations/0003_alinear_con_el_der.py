"""Alinea provincias, localidades y proveedores con el DER (Fase 4).

Renombra ``provincia`` y ``localidad`` al plural, pone a cada clave
foránea el nombre de columna del esquema (``id_provincia`` en lugar de
``id_provincia_id``), ajusta las longitudes y reemplaza las restricciones
con nombre propio de Django por las del esquema (``ck_proveedores_cuit``,
``uq_localidades_nombre_norm``...).

Los textos opcionales que estaban guardados como cadena vacía pasan a
NULL, que es lo que pide V-G1 y lo que esperan los CHECK de la base.
"""

import cafeto.restricciones
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def forzar_chequeo_de_claves(schema_editor):
    """Vacía la cola de controles diferidos antes de seguir con el DDL.

    Django crea las claves foráneas como DEFERRABLE INITIALLY DEFERRED, así
    que cada UPDATE dentro de la migración deja controles pendientes hasta
    el commit. PostgreSQL se niega a hacer ALTER TABLE sobre una tabla con
    eventos de trigger pendientes ("cannot ALTER TABLE ... because it has
    pending trigger events"), y las restricciones se agregan justo después.
    Este SET CONSTRAINTS los ejecuta en el acto y deja la tabla libre.
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')


def vaciar_textos_opcionales(apps, schema_editor):
    """Cadena vacía -> NULL en los campos opcionales (V-G1)."""
    Localidad = apps.get_model('proveedores', 'Localidad')
    Proveedor = apps.get_model('proveedores', 'Proveedor')

    Localidad.objects.filter(codigo_postal='').update(codigo_postal=None)
    Proveedor.objects.filter(telefono_proveedor='').update(
        telefono_proveedor=None)
    Proveedor.objects.filter(email_proveedor='').update(email_proveedor=None)
    Proveedor.objects.filter(domicilio='').update(domicilio=None)
    forzar_chequeo_de_claves(schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0006_alinear_con_el_der'),
        ('proveedores', '0002_datos_iniciales_v2'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='localidad',
            name='localidad_unica_por_provincia',
        ),
        migrations.RemoveConstraint(
            model_name='proveedor',
            name='proveedor_condicion_pago_valida',
        ),
        migrations.RemoveConstraint(
            model_name='proveedor',
            name='proveedor_nombre_no_vacio',
        ),
        migrations.AlterField(
            model_name='localidad',
            name='codigo_postal',
            field=models.CharField(blank=True, max_length=8, null=True, verbose_name='Código postal'),
        ),
        migrations.AlterField(
            model_name='localidad',
            name='id_provincia',
            field=models.ForeignKey(db_column='id_provincia', on_delete=django.db.models.deletion.RESTRICT, related_name='localidades', to='proveedores.provincia', verbose_name='Provincia'),
        ),
        migrations.AlterField(
            model_name='proveedor',
            name='condicion_pago',
            field=models.CharField(choices=[('CONTADO', 'Contado'), ('CTA_CTE', 'Cuenta corriente'), ('15_DIAS', '15 días'), ('30_DIAS', '30 días'), ('60_DIAS', '60 días')], default='CONTADO', max_length=10, verbose_name='Condición de pago'),
        ),
        migrations.AlterField(
            model_name='proveedor',
            name='cuit_proveedor',
            field=models.CharField(max_length=13, verbose_name='CUIT'),
        ),
        migrations.AlterField(
            model_name='proveedor',
            name='domicilio',
            field=models.CharField(blank=True, max_length=120, null=True, verbose_name='Domicilio'),
        ),
        migrations.AlterField(
            model_name='proveedor',
            name='email_proveedor',
            field=models.EmailField(blank=True, max_length=254, null=True, verbose_name='Email'),
        ),
        migrations.AlterField(
            model_name='proveedor',
            name='fecha_alta',
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Fecha de alta'),
        ),
        migrations.AlterField(
            model_name='proveedor',
            name='fecha_modificacion',
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Última modificación'),
        ),
        migrations.AlterField(
            model_name='proveedor',
            name='id_localidad',
            field=models.ForeignKey(blank=True, db_column='id_localidad', null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='proveedores', to='proveedores.localidad', verbose_name='Localidad'),
        ),
        migrations.AlterField(
            model_name='proveedor',
            name='nombre_proveedor',
            field=models.CharField(max_length=50, verbose_name='Nombre'),
        ),
        migrations.AlterField(
            model_name='proveedor',
            name='telefono_proveedor',
            field=models.CharField(blank=True, max_length=15, null=True, verbose_name='Teléfono'),
        ),
        migrations.AlterField(
            model_name='proveedor',
            name='usuario_modificacion',
            field=models.ForeignKey(blank=True, db_column='usuario_modificacion', db_constraint=False, editable=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='proveedores_modificados', to='usuarios.usuario', verbose_name='Modificado por'),
        ),
        migrations.AlterField(
            model_name='provincia',
            name='nombre_provincia',
            field=models.CharField(max_length=60, verbose_name='Provincia'),
        ),
        migrations.RunPython(
            vaciar_textos_opcionales, reverse_code=migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='localidad',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("trim(nombre_localidad) <> ''"), name='ck_localidades_textos'),
        ),
        migrations.AddConstraint(
            model_name='localidad',
            constraint=models.UniqueConstraint(models.F('id_provincia'), cafeto.restricciones.ClaveNormalizada('nombre_localidad'), name='uq_localidades_nombre_norm'),
        ),
        migrations.AddConstraint(
            model_name='proveedor',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("cuit_proveedor ~ '^[0-9]{2}-[0-9]{8}-[0-9]$'"), name='ck_proveedores_cuit'),
        ),
        migrations.AddConstraint(
            model_name='proveedor',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("condicion_pago IN ('CONTADO','CTA_CTE','15_DIAS','30_DIAS','60_DIAS')"), name='ck_proveedores_condicion'),
        ),
        migrations.AddConstraint(
            model_name='proveedor',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("trim(nombre_proveedor) <> '' AND trim(cuit_proveedor) <> '' AND trim(condicion_pago) <> ''"), name='ck_proveedores_textos'),
        ),
        migrations.AddConstraint(
            model_name='proveedor',
            constraint=models.UniqueConstraint(fields=('cuit_proveedor',), name='uq_proveedores_cuit_proveedor'),
        ),
        migrations.AddConstraint(
            model_name='provincia',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("trim(nombre_provincia) <> ''"), name='ck_provincias_textos'),
        ),
        migrations.AddConstraint(
            model_name='provincia',
            constraint=models.UniqueConstraint(cafeto.restricciones.ClaveNormalizada('nombre_provincia'), name='uq_provincias_nombre_norm'),
        ),
        migrations.AlterModelTable(
            name='localidad',
            table='localidades',
        ),
        migrations.AlterModelTable(
            name='provincia',
            table='provincias',
        ),
    ]
