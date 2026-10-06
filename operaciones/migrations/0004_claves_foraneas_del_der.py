"""Reemplaza las claves foráneas de Django por las que declara el DER.

El problema
-----------
Django crea las claves foráneas a su manera: les pone un nombre generado
(``productos_id_rubro_8f3a1c2b_fk_rubros_id_rubro``) y NO les declara
acciones referenciales, porque resuelve ``on_delete`` en Python, antes de
mandar el DELETE.

Eso alcanza mientras todo pase por el ORM. Acá no es el caso: los
procedimientos de ``04_procedimientos.sql`` trabajan en SQL puro, y las
reglas de integridad referencial del DER —``ON DELETE RESTRICT``,
``ON DELETE CASCADE``, ``ON DELETE SET NULL``, ``ON UPDATE CASCADE``—
tienen que estar en la base para valer también por ese camino.

La solución
-----------
Se borran las claves foráneas que creó Django y se vuelven a crear con las
sentencias EXACTAS de ``01_esquema.sql``: mismo nombre, mismas acciones.
Las sentencias no se copian: se leen del archivo, que sigue siendo la
única fuente. Si mañana cambia una regla de integridad en el DER, se
regenera el esquema y se vuelve a correr esta migración.

Lo que Django pierde con esto: nada. ``on_delete`` sigue funcionando igual
—lo aplica en Python antes de llegar a la base— y ahora además hay una
segunda barrera para lo que no pasa por el ORM.
"""
import re
from pathlib import Path

from django.db import migrations

from operaciones.esquema import ejecutar_salteando_lo_que_no_existe

# Dónde están los guiones SQL, en orden de preferencia.
#
# PRIMERO la copia que vive DENTRO del proyecto: es la que hace que este
# repositorio se pueda clonar y levantar solo. La escribe
# "02 - Scripts/sync_bd_django.py" desde "04 - Base de Datos", que sigue
# siendo la fuente; verificar_sync_bd.py comprueba que no se desincronicen.
#
# Las otras dos son compatibilidad hacia atrás: /opt/bd fue el punto de
# montaje que usaba docker-compose, y la carpeta hermana sirve cuando se
# corre fuera de Docker desde el repositorio completo.
RUTAS_POSIBLES = [
    Path(__file__).resolve().parents[2] / 'base_de_datos',
    Path('/opt/bd'),
    Path(__file__).resolve().parents[3] / '04 - Base de Datos',
]

# Las sentencias de clave foránea del esquema, tal como las escribe
# gen_ddl.py: "ALTER TABLE x ADD CONSTRAINT fk_... FOREIGN KEY ... ;"
PATRON_FK = re.compile(
    r'ALTER TABLE \w+ ADD CONSTRAINT fk_\w+ FOREIGN KEY .*?;',
    re.DOTALL)

# Borra toda clave foránea de las tablas de negocio. Se excluyen las de
# Django (auth_, django_) para no tocar lo que el framework administra.
BORRAR_LAS_DE_DJANGO = """
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT conrelid::regclass AS tabla, conname AS restriccion
          FROM pg_constraint
         WHERE contype = 'f'
           AND connamespace = 'public'::regnamespace
           AND conrelid::regclass::text NOT LIKE 'django\\_%'
           AND conrelid::regclass::text NOT LIKE 'auth\\_%'
           AND conrelid::regclass::text NOT LIKE 'hist\\_%'
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',
                       r.tabla, r.restriccion);
    END LOOP;
END $$;
"""


def esquema():
    for ruta in RUTAS_POSIBLES:
        archivo = ruta / '01_esquema.sql'
        if archivo.is_file():
            return archivo.read_text(encoding='utf-8')
    raise FileNotFoundError(
        'No encuentro 01_esquema.sql. Tiene que estar en '
        '"base_de_datos/", dentro del proyecto.')


def poner_las_del_der(apps, schema_editor):
    sentencias = PATRON_FK.findall(esquema())
    if not sentencias:
        raise RuntimeError(
            'No encontré ninguna sentencia de clave foránea en '
            '01_esquema.sql: revisá que el archivo esté completo.')

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(BORRAR_LAS_DE_DJANGO)
        # El esquema que se lee es el de HOY; esta migración corre en su
        # punto del historial. Las claves de una tabla o una columna que el
        # DER renombró después se saltean acá y las repone entera la
        # migración que introdujo ese renombre.
        ejecutar_salteando_lo_que_no_existe(cursor, sentencias)


def volver_a_las_de_django(apps, schema_editor):
    """Deshace el cambio dejando que Django las vuelva a crear.

    Se borran las del DER; las de Django las repone el propio framework
    al revertir las migraciones que crearon las tablas.
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(BORRAR_LAS_DE_DJANGO)


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0003_logica_de_la_base'),
        ('maestros', '0003_permisos_datos_maestros'),
        ('proveedores', '0004_jurisdicciones_argentinas'),
    ]

    operations = [
        migrations.RunPython(poner_las_del_der,
                             reverse_code=volver_a_las_de_django),
    ]
