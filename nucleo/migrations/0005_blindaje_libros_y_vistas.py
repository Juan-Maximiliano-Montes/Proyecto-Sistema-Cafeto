"""Blindaje de los libros vivos y la vista de ventas completa (DB-TRG-01).

Lo que ya cubrían las migraciones anteriores:

* 0003 rechaza todo UPDATE y todo DELETE sobre los libros vivos
  (``fn_libro_inmutable``), salvo el pase a histórico.
* 0004 hace lo mismo con el archivo ``hist_*`` y, además, le cierra el
  ``TRUNCATE``.

Lo que faltaba, y cierra esta migración:

1. **TRUNCATE.** ``TRUNCATE`` no dispara los triggers de fila: un
   ``TRUNCATE log_auditoria`` (o ``TRUNCATE ... CASCADE`` desde una tabla
   padre) vaciaba un libro entero sin que 0003 se enterara. Cada libro vivo
   de 0003 —y la cuenta corriente— recibe ahora un trigger ``BEFORE
   TRUNCATE``. La única salida es la misma depuración explícita de 0004
   (``SET LOCAL cafeto.depuracion_historica = 'on'``); el sistema no trae
   ningún proceso que la use.

2. **La cuenta corriente (``movimientos_cuenta``).** No es un libro de sólo
   agregado puro: el fiado achica su ``saldo_pendiente`` cuando se cobra y
   marca ``puntos_acreditados`` cuando acredita los puntos (DD-91). Pero sólo
   eso y sólo en las filas FIADO. El trigger deja pasar exactamente ese
   UPDATE —comparando la fila entera menos esas dos columnas, así que una
   columna que se agregue mañana queda protegida sin tocar esto— y rechaza
   cualquier otro, y rechaza todo DELETE salvo el pase a histórico
   (``cafeto.pasaje_historico`` en ``'on'`` o ``'1'``).

3. **La vista ``v_ventas_completo``** suma ``estado_venta``: un reporte que
   cruza el corte del archivado tiene que poder excluir las ventas anuladas.
   La columna ya la agregó ``ventas.0003_anulacion_de_venta`` (con la misma
   definición); acá se re-declara con ``CREATE OR REPLACE`` para que el
   esquema no dependa del orden en que se apliquen las migraciones de
   ``ventas`` y ``nucleo``. Es idempotente. Al revertir no se toca: la
   revierte quien la creó.
"""
import importlib

from django.db import migrations

# La lista de libros vivos es la de 0003: una sola fuente de verdad.
LIBROS = importlib.import_module('nucleo.migrations.0003_libros_inalterables').LIBROS

# Libros que también se blindan contra el vaciado: los de 0003 más la
# cuenta corriente, que tiene su propio trigger de fila (punto 2).
CON_TRUNCATE = (*LIBROS, 'movimientos_cuenta')

FUNCION_TRUNCATE = r"""
CREATE OR REPLACE FUNCTION fn_libro_sin_vaciar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(current_setting('cafeto.depuracion_historica', true), '') = 'on' THEN
        RETURN NULL;     -- la depuración explícita (DD-99)
    END IF;
    RAISE EXCEPTION
        'La tabla % es un libro de solo agregado: no se vacía con TRUNCATE (DD-04, DD-68).',
        TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""

FUNCION_CUENTA = r"""
CREATE OR REPLACE FUNCTION fn_movimientos_cuenta_inmutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF coalesce(current_setting('cafeto.pasaje_historico', true), '') IN ('on', '1') THEN
            RETURN OLD;     -- el pase a histórico, que ya copió la fila
        END IF;
        RAISE EXCEPTION
            'La cuenta corriente es un libro de solo agregado: un movimiento no se borra, se contra-asienta (DD-91, DD-04).'
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- UPDATE: sólo el fiado puede cambiar, y sólo lo que falta pagar y la
    -- marca de puntos acreditados. Todo lo demás de la fila queda igual.
    IF OLD.tipo_movimiento = 'FIADO'
       AND (to_jsonb(NEW) - 'saldo_pendiente' - 'puntos_acreditados')
         = (to_jsonb(OLD) - 'saldo_pendiente' - 'puntos_acreditados') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION
        'La cuenta corriente es un libro de solo agregado: de un fiado sólo cambian el saldo pendiente y los puntos acreditados (DD-91, DD-04).'
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""

TRIGGER_CUENTA = (
    'CREATE TRIGGER tr_movimientos_cuenta_inalterable '
    'BEFORE UPDATE OR DELETE ON movimientos_cuenta '
    'FOR EACH ROW EXECUTE FUNCTION fn_movimientos_cuenta_inmutable();')

SIN_TRIGGER_CUENTA = (
    'DROP TRIGGER IF EXISTS tr_movimientos_cuenta_inalterable ON movimientos_cuenta;\n'
    'DROP FUNCTION IF EXISTS fn_movimientos_cuenta_inmutable();')

# CREATE OR REPLACE VIEW sólo admite columnas nuevas AL FINAL: acá la lista
# es la definitiva (la de 0002 más estado_venta, igual que ventas 0003).
VISTA_VENTAS = """
CREATE OR REPLACE VIEW v_ventas_completo AS
    SELECT id_venta, id_turno_caja, id_usuario, id_cliente, fecha_venta,
           hora_venta, subtotal, monto_descuento, ajuste_redondeo,
           monto_recargo, total_venta, estado_venta FROM ventas
    UNION ALL
    SELECT id_venta, id_turno_caja, id_usuario, id_cliente, fecha_venta,
           hora_venta, subtotal, monto_descuento, ajuste_redondeo,
           monto_recargo, total_venta, estado_venta FROM hist_ventas;
"""


def _crear_truncate(tabla):
    return (f'CREATE TRIGGER tr_{tabla}_sin_vaciar '
            f'BEFORE TRUNCATE ON {tabla} '
            'FOR EACH STATEMENT EXECUTE FUNCTION fn_libro_sin_vaciar();')


def _borrar_truncate(tabla):
    return f'DROP TRIGGER IF EXISTS tr_{tabla}_sin_vaciar ON {tabla};'


class Migration(migrations.Migration):

    dependencies = [
        ('nucleo', '0004_archivo_historico_legal'),
        ('fidelizacion', '0009_plazo_del_fiado'),
        ('ventas', '0012_relevo_y_revision_del_turno'),
    ]

    operations = [
        # 1 · ningún libro vivo se vacía con TRUNCATE
        migrations.RunSQL(FUNCION_TRUNCATE,
                          'DROP FUNCTION IF EXISTS fn_libro_sin_vaciar();'),
        *[migrations.RunSQL(_crear_truncate(t), _borrar_truncate(t))
          for t in CON_TRUNCATE],
        # 2 · la cuenta corriente: sólo cambia el saldo del fiado
        migrations.RunSQL(FUNCION_CUENTA + TRIGGER_CUENTA, SIN_TRIGGER_CUENTA),
        # 3 · v_ventas_completo con estado_venta (idempotente)
        migrations.RunSQL(VISTA_VENTAS, migrations.RunSQL.noop),
    ]
