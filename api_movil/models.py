"""Las tablas propias de la API móvil.

    SESIONES_MOVILES          la sesión del celular de cada persona del
                              equipo: una por usuario, revocable
    OPERACIONES_CELULAR       las operaciones ya hechas, por su clave
                              (``Idempotency-Key``)
    INTENTOS_VINCULACION      los DNI que no coincidieron al vincular una
                              cuenta de cliente (el freno a probar DNI)

Van en la base y no en la caché a propósito: la caché de Django, sin
configurar, vive en la memoria de cada proceso, y en el NAS corren varios
procesos de gunicorn. Un reintento que cayera en otro proceso no vería la
clave y repetiría la merma; una sesión cerrada en uno seguiría abierta en
otro.
"""
from django.db import models
from django.utils import timezone


class SesionMovil(models.Model):
    """La sesión de la app de una persona del equipo (DD-01, en el celular).

    La web tiene SESIONES; el celular tiene esta tabla aparte para no cerrar
    la sesión de la web al entrar. Igual que allá, hay UNA abierta por
    usuario: entrar de nuevo cierra la anterior, y salir la cierra.

    El token que lleva la app trae el ``jti`` de esta fila, firmado: sin la
    fila abierta, el token no vale aunque la firma esté bien. El token en
    sí nunca se guarda.
    """

    MOTIVOS = (
        ('SALIDA', 'Salió desde la app'),
        ('NUEVO_INGRESO', 'Entró de nuevo (en este u otro celular)'),
        ('CAMBIO_DE_CLAVE', 'Eligió una clave nueva'),
    )

    id_sesion_movil = models.BigAutoField('ID', primary_key=True)
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='sesiones_moviles', verbose_name='Usuario')
    jti = models.CharField('Identificador del token', max_length=64)
    creada = models.DateTimeField('Inicio', default=timezone.now)
    ultima_actividad = models.DateTimeField('Último uso', default=timezone.now)
    ip_origen = models.CharField('IP de origen', max_length=45)
    revocada_en = models.DateTimeField('Cerrada el', null=True, blank=True)
    motivo_revocacion = models.CharField(
        'Motivo del cierre', max_length=20, choices=MOTIVOS, null=True,
        blank=True)

    class Meta:
        db_table = 'sesiones_moviles'
        verbose_name = 'Sesión móvil'
        verbose_name_plural = 'Sesiones móviles'
        constraints = [
            models.UniqueConstraint(fields=['jti'], name='uq_sesiones_moviles_jti'),
            # Una sola sesión móvil abierta por usuario (como DD-01).
            models.UniqueConstraint(
                fields=['id_usuario'], condition=models.Q(revocada_en__isnull=True),
                name='uq_sesion_movil_abierta_por_usuario'),
            models.CheckConstraint(
                condition=models.Q(revocada_en__isnull=True, motivo_revocacion__isnull=True)
                | models.Q(revocada_en__isnull=False, motivo_revocacion__isnull=False),
                name='ck_sesiones_moviles_cierre'),
        ]

    def __str__(self):
        return f'Sesión móvil {self.pk} de {self.id_usuario}'


class IntentoDeVinculacion(models.Model):
    """Un DNI que no coincidió al vincular una cuenta de la app con su ficha.

    Cuando dos fichas comparten el correo, la app pide el DNI para elegir
    la suya. Sin un freno, la misma cuenta podría probar DNI hasta dar con
    el de otra ficha y quedarse con sus puntos.
    """

    id_intento = models.BigAutoField('ID', primary_key=True)
    uid_firebase = models.CharField('Cuenta de la app', max_length=128)
    fecha = models.DateTimeField('Fecha', default=timezone.now)

    class Meta:
        db_table = 'intentos_vinculacion'
        verbose_name = 'Intento de vinculación'
        verbose_name_plural = 'Intentos de vinculación'
        indexes = [models.Index(fields=['uid_firebase', 'fecha'],
                                name='ix_intentos_vinculacion_uid')]

    def __str__(self):
        return f'{self.uid_firebase} · {self.fecha:%d/%m %H:%M}'


class OperacionDelCelular(models.Model):
    """Una operación que ya se hizo con esta clave: no se repite.

    La fila se escribe en la MISMA transacción que la operación: si la
    operación falla, la clave queda libre para reintentar. El índice único
    es el que frena dos envíos simultáneos de la misma clave.
    """

    id_operacion = models.BigAutoField('ID', primary_key=True)
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='+', verbose_name='Usuario')
    clave = models.CharField('Clave de la operación', max_length=100)
    operacion = models.CharField('Operación', max_length=20)
    id_registro = models.BigIntegerField('ID de lo que se creó', null=True,
                                         blank=True)
    fecha = models.DateTimeField('Fecha', default=timezone.now)

    class Meta:
        db_table = 'operaciones_celular'
        verbose_name = 'Operación desde el celular'
        verbose_name_plural = 'Operaciones desde el celular'
        constraints = [
            models.UniqueConstraint(fields=['id_usuario', 'clave'],
                                    name='uq_operaciones_celular_clave'),
        ]
        indexes = [models.Index(fields=['fecha'],
                                name='ix_operaciones_celular_fecha')]

    def __str__(self):
        return f'{self.operacion} {self.id_registro} ({self.clave})'
