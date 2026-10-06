"""Lo que Django no sabe declarar: la columna GENERADA y los DEFAULT.

Dos huecos del ORM que hay que tapar para que la base migrada por Django
sea idéntica a la que instala ``01_esquema.sql``:

1. ``VENTAS.monto_vuelto`` es una columna ``GENERATED ALWAYS AS (...)
   STORED`` (D-08): el vuelto no se guarda, se calcula. Django 4.2 no
   tiene ``GeneratedField`` —llega en 5.0—, así que la columna se agrega
   acá. Nunca se escribe desde Django: el modelo ``Venta`` la expone como
   propiedad de sólo lectura.

2. Los DEFAULT de columna. Django aplica los valores por omisión en
   Python, al armar el INSERT, y deja la columna sin ``DEFAULT`` en
   PostgreSQL. Eso alcanza mientras todo entre por el ORM, pero acá no es
   el caso: los procedimientos de ``04_procedimientos.sql`` insertan
   directo en SQL y cuentan con esos DEFAULT. Se agregan los 65
   que declara el esquema (los de las claves primarias no hacen falta:
   Django ya crea las columnas como IDENTITY).
"""
from django.db import migrations


# (tabla, columna, expresión del DEFAULT) — copiados de 01_esquema.sql
DEFAULTS = [
    ('perfiles',                'borrado_logico',            "FALSE"),
    ('perfiles',                'fecha_modificacion',        "CURRENT_TIMESTAMP"),
    ('permisos',                'borrado_logico',            "FALSE"),
    ('permisos',                'fecha_modificacion',        "CURRENT_TIMESTAMP"),
    ('usuarios',                'activo',                    "TRUE"),
    ('usuarios',                'debe_cambiar_clave',        "TRUE"),
    ('usuarios',                'fecha_alta',                "CURRENT_TIMESTAMP"),
    ('usuarios',                'fecha_ultima_modificacion', "CURRENT_TIMESTAMP"),
    ('sesiones',                'fecha_inicio',              "CURRENT_TIMESTAMP"),
    ('sesiones',                'fecha_ultimo_acceso',       "CURRENT_TIMESTAMP"),
    ('sesiones',                'activa',                    "TRUE"),
    ('log_auditoria',           'fecha_hora',                "CURRENT_TIMESTAMP"),
    ('intentos_acceso',         'fecha_hora',                "CURRENT_TIMESTAMP"),
    ('clientes',                'puntos_acumulados',         "0"),
    ('clientes',                'puntos_disponibles',        "0"),
    ('clientes',                'fecha_alta',                "CURRENT_DATE"),
    ('clientes',                'borrado_logico',            "FALSE"),
    ('clientes',                'fecha_modificacion',        "CURRENT_TIMESTAMP"),
    ('categorias_fidelizacion', 'borrado_logico',            "FALSE"),
    ('categorias_fidelizacion', 'fecha_modificacion',        "CURRENT_TIMESTAMP"),
    ('beneficios',              'vigencia_desde',            "CURRENT_DATE"),
    ('beneficios',              'borrado_logico',            "FALSE"),
    ('beneficios',              'fecha_modificacion',        "CURRENT_TIMESTAMP"),
    ('beneficios_productos',    'cantidad',                  "1"),
    ('movimientos_puntos',      'fecha_movimiento',          "CURRENT_TIMESTAMP"),
    ('ventas',                  'fecha_venta',               "CURRENT_DATE"),
    ('ventas',                  'hora_venta',                "CURRENT_TIME"),
    ('ventas',                  'monto_descuento',           "0"),
    ('ventas',                  'total_venta',               "0"),
    ('ventas',                  'estado_venta',              "'COMPLETADA'"),
    ('detalles_venta',          'tipo_linea',                "'PRODUCTO'"),
    ('cajas',                   'fecha_apertura',            "CURRENT_TIMESTAMP"),
    ('cajas',                   'estado_caja',               "'ABIERTA'"),
    ('movimientos_caja',        'fecha_hora',                "CURRENT_TIMESTAMP"),
    ('medios_pago',             'recargo_porcentaje',        "0"),
    ('medios_pago',             'borrado_logico',            "FALSE"),
    ('medios_pago',             'fecha_modificacion',        "CURRENT_TIMESTAMP"),
    ('puestos_caja',            'borrado_logico',            "FALSE"),
    ('puestos_caja',            'fecha_modificacion',        "CURRENT_TIMESTAMP"),
    ('proveedores',             'condicion_pago',            "'CONTADO'"),
    ('proveedores',             'fecha_alta',                "CURRENT_TIMESTAMP"),
    ('proveedores',             'borrado_logico',            "FALSE"),
    ('proveedores',             'fecha_modificacion',        "CURRENT_TIMESTAMP"),
    ('ordenes_compra',          'fecha_orden',               "CURRENT_DATE"),
    ('ordenes_compra',          'estado_orden',              "'PENDIENTE'"),
    ('ordenes_compra',          'total_orden',               "0"),
    ('compras',                 'fecha_compra',              "CURRENT_DATE"),
    ('compras',                 'estado_compra',             "'PENDIENTE'"),
    ('egresos',                 'estado_pago',               "'PENDIENTE_PAGO'"),
    ('tipos_egreso',            'borrado_logico',            "FALSE"),
    ('tipos_egreso',            'fecha_modificacion',        "CURRENT_TIMESTAMP"),
    ('productos',               'unidad_medida',             "'UNIDAD'"),
    ('productos',               'stock_actual',              "0"),
    ('productos',               'stock_minimo',              "0"),
    ('productos',               'controla_vencimiento',      "FALSE"),
    ('productos',               'borrado_logico',            "FALSE"),
    ('productos',               'fecha_modificacion',        "CURRENT_TIMESTAMP"),
    ('rubros',                  'activo',                    "TRUE"),
    ('rubros',                  'borrado_logico',            "FALSE"),
    ('rubros',                  'fecha_modificacion',        "CURRENT_TIMESTAMP"),
    ('lotes',                   'stock_lote',                "0"),
    ('lotes',                   'fecha_alta',                "CURRENT_TIMESTAMP"),
    ('movimientos_stock',       'fecha_movimiento',          "CURRENT_TIMESTAMP"),
    ('inventarios_fisicos',     'fecha_inventario',          "CURRENT_DATE"),
    ('inventarios_fisicos',     'estado_inventario',         "'EN_CURSO'"),
]

