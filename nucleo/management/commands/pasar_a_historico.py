"""Archiva lo cerrado y antiguo en las tablas ``hist_*``.

Reemplaza al antiguo procedimiento de pasaje. Es la **única
baja física admitida** en todo el sistema (RN-8.1.1): en cualquier otro
lugar, borrar una fila es un error.

Por qué este proceso lleva SQL embebido y el resto del refactor no
--------------------------------------------------------------------
Los demás lotes movieron REGLAS DE NEGOCIO al ORM, y ahí Python gana: "una
venta no se borra", "el stock sale por FEFO" se leen mejor en una clase que
en SQL.

Esto es otra cosa: una operación masiva de mantenimiento. Archivar con el
ORM significa traer cada fila a memoria, instanciarla y volver a guardarla
—millones de viajes de ida y vuelta— cuando PostgreSQL resuelve lo mismo
con un ``INSERT ... SELECT`` que nunca saca los datos del servidor.

El SQL va embebido como TEXTO en este archivo, no leído de una carpeta
externa: el proyecto sigue siendo autocontenido, que era el objetivo.

Las cuatro reglas del proceso
-----------------------------
1. **Nada se archiva sin estar cerrado** (estado terminal, DD-99). El corte
   es siempre antigüedad Y estado:

   * turnos de caja: CERRADOS y sin una diferencia pendiente de revisión;
     sus ventas, con la cuenta corriente saldada (el fiado de esas ventas
     en $ 0,00: cobrado, cancelado o incobrable) — 24 meses;
   * compras: PAGADAS y con todos sus pagos ACREDITADOS o RECHAZADOS; sus
     lotes, sólo sin stock — 24 meses;
   * egresos sueltos: ACREDITADOS o RECHAZADOS — 24 meses;
   * pedidos de autorización a distancia: USADOS, VENCIDOS (anulados) o
     RECHAZADOS — 12 meses;
   * facturas recibidas descartadas — 12 meses (las confirmadas se van
     con su compra);
   * auditoría, sesiones e intentos de acceso — 12 meses.

   Archivar algo abierto lo sacaría de la vista justo cuando hace falta.
   Las maestras (productos, clientes, proveedores, usuarios) no se archivan
   nunca: se dan de baja lógica (RN-8.1.1).
2. **Se archiva por familia**, respetando las claves foráneas: los hijos
   primero, y un padre sólo se mueve si ningún hijo vivo lo referencia.
3. **Copiar, verificar y recién entonces borrar**, todo en UNA
   transacción. Si la verificación no cuadra, no se borra nada.
4. **El histórico es de sólo lectura**: una fotografía sin claves foráneas,
   que un trigger de la base protege de UPDATE, DELETE y TRUNCATE y que se
   conserva diez años (art. 328 del Código Civil y Comercial; DD-99).

Uso::

    manage.py pasar_a_historico                  # muestra qué archivaría
    manage.py pasar_a_historico --ejecutar
    manage.py pasar_a_historico --ejecutar --meses-operativo 36
"""
import re

from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection, transaction

from nucleo import contexto

# Cortes por defecto, en meses hacia atrás (RPH, DD-99).
CORTE_OPERATIVO = 24      # turnos de caja, ventas, cuenta corriente, ajustes y producciones
CORTE_COMPRAS = 24        # compras, lotes, órdenes y egresos
CORTE_LOG = 12            # auditoría, sesiones, intentos de acceso, facturas descartadas
CORTE_SOLICITUDES = 12    # pedidos de autorización a distancia
# Lo que se archiva se conserva, como mínimo, este tiempo (art. 328 CCyC).
ANIOS_DE_CONSERVACION = 10

COLUMNAS_VENTAS = (
    'id_venta, token_operacion, fecha_venta, hora_venta, subtotal, '
    'monto_descuento, ajuste_redondeo, monto_recargo, total_venta, '
    'comprobante_fiscal_ref, id_beneficio, id_cliente, id_turno_caja, '
    'id_usuario, estado_venta, fecha_anulacion, id_usuario_anulacion, '
    'motivo_anulacion, detalle_anulacion, id_usuario_solicitante')

# Y con los turnos: el relevo y la revisión de diferencias (DD-97) se
# agregaron después de ``fecha_archivo``.
COLUMNAS_TURNOS = (
    'id_turno_caja, id_caja_registradora, id_usuario_apertura, id_usuario_cierre, '
    'id_usuario_supervisor, monto_inicial, monto_esperado, monto_final, '
    'diferencia_caja, observacion_diferencia, tipo_cierre, fecha_apertura, '
    'fecha_cierre, estado_turno, id_turno_anterior, relevo_en_ausencia, '
    'requiere_revision, id_usuario_revision, fecha_revision')

# Y con las compras: la marca de compra ocasional (DD-98).
COLUMNAS_COMPRAS = (
    'id_compra, id_proveedor, id_orden_compra, id_usuario, fecha_compra, '
    'tipo_comprobante, punto_venta, numero_comprobante, importe_otros_conceptos, '
    'total_compra, fecha_vencimiento_pago, estado_compra, compra_ocasional')

