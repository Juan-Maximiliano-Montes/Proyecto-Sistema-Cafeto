"""Punto de partida del esquema: lo crea Django y nada más.

El historial arranca acá. No hay guiones SQL externos, ni triggers, ni
procedimientos almacenados: las tablas, sus CHECK, sus UNIQUE y sus
índices salen de los modelos, y toda la lógica transaccional y de
validación vive en Python (``clean()`` de cada modelo, los mixins de
``cafeto/reglas.py`` y los servicios de ``operaciones/``).

Lo único que no sale de un modelo va primero, en ``FUNCIONES_BASE``: la
extensión ``unaccent`` y la función ``f_clave_normalizada()`` (DD-50).
Django no tiene forma de declarar ninguna de las dos, y los índices
únicos por clave normalizada de ``usuarios``, ``maestros`` y
``proveedores`` las llaman: sin la función, el
``CREATE UNIQUE INDEX ... (f_clave_normalizada(nombre))`` falla.

Por eso van como PRIMERA operación de esta migración, que es la raíz del
grafo: las otras tres apps dependen de ``usuarios.0001_initial``, así que
cuando les toca crear sus índices la función ya existe.

``IMMUTABLE`` es la parte que importa: PostgreSQL sólo deja indexar
expresiones inmutables. ``unaccent`` sin argumentos no lo es —depende del
diccionario configurado—, así que se la envuelve pasándole el diccionario
explícito, que es el patrón recomendado.
"""
import cafeto.restricciones
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

CREAR_FUNCIONES_BASE = """
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION f_clave_normalizada(p_texto TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT lower(unaccent('unaccent', regexp_replace(btrim(p_texto), '[[:space:]]+', ' ', 'g')))
$$;

COMMENT ON FUNCTION f_clave_normalizada(TEXT) IS
  'DD-50: clave de comparación de los nombres de catálogo: minúsculas, sin acentos y con los espacios internos colapsados.';
"""

