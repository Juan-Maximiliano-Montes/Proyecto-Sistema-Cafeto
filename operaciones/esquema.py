"""Lee ``04 - Base de Datos/01_esquema.sql`` y extrae lo que Django no declara.

Hay dos cosas del esquema que el ORM no sabe poner y que las migraciones
tienen que aplicar a mano:

  * los **DEFAULT de columna**: Django los resuelve en Python al armar el
    INSERT y deja la columna sin default en PostgreSQL. Alcanza mientras
    todo entre por el ORM, y acá no es el caso: los procedimientos de
    ``04_procedimientos.sql`` insertan en SQL puro y cuentan con ellos.
  * las **claves foráneas con su nombre y sus acciones**: Django las crea
    con un nombre generado y sin ``ON DELETE`` / ``ON UPDATE``, porque
    resuelve ``on_delete`` en Python antes de mandar la sentencia.

Las primeras migraciones de la Fase 4 llevaban esta lógica copiada adentro.
Al agregar una entidad nueva (MARCAS) hubo que repetirla, así que vive acá:
una sola fuente, que además se lee del esquema y no de una lista a mano.
"""
import re
from pathlib import Path

from django.db.utils import ProgrammingError

# Dentro de Docker lo monta docker-compose.yml; fuera, está en el repo.
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
    Path(__file__).resolve().parents[1] / 'base_de_datos',
    Path('/opt/bd'),
    Path(__file__).resolve().parents[2] / '04 - Base de Datos',
]

_TABLA = re.compile(r'CREATE TABLE (\w+) \((.*?)\n\);', re.S)
_DEFAULT = re.compile(r'\bDEFAULT\s+(.+)$')
_FK = re.compile(
    r'ALTER TABLE \w+ ADD CONSTRAINT fk_\w+ FOREIGN KEY .*?;', re.S)

# Borra toda clave foránea de las tablas de negocio, para volver a crearlas
# como las define el DER. Se excluyen las de Django (auth_, django_), que
# administra el framework, y las del histórico.
BORRAR_CLAVES_FORANEAS = r"""
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT conrelid::regclass AS tabla, conname AS restriccion
          FROM pg_constraint
         WHERE contype = 'f'
           AND connamespace = 'public'::regnamespace
           AND conrelid::regclass::text NOT LIKE 'django\_%'
           AND conrelid::regclass::text NOT LIKE 'auth\_%'
           AND conrelid::regclass::text NOT LIKE 'hist\_%'
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',
                       r.tabla, r.restriccion);
    END LOOP;
END $$;
"""


def texto_del_esquema():
    for ruta in RUTAS_POSIBLES:
        archivo = ruta / '01_esquema.sql'
        if archivo.is_file():
            return archivo.read_text(encoding='utf-8')
    raise FileNotFoundError(
        'No encuentro 01_esquema.sql. Dentro de Docker lo monta '
        'docker-compose.yml en /opt/bd; fuera, tiene que estar en '
        '"04 - Base de Datos" al lado de Proyecto-Cafeto.')


def defaults_del_esquema():
    """[(tabla, columna, expresión), ...] de cada columna con DEFAULT.

    Se saltean las claves primarias: Django ya las crea como IDENTITY.
    """
    salida = []
    for tabla, cuerpo in _TABLA.findall(texto_del_esquema()):
        for linea in cuerpo.split('\n'):
            linea = linea.strip().rstrip(',')
            if not linea or linea.startswith('CONSTRAINT'):
                continue
            encontrado = _DEFAULT.search(linea)
            if not encontrado:
                continue
            expresion = encontrado.group(1).strip()
            if expresion == 'AS IDENTITY':
                continue
            salida.append((tabla, linea.split()[0], expresion))
    return salida


def sentencias_de_claves_foraneas():
    """Las sentencias ``ALTER TABLE ... ADD CONSTRAINT fk_...`` del esquema."""
    sentencias = _FK.findall(texto_del_esquema())
    if not sentencias:
        raise RuntimeError(
            'No encontré ninguna clave foránea en 01_esquema.sql: revisá que '
            'el archivo esté completo.')
    return sentencias


# Codigos SQLSTATE de PostgreSQL para "eso que nombras no existe".
TODAVIA_NO_EXISTE = (
    '42P01',  # undefined_table
    '42703',  # undefined_column
    '42704',  # undefined_object
)


def ejecutar_salteando_lo_que_no_existe(cursor, sentencias):
    """Corre las sentencias y saltea las que nombran algo inexistente.

    Una migracion lee el ``01_esquema.sql`` de HOY, pero corre en el punto
    del historial en que fue escrita. Si el DER renombro una tabla o una
    columna despues de eso, la sentencia habla de un objeto que en ese
    momento todavia no existe, y la migracion revienta al construir una
    base desde cero (por ejemplo, la de los tests).

    Saltearla es lo correcto y no deja la base incompleta: la migracion que
    introdujo el renombre vuelve a aplicar el juego entero de claves y
    defaults leyendo el mismo archivo, asi que el estado final es el que
    dice el esquema.

    Cada sentencia va en su propio SAVEPOINT: en PostgreSQL un error aborta
    la transaccion completa, y sin el punto de rescate no se podria seguir.

    Devuelve las sentencias salteadas, para poder informarlas.
    """
    salteadas = []
    for numero, sentencia in enumerate(sentencias):
        punto = 'esquema_%d' % numero
        cursor.execute('SAVEPOINT %s' % punto)
        try:
            cursor.execute(sentencia)
        except ProgrammingError as error:
            codigo = getattr(getattr(error, '__cause__', None), 'pgcode', None)
            if codigo not in TODAVIA_NO_EXISTE:
                raise
            cursor.execute('ROLLBACK TO SAVEPOINT %s' % punto)
            salteadas.append(sentencia)
        else:
            cursor.execute('RELEASE SAVEPOINT %s' % punto)
    return salteadas