# Lo mismo con el log: ``resultado_operacion`` (DD-62) se agregó a las dos
# tablas después de ``fecha_archivo``, así que ``l.*`` no coincide.
COLUMNAS_LOG = (
    'id_auditoria, operacion_realizada, modulo_afectado, detalle_operacion, '
    'fecha_hora, id_usuario, id_perfil_efectivo, id_sesion, tabla_afectada, '
    'id_registro, registro_anterior, registro_nuevo, resultado_operacion')


# ---------------------------------------------------------------------------
#  LAS FAMILIAS
# ---------------------------------------------------------------------------
# Cada familia es (nombre, SQL de selección, [(tabla, SQL de copia)], [SQL de
# borrado]). El orden de las listas IMPORTA: los hijos antes que el padre.
#
# Las tablas temporales guardan qué se eligió, para que la copia y el borrado
# miren exactamente el mismo conjunto. Sin eso, una fila que entra entre una
# cosa y la otra se borraría sin haberse copiado.

# Django resuelve el ON DELETE CASCADE en Python, no en la base: un
# ``DELETE FROM ventas`` en SQL NO se lleva sus renglones. Por eso cada
# familia borra a los hijos explícitamente, antes que al padre.

FAMILIA_CAJA = {
    'nombre': 'Turnos de caja, sus ventas y sus cambios',
    'seleccion': [
        # Un turno se archiva si está cerrado, es viejo y ninguna venta
        # suya tiene un cambio registrado en un turno que se queda.
        # Tampoco si quedó una diferencia sin revisar (DD-97) o un pedido de
        # autorización que todavía no se archivó.
        """CREATE TEMP TABLE tmp_turnos ON COMMIT DROP AS
             SELECT t.id_turno_caja FROM turnos_caja t
              WHERE t.estado_turno = 'CERRADA' AND t.fecha_cierre < %(operativo)s
                AND NOT (t.requiere_revision AND t.id_usuario_revision IS NULL)
                AND NOT EXISTS (SELECT 1 FROM solicitudes_autorizacion s
                                 WHERE s.id_turno_caja = t.id_turno_caja)""",
    ],
    # Se repiten hasta que ya no saquen ningún turno: sacar uno puede dejar
    # colgando a otro (un cambio, un cobro o un fiado que cruza turnos).
    'iterar': [
        """DELETE FROM tmp_turnos tt
            WHERE EXISTS (SELECT 1 FROM ventas v JOIN cambios c USING (id_venta)
                           WHERE v.id_turno_caja = tt.id_turno_caja
                             AND c.id_turno_caja NOT IN (SELECT id_turno_caja FROM tmp_turnos))""",
        # La cuenta corriente: el fiado de las ventas del turno tiene que
        # estar en $ 0,00 y todo lo que lo achicó, en turnos que también se
        # van (o sin turno).
        """DELETE FROM tmp_turnos tt
            WHERE EXISTS (SELECT 1 FROM ventas v
                            JOIN movimientos_cuenta f ON f.id_venta = v.id_venta AND f.tipo_movimiento = 'FIADO'
                           WHERE v.id_turno_caja = tt.id_turno_caja
                             AND (f.saldo_pendiente > 0
                                  OR EXISTS (SELECT 1 FROM movimientos_cuenta c
                                              WHERE c.id_fiado = f.id_movimiento_cuenta
                                                AND c.id_turno_caja IS NOT NULL
                                                AND c.id_turno_caja NOT IN (SELECT id_turno_caja FROM tmp_turnos))))""",
        # Un cobro hecho en este turno de un fiado de un turno que se queda.
        """DELETE FROM tmp_turnos tt
            WHERE EXISTS (SELECT 1 FROM movimientos_cuenta c
                            JOIN movimientos_cuenta f ON f.id_movimiento_cuenta = c.id_fiado
                            JOIN ventas v ON v.id_venta = f.id_venta
                           WHERE c.id_turno_caja = tt.id_turno_caja
                             AND v.id_turno_caja NOT IN (SELECT id_turno_caja FROM tmp_turnos))""",
    ],
    'despues': [
        """CREATE TEMP TABLE tmp_ventas ON COMMIT DROP AS
             SELECT id_venta FROM ventas
              WHERE id_turno_caja IN (SELECT id_turno_caja FROM tmp_turnos)""",
        """CREATE TEMP TABLE tmp_cambios ON COMMIT DROP AS
             SELECT id_cambio FROM cambios
              WHERE id_turno_caja IN (SELECT id_turno_caja FROM tmp_turnos)""",
        """CREATE TEMP TABLE tmp_movcuenta ON COMMIT DROP AS
             SELECT f.id_movimiento_cuenta FROM movimientos_cuenta f
              WHERE f.id_venta IN (SELECT id_venta FROM tmp_ventas)
             UNION
             SELECT c.id_movimiento_cuenta FROM movimientos_cuenta c
               JOIN movimientos_cuenta f ON f.id_movimiento_cuenta = c.id_fiado
              WHERE f.id_venta IN (SELECT id_venta FROM tmp_ventas)
             UNION
             SELECT m.id_movimiento_cuenta FROM movimientos_cuenta m
              WHERE m.id_turno_caja IN (SELECT id_turno_caja FROM tmp_turnos)""",
    ],
    'copias': [
        ('movimientos_cuenta',
         """INSERT INTO hist_movimientos_cuenta
            SELECT m.*, CURRENT_TIMESTAMP FROM movimientos_cuenta m
             WHERE id_movimiento_cuenta IN (SELECT id_movimiento_cuenta FROM tmp_movcuenta)"""),
        ('movimientos_puntos',
         """INSERT INTO hist_movimientos_puntos
            SELECT m.*, CURRENT_TIMESTAMP FROM movimientos_puntos m
             WHERE id_venta IN (SELECT id_venta FROM tmp_ventas)"""),
        ('movimientos_stock (ventas)',
         """INSERT INTO hist_movimientos_stock
            SELECT m.*, CURRENT_TIMESTAMP FROM movimientos_stock m
             WHERE id_venta IN (SELECT id_venta FROM tmp_ventas)
                OR id_cambio IN (SELECT id_cambio FROM tmp_cambios)"""),
        ('movimientos_caja',
         """INSERT INTO hist_movimientos_caja
            SELECT m.*, CURRENT_TIMESTAMP FROM movimientos_caja m
             WHERE id_turno_caja IN (SELECT id_turno_caja FROM tmp_turnos)"""),
        ('detalles_cambio',
         """INSERT INTO hist_detalles_cambio
            SELECT d.*, CURRENT_TIMESTAMP FROM detalles_cambio d
             WHERE id_cambio IN (SELECT id_cambio FROM tmp_cambios)"""),
        ('cambios',
         """INSERT INTO hist_cambios
            SELECT c.*, CURRENT_TIMESTAMP FROM cambios c
             WHERE id_cambio IN (SELECT id_cambio FROM tmp_cambios)"""),
        ('pagos_venta',
         """INSERT INTO hist_pagos_venta
            SELECT p.*, CURRENT_TIMESTAMP FROM pagos_venta p
             WHERE id_venta IN (SELECT id_venta FROM tmp_ventas)"""),
        ('detalles_venta',
         """INSERT INTO hist_detalles_venta
            SELECT d.*, CURRENT_TIMESTAMP FROM detalles_venta d
             WHERE id_venta IN (SELECT id_venta FROM tmp_ventas)"""),
        # Las columnas van NOMBRADAS: las de la anulación (ventas 0003) se
        # agregaron al final de las dos tablas, y en hist_ventas quedaron
        # después de fecha_archivo, así que ``v.*`` ya no coincide.
        ('ventas',
         """INSERT INTO hist_ventas (""" + COLUMNAS_VENTAS + """, fecha_archivo)
            SELECT """ + COLUMNAS_VENTAS + """, CURRENT_TIMESTAMP FROM ventas v
             WHERE id_venta IN (SELECT id_venta FROM tmp_ventas)"""),
        ('turnos_caja',
         """INSERT INTO hist_turnos_caja (""" + COLUMNAS_TURNOS + """, fecha_archivo)
            SELECT """ + COLUMNAS_TURNOS + """, CURRENT_TIMESTAMP FROM turnos_caja c
             WHERE id_turno_caja IN (SELECT id_turno_caja FROM tmp_turnos)"""),
    ],
    # Regla 3: lo copiado tiene que coincidir con lo seleccionado.
    'verificacion': ('ventas', 'SELECT COUNT(*) FROM tmp_ventas'),
    'borrados': [
        """DELETE FROM movimientos_cuenta
            WHERE id_movimiento_cuenta IN (SELECT id_movimiento_cuenta FROM tmp_movcuenta)
              AND id_fiado IS NOT NULL""",
        "DELETE FROM movimientos_cuenta WHERE id_movimiento_cuenta IN (SELECT id_movimiento_cuenta FROM tmp_movcuenta)",
        "DELETE FROM movimientos_puntos WHERE id_venta IN (SELECT id_venta FROM tmp_ventas)",
        """DELETE FROM movimientos_stock
            WHERE id_venta IN (SELECT id_venta FROM tmp_ventas)
               OR id_cambio IN (SELECT id_cambio FROM tmp_cambios)""",
        "DELETE FROM movimientos_caja   WHERE id_turno_caja IN (SELECT id_turno_caja FROM tmp_turnos)",
        "DELETE FROM detalles_cambio    WHERE id_cambio IN (SELECT id_cambio FROM tmp_cambios)",
        "DELETE FROM cambios            WHERE id_cambio IN (SELECT id_cambio FROM tmp_cambios)",
        "DELETE FROM pagos_venta        WHERE id_venta IN (SELECT id_venta FROM tmp_ventas)",
        "DELETE FROM detalles_venta     WHERE id_venta IN (SELECT id_venta FROM tmp_ventas)",
        "DELETE FROM ventas             WHERE id_venta IN (SELECT id_venta FROM tmp_ventas)",
        "DELETE FROM turnos_caja        WHERE id_turno_caja IN (SELECT id_turno_caja FROM tmp_turnos)",
    ],
}

