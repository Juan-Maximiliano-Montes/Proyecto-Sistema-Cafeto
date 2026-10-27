"""La clave normalizada pasa a ignorar también la puntuación (DD-50).

El agujero
----------
``f_clave_normalizada`` bajaba a minúsculas, sacaba los acentos y
colapsaba los espacios, pero dejaba la puntuación tal cual. Resultado:

    'Coca Cola'  ->  'coca cola'
    'Coca-cola'  ->  'coca-cola'      <-- clave distinta: ENTRABAN LAS DOS

Un guion, un punto o un paréntesis no distinguen una marca de otra, y es
justo la variante que más se cuela al cargar a mano. Ahora todo lo que no
es letra ni número se vuelve separador::

    Coca Cola · coca cola · COCA  COLA · Coca-Cola · Coca.Cola
    · Cocá Cola · 3M (Argentina)/3m argentina   ->  una sola clave

Lo que NO cambia: 'coca' sigue siendo distinto de 'coca cola'. Son
nombres distintos; un nombre más corto no es un duplicado del más largo.

Por qué hay que reconstruir los índices
---------------------------------------
Once índices únicos están construidos SOBRE esta función. Un índice de
expresión guarda el resultado ya calculado: si se reemplaza la función sin
tocarlos, siguen conteniendo las claves viejas y dejan de coincidir con lo
que la función devuelve ahora. PostgreSQL no avisa — simplemente el índice
miente. Así que se los baja, se cambia la función y se los vuelve a crear.

No se los nombra a mano: se leen de ``pg_indexes`` y se recrean con su
propia definición, así la migración sigue siendo correcta si mañana hay
doce en vez de once.

Antes de todo eso se revisa que los datos actuales no choquen bajo la
clave NUEVA: dos filas que hoy conviven ("Coca Cola" y "Coca-cola") a
partir de ahora son la misma, y el índice no se podría crear. No se
arreglan solas a propósito: elegir cuál sobrevive es del negocio.
"""
from collections import defaultdict

from django.db import migrations

# (app, modelo, campo del nombre, campos que acompañan en la identidad)
#
# Los ámbitos importan: una localidad es única DENTRO de su provincia, y
# un producto dentro de su marca y su presentación (DD-55). Comparar sin
# el ámbito daría choques que no existen.
ENTIDADES_CON_CLAVE = [
    ('usuarios', 'Perfil', 'nombre', ()),
    ('proveedores', 'Provincia', 'nombre_provincia', ()),
    ('proveedores', 'Localidad', 'nombre_localidad', ('id_provincia',)),
    ('proveedores', 'Proveedor', 'nombre_proveedor', ()),
    ('maestros', 'Rubro', 'nombre_rubro', ()),
    ('maestros', 'Marca', 'nombre_marca', ()),
    ('maestros', 'TipoEgreso', 'nombre_tipo_egreso', ()),
    ('maestros', 'MedioPago', 'nombre_medio_pago', ()),
    ('maestros', 'CajaRegistradora', 'nombre_caja_registradora', ()),
    ('maestros', 'CategoriaFidelizacion', 'nombre_categoria', ()),
    ('maestros', 'Producto', 'nombre_producto',
     ('id_marca', 'contenido', 'unidad_medida')),
]


NUEVA_FUNCION = """
CREATE OR REPLACE FUNCTION f_clave_normalizada(p_texto TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT lower(btrim(regexp_replace(
        unaccent('unaccent', p_texto), '[^[:alnum:]]+', ' ', 'g')))
$$;

COMMENT ON FUNCTION f_clave_normalizada(TEXT) IS
  'DD-50: clave de comparación de los nombres de catálogo: minúsculas, sin acentos, sin puntuación y con un solo espacio entre palabras.';
"""

VIEJA_FUNCION = """
CREATE OR REPLACE FUNCTION f_clave_normalizada(p_texto TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT lower(unaccent('unaccent', regexp_replace(btrim(p_texto), '[[:space:]]+', ' ', 'g')))
$$;

COMMENT ON FUNCTION f_clave_normalizada(TEXT) IS
  'DD-50: clave de comparación de los nombres de catálogo: minúsculas, sin acentos y con los espacios internos colapsados.';
"""


def _cambiar_funcion(cuerpo):
    """Baja los índices que la usan, cambia la función y los rehace.

    Las definiciones se leen de ``pg_indexes``, que devuelve el
    ``CREATE INDEX`` completo, así que recrearlos es volver a ejecutar lo
    que ya estaba. ``%s`` va escapado porque esto viaja como SQL literal.
    """
    return """
DO $plpgsql$
DECLARE
    definiciones TEXT[];
    una TEXT;
BEGIN
    SELECT array_agg(indexdef) INTO definiciones
      FROM pg_indexes
     WHERE schemaname = 'public'
       AND indexdef LIKE '%%f_clave_normalizada%%';

    IF definiciones IS NOT NULL THEN
        FOREACH una IN ARRAY definiciones LOOP
            EXECUTE format('DROP INDEX %%I',
                           (regexp_match(una, 'INDEX (\\w+) ON'))[1]);
        END LOOP;
    END IF;

    %s

    IF definiciones IS NOT NULL THEN
        FOREACH una IN ARRAY definiciones LOOP
            EXECUTE una;
        END LOOP;
    END IF;
END $plpgsql$;
""" % cuerpo.replace('%', '%%')


def _clave_nueva(texto):
    """La misma normalización que la función nueva, en Python."""
    import re
    import unicodedata

    if texto is None:
        return None
    sin_tildes = ''.join(
        c for c in unicodedata.normalize('NFKD', str(texto))
        if not unicodedata.combining(c))
    return re.sub(r'[^0-9A-Za-z]+', ' ', sin_tildes).strip().lower()


def exigir_que_no_haya_choques(apps, schema_editor):
    """Aborta nombrando las filas que la clave nueva vuelve duplicadas."""
    problemas = []

    for app, nombre_modelo, campo, ambito in ENTIDADES_CON_CLAVE:
        Modelo = apps.get_model(app, nombre_modelo)
        por_clave = defaultdict(list)
        for fila in Modelo._base_manager.all():
            clave = (_clave_nueva(getattr(fila, campo)),) + tuple(
                getattr(fila, '%s_id' % c, getattr(fila, c, None))
                for c in ambito)
            por_clave[clave].append(fila)

        for filas in por_clave.values():
            if len(filas) > 1:
                problemas.append('  · %s: %s' % (
                    nombre_modelo,
                    '  |  '.join('%s (id %s)' % (getattr(f, campo), f.pk)
                                 for f in filas)))

    if problemas:
        raise RuntimeError(
            'Con la clave normalizada nueva (que además ignora la '
            'puntuación) estas filas pasan a ser duplicadas, así que los '
            'índices únicos no se pueden reconstruir:\n'
            + '\n'.join(problemas)
            + '\n\nResolvelas antes de migrar: dejá una y cambiale el '
              'nombre, o dale de baja a la otra desde su pantalla.')


def sin_efecto(apps, schema_editor):
    """El chequeo no escribe nada: revertirlo no tiene que deshacer nada."""


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0002_datos_iniciales'),
        # La función la usan índices de las otras tres apps, así que sus
        # tablas tienen que existir antes de tocarla.
        ('maestros', '0002_datos_iniciales'),
        ('proveedores', '0003_unicidad_del_nombre'),
    ]

    operations = [
        migrations.RunPython(exigir_que_no_haya_choques, sin_efecto),
        migrations.RunSQL(sql=_cambiar_funcion(NUEVA_FUNCION),
                          reverse_sql=_cambiar_funcion(VIEJA_FUNCION)),
    ]
