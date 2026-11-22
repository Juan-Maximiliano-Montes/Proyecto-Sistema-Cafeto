"""Módulo Ventas y Caja.

Diez tablas:

    MEDIOS_PAGO          efectivo, transferencia, tarjetas, QR
    CAJAS_REGISTRADORAS  los cajones de cobro (Cafetería, Minisuper)
    TURNOS_CAJA          de la apertura al arqueo de cierre, por cajón
    MOVIMIENTOS_CAJA     el libro del efectivo del cajón (sólo agregado)
    VENTAS               el ticket
    DETALLES_VENTA       sus renglones, con precio y costo congelados
    PAGOS_VENTA          cómo se pagó: uno o varios medios (pago mixto)
    CAMBIOS              devolución o cambio de mercadería de una venta
    DETALLES_CAMBIO      lo que vuelve y lo que se entrega
    SOLICITUDES_AUTORIZACION  el permiso que la caja pide a distancia (DD-94)

El efectivo del cajón es la SUMA del libro de caja: no hay una columna
"saldo" que se pueda desincronizar.

Una venta no se borra ni se edita. Dos caminos según CUÁNDO aparece el
problema:

* **Anulación** (``ventas.anulacion``): el error se descubre en el
  mostrador, con el turno de esa caja todavía abierto (el pago fue
  rechazado, se cargó otro producto, se cobró dos veces). La venta queda
  ANULADA con quién, cuándo y por qué, y se contra-asienta todo lo que
  movió: stock, efectivo y puntos.
* **Cambio o devolución** (``ventas.cambios``): el cliente vuelve después.
  Se registra un CAMBIO que referencia la venta original, y el ticket
  queda tal como se emitió.
"""
from decimal import Decimal

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from nucleo.modelos import (
    Maestra,
    campo_cantidad,
    campo_importe,
    sello_usuario_modificacion,
)
from nucleo.motivos import MotivoCompleto
from nucleo.reglas import (
    LibroInmutable,
    ManagerDeLibro,
    ManagerSinBajaFisica,
    SinBajaFisica,
)
from nucleo.restricciones import CheckEnBase, CheckSQL, ClaveNormalizada
from nucleo.validaciones import (
    NO_NEGATIVO,
    clave_normalizada,
    exigir_nombre_unico,
    texto_de_catalogo,
    validar_decimal,
)
from nucleo.templatetags.cafeto import pesos


# ===========================================================================
#  MEDIOS DE PAGO
# ===========================================================================
class MedioPago(Maestra):
    """Forma de cobro.

    ``afecta_caja`` dice si la plata entra físicamente al cajón (el
    efectivo) o va a una cuenta (transferencia, tarjeta, QR). Sólo lo que
    afecta la caja admite vuelto y entra al arqueo.

    Recargo y cuotas (DD-65). La Ley 25.065 (art. 37, inc. c) prohíbe
    cobrar distinto con tarjeta que en efectivo, y la Resolución 51-E/2017
    de la Secretaría de Comercio aclara que eso vale para el pago en una
    sola cuota. Lo único que puede trasladarse es el costo de financiar en
    cuotas, informado. Por eso el recargo sólo existe en un plan de tarjeta
    de crédito en más de una cuota ("Crédito 3 cuotas"), y la base lo exige.
    """

    # El dato inicial protegido: su recargo es siempre 0 y no se da de baja,
    # porque el vuelto y el arqueo se calculan sobre el importe en efectivo.
    NOMBRE_PROTEGIDO = 'Efectivo'
    # El fiado (DD-91): un medio que no cobra nada en el momento. La venta
    # queda paga "a cuenta" y lo que se debe vive en MOVIMIENTOS_CUENTA.
    NOMBRE_CUENTA_CORRIENTE = 'Cuenta corriente'

    id_medio_pago = models.AutoField('ID', primary_key=True)
    nombre_medio_pago = models.CharField('Medio de pago', max_length=25)
    recargo_porcentaje = models.DecimalField(
        'Recargo (%)', max_digits=5, decimal_places=2, default=Decimal('0'),
        validators=[NO_NEGATIVO])
    afecta_caja = models.BooleanField(
        'Entra al cajón (efectivo)', default=False,
        help_text='Marcalo sólo si el dinero entra físicamente a la caja: '
                  'admite vuelto y se cuenta en el arqueo.')
    es_tarjeta_credito = models.BooleanField(
        'Es tarjeta de crédito', default=False,
        help_text='Sólo la tarjeta de crédito admite pagar en cuotas.')
    cantidad_cuotas = models.PositiveSmallIntegerField(
        'Cuotas', default=1,
        help_text='1 si es un solo pago. Un plan en cuotas es un medio de '
                  'pago aparte (por ejemplo, "Crédito 3 cuotas").')
    usuario_modificacion = sello_usuario_modificacion('medios_pago_modificados')

    @property
    def cuotas_para_mostrar(self):
        """En la base, ``cantidad_cuotas`` es 1 en todo lo que no es crédito
        (un solo pago: así las cuentas y la regla del recargo no tienen que
        pensar en un nulo). En pantalla, un "1" al lado del efectivo se
        leía como "1 cuota", y el efectivo no tiene cuotas."""
        if not self.es_tarjeta_credito:
            return 'No aplica'
        if self.cantidad_cuotas == 1:
            return 'Un pago'
        return f'{self.cantidad_cuotas} cuotas'

    @property
    def recargo_para_mostrar(self):
        """"9 %" y no "9": un número suelto al lado de "Recargo" no decía
        si eran pesos o porcentaje."""
        if not self.recargo_porcentaje:
            return 'Sin recargo'
        return f'{self.recargo_porcentaje.normalize():f} %'.replace('.', ',')

    @property
    def no_se_da_de_baja(self):
        """El efectivo no se da de baja (lo rechaza el modelo): la lista no
        ofrece el botón que después diría que no."""
        return self.es_efectivo

    class Meta:
        db_table = 'medios_pago'
        verbose_name = 'Medio de pago'
        verbose_name_plural = 'Medios de pago'
        ordering = ['nombre_medio_pago']
        constraints = [
            # Ley 25.065, art. 37 c) y Res. 51-E/2017: sin recargo en un
            # solo pago; sólo el costo de financiar en cuotas (DD-65).
            CheckEnBase(
                check=CheckSQL(
                    'recargo_porcentaje >= 0 AND recargo_porcentaje <= 100 '
                    'AND (recargo_porcentaje = 0 OR cantidad_cuotas > 1)'),
                name='ck_medios_pago_recargo'),
            # Las cuotas son de la tarjeta de crédito, y nada más.
            CheckEnBase(
                check=CheckSQL(
                    'cantidad_cuotas BETWEEN 1 AND 36 '
                    'AND (es_tarjeta_credito OR cantidad_cuotas = 1)'),
                name='ck_medios_pago_cuotas'),
            CheckEnBase(
                check=CheckSQL("trim(nombre_medio_pago) <> ''"),
                name='ck_medios_pago_textos'),
            # Lo que entra al cajón no lleva recargo: el vuelto y el arqueo
            # se calculan sobre el importe cobrado.
            CheckEnBase(
                check=CheckSQL('NOT afecta_caja OR recargo_porcentaje = 0'),
                name='ck_medios_pago_caja_sin_recargo'),
            models.UniqueConstraint(
                ClaveNormalizada('nombre_medio_pago'),
                name='uq_medios_pago_nombre_norm'),
        ]

    def __str__(self):
        return self.nombre_medio_pago

    @property
    def clave(self):
        return clave_normalizada(self.nombre_medio_pago)

    @property
    def es_efectivo(self):
        return clave_normalizada(self.nombre_medio_pago) == \
            clave_normalizada(self.NOMBRE_PROTEGIDO)

    @property
    def es_cuenta_corriente(self):
        return clave_normalizada(self.nombre_medio_pago) == \
            clave_normalizada(self.NOMBRE_CUENTA_CORRIENTE)

    def clean(self):
        super().clean()
        if self.pk and type(self).objects.filter(
                pk=self.pk, nombre_medio_pago=self.NOMBRE_CUENTA_CORRIENTE).exists() and (
                not self.es_cuenta_corriente or self.afecta_caja or self.recargo_porcentaje
                or self.es_tarjeta_credito):
            raise ValidationError(
                f'«{self.NOMBRE_CUENTA_CORRIENTE}» es el medio reservado para el fiado: '
                'no se le cambia el nombre, no entra al cajón y no lleva recargo.')
        # Mínimo 2: "QR" es un nombre de medio de pago legítimo (y es uno
        # de los datos iniciales; con 3 no se lo podía ni editar).
        self.nombre_medio_pago = texto_de_catalogo(
            self.nombre_medio_pago, 2, 25, 'El nombre del medio de pago')
        exigir_nombre_unico(self, 'nombre_medio_pago',
                            etiqueta='un medio de pago')
        self.recargo_porcentaje = validar_decimal(
            self.recargo_porcentaje, minimo=Decimal('0'),
            maximo=Decimal('100'), decimales=2, etiqueta='El recargo')

        if ((self.es_efectivo or self.afecta_caja)
                and self.recargo_porcentaje != Decimal('0')):
            raise ValidationError({'recargo_porcentaje': (
                'El efectivo no puede tener recargo: el vuelto y el arqueo '
                'de caja se calculan sobre el importe cobrado.')})
        if self.afecta_caja and self.es_tarjeta_credito:
            raise ValidationError({'es_tarjeta_credito': (
                'Una tarjeta de crédito no entra al cajón: desmarcá «Entra '
                'al cajón».')})
        if not self.es_tarjeta_credito:
            self.cantidad_cuotas = 1
        if not self.cantidad_cuotas or not 1 <= self.cantidad_cuotas <= 36:
            raise ValidationError({'cantidad_cuotas': (
                'Las cuotas van de 1 a 36.')})
        if self.recargo_porcentaje and self.cantidad_cuotas == 1:
            raise ValidationError({'recargo_porcentaje': (
                'Un pago en una sola cuota no lleva recargo: la Ley 25.065 '
                '(art. 37) prohíbe cobrar distinto con tarjeta que en '
                'efectivo. El recargo sólo es el costo de financiar un plan '
                'en cuotas.')})
        if self.pk is not None:
            anterior = type(self).objects.filter(pk=self.pk).values(
                'cantidad_cuotas', 'es_tarjeta_credito').first()
            if (anterior is not None and self.pagos.exists()
                    and (anterior['cantidad_cuotas'] != self.cantidad_cuotas
                         or anterior['es_tarjeta_credito'] != self.es_tarjeta_credito)):
                raise ValidationError({'cantidad_cuotas': (
                    'Este medio ya se usó para cobrar: sus cuotas no se '
                    'cambian (los tickets ya emitidos dicen otra cosa). '
                    'Para otro plan, da de alta un medio de pago nuevo.')})
        if self.es_efectivo:
            self.afecta_caja = True

    def motivos_que_impiden_la_baja(self):
        motivos = []
        if self.es_efectivo:
            # Mensaje COMPLETO y no fragmento: el efectivo no se bloquea por
            # tener dependientes —se bloquea por lo que ES—, así que la frase
            # no encaja detrás del "No se puede dar de baja X porque..." que
            # arma el CRUD para los demás casos.
            motivos.append(MotivoCompleto(
                'El efectivo no se puede dar de baja porque la caja lo '
                'necesita para dar vuelto y hacer el arqueo.'))
        quedan = type(self).objects.filter(borrado_logico=False).exclude(
            pk=self.pk).count()
        if not quedan:
            motivos.append('es el último medio de pago vigente')
        return motivos