# Lo que tiene que cumplir un lote para irse con su documento de origen:
# sin stock, sin asientos de OTROS documentos y sin renglones de ajuste.
LOTE_CERRADO = """
    l.stock_lote = 0
    AND NOT EXISTS (SELECT 1 FROM movimientos_stock m
                     WHERE m.id_lote = l.id_lote
                       AND m.id_compra IS DISTINCT FROM %s
                       AND m.id_produccion IS DISTINCT FROM %s)
    AND NOT EXISTS (SELECT 1 FROM detalles_ajuste da WHERE da.id_lote = l.id_lote)
"""

FAMILIA_COMPRA = {
    'nombre': 'Compras pagadas',
    'seleccion': [
        # Una compra sólo se archiva si ya no queda NADA vivo colgando de
        # ella: ni stock de sus lotes, ni movimientos ajenos sobre esos
        # lotes, ni plata de caja atada a sus egresos.
        """CREATE TEMP TABLE tmp_compras ON COMMIT DROP AS
             SELECT c.id_compra FROM compras c
              WHERE c.estado_compra = 'PAGADA' AND c.fecha_compra < %(compras)s
                AND NOT EXISTS (SELECT 1 FROM egresos e
                                 WHERE e.id_compra = c.id_compra
                                   AND e.estado_pago NOT IN ('ACREDITADO','RECHAZADO'))
                AND NOT EXISTS (SELECT 1 FROM lotes l
                                  JOIN detalles_compra d USING (id_detalle_compra)
                                 WHERE d.id_compra = c.id_compra AND NOT ("""
        + (LOTE_CERRADO % ('c.id_compra', 'NULL')) + """))
                AND NOT EXISTS (SELECT 1 FROM movimientos_caja mc
                                  JOIN egresos e USING (id_egreso)
                                 WHERE e.id_compra = c.id_compra)""",
        """CREATE TEMP TABLE tmp_lotes ON COMMIT DROP AS
             SELECT l.id_lote FROM lotes l
               JOIN detalles_compra d USING (id_detalle_compra)
              WHERE d.id_compra IN (SELECT id_compra FROM tmp_compras)""",
    ],
    'copias': [
        ('facturas_recibidas (confirmadas)',
         """INSERT INTO hist_facturas_recibidas
            SELECT f.*, CURRENT_TIMESTAMP FROM facturas_recibidas f
             WHERE id_compra IN (SELECT id_compra FROM tmp_compras)"""),
        ('movimientos_stock (compras)',
         """INSERT INTO hist_movimientos_stock
            SELECT m.*, CURRENT_TIMESTAMP FROM movimientos_stock m
             WHERE id_compra IN (SELECT id_compra FROM tmp_compras)
                OR id_lote IN (SELECT id_lote FROM tmp_lotes)"""),
        ('egresos (de compras)',
         """INSERT INTO hist_egresos
            SELECT e.*, CURRENT_TIMESTAMP FROM egresos e
             WHERE id_compra IN (SELECT id_compra FROM tmp_compras)"""),
        ('lotes',
         """INSERT INTO hist_lotes
            SELECT l.*, CURRENT_TIMESTAMP FROM lotes l
             WHERE id_lote IN (SELECT id_lote FROM tmp_lotes)"""),
        ('detalles_compra',
         """INSERT INTO hist_detalles_compra
            SELECT d.*, CURRENT_TIMESTAMP FROM detalles_compra d
             WHERE id_compra IN (SELECT id_compra FROM tmp_compras)"""),
        ('compras',
         """INSERT INTO hist_compras (""" + COLUMNAS_COMPRAS + """, fecha_archivo)
            SELECT """ + COLUMNAS_COMPRAS + """, CURRENT_TIMESTAMP FROM compras c
             WHERE id_compra IN (SELECT id_compra FROM tmp_compras)"""),
    ],
    'verificacion': ('compras', 'SELECT COUNT(*) FROM tmp_compras'),
    'borrados': [
        "DELETE FROM facturas_recibidas WHERE id_compra IN (SELECT id_compra FROM tmp_compras)",
        """DELETE FROM movimientos_stock
            WHERE id_compra IN (SELECT id_compra FROM tmp_compras)
               OR id_lote IN (SELECT id_lote FROM tmp_lotes)""",
        "DELETE FROM egresos WHERE id_compra IN (SELECT id_compra FROM tmp_compras)",
        "DELETE FROM lotes   WHERE id_lote IN (SELECT id_lote FROM tmp_lotes)",
        "DELETE FROM detalles_compra WHERE id_compra IN (SELECT id_compra FROM tmp_compras)",
        "DELETE FROM compras WHERE id_compra IN (SELECT id_compra FROM tmp_compras)",
    ],
}

