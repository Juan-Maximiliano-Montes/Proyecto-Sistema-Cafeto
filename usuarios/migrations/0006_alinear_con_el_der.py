"""Alinea las tablas de seguridad con el DER de alto refinamiento (Fase 4).

Renombra ``perfil``/``permiso``/``perfil_permiso``/``usuario`` a su nombre
plural y cada columna al nombre que le da ``01_esquema.sql``; ajusta
longitudes; agrega el ``borrado_logico`` de las maestras y la
``fecha_alta`` de los usuarios, y cambia la baja lógica de USUARIOS de
``borrado_logico`` (True = eliminado) a ``activo`` (False = eliminado),
que es como la escribe el DER.

NO se pierden datos: el renombre de tabla y de columna es un
``ALTER TABLE ... RENAME``, y el cambio de sentido de la baja se hace con
un paso de datos que copia el valor invertido antes de borrar la columna
vieja.
"""

import cafeto.restricciones
import django.core.validators
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


def copiar_baja_logica_a_activo(apps, schema_editor):
    """``borrado_logico = True`` pasa a ser ``activo = False``."""
    Usuario = apps.get_model('usuarios', 'Usuario')
    Usuario.objects.filter(borrado_logico=True).update(activo=False)
    Usuario.objects.filter(borrado_logico=False).update(activo=True)
    forzar_chequeo_de_claves(schema_editor)


def volver_a_borrado_logico(apps, schema_editor):
    """Inverso del anterior, para poder deshacer la migración."""
    Usuario = apps.get_model('usuarios', 'Usuario')
    Usuario.objects.filter(activo=False).update(borrado_logico=True)
    Usuario.objects.filter(activo=True).update(borrado_logico=False)


