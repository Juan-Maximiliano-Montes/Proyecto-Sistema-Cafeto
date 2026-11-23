"""Archivo histórico y retención legal (DD-99).

Cada libro se lee de la tabla viva y de su histórico juntos (UNION ALL), así
quien consulta 2019 no tiene que saber si eso ya se archivó. Es de sólo
lectura: el histórico lo protege un trigger y acá no hay nada que escriba.

Las columnas se piden por nombre (nunca ``*``): el histórico pudo recibir
columnas nuevas después de ``fecha_archivo`` y el orden no es el mismo.
"""
from dataclasses import dataclass
from datetime import date

from django.db import connection

from nucleo.exportar import ColumnaExportable as Col

ANIOS_DE_CONSERVACION = 10
FILAS_EN_PANTALLA = 200


@dataclass(frozen=True)
class Libro:
    clave: str
    titulo: str
    icono: str
    tabla: str
    fecha: str                       # expresión de fecha sobre el alias x
    columnas: tuple                  # ((expresión sobre x y joins, Col), ...)
    joins: str = ''
    regla: str = ''
    enlace: str = ''                 # nombre de la URL de la lista viva


LIBROS = (
    Libro('ventas', 'Ventas', 'bi-receipt', 'ventas', 'x.fecha_venta',
          (('x.id_venta', Col('N° de venta', 'numero')), ('x.fecha_venta', Col('Fecha', 'fecha')),
           ("to_char(x.hora_venta, 'HH24:MI')", Col('Hora')),
           ('x.total_venta', Col('Total', 'dinero')), ('x.estado_venta', Col('Estado')),
           ("coalesce(c.apellido_cliente || ', ' || c.nombre_cliente, 'Consumidor final')", Col('Cliente')),
           ("u.apellido || ', ' || u.nombre", Col('Atendió')),
           ('x.comprobante_fiscal_ref', Col('Comprobante fiscal')), ('x.id_turno_caja', Col('Turno', 'numero'))),
          joins='LEFT JOIN clientes c ON c.id_cliente = x.id_cliente '
                'LEFT JOIN usuarios u ON u.id_usuario = x.id_usuario',
          regla='Se archivan a los 24 meses con su turno cerrado y la cuenta corriente en $ 0,00 '
                '(cobrada, cancelada o incobrable).',
          enlace='ventas:ventas'),
    Libro('compras', 'Compras', 'bi-bag', 'compras', 'x.fecha_compra',
          (('x.id_compra', Col('N° de compra', 'numero')), ('x.fecha_compra', Col('Fecha', 'fecha')),
           ('p.nombre_proveedor', Col('Proveedor')), ('p.cuit_proveedor', Col('CUIT')),
           ('x.tipo_comprobante', Col('Tipo')),
           # Los mismos cuatro dígitos de punto de venta que Compra.numero_formateado
           # ("0001-00001234", como figura impreso): con cinco, el archivo mostraba
           # "00001-00001234" y no coincidía con el número que busca el contador.
           ("lpad(x.punto_venta::text, 4, '0') || '-' || lpad(x.numero_comprobante::text, 8, '0')", Col('Número')),
           ('x.total_compra', Col('Total', 'dinero')), ('x.estado_compra', Col('Estado')),
           ('x.compra_ocasional', Col('Ocasional', 'si_no'))),
          joins='LEFT JOIN proveedores p ON p.id_proveedor = x.id_proveedor',
          regla='Se archivan a los 24 meses ya PAGADAS y con todos sus pagos acreditados o rechazados; '
                'sus lotes, sólo sin stock.',
          enlace='compras:compras'),
    # Un pago todavía sin hacer no tiene fecha de pago: cuenta por su vencimiento.
    Libro('egresos', 'Pagos y gastos', 'bi-wallet2', 'egresos', 'coalesce(x.fecha_egreso, x.fecha_vencimiento)',
          (('x.id_egreso', Col('N° de pago', 'numero')), ('x.fecha_egreso', Col('Fecha', 'fecha')),
           # Un egreso se le paga a un proveedor O a otro destinatario, nunca a
           # los dos (DD-25.1): cuando es a un proveedor, el nombre está en la
           # tabla de proveedores y 'destinatario' viene NULL. Sin el LEFT JOIN
           # la columna «A quién» salía VACÍA en todos los pagos a proveedores,
           # que son la mayoría del libro.
           ('coalesce(p.nombre_proveedor, x.destinatario)', Col('A quién')),
           ('x.concepto', Col('Concepto')),
           ('x.monto', Col('Monto', 'dinero')), ('x.estado_pago', Col('Estado')),
           ('x.fecha_acreditacion', Col('Acreditado', 'fecha')), ('x.comprobante', Col('Comprobante')),
           ('x.id_compra', Col('Compra', 'numero'))),
          joins='LEFT JOIN proveedores p ON p.id_proveedor = x.id_proveedor',
          regla='Se archivan a los 24 meses acreditados o rechazados (los de una compra, con ella).',
          enlace='compras:egresos'),
    Libro('turnos', 'Turnos de caja', 'bi-cash-stack', 'turnos_caja', 'x.fecha_apertura::date',
          (('x.id_turno_caja', Col('N° de turno', 'numero')), ('x.fecha_apertura', Col('Apertura', 'fecha_hora')),
           ('x.fecha_cierre', Col('Cierre', 'fecha_hora')), ('cr.nombre_caja_registradora', Col('Caja')),
           ('x.monto_inicial', Col('Fondo', 'dinero')), ('x.monto_esperado', Col('Esperado', 'dinero')),
           ('x.monto_final', Col('Contado', 'dinero')), ('x.diferencia_caja', Col('Diferencia', 'dinero')),
           ('x.estado_turno', Col('Estado')), ('x.tipo_cierre', Col('Cierre por'))),
          joins='LEFT JOIN cajas_registradoras cr ON cr.id_caja_registradora = x.id_caja_registradora',
          regla='Se archivan a los 24 meses CERRADOS y sin una diferencia pendiente de revisión.',
          enlace='ventas:turnos'),
    Libro('cuenta', 'Cuenta corriente', 'bi-journal-text', 'movimientos_cuenta', 'x.fecha_movimiento::date',
          (('x.id_movimiento_cuenta', Col('N° de movimiento', 'numero')),
           ('x.fecha_movimiento', Col('Fecha', 'fecha_hora')),
           ("c.apellido_cliente || ', ' || c.nombre_cliente", Col('Cliente')),
           ('x.tipo_movimiento', Col('Movimiento')), ('x.monto', Col('Monto', 'dinero')),
           ('x.saldo_pendiente', Col('Saldo que quedaba', 'dinero')), ('x.id_venta', Col('Venta', 'numero')),
           ('x.motivo', Col('Motivo'))),
          joins='LEFT JOIN clientes c ON c.id_cliente = x.id_cliente',
          regla='Se archiva con su venta, sólo cuando el fiado quedó en $ 0,00.',
          enlace='fidelizacion:cuentas'),
    Libro('auditoria', 'Registro de actividad', 'bi-shield-check', 'log_auditoria', 'x.fecha_hora::date',
          (('x.id_auditoria', Col('N°', 'numero')), ('x.fecha_hora', Col('Fecha', 'fecha_hora')),
           ("u.apellido || ', ' || u.nombre", Col('Usuario')), ('x.operacion_realizada', Col('Operación')),
           ('x.modulo_afectado', Col('Módulo')), ('x.detalle_operacion', Col('Detalle'))),
          joins='LEFT JOIN usuarios u ON u.id_usuario = x.id_usuario',
          regla='Se archiva a los 12 meses.', enlace='seguridad:actividad'),
)
POR_CLAVE = {libro.clave: libro for libro in LIBROS}