FAMILIA_ORDEN = {
    'nombre': 'Órdenes de compra cerradas',
    'seleccion': [
        # Sin compra asociada: si la tiene, se va con la familia anterior.
        """CREATE TEMP TABLE tmp_ordenes ON COMMIT DROP AS
             SELECT o.id_orden_compra FROM ordenes_compra o
              WHERE o.estado_orden IN ('RECEPCIONADA','CANCELADA')
                AND o.fecha_orden < %(compras)s
                AND NOT EXISTS (SELECT 1 FROM compras c
                                 WHERE c.id_orden_compra = o.id_orden_compra)""",
    ],
    'copias': [
        ('detalles_orden_compra',
         """INSERT INTO hist_detalles_orden_compra
            SELECT d.*, CURRENT_TIMESTAMP FROM detalles_orden_compra d
             WHERE id_orden_compra IN (SELECT id_orden_compra FROM tmp_ordenes)"""),
        ('ordenes_compra',
         """INSERT INTO hist_ordenes_compra
            SELECT o.*, CURRENT_TIMESTAMP FROM ordenes_compra o
             WHERE id_orden_compra IN (SELECT id_orden_compra FROM tmp_ordenes)"""),
    ],
    'verificacion': ('ordenes_compra', 'SELECT COUNT(*) FROM tmp_ordenes'),
    'borrados': [
        "DELETE FROM detalles_orden_compra WHERE id_orden_compra IN (SELECT id_orden_compra FROM tmp_ordenes)",
        "DELETE FROM ordenes_compra WHERE id_orden_compra IN (SELECT id_orden_compra FROM tmp_ordenes)",
    ],
}