# ===========================================================================
#  CAJAS REGISTRADORAS
# ===========================================================================
class CajaRegistradora(Maestra):
    """Cajón de cobro: la Cafetería y el Minisuper tienen el suyo.

    Una caja registradora no puede tener dos turnos abiertos (DD-42), pero
    en un turno abierto vende cualquier usuario habilitado: en la hora pico
    atienden dos o tres personas en el mismo cajón.
    """

    id_caja_registradora = models.AutoField('ID', primary_key=True)
    nombre_caja_registradora = models.CharField('Caja registradora', max_length=20)
    usuario_modificacion = sello_usuario_modificacion('cajas_registradoras_modificadas')

    class Meta:
        db_table = 'cajas_registradoras'
        verbose_name = 'Caja registradora'
        verbose_name_plural = 'Cajas registradoras'
        ordering = ['nombre_caja_registradora']
        constraints = [
            CheckEnBase(
                check=CheckSQL('length(trim(nombre_caja_registradora)) >= 3'),
                name='ck_cajas_registradoras_nombre'),
            CheckEnBase(
                check=CheckSQL("trim(nombre_caja_registradora) <> ''"),
                name='ck_cajas_registradoras_textos'),
            models.UniqueConstraint(
                ClaveNormalizada('nombre_caja_registradora'),
                name='uq_cajas_registradoras_nombre_norm'),
        ]

    def __str__(self):
        return self.nombre_caja_registradora

    @property
    def clave(self):
        return clave_normalizada(self.nombre_caja_registradora)

    def clean(self):
        super().clean()
        self.nombre_caja_registradora = texto_de_catalogo(
            self.nombre_caja_registradora, 3, 20, 'El nombre de la caja registradora')
        exigir_nombre_unico(self, 'nombre_caja_registradora',
                            etiqueta='una caja registradora')

    def motivos_que_impiden_la_baja(self):
        from ventas.models import TurnoCaja

        motivos = []
        if TurnoCaja.objects.filter(id_caja_registradora=self, estado_turno='ABIERTA').exists():
            motivos.append('tiene un turno de caja ABIERTO; cerralo primero')
        quedan = type(self).objects.filter(borrado_logico=False).exclude(
            pk=self.pk).count()
        if not quedan:
            motivos.append('es la última caja registradora vigente')
        return motivos


def hora_actual():
    """Hora local, para el default de VENTAS.hora_venta."""
    return timezone.localtime().time()


def _en(valores):
    return '(%s)' % ', '.join(f"'{v}'" for v in valores)


# ===========================================================================
#  CAJA
# ===========================================================================
class TipoCierre(models.TextChoices):
    NORMAL = 'NORMAL', 'Normal'
    RELEVO = 'RELEVO', 'Relevo (sigue otro turno)'
    SUPERVISADO = 'SUPERVISADO', 'Supervisado (con encargado/a)'


