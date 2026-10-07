"""Modelos de Proveedores y de Ubicaciones (Provincias / Localidades).

Las tablas de ubicación viven acá porque fueron las primeras en usarse
desde el alta de proveedores. Desde la Fase 4 también son parte de
"Mantener datos maestros" (DFD 10) y tienen su propio CRUD.

Alineación con la base: las tablas se llaman ``provincias``,
``localidades`` y ``proveedores``, y cada columna lleva el nombre que le
da el DER.

Geografía: las provincias y las localidades NO tienen baja lógica. Son un
catálogo geográfico: una provincia no deja de existir, y borrarla lógicamente
sólo escondería direcciones históricas. Por eso su CRUD es alta y
modificación solamente (regla 4.4.a).
"""
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.db import models
from django.utils import timezone

from cafeto.restricciones import CheckEnBase, CheckSQL, ClaveNormalizada
from cafeto.reglas import (ConSelloDeModificacion,
                           ManagerSinBajaFisica,
                           SinBajaFisica)
from cafeto.validaciones import (
    exigir_que_no_este_dado_de_baja,
    NO_NEGATIVO,
    VALIDADOR_CUIT,
    VALIDADOR_TELEFONO,
    clave_normalizada,
    normalizar_cuit,
    normalizar_telefono,
    texto_de_catalogo,
    validar_codigo_postal,
    validar_cuit,
    validar_telefono,
)
from usuarios.models import sello_fecha_modificacion, sello_usuario_modificacion

# Re-exportados para no romper los imports que ya existían
# (``from .models import normalizar_telefono`` en forms.py).
__all__ = ['Provincia', 'Localidad', 'Proveedor', 'CondicionPago',
           'normalizar_cuit', 'normalizar_telefono']


# ===========================================================================
#  UBICACIONES
# ===========================================================================
class Provincia(models.Model):
    """Jurisdicción argentina. Catálogo de referencia (DD-16)."""

    id_provincia = models.AutoField('ID', primary_key=True)
    nombre_provincia = models.CharField('Provincia', max_length=60)

    class Meta:
        db_table = 'provincias'
        verbose_name = 'Provincia'
        verbose_name_plural = 'Provincias'
        ordering = ['nombre_provincia']
        constraints = [
            CheckEnBase(
                check=CheckSQL("trim(nombre_provincia) <> ''"),
                name='ck_provincias_textos'),
            models.UniqueConstraint(
                ClaveNormalizada('nombre_provincia'),
                name='uq_provincias_nombre_norm'),
        ]

    def __str__(self):
        return self.nombre_provincia

    def clean(self):
        super().clean()
        self.nombre_provincia = texto_de_catalogo(
            self.nombre_provincia, 3, 60, 'El nombre de la provincia')


class Localidad(models.Model):
    """Localidad perteneciente a una provincia."""

    id_localidad = models.AutoField('ID', primary_key=True)
    id_provincia = models.ForeignKey(
        Provincia, on_delete=models.RESTRICT, db_column='id_provincia',
        verbose_name='Provincia', related_name='localidades')
    nombre_localidad = models.CharField('Localidad', max_length=80)
    codigo_postal = models.CharField(
        'Código postal', max_length=8, null=True, blank=True)

    class Meta:
        db_table = 'localidades'
        verbose_name = 'Localidad'
        verbose_name_plural = 'Localidades'
        ordering = ['nombre_localidad']
        constraints = [
            CheckEnBase(
                check=CheckSQL("trim(nombre_localidad) <> ''"),
                name='ck_localidades_textos'),
            # Hay localidades homónimas en provincias distintas: el nombre
            # es único DENTRO de su provincia (DD-50).
            models.UniqueConstraint(
                'id_provincia', ClaveNormalizada('nombre_localidad'),
                name='uq_localidades_nombre_norm'),
        ]

    def __str__(self):
        return f'{self.nombre_localidad} ({self.id_provincia.nombre_provincia})'

    def clean(self):
        super().clean()
        self.nombre_localidad = texto_de_catalogo(
            self.nombre_localidad, 2, 80, 'El nombre de la localidad')
        self.codigo_postal = validar_codigo_postal(self.codigo_postal)


# ===========================================================================
#  PROVEEDOR
# ===========================================================================
class CondicionPago(models.TextChoices):
    """Va FUERA de la clase para poder usarla en Meta.constraints."""
    CONTADO = 'CONTADO', 'Contado'
    CUENTA_CORRIENTE = 'CTA_CTE', 'Cuenta corriente'
    DIAS_15 = '15_DIAS', '15 días'
    DIAS_30 = '30_DIAS', '30 días'
    DIAS_60 = '60_DIAS', '60 días'