FAMILIA_AJUSTE = {
    'nombre': 'Ajustes de stock resueltos',
    'seleccion': [
        """CREATE TEMP TABLE tmp_ajustes ON COMMIT DROP AS
             SELECT a.id_ajuste FROM ajustes_stock a
              WHERE a.estado_ajuste IN ('APROBADO','RECHAZADO')
                AND a.fecha_resolucion < %(operativo)s""",
    ],
    'copias': [
        ('movimientos_stock (ajustes)',
         """INSERT INTO hist_movimientos_stock
            SELECT m.*, CURRENT_TIMESTAMP FROM movimientos_stock m
             WHERE id_ajuste IN (SELECT id_ajuste FROM tmp_ajustes)"""),
        ('detalles_ajuste',
         """INSERT INTO hist_detalles_ajuste
            SELECT d.*, CURRENT_TIMESTAMP FROM detalles_ajuste d
             WHERE id_ajuste IN (SELECT id_ajuste FROM tmp_ajustes)"""),
        ('ajustes_stock',
         """INSERT INTO hist_ajustes_stock
            SELECT a.*, CURRENT_TIMESTAMP FROM ajustes_stock a
             WHERE id_ajuste IN (SELECT id_ajuste FROM tmp_ajustes)"""),
    ],
    'verificacion': ('ajustes_stock', 'SELECT COUNT(*) FROM tmp_ajustes'),
    'borrados': [
        "DELETE FROM movimientos_stock WHERE id_ajuste IN (SELECT id_ajuste FROM tmp_ajustes)",
        "DELETE FROM detalles_ajuste   WHERE id_ajuste IN (SELECT id_ajuste FROM tmp_ajustes)",
        "DELETE FROM ajustes_stock     WHERE id_ajuste IN (SELECT id_ajuste FROM tmp_ajustes)",
    ],
}