class TurnoCaja(SinBajaFisica, models.Model):
    """Turno de un cajón: de la apertura al arqueo de cierre.

    El turno es del CAJÓN, no de una persona: lo abre alguien, venden
    todos los que estén habilitados y lo cierra quien hace el arqueo. Así
    funcionan los cambios de turno de la tarde (RELEVO: se cuenta, se
    cierra y el que entra abre con lo contado) y los cierres que hace la
    encargada cuando hay una diferencia que justificar (SUPERVISADO).

    El arqueo es ciego: quien cuenta declara lo que hay sin ver lo
    esperado, y recién al cerrar se calcula la diferencia.
    """

    ESTADOS = (('ABIERTA', 'Abierta'), ('CERRADA', 'Cerrada'))

    id_turno_caja = models.AutoField('ID', primary_key=True)
    id_caja_registradora = models.ForeignKey(
        CajaRegistradora, on_delete=models.RESTRICT,
        db_column='id_caja_registradora', related_name='turnos',
        verbose_name='Caja registradora')
    id_usuario_apertura = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_apertura', related_name='turnos_abiertos',
        verbose_name='Abrió')
    id_usuario_cierre = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_cierre', null=True, blank=True,
        related_name='turnos_cerrados', verbose_name='Cerró')
    id_usuario_supervisor = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_supervisor', null=True, blank=True,
        related_name='turnos_supervisados', verbose_name='Supervisó')
    monto_inicial = campo_importe('Fondo inicial', validators=[NO_NEGATIVO])
    monto_esperado = campo_importe('Esperado según el libro', null=True,
                                   blank=True)
    monto_final = campo_importe('Contado al cierre', null=True, blank=True,
                                validators=[NO_NEGATIVO])
    diferencia_caja = campo_importe('Diferencia', null=True, blank=True)
    observacion_diferencia = models.CharField(
        'Observación', max_length=120, null=True, blank=True)
    tipo_cierre = models.CharField(
        'Tipo de cierre', max_length=12, choices=TipoCierre.choices,
        null=True, blank=True)
    fecha_apertura = models.DateTimeField('Apertura', default=timezone.now)
    fecha_cierre = models.DateTimeField('Cierre', null=True, blank=True)
    estado_turno = models.CharField(
        'Estado', max_length=10, choices=ESTADOS, default='ABIERTA')
    # Relevo (DD-97): el turno nuevo sabe de cuál vino. Así el comprobante
    # del que cierra dice quién recibió el cajón y con qué turno siguió.
    id_turno_anterior = models.ForeignKey(
        'self', on_delete=models.RESTRICT, db_column='id_turno_anterior',
        null=True, blank=True, related_name='turnos_siguientes',
        verbose_name='Relevó al turno')
    # Quien llegó y el que abrió ya no estaba: lo cerró el que entra.
    relevo_en_ausencia = models.BooleanField(
        'Cerrado en relevo en ausencia de quien abrió', default=False)
    # Cerró con una diferencia que nadie con permiso aprobó (no había nadie y
    # la fila no podía esperar): queda a revisar por la titular o la
    # encargada, que lo marca revisado con su nombre.
    requiere_revision = models.BooleanField(
        'Diferencia pendiente de revisión', default=False)
    id_usuario_revision = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_revision', null=True, blank=True,
        related_name='turnos_revisados', verbose_name='Revisó la diferencia')
    fecha_revision = models.DateTimeField('Revisión', null=True, blank=True)

    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'turnos_caja'
        verbose_name = 'Turno de caja'
        verbose_name_plural = 'Turnos de caja'
        ordering = ['-fecha_apertura']
        constraints = [
            CheckEnBase(
                check=CheckSQL("estado_turno IN ('ABIERTA','CERRADA')"),
                name='ck_turnos_caja_estado'),
            CheckEnBase(
                check=CheckSQL(
                    'tipo_cierre IS NULL OR tipo_cierre IN %s'
                    % _en(TipoCierre.values)),
                name='ck_turnos_caja_tipo_cierre'),
            CheckEnBase(
                check=CheckSQL(
                    'monto_inicial >= 0 AND (monto_final IS NULL OR '
                    'monto_final >= 0)'),
                name='ck_turnos_caja_montos'),
            CheckEnBase(
                check=CheckSQL(
                    "(estado_turno = 'ABIERTA' AND fecha_cierre IS NULL AND "
                    'id_usuario_cierre IS NULL AND monto_final IS NULL AND '
                    'monto_esperado IS NULL AND diferencia_caja IS NULL AND '
                    'tipo_cierre IS NULL AND id_usuario_supervisor IS NULL) OR '
                    "(estado_turno = 'CERRADA' AND fecha_cierre IS NOT NULL AND "
                    'id_usuario_cierre IS NOT NULL AND monto_final IS NOT NULL '
                    'AND monto_esperado IS NOT NULL AND tipo_cierre IS NOT NULL '
                    'AND diferencia_caja = monto_final - monto_esperado)'),
                name='ck_turnos_caja_cierre'),
            CheckEnBase(
                check=CheckSQL(
                    'diferencia_caja IS NULL OR diferencia_caja = 0 OR '
                    "trim(coalesce(observacion_diferencia, '')) <> ''"),
                name='ck_turnos_caja_justificacion'),
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_cierre = 'SUPERVISADO') = "
                    '(id_usuario_supervisor IS NOT NULL) AND '
                    '(id_usuario_supervisor IS NULL OR '
                    'id_usuario_supervisor <> id_usuario_cierre)'),
                name='ck_turnos_caja_supervision'),
            CheckEnBase(
                check=CheckSQL(
                    'fecha_cierre IS NULL OR fecha_cierre >= fecha_apertura'),
                name='ck_turnos_caja_fechas'),
            # Una caja registradora no puede tener dos turnos abiertos (DD-42).
            models.UniqueConstraint(
                fields=['id_caja_registradora'],
                condition=models.Q(estado_turno='ABIERTA'),
                name='uq_turno_abierto_por_caja'),
            # Un turno se releva una sola vez: lo sigue un único turno.
            models.UniqueConstraint(
                fields=['id_turno_anterior'], name='uq_turnos_caja_anterior'),
            # Relevo en ausencia: lo cierra otra persona que la que abrió.
            CheckEnBase(
                check=CheckSQL(
                    'NOT relevo_en_ausencia OR (estado_turno = \'CERRADA\' AND '
                    'id_usuario_cierre <> id_usuario_apertura)'),
                name='ck_turnos_caja_ausencia'),
            # Una diferencia a revisar es de un turno cerrado que no cuadró y
            # que nadie supervisó; la revisa otra persona, con fecha.
            CheckEnBase(
                check=CheckSQL(
                    "(NOT requiere_revision OR (estado_turno = 'CERRADA' AND "
                    'diferencia_caja <> 0 AND id_usuario_supervisor IS NULL)) AND '
                    '((id_usuario_revision IS NULL) = (fecha_revision IS NULL)) AND '
                    '(id_usuario_revision IS NULL OR (requiere_revision AND '
                    'id_usuario_revision <> id_usuario_cierre AND '
                    'fecha_revision >= fecha_cierre))'),
                name='ck_turnos_caja_revision'),
            CheckEnBase(
                check=CheckSQL(
                    'id_turno_anterior IS NULL OR id_turno_anterior <> id_turno_caja'),
                name='ck_turnos_caja_anterior'),
        ]

    def __str__(self):
        return (f'{self.id_caja_registradora} · turno {self.pk} '
                f'({self.get_estado_turno_display().lower()})')

    @property
    def abierto(self):
        return self.estado_turno == 'ABIERTA'

    @property
    def pendiente_de_revision(self):
        return self.requiere_revision and self.id_usuario_revision_id is None

    @property
    def siguiente(self):
        """El turno que siguió por relevo, o None."""
        if self.pk is None:
            return None
        return self.turnos_siguientes.select_related('id_usuario_apertura').first()

    @property
    def quedo_de_otro_dia(self):
        """Sigue abierto y se abrió un día anterior a hoy."""
        return (self.abierto and
                timezone.localtime(self.fecha_apertura).date() < timezone.localdate())


