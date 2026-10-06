"""Baja de la base los triggers que ya viven en Python.

Primera mitad del refactor: la lógica de las maestras, los libros de sólo
agregado, los saldos derivados, la caja, las ventas y los puntos pasó a
``cafeto/reglas.py`` y a los servicios de ``operaciones/``. Mientras los
triggers sigan instalados, las dos capas pelean: el trigger
``fn_protege_derivados`` rechaza la escritura legítima que hace
``actualizar_saldo_derivado()``, porque no puede distinguir al servicio
autorizado de una pantalla cualquiera.

Qué NO se baja todavía, y por qué
---------------------------------
T11 (egresos), T12 (órdenes de compra) y T13 (inventario físico) siguen en
PostgreSQL hasta que su reemplazo en Python esté escrito. Bajarlos ahora
dejaría esas reglas sin NADIE que las haga cumplir, que es peor que
tenerlas duplicadas.

El SQL va embebido como texto, no leído de un archivo: así la migración se
basta a sí misma y el repositorio no depende de ninguna carpeta externa.

Sobre la vuelta atrás
---------------------
``reverse_code`` reinstala los triggers corriendo de nuevo los guiones,
pero eso sólo tiene sentido si además se revierten los modelos. Si no, las
dos capas vuelven a pelear. Por eso la reversión es explícita y ruidosa.
"""
from django.db import migrations


# Los triggers cuya lógica ya está en Python. Se nombran uno por uno en vez
# de barrer con un DO $$ ... LOOP $$: una lista explícita es revisable, y lo
# que no está en ella queda en pie a propósito.
TRIGGERS_PORTADOS = [
    # T01 · sello de modificación (ConSelloDeModificacion)
    ('trg_sello_modificacion', ['perfiles', 'permisos', 'rubros', 'marcas',
                                'productos', 'proveedores', 'clientes',
                                'categorias_fidelizacion', 'beneficios',
                                'tipos_egreso', 'medios_pago',
                                'cajas_registradoras']),
    ('trg_usuario_ciclo_de_vida', ['usuarios']),
    # T02 · dar de baja cierra sesiones (Usuario.save)
    ('trg_baja_usuario_cierra_sesiones', ['usuarios']),
    # T03 · libros de sólo agregado (LibroInmutable)
    ('trg_libro_inmutable', ['movimientos_caja', 'movimientos_stock',
                             'movimientos_puntos', 'log_auditoria',
                             'intentos_acceso', 'detalles_venta']),
    # T04 · saldos derivados (ConSaldosDerivados)
    ('trg_protege_derivados', ['productos', 'lotes', 'clientes', 'compras']),
    ('trg_saldos_nacen_en_cero', ['productos', 'lotes', 'clientes',
                                  'compras']),
    # T05 · stock (MovimientoStock.save)
    ('trg_movimiento_stock_valida', ['movimientos_stock']),
    ('trg_actualiza_stock', ['movimientos_stock']),
    # T06 · caja (operaciones/caja.py)
    ('trg_movimiento_caja_valida', ['movimientos_caja']),
    # T07 · arqueo y cierre (operaciones/caja.py)
    ('trg_turno_caja_valido', ['turnos_caja']),
    # T08 y T09 · ventas (operaciones/ventas.py)
    ('trg_venta_alta', ['ventas']),
    ('trg_venta_modificacion', ['ventas']),
    ('trg_detalle_venta_valida', ['detalles_venta']),
    ('trg_detalle_venta_efectos', ['detalles_venta']),
    # T10 · puntos (operaciones/puntos.py + MovimientoPuntos.save)
    ('trg_movimiento_puntos_valida', ['movimientos_puntos']),
    ('trg_actualiza_puntos', ['movimientos_puntos']),
    # T14 · tablas asociativas custodiadas (delete() de cada modelo)
    ('trg_perfil_conserva_permiso', ['perfiles_permisos']),
    ('trg_beneficio_especie_receta', ['beneficios_productos']),
    # T15 · la baja es lógica (SinBajaFisica)
    ('trg_sin_baja_fisica', ['perfiles', 'permisos', 'usuarios', 'rubros',
                             'marcas', 'productos', 'proveedores', 'clientes',
                             'categorias_fidelizacion', 'beneficios',
                             'tipos_egreso', 'medios_pago',
                             'cajas_registradoras', 'ventas', 'turnos_caja',
                             'compras', 'ordenes_compra', 'egresos', 'lotes',
                             'inventarios_fisicos']),
]

# Las funciones que quedan sin ningún trigger que las llame. NO se borra
# `f_clave_normalizada` (la usan los índices normalizados del esquema) ni
# `fn_usuario_actual` / `fn_en_pasaje_historico` / `fn_es_efectivo` /
# `fn_saldo_caja`, que todavía usan los triggers de los lotes 3 y 4.
FUNCIONES_PORTADAS = [
    'fn_sello_modificacion', 'fn_usuario_ciclo_de_vida',
    'fn_baja_usuario_cierra_sesiones', 'fn_libro_inmutable',
    'fn_protege_derivados', 'fn_saldos_nacen_en_cero',
    'fn_movimiento_stock_valida', 'fn_actualiza_stock',
    'fn_movimiento_caja_valida', 'fn_caja_valida',
    'fn_venta_alta', 'fn_venta_modificacion',
    'fn_detalle_venta_valida', 'fn_detalle_venta_efectos',
    'fn_movimiento_puntos_valida', 'fn_actualiza_puntos',
    'fn_perfil_conserva_permiso', 'fn_beneficio_especie_receta',
    'fn_sin_baja_fisica',
]


def _sql_para_bajar():
    lineas = []
    for trigger, tablas in TRIGGERS_PORTADOS:
        for tabla in tablas:
            lineas.append('DROP TRIGGER IF EXISTS %s ON %s;'
                          % (trigger, tabla))
    for funcion in FUNCIONES_PORTADAS:
        # CASCADE por si quedó algún trigger colgando que no esté listado.
        lineas.append('DROP FUNCTION IF EXISTS %s() CASCADE;' % funcion)
    return '\n'.join(lineas)


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0015_permiso_de_auditoria'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_sql_para_bajar(),
            reverse_sql=migrations.RunSQL.noop,
            # La lógica equivalente vive ahora en cafeto/reglas.py,
            # operaciones/caja.py, operaciones/ventas.py,
            # operaciones/puntos.py y el save() de los libros.
        ),
    ]