FAMILIA_PRODUCCION = {
    'nombre': 'Producciones viejas',
    'seleccion': [
        """CREATE TEMP TABLE tmp_producciones ON COMMIT DROP AS
             SELECT p.id_produccion FROM producciones p
              WHERE p.fecha_produccion < %(operativo)s
                AND NOT EXISTS (SELECT 1 FROM lotes l
                                 WHERE l.id_produccion = p.id_produccion
                                   AND NOT ("""
        + (LOTE_CERRADO % ('NULL', 'p.id_produccion')) + """))""",
        """CREATE TEMP TABLE tmp_lotes_prod ON COMMIT DROP AS
             SELECT l.id_lote FROM lotes l
              WHERE l.id_produccion IN (SELECT id_produccion FROM tmp_producciones)""",
    ],
    'copias': [
        ('movimientos_stock (producción)',
         """INSERT INTO hist_movimientos_stock
            SELECT m.*, CURRENT_TIMESTAMP FROM movimientos_stock m
             WHERE id_produccion IN (SELECT id_produccion FROM tmp_producciones)
                OR id_lote IN (SELECT id_lote FROM tmp_lotes_prod)"""),
        ('lotes (producción)',
         """INSERT INTO hist_lotes
            SELECT l.*, CURRENT_TIMESTAMP FROM lotes l
             WHERE id_lote IN (SELECT id_lote FROM tmp_lotes_prod)"""),
        ('producciones',
         """INSERT INTO hist_producciones
            SELECT p.*, CURRENT_TIMESTAMP FROM producciones p
             WHERE id_produccion IN (SELECT id_produccion FROM tmp_producciones)"""),
    ],
    'verificacion': ('producciones', 'SELECT COUNT(*) FROM tmp_producciones'),
    'borrados': [
        """DELETE FROM movimientos_stock
            WHERE id_produccion IN (SELECT id_produccion FROM tmp_producciones)
               OR id_lote IN (SELECT id_lote FROM tmp_lotes_prod)""",
        "DELETE FROM lotes        WHERE id_lote IN (SELECT id_lote FROM tmp_lotes_prod)",
        "DELETE FROM producciones WHERE id_produccion IN (SELECT id_produccion FROM tmp_producciones)",
    ],
}

FAMILIA_EGRESO = {
    'nombre': 'Egresos sin compra (servicios, impuestos)',
    'seleccion': [
        """CREATE TEMP TABLE tmp_egresos ON COMMIT DROP AS
             SELECT e.id_egreso FROM egresos e
              WHERE e.id_compra IS NULL
                AND e.estado_pago IN ('ACREDITADO','RECHAZADO')
                AND e.fecha_egreso < %(compras)s
                AND NOT EXISTS (SELECT 1 FROM movimientos_caja m
                                 WHERE m.id_egreso = e.id_egreso)""",
    ],
    'copias': [
        ('egresos (sin compra)',
         """INSERT INTO hist_egresos
            SELECT e.*, CURRENT_TIMESTAMP FROM egresos e
             WHERE id_egreso IN (SELECT id_egreso FROM tmp_egresos)"""),
    ],
    'verificacion': ('egresos (sin compra)', 'SELECT COUNT(*) FROM tmp_egresos'),
    'borrados': [
        "DELETE FROM egresos WHERE id_egreso IN (SELECT id_egreso FROM tmp_egresos)",
    ],
}

FAMILIA_SOLICITUDES = {
    'nombre': 'Pedidos de autorización a distancia resueltos',
    'seleccion': [
        """CREATE TEMP TABLE tmp_solicitudes ON COMMIT DROP AS
             SELECT s.id_solicitud FROM solicitudes_autorizacion s
              WHERE s.fecha_solicitud < %(solicitudes)s
                AND (s.estado_solicitud IN ('USADA','ANULADA','RECHAZADA')
                     -- vencida: nadie la contestó y ya no sirve (vale minutos)
                     OR s.fecha_vencimiento < CURRENT_TIMESTAMP)""",
    ],
    'copias': [
        ('solicitudes_autorizacion',
         """INSERT INTO hist_solicitudes_autorizacion
            SELECT s.*, CURRENT_TIMESTAMP FROM solicitudes_autorizacion s
             WHERE id_solicitud IN (SELECT id_solicitud FROM tmp_solicitudes)"""),
    ],
    'verificacion': ('solicitudes_autorizacion', 'SELECT COUNT(*) FROM tmp_solicitudes'),
    'borrados': [
        "DELETE FROM solicitudes_autorizacion WHERE id_solicitud IN (SELECT id_solicitud FROM tmp_solicitudes)",
    ],
}

FAMILIA_FACTURAS_DESCARTADAS = {
    'nombre': 'Facturas recibidas descartadas',
    'seleccion': [
        """CREATE TEMP TABLE tmp_facturas ON COMMIT DROP AS
             SELECT f.id_factura_recibida FROM facturas_recibidas f
              WHERE f.estado_factura = 'DESCARTADA' AND f.fecha_resolucion < %(log)s""",
    ],
    'copias': [
        ('facturas_recibidas (descartadas)',
         """INSERT INTO hist_facturas_recibidas
            SELECT f.*, CURRENT_TIMESTAMP FROM facturas_recibidas f
             WHERE id_factura_recibida IN (SELECT id_factura_recibida FROM tmp_facturas)"""),
    ],
    'verificacion': ('facturas_recibidas (descartadas)', 'SELECT COUNT(*) FROM tmp_facturas'),
    'borrados': [
        "DELETE FROM facturas_recibidas WHERE id_factura_recibida IN (SELECT id_factura_recibida FROM tmp_facturas)",
    ],
}