class TipoMovimientoCaja(models.TextChoices):
    APERTURA = 'APERTURA', 'Fondo inicial'
    VENTA = 'VENTA', 'Venta'
    COBRO_CAMBIO = 'COBRO_CAMBIO', 'Diferencia cobrada en un cambio'
    REFUERZO = 'REFUERZO', 'Refuerzo de cambio'
    RETIRO = 'RETIRO', 'Retiro de efectivo'
    GASTO = 'GASTO', 'Gasto pagado con la caja'
    DEVOLUCION = 'DEVOLUCION', 'Devolución de dinero'
    ANULACION = 'ANULACION', 'Anulación de venta'
    COBRO_CUENTA = 'COBRO_CUENTA', 'Cobro de cuenta corriente'


ENTRADAS_DE_CAJA = ('VENTA', 'COBRO_CAMBIO', 'REFUERZO', 'COBRO_CUENTA')
SALIDAS_DE_CAJA = ('RETIRO', 'GASTO', 'DEVOLUCION', 'ANULACION')


class MovimientoCaja(LibroInmutable, models.Model):
    """Asiento del libro de caja. Append-only (DD-02).

    Cada asiento dice de dónde viene: una venta, un cambio, un egreso
    (el gasto pagado con plata del cajón) o una decisión de caja —el fondo
    inicial, el refuerzo de cambio, el retiro para depositar—, que llevan
    su concepto escrito.
    """

    id_movimiento_caja = models.AutoField('ID', primary_key=True)
    id_turno_caja = models.ForeignKey(
        TurnoCaja, on_delete=models.RESTRICT, db_column='id_turno_caja',
        related_name='movimientos', verbose_name='Turno de caja')
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='movimientos_caja', verbose_name='Usuario')
    id_venta = models.ForeignKey(
        'ventas.Venta', on_delete=models.RESTRICT, db_column='id_venta',
        null=True, blank=True, related_name='movimientos_caja')
    id_cambio = models.ForeignKey(
        'ventas.Cambio', on_delete=models.RESTRICT, db_column='id_cambio',
        null=True, blank=True, related_name='movimientos_caja')
    id_egreso = models.ForeignKey(
        'compras.Egreso', on_delete=models.RESTRICT, db_column='id_egreso',
        null=True, blank=True, related_name='movimientos_caja')
    tipo_movimiento = models.CharField(
        'Tipo', max_length=12, choices=TipoMovimientoCaja.choices)
    monto = campo_importe('Monto')
    concepto = models.CharField('Concepto', max_length=100)
    fecha_hora = models.DateTimeField('Fecha y hora', default=timezone.now)

    objects = ManagerDeLibro()

    class Meta:
        db_table = 'movimientos_caja'
        verbose_name = 'Movimiento de caja'
        verbose_name_plural = 'Movimientos de caja'
        ordering = ['-fecha_hora', '-id_movimiento_caja']
        constraints = [
            CheckEnBase(
                check=CheckSQL('tipo_movimiento IN %s'
                               % _en(TipoMovimientoCaja.values)),
                name='ck_movcaja_tipo'),
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_movimiento = 'APERTURA' AND monto >= 0) OR "
                    f'(tipo_movimiento IN {_en(ENTRADAS_DE_CAJA)} AND monto > 0)'
                    f' OR (tipo_movimiento IN {_en(SALIDAS_DE_CAJA)} AND '
                    'monto < 0)'),
                name='ck_movcaja_signo'),
            CheckEnBase(
                check=CheckSQL(
                    "(id_venta IS NOT NULL) = (tipo_movimiento IN "
                    "('VENTA','ANULACION')) AND "
                    '(id_cambio IS NOT NULL) = (tipo_movimiento IN '
                    "('COBRO_CAMBIO','DEVOLUCION')) AND "
                    "(id_egreso IS NOT NULL) = (tipo_movimiento = 'GASTO')"),
                name='ck_movcaja_documento'),
            CheckEnBase(
                check=CheckSQL("trim(concepto) <> ''"),
                name='ck_movimientos_caja_textos'),
            models.UniqueConstraint(
                fields=['id_turno_caja'],
                condition=models.Q(tipo_movimiento='APERTURA'),
                name='uq_movcaja_una_apertura'),
            # Una venta entra al cajón una vez y, si se anula, sale una vez.
            models.UniqueConstraint(
                fields=['id_venta', 'tipo_movimiento'],
                condition=models.Q(id_venta__isnull=False),
                name='uq_movcaja_por_venta'),
            models.UniqueConstraint(
                fields=['id_cambio'],
                condition=models.Q(id_cambio__isnull=False),
                name='uq_movcaja_por_cambio'),
            models.UniqueConstraint(
                fields=['id_egreso'],
                condition=models.Q(id_egreso__isnull=False),
                name='uq_movcaja_por_egreso'),
        ]

    def __str__(self):
        return f'{self.get_tipo_movimiento_display()} {self.monto:+}'


# ===========================================================================
#  VENTAS
# ===========================================================================
class EstadoVenta(models.TextChoices):
    COMPLETADA = 'COMPLETADA', 'Completada'
    ANULADA = 'ANULADA', 'Anulada'


class MotivoAnulacion(models.TextChoices):
    PAGO_RECHAZADO = 'PAGO_RECHAZADO', 'El pago fue rechazado'
    ERROR_DE_CARGA = 'ERROR_DE_CARGA', 'Se cargó mal (producto, cantidad o precio)'
    COBRO_DUPLICADO = 'COBRO_DUPLICADO', 'Se cobró dos veces'
    CLIENTE_DESISTIO = 'CLIENTE_DESISTIO', 'El cliente desistió antes de irse'
    OTRO = 'OTRO', 'Otro (explicar)'


ANULACION = ('fecha_anulacion', 'id_usuario_anulacion', 'id_usuario_solicitante',
             'motivo_anulacion', 'detalle_anulacion')