def aplicar_defaults(schema_editor):
    sentencias = ['ALTER TABLE %s ALTER COLUMN %s SET DEFAULT %s'
                  % (tabla, columna, expresion)
                  for tabla, columna, expresion in defaults_del_esquema()]
    with schema_editor.connection.cursor() as cursor:
        ejecutar_salteando_lo_que_no_existe(cursor, sentencias)


def aplicar_claves_foraneas(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(BORRAR_CLAVES_FORANEAS)
        ejecutar_salteando_lo_que_no_existe(
            cursor, sentencias_de_claves_foraneas())


# ---------------------------------------------------------------------------
#  LA LÓGICA: triggers, histórico y procedimientos
# ---------------------------------------------------------------------------
# En este orden: los triggers antes que los procedimientos, porque algunos
# procedimientos se apoyan en las funciones que declara 02.
SCRIPTS_DE_LOGICA = ['02_triggers.sql', '03_historico.sql',
                     '04_procedimientos.sql']

# Deja la base sin nada de lo que instalan esos guiones. Las funciones se
# borran con CASCADE, que se lleva puestos los triggers que las usan; por eso
# no hace falta nombrarlos uno por uno. Las tablas de negocio no se tocan:
# ésas las administra Django.
DESINSTALAR_LOGICA = r"""
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT table_name FROM information_schema.views
              WHERE table_schema = 'public'
                AND (table_name LIKE 'v\_%' OR table_name LIKE 'hist\_%')
    LOOP
        EXECUTE format('DROP VIEW IF EXISTS %I CASCADE', r.table_name);
    END LOOP;

    FOR r IN SELECT table_name FROM information_schema.tables
              WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                AND table_name LIKE 'hist\_%'
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', r.table_name);
    END LOOP;

    FOR r IN SELECT p.oid::regprocedure AS firma, p.prokind
               FROM pg_proc p
               JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname = 'public'
                -- Los ayudantes internos llevan guion bajo adelante
                -- (_sp_efectivo_sale). Sin ellos en la lista, el guion
                -- los volvia a crear con CREATE OR REPLACE, y PostgreSQL
                -- rechaza eso cuando cambio el nombre de un parametro.
                -- f_clave_normalizada NO entra: vive en 01_esquema.sql y
                -- de ella cuelgan los indices normalizados.
                AND (p.proname LIKE 'fn\_%' OR p.proname LIKE 'sp\_%'
                     OR p.proname LIKE '\_fn\_%' OR p.proname LIKE '\_sp\_%')
    LOOP
        IF r.prokind = 'p' THEN
            EXECUTE format('DROP PROCEDURE IF EXISTS %s CASCADE', r.firma);
        ELSE
            EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.firma);
        END IF;
    END LOOP;
END $$;
"""


def carpeta_de_scripts():
    for ruta in RUTAS_POSIBLES:
        if (ruta / SCRIPTS_DE_LOGICA[0]).is_file():
            return ruta
    raise FileNotFoundError(
        'No encuentro los guiones SQL de la base. Dentro de Docker los monta '
        'docker-compose.yml en /opt/bd.')


def desinstalar_logica(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(DESINSTALAR_LOGICA)


def instalar_logica(schema_editor, tolerante=False):
    """Borra y vuelve a instalar triggers, histórico y procedimientos.

    Se desinstala primero porque ``CREATE TRIGGER`` no es idempotente: si el
    trigger ya existe, falla. ``CREATE OR REPLACE FUNCTION`` sí lo es, pero
    con eso solo no alcanza para que una tabla NUEVA reciba sus triggers.
    Reinstalar todo es más simple que llevar la cuenta de qué falta, y deja
    la base exactamente como dicen los guiones.

    ``tolerante=True`` saltea el guion que nombre una tabla o una columna que
    TODAVÍA no existe, y devuelve la lista de los que salteó.

    Para qué sirve: los guiones que se leen son los de HOY, pero una
    migración vieja corre en el punto del historial en que fue escrita. Si el
    DER renombró algo después —``cajas`` a ``turnos_caja``, por ejemplo—, el
    cuerpo de ``sp_abrir_caja`` habla de una columna que en ese momento no
    existe, y armar la base desde cero (la de los tests) revienta.

    Saltearlo no deja la base a medias: la migración que introdujo el
    renombre vuelve a llamar acá, ya sin ``tolerante``, y entonces sí tiene
    que entrar todo. Si falla ahí, falla de verdad y hay que mirarlo.
    """
    carpeta = carpeta_de_scripts()
    desinstalar_logica(schema_editor)
    salteados = []
    with schema_editor.connection.cursor() as cursor:
        for numero, nombre in enumerate(SCRIPTS_DE_LOGICA):
            guion = (carpeta / nombre).read_text(encoding='utf-8')
            if not tolerante:
                cursor.execute(guion)
                continue
            punto = 'logica_%d' % numero
            cursor.execute('SAVEPOINT %s' % punto)
            try:
                cursor.execute(guion)
            except ProgrammingError as error:
                codigo = getattr(getattr(error, '__cause__', None), 'pgcode', None)
                if codigo not in TODAVIA_NO_EXISTE:
                    raise
                cursor.execute('ROLLBACK TO SAVEPOINT %s' % punto)
                salteados.append(nombre)
            else:
                cursor.execute('RELEASE SAVEPOINT %s' % punto)
    return salteados