FAMILIA_AUDITORIA = {
    'nombre': 'Auditoría, sesiones e intentos de acceso',
    'seleccion': [
        """CREATE TEMP TABLE tmp_sesiones ON COMMIT DROP AS
             SELECT s.id_sesion FROM sesiones s
              WHERE NOT s.activa AND s.fecha_cierre < %(log)s
                AND NOT EXISTS (SELECT 1 FROM log_auditoria l
                                 WHERE l.id_sesion = s.id_sesion
                                   AND l.fecha_hora >= %(log)s)""",
        # Lo que se copia y lo que se borra sale de estas listas, no de un
        # ``fecha_hora < corte`` repetido en cada sentencia: un asiento que
        # entrara entre la copia y el borrado se borraría sin haberse
        # copiado (regla 3).
        """CREATE TEMP TABLE tmp_log ON COMMIT DROP AS
             SELECT l.id_auditoria FROM log_auditoria l
              WHERE l.fecha_hora < %(log)s""",
        """CREATE TEMP TABLE tmp_intentos ON COMMIT DROP AS
             SELECT i.id_intento FROM intentos_acceso i
              WHERE i.fecha_hora < %(log)s""",
    ],
    'copias': [
        # El log primero: DD-23, una sesión sólo se archiva cuando ya no la
        # referencia ningún asiento vivo.
        ('log_auditoria',
         """INSERT INTO hist_log_auditoria (""" + COLUMNAS_LOG + """, fecha_archivo)
            SELECT """ + COLUMNAS_LOG + """, CURRENT_TIMESTAMP FROM log_auditoria l
             WHERE id_auditoria IN (SELECT id_auditoria FROM tmp_log)"""),
        ('sesiones',
         """INSERT INTO hist_sesiones
            SELECT s.*, CURRENT_TIMESTAMP FROM sesiones s
             WHERE id_sesion IN (SELECT id_sesion FROM tmp_sesiones)"""),
        ('intentos_acceso',
         """INSERT INTO hist_intentos_acceso
            SELECT i.*, CURRENT_TIMESTAMP FROM intentos_acceso i
             WHERE id_intento IN (SELECT id_intento FROM tmp_intentos)"""),
    ],
    # Regla 3, igual que en las demás familias, pero con tres tablas: cada
    # una se cuenta contra su propia lista.
    'verificacion': [
        ('log_auditoria', 'SELECT COUNT(*) FROM tmp_log'),
        ('sesiones', 'SELECT COUNT(*) FROM tmp_sesiones'),
        ('intentos_acceso', 'SELECT COUNT(*) FROM tmp_intentos'),
    ],
    'borrados': [
        "DELETE FROM log_auditoria WHERE id_auditoria IN (SELECT id_auditoria FROM tmp_log)",
        "DELETE FROM sesiones WHERE id_sesion IN (SELECT id_sesion FROM tmp_sesiones)",
        "DELETE FROM intentos_acceso WHERE id_intento IN (SELECT id_intento FROM tmp_intentos)",
    ],
}

# El orden importa: las ventas se van antes que las compras cuyos lotes
# vendieron, y los ajustes antes que las compras cuyos lotes contaron.
# Los pedidos de autorización primero: apuntan a turnos y ventas.
COPIA_POR_POSICION = re.compile(
    r'INSERT INTO (hist_\w+)\s+SELECT (\w+)\.\*, CURRENT_TIMESTAMP FROM (\w+) \2\b')

FAMILIAS = [FAMILIA_SOLICITUDES, FAMILIA_CAJA, FAMILIA_AJUSTE, FAMILIA_PRODUCCION,
            FAMILIA_COMPRA, FAMILIA_ORDEN, FAMILIA_EGRESO, FAMILIA_FACTURAS_DESCARTADAS,
            FAMILIA_AUDITORIA]