class Venta(SinBajaFisica, models.Model):
    """El ticket. Una vez emitido no cambia (salvo la referencia fiscal).

    Los importes cuadran por construcción:

        subtotal         = suma de los renglones (ya con su descuento)
        total            = subtotal - descuento + redondeo + recargo
        suma de pagos    = total

    ``ajuste_redondeo`` lleva el total al peso entero: en el mostrador no
    se dan centavos. ``token_operacion`` hace que un doble clic en
    "Cobrar" (o el reintento de una tablet que perdió el wifi) no genere
    dos ventas: la segunda choca contra el índice único.
    """

    # Lo único que se completa después de emitida: el número del ticket
    # fiscal, cuando el controlador fiscal lo imprime, y la anulación (el
    # estado y quién, cuándo y por qué), que escribe ``ventas.anulacion``.
    CAMPOS_POSTERIORES = frozenset({'comprobante_fiscal_ref', 'estado_venta',
                                    *ANULACION})

    id_venta = models.AutoField('ID', primary_key=True)
    id_turno_caja = models.ForeignKey(
        TurnoCaja, on_delete=models.RESTRICT, db_column='id_turno_caja',
        related_name='ventas', verbose_name='Turno de caja')
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='ventas', verbose_name='Vendió')
    id_cliente = models.ForeignKey(
        'fidelizacion.Cliente', on_delete=models.RESTRICT,
        db_column='id_cliente', null=True, blank=True, related_name='ventas',
        verbose_name='Cliente')
    id_beneficio = models.ForeignKey(
        'fidelizacion.Beneficio', on_delete=models.RESTRICT,
        db_column='id_beneficio', null=True, blank=True,
        related_name='ventas', verbose_name='Beneficio')
    token_operacion = models.UUIDField(
        'Token de la operación', default=uuid.uuid4, editable=False)
    fecha_venta = models.DateField('Fecha', default=timezone.localdate)
    hora_venta = models.TimeField('Hora', default=hora_actual)
    subtotal = campo_importe('Subtotal', default=Decimal('0'))
    monto_descuento = campo_importe('Descuento', default=Decimal('0'))
    ajuste_redondeo = campo_importe('Redondeo', default=Decimal('0'))
    monto_recargo = campo_importe('Recargo', default=Decimal('0'))
    total_venta = campo_importe('Total', default=Decimal('0'))
    comprobante_fiscal_ref = models.CharField(
        'Comprobante fiscal', max_length=30, null=True, blank=True)
    # DD-26: la venta nace COMPLETADA; si se anula, queda ANULADA con
    # cuándo, por qué, quién la pidió y quién la autorizó. Nunca vuelve
    # atrás. Quien autoriza no puede ser quien vendió (DD-61).
    estado_venta = models.CharField(
        'Estado', max_length=15, choices=EstadoVenta.choices,
        default=EstadoVenta.COMPLETADA)
    fecha_anulacion = models.DateTimeField('Anulada el', null=True, blank=True)
    id_usuario_anulacion = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_anulacion', null=True, blank=True,
        related_name='ventas_anuladas', verbose_name='Anuló')
    # Quien pidió la anulación: el cajero desde Cambios (y entonces la
    # autoriza otra persona con su clave) o el mismo que anula.
    id_usuario_solicitante = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_solicitante', null=True, blank=True,
        related_name='anulaciones_pedidas', verbose_name='Pidió la anulación')
    motivo_anulacion = models.CharField(
        'Motivo de la anulación', max_length=20,
        choices=MotivoAnulacion.choices, null=True, blank=True)
    detalle_anulacion = models.CharField(
        'Detalle de la anulación', max_length=120, null=True, blank=True)

    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'ventas'
        verbose_name = 'Venta'
        verbose_name_plural = 'Ventas'
        ordering = ['-fecha_venta', '-hora_venta']
        indexes = [models.Index(fields=['fecha_venta'],
                                name='ix_ventas_fecha')]
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'subtotal >= 0 AND monto_descuento >= 0 AND '
                    'monto_recargo >= 0 AND total_venta >= 0 AND '
                    'monto_descuento <= subtotal AND abs(ajuste_redondeo) < 1'),
                name='ck_ventas_importes'),
            CheckEnBase(
                check=CheckSQL(
                    'total_venta = subtotal - monto_descuento + '
                    'ajuste_redondeo + monto_recargo'),
                name='ck_ventas_total'),
            CheckEnBase(
                check=CheckSQL(
                    'id_beneficio IS NULL OR id_cliente IS NOT NULL'),
                name='ck_ventas_beneficio_cliente'),
            CheckEnBase(
                check=CheckSQL(
                    'comprobante_fiscal_ref IS NULL OR '
                    "trim(comprobante_fiscal_ref) <> ''"),
                name='ck_ventas_textos'),
            models.UniqueConstraint(fields=['token_operacion'],
                                    name='uq_ventas_token_operacion'),
            CheckEnBase(
                check=CheckSQL(
                    "estado_venta IN ('COMPLETADA','ANULADA')"),
                name='ck_ventas_estado'),
            # Los datos de la anulación están si y sólo si está anulada.
            CheckEnBase(
                check=CheckSQL(
                    "(estado_venta = 'ANULADA') = (fecha_anulacion IS NOT NULL "
                    'AND id_usuario_anulacion IS NOT NULL AND '
                    'id_usuario_solicitante IS NOT NULL AND '
                    'motivo_anulacion IS NOT NULL) AND '
                    "(estado_venta = 'ANULADA' OR detalle_anulacion IS NULL)"),
                name='ck_ventas_anulacion'),
            CheckEnBase(
                check=CheckSQL(
                    "(motivo_anulacion IS NULL OR motivo_anulacion IN "
                    "('PAGO_RECHAZADO','ERROR_DE_CARGA','COBRO_DUPLICADO',"
                    "'CLIENTE_DESISTIO','OTRO')) AND "
                    "(detalle_anulacion IS NULL OR trim(detalle_anulacion) <> '') AND "
                    "(motivo_anulacion IS DISTINCT FROM 'OTRO' OR "
                    'detalle_anulacion IS NOT NULL)'),
                name='ck_ventas_motivo_anulacion'),
        ]

    def __str__(self):
        return f'Venta N° {self.pk} · {self.fecha_venta:%d/%m/%Y}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            campos = set(kwargs.get('update_fields') or ())
            if not campos or not campos <= self.CAMPOS_POSTERIORES:
                raise ValidationError(
                    'Una venta emitida no se modifica: si hubo un error, se '
                    'anula (con el turno abierto) o se registra un cambio o '
                    'una devolución.')
            if campos & ({'estado_venta'} | set(ANULACION)):
                previo = type(self).objects.filter(pk=self.pk).values_list(
                    'estado_venta', flat=True).first()
                if previo == EstadoVenta.ANULADA:
                    raise ValidationError(
                        f'La venta N° {self.pk} ya está anulada: una anulación '
                        'no se modifica ni se deshace.')
                # DD-61: separación de funciones. Quien cobró no autoriza
                # la anulación de su propia venta (si no, podría cobrar,
                # anular y quedarse con la plata sin que nadie intervenga).
                # Salvo el titular (DD-69): nadie se defrauda a sí mismo.
                if (self.estado_venta == EstadoVenta.ANULADA
                        and self.id_usuario_anulacion_id == self.id_usuario_id
                        and not self.id_usuario_anulacion.es_titular):
                    raise ValidationError(
                        'La anulación la autoriza otra persona, no quien '
                        'hizo la venta.')
        return super().save(*args, **kwargs)

    @property
    def anulada(self):
        return self.estado_venta == EstadoVenta.ANULADA

    @property
    def base_descuento_cabecera(self):
        """Sobre qué se calculó el descuento de cabecera: los renglones sin
        descuento propio (ver ``ventas.servicios._descuento_de_cabecera``)."""
        return self.detalles.filter(
            motivo_descuento__isnull=True, id_beneficio__isnull=True,
        ).aggregate(t=models.Sum('subtotal'))['t'] or Decimal('0')

    @property
    def monto_vuelto(self):
        return sum((p.monto_vuelto or Decimal('0') for p in self.pagos.all()),
                   Decimal('0'))


