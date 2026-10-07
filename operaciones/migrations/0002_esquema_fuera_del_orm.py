"""Las tres piezas del esquema que Django 4.2 no sabe declarar.

Todo lo demás de este proyecto sale de un modelo. Esto no, y en cada caso
por una limitación concreta del ORM, no por gusto:

1. ``VENTAS.monto_vuelto`` — columna ``GENERATED ALWAYS AS ... STORED``
   (DD-46, D-08). El vuelto no se guarda: lo calcula el motor como
   ``monto_recibido - total_venta``, queda grabado y es imposible que no
   coincida con la venta. ``GeneratedField`` llega en Django 5.0; en 4.2
   la columna se agrega acá. Nunca se escribe desde Django: el modelo
   ``Venta`` la expone como propiedad de sólo lectura.

2. Las tablas ``hist_*`` — cada una se crea con
   ``LIKE <tabla> INCLUDING DEFAULTS``, así que hereda la forma de su
   tabla viva. Declararlas como modelos sería escribir dos veces las
   mismas treinta columnas y dejar abierta la posibilidad de que se
   desincronicen. Además el histórico **no tiene claves foráneas** a
   propósito: es una fotografía, no un libro vivo, y una FK le impediría
   conservar filas cuyo padre ya se archivó.

3. Las vistas ``v_*_completo`` — unen el libro vivo con su histórico
   (DD-04), que es lo que permite reconstruir un saldo que cruza el corte
   del archivado. Django no modela vistas.

Ninguna de las tres es lógica de negocio: son estructura de datos. Las
reglas viven todas en Python —el ``clean()`` de cada modelo, los mixins
de ``cafeto/reglas.py`` y los servicios de ``operaciones/``— y no hay en
la base ni un trigger ni un procedimiento almacenado.

El orden importa: ``monto_vuelto`` va primero porque ``hist_ventas`` se
copia de ``ventas`` y ``v_ventas_completo`` la lee. El SQL va embebido
como texto: el proyecto no lee ningún archivo externo.
"""
from django.db import migrations

# -- 1 · la columna generada ------------------------------------------
CREAR_COLUMNA_GENERADA = """
ALTER TABLE ventas ADD COLUMN IF NOT EXISTS monto_vuelto NUMERIC(12,2)
    GENERATED ALWAYS AS (monto_recibido - total_venta) STORED;

COMMENT ON COLUMN ventas.monto_vuelto IS
  'DD-46: vuelto entregado al cliente. La calcula el motor como monto_recibido - total_venta, queda grabada y auditada. Nula si la venta no fue en efectivo.';
"""

BORRAR_COLUMNA_GENERADA = """
ALTER TABLE ventas DROP COLUMN IF EXISTS monto_vuelto;
"""


# -- 2 · el histórico ---------------------------------------------------
# Las familias que se archivan. Las maestras, USUARIOS, PROVINCIAS,
# LOCALIDADES e INVENTARIOS no se archivan nunca (RN-8.1.1), así que no
# tienen histórico.
TABLAS_CON_HISTORICO = [
    'turnos_caja', 'movimientos_caja', 'ventas', 'detalles_venta',
    'movimientos_stock', 'movimientos_puntos', 'compras',
    'detalles_compra', 'lotes', 'ordenes_compra',
    'detalles_orden_compra', 'egresos', 'log_auditoria',
    'sesiones', 'intentos_acceso',
]

# ``LIKE`` sin ``INCLUDING GENERATED``: en el histórico ``monto_vuelto``
# es una columna común. Tiene que serlo, porque el archivado hace
# ``INSERT INTO hist_ventas SELECT v.*`` y una columna generada no admite
# que le escriban un valor.
CREAR_HISTORICO = """
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[%s] LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS hist_%%1$s (LIKE %%1$s INCLUDING DEFAULTS)', t);
        EXECUTE format(
            'ALTER TABLE hist_%%s ADD COLUMN IF NOT EXISTS fecha_archivo '
            'TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP', t);
        EXECUTE format('COMMENT ON TABLE hist_%%1$s IS %%2$L', t,
                       'Histórico de ' || upper(t) || ': fotografía sin FK, '
                       'de sólo lectura, cargada por pasar_a_historico.');
    END LOOP;
END $$;
""" % ', '.join("'%s'" % tabla for tabla in TABLAS_CON_HISTORICO)


# -- 3 · los libros completos -------------------------------------------
CREAR_VISTAS = """
CREATE OR REPLACE VIEW v_movimientos_stock_completo AS
    SELECT id_movimiento_stock, id_producto, id_lote, id_venta, id_compra,
           tipo_movimiento, cantidad, fecha_movimiento FROM movimientos_stock
    UNION ALL
    SELECT id_movimiento_stock, id_producto, id_lote, id_venta, id_compra,
           tipo_movimiento, cantidad, fecha_movimiento FROM hist_movimientos_stock;

CREATE OR REPLACE VIEW v_movimientos_puntos_completo AS
    SELECT id_movimiento_punto, id_cliente, id_venta, id_beneficio,
           tipo_movimiento, puntos, fecha_movimiento FROM movimientos_puntos
    UNION ALL
    SELECT id_movimiento_punto, id_cliente, id_venta, id_beneficio,
           tipo_movimiento, puntos, fecha_movimiento FROM hist_movimientos_puntos;

-- monto_recibido y monto_vuelto viajan a la vista porque el arqueo y los
-- reportes de caja los necesitan también sobre el histórico (DD-46).
CREATE OR REPLACE VIEW v_ventas_completo AS
    SELECT id_venta, id_turno_caja, id_usuario, id_cliente, id_medio_pago,
           fecha_venta, total_venta, monto_descuento, monto_recibido,
           monto_vuelto, estado_venta FROM ventas
    UNION ALL
    SELECT id_venta, id_turno_caja, id_usuario, id_cliente, id_medio_pago,
           fecha_venta, total_venta, monto_descuento, monto_recibido,
           monto_vuelto, estado_venta FROM hist_ventas;
"""

# Al revertir, en orden inverso: las vistas dependen de las tablas, y
# hist_ventas se copió de ventas.
BORRAR = """
DROP VIEW IF EXISTS v_ventas_completo;
DROP VIEW IF EXISTS v_movimientos_puntos_completo;
DROP VIEW IF EXISTS v_movimientos_stock_completo;
""" + '\n'.join('DROP TABLE IF EXISTS hist_%s;' % tabla
                for tabla in TABLAS_CON_HISTORICO) + BORRAR_COLUMNA_GENERADA


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREAR_COLUMNA_GENERADA + CREAR_HISTORICO + CREAR_VISTAS,
            reverse_sql=BORRAR),
    ]
