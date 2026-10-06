"""Extensión unaccent y función f_clave_normalizada (DD-50).

Va PRIMERO y sola, porque los índices únicos por clave normalizada de
todas las apps la llaman: si no existe la función, el
``CREATE UNIQUE INDEX ... (f_clave_normalizada(nombre))`` falla.

Es una de las dos excepciones que la Fase 4 crea con RunSQL en lugar de
declararlas en un modelo: Django no tiene forma de pedir una extensión de
PostgreSQL ni de definir una función SQL propia.

``IMMUTABLE`` es la parte que importa: PostgreSQL sólo deja indexar
expresiones inmutables. ``unaccent`` sin argumentos no lo es (depende del
diccionario que esté configurado), así que se la envuelve pasándole el
diccionario explícito, que es el patrón recomendado.
"""
from django.db import migrations


CREAR = """
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION f_clave_normalizada(p_texto TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT lower(unaccent('unaccent', regexp_replace(btrim(p_texto), '[[:space:]]+', ' ', 'g')))
$$;

COMMENT ON FUNCTION f_clave_normalizada(TEXT) IS
  'DD-50: clave de comparación de los nombres de catálogo: minúsculas, sin acentos y con los espacios internos colapsados.';
"""

# No se borra la extensión: puede estar usándola algo más en la base.
DESHACER = """
DROP FUNCTION IF EXISTS f_clave_normalizada(TEXT);
"""


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0004_permisos_catalogo_solo_lectura'),
    ]

    operations = [
        migrations.RunSQL(sql=CREAR, reverse_sql=DESHACER),
    ]