class Command(BaseCommand):
    help = ('Archiva en las tablas hist_* lo que ya está cerrado y es '
            'antiguo. Es la única baja física del sistema (RN-8.1.1).')

    def add_arguments(self, parser):
        parser.add_argument(
            '--ejecutar', action='store_true',
            help='Sin esto sólo informa cuánto archivaría, sin tocar nada.')
        parser.add_argument('--meses-operativo', type=int,
                            default=CORTE_OPERATIVO)
        parser.add_argument('--meses-compras', type=int, default=CORTE_COMPRAS)
        parser.add_argument('--meses-log', type=int, default=CORTE_LOG)
        parser.add_argument('--meses-solicitudes', type=int, default=CORTE_SOLICITUDES)

    def handle(self, *args, **opciones):
        cortes = {
            'operativo': "CURRENT_DATE - INTERVAL '%d months'"
                         % opciones['meses_operativo'],
            'compras': "CURRENT_DATE - INTERVAL '%d months'"
                       % opciones['meses_compras'],
            'log': "CURRENT_DATE - INTERVAL '%d months'"
                   % opciones['meses_log'],
            'solicitudes': "CURRENT_DATE - INTERVAL '%d months'"
                           % opciones['meses_solicitudes'],
        }

        if not opciones['ejecutar']:
            self.stdout.write(self.style.WARNING(
                'MODO SIMULACIÓN: se informa qué se archivaría y al final se '
                'deshace todo. Agregá --ejecutar para archivar de verdad.'))
        self.stdout.write('')

        total = 0
        # `pasaje_historico()` es lo que habilita el borrado físico: fuera
        # de este bloque, SinBajaFisica y LibroInmutable lo rechazan.
        with contexto.pasaje_historico(), transaction.atomic():
            with connection.cursor() as cursor:
                # El trigger de los libros (DD-68) sólo deja borrar mientras
                # dure el archivado. SET LOCAL ya se deshace al terminar la
                # transacción; se apaga igual a mano por si este comando
                # corre dentro de otra transacción.
                cursor.execute("SET LOCAL cafeto.pasaje_historico = 'on'")
                try:
                    for familia in FAMILIAS:
                        total += self._archivar(cursor, familia, cortes)
                finally:
                    try:
                        cursor.execute("SET LOCAL cafeto.pasaje_historico = 'off'")
                    except DatabaseError:
                        pass   # transacción abortada: el rollback ya la apaga

            if not opciones['ejecutar']:
                transaction.set_rollback(True)

        self.stdout.write('')
        estilo = self.style.SUCCESS if opciones['ejecutar'] else self.style.WARNING
        self.stdout.write(estilo(
            '%d fila(s) %s.' % (total, 'archivadas' if opciones['ejecutar']
                                else 'se archivarían')))

    def _archivar(self, cursor, familia, cortes):
        self.stdout.write(self.style.MIGRATE_HEADING(familia['nombre']))

        for sentencia in familia['seleccion']:
            cursor.execute(self._con_cortes(sentencia, cortes))
        for _vuelta in range(100):
            sacadas = 0
            for sentencia in familia.get('iterar', []):
                cursor.execute(self._con_cortes(sentencia, cortes))
                sacadas += cursor.rowcount
            if not sacadas:
                break
        for sentencia in familia.get('despues', []):
            cursor.execute(self._con_cortes(sentencia, cortes))

        total = 0
        copiadas = {}
        for etiqueta, sentencia in familia['copias']:
            cursor.execute(self._por_nombre(cursor, self._con_cortes(sentencia, cortes)))
            copiadas[etiqueta] = cursor.rowcount
            total += cursor.rowcount
            if cursor.rowcount:
                self.stdout.write('    %-30s %6d' % (etiqueta, cursor.rowcount))

        # Regla 3: si lo copiado no coincide con lo seleccionado, se aborta
        # ANTES de borrar. La transacción se encarga del resto.
        # Una verificación es ``(etiqueta, consulta)``; una familia que
        # copia varias tablas de listas distintas lleva una lista de ellas.
        verificacion = familia.get('verificacion') or []
        if isinstance(verificacion, tuple):
            verificacion = [verificacion]
        for etiqueta, consulta in verificacion:
            cursor.execute(self._con_cortes(consulta, cortes))
            esperadas = cursor.fetchone()[0]
            if copiadas.get(etiqueta, 0) != esperadas:
                raise RuntimeError(
                    'Pasaje abortado en "%s": se seleccionaron %d filas de '
                    '%s y se copiaron %d. No se borró nada.'
                    % (familia['nombre'], esperadas, etiqueta,
                       copiadas.get(etiqueta, 0)))

        for sentencia in familia['borrados']:
            cursor.execute(self._con_cortes(sentencia, cortes))

        if not total:
            self.stdout.write('    (nada para archivar)')
        return total

    @staticmethod
    def _por_nombre(cursor, sentencia):
        """``INSERT INTO hist_x SELECT a.*, ...`` copia por POSICIÓN: si a
        la tabla viva se le agregó una columna después de crear su histórico,
        la del histórico quedó detrás de ``fecha_archivo`` y los valores se
        correrían de lugar (o, peor, entrarían en la columna equivocada sin
        error si los tipos coinciden). Se reescribe con las columnas por
        nombre, leídas del catálogo."""
        encontrado = COPIA_POR_POSICION.search(sentencia)
        if not encontrado:
            return sentencia
        historico, alias, viva = encontrado.groups()
        cursor.execute(
            'SELECT column_name FROM information_schema.columns '
            'WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position', [viva])
        columnas = [fila[0] for fila in cursor.fetchall()]
        destino = ', '.join(columnas)
        origen = ', '.join(f'{alias}.{c}' for c in columnas)
        return COPIA_POR_POSICION.sub(
            f'INSERT INTO {historico} ({destino}, fecha_archivo) '
            f'SELECT {origen}, CURRENT_TIMESTAMP FROM {viva} {alias}', sentencia, count=1)

    @staticmethod
    def _con_cortes(sentencia, cortes):
        """Interpola los cortes de fecha.

        Van interpolados y no como parámetros porque son EXPRESIONES de SQL
        (``CURRENT_DATE - INTERVAL '24 months'``), no valores. Es seguro: se
        arman acá a partir de enteros validados por argparse, nunca de la
        entrada de un usuario.
        """
        return sentencia % cortes
