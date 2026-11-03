"""Las piezas del esquema que no salen de un modelo: histórico y vistas.

Todo lo demás de este proyecto sale de un modelo. Esto no, y en cada caso
por una limitación concreta del ORM, no por gusto:

1. Las tablas ``hist_*`` — cada una se crea con
   ``LIKE <tabla> INCLUDING DEFAULTS``, así que hereda la forma de su
   tabla viva. Declararlas como modelos sería escribir dos veces las
   mismas columnas y dejar abierta la posibilidad de que se
   desincronicen. Además el histórico **no tiene claves foráneas** a
   propósito: es una fotografía, no un libro vivo, y una FK le impediría
   conservar filas cuyo padre ya se archivó.

2. Las vistas ``v_*_completo`` — unen el libro vivo con su histórico
   (DD-04), que es lo que permite reconstruir un saldo que cruza el corte
   del archivado. Django no modela vistas.

Ninguna de las dos es lógica de negocio: son estructura de datos. Las
reglas viven todas en Python —el ``clean()`` de cada modelo, los mixins
de ``nucleo/reglas.py`` y los servicios de cada módulo— y no hay en la
base ni un trigger ni un procedimiento almacenado.

El vuelto de un pago (``pagos_venta.monto_vuelto``) es una columna
GENERATED, pero ya la declara el modelo con ``GeneratedField``.
"""
from django.db import migrations

# -- 1 · el histórico ---------------------------------------------------
# Las familias que se archivan. Las maestras, USUARIOS, PROVINCIAS,
# LOCALIDADES, la COMPOSICIÓN de los productos y los PARÁMETROS no se
# archivan nunca (RN-8.1.1), así que no tienen histórico.
TABLAS_CON_HISTORICO = [
    'turnos_caja', 'movimientos_caja', 'ventas', 'detalles_venta',
    'pagos_venta', 'cambios', 'detalles_cambio',
    'movimientos_stock', 'movimientos_puntos', 'compras',
    'detalles_compra', 'lotes', 'ordenes_compra',
    'detalles_orden_compra', 'egresos', 'ajustes_stock', 'detalles_ajuste',
    'producciones', 'log_auditoria', 'sesiones', 'intentos_acceso',
]

# ``LIKE`` sin ``INCLUDING GENERATED``: en el histórico
# ``pagos_venta.monto_vuelto`` es una columna común. Tiene que serlo,
# porque el archivado hace ``INSERT INTO hist_pagos_venta SELECT p.*`` y
# una columna generada no admite que le escriban un valor.
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


# -- 2 · los libros completos -------------------------------------------
CREAR_VISTAS = """
CREATE OR REPLACE VIEW v_movimientos_stock_completo AS
    SELECT id_movimiento_stock, id_producto, id_lote, id_compra, id_venta,
           id_cambio, id_ajuste, id_produccion, tipo_movimiento, cantidad,
           costo_unitario, fecha_movimiento FROM movimientos_stock
    UNION ALL
    SELECT id_movimiento_stock, id_producto, id_lote, id_compra, id_venta,
           id_cambio, id_ajuste, id_produccion, tipo_movimiento, cantidad,
           costo_unitario, fecha_movimiento FROM hist_movimientos_stock;

CREATE OR REPLACE VIEW v_movimientos_puntos_completo AS
    SELECT id_movimiento_punto, id_cliente, id_venta, id_beneficio,
           tipo_movimiento, puntos, fecha_movimiento FROM movimientos_puntos
    UNION ALL
    SELECT id_movimiento_punto, id_cliente, id_venta, id_beneficio,
           tipo_movimiento, puntos, fecha_movimiento FROM hist_movimientos_puntos;

CREATE OR REPLACE VIEW v_ventas_completo AS
    SELECT id_venta, id_turno_caja, id_usuario, id_cliente, fecha_venta,
           hora_venta, subtotal, monto_descuento, ajuste_redondeo,
           monto_recargo, total_venta FROM ventas
    UNION ALL
    SELECT id_venta, id_turno_caja, id_usuario, id_cliente, fecha_venta,
           hora_venta, subtotal, monto_descuento, ajuste_redondeo,
           monto_recargo, total_venta FROM hist_ventas;

-- Los cobros por medio de pago, para los reportes de caja que cruzan el
-- corte del archivado.
CREATE OR REPLACE VIEW v_pagos_venta_completo AS
    SELECT id_pago_venta, id_venta, id_medio_pago, monto, monto_recargo,
           monto_recibido, monto_vuelto FROM pagos_venta
    UNION ALL
    SELECT id_pago_venta, id_venta, id_medio_pago, monto, monto_recargo,
           monto_recibido, monto_vuelto FROM hist_pagos_venta;
"""

# Al revertir, en orden inverso: las vistas dependen de las tablas.
BORRAR = """
DROP VIEW IF EXISTS v_pagos_venta_completo;
DROP VIEW IF EXISTS v_ventas_completo;
DROP VIEW IF EXISTS v_movimientos_puntos_completo;
DROP VIEW IF EXISTS v_movimientos_stock_completo;
""" + '\n'.join('DROP TABLE IF EXISTS hist_%s;' % tabla
                for tabla in TABLAS_CON_HISTORICO)


class Migration(migrations.Migration):

    dependencies = [
        ('nucleo', '0001_funciones_base'),
        ('seguridad', '0003_datos_iniciales'),
        ('geografia', '0002_datos_iniciales'),
        ('stock', '0003_datos_iniciales'),
        ('ventas', '0002_datos_iniciales'),
        ('compras', '0004_datos_iniciales'),
        ('fidelizacion', '0004_datos_iniciales'),
        ('negocio', '0003_datos_iniciales'),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREAR_HISTORICO + CREAR_VISTAS,
            reverse_sql=BORRAR),
    ]
