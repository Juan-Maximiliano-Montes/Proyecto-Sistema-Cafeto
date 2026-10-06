"""MARCAS como tabla propia y la presentación del producto como dato (DD-55).

Qué problema resuelve
---------------------
El nombre del producto venía guardando tres datos en un solo campo: la
marca, la denominación y la presentación ("Café molido La Morenita 500 g").
Eso rompe la primera forma normal y tiene una consecuencia concreta que se
vio usando el sistema: cargar "Café molido La morenita" existiendo
"Café molido La Morenita 500 g" NO se detectaba como duplicado, porque
como cadenas son distintas. Eran dos filas para el mismo artículo.

Qué cambia
----------
* Nace MARCAS, una maestra más, con su baja lógica y su sello de
  modificación como todas.
* PRODUCTOS pasa a tener ``id_marca`` y ``contenido``. La denominación
  queda sola en ``nombre_producto``.
* La identidad del producto pasa a ser la TERNA **marca + nombre +
  presentación** (``uq_productos_identidad``), en lugar del nombre suelto.
  Con esa regla el caso de arriba se rechaza.

Sobre los datos que ya estaban
------------------------------
No se adivina la marca a partir del nombre viejo. Un separador automático
("la última palabra es la marca") acierta a veces y ensucia el resto, y
acá los datos son del negocio. Todos los productos existentes quedan
asignados a la marca **"Sin marca"** y conservan su nombre tal cual: el
Administrador los reclasifica desde la pantalla, viendo cada caso. La
migración deja el sistema consistente, no adivina.
"""
from decimal import Decimal

import cafeto.restricciones
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

# Marca de los productos a granel y de elaboración propia: el café que se
# muele en el local no tiene marca de fábrica, pero igual necesita una para
# que la identidad (marca + nombre + presentación) esté siempre completa.
MARCA_GENERICA = 'Sin marca'


def crear_marca_generica(apps, schema_editor):
    Marca = apps.get_model('maestros', 'Marca')
    Marca.objects.get_or_create(
        nombre_marca=MARCA_GENERICA,
        defaults={'descripcion_marca':
                  'Productos a granel y de elaboración propia'})


def borrar_marca_generica(apps, schema_editor):
    Marca = apps.get_model('maestros', 'Marca')
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET LOCAL cafeto.pasaje_historico = 'on'")
        Marca.objects.filter(nombre_marca=MARCA_GENERICA).delete()


def asignar_marca_generica(apps, schema_editor):
    """Deja todos los productos existentes en "Sin marca".

    Es a propósito: ver el encabezado del archivo. Lo que importa es que
    ninguna fila quede sin marca, para poder exigir la columna NOT NULL y
    la identidad única.
    """
    Marca = apps.get_model('maestros', 'Marca')
    Producto = apps.get_model('maestros', 'Producto')
    generica = Marca.objects.get(nombre_marca=MARCA_GENERICA)
    Producto.objects.filter(id_marca__isnull=True).update(id_marca=generica)

    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0007_nombres_de_cincuenta'),
        ('maestros', '0003_permisos_datos_maestros'),
    ]

    operations = [
        # ---------- 1. La tabla MARCAS ----------
        migrations.CreateModel(
            name='Marca',
            fields=[
                ('borrado_logico', models.BooleanField(default=False, verbose_name='Borrado lógico')),
                ('fecha_modificacion', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Última modificación')),
                ('id_marca', models.AutoField(primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre_marca', models.CharField(max_length=50, verbose_name='Marca')),
                ('descripcion_marca', models.CharField(blank=True, max_length=120, null=True, verbose_name='Descripción')),
            ],
            options={
                'verbose_name': 'Marca',
                'verbose_name_plural': 'Marcas',
                'db_table': 'marcas',
                'ordering': ['nombre_marca'],
            },
        ),
        migrations.AddField(
            model_name='marca',
            name='usuario_modificacion',
            field=models.ForeignKey(blank=True, db_column='usuario_modificacion', db_constraint=False, editable=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='marcas_modificadas', to='usuarios.usuario', verbose_name='Modificado por'),
        ),
        migrations.AddConstraint(
            model_name='marca',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("trim(nombre_marca) <> ''"), name='ck_marcas_textos'),
        ),
        migrations.AddConstraint(
            model_name='marca',
            constraint=models.UniqueConstraint(cafeto.restricciones.ClaveNormalizada('nombre_marca'), name='uq_marcas_nombre_norm'),
        ),

        # ---------- 2. La marca que recibe lo que ya existe ----------
        migrations.RunPython(crear_marca_generica,
                             reverse_code=borrar_marca_generica),

        # ---------- 3. El nombre deja de cargar marca y presentación ----------
        migrations.RemoveConstraint(
            model_name='producto',
            name='uq_productos_nombre_norm',
        ),
        migrations.AddField(
            model_name='producto',
            name='contenido',
            field=models.DecimalField(decimal_places=3, default=Decimal('1'), max_digits=10, verbose_name='Contenido'),
        ),
        migrations.AlterField(
            model_name='producto',
            name='nombre_producto',
            field=models.CharField(help_text='Sin la marca ni el contenido: van en sus propios campos.', max_length=50, verbose_name='Nombre del producto'),
        ),

        # ---------- 4. La marca del producto: primero opcional, se llena,
        #              y recién entonces se exige ----------
        migrations.AddField(
            model_name='producto',
            name='id_marca',
            field=models.ForeignKey(db_column='id_marca', null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='productos', to='maestros.marca', verbose_name='Marca'),
        ),
        migrations.RunPython(asignar_marca_generica,
                             reverse_code=migrations.RunPython.noop),
        migrations.AlterField(
            model_name='producto',
            name='id_marca',
            field=models.ForeignKey(db_column='id_marca', on_delete=django.db.models.deletion.RESTRICT, related_name='productos', to='maestros.marca', verbose_name='Marca'),
        ),

        # ---------- 5. Las reglas nuevas, ya con las columnas pobladas ----------
        migrations.AddConstraint(
            model_name='producto',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL('contenido > 0'), name='ck_productos_contenido'),
        ),
        migrations.AddConstraint(
            model_name='producto',
            constraint=models.UniqueConstraint(models.F('id_marca'), cafeto.restricciones.ClaveNormalizada('nombre_producto'), models.F('contenido'), cafeto.restricciones.ClaveNormalizada('unidad_medida'), name='uq_productos_identidad'),
        ),
    ]