# La extensión NO se borra al revertir: puede estar usándola algo más.
BORRAR_FUNCIONES_BASE = """
DROP FUNCTION IF EXISTS f_clave_normalizada(TEXT);
"""


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.RunSQL(sql=CREAR_FUNCIONES_BASE,
                          reverse_sql=BORRAR_FUNCIONES_BASE),
        migrations.CreateModel(
            name='Perfil',
            fields=[
                ('id_perfil', models.AutoField(primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(db_column='nombre_perfil', max_length=20, verbose_name='Nombre del perfil')),
                ('descripcion', models.CharField(blank=True, db_column='descripcion_perfil', max_length=120, null=True, verbose_name='Descripción')),
                ('es_sistema', models.BooleanField(db_column='es_perfil_sistema', default=False, editable=False, verbose_name='Perfil del sistema')),
                ('borrado_logico', models.BooleanField(default=False, verbose_name='Borrado lógico')),
                ('fecha_modificacion', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Última modificación')),
            ],
            options={
                'verbose_name': 'Perfil',
                'verbose_name_plural': 'Perfiles',
                'db_table': 'perfiles',
                'ordering': ['nombre'],
            },
        ),
        migrations.CreateModel(
            name='Usuario',
            fields=[
                ('id_usuario', models.AutoField(primary_key=True, serialize=False, verbose_name='ID')),
                ('dni_usuario', models.CharField(max_length=8, validators=[django.core.validators.RegexValidator(message='El DNI debe tener 7 u 8 dígitos, sin puntos ni espacios.', regex='^\\d{7,8}$')], verbose_name='DNI')),
                ('apellido', models.CharField(max_length=50, validators=[django.core.validators.RegexValidator(message='Sólo se admiten letras y espacios.', regex='^[A-Za-zÁÉÍÓÚáéíóúÑñÜü ]+$')], verbose_name='Apellido')),
                ('nombre', models.CharField(max_length=50, validators=[django.core.validators.RegexValidator(message='Sólo se admiten letras y espacios.', regex='^[A-Za-zÁÉÍÓÚáéíóúÑñÜü ]+$')], verbose_name='Nombre')),
                ('correo', models.EmailField(max_length=254, verbose_name='Correo')),
                ('nombre_usuario', models.CharField(editable=False, max_length=35, verbose_name='Usuario')),
                ('clave', models.CharField(db_column='contrasena_hash', editable=False, max_length=255, verbose_name='Clave (hash)')),
                ('debe_cambiar_clave', models.BooleanField(default=True, verbose_name='Debe cambiar la clave')),
                ('activo', models.BooleanField(default=True, verbose_name='Activo')),
                ('fecha_alta', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Fecha de alta')),
                ('fecha_ultima_modificacion', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Última modificación')),
                ('fecha_baja', models.DateTimeField(blank=True, null=True, verbose_name='Fecha de baja')),
                ('id_perfil', models.ForeignKey(db_column='id_perfil', on_delete=django.db.models.deletion.RESTRICT, related_name='usuarios', to='usuarios.perfil', verbose_name='Perfil')),
                ('usuario_modificacion', models.ForeignKey(blank=True, db_column='usuario_modificacion', db_constraint=False, editable=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='usuarios_modificados', to='usuarios.usuario', verbose_name='Modificado por')),
            ],
            options={
                'verbose_name': 'Usuario',
                'verbose_name_plural': 'Usuarios',
                'db_table': 'usuarios',
                'ordering': ['apellido', 'nombre'],
            },
        ),
        migrations.CreateModel(
            name='Permiso',
            fields=[
                ('id_permiso', models.AutoField(primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo', models.SlugField(db_column='codigo_permiso', max_length=40, verbose_name='Código')),
                ('nombre', models.CharField(db_column='nombre_permiso', max_length=50, verbose_name='Nombre')),
                ('descripcion', models.CharField(blank=True, db_column='descripcion_permiso', max_length=120, null=True, verbose_name='Descripción')),
                ('modulo', models.CharField(choices=[('USUARIOS', 'Usuarios'), ('PERFILES', 'Perfiles'), ('PERMISOS', 'Permisos'), ('PROVEEDORES', 'Proveedores'), ('UBICACIONES', 'Ubicaciones'), ('DATOS_MAESTROS', 'Datos maestros'), ('GENERAL', 'General')], default='GENERAL', max_length=40, verbose_name='Módulo')),
                ('borrado_logico', models.BooleanField(default=False, verbose_name='Borrado lógico')),
                ('fecha_modificacion', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Última modificación')),
                ('usuario_modificacion', models.ForeignKey(blank=True, db_column='usuario_modificacion', db_constraint=False, editable=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='permisos_modificados', to='usuarios.usuario', verbose_name='Modificado por')),
            ],
            options={
                'verbose_name': 'Permiso',
                'verbose_name_plural': 'Permisos',
                'db_table': 'permisos',
                'ordering': ['modulo', 'nombre'],
            },
        ),
        migrations.CreateModel(
            name='PerfilPermiso',
            fields=[
                ('id_perfil_permiso', models.AutoField(primary_key=True, serialize=False, verbose_name='ID')),
                ('fecha_asignacion', models.DateTimeField(auto_now_add=True, verbose_name='Fecha de asignación')),
                ('perfil', models.ForeignKey(db_column='id_perfil', on_delete=django.db.models.deletion.CASCADE, to='usuarios.perfil', verbose_name='Perfil')),
                ('permiso', models.ForeignKey(db_column='id_permiso', on_delete=django.db.models.deletion.CASCADE, to='usuarios.permiso', verbose_name='Permiso')),
            ],
            options={
                'verbose_name': 'Permiso de perfil',
                'verbose_name_plural': 'Permisos de perfil',
                'db_table': 'perfiles_permisos',
            },
        ),
        migrations.AddField(
            model_name='perfil',
            name='permisos',
            field=models.ManyToManyField(blank=True, related_name='perfiles', through='usuarios.PerfilPermiso', to='usuarios.permiso'),
        ),
        migrations.AddField(
            model_name='perfil',
            name='usuario_modificacion',
            field=models.ForeignKey(blank=True, db_column='usuario_modificacion', db_constraint=False, editable=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='perfiles_modificados', to='usuarios.usuario', verbose_name='Modificado por'),
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
            model_name='perfilpermiso',
            constraint=models.UniqueConstraint(fields=('perfil', 'permiso'), name='uq_perfiles_permisos'),
        ),
        migrations.AddConstraint(
            model_name='perfil',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("trim(nombre_perfil) <> ''"), name='ck_perfiles_textos'),
        ),
        migrations.AddConstraint(
            model_name='perfil',
            constraint=models.UniqueConstraint(cafeto.restricciones.ClaveNormalizada('nombre'), name='uq_perfiles_nombre_norm'),
        ),
    ]