def libro(clave):
    return POR_CLAVE.get(clave) or LIBROS[0]


def _union(libro_, columnas, donde):
    """El SELECT de la tabla viva y el del histórico, con lo mismo."""
    partes = []
    for tabla, lugar in ((libro_.tabla, 'En uso'), ('hist_' + libro_.tabla, 'Archivada')):
        partes.append(f"SELECT {columnas}, '{lugar}' AS lugar FROM {tabla} x {libro_.joins} WHERE {donde}")
    return ' UNION ALL '.join(partes)


def por_anio():
    """Cuántos comprobantes hay de cada año, vivos y archivados.

    ``[{'anio', 'conservar_hasta', <clave>: {'vigentes', 'archivadas'}}]``,
    del año más nuevo al más viejo."""
    anios = {}
    with connection.cursor() as cursor:
        for libro_ in LIBROS:
            consulta = (f"SELECT extract(year FROM f_)::int, lugar, count(*) FROM ("
                        f"SELECT {libro_.fecha} AS f_, 'Vigente' AS lugar FROM {libro_.tabla} x UNION ALL "
                        f"SELECT {libro_.fecha}, 'Archivada' FROM hist_{libro_.tabla} x) t "
                        f"WHERE f_ IS NOT NULL GROUP BY 1, 2")
            cursor.execute(consulta)
            for anio, lugar, cantidad in cursor.fetchall():
                fila = anios.setdefault(anio, {'anio': anio})
                celda = fila.setdefault(libro_.clave, {'vigentes': 0, 'archivadas': 0})
                celda['vigentes' if lugar == 'Vigente' else 'archivadas'] += cantidad
    filas = []
    for anio in sorted(anios, reverse=True):
        fila = anios[anio]
        for libro_ in LIBROS:
            fila.setdefault(libro_.clave, {'vigentes': 0, 'archivadas': 0})
        fila['libros'] = [dict(fila[l.clave], clave=l.clave) for l in LIBROS]
        fila['total'] = sum(c['vigentes'] + c['archivadas'] for c in fila['libros'])
        fila['conservar_hasta'] = date(anio + ANIOS_DE_CONSERVACION, 12, 31)
        filas.append(fila)
    return filas


def consultar(libro_, desde, hasta, tope=None):
    """``(columnas, filas, hay_mas)`` del período, vivo + archivado, en orden
    de fecha (y de número, a igual fecha)."""
    expresiones = ', '.join(f'{expr} AS c{i}' for i, (expr, _col) in enumerate(libro_.columnas))
    expresiones += f', {libro_.fecha} AS f_orden'
    consulta = (f'SELECT * FROM ({_union(libro_, expresiones, f"{libro_.fecha} BETWEEN %s AND %s")}) t '
                'ORDER BY f_orden, c0')
    if tope:
        consulta += f' LIMIT {int(tope) + 1}'
    with connection.cursor() as cursor:
        cursor.execute(consulta, [desde, hasta, desde, hasta])
        filas = [list(fila[:-2]) + [fila[-1]] for fila in cursor.fetchall()]
    hay_mas = bool(tope) and len(filas) > tope
    columnas = [col for _expr, col in libro_.columnas] + [Col('Dónde está')]
    return columnas, filas[:tope] if tope else filas, hay_mas
