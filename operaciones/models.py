"""Tablas operativas del sistema Cafeto.

Acá viven las 18 tablas que no son ni seguridad (``usuarios``) ni
catálogo (``maestros``, ``proveedores``): sesiones y auditoría, caja,
ventas, compras, stock, inventario, egresos y fidelización de clientes.

Por qué están modeladas si todavía no tienen pantalla
-----------------------------------------------------
Dos motivos:

  * Django es el dueño del esquema: las 33 tablas las crean sus
    migraciones, no un guion aparte, así que todas tienen que estar
    declaradas como modelo.
  * Los CRUD de datos maestros consultan estas tablas para decidir si una
    baja se puede hacer: una caja registradora con turno abierto, un producto
    con stock, una categoría con clientes. Sin los modelos no habría forma
    de preguntarlo por el ORM.

Los libros de movimientos (``movimientos_caja``, ``movimientos_stock``,
``movimientos_puntos``, ``log_auditoria``, ``intentos_acceso``,
``detalles_venta``) son APPEND-ONLY: heredan de ``LibroInmutable``
(``cafeto/reglas.py``), que rechaza cualquier UPDATE o DELETE tanto por
la instancia como por el QuerySet. Un asiento no se corrige: se
contra-asienta.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Coalesce
from django.utils import timezone

from cafeto.reglas import (
    ConSaldosDerivados,
    ConSelloDeModificacion,
    LibroInmutable,
    ManagerDeLibro,
    ManagerSinBajaFisica,
    SinBajaFisica,
)
from cafeto.restricciones import CheckEnBase, CheckSQL
from cafeto.validaciones import (
    NO_NEGATIVO,
    VALIDADOR_DNI,
    VALIDADOR_TELEFONO,
    exigir_que_no_este_dado_de_baja,
    validar_edad,
)
from usuarios.models import sello_fecha_modificacion, sello_usuario_modificacion


def hora_actual():
    """Hora local, para el default de VENTAS.hora_venta.

    ``timezone.localtime()`` devuelve un datetime completo; el campo es un
    TIME, así que hay que quedarse sólo con la hora.
    """
    return timezone.localtime().time()


# ===========================================================================
#  SESIONES, AUDITORÍA E INTENTOS DE ACCESO
# ===========================================================================
class Sesion(models.Model):
    """Sesión abierta por un usuario. Única por usuario a la vez (DD-01).

    Es la que firma la auditoría: ``log_auditoria.id_sesion`` apunta acá,
    así cada operación registrada queda atada a quién la hizo, desde qué
    IP y en qué turno.
    """

    MOTIVOS = (
        ('EXPIRACION', 'Expiró'),
        ('REVOCACION', 'Revocada'),
        ('CIERRE_MANUAL', 'Cierre manual'),
    )

    id_sesion = models.AutoField('ID', primary_key=True)
    id_usuario = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='sesiones', verbose_name='Usuario')
    id_turno_caja = models.ForeignKey(
        'TurnoCaja', on_delete=models.SET_NULL, db_column='id_turno_caja',
        null=True, blank=True, related_name='sesiones', verbose_name='Turno de caja')
    token_hash = models.CharField('Token (hash)', max_length=255)
    fecha_inicio = models.DateTimeField('Inicio', default=timezone.now)
    fecha_ultimo_acceso = models.DateTimeField(
        'Último acceso', default=timezone.now)
    fecha_expiracion = models.DateTimeField('Expira')
    fecha_cierre = models.DateTimeField('Cierre', null=True, blank=True)
    motivo_cierre = models.CharField(
        'Motivo del cierre', max_length=20, choices=MOTIVOS,
        null=True, blank=True)
    ip_origen = models.CharField('IP de origen', max_length=45)
    activa = models.BooleanField('Activa', default=True)

    class Meta:
        db_table = 'sesiones'
        verbose_name = 'Sesión'
        verbose_name_plural = 'Sesiones'
        ordering = ['-fecha_inicio']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "motivo_cierre IS NULL OR motivo_cierre IN "
                    "('EXPIRACION','REVOCACION','CIERRE_MANUAL')"),
                name='ck_sesiones_motivo'),
            CheckEnBase(
                check=CheckSQL(
                    '(activa AND fecha_cierre IS NULL AND motivo_cierre IS NULL)'
                    ' OR (NOT activa AND fecha_cierre IS NOT NULL AND '
                    'motivo_cierre IS NOT NULL)'),
                name='ck_sesiones_estado'),
            CheckEnBase(
                check=CheckSQL('fecha_expiracion > fecha_inicio'),
                name='ck_sesiones_expiracion'),
            CheckEnBase(
                check=CheckSQL(
                    'fecha_cierre IS NULL OR fecha_cierre >= fecha_inicio'),
                name='ck_sesiones_fechas'),
            CheckEnBase(
                check=CheckSQL('fecha_ultimo_acceso >= fecha_inicio'),
                name='ck_sesiones_ultimo_acceso'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(token_hash) <> '' AND trim(ip_origen) <> ''"),
                name='ck_sesiones_textos'),
            models.UniqueConstraint(
                fields=['token_hash'], name='uq_sesiones_token_hash'),
            # Una sola sesión activa por usuario (DD-01).
            models.UniqueConstraint(
                fields=['id_usuario'], condition=models.Q(activa=True),
                name='uq_sesion_activa_por_usuario'),
        ]

    def __str__(self):
        return f'Sesión {self.pk} de {self.id_usuario}'

    def cerrar(self, motivo='CIERRE_MANUAL'):
        self.activa = False
        self.fecha_cierre = timezone.now()
        self.motivo_cierre = motivo
        self.save(update_fields=['activa', 'fecha_cierre', 'motivo_cierre'])


class LogAuditoria(LibroInmutable, models.Model):
    """Qué hizo cada quién. Libro append-only (DD-04).

    ``registro_anterior`` y ``registro_nuevo`` guardan el estado de la fila
    antes y después, en JSONB: con eso se puede reconstruir cualquier
    cambio sin versionar las tablas.
    """

    id_auditoria = models.AutoField('ID', primary_key=True)
    operacion_realizada = models.CharField('Operación', max_length=50)
    modulo_afectado = models.CharField('Módulo', max_length=40)
    detalle_operacion = models.TextField('Detalle', null=True, blank=True)
    fecha_hora = models.DateTimeField('Fecha y hora', default=timezone.now)
    id_sesion = models.ForeignKey(
        Sesion, on_delete=models.RESTRICT, db_column='id_sesion',
        related_name='auditoria', verbose_name='Sesión')
    registro_anterior = models.JSONField('Antes', null=True, blank=True)
    registro_nuevo = models.JSONField('Después', null=True, blank=True)

    # Un asiento no se corrige, se contra-asienta: es el rastro de quién hizo qué. Si una fila se pudiera editar, no probaría nada.
    # El manager corta también el camino del conjunto, porque
    # QuerySet.update()/delete() no pasan por la instancia.
    objects = ManagerDeLibro()

    class Meta:
        db_table = 'log_auditoria'
        verbose_name = 'Registro de auditoría'
        verbose_name_plural = 'Log de auditoría'
        ordering = ['-fecha_hora']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "trim(operacion_realizada) <> '' AND trim(modulo_afectado) <> ''"),
                name='ck_log_auditoria_textos'),
        ]

    def __str__(self):
        return f'{self.fecha_hora:%d/%m/%Y %H:%M} · {self.operacion_realizada}'


class IntentoAcceso(LibroInmutable, models.Model):
    """Intento de login rechazado. Libro append-only."""

    MOTIVOS = (
        ('CLAVE_INCORRECTA', 'Clave incorrecta'),
        ('USUARIO_INEXISTENTE', 'Usuario inexistente'),
        ('USUARIO_INACTIVO', 'Usuario inactivo'),
    )

    id_intento = models.AutoField('ID', primary_key=True)
    id_usuario = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        null=True, blank=True, related_name='intentos', verbose_name='Usuario')
    nombre_usuario_ingresado = models.CharField(
        'Usuario tipeado', max_length=35, blank=True)
    ip_origen = models.CharField('IP de origen', max_length=45)
    fecha_hora = models.DateTimeField('Fecha y hora', default=timezone.now)
    motivo_rechazo = models.CharField(
        'Motivo', max_length=25, choices=MOTIVOS)

    # Un asiento no se corrige, se contra-asienta: es los intentos de acceso rechazados, que son la base del bloqueo por reintentos.
    # El manager corta también el camino del conjunto, porque
    # QuerySet.update()/delete() no pasan por la instancia.
    objects = ManagerDeLibro()

    class Meta:
        db_table = 'intentos_acceso'
        verbose_name = 'Intento de acceso'
        verbose_name_plural = 'Intentos de acceso'
        ordering = ['-fecha_hora']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "motivo_rechazo IN ('CLAVE_INCORRECTA','USUARIO_INEXISTENTE',"
                    "'USUARIO_INACTIVO')"),
                name='ck_intentos_motivo'),
            CheckEnBase(
                check=CheckSQL(
                    "(motivo_rechazo = 'USUARIO_INEXISTENTE') = (id_usuario IS NULL)"),
                name='ck_intentos_usuario'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(ip_origen) <> '' AND trim(motivo_rechazo) <> ''"),
                name='ck_intentos_acceso_textos'),
        ]

    def __str__(self):
        return f'{self.nombre_usuario_ingresado} · {self.motivo_rechazo}'


# ===========================================================================
#  CLIENTES Y PUNTOS
# ===========================================================================
class Cliente(ConSaldosDerivados, ConSelloDeModificacion,
              SinBajaFisica, models.Model):
    """Cliente del programa de fidelización (Alternativa II)."""

    # Los mueve el libro de puntos, nunca una pantalla (DD-04).
    SALDOS_DERIVADOS = {'puntos_acumulados': 0, 'puntos_disponibles': 0}

    id_cliente = models.AutoField('ID', primary_key=True)
    id_categoria = models.ForeignKey(
        'maestros.CategoriaFidelizacion', on_delete=models.RESTRICT,
        db_column='id_categoria', related_name='clientes',
        verbose_name='Categoría')
    apellido_cliente = models.CharField('Apellido', max_length=50)
    nombre_cliente = models.CharField('Nombre', max_length=50)
    fecha_nacimiento = models.DateField(
        'Fecha de nacimiento', null=True, blank=True,
        help_text='Opcional. Se usa para el saludo de cumpleaños del '
                  'programa de fidelización.')
    dni_cliente = models.CharField('DNI', max_length=8, null=True, blank=True,
        validators=[VALIDADOR_DNI])
    telefono_cliente = models.CharField(
        'Teléfono', max_length=15, null=True, blank=True,
        validators=[VALIDADOR_TELEFONO])
    email_cliente = models.EmailField(
        'Email', max_length=254, null=True, blank=True)
    qr_cliente = models.CharField('Código QR', max_length=64)
    puntos_acumulados = models.IntegerField(
        'Puntos acumulados', default=0, editable=False)
    puntos_disponibles = models.IntegerField(
        'Puntos disponibles', default=0, editable=False)
    fecha_alta = models.DateField('Fecha de alta', default=timezone.localdate)
    domicilio = models.CharField(
        'Domicilio', max_length=120, null=True, blank=True)
    id_localidad = models.ForeignKey(
        'proveedores.Localidad', on_delete=models.RESTRICT,
        db_column='id_localidad', null=True, blank=True,
        related_name='clientes', verbose_name='Localidad')
    borrado_logico = models.BooleanField('Borrado lógico', default=False)
    fecha_modificacion = sello_fecha_modificacion()
    usuario_modificacion = sello_usuario_modificacion('clientes_modificados')

    class Meta:
        db_table = 'clientes'
        verbose_name = 'Cliente'
        verbose_name_plural = 'Clientes'
        ordering = ['apellido_cliente', 'nombre_cliente']
        constraints = [
            CheckEnBase(
                check=CheckSQL('puntos_acumulados >= 0'),
                name='ck_clientes_puntos'),
            CheckEnBase(
                check=CheckSQL(
                    "dni_cliente IS NULL OR dni_cliente ~ '^[0-9]{7,8}$'"),
                name='ck_clientes_dni'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(apellido_cliente) <> '' AND trim(nombre_cliente) <> ''"
                    " AND trim(qr_cliente) <> ''"),
                name='ck_clientes_textos'),
            # Cota fija. La regla viva (entre 14 y 100 años, nunca
            # futura) se mide contra HOY y por eso vive en clean(): un
            # CHECK se evalúa una sola vez, al grabar.
            CheckEnBase(
                check=CheckSQL(
                    "fecha_nacimiento IS NULL OR "
                    "fecha_nacimiento >= DATE '1900-01-01'"),
                name='ck_clientes_nacimiento'),
            models.UniqueConstraint(
                fields=['qr_cliente'], name='uq_clientes_qr_cliente'),
            models.UniqueConstraint(
                fields=['telefono_cliente'],
                name='uq_clientes_telefono_cliente'),
            models.UniqueConstraint(
                fields=['dni_cliente'], name='uq_clientes_dni_cliente'),
        ]

    def clean(self):
        """Capa 2: lo que la base no puede controlar sola.

        Las tres reglas de acá tienen algo en común: ninguna se puede
        escribir como CHECK. La edad se mide contra la fecha de hoy, y el
        duplicado dado de baja necesita mirar OTRA fila de la tabla.
        """
        super().clean()

        validar_edad(self.fecha_nacimiento, timezone.localdate())

        # El índice único no distingue vigentes de dados de baja. Sin esto,
        # recargar un cliente que se había dado de baja devolvía un error
        # 500 de PostgreSQL en vez de decir qué hacer.
        if self.dni_cliente:
            exigir_que_no_este_dado_de_baja(
                type(self), self, {'dni_cliente': self.dni_cliente},
                etiqueta='El cliente con DNI %s' % self.dni_cliente)
        if self.telefono_cliente:
            exigir_que_no_este_dado_de_baja(
                type(self), self, {'telefono_cliente': self.telefono_cliente},
                etiqueta='El cliente con teléfono %s' % self.telefono_cliente)

    @property
    def edad(self):
        """Años cumplidos, o None si no se cargó la fecha."""
        from cafeto.validaciones import anios_cumplidos

        if self.fecha_nacimiento is None:
            return None
        return anios_cumplidos(self.fecha_nacimiento, timezone.localdate())

    def __str__(self):
        return '%s, %s' % (self.apellido_cliente, self.nombre_cliente)


class MovimientoPuntos(LibroInmutable, models.Model):
    """Asiento del libro de puntos. Append-only."""

    TIPOS = (
        ('ACUMULACION', 'Acumulación'),
        ('CANJE', 'Canje'),
        ('ANULACION', 'Anulación'),
        ('AJUSTE', 'Ajuste'),
        ('VENCIMIENTO', 'Vencimiento'),
    )

    id_movimiento_punto = models.AutoField('ID', primary_key=True)
    id_cliente = models.ForeignKey(
        Cliente, on_delete=models.RESTRICT, db_column='id_cliente',
        related_name='movimientos_puntos', verbose_name='Cliente')
    id_venta = models.ForeignKey(
        'Venta', on_delete=models.RESTRICT, db_column='id_venta',
        null=True, blank=True, related_name='movimientos_puntos',
        verbose_name='Venta')
    id_beneficio = models.ForeignKey(
        'maestros.Beneficio', on_delete=models.RESTRICT,
        db_column='id_beneficio', null=True, blank=True,
        related_name='movimientos_puntos', verbose_name='Beneficio')
    tipo_movimiento = models.CharField('Tipo', max_length=15, choices=TIPOS)
    puntos = models.IntegerField('Puntos')
    fecha_movimiento = models.DateTimeField('Fecha', default=timezone.now)

    # Un asiento no se corrige, se contra-asienta: es el libro de puntos: el saldo del cliente es su suma.
    # El manager corta también el camino del conjunto, porque
    # QuerySet.update()/delete() no pasan por la instancia.
    objects = ManagerDeLibro()

    def save(self, *args, **kwargs):
        """Asienta los puntos, mueve los saldos y recategoriza.

        Reemplaza a ``fn_actualiza_puntos``. Tres reglas:

        * los **disponibles** suben y bajan con cada movimiento;
        * los **acumulados** —que deciden la categoría— sólo suben con la
          ACUMULACION, y bajan únicamente cuando se anula la venta que los
          originó. Un canje gasta puntos pero no borra el mérito de
          haberlos ganado (DD-33);
        * la recategorización es automática y **ascendente**: la categoría
          nunca baja (DD-11). Quien llegó a Oro no vuelve a Plata por
          gastar sus puntos.
        """
        from django.db import transaction

        from maestros.models import CategoriaFidelizacion

        with transaction.atomic():
            super().save(*args, **kwargs)

            cliente = (Cliente.objects.select_for_update()
                       .get(pk=self.id_cliente_id))

            suma_acumulados = self.puntos if (
                self.tipo_movimiento == 'ACUMULACION'
                or (self.tipo_movimiento == 'ANULACION'
                    and self.id_beneficio_id is None)) else 0

            cliente.actualizar_saldo_derivado(
                puntos_disponibles=cliente.puntos_disponibles + self.puntos,
                puntos_acumulados=cliente.puntos_acumulados + suma_acumulados)

            nueva = (CategoriaFidelizacion.objects
                     .filter(puntos_minimos__lte=cliente.puntos_acumulados,
                             borrado_logico=False)
                     .order_by('-puntos_minimos').first())
            actual = cliente.id_categoria
            if (nueva is not None and nueva.pk != actual.pk
                    and nueva.puntos_minimos > actual.puntos_minimos):
                cliente.id_categoria = nueva
                cliente.save(update_fields=['id_categoria'])

    class Meta:
        db_table = 'movimientos_puntos'
        verbose_name = 'Movimiento de puntos'
        verbose_name_plural = 'Movimientos de puntos'
        ordering = ['-fecha_movimiento']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "tipo_movimiento IN ('ACUMULACION','CANJE','ANULACION',"
                    "'AJUSTE','VENCIMIENTO')"),
                name='ck_movpuntos_tipo'),
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_movimiento = 'ACUMULACION' AND id_venta IS NOT NULL"
                    " AND id_beneficio IS NULL AND puntos > 0) OR "
                    "(tipo_movimiento = 'CANJE' AND id_venta IS NOT NULL AND "
                    "id_beneficio IS NOT NULL AND puntos < 0) OR "
                    "(tipo_movimiento = 'ANULACION' AND id_venta IS NOT NULL "
                    "AND puntos <> 0) OR (tipo_movimiento = 'AJUSTE' AND "
                    "id_venta IS NULL AND id_beneficio IS NULL AND puntos <> 0)"
                    " OR (tipo_movimiento = 'VENCIMIENTO' AND id_venta IS NULL"
                    " AND id_beneficio IS NULL AND puntos < 0)"),
                name='ck_movpuntos_matriz'),
            CheckEnBase(
                check=CheckSQL("trim(tipo_movimiento) <> ''"),
                name='ck_movimientos_puntos_textos'),
            models.UniqueConstraint(
                fields=['id_venta'],
                condition=models.Q(tipo_movimiento='ACUMULACION'),
                name='uq_acumulacion_por_venta'),
            models.UniqueConstraint(
                fields=['id_venta', 'id_beneficio'],
                condition=models.Q(tipo_movimiento='CANJE'),
                name='uq_canje_por_venta'),
            models.UniqueConstraint(
                'id_venta',
                Coalesce('id_beneficio', models.Value(0)),
                condition=models.Q(tipo_movimiento='ANULACION'),
                name='uq_anulacion_puntos'),
        ]

    def __str__(self):
        return f'{self.tipo_movimiento} {self.puntos:+} ({self.id_cliente})'


# ===========================================================================
#  CAJA
# ===========================================================================
class TurnoCaja(SinBajaFisica, models.Model):
    """Turno de caja: de la apertura al arqueo de cierre."""

    ESTADOS = (('ABIERTA', 'Abierta'), ('CERRADA', 'Cerrada'))

    id_turno_caja = models.AutoField('ID', primary_key=True)
    id_caja_registradora = models.ForeignKey(
        'maestros.CajaRegistradora', on_delete=models.RESTRICT,
        db_column='id_caja_registradora', related_name='turnos',
        verbose_name='Caja registradora')
    id_usuario_apertura = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_apertura', related_name='turnos_abiertos',
        verbose_name='Abrió')
    id_usuario_cierre = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_cierre', null=True, blank=True,
        related_name='turnos_cerrados', verbose_name='Cerró')
    monto_inicial = models.DecimalField(
        'Monto inicial', max_digits=12, decimal_places=2,
        validators=[NO_NEGATIVO])
    monto_final = models.DecimalField(
        'Monto final', max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[NO_NEGATIVO])
    diferencia_caja = models.DecimalField(
        'Diferencia', max_digits=12, decimal_places=2, null=True, blank=True)
    observacion_diferencia = models.CharField(
        'Observación', max_length=120, null=True, blank=True)
    fecha_apertura = models.DateTimeField('Apertura', default=timezone.now)
    fecha_cierre = models.DateTimeField('Cierre', null=True, blank=True)
    estado_turno = models.CharField(
        'Estado', max_length=10, choices=ESTADOS, default='ABIERTA')

    # Acá la baja es lógica: el DELETE no corre (RN-8.1.1).
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
                    'monto_inicial >= 0 AND (monto_final IS NULL OR '
                    'monto_final >= 0)'),
                name='ck_turnos_caja_montos'),
            CheckEnBase(
                check=CheckSQL(
                    "(estado_turno = 'ABIERTA' AND fecha_cierre IS NULL AND "
                    "id_usuario_cierre IS NULL AND diferencia_caja IS NULL) OR "
                    "(estado_turno = 'CERRADA' AND fecha_cierre IS NOT NULL AND "
                    "id_usuario_cierre IS NOT NULL AND monto_final IS NOT NULL "
                    "AND diferencia_caja IS NOT NULL)"),
                name='ck_turnos_caja_cierre'),
            CheckEnBase(
                check=CheckSQL(
                    'diferencia_caja IS NULL OR diferencia_caja = 0 OR '
                    'observacion_diferencia IS NOT NULL'),
                name='ck_turnos_caja_justificacion'),
            CheckEnBase(
                check=CheckSQL(
                    'fecha_cierre IS NULL OR fecha_cierre >= fecha_apertura'),
                name='ck_turnos_caja_fechas'),
            CheckEnBase(
                check=CheckSQL("trim(estado_turno) <> ''"),
                name='ck_turnos_caja_textos'),
            # Un usuario no puede tener dos turnos de caja abiertos (RN-2.1.1).
            models.UniqueConstraint(
                fields=['id_usuario_apertura'],
                condition=models.Q(estado_turno='ABIERTA'),
                name='uq_turno_abierto_por_usuario'),
            # Una caja registradora no puede tener dos turnos abiertos (DD-42).
            models.UniqueConstraint(
                fields=['id_caja_registradora'],
                condition=models.Q(estado_turno='ABIERTA'),
                name='uq_turno_abierto_por_caja'),
        ]

    def __str__(self):
        return f'Turno {self.pk} · {self.id_caja_registradora} ({self.estado_turno})'


class MovimientoCaja(LibroInmutable, models.Model):
    """Asiento del libro de caja. Append-only (DD-02)."""

    TIPOS = (
        ('APERTURA', 'Apertura'), ('VENTA', 'Venta'), ('EGRESO', 'Egreso'),
        ('AJUSTE', 'Ajuste'), ('ANULACION', 'Anulación'),
    )

    id_movimiento_caja = models.AutoField('ID', primary_key=True)
    id_turno_caja = models.ForeignKey(
        TurnoCaja, on_delete=models.RESTRICT, db_column='id_turno_caja',
        related_name='movimientos', verbose_name='Turno de caja')
    id_usuario = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='movimientos_caja', verbose_name='Usuario')
    id_venta = models.ForeignKey(
        'Venta', on_delete=models.RESTRICT, db_column='id_venta',
        null=True, blank=True, related_name='movimientos_caja')
    id_egreso = models.ForeignKey(
        'Egreso', on_delete=models.RESTRICT, db_column='id_egreso',
        null=True, blank=True, related_name='movimientos_caja')
    tipo_movimiento = models.CharField('Tipo', max_length=10, choices=TIPOS)
    monto = models.DecimalField('Monto', max_digits=12, decimal_places=2,
        validators=[NO_NEGATIVO])
    concepto = models.CharField('Concepto', max_length=100)
    fecha_hora = models.DateTimeField('Fecha y hora', default=timezone.now)

    # Un asiento no se corrige, se contra-asienta: es el libro de caja: el arqueo se calcula sobre él.
    # El manager corta también el camino del conjunto, porque
    # QuerySet.update()/delete() no pasan por la instancia.
    objects = ManagerDeLibro()

    class Meta:
        db_table = 'movimientos_caja'
        verbose_name = 'Movimiento de caja'
        verbose_name_plural = 'Movimientos de caja'
        ordering = ['-fecha_hora']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "tipo_movimiento IN ('APERTURA','VENTA','EGRESO','AJUSTE',"
                    "'ANULACION')"),
                name='ck_movcaja_tipo'),
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_movimiento = 'APERTURA' AND monto >= 0) OR "
                    "(tipo_movimiento = 'VENTA' AND monto > 0) OR "
                    "(tipo_movimiento IN ('EGRESO','ANULACION') AND monto < 0)"
                    " OR (tipo_movimiento = 'AJUSTE' AND monto <> 0)"),
                name='ck_movcaja_signo'),
            CheckEnBase(
                check=CheckSQL(
                    "(id_venta IS NOT NULL) = (tipo_movimiento IN "
                    "('VENTA','ANULACION')) AND (id_egreso IS NOT NULL) = "
                    "(tipo_movimiento = 'EGRESO')"),
                name='ck_movcaja_documento'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(tipo_movimiento) <> '' AND trim(concepto) <> ''"),
                name='ck_movimientos_caja_textos'),
            models.UniqueConstraint(
                fields=['id_venta', 'tipo_movimiento'],
                condition=models.Q(id_venta__isnull=False),
                name='uq_movcaja_por_venta'),
            models.UniqueConstraint(
                fields=['id_egreso'],
                condition=models.Q(id_egreso__isnull=False),
                name='uq_movcaja_por_egreso'),
        ]

    def __str__(self):
        return f'{self.tipo_movimiento} {self.monto:+}'


# ===========================================================================
#  VENTAS
# ===========================================================================
class Venta(SinBajaFisica, models.Model):
    """Comprobante de venta.

    ``monto_vuelto`` NO es un campo de este modelo: en la base es una
    columna GENERATED ALWAYS (``monto_recibido - total_venta``, D-08) que
    la agrega una migración por RunSQL, porque Django 4.2 todavía no tiene
    ``GeneratedField``. Nunca se escribe desde Django; se lee con la
    propiedad ``monto_vuelto`` de más abajo.
    """

    ESTADOS = (('COMPLETADA', 'Completada'), ('ANULADA', 'Anulada'))

    id_venta = models.AutoField('ID', primary_key=True)
    id_turno_caja = models.ForeignKey(
        TurnoCaja, on_delete=models.RESTRICT, db_column='id_turno_caja',
        related_name='ventas', verbose_name='Turno de caja')
    id_usuario = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='ventas', verbose_name='Cajero')
    id_cliente = models.ForeignKey(
        Cliente, on_delete=models.RESTRICT, db_column='id_cliente',
        null=True, blank=True, related_name='ventas', verbose_name='Cliente')
    id_medio_pago = models.ForeignKey(
        'maestros.MedioPago', on_delete=models.RESTRICT,
        db_column='id_medio_pago', related_name='ventas',
        verbose_name='Medio de pago')
    id_beneficio = models.ForeignKey(
        'maestros.Beneficio', on_delete=models.RESTRICT,
        db_column='id_beneficio', null=True, blank=True,
        related_name='ventas', verbose_name='Beneficio')
    id_usuario_anulacion = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_anulacion', null=True, blank=True,
        related_name='ventas_anuladas', verbose_name='Anuló')
    fecha_venta = models.DateField('Fecha', default=timezone.localdate)
    hora_venta = models.TimeField('Hora', default=hora_actual)
    monto_descuento = models.DecimalField(
        'Descuento', max_digits=12, decimal_places=2, default=Decimal('0'),
        validators=[NO_NEGATIVO])
    total_venta = models.DecimalField(
        'Total', max_digits=12, decimal_places=2, default=Decimal('0'),
        validators=[NO_NEGATIVO])
    monto_recibido = models.DecimalField(
        'Recibido', max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[NO_NEGATIVO])
    estado_venta = models.CharField(
        'Estado', max_length=15, choices=ESTADOS, default='COMPLETADA')
    fecha_anulacion = models.DateTimeField(
        'Fecha de anulación', null=True, blank=True)
    motivo_anulacion = models.CharField(
        'Motivo de anulación', max_length=120, null=True, blank=True)

    # Acá la baja es lógica: el DELETE no corre (RN-8.1.1).
    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'ventas'
        verbose_name = 'Venta'
        verbose_name_plural = 'Ventas'
        ordering = ['-fecha_venta', '-hora_venta']
        constraints = [
            CheckEnBase(
                check=CheckSQL("estado_venta IN ('COMPLETADA','ANULADA')"),
                name='ck_ventas_estado'),
            CheckEnBase(
                check=CheckSQL('total_venta >= 0 AND monto_descuento >= 0'),
                name='ck_ventas_importes'),
            CheckEnBase(
                check=CheckSQL(
                    'monto_recibido IS NULL OR monto_recibido >= total_venta'),
                name='ck_ventas_recibido'),
            CheckEnBase(
                check=CheckSQL(
                    'fecha_anulacion IS NULL OR fecha_anulacion >= '
                    'fecha_venta::timestamptz + hora_venta'),
                name='ck_ventas_fechas'),
            CheckEnBase(
                check=CheckSQL(
                    "(estado_venta = 'COMPLETADA' AND fecha_anulacion IS NULL "
                    "AND id_usuario_anulacion IS NULL AND motivo_anulacion IS "
                    "NULL) OR (estado_venta = 'ANULADA' AND fecha_anulacion IS "
                    "NOT NULL AND id_usuario_anulacion IS NOT NULL AND "
                    "motivo_anulacion IS NOT NULL)"),
                name='ck_ventas_anulacion'),
            CheckEnBase(
                check=CheckSQL(
                    'id_beneficio IS NULL OR id_cliente IS NOT NULL'),
                name='ck_ventas_beneficio_cliente'),
            CheckEnBase(
                check=CheckSQL("trim(estado_venta) <> ''"),
                name='ck_ventas_textos'),
        ]

    def __str__(self):
        return f'Venta {self.pk} · {self.fecha_venta:%d/%m/%Y}'

    @property
    def monto_vuelto(self):
        """Columna GENERATED de la base: sólo lectura (D-08)."""
        if self.pk is None:
            return None
        fila = type(self).objects.filter(pk=self.pk).values_list(
            'id_venta', flat=True)
        if not fila:
            return None
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                'SELECT monto_vuelto FROM ventas WHERE id_venta = %s', [self.pk])
            resultado = cur.fetchone()
        return resultado[0] if resultado else None


class DetalleVenta(LibroInmutable, models.Model):
    """Renglón de una venta. Append-only.

    ``tipo_linea`` distingue el producto vendido, la cortesía en especie,
    el descuento del beneficio (precio negativo) y el recargo del medio de
    pago (DD-45).
    """

    TIPOS = (
        ('PRODUCTO', 'Producto'), ('CORTESIA', 'Cortesía'),
        ('DESCUENTO', 'Descuento'), ('RECARGO', 'Recargo'),
    )

    id_detalle_venta = models.AutoField('ID', primary_key=True)
    id_venta = models.ForeignKey(
        Venta, on_delete=models.CASCADE, db_column='id_venta',
        related_name='detalles', verbose_name='Venta')
    id_producto = models.ForeignKey(
        'maestros.Producto', on_delete=models.RESTRICT,
        db_column='id_producto', null=True, blank=True,
        related_name='renglones_venta', verbose_name='Producto')
    id_beneficio = models.ForeignKey(
        'maestros.Beneficio', on_delete=models.RESTRICT,
        db_column='id_beneficio', null=True, blank=True,
        related_name='renglones_venta', verbose_name='Beneficio')
    tipo_linea = models.CharField(
        'Tipo de renglón', max_length=12, choices=TIPOS, default='PRODUCTO')
    descripcion_item = models.CharField('Descripción', max_length=50)
    cantidad = models.IntegerField('Cantidad')
    precio_unitario = models.DecimalField(
        'Precio unitario', max_digits=12, decimal_places=2,
        validators=[NO_NEGATIVO])
    subtotal = models.DecimalField(
        'Subtotal', max_digits=12, decimal_places=2,
        validators=[NO_NEGATIVO])

    # Un asiento no se corrige, se contra-asienta: es los renglones de la venta, que respaldan el ticket.
    # El manager corta también el camino del conjunto, porque
    # QuerySet.update()/delete() no pasan por la instancia.
    objects = ManagerDeLibro()

    class Meta:
        db_table = 'detalles_venta'
        verbose_name = 'Renglón de venta'
        verbose_name_plural = 'Renglones de venta'
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "tipo_linea IN ('PRODUCTO','CORTESIA','DESCUENTO','RECARGO')"),
                name='ck_detventa_linea'),
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_linea = 'PRODUCTO'  AND id_producto IS NOT NULL AND "
                    "precio_unitario >= 0 AND id_beneficio IS NULL) OR "
                    "(tipo_linea = 'CORTESIA'  AND id_producto IS NOT NULL AND "
                    "precio_unitario  = 0 AND id_beneficio IS NOT NULL) OR "
                    "(tipo_linea = 'DESCUENTO' AND id_producto IS NULL     AND "
                    "precio_unitario  < 0 AND id_beneficio IS NOT NULL) OR "
                    "(tipo_linea = 'RECARGO'   AND id_producto IS NULL     AND "
                    "precio_unitario  > 0 AND id_beneficio IS NULL"
                    "                           AND cantidad = 1)"),
                name='ck_detventa_coherencia'),
            CheckEnBase(
                check=CheckSQL(
                    'cantidad > 0 AND subtotal = cantidad * precio_unitario'),
                name='ck_detventa_subtotal'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(tipo_linea) <> '' AND trim(descripcion_item) <> ''"),
                name='ck_detalles_venta_textos'),
        ]

    def __str__(self):
        return f'{self.cantidad} × {self.descripcion_item}'


# ===========================================================================
#  COMPRAS
# ===========================================================================
class OrdenCompra(SinBajaFisica, models.Model):
    """Pedido al proveedor. Ciclo PENDIENTE → EMITIDA → RECEPCIONADA (DD-44)."""

    ESTADOS = (
        ('PENDIENTE', 'Pendiente'), ('EMITIDA', 'Emitida'),
        ('RECEPCIONADA', 'Recepcionada'), ('CANCELADA', 'Cancelada'),
    )

    id_orden_compra = models.AutoField('ID', primary_key=True)
    id_proveedor = models.ForeignKey(
        'proveedores.Proveedor', on_delete=models.RESTRICT,
        db_column='id_proveedor', related_name='ordenes',
        verbose_name='Proveedor')
    id_usuario = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='ordenes', verbose_name='Usuario')
    fecha_orden = models.DateField('Fecha', default=timezone.localdate)
    estado_orden = models.CharField(
        'Estado', max_length=15, choices=ESTADOS, default='PENDIENTE')
    total_orden = models.DecimalField(
        'Total', max_digits=12, decimal_places=2, default=Decimal('0'),
        validators=[NO_NEGATIVO])

    # El borrador se corrige; la orden enviada ya comprometió al
    # proveedor y sólo se recepciona o se cancela (DD-44).
    TRANSICIONES = {
        'PENDIENTE': {'EMITIDA', 'CANCELADA'},
        'EMITIDA': {'RECEPCIONADA', 'CANCELADA'},
        'RECEPCIONADA': set(),
        'CANCELADA': set(),
    }

    def save(self, *args, **kwargs):
        """Reemplaza a ``fn_orden_valida``."""
        if not self._state.adding:
            anterior = type(self).objects.filter(pk=self.pk).values(
                'estado_orden', 'id_proveedor', 'total_orden',
                'fecha_orden').first()
            if anterior is not None:
                previo = anterior['estado_orden']
                if previo in ('RECEPCIONADA', 'CANCELADA'):
                    raise ValidationError(
                        'La orden %s ya está %s: es un estado final (DD-44).'
                        % (self.pk, previo))
                if (self.estado_orden != previo
                        and self.estado_orden not in self.TRANSICIONES[previo]):
                    raise ValidationError(
                        'Transición de estado no admitida en la orden: '
                        '%s → %s (DD-44).' % (previo, self.estado_orden))
                # Una orden ya enviada al proveedor no se reescribe.
                if previo != 'PENDIENTE' and (
                        self.id_proveedor_id != anterior['id_proveedor']
                        or self.total_orden != anterior['total_orden']
                        or self.fecha_orden != anterior['fecha_orden']):
                    raise ValidationError(
                        'La orden %s ya fue emitida: su contenido no se '
                        'modifica (DD-44).' % self.pk)
        return super().save(*args, **kwargs)

    # Acá la baja es lógica: el DELETE no corre (RN-8.1.1).
    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'ordenes_compra'
        verbose_name = 'Orden de compra'
        verbose_name_plural = 'Órdenes de compra'
        ordering = ['-fecha_orden']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "estado_orden IN ('PENDIENTE','EMITIDA','RECEPCIONADA',"
                    "'CANCELADA')"),
                name='ck_ordenes_estado'),
            CheckEnBase(
                check=CheckSQL('total_orden >= 0'), name='ck_ordenes_total'),
            CheckEnBase(
                check=CheckSQL("trim(estado_orden) <> ''"),
                name='ck_ordenes_compra_textos'),
        ]

    def __str__(self):
        return f'Orden {self.pk} · {self.id_proveedor}'


class DetalleOrdenCompra(models.Model):
    """Renglón del pedido."""

    id_detalle_orden_compra = models.AutoField('ID', primary_key=True)
    id_orden_compra = models.ForeignKey(
        OrdenCompra, on_delete=models.CASCADE, db_column='id_orden_compra',
        related_name='detalles', verbose_name='Orden')
    id_producto = models.ForeignKey(
        'maestros.Producto', on_delete=models.RESTRICT,
        db_column='id_producto', related_name='renglones_orden',
        verbose_name='Producto')
    cantidad_pedida = models.IntegerField('Cantidad pedida')
    precio_unitario = models.DecimalField(
        'Precio unitario', max_digits=12, decimal_places=2,
        validators=[NO_NEGATIVO])

    def _exigir_orden_editable(self):
        """Los renglones se cargan mientras la orden es borrador (DD-44)."""
        from cafeto import contexto

        if contexto.en_pasaje_historico():
            return
        estado = self.id_orden_compra.estado_orden
        if estado != 'PENDIENTE':
            raise ValidationError(
                'La orden %s está %s: sus renglones ya no se modifican '
                '(DD-44).' % (self.id_orden_compra_id, estado))

    def save(self, *args, **kwargs):
        self._exigir_orden_editable()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._exigir_orden_editable()
        return super().delete(*args, **kwargs)

    class Meta:
        db_table = 'detalles_orden_compra'
        verbose_name = 'Renglón de orden'
        verbose_name_plural = 'Renglones de orden'
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'cantidad_pedida > 0 AND precio_unitario >= 0'),
                name='ck_detoc_valores'),
            models.UniqueConstraint(
                fields=['id_orden_compra', 'id_producto'],
                name='uq_detalles_orden_compra_id_orden_compra_id_producto'),
        ]

    def __str__(self):
        return f'{self.cantidad_pedida} × {self.id_producto}'


class Compra(ConSaldosDerivados, SinBajaFisica, models.Model):
    """Factura de compra recibida del proveedor."""

    # Se deriva de los egresos asociados, no se escribe a mano (DD-22).
    SALDOS_DERIVADOS = {'estado_compra': 'PENDIENTE'}

    # Acá la baja es lógica (RN-8.1.1).
    objects = ManagerSinBajaFisica()

    ESTADOS = (
        ('PENDIENTE', 'Pendiente'), ('EN_PROCESO_PAGO', 'En proceso de pago'),
        ('PAGADA', 'Pagada'),
    )

    id_compra = models.AutoField('ID', primary_key=True)
    id_proveedor = models.ForeignKey(
        'proveedores.Proveedor', on_delete=models.RESTRICT,
        db_column='id_proveedor', related_name='compras',
        verbose_name='Proveedor')
    id_orden_compra = models.ForeignKey(
        OrdenCompra, on_delete=models.RESTRICT, db_column='id_orden_compra',
        null=True, blank=True, related_name='compras', verbose_name='Orden')
    id_usuario = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='compras', verbose_name='Usuario')
    fecha_compra = models.DateField('Fecha', default=timezone.localdate)
    numero_factura = models.CharField('N° de factura', max_length=20)
    total_compra = models.DecimalField(
        'Total', max_digits=12, decimal_places=2,
        validators=[NO_NEGATIVO])
    fecha_vencimiento_pago = models.DateField(
        'Vence el', null=True, blank=True)
    estado_compra = models.CharField(
        'Estado', max_length=20, choices=ESTADOS, default='PENDIENTE')

    class Meta:
        db_table = 'compras'
        verbose_name = 'Compra'
        verbose_name_plural = 'Compras'
        ordering = ['-fecha_compra']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "estado_compra IN ('PENDIENTE','EN_PROCESO_PAGO','PAGADA')"),
                name='ck_compras_estado'),
            CheckEnBase(
                check=CheckSQL(
                    'total_compra >= 0 AND (fecha_vencimiento_pago IS NULL OR '
                    'fecha_vencimiento_pago >= fecha_compra)'),
                name='ck_compras_valores'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(numero_factura) <> '' AND trim(estado_compra) <> ''"),
                name='ck_compras_textos'),
            models.UniqueConstraint(
                fields=['id_proveedor', 'numero_factura'],
                name='uq_compras_id_proveedor_numero_factura'),
        ]

    def __str__(self):
        return f'Factura {self.numero_factura} · {self.id_proveedor}'


class DetalleCompra(models.Model):
    """Renglón de la factura de compra."""

    id_detalle_compra = models.AutoField('ID', primary_key=True)
    id_compra = models.ForeignKey(
        Compra, on_delete=models.CASCADE, db_column='id_compra',
        related_name='detalles', verbose_name='Compra')
    id_producto = models.ForeignKey(
        'maestros.Producto', on_delete=models.RESTRICT,
        db_column='id_producto', related_name='renglones_compra',
        verbose_name='Producto')
    cantidad_recibida = models.IntegerField('Cantidad recibida')
    precio_costo_unitario = models.DecimalField(
        'Costo unitario', max_digits=12, decimal_places=2,
        validators=[NO_NEGATIVO])

    class Meta:
        db_table = 'detalles_compra'
        verbose_name = 'Renglón de compra'
        verbose_name_plural = 'Renglones de compra'
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'cantidad_recibida > 0 AND precio_costo_unitario >= 0'),
                name='ck_detcompra_valores'),
            models.UniqueConstraint(
                fields=['id_compra', 'id_producto'],
                name='uq_detalles_compra_id_compra_id_producto'),
        ]

    def __str__(self):
        return f'{self.cantidad_recibida} × {self.id_producto}'


class Egreso(SinBajaFisica, models.Model):
    """Salida de dinero: pago a proveedor, servicio, impuesto o sueldo.

    Ciclo PENDIENTE_PAGO → PENDIENTE_ACREDITACION → ACREDITADO / RECHAZADO
    (DD-06, DD-32).
    """

    ESTADOS = (
        ('PENDIENTE_PAGO', 'Pendiente de pago'),
        ('PENDIENTE_ACREDITACION', 'Pendiente de acreditación'),
        ('ACREDITADO', 'Acreditado'),
        ('RECHAZADO', 'Rechazado'),
    )

    id_egreso = models.AutoField('ID', primary_key=True)
    id_tipo_egreso = models.ForeignKey(
        'maestros.TipoEgreso', on_delete=models.RESTRICT,
        db_column='id_tipo_egreso', related_name='egresos',
        verbose_name='Tipo de egreso')
    id_proveedor = models.ForeignKey(
        'proveedores.Proveedor', on_delete=models.RESTRICT,
        db_column='id_proveedor', null=True, blank=True,
        related_name='egresos', verbose_name='Proveedor')
    destinatario = models.CharField(
        'Destinatario', max_length=60, null=True, blank=True)
    id_compra = models.ForeignKey(
        Compra, on_delete=models.RESTRICT, db_column='id_compra',
        null=True, blank=True, related_name='egresos', verbose_name='Compra')
    id_medio_pago = models.ForeignKey(
        'maestros.MedioPago', on_delete=models.RESTRICT,
        db_column='id_medio_pago', null=True, blank=True,
        related_name='egresos', verbose_name='Medio de pago')
    monto = models.DecimalField('Monto', max_digits=12, decimal_places=2,
        validators=[NO_NEGATIVO])
    concepto = models.CharField('Concepto', max_length=100)
    fecha_egreso = models.DateField('Fecha del pago', null=True, blank=True)
    fecha_vencimiento = models.DateField('Vence el', null=True, blank=True)
    comprobante = models.CharField(
        'Comprobante', max_length=50, null=True, blank=True)
    estado_pago = models.CharField(
        'Estado', max_length=25, choices=ESTADOS, default='PENDIENTE_PAGO')
    fecha_acreditacion = models.DateField(
        'Acreditado el', null=True, blank=True)
    referencia_bancaria = models.CharField(
        'Referencia bancaria', max_length=40, null=True, blank=True)

    # Las transiciones que el egreso admite (DD-06). Lo que no está acá,
    # no se puede: un reintento de un pago rechazado es un egreso NUEVO,
    # no una vuelta atrás del anterior.
    TRANSICIONES = {
        'PENDIENTE_PAGO': {'PENDIENTE_ACREDITACION', 'ACREDITADO'},
        'PENDIENTE_ACREDITACION': {'ACREDITADO', 'RECHAZADO'},
        'ACREDITADO': set(),
        'RECHAZADO': set(),
    }

    def save(self, *args, **kwargs):
        """Reemplaza a ``fn_egreso_valida`` y a ``fn_estado_compra``."""
        from django.db import transaction

        with transaction.atomic():
            # El egreso que imputa una compra la paga al proveedor que la
            # facturó. Pagarle a otro rompe la trazabilidad del gasto.
            if self.id_compra_id and (
                    self.id_proveedor_id != self.id_compra.id_proveedor_id):
                raise ValidationError(
                    'El egreso imputa la compra %s a un proveedor distinto '
                    'del que la facturó (DD-22).' % self.id_compra_id)

            if self._state.adding:
                if self.estado_pago == 'RECHAZADO':
                    raise ValidationError('Un egreso no nace rechazado.')
            else:
                self._validar_transicion()

            super().save(*args, **kwargs)
            self._recalcular_estado_de_la_compra()

    def _validar_transicion(self):
        anterior = type(self).objects.filter(pk=self.pk).values(
            'estado_pago', 'monto', 'id_tipo_egreso', 'id_compra').first()
        if anterior is None:
            return

        previo = anterior['estado_pago']
        if previo in ('ACREDITADO', 'RECHAZADO'):
            raise ValidationError(
                'El egreso %s está en un estado final (%s): el reintento es '
                'un egreso nuevo (DD-06).' % (self.pk, previo))

        if (self.estado_pago != previo
                and self.estado_pago not in self.TRANSICIONES[previo]):
            raise ValidationError(
                'Transición de estado no admitida: %s → %s (DD-06).'
                % (previo, self.estado_pago))

        # Lo que define QUÉ se paga no se retoca: si cambia, es otro egreso.
        if (self.monto != anterior['monto']
                or self.id_tipo_egreso_id != anterior['id_tipo_egreso']
                or self.id_compra_id != anterior['id_compra']):
            raise ValidationError(
                'El importe, el tipo y la compra de un egreso no se '
                'modifican.')

    def _recalcular_estado_de_la_compra(self):
        """El estado de la compra se DERIVA de sus egresos (DD-22).

        Reemplaza a ``fn_estado_compra``. Por eso ``estado_compra`` es un
        saldo derivado y no se escribe a mano desde ninguna pantalla.
        """
        from django.db.models import Sum

        if not self.id_compra_id:
            return

        compra = Compra.objects.select_for_update().get(pk=self.id_compra_id)
        egresos = Egreso.objects.filter(id_compra=compra)
        acreditado = egresos.filter(estado_pago='ACREDITADO').aggregate(
            t=Sum('monto'))['t'] or 0
        en_proceso = egresos.filter(
            estado_pago='PENDIENTE_ACREDITACION').exists()

        if acreditado >= compra.total_compra:
            estado = 'PAGADA'
        elif en_proceso:
            estado = 'EN_PROCESO_PAGO'
        else:
            estado = 'PENDIENTE'
        compra.actualizar_saldo_derivado(estado_compra=estado)

    # Acá la baja es lógica: el DELETE no corre (RN-8.1.1).
    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'egresos'
        verbose_name = 'Egreso'
        verbose_name_plural = 'Egresos'
        ordering = ['-fecha_vencimiento', '-fecha_egreso']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "estado_pago IN ('PENDIENTE_PAGO','PENDIENTE_ACREDITACION',"
                    "'ACREDITADO','RECHAZADO')"),
                name='ck_egresos_estado'),
            CheckEnBase(
                check=CheckSQL('monto > 0'), name='ck_egresos_monto'),
            CheckEnBase(
                check=CheckSQL(
                    "(estado_pago = 'ACREDITADO' AND fecha_acreditacion IS NOT "
                    "NULL) OR (estado_pago <> 'ACREDITADO' AND "
                    "fecha_acreditacion IS NULL)"),
                name='ck_egresos_acreditacion'),
            CheckEnBase(
                check=CheckSQL(
                    "(estado_pago = 'PENDIENTE_PAGO' AND fecha_egreso IS NULL "
                    "AND id_medio_pago IS NULL AND comprobante IS NULL AND "
                    "fecha_vencimiento IS NOT NULL) OR (estado_pago <> "
                    "'PENDIENTE_PAGO' AND fecha_egreso IS NOT NULL AND "
                    "id_medio_pago IS NOT NULL)"),
                name='ck_egresos_pendiente'),
            CheckEnBase(
                check=CheckSQL(
                    'fecha_acreditacion IS NULL OR fecha_egreso IS NULL OR '
                    'fecha_acreditacion >= fecha_egreso'),
                name='ck_egresos_fechas'),
            CheckEnBase(
                check=CheckSQL(
                    '(id_proveedor IS NOT NULL AND destinatario IS NULL) OR '
                    '(id_proveedor IS NULL AND destinatario IS NOT NULL)'),
                name='ck_egresos_acreedor'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(concepto) <> '' AND trim(estado_pago) <> ''"),
                name='ck_egresos_textos'),
        ]

    def __str__(self):
        return f'{self.concepto} · {self.monto}'


# ===========================================================================
#  STOCK E INVENTARIO
# ===========================================================================
class Lote(ConSaldosDerivados, SinBajaFisica, models.Model):
    """Partida de un producto con control de vencimiento."""

    # Lo mueve el libro de movimientos de stock (DD-20).
    SALDOS_DERIVADOS = {'stock_lote': 0}

    # Acá la baja es lógica (RN-8.1.1).
    objects = ManagerSinBajaFisica()

    id_lote = models.AutoField('ID', primary_key=True)
    id_producto = models.ForeignKey(
        'maestros.Producto', on_delete=models.RESTRICT,
        db_column='id_producto', related_name='lotes', verbose_name='Producto')
    id_detalle_compra = models.ForeignKey(
        DetalleCompra, on_delete=models.RESTRICT,
        db_column='id_detalle_compra', null=True, blank=True,
        related_name='lotes', verbose_name='Renglón de compra')
    numero_lote = models.CharField(
        'N° de lote', max_length=40, null=True, blank=True)
    fecha_vencimiento = models.DateField('Vence el', null=True, blank=True)
    cantidad_inicial = models.IntegerField('Cantidad inicial')
    stock_lote = models.IntegerField('Stock del lote', default=0, editable=False)
    fecha_alta = models.DateTimeField('Alta', default=timezone.now)

    class Meta:
        db_table = 'lotes'
        verbose_name = 'Lote'
        verbose_name_plural = 'Lotes'
        ordering = ['fecha_vencimiento']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'cantidad_inicial >= 0 AND stock_lote >= 0'),
                name='ck_lotes_cantidades'),
            CheckEnBase(
                check=CheckSQL(
                    'fecha_vencimiento IS NULL OR fecha_vencimiento > '
                    'fecha_alta::DATE'),
                name='ck_lotes_fechas'),
        ]

    def __str__(self):
        return f'Lote {self.numero_lote or self.pk} de {self.id_producto}'


class MovimientoStock(LibroInmutable, models.Model):
    """Asiento del libro de stock. Append-only."""

    TIPOS = (
        ('ENTRADA', 'Entrada'), ('SALIDA', 'Salida'), ('MERMA', 'Merma'),
        ('AJUSTE', 'Ajuste'), ('ANULACION', 'Anulación'),
    )

    id_movimiento_stock = models.AutoField('ID', primary_key=True)
    id_producto = models.ForeignKey(
        'maestros.Producto', on_delete=models.RESTRICT,
        db_column='id_producto', related_name='movimientos',
        verbose_name='Producto')
    id_usuario = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='movimientos_stock', verbose_name='Usuario')
    id_usuario_autorizante = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_autorizante', null=True, blank=True,
        related_name='movimientos_autorizados', verbose_name='Autoriza')
    id_lote = models.ForeignKey(
        Lote, on_delete=models.RESTRICT, db_column='id_lote',
        null=True, blank=True, related_name='movimientos', verbose_name='Lote')
    id_venta = models.ForeignKey(
        Venta, on_delete=models.RESTRICT, db_column='id_venta',
        null=True, blank=True, related_name='movimientos_stock')
    id_compra = models.ForeignKey(
        Compra, on_delete=models.RESTRICT, db_column='id_compra',
        null=True, blank=True, related_name='movimientos_stock')
    tipo_movimiento = models.CharField('Tipo', max_length=10, choices=TIPOS)
    cantidad = models.IntegerField('Cantidad')
    motivo_ajuste = models.CharField(
        'Motivo', max_length=120, null=True, blank=True)
    fecha_movimiento = models.DateTimeField('Fecha', default=timezone.now)

    # Un asiento no se corrige, se contra-asienta: es el libro de stock: el stock del producto es su suma.
    # El manager corta también el camino del conjunto, porque
    # QuerySet.update()/delete() no pasan por la instancia.
    objects = ManagerDeLibro()

    def save(self, *args, **kwargs):
        """Asienta el movimiento y mueve el stock, en la misma transacción.

        Reemplaza a ``fn_actualiza_stock``. ``cantidad`` ya viene con signo
        —negativo si sale—, así que sumar alcanza.

        Va acá y no en un servicio porque el asiento y el saldo tienen que
        moverse juntos SIEMPRE: si quedara afuera, un ``create()`` suelto
        dejaría el stock desfasado y nadie se enteraría.
        """
        from django.db import transaction

        # Import adentro del método: maestros.models importa de acá, así que
        # a nivel de módulo sería circular.
        from maestros.models import Producto

        with transaction.atomic():
            # T05 · fn_movimiento_stock_valida: el lote y el producto tienen
            # que contar la misma historia antes de tocar ningún saldo.
            producto = (Producto.objects.select_for_update()
                        .get(pk=self.id_producto_id))

            if producto.controla_vencimiento and self.id_lote_id is None:
                raise ValidationError(
                    'El producto "%s" se gestiona por lote: el movimiento '
                    'exige indicar cuál (DD-20).' % producto)
            if not producto.controla_vencimiento and self.id_lote_id:
                raise ValidationError(
                    'El producto "%s" no se gestiona por lote: el movimiento '
                    'no lleva lote.' % producto)

            if self.id_lote_id:
                lote = (Lote.objects.select_for_update()
                        .filter(pk=self.id_lote_id,
                                id_producto=producto).first())
                if lote is None:
                    raise ValidationError(
                        'El lote indicado no pertenece al producto "%s".'
                        % producto)
                if lote.stock_lote + self.cantidad < 0:
                    raise ValidationError(
                        'La salida de %d unidad(es) supera el stock del lote '
                        '(hay %d).' % (-self.cantidad, lote.stock_lote))

            super().save(*args, **kwargs)

            producto.actualizar_saldo_derivado(
                stock_actual=producto.stock_actual + self.cantidad)

            if self.id_lote_id:
                lote.actualizar_saldo_derivado(
                    stock_lote=lote.stock_lote + self.cantidad)

    class Meta:
        db_table = 'movimientos_stock'
        verbose_name = 'Movimiento de stock'
        verbose_name_plural = 'Movimientos de stock'
        ordering = ['-fecha_movimiento']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "tipo_movimiento IN ('ENTRADA','SALIDA','MERMA','AJUSTE',"
                    "'ANULACION')"),
                name='ck_movstock_tipo'),
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_movimiento IN ('ENTRADA','ANULACION') AND cantidad "
                    "> 0) OR (tipo_movimiento IN ('SALIDA','MERMA') AND "
                    "cantidad < 0) OR (tipo_movimiento = 'AJUSTE' AND cantidad"
                    " <> 0)"),
                name='ck_movstock_signo'),
            CheckEnBase(
                check=CheckSQL(
                    "tipo_movimiento NOT IN ('MERMA','AJUSTE') OR "
                    "(id_usuario_autorizante IS NOT NULL AND motivo_ajuste IS "
                    "NOT NULL AND id_usuario_autorizante <> id_usuario)"),
                name='ck_movstock_autorizacion'),
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_movimiento = 'ENTRADA' AND id_compra IS NOT NULL AND"
                    " id_venta IS NULL) OR (tipo_movimiento IN ('SALIDA',"
                    "'ANULACION') AND id_venta IS NOT NULL AND id_compra IS "
                    "NULL) OR (tipo_movimiento IN ('MERMA','AJUSTE') AND "
                    "id_venta IS NULL AND id_compra IS NULL)"),
                name='ck_movstock_documento'),
            CheckEnBase(
                check=CheckSQL("trim(tipo_movimiento) <> ''"),
                name='ck_movimientos_stock_textos'),
        ]

    def __str__(self):
        return f'{self.tipo_movimiento} {self.cantidad:+} de {self.id_producto}'


class InventarioFisico(SinBajaFisica, models.Model):
    """Conteo físico del depósito. Uno en curso a la vez."""

    ESTADOS = (('EN_CURSO', 'En curso'), ('CERRADO', 'Cerrado'))

    id_inventario = models.AutoField('ID', primary_key=True)
    id_usuario = models.ForeignKey(
        'usuarios.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='inventarios', verbose_name='Usuario')
    fecha_inventario = models.DateField('Fecha', default=timezone.localdate)
    estado_inventario = models.CharField(
        'Estado', max_length=15, choices=ESTADOS, default='EN_CURSO')

    # Acá la baja es lógica: el DELETE no corre (RN-8.1.1).
    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'inventarios_fisicos'
        verbose_name = 'Inventario físico'
        verbose_name_plural = 'Inventarios físicos'
        ordering = ['-fecha_inventario']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "estado_inventario IN ('EN_CURSO','CERRADO')"),
                name='ck_inventarios_estado'),
            CheckEnBase(
                check=CheckSQL("trim(estado_inventario) <> ''"),
                name='ck_inventarios_fisicos_textos'),
            # Un solo inventario en curso en todo el sistema.
            models.UniqueConstraint(
                models.Value(1),
                condition=models.Q(estado_inventario='EN_CURSO'),
                name='uq_inventario_en_curso'),
        ]

    def __str__(self):
        return f'Inventario {self.fecha_inventario:%d/%m/%Y}'


class DetalleInventario(models.Model):
    """Renglón del conteo: teórico, contado y diferencia."""

    id_detalle_inventario = models.AutoField('ID', primary_key=True)
    id_inventario = models.ForeignKey(
        InventarioFisico, on_delete=models.CASCADE, db_column='id_inventario',
        related_name='detalles', verbose_name='Inventario')
    id_producto = models.ForeignKey(
        'maestros.Producto', on_delete=models.RESTRICT,
        db_column='id_producto', related_name='renglones_inventario',
        verbose_name='Producto')
    cantidad_teorica = models.IntegerField('Cantidad teórica')
    cantidad_contada = models.IntegerField('Cantidad contada')
    diferencia = models.IntegerField('Diferencia')

    def _exigir_inventario_abierto(self):
        """Un inventario cerrado es inmutable (RN-5.3.2)."""
        from cafeto import contexto

        if contexto.en_pasaje_historico():
            return
        if self.id_inventario.estado_inventario == 'CERRADO':
            raise ValidationError(
                'El inventario %s está cerrado: sus renglones no se '
                'modifican (RN-5.3.2).' % self.id_inventario_id)

    def save(self, *args, **kwargs):
        """Congela la cantidad teórica y deriva la diferencia (DD-08).

        Reemplaza a ``fn_detalle_inventario_valida``. La teórica se toma
        UNA vez, al crear el renglón: si se recalculara al editar, el
        conteo se compararía contra un stock que ya cambió y la diferencia
        dejaría de significar algo.
        """
        self._exigir_inventario_abierto()

        if self._state.adding:
            self.cantidad_teorica = self.id_producto.stock_actual
        else:
            anterior = type(self).objects.filter(pk=self.pk).values_list(
                'cantidad_teorica', flat=True).first()
            if anterior is not None:
                self.cantidad_teorica = anterior

        self.diferencia = self.cantidad_contada - self.cantidad_teorica
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._exigir_inventario_abierto()
        return super().delete(*args, **kwargs)

    class Meta:
        db_table = 'detalles_inventario'
        verbose_name = 'Renglón de inventario'
        verbose_name_plural = 'Renglones de inventario'
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'cantidad_teorica >= 0 AND cantidad_contada >= 0 AND '
                    'diferencia = cantidad_contada - cantidad_teorica'),
                name='ck_detinv_valores'),
            models.UniqueConstraint(
                fields=['id_inventario', 'id_producto'],
                name='uq_detalles_inventario_id_inventario_id_producto'),
        ]

    def __str__(self):
        return f'{self.id_producto}: {self.cantidad_contada} ({self.diferencia:+})'
