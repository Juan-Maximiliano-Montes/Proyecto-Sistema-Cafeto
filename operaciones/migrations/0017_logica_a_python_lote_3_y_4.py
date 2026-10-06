"""Baja de la base los triggers y procedimientos que quedaban.

Segunda y última mitad del refactor. La 0016 bajó lo de los lotes 1 y 2;
acá se va el resto:

* **T11, T12 y T13** (egresos, órdenes de compra e inventario físico), que
  ahora viven en el ``save()`` de ``Egreso``, ``OrdenCompra``,
  ``DetalleOrdenCompra`` y ``DetalleInventario``.
* **Los 27 procedimientos** ``sp_*``, reemplazados por los servicios de
  ``operaciones/``: ``caja.py``, ``ventas.py``, ``puntos.py``,
  ``compras.py`` y ``egresos.py``.
* ``sp_pasaje_historico``, reemplazado por el comando
  ``manage.py pasar_a_historico``.

Qué se CONSERVA, y por qué
--------------------------
``f_clave_normalizada()`` y la extensión ``unaccent`` se quedan. No son
lógica de negocio: son la definición de los índices únicos normalizados
del esquema (DD-50), esos que hacen que "Café" y "cafe" sean el mismo
nombre. Un índice de expresión NO puede llamar a código Python — lo
resuelve el motor al escribir cada fila — así que moverlo al ORM no es
posible ni deseable.

Las tablas ``hist_*`` y las vistas ``v_*_completo`` también se conservan:
son estructura de datos, no reglas. Las llena el comando de archivado.

El SQL va embebido como texto. El proyecto no lee ningún archivo externo.
"""
from django.db import migrations


TRIGGERS_RESTANTES = [
    # T11 · egresos (Egreso.save)
    ('trg_egreso_valida', ['egresos']),
    ('trg_estado_compra', ['egresos']),
    # T12 · órdenes de compra (OrdenCompra.save / DetalleOrdenCompra)
    ('trg_orden_valida', ['ordenes_compra']),
    ('trg_detalle_orden_valida', ['detalles_orden_compra']),
    # T13 · inventario físico (DetalleInventario.save)
    ('trg_detalle_inventario_valida', ['detalles_inventario']),
]

FUNCIONES_DE_TRIGGER = [
    'fn_egreso_valida', 'fn_estado_compra', 'fn_orden_valida',
    'fn_detalle_orden_valida', 'fn_detalle_inventario_valida',
    # Auxiliares que ya no llama ningún trigger.
    'fn_usuario_actual', 'fn_en_pasaje_historico', 'fn_es_efectivo',
    'fn_saldo_caja',
]

# Los procedimientos, ahora servicios de Python. Se nombran uno por uno:
# una lista explícita se revisa, un DROP masivo no.
PROCEDIMIENTOS = [
    # Seguridad y sesiones
    'sp_registrar_intento_fallido', 'sp_iniciar_sesion',
    'sp_cerrar_sesiones_vencidas',
    # Caja  -> operaciones/caja.py
    'sp_abrir_caja', 'sp_declarar_arqueo', 'sp_cerrar_caja',
    # Ventas -> operaciones/ventas.py
    'sp_registrar_venta', 'sp_anular_venta',
    # Puntos -> operaciones/puntos.py
    'sp_acumular_puntos', 'sp_registrar_canje', 'sp_revertir_puntos',
    # Compras -> operaciones/compras.py
    'sp_emitir_orden', 'sp_enviar_orden', 'sp_cancelar_orden',
    'sp_recepcionar_compra',
    # Egresos -> operaciones/egresos.py
    'sp_registrar_obligacion', '_sp_efectivo_sale', 'sp_pagar_obligacion',
    'sp_registrar_egreso', 'sp_conciliar_pago', 'fn_vencimientos_proximos',
    # Stock e inventario
    'sp_registrar_merma', 'sp_abrir_inventario', 'sp_registrar_conteo',
    'sp_cerrar_inventario', 'sp_recalcular_saldos',
    # Baja lógica genérica -> cada modelo con SinBajaFisica
    'sp_baja_logica',
    # Archivado -> manage.py pasar_a_historico
    'sp_pasaje_historico',
]


def _sql_para_bajar():
    lineas = []
    for trigger, tablas in TRIGGERS_RESTANTES:
        for tabla in tablas:
            lineas.append('DROP TRIGGER IF EXISTS %s ON %s;' % (trigger, tabla))
    for nombre in FUNCIONES_DE_TRIGGER + PROCEDIMIENTOS:
        # Se borran por NOMBRE, sin firma: varios tienen argumentos con
        # valores por defecto y escribir la firma exacta de cada uno es una
        # fuente de errores. El bloque busca todas las sobrecargas.
        lineas.append("""
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS firma, p.prokind
               FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname = 'public' AND p.proname = '%s'
    LOOP
        IF r.prokind = 'p' THEN
            EXECUTE format('DROP PROCEDURE IF EXISTS %%s CASCADE', r.firma);
        ELSE
            EXECUTE format('DROP FUNCTION IF EXISTS %%s CASCADE', r.firma);
        END IF;
    END LOOP;
END $$;""" % nombre)
    return '\n'.join(lineas)


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0016_logica_a_python_lote_1_y_2'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_sql_para_bajar(),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