AGREGAR_VUELTO = """
ALTER TABLE ventas
    ADD COLUMN monto_vuelto NUMERIC(12,2)
    GENERATED ALWAYS AS (monto_recibido - total_venta) STORED;

COMMENT ON COLUMN ventas.monto_vuelto IS
  'Vuelto entregado al cliente. Columna calculada por la base (D-08): monto_recibido - total_venta.';
"""

QUITAR_VUELTO = 'ALTER TABLE ventas DROP COLUMN IF EXISTS monto_vuelto;'


def poner_defaults(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for tabla, columna, expresion in DEFAULTS:
            cursor.execute(
                'ALTER TABLE %s ALTER COLUMN %s SET DEFAULT %s'
                % (tabla, columna, expresion))


def quitar_defaults(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for tabla, columna, _ in DEFAULTS:
            cursor.execute(
                'ALTER TABLE %s ALTER COLUMN %s DROP DEFAULT'
                % (tabla, columna))


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0001_initial'),
        ('maestros', '0001_initial'),
        ('proveedores', '0003_alinear_con_el_der'),
        ('usuarios', '0006_alinear_con_el_der'),
    ]

    operations = [
        migrations.RunSQL(sql=AGREGAR_VUELTO, reverse_sql=QUITAR_VUELTO),
        migrations.RunPython(poner_defaults, reverse_code=quitar_defaults),
    ]
