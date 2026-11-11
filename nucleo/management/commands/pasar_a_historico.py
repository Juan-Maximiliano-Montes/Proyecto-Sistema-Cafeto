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
1. **Nada se archiva sin estar cerrado.** El corte es siempre antigüedad Y
   estado: un turno CERRADO, una compra PAGADA, una orden RECEPCIONADA.
   Archivar algo abierto lo sacaría de la vista justo cuando hace falta.
2. **Se archiva por familia**, respetando las claves foráneas: los hijos
   primero, y un padre sólo se mueve si ningún hijo vivo lo referencia.
3. **Copiar, verificar y recién entonces borrar**, todo en UNA
   transacción. Si la verificación no cuadra, no se borra nada.
4. **El histórico es de sólo lectura**: una fotografía sin claves foráneas.

Uso::

    manage.py pasar_a_historico                  # muestra qué archivaría
    manage.py pasar_a_historico --ejecutar
    manage.py pasar_a_historico --ejecutar --meses-operativo 36
"""
from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection, transaction

from nucleo import contexto

# Cortes por defecto, en meses hacia atrás.
CORTE_OPERATIVO = 24      # turnos de caja, ventas, ajustes y producciones
CORTE_COMPRAS = 36        # compras, órdenes y egresos
CORTE_LOG = 12            # auditoría, sesiones e intentos de acceso

COLUMNAS_VENTAS = (
    'id_venta, token_operacion, fecha_venta, hora_venta, subtotal, '
    'monto_descuento, ajuste_redondeo, monto_recargo, total_venta, '
    'comprobante_fiscal_ref, id_beneficio, id_cliente, id_turno_caja, '
    'id_usuario, estado_venta, fecha_anulacion, id_usuario_anulacion, '
    'motivo_anulacion, detalle_anulacion, id_usuario_solicitante')

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
        """CREATE TEMP TABLE tmp_turnos ON COMMIT DROP AS
             SELECT t.id_turno_caja FROM turnos_caja t
              WHERE t.estado_turno = 'CERRADA' AND t.fecha_cierre < %(operativo)s""",
        """DELETE FROM tmp_turnos tt
            WHERE EXISTS (SELECT 1 FROM ventas v JOIN cambios c USING (id_venta)
                           WHERE v.id_turno_caja = tt.id_turno_caja
                             AND c.id_turno_caja NOT IN (SELECT id_turno_caja FROM tmp_turnos))""",
        """CREATE TEMP TABLE tmp_ventas ON COMMIT DROP AS
             SELECT id_venta FROM ventas
              WHERE id_turno_caja IN (SELECT id_turno_caja FROM tmp_turnos)""",
        """CREATE TEMP TABLE tmp_cambios ON COMMIT DROP AS
             SELECT id_cambio FROM cambios
              WHERE id_turno_caja IN (SELECT id_turno_caja FROM tmp_turnos)""",
    ],
    'copias': [
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
         """INSERT INTO hist_turnos_caja
            SELECT c.*, CURRENT_TIMESTAMP FROM turnos_caja c
             WHERE id_turno_caja IN (SELECT id_turno_caja FROM tmp_turnos)"""),
    ],
    # Regla 3: lo copiado tiene que coincidir con lo seleccionado.
    'verificacion': ('ventas', 'SELECT COUNT(*) FROM tmp_ventas'),
    'borrados': [
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
         """INSERT INTO hist_compras
            SELECT c.*, CURRENT_TIMESTAMP FROM compras c
             WHERE id_compra IN (SELECT id_compra FROM tmp_compras)"""),
    ],
    'verificacion': ('compras', 'SELECT COUNT(*) FROM tmp_compras'),
    'borrados': [
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

FAMILIA_AUDITORIA = {
    'nombre': 'Auditoría, sesiones e intentos de acceso',
    'seleccion': [
        """CREATE TEMP TABLE tmp_sesiones ON COMMIT DROP AS
             SELECT s.id_sesion FROM sesiones s
              WHERE NOT s.activa AND s.fecha_cierre < %(log)s
                AND NOT EXISTS (SELECT 1 FROM log_auditoria l
                                 WHERE l.id_sesion = s.id_sesion
                                   AND l.fecha_hora >= %(log)s)""",
    ],
    'copias': [
        # El log primero: DD-23, una sesión sólo se archiva cuando ya no la
        # referencia ningún asiento vivo.
        ('log_auditoria',
         """INSERT INTO hist_log_auditoria (""" + COLUMNAS_LOG + """, fecha_archivo)
            SELECT """ + COLUMNAS_LOG + """, CURRENT_TIMESTAMP FROM log_auditoria l
             WHERE fecha_hora < %(log)s"""),
        ('sesiones',
         """INSERT INTO hist_sesiones
            SELECT s.*, CURRENT_TIMESTAMP FROM sesiones s
             WHERE id_sesion IN (SELECT id_sesion FROM tmp_sesiones)"""),
        ('intentos_acceso',
         """INSERT INTO hist_intentos_acceso
            SELECT i.*, CURRENT_TIMESTAMP FROM intentos_acceso i
             WHERE fecha_hora < %(log)s"""),
    ],
    'borrados': [
        "DELETE FROM log_auditoria WHERE fecha_hora < %(log)s",
        "DELETE FROM sesiones WHERE id_sesion IN (SELECT id_sesion FROM tmp_sesiones)",
        "DELETE FROM intentos_acceso WHERE fecha_hora < %(log)s",
    ],
}

# El orden importa: las ventas se van antes que las compras cuyos lotes
# vendieron, y los ajustes antes que las compras cuyos lotes contaron.
FAMILIAS = [FAMILIA_CAJA, FAMILIA_AJUSTE, FAMILIA_PRODUCCION, FAMILIA_COMPRA,
            FAMILIA_ORDEN, FAMILIA_EGRESO, FAMILIA_AUDITORIA]


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

    def handle(self, *args, **opciones):
        cortes = {
            'operativo': "CURRENT_DATE - INTERVAL '%d months'"
                         % opciones['meses_operativo'],
            'compras': "CURRENT_DATE - INTERVAL '%d months'"
                       % opciones['meses_compras'],
            'log': "CURRENT_DATE - INTERVAL '%d months'"
                   % opciones['meses_log'],
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

        total = 0
        copiadas = {}
        for etiqueta, sentencia in familia['copias']:
            cursor.execute(self._con_cortes(sentencia, cortes))
            copiadas[etiqueta] = cursor.rowcount
            total += cursor.rowcount
            if cursor.rowcount:
                self.stdout.write('    %-30s %6d' % (etiqueta, cursor.rowcount))

        # Regla 3: si lo copiado no coincide con lo seleccionado, se aborta
        # ANTES de borrar. La transacción se encarga del resto.
        verificacion = familia.get('verificacion')
        if verificacion:
            etiqueta, consulta = verificacion
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
    def _con_cortes(sentencia, cortes):
        """Interpola los cortes de fecha.

        Van interpolados y no como parámetros porque son EXPRESIONES de SQL
        (``CURRENT_DATE - INTERVAL '24 months'``), no valores. Es seguro: se
        arman acá a partir de enteros validados por argparse, nunca de la
        entrada de un usuario.
        """
        return sentencia % cortes
