"""Módulo Negocio: los datos del comercio y sus parámetros de operación.

Una sola tabla, PARAMETROS_NEGOCIO, con UNA sola fila.

Por qué una tabla y no constantes en el código: son decisiones de la
dueña, no del programador. Cuánta diferencia de caja se tolera sin que
intervenga la encargada, cuántos días hay para cambiar un producto o el
CUIT que sale en los reportes cambian sin que cambie el sistema, y
tienen que poder tocarse desde una pantalla —con auditoría— y no con un
deploy.

Por qué una sola fila: son los datos de ESTE comercio. La fila se fuerza
con ``id_parametros = 1`` (CHECK) y la crea la migración de datos; nadie
la da de alta ni de baja.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from nucleo.modelos import (
    campo_importe,
    sello_fecha_modificacion,
    sello_usuario_modificacion,
)
from nucleo.reglas import ConSelloDeModificacion, ManagerSinBajaFisica, SinBajaFisica
from nucleo.restricciones import CheckEnBase, CheckSQL
from nucleo.validaciones import (
    VALIDADOR_CUIT,
    VALIDADOR_TELEFONO,
    texto_de_catalogo,
    validar_cuit,
    validar_decimal,
    validar_entero,
    validar_telefono,
)


class CondicionIVA(models.TextChoices):
    MONOTRIBUTO = 'MONOTRIBUTO', 'Monotributista'
    RESPONSABLE_INSCRIPTO = 'RI', 'Responsable inscripto'
    EXENTO = 'EXENTO', 'Exento'


class ParametrosNegocio(ConSelloDeModificacion, SinBajaFisica, models.Model):
    """Datos del comercio y reglas de operación que decide la dueña."""

    ID_UNICO = 1

    id_parametros = models.PositiveSmallIntegerField(
        primary_key=True, default=ID_UNICO, editable=False)

    # --- El comercio -------------------------------------------------------
    razon_social = models.CharField('Razón social', max_length=80)
    nombre_fantasia = models.CharField('Nombre de fantasía', max_length=60)
    cuit = models.CharField('CUIT', max_length=13, null=True, blank=True,
                            validators=[VALIDADOR_CUIT])
    condicion_iva = models.CharField(
        'Condición frente al IVA', max_length=12,
        choices=CondicionIVA.choices, default=CondicionIVA.MONOTRIBUTO)
    domicilio = models.CharField('Domicilio', max_length=120, null=True,
                                 blank=True)
    id_localidad = models.ForeignKey(
        'geografia.Localidad', on_delete=models.RESTRICT,
        db_column='id_localidad', null=True, blank=True,
        related_name='+', verbose_name='Localidad')
    telefono = models.CharField('Teléfono', max_length=15, null=True,
                                blank=True, validators=[VALIDADOR_TELEFONO])
    correo = models.EmailField('Correo', max_length=254, null=True,
                               blank=True)

    # --- La operación ------------------------------------------------------
    redondeo_ventas = campo_importe(
        'Redondeo del total', default=Decimal('1.00'),
        help_text='A cuánto se redondea el total de cada venta: $1 = sin '
                  'centavos; $0,10; $0,01 = exacto.')
    tolerancia_arqueo = campo_importe(
        'Diferencia de caja tolerada', default=Decimal('500.00'),
        help_text='Hasta este monto (en más o en menos) el cajero cierra '
                  'solo; si la diferencia es mayor, el cierre lo supervisa '
                  'alguien con permiso.')
    fondo_caja_sugerido = campo_importe(
        'Fondo de caja sugerido', default=Decimal('10000.00'),
        help_text='Lo que se propone como fondo de cambio al abrir un turno.')
    dias_para_cambios = models.PositiveSmallIntegerField(
        'Días para cambios y devoluciones', default=30)
    dias_aviso_vencimiento = models.PositiveSmallIntegerField(
        'Avisar vencimientos con (días)', default=7)

    fecha_modificacion = sello_fecha_modificacion()
    usuario_modificacion = sello_usuario_modificacion(
        'parametros_modificados')

    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'parametros_negocio'
        verbose_name = 'Parámetros del negocio'
        verbose_name_plural = 'Parámetros del negocio'
        constraints = [
            CheckEnBase(check=CheckSQL('id_parametros = 1'),
                        name='ck_parametros_fila_unica'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(razon_social) <> '' AND trim(nombre_fantasia) <> ''"),
                name='ck_parametros_textos'),
            CheckEnBase(
                check=CheckSQL(
                    "cuit IS NULL OR cuit ~ '^[0-9]{2}-[0-9]{8}-[0-9]$'"),
                name='ck_parametros_cuit'),
            CheckEnBase(
                check=CheckSQL(
                    "condicion_iva IN ('MONOTRIBUTO','RI','EXENTO')"),
                name='ck_parametros_iva'),
            CheckEnBase(
                check=CheckSQL(
                    'redondeo_ventas IN (0.01, 0.10, 1.00) AND '
                    'tolerancia_arqueo >= 0 AND fondo_caja_sugerido >= 0 AND '
                    'dias_para_cambios BETWEEN 0 AND 365 AND '
                    'dias_aviso_vencimiento BETWEEN 1 AND 90'),
                name='ck_parametros_operacion'),
        ]

    def __str__(self):
        return self.nombre_fantasia

    @classmethod
    def vigentes(cls):
        """La fila única. La crea la migración; si no está, se crea acá."""
        fila = cls.objects.filter(pk=cls.ID_UNICO).first()
        if fila is None:
            fila = cls.objects.create(razon_social='Cafeto Las Lomitas',
                                      nombre_fantasia='Cafeto')
        return fila

    def clean(self):
        super().clean()
        self.razon_social = texto_de_catalogo(
            self.razon_social, 3, 80, 'La razón social')
        self.nombre_fantasia = texto_de_catalogo(
            self.nombre_fantasia, 2, 60, 'El nombre de fantasía')
        self.domicilio = texto_de_catalogo(
            self.domicilio, 3, 120, 'El domicilio', obligatorio=False)
        if self.cuit:
            self.cuit = validar_cuit(self.cuit)
        else:
            self.cuit = None
        self.telefono = validar_telefono(self.telefono)
        if self.redondeo_ventas not in (Decimal('0.01'), Decimal('0.10'),
                                        Decimal('1.00')):
            raise ValidationError({'redondeo_ventas': (
                'Elegí $0,01 (exacto), $0,10 o $1.')})
        validar_decimal(self.tolerancia_arqueo, Decimal('0'),
                        Decimal('9999999'), 2, 'La tolerancia')
        validar_decimal(self.fondo_caja_sugerido, Decimal('0'),
                        Decimal('9999999'), 2, 'El fondo sugerido')
        validar_entero(self.dias_para_cambios, 0, 365,
                       'Los días para cambios')
        validar_entero(self.dias_aviso_vencimiento, 1, 90,
                       'Los días de aviso')

    def delete(self, *args, **kwargs):
        raise ValidationError('Los parámetros del negocio no se borran.')
