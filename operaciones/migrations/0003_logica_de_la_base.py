"""Instala la lógica de negocio que vive en la base.

Reparto de responsabilidades de la Fase 4 (ver DD "Django dueño de las
tablas, BD dueña de la lógica"):

  * Django crea y migra las TABLAS, con sus columnas, claves, CHECK,
    UNIQUE e índices.
  * ``04 - Base de Datos`` aporta la LÓGICA: los triggers que defienden
    las reglas que un CHECK no alcanza a expresar (02), las tablas y los
    procedimientos del pasaje a histórico (03) y los procedimientos de
    negocio (04).

Esta migración lee esos tres scripts y los ejecuta tal cual. No los copia
ni los reescribe: el archivo SQL sigue siendo la única fuente, generada y
revisada en la Fase 1. Si mañana cambia una regla, se corrige el script y
se vuelve a correr esta migración.

Los scripts entran al contenedor por el montaje de sólo lectura
``../04 - Base de Datos:/opt/bd:ro`` que declara docker-compose.yml.
"""
from pathlib import Path

from django.db import migrations

# En el contenedor, el montaje. Fuera de Docker, la carpeta del repo.
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

# En este orden: los triggers antes que los procedimientos, porque algunos
# procedimientos se apoyan en las funciones que declara 02.
SCRIPTS = ['02_triggers.sql', '03_historico.sql', '04_procedimientos.sql']


def carpeta_de_scripts():
    for ruta in RUTAS_POSIBLES:
        if (ruta / SCRIPTS[0]).is_file():
            return ruta
    raise FileNotFoundError(
        'No encuentro los scripts SQL de la base. Esperaba hallarlos en '
        + ' o en '.join(str(r) for r in RUTAS_POSIBLES)
        + '. Dentro de Docker los monta docker-compose.yml '
          '("../04 - Base de Datos:/opt/bd:ro"); fuera de Docker tienen que '
          'estar en la carpeta del proyecto.')


def instalar(apps, schema_editor):
    """Instala la lógica, salteando lo que todavía no puede existir.

    Los guiones que se leen son los de HOY; esta migración corre en su punto
    del historial. Lo que hable de una tabla o una columna que el DER
    renombró después se saltea acá y lo repone entera la migración que
    introdujo ese renombre.
    """
    from operaciones.esquema import instalar_logica

    salteados = instalar_logica(schema_editor, tolerante=True)
    for nombre in salteados:
        print('    [~] %s se saltea: nombra algo que todavía no existe en '
              'este punto del historial.' % nombre)


def desinstalar(apps, schema_editor):
    """Deshace lo que instalan los scripts.

    Se borran las funciones con CASCADE, que se lleva puestos los triggers
    que las usan, y las tablas y vistas del histórico. Las tablas de
    negocio no se tocan: las administra Django.
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            DO $$
            DECLARE r RECORD;
            BEGIN
                -- Vistas y tablas del histórico (03_historico.sql)
                FOR r IN SELECT table_name FROM information_schema.views
                          WHERE table_schema = 'public'
                            AND (table_name LIKE 'v\\_%' OR table_name LIKE 'hist\\_%')
                LOOP
                    EXECUTE format('DROP VIEW IF EXISTS %I CASCADE', r.table_name);
                END LOOP;

                FOR r IN SELECT table_name FROM information_schema.tables
                          WHERE table_schema = 'public'
                            AND table_type = 'BASE TABLE'
                            AND table_name LIKE 'hist\\_%'
                LOOP
                    EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', r.table_name);
                END LOOP;

                -- Funciones y procedimientos propios (fn_*, sp_*). El
                -- CASCADE arrastra los triggers que las invocan.
                FOR r IN SELECT p.oid::regprocedure AS firma, p.prokind
                           FROM pg_proc p
                           JOIN pg_namespace n ON n.oid = p.pronamespace
                          WHERE n.nspname = 'public'
                            AND (p.proname LIKE 'fn\\_%' OR p.proname LIKE 'sp\\_%')
                LOOP
                    IF r.prokind = 'p' THEN
                        EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.firma);
                    ELSE
                        EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.firma);
                    END IF;
                END LOOP;
            END $$;
        """)


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0002_columna_generada_y_defaults'),
    ]

    operations = [
        migrations.RunPython(instalar, reverse_code=desinstalar),
    ]
