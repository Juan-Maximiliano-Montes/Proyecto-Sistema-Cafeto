"""Libros inalterables también en la base (DD-68).

Los libros que prueban lo que pasó —la auditoría, los intentos de acceso,
los movimientos de caja, de stock y de puntos, y los renglones de ventas,
pagos, cambios y producciones— ya los protegía la regla de modelo
``LibroInmutable``. Esa regla vale para todo lo que pasa por la aplicación,
pero no para quien entre directo a la base (``psql``, un script): por eso
el diseño los resolvía con el trigger ``TR_log_inalterable``.

Esta migración agrega ese trigger real, como defensa en profundidad
(NIST SP 800-53, OWASP): rechaza todo UPDATE y todo DELETE sobre esas
tablas. La única excepción es el pase a histórico, que marca su transacción
con ``SET LOCAL cafeto.pasaje_historico = 'on'`` (sólo dura esa
transacción) y nada más borra filas que ya copió a ``hist_*``.

La lógica de negocio sigue en los servicios de la aplicación: el trigger no
decide nada, sólo impide que se reescriba la historia.
"""
from django.db import migrations

LIBROS = (
    'log_auditoria', 'intentos_acceso', 'movimientos_caja',
    'movimientos_stock', 'movimientos_puntos', 'detalles_venta',
    'pagos_venta', 'cambios', 'detalles_cambio', 'producciones',
)

FUNCION = r"""
CREATE OR REPLACE FUNCTION fn_libro_inmutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND coalesce(current_setting('cafeto.pasaje_historico', true), '') = 'on' THEN
        RETURN OLD;     -- el pase a histórico, que ya copió la fila
    END IF;
    RAISE EXCEPTION
        'La tabla % es un libro de solo agregado: un asiento no se corrige ni se borra, se contra-asienta (DD-04, DD-68).',
        TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""


def _crear(tabla):
    return (f'CREATE TRIGGER tr_{tabla}_inalterable '
            f'BEFORE UPDATE OR DELETE ON {tabla} '
            'FOR EACH ROW EXECUTE FUNCTION fn_libro_inmutable();')


def _borrar(tabla):
    return f'DROP TRIGGER IF EXISTS tr_{tabla}_inalterable ON {tabla};'


class Migration(migrations.Migration):

    dependencies = [
        ('nucleo', '0002_historico_y_vistas'),
        ('seguridad', '0004_resultado_de_la_operacion'),
        ('ventas', '0006_cuotas_y_recargo_legal'),
        ('stock', '0004_anulacion_de_venta'),
        ('fidelizacion', '0006_permisos_de_clientes'),
    ]

    operations = [
        migrations.RunSQL(FUNCION, 'DROP FUNCTION IF EXISTS fn_libro_inmutable();'),
        *[migrations.RunSQL(_crear(t), _borrar(t)) for t in LIBROS],
    ]
