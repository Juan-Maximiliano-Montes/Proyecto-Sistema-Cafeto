"""Qué decir cuando la base rechaza algo (DD-101).

Una regla de PostgreSQL (CHECK o UNIQUE) que se dispara llega como
``IntegrityError``. En pantalla no va el nombre de la restricción ni el SQL
(le dicen a la persona cómo se llama una tabla, no qué hizo mal), sino qué
dato revisar. Este diccionario cubre las tablas donde un rechazo deja a
alguien trabado en el medio de algo: la caja, las compras, las facturas
recibidas, la cuenta corriente y las autorizaciones.
"""

POR_RESTRICCION = {
    # --- Turnos de caja -------------------------------------------------
    'ck_turnos_caja_estado': 'El turno sólo puede estar abierto o cerrado.',
    'ck_turnos_caja_tipo_cierre': 'Elegí cómo se cierra el turno: cerrar, cambio de turno o con aprobación.',
    'ck_turnos_caja_montos': 'El fondo y lo contado no pueden ser negativos: revisá los montos del cajón.',
    'ck_turnos_caja_cierre': ('Al cierre le falta un dato (lo contado, quién cierra o cómo se cierra). '
                              'Volvé a confirmar el cierre.'),
    'ck_turnos_caja_justificacion': 'La caja cierra con diferencia: escribí a qué se debe en el motivo.',
    'ck_turnos_caja_supervision': ('Quien aprueba la diferencia tiene que ser otra persona, no quien cierra: '
                                   'revisá «Usuario o DNI de quien autoriza».'),
    'ck_turnos_caja_fechas': 'La fecha de cierre no puede ser anterior a la de apertura.',
    'ck_turnos_caja_ausencia': ('Cerrar el turno de otra persona lo hace quien llega, no quien lo abrió: '
                                'si el turno es tuyo, elegí «Cerrar la caja».'),
    'ck_turnos_caja_revision': ('Sólo queda pendiente de revisión un cierre con diferencia que nadie aprobó, '
                                'y la revisa otra persona que la que cerró.'),
    'ck_turnos_caja_anterior': 'Un turno no puede relevarse a sí mismo.',
    'uq_turno_abierto_por_caja': 'Esta caja ya tiene un turno abierto: se vende en ese turno.',
    'uq_turnos_caja_anterior': 'Ese turno ya se relevó: lo sigue otro turno.',
    # --- Compras --------------------------------------------------------
    'ck_compras_comprobante': 'Revisá el tipo, el punto de venta y el número del comprobante.',
    'ck_compras_estado': 'El estado de la compra no es válido.',
    'ck_compras_valores': ('Revisá los importes: el total tiene que ser mayor que cero, los otros conceptos '
                           'menores que el total y el pago no puede vencer antes de la factura.'),
    'compras_numero_comprobante_check': 'El número del comprobante no puede ser negativo.',
    'compras_punto_venta_check': 'El punto de venta no puede ser negativo.',
    'uq_compras_comprobante': ('Ese comprobante de ese proveedor ya está cargado (mismo tipo, punto de venta y '
                               'número): fijate en Compras antes de cargarlo de nuevo.'),
    # --- Facturas recibidas ---------------------------------------------
    'ck_factrecibidas_dominios': 'La factura recibida tiene un estado o un origen que no corresponde.',
    'ck_factrecibidas_resolucion': ('Esa factura recibida ya se confirmó o se descartó: recargá la bandeja de '
                                    'Compras.'),
    'uq_factrecibidas_pendiente': 'Esa misma factura ya está en la bandeja esperando que la revisen.',
    'uq_factrecibidas_compra': 'Esa compra ya confirmó otra factura recibida.',
    # --- Cuenta corriente -----------------------------------------------
    'ck_movcuenta_tipo': 'El movimiento de la cuenta corriente no es de un tipo válido.',
    'ck_movcuenta_fiado': ('Un fiado necesita la venta, el vencimiento y el saldo: volvé a cobrar el ticket '
                           'eligiendo «Cuenta corriente».'),
    'ck_movcuenta_aplicacion': 'Un cobro, una cancelación o un incobrable tienen que aplicarse a un fiado.',
    'ck_movcuenta_medio': ('Un cobro de cuenta corriente necesita el medio de pago (y la caja abierta si es '
                           'en efectivo).'),
    'uq_movcuenta_fiado_venta': 'Esa venta ya tiene su fiado anotado.',
    # --- Autorizaciones a distancia -------------------------------------
    'ck_solautoriz_valores': 'El pedido de autorización tiene datos incompletos: pedilo de nuevo.',
    'ck_solautoriz_resolucion': ('Ese pedido de autorización ya se resolvió (aprobado, rechazado o anulado): '
                                 'recargá la pantalla.'),
    'ck_solautoriz_venta': 'Ese pedido de autorización ya se usó en otra venta.',
    'solicitudes_autorizacion_intentos_pin_check': 'El PIN se probó demasiadas veces: pedí una autorización nueva.',
    'uq_solicitudes_autorizacion_token_hash': 'Ese pedido de autorización ya existe: pedilo de nuevo.',
}


def restriccion_de(error):
    """El nombre de la restricción que rechazó, o ''."""
    causa = getattr(error, '__cause__', None)
    diag = getattr(causa, 'diag', None)
    nombre = getattr(diag, 'constraint_name', None)
    if nombre:
        return nombre
    texto = str(error)
    for nombre in POR_RESTRICCION:
        if f'"{nombre}"' in texto:
            return nombre
    return ''


def mensaje(error):
    """El mensaje concreto de la restricción, o None si no está en el mapa."""
    return POR_RESTRICCION.get(restriccion_de(error))
