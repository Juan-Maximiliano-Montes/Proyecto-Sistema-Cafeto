"""Lo único del esquema que no sale de un modelo: ``f_clave_normalizada``.

Es la raíz del grafo de migraciones: los índices únicos por clave
normalizada de todos los módulos (``CREATE UNIQUE INDEX ...
(f_clave_normalizada(nombre))``) la llaman, así que la migración 0001 de
cada app depende de ésta.

La clave de comparación de un nombre de catálogo (DD-50):

    minúsculas · sin acentos · todo lo que no es letra ni número se vuelve
    un espacio · espacios colapsados y recortados

    Coca Cola · COCA  COLA · Coca-Cola · Cocá.Cola   ->  'coca cola'

``IMMUTABLE`` es la parte que importa: PostgreSQL sólo deja indexar
expresiones inmutables. ``unaccent`` sin argumentos no lo es —depende del
diccionario configurado—, así que se le pasa el diccionario explícito.
"""
from django.db import migrations

CREAR = r"""
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION f_clave_normalizada(p_texto TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT lower(btrim(regexp_replace(
        unaccent('unaccent', p_texto), '[^[:alnum:]]+', ' ', 'g')))
$$;

COMMENT ON FUNCTION f_clave_normalizada(TEXT) IS
  'DD-50: clave de comparación de los nombres de catálogo: minúsculas, sin acentos, sin puntuación y con los espacios colapsados.';
"""

# La extensión NO se borra al revertir: puede estar usándola algo más.
BORRAR = 'DROP FUNCTION IF EXISTS f_clave_normalizada(TEXT);'


class Migration(migrations.Migration):

    initial = True
    dependencies = []
    operations = [migrations.RunSQL(sql=CREAR, reverse_sql=BORRAR)]