class MotivoDescuento(models.TextChoices):
    VENCIMIENTO = 'VENCIMIENTO', 'Próximo a vencer'
    PROMOCION = 'PROMOCION', 'Promoción del día'
    PRODUCTO_DANADO = 'PRODUCTO_DANADO', 'Envase o producto dañado'
    CORTESIA = 'CORTESIA', 'Cortesía del local'
    BENEFICIO = 'BENEFICIO', 'Beneficio del club'
    OTRO = 'OTRO', 'Otro (explicar)'


class DetalleVenta(LibroInmutable, models.Model):
    """Renglón de una venta. Append-only.

    Congela lo que se cobró y lo que costaba: el nombre, el precio
    vigente al confirmar y el costo (RN-3.1.3). Si mañana el producto
    cambia, el ticket de ayer y su margen siguen diciendo lo que fue.

    El descuento es del RENGLÓN y lleva motivo: la medialuna del día
    anterior al 30 %, el yogur que vence mañana con la rebaja automática.
    Si supera el tope del perfil de quien vende, queda escrito quién lo
    autorizó.
    """

    id_detalle_venta = models.AutoField('ID', primary_key=True)
    id_venta = models.ForeignKey(
        Venta, on_delete=models.CASCADE, db_column='id_venta',
        related_name='detalles', verbose_name='Venta')
    id_producto = models.ForeignKey(
        'stock.Producto', on_delete=models.RESTRICT, db_column='id_producto',
        related_name='renglones_venta', verbose_name='Producto')
    id_beneficio = models.ForeignKey(
        'fidelizacion.Beneficio', on_delete=models.RESTRICT,
        db_column='id_beneficio', null=True, blank=True,
        related_name='renglones_venta', verbose_name='Beneficio')
    id_usuario_autorizante = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_autorizante', null=True, blank=True,
        related_name='descuentos_autorizados',
        verbose_name='Autorizó el descuento')
    descripcion_item = models.CharField('Descripción', max_length=80)
    cantidad = campo_cantidad('Cantidad')
    precio_unitario = campo_importe('Precio unitario',
                                    validators=[NO_NEGATIVO])
    costo_unitario = campo_importe('Costo unitario', default=Decimal('0'),
                                   validators=[NO_NEGATIVO])
    monto_descuento_linea = campo_importe('Descuento', default=Decimal('0'),
                                          validators=[NO_NEGATIVO])
    motivo_descuento = models.CharField(
        'Motivo del descuento', max_length=16,
        choices=MotivoDescuento.choices, null=True, blank=True)
    detalle_motivo = models.CharField('Detalle del motivo', max_length=80,
                                      null=True, blank=True)
    subtotal = campo_importe('Subtotal', validators=[NO_NEGATIVO])

    objects = ManagerDeLibro()

    class Meta:
        db_table = 'detalles_venta'
        verbose_name = 'Renglón de venta'
        verbose_name_plural = 'Renglones de venta'
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'cantidad > 0 AND precio_unitario >= 0 AND '
                    'costo_unitario >= 0 AND monto_descuento_linea >= 0 AND '
                    'monto_descuento_linea <= round(cantidad * '
                    'precio_unitario, 2)'),
                name='ck_detventa_valores'),
            CheckEnBase(
                check=CheckSQL(
                    'subtotal = round(cantidad * precio_unitario, 2) - '
                    'monto_descuento_linea'),
                name='ck_detventa_subtotal'),
            CheckEnBase(
                check=CheckSQL(
                    '(monto_descuento_linea > 0) = (motivo_descuento IS NOT '
                    'NULL) AND (motivo_descuento IS NULL OR motivo_descuento '
                    'IN %s)' % _en(MotivoDescuento.values)),
                name='ck_detventa_motivo'),
            CheckEnBase(
                check=CheckSQL(
                    "motivo_descuento IS DISTINCT FROM 'OTRO' OR "
                    "trim(coalesce(detalle_motivo, '')) <> ''"),
                name='ck_detventa_detalle_motivo'),
            CheckEnBase(
                check=CheckSQL(
                    "(motivo_descuento IS NOT DISTINCT FROM 'BENEFICIO') = "
                    '(id_beneficio IS NOT NULL)'),
                name='ck_detventa_beneficio'),
            CheckEnBase(
                check=CheckSQL("trim(descripcion_item) <> ''"),
                name='ck_detalles_venta_textos'),
        ]

    def __str__(self):
        return f'{self.cantidad} × {self.descripcion_item}'

    @property
    def importe_bruto(self):
        return (self.cantidad * self.precio_unitario).quantize(Decimal('0.01'))

    @property
    def porcentaje_descuento(self):
        bruto = self.importe_bruto
        if not bruto:
            return Decimal('0')
        return (self.monto_descuento_linea * 100 / bruto).quantize(
            Decimal('0.01'))

    @property
    def precio_efectivo(self):
        """Lo que se cobró por unidad, descuento incluido."""
        return (self.subtotal / self.cantidad).quantize(Decimal('0.01'))

    @property
    def precio_reintegrable(self):
        """Lo que se le devuelve por unidad si la trae de vuelta.

        Es lo que pagó por ella: el precio del renglón con su descuento y,
        si el renglón entró en el descuento de cabecera (el beneficio del
        club, DD-18), menos la parte de ese descuento que le tocó. Sin
        esto, quien compró con el 10 % del club y devuelve recibiría el
        precio lleno.
        """
        precio = self.subtotal / self.cantidad
        venta = self.id_venta
        if (venta.monto_descuento and self.motivo_descuento is None
                and self.id_beneficio_id is None):
            base = venta.base_descuento_cabecera
            if base:
                precio = precio * (1 - venta.monto_descuento / base)
        return precio.quantize(Decimal('0.01'))