def normalizar_datos(apps, schema_editor):
    """Deja los datos como los exigen los CHECK que vienen después.

    * V-G1: una descripción opcional vacía se guarda NULL, no cadena vacía.
    * ck_usuarios_baja: ``activo`` y ``fecha_baja`` tienen que contar lo
      mismo (vigente -> sin fecha de baja; dado de baja -> con fecha).
    * ck_usuarios_fechas: la baja nunca puede ser anterior al alta. Como
      la columna ``fecha_alta`` es nueva y se llenó con el momento de la
      migración, en los usuarios ya dados de baja se la retrasa hasta la
      fecha de baja.
    """
    Perfil = apps.get_model('usuarios', 'Perfil')
    Permiso = apps.get_model('usuarios', 'Permiso')
    Usuario = apps.get_model('usuarios', 'Usuario')

    Perfil.objects.filter(descripcion='').update(descripcion=None)
    Permiso.objects.filter(descripcion='').update(descripcion=None)

    Usuario.objects.filter(activo=True).exclude(fecha_baja=None).update(
        fecha_baja=None)

    for usuario in Usuario.objects.filter(activo=False):
        if usuario.fecha_baja is None:
            usuario.fecha_baja = usuario.fecha_alta
        elif usuario.fecha_baja < usuario.fecha_alta:
            usuario.fecha_alta = usuario.fecha_baja
        usuario.save(update_fields=['fecha_alta', 'fecha_baja'])

    forzar_chequeo_de_claves(schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0005_unaccent_y_clave_normalizada'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='perfilpermiso',
            name='perfil_permiso_unico',
        ),
        migrations.AddField(
            model_name='perfil',
            name='borrado_logico',
            field=models.BooleanField(default=False, verbose_name='Borrado lógico'),
        ),
        migrations.AddField(
            model_name='permiso',
            name='borrado_logico',
            field=models.BooleanField(default=False, verbose_name='Borrado lógico'),
        ),
        migrations.AddField(
            model_name='usuario',
            name='activo',
            field=models.BooleanField(default=True, verbose_name='Activo'),
        ),
        migrations.RunPython(
            copiar_baja_logica_a_activo,
            reverse_code=volver_a_borrado_logico,
        ),
        migrations.RemoveField(
            model_name='usuario',
            name='borrado_logico',
        ),
        migrations.AddField(
            model_name='usuario',
            name='fecha_alta',
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Fecha de alta'),
        ),
        migrations.AddField(
            model_name='usuario',
            name='usuario_modificacion',
            field=models.ForeignKey(blank=True, db_column='usuario_modificacion', db_constraint=False, editable=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='usuarios_modificados', to='usuarios.usuario', verbose_name='Modificado por'),
        ),
        migrations.AlterField(
            model_name='perfil',
            name='descripcion',
            field=models.CharField(blank=True, db_column='descripcion_perfil', max_length=120, null=True, verbose_name='Descripción'),
        ),
        migrations.AlterField(
            model_name='perfil',
            name='fecha_modificacion',
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Última modificación'),
        ),
        migrations.AlterField(
            model_name='perfil',
            name='nombre',
            field=models.CharField(db_column='nombre_perfil', max_length=20, verbose_name='Nombre del perfil'),
        ),
        migrations.AlterField(
            model_name='perfil',
            name='usuario_modificacion',
            field=models.ForeignKey(blank=True, db_column='usuario_modificacion', db_constraint=False, editable=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='perfiles_modificados', to='usuarios.usuario', verbose_name='Modificado por'),
        ),
        migrations.AlterField(
            model_name='perfilpermiso',
            name='perfil',
            field=models.ForeignKey(db_column='id_perfil', on_delete=django.db.models.deletion.CASCADE, to='usuarios.perfil', verbose_name='Perfil'),
        ),
        migrations.AlterField(
            model_name='perfilpermiso',
            name='permiso',
            field=models.ForeignKey(db_column='id_permiso', on_delete=django.db.models.deletion.CASCADE, to='usuarios.permiso', verbose_name='Permiso'),
        ),
        migrations.AlterField(
            model_name='permiso',
            name='codigo',
            field=models.SlugField(db_column='codigo_permiso', max_length=40, verbose_name='Código'),
        ),
        migrations.AlterField(
            model_name='permiso',
            name='descripcion',
            field=models.CharField(blank=True, db_column='descripcion_permiso', max_length=120, null=True, verbose_name='Descripción'),
        ),
        migrations.AlterField(
            model_name='permiso',
            name='fecha_modificacion',
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Última modificación'),
        ),
        migrations.AlterField(
            model_name='permiso',
            name='modulo',
            field=models.CharField(choices=[('USUARIOS', 'Usuarios'), ('PERFILES', 'Perfiles'), ('PERMISOS', 'Permisos'), ('PROVEEDORES', 'Proveedores'), ('UBICACIONES', 'Ubicaciones'), ('DATOS_MAESTROS', 'Datos maestros'), ('GENERAL', 'General')], default='GENERAL', max_length=40, verbose_name='Módulo'),
        ),
        migrations.AlterField(
            model_name='permiso',
            name='nombre',
            field=models.CharField(db_column='nombre_permiso', max_length=50, verbose_name='Nombre'),
        ),
        migrations.AlterField(
            model_name='permiso',
            name='usuario_modificacion',
            field=models.ForeignKey(blank=True, db_column='usuario_modificacion', db_constraint=False, editable=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='permisos_modificados', to='usuarios.usuario', verbose_name='Modificado por'),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='apellido',
            field=models.CharField(max_length=30, validators=[django.core.validators.RegexValidator(message='Sólo se admiten letras y espacios.', regex='^[A-Za-zÁÉÍÓÚáéíóúÑñÜü ]+$')], verbose_name='Apellido'),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='clave',
            field=models.CharField(db_column='contrasena_hash', editable=False, max_length=255, verbose_name='Clave (hash)'),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='correo',
            field=models.EmailField(max_length=254, verbose_name='Correo'),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='dni_usuario',
            field=models.CharField(max_length=8, validators=[django.core.validators.RegexValidator(message='El DNI debe tener 7 u 8 dígitos, sin puntos ni espacios.', regex='^\\d{7,8}$')], verbose_name='DNI'),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='fecha_ultima_modificacion',
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Última modificación'),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='id_perfil',
            field=models.ForeignKey(db_column='id_perfil', on_delete=django.db.models.deletion.RESTRICT, related_name='usuarios', to='usuarios.perfil', verbose_name='Perfil'),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='nombre',
            field=models.CharField(max_length=30, validators=[django.core.validators.RegexValidator(message='Sólo se admiten letras y espacios.', regex='^[A-Za-zÁÉÍÓÚáéíóúÑñÜü ]+$')], verbose_name='Nombre'),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='nombre_usuario',
            field=models.CharField(editable=False, max_length=35, verbose_name='Usuario'),
        ),
        migrations.RunPython(
            normalizar_datos, reverse_code=migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='perfil',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("trim(nombre_perfil) <> ''"), name='ck_perfiles_textos'),
        ),
        migrations.AddConstraint(
            model_name='perfil',
            constraint=models.UniqueConstraint(cafeto.restricciones.ClaveNormalizada('nombre'), name='uq_perfiles_nombre_norm'),
        ),
        migrations.AddConstraint(
            model_name='perfilpermiso',
            constraint=models.UniqueConstraint(fields=('perfil', 'permiso'), name='uq_perfiles_permisos'),
        ),
        migrations.AddConstraint(
            model_name='permiso',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("codigo_permiso ~ '^[a-z][a-z0-9_]*$'"), name='ck_permisos_codigo'),
        ),
        migrations.AddConstraint(
            model_name='permiso',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("trim(codigo_permiso) <> '' AND trim(nombre_permiso) <> '' AND trim(modulo) <> ''"), name='ck_permisos_textos'),
        ),
        migrations.AddConstraint(
            model_name='permiso',
            constraint=models.UniqueConstraint(fields=('codigo',), name='uq_permisos_codigo_permiso'),
        ),
        migrations.AddConstraint(
            model_name='usuario',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("dni_usuario ~ '^[0-9]{7,8}$'"), name='ck_usuarios_dni'),
        ),
        migrations.AddConstraint(
            model_name='usuario',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL('(activo AND fecha_baja IS NULL) OR (NOT activo AND fecha_baja IS NOT NULL)'), name='ck_usuarios_baja'),
        ),
        migrations.AddConstraint(
            model_name='usuario',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL('fecha_baja IS NULL OR fecha_baja >= fecha_alta'), name='ck_usuarios_fechas'),
        ),
        migrations.AddConstraint(
            model_name='usuario',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("trim(dni_usuario) <> '' AND trim(apellido) <> '' AND trim(nombre) <> '' AND trim(correo) <> '' AND trim(nombre_usuario) <> '' AND trim(contrasena_hash) <> ''"), name='ck_usuarios_textos'),
        ),
        migrations.AddConstraint(
            model_name='usuario',
            constraint=models.UniqueConstraint(fields=('dni_usuario',), name='uq_usuarios_dni_usuario'),
        ),
        migrations.AddConstraint(
            model_name='usuario',
            constraint=models.UniqueConstraint(fields=('nombre_usuario',), name='uq_usuarios_nombre_usuario'),
        ),
        migrations.AlterModelTable(
            name='perfil',
            table='perfiles',
        ),
        migrations.AlterModelTable(
            name='perfilpermiso',
            table='perfiles_permisos',
        ),
        migrations.AlterModelTable(
            name='permiso',
            table='permisos',
        ),
        migrations.AlterModelTable(
            name='usuario',
            table='usuarios',
        ),
    ]