class Proveedor(ConSelloDeModificacion,
                SinBajaFisica, models.Model):
    """Proveedor de mercadería. Tabla PROVEEDORES del DER."""

    # Alias: hace que 'Proveedor.CondicionPago' siga funcionando en views.py
    CondicionPago = CondicionPago

    id_proveedor = models.AutoField('ID', primary_key=True)
    nombre_proveedor = models.CharField('Nombre', max_length=50)
    cuit_proveedor = models.CharField('CUIT', max_length=13,
        validators=[VALIDADOR_CUIT])
    telefono_proveedor = models.CharField(
        'Teléfono', max_length=15, null=True, blank=True,
        validators=[VALIDADOR_TELEFONO])
    email_proveedor = models.EmailField(
        'Email', max_length=254, null=True, blank=True)
    condicion_pago = models.CharField(
        'Condición de pago',
        max_length=10,
        choices=CondicionPago.choices,
        default=CondicionPago.CONTADO,
    )
    fecha_alta = models.DateTimeField(
        'Fecha de alta', default=timezone.now, editable=False)
    domicilio = models.CharField(
        'Domicilio', max_length=120, null=True, blank=True)
    id_localidad = models.ForeignKey(
        Localidad, on_delete=models.RESTRICT, db_column='id_localidad',
        null=True, blank=True,
        verbose_name='Localidad', related_name='proveedores')

    borrado_logico = models.BooleanField('Borrado lógico', default=False)
    fecha_modificacion = sello_fecha_modificacion()
    usuario_modificacion = sello_usuario_modificacion('proveedores_modificados')

    # Sello de modificación y baja lógica, ahora en mixins del ORM
    # (DD-17 y RN-8.1.1).
    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'proveedores'
        verbose_name = 'Proveedor'
        verbose_name_plural = 'Proveedores'
        ordering = ['nombre_proveedor']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "cuit_proveedor ~ '^[0-9]{2}-[0-9]{8}-[0-9]$'"),
                name='ck_proveedores_cuit'),
            CheckEnBase(
                check=CheckSQL(
                    "condicion_pago IN ('CONTADO','CTA_CTE','15_DIAS',"
                    "'30_DIAS','60_DIAS')"),
                name='ck_proveedores_condicion'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(nombre_proveedor) <> '' AND trim(cuit_proveedor) <> ''"
                    " AND trim(condicion_pago) <> ''"),
                name='ck_proveedores_textos'),
            models.UniqueConstraint(
                fields=['cuit_proveedor'],
                name='uq_proveedores_cuit_proveedor'),
            # La razón social, única por CLAVE NORMALIZADA (DD-50), igual
            # que en todas las maestras: "Arcor SA", "ARCOR SA" y
            # "arcor  sa" son el mismo proveedor.
            #
            # Va además del UNIQUE del CUIT, que es la identidad fiscal y
            # no alcanza: dos altas del mismo proveedor con el CUIT mal
            # tipeado pasaban las dos, y quedaban dos razones sociales
            # iguales compitiendo en el combo de productos y de compras.
            #
            # Se usa ``ClaveNormalizada`` y no ``Lower``: además de las
            # mayúsculas, saca los acentos y colapsa los espacios
            # internos, así "Café del Valle" y "cafe del valle" también
            # chocan. ``Lower`` sólo cubriría las mayúsculas.
            models.UniqueConstraint(
                ClaveNormalizada('nombre_proveedor'),
                name='uq_proveedores_nombre_norm'),
        ]

    def __str__(self):
        return self.nombre_proveedor

    def _exigir_nombre_sin_duplicado(self):
        """Razón social repetida: se corta acá y se explica (V-G8).

        El índice único ya lo impide, pero un IntegrityError de PostgreSQL
        le llega al usuario como un error 500 que no dice nada. Acá se
        busca con la MISMA función que usa el índice
        (``f_clave_normalizada``), así lo que se rechaza en Python es
        exactamente lo que rechazaría la base, y se puede decir en
        castellano qué fila está ocupando el nombre.

        Se compara sobre ``self.nombre_proveedor``, que en este punto ya
        pasó por ``texto_de_catalogo()``: viene sin espacios de sobra en
        las puntas ni espacios internos repetidos.
        """
        if not self.nombre_proveedor:
            return
        candidatos = type(self).objects.annotate(
            _clave=ClaveNormalizada('nombre_proveedor')
        ).filter(_clave=clave_normalizada(self.nombre_proveedor))
        if self.pk is not None:
            candidatos = candidatos.exclude(pk=self.pk)
        duplicado = candidatos.first()
        if duplicado is None:
            return
        if duplicado.borrado_logico:
            raise ValidationError({'nombre_proveedor': (
                f'Ya existe un registro con este nombre («{duplicado}», CUIT '
                f'{duplicado.cuit_proveedor}), pero está dado de baja. '
                'Buscalo con el filtro "Dados de baja" y reactivalo en vez '
                'de crear uno nuevo.')})
        raise ValidationError({'nombre_proveedor': (
            'Ya existe un registro con este nombre (CUIT '
            f'{duplicado.cuit_proveedor}). No se distinguen mayúsculas, '
            'acentos ni espacios de más.')})

    # ------------------------------------------------------------------
    #  Limpieza y validación (capa 2 de 4.4.V)
    # ------------------------------------------------------------------
    def clean(self):
        super().clean()
        self.nombre_proveedor = texto_de_catalogo(
            self.nombre_proveedor, 2, 50, 'El nombre del proveedor')
        self.cuit_proveedor = validar_cuit(self.cuit_proveedor)
        self.telefono_proveedor = validar_telefono(self.telefono_proveedor)

        # Los UNIQUE no distinguen vigentes de dados de baja: sin esto,
        # recargar un proveedor dado de baja terminaba en un error 500 de
        # PostgreSQL en vez de decir qué hacer con él.
        exigir_que_no_este_dado_de_baja(
            type(self), self, {'cuit_proveedor': self.cuit_proveedor},
            etiqueta='El proveedor con CUIT %s' % self.cuit_proveedor)

        self._exigir_nombre_sin_duplicado()

        if self.email_proveedor:
            correo = self.email_proveedor.strip().lower()
            EmailValidator(
                message='El correo no tiene un formato válido '
                        '(ej. ventas@proveedor.com).')(correo)
            if len(correo) > 254:
                raise ValidationError(
                    {'email_proveedor': 'El correo no puede superar los 254 '
                                        'caracteres.'})
            self.email_proveedor = correo
        else:
            self.email_proveedor = None

        self.domicilio = texto_de_catalogo(
            self.domicilio, 3, 120, 'El domicilio', obligatorio=False)

        # Un domicilio sin localidad no se puede ubicar ni usar en un remito.
        if self.domicilio and self.id_localidad_id is None:
            raise ValidationError({
                'id_localidad': 'Si cargás un domicilio, la localidad es '
                                'obligatoria.',
            })

        if self.condicion_pago not in CondicionPago.values:
            raise ValidationError({
                'condicion_pago': 'Elegí una condición de pago de la lista.',
            })

    def save(self, *args, **kwargs):
        # Red de seguridad para las altas por shell o por /admin, que no
        # siempre pasan por full_clean().
        if self.nombre_proveedor:
            self.nombre_proveedor = self.nombre_proveedor.strip()
        if self.email_proveedor:
            self.email_proveedor = self.email_proveedor.strip().lower()
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------
    #  V-G8 · duplicados por clave normalizada
    # ------------------------------------------------------------------
    @property
    def clave(self):
        return clave_normalizada(self.nombre_proveedor)

    # ------------------------------------------------------------------
    #  V-G10 · dependientes que impiden la baja
    # ------------------------------------------------------------------
    def motivos_que_impiden_la_baja(self):
        """Lista de motivos por los que este proveedor no se puede dar de baja.

        Vacía = se puede. Cada motivo ya viene redactado para mostrarlo tal
        cual al usuario, con la cantidad, para que sepa qué tiene que
        resolver antes.
        """
        from maestros.models import Producto
        from operaciones.models import Compra, OrdenCompra

        motivos = []

        abiertas = OrdenCompra.objects.filter(
            id_proveedor=self, estado_orden__in=('PENDIENTE', 'EMITIDA')
        ).count()
        if abiertas:
            motivos.append(
                f'tiene {abiertas} orden(es) de compra sin cerrar '
                '(pendientes o emitidas)')

        impagas = Compra.objects.filter(id_proveedor=self).exclude(
            estado_compra='PAGADA').count()
        if impagas:
            motivos.append(f'tiene {impagas} compra(s) que todavía no están pagadas')

        productos = Producto.objects.filter(
            id_proveedor=self, borrado_logico=False).count()
        if productos:
            motivos.append(
                f'tiene {productos} producto(s) vigente(s) que lo tienen como '
                'proveedor (reasignalos primero)')

        return motivos