class PagoVenta(LibroInmutable, models.Model):
    """Una parte del cobro: $5.000 en efectivo y el resto con QR.

    El recargo es del PAGO y no de la venta: si la mitad se paga con
    tarjeta de crédito, el recargo se calcula sobre esa mitad. Se congela
    el porcentaje vigente, así un cambio de tarifa no reescribe tickets
    viejos. El vuelto lo calcula el motor (columna generada).
    """

    id_pago_venta = models.AutoField('ID', primary_key=True)
    id_venta = models.ForeignKey(
        Venta, on_delete=models.CASCADE, db_column='id_venta',
        related_name='pagos', verbose_name='Venta')
    id_medio_pago = models.ForeignKey(
        MedioPago, on_delete=models.RESTRICT, db_column='id_medio_pago',
        related_name='pagos', verbose_name='Medio de pago')
    monto = campo_importe('Importe que cubre')
    recargo_porcentaje = models.DecimalField(
        'Recargo (%)', max_digits=5, decimal_places=2, default=Decimal('0'))
    monto_recargo = campo_importe('Recargo', default=Decimal('0'))
    monto_recibido = campo_importe('Recibido', null=True, blank=True)
    monto_vuelto = models.GeneratedField(
        expression=models.F('monto_recibido') - models.F('monto')
        - models.F('monto_recargo'),
        output_field=models.DecimalField(max_digits=12, decimal_places=2),
        db_persist=True, verbose_name='Vuelto')

    objects = ManagerDeLibro()

    class Meta:
        db_table = 'pagos_venta'
        verbose_name = 'Pago de una venta'
        verbose_name_plural = 'Pagos de ventas'
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'monto > 0 AND recargo_porcentaje >= 0 AND '
                    'recargo_porcentaje <= 100'),
                name='ck_pagos_valores'),
            CheckEnBase(
                check=CheckSQL(
                    'monto_recargo = round(monto * recargo_porcentaje / 100, 0)'),
                name='ck_pagos_recargo'),
            CheckEnBase(
                check=CheckSQL(
                    'monto_recibido IS NULL OR monto_recibido >= monto + '
                    'monto_recargo'),
                name='ck_pagos_recibido'),
            models.UniqueConstraint(fields=['id_venta', 'id_medio_pago'],
                                    name='uq_pagos_medio_por_venta'),
        ]

    def __str__(self):
        return f'{self.id_medio_pago}: {pesos(self.importe_cobrado)}'

    @property
    def importe_cobrado(self):
        return self.monto + self.monto_recargo


# ===========================================================================
#  CAMBIOS Y DEVOLUCIONES
# ===========================================================================
class MotivoCambio(models.TextChoices):
    PRODUCTO_DANADO = 'PRODUCTO_DANADO', 'Producto dañado o en mal estado'
    VENCIDO = 'VENCIDO', 'Producto vencido'
    ERROR_DE_VENTA = 'ERROR_DE_VENTA', 'Se cobró o se entregó otra cosa'
    CLIENTE_ARREPENTIDO = 'CLIENTE_ARREPENTIDO', 'El cliente se arrepintió'
    OTRO = 'OTRO', 'Otro (explicar)'


class Cambio(LibroInmutable, models.Model):
    """Lo que pasó DESPUÉS de una venta: un cambio o una devolución.

    Siempre referencia la venta original —no hay devoluciones "sueltas"—
    y queda en el turno donde se hizo, que puede no ser el de la venta.

    El saldo se cobra o se devuelve:
        importe_cobrado  = lo que el cliente pone de más
        importe_devuelto = lo que se le reintegra
    Uno de los dos es cero. Devolver dinero pide autorización de alguien
    con permiso, que queda escrita.
    """

    id_cambio = models.AutoField('ID', primary_key=True)
    id_venta = models.ForeignKey(
        Venta, on_delete=models.RESTRICT, db_column='id_venta',
        related_name='cambios', verbose_name='Venta original')
    id_turno_caja = models.ForeignKey(
        TurnoCaja, on_delete=models.RESTRICT, db_column='id_turno_caja',
        related_name='cambios', verbose_name='Turno de caja')
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='cambios', verbose_name='Registró')
    id_usuario_autorizante = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_autorizante', null=True, blank=True,
        related_name='cambios_autorizados', verbose_name='Autorizó')
    id_medio_pago = models.ForeignKey(
        MedioPago, on_delete=models.RESTRICT, db_column='id_medio_pago',
        null=True, blank=True, related_name='cambios',
        verbose_name='Medio del saldo')
    token_operacion = models.UUIDField(
        'Token de la operación', default=uuid.uuid4, editable=False)
    fecha_cambio = models.DateTimeField('Fecha', default=timezone.now)
    motivo = models.CharField('Motivo', max_length=20,
                              choices=MotivoCambio.choices)
    detalle_motivo = models.CharField('Detalle', max_length=120, null=True,
                                      blank=True)
    importe_devuelto = campo_importe('Devuelto al cliente',
                                     default=Decimal('0'))
    importe_cobrado = campo_importe('Cobrado al cliente',
                                    default=Decimal('0'))

    objects = ManagerDeLibro()

    class Meta:
        db_table = 'cambios'
        verbose_name = 'Cambio o devolución'
        verbose_name_plural = 'Cambios y devoluciones'
        ordering = ['-fecha_cambio']
        constraints = [
            CheckEnBase(
                check=CheckSQL('motivo IN %s' % _en(MotivoCambio.values)),
                name='ck_cambios_motivo'),
            CheckEnBase(
                check=CheckSQL(
                    'importe_devuelto >= 0 AND importe_cobrado >= 0 AND NOT '
                    '(importe_devuelto > 0 AND importe_cobrado > 0)'),
                name='ck_cambios_importes'),
            CheckEnBase(
                check=CheckSQL(
                    '(importe_devuelto + importe_cobrado > 0) = '
                    '(id_medio_pago IS NOT NULL)'),
                name='ck_cambios_medio'),
            CheckEnBase(
                check=CheckSQL(
                    'importe_devuelto = 0 OR id_usuario_autorizante IS NOT '
                    'NULL'),
                name='ck_cambios_autorizacion'),
            CheckEnBase(
                check=CheckSQL(
                    "motivo <> 'OTRO' OR trim(coalesce(detalle_motivo, '')) "
                    "<> ''"),
                name='ck_cambios_detalle'),
            models.UniqueConstraint(fields=['token_operacion'],
                                    name='uq_cambios_token_operacion'),
        ]

    def __str__(self):
        return f'Cambio N° {self.pk} de la venta N° {self.id_venta_id}'


class DetalleCambio(LibroInmutable, models.Model):
    """Lo que vuelve (DEVUELVE) y lo que se lleva (ENTREGA) el cliente.

    Lo que vuelve referencia el renglón de la venta original —no se puede
    devolver más de lo que se compró— y se valoriza a lo que efectivamente
    pagó. Si está apto vuelve al stock; si no (la lata abollada, el yogur
    vencido), no reingresa y queda como pérdida.
    """

    SENTIDOS = (('DEVUELVE', 'Devuelve'), ('ENTREGA', 'Se lleva'))

    id_detalle_cambio = models.AutoField('ID', primary_key=True)
    id_cambio = models.ForeignKey(
        Cambio, on_delete=models.CASCADE, db_column='id_cambio',
        related_name='detalles', verbose_name='Cambio')
    sentido = models.CharField('Sentido', max_length=8, choices=SENTIDOS)
    id_producto = models.ForeignKey(
        'stock.Producto', on_delete=models.RESTRICT, db_column='id_producto',
        related_name='renglones_cambio', verbose_name='Producto')
    id_detalle_venta = models.ForeignKey(
        DetalleVenta, on_delete=models.RESTRICT,
        db_column='id_detalle_venta', null=True, blank=True,
        related_name='devoluciones', verbose_name='Renglón original')
    cantidad = campo_cantidad('Cantidad')
    precio_unitario = campo_importe('Precio unitario')
    subtotal = campo_importe('Subtotal')
    reingresa_stock = models.BooleanField('Vuelve al stock', null=True,
                                          blank=True)

    objects = ManagerDeLibro()

    class Meta:
        db_table = 'detalles_cambio'
        verbose_name = 'Renglón de cambio'
        verbose_name_plural = 'Renglones de cambio'
        constraints = [
            CheckEnBase(
                check=CheckSQL("sentido IN ('DEVUELVE','ENTREGA')"),
                name='ck_detcambio_sentido'),
            CheckEnBase(
                check=CheckSQL(
                    'cantidad > 0 AND precio_unitario >= 0 AND subtotal = '
                    'round(cantidad * precio_unitario, 2)'),
                name='ck_detcambio_valores'),
            CheckEnBase(
                check=CheckSQL(
                    "(sentido = 'DEVUELVE') = (id_detalle_venta IS NOT NULL) "
                    "AND (sentido = 'DEVUELVE') = (reingresa_stock IS NOT "
                    'NULL)'),
                name='ck_detcambio_forma'),
        ]

    def __str__(self):
        return f'{self.get_sentido_display()} {self.cantidad} × {self.id_producto}'


# ===========================================================================
#  AUTORIZACIÓN A DISTANCIA (DD-94)
# ===========================================================================
class EstadoSolicitud(models.TextChoices):
    PENDIENTE = 'PENDIENTE', 'Esperando respuesta'
    APROBADA = 'APROBADA', 'Aprobada'
    RECHAZADA = 'RECHAZADA', 'Rechazada'
    USADA = 'USADA', 'Usada en la venta'
    ANULADA = 'ANULADA', 'Anulada'


class ViaAprobacion(models.TextChoices):
    ENLACE = 'ENLACE', 'Enlace del correo o del WhatsApp'
    PIN = 'PIN', 'PIN dictado a la caja'
    BANDEJA = 'BANDEJA', 'Desde el sistema'


class SolicitudAutorizacion(models.Model):
    """La caja pide permiso y la dueña contesta desde el celular.

    Cuando una venta necesita la firma de alguien que no está al lado de la
    caja (fiarle a alguien por primera vez, pasar su tope, un descuento por
    encima del de la cajera), la caja deja acá el pedido con el ticket tal
    como está. Se avisa a la titular por correo y por n8n; ella aprueba con
    el enlace, desde la bandeja del sistema, o le dicta a la caja un PIN de
    4 dígitos. Vale 10 minutos y para ESE ticket: la ``huella_operacion``
    resume cliente, productos, descuentos y pagos, y si el ticket cambia la
    aprobación ya no sirve.

    Del enlace y del PIN se guarda sólo el hash: quien lea la base no puede
    aprobar nada.
    """

    VIGENCIA_MINUTOS = 10
    INTENTOS_DE_PIN = 5

    id_solicitud = models.AutoField('ID', primary_key=True)
    id_usuario_solicitante = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario_solicitante',
        related_name='+', verbose_name='Pidió')
    id_usuario_autorizante = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario_autorizante',
        null=True, blank=True, related_name='+', verbose_name='Contestó')
    id_turno_caja = models.ForeignKey(
        TurnoCaja, on_delete=models.RESTRICT, db_column='id_turno_caja',
        related_name='solicitudes', verbose_name='Turno de caja')
    id_cliente = models.ForeignKey(
        'fidelizacion.Cliente', on_delete=models.RESTRICT, db_column='id_cliente',
        null=True, blank=True, related_name='+', verbose_name='Cliente')
    id_venta = models.ForeignKey(
        Venta, on_delete=models.RESTRICT, db_column='id_venta', null=True, blank=True,
        related_name='+', verbose_name='Venta')
    motivo_solicitud = models.CharField('Qué se pide', max_length=200)
    monto_solicitud = campo_importe('Total del ticket')
    pedido = models.JSONField('Ticket')
    huella_operacion = models.CharField('Huella del ticket', max_length=64)
    token_hash = models.CharField('Hash del enlace', max_length=64)
    pin_hash = models.CharField('Hash del PIN', max_length=64)
    intentos_pin = models.PositiveSmallIntegerField('Intentos de PIN', default=0)
    estado_solicitud = models.CharField(
        'Estado', max_length=10, choices=EstadoSolicitud.choices, default=EstadoSolicitud.PENDIENTE)
    via_aprobacion = models.CharField(
        'Cómo se aprobó', max_length=8, choices=ViaAprobacion.choices, null=True, blank=True)
    fecha_solicitud = models.DateTimeField('Pedida', default=timezone.now)
    fecha_vencimiento = models.DateTimeField('Vence')
    fecha_resolucion = models.DateTimeField('Contestada', null=True, blank=True)

    class Meta:
        db_table = 'solicitudes_autorizacion'
        verbose_name = 'Pedido de autorización'
        verbose_name_plural = 'Pedidos de autorización'
        ordering = ['-id_solicitud']
        indexes = [models.Index(fields=['estado_solicitud', 'fecha_vencimiento'], name='ix_solautoriz_pendientes')]
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "estado_solicitud IN ('PENDIENTE','APROBADA','RECHAZADA','USADA','ANULADA') "
                    'AND monto_solicitud >= 0 AND intentos_pin BETWEEN 0 AND 5 '
                    'AND fecha_vencimiento > fecha_solicitud '
                    "AND trim(motivo_solicitud) <> '' AND length(huella_operacion) = 64"),
                name='ck_solautoriz_valores'),
            # Lo que contestó: nadie y nada mientras espera; quién y cómo si
            # se aprobó; sólo cuándo si se rechazó o se anuló.
            CheckEnBase(
                check=CheckSQL(
                    "(estado_solicitud = 'PENDIENTE' AND id_usuario_autorizante IS NULL "
                    'AND via_aprobacion IS NULL AND fecha_resolucion IS NULL) '
                    "OR (estado_solicitud IN ('APROBADA','USADA') AND id_usuario_autorizante IS NOT NULL "
                    "AND via_aprobacion IN ('ENLACE','PIN','BANDEJA') AND fecha_resolucion IS NOT NULL) "
                    "OR (estado_solicitud IN ('RECHAZADA','ANULADA') AND via_aprobacion IS NULL "
                    'AND fecha_resolucion IS NOT NULL) '
                    # Aprobada y no usada (se cerró de otra forma): se anula
                    # pero conserva quién y cómo la aprobó (DD-101).
                    "OR (estado_solicitud = 'ANULADA' AND id_usuario_autorizante IS NOT NULL "
                    "AND via_aprobacion IN ('ENLACE','PIN','BANDEJA') AND fecha_resolucion IS NOT NULL)"),
                name='ck_solautoriz_resolucion'),
            # La venta sólo la tiene una aprobación usada. Una usada puede
            # no tener venta: la de un ajuste de stock desde el mostrador.
            CheckEnBase(
                check=CheckSQL("id_venta IS NULL OR estado_solicitud = 'USADA'"),
                name='ck_solautoriz_venta'),
            models.UniqueConstraint(fields=['token_hash'], name='uq_solicitudes_autorizacion_token_hash'),
        ]

    def __str__(self):
        return f'Pedido de autorización N° {self.pk}'

    @property
    def vencida(self):
        return timezone.now() >= self.fecha_vencimiento
