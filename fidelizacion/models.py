"""Módulo Clientes y Fidelización (DFD 9).

CATEGORIAS_FIDELIZACION, BENEFICIOS con su receta en especie
(BENEFICIOS_PRODUCTOS), CLIENTES y el libro MOVIMIENTOS_PUNTOS.

Los puntos de un cliente NO se escriben a mano: los mueve el libro de
puntos.
"""
import secrets
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from nucleo.modelos import (
    Maestra,
    campo_importe,
    sello_fecha_modificacion,
    sello_usuario_modificacion,
)
from nucleo.reglas import (
    ConSaldosDerivados,
    ConSelloDeModificacion,
    LibroInmutable,
    ManagerDeLibro,
    SinBajaFisica,
)
from nucleo.restricciones import CheckEnBase, CheckSQL, ClaveNormalizada
from nucleo.validaciones import (
    NO_NEGATIVO,
    VALIDADOR_DNI,
    exigir_dni_creible,
    validar_anio_de_negocio,
    VALIDADOR_TELEFONO,
    clave_normalizada,
    exigir_nombre_unico,
    exigir_que_no_este_dado_de_baja,
    texto_de_catalogo,
    validar_decimal,
    validar_edad,
    validar_entero,
    validar_orden_de_fechas,
)
from nucleo.textos import pluralizar


# ===========================================================================
#  CATEGORÍAS DE FIDELIZACIÓN
# ===========================================================================
class CategoriaFidelizacion(Maestra):
    """Escalón del programa de fidelización (Bronze, Silver, Gold...).

    ``importe_por_punto`` es cuántos pesos hay que gastar para sumar un
    punto (DD-49): la categoría de ingreso pide más pesos por punto y cada
    escalón pide menos.
    """

    id_categoria = models.AutoField('ID', primary_key=True)
    nombre_categoria = models.CharField('Categoría', max_length=15)
    puntos_minimos = models.IntegerField('Puntos mínimos')
    importe_por_punto = models.DecimalField(
        'Importe por punto ($)', max_digits=12, decimal_places=2,
        validators=[NO_NEGATIVO])
    usuario_modificacion = sello_usuario_modificacion('categorias_modificadas')

    class Meta:
        db_table = 'categorias_fidelizacion'
        verbose_name = 'Categoría de fidelización'
        verbose_name_plural = 'Categorías de fidelización'
        ordering = ['puntos_minimos']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'puntos_minimos >= 0 AND importe_por_punto > 0'),
                name='ck_categorias_valores'),
            CheckEnBase(
                check=CheckSQL("trim(nombre_categoria) <> ''"),
                name='ck_categorias_fidelizacion_textos'),
            models.UniqueConstraint(
                fields=['puntos_minimos'],
                name='uq_categorias_fidelizacion_puntos_minimos'),
            models.UniqueConstraint(
                ClaveNormalizada('nombre_categoria'),
                name='uq_categorias_nombre_norm'),
        ]

    def __str__(self):
        return self.nombre_categoria

    @property
    def clave(self):
        return clave_normalizada(self.nombre_categoria)

    @property
    def es_de_ingreso(self):
        """La categoría con umbral 0: la que recibe a todo cliente nuevo."""
        return self.puntos_minimos == 0

    def clean(self):
        super().clean()
        self.nombre_categoria = texto_de_catalogo(
            self.nombre_categoria, 3, 15, 'El nombre de la categoría',
            exige_letra=True)
        exigir_nombre_unico(self, 'nombre_categoria',
                            etiqueta='una categoría')
        self.puntos_minimos = validar_entero(
            self.puntos_minimos, minimo=0, maximo=10_000_000,
            etiqueta='Los puntos mínimos')
        self.importe_por_punto = validar_decimal(
            self.importe_por_punto, minimo=Decimal('0'),
            maximo=Decimal('9999999999.99'), decimales=2,
            etiqueta='El importe por punto', incluye_minimo=False)
        if self.importe_por_punto < 1:
            raise ValidationError(
                'El importe por punto es cuántos pesos hay que gastar para '
                'sumar un punto: tiene que ser de $ 1 o más.')

        self._validar_categoria_de_ingreso()

    def _validar_categoria_de_ingreso(self):
        """Siempre tiene que existir exactamente una categoría con umbral 0.

        Es la puerta de entrada: sin ella, un cliente nuevo no tendría
        categoría a la que entrar y el alta fallaría.
        """
        if self._state.adding:
            return
        anterior = type(self).objects.filter(pk=self.pk).values(
            'puntos_minimos', 'borrado_logico').first()
        if anterior is None:
            return
        era_de_ingreso = anterior['puntos_minimos'] == 0
        if era_de_ingreso and self.puntos_minimos != 0:
            raise ValidationError({'puntos_minimos': (
                'Esta es la categoría de ingreso (umbral 0): no se le puede '
                'subir el umbral, porque los clientes nuevos no tendrían a '
                'qué categoría entrar.')})

    def motivos_que_impiden_la_baja(self):
        from fidelizacion.models import Cliente

        motivos = []
        if self.es_de_ingreso:
            motivos.append(
                'es la categoría de ingreso (umbral 0): todo cliente nuevo '
                'entra por ella')
        clientes = Cliente.objects.filter(
            id_categoria=self, borrado_logico=False).count()
        if clientes:
            motivos.append(f"tiene {pluralizar(clientes, 'cliente vigente', 'clientes vigentes')}")
        beneficios = Beneficio.objects.filter(
            id_categoria=self, borrado_logico=False).count()
        if beneficios:
            motivos.append(f"tiene {pluralizar(beneficios, 'beneficio vigente', 'beneficios vigentes')}")
        return motivos


# ===========================================================================
#  BENEFICIOS
# ===========================================================================
class TipoBeneficio(models.TextChoices):
    AUTOMATICO = 'AUTOMATICO', 'Automático (se aplica solo)'
    CANJEABLE = 'CANJEABLE', 'Canjeable (el cliente entrega puntos)'


class ModoEntrega(models.TextChoices):
    DESCUENTO = 'DESCUENTO', 'Descuento en el total'
    ESPECIE = 'ESPECIE', 'Productos en especie'


class Beneficio(Maestra):
    """Premio de una categoría de fidelización.

    Se cruzan dos ejes:
      * CÓMO SE GATILLA: ``AUTOMATICO`` (lo aplica el sistema al cobrar) o
        ``CANJEABLE`` (el cliente entrega puntos).
      * QUÉ ENTREGA: ``DESCUENTO`` (un porcentaje) o ``ESPECIE`` (productos,
        que se listan en la receta BENEFICIOS_PRODUCTOS).
    """

    id_beneficio = models.AutoField('ID', primary_key=True)
    id_categoria = models.ForeignKey(
        CategoriaFidelizacion, on_delete=models.RESTRICT,
        db_column='id_categoria',
        verbose_name='Categoría', related_name='beneficios')
    descripcion_beneficio = models.CharField('Descripción', max_length=120)
    tipo_beneficio = models.CharField(
        'Tipo', max_length=15, choices=TipoBeneficio.choices)
    modo_entrega = models.CharField(
        'Modo de entrega', max_length=15, choices=ModoEntrega.choices)
    porcentaje_descuento = models.DecimalField(
        'Descuento (%)', max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[NO_NEGATIVO])
    puntos_requeridos = models.IntegerField(
        'Puntos requeridos', null=True, blank=True)
    vigencia_desde = models.DateField('Vigente desde', default=timezone.localdate)
    vigencia_hasta = models.DateField('Vigente hasta', null=True, blank=True)
    productos = models.ManyToManyField(
        'stock.Producto', through='BeneficioProducto', related_name='beneficios',
        blank=True)
    usuario_modificacion = sello_usuario_modificacion('beneficios_modificados')

    class Meta:
        db_table = 'beneficios'
        verbose_name = 'Beneficio'
        verbose_name_plural = 'Beneficios'
        ordering = ['id_categoria', 'descripcion_beneficio']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "tipo_beneficio IN ('AUTOMATICO','CANJEABLE')"),
                name='ck_beneficios_tipo'),
            CheckEnBase(
                check=CheckSQL("modo_entrega IN ('DESCUENTO','ESPECIE')"),
                name='ck_beneficios_modo'),
            CheckEnBase(
                check=CheckSQL(
                    "(modo_entrega = 'DESCUENTO' AND porcentaje_descuento IS NOT NULL)"
                    " OR (modo_entrega = 'ESPECIE' AND porcentaje_descuento IS NULL)"),
                name='ck_beneficios_que_entrega'),
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_beneficio = 'CANJEABLE' AND puntos_requeridos IS NOT NULL)"
                    " OR (tipo_beneficio = 'AUTOMATICO' AND puntos_requeridos IS NULL)"),
                name='ck_beneficios_como_se_gatilla'),
            CheckEnBase(
                check=CheckSQL(
                    '(porcentaje_descuento IS NULL OR (porcentaje_descuento > 0'
                    ' AND porcentaje_descuento <= 100)) AND (puntos_requeridos'
                    ' IS NULL OR puntos_requeridos > 0)'),
                name='ck_beneficios_rangos'),
            CheckEnBase(
                check=CheckSQL(
                    'vigencia_hasta IS NULL OR vigencia_hasta >= vigencia_desde'),
                name='ck_beneficios_vigencia'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(descripcion_beneficio) <> '' AND trim(tipo_beneficio) <> ''"
                    " AND trim(modo_entrega) <> ''"),
                name='ck_beneficios_textos'),
        ]

    def __str__(self):
        return self.descripcion_beneficio

    # ------------------------------------------------------------------
    @property
    def es_en_especie(self):
        return self.modo_entrega == ModoEntrega.ESPECIE

    @property
    def ya_se_uso(self):
        """True si algún comprobante ya lo registra.

        A partir de ese momento sólo se pueden tocar la descripción y la
        fecha de fin: cambiar el porcentaje o los puntos reescribiría la
        historia de ventas ya emitidas.
        """
        from fidelizacion.models import MovimientoPuntos
        from ventas.models import DetalleVenta, Venta

        return (
            Venta.objects.filter(id_beneficio=self).exists()
            or DetalleVenta.objects.filter(id_beneficio=self).exists()
            or MovimientoPuntos.objects.filter(id_beneficio=self).exists()
        )

    def clean(self):
        super().clean()
        self.descripcion_beneficio = texto_de_catalogo(
            self.descripcion_beneficio, 5, 120, 'La descripción del beneficio',
            exige_letra=True)

        if self.tipo_beneficio not in TipoBeneficio.values:
            raise ValidationError({'tipo_beneficio': 'Elegí un tipo de la lista.'})
        if self.modo_entrega not in ModoEntrega.values:
            raise ValidationError(
                {'modo_entrega': 'Elegí un modo de entrega de la lista.'})

        self._validar_que_entrega()
        self._validar_como_se_gatilla()
        self._validar_vigencia()
        self._validar_superposicion()

    def _validar_que_entrega(self):
        if self.modo_entrega == ModoEntrega.DESCUENTO:
            self.porcentaje_descuento = validar_decimal(
                self.porcentaje_descuento, minimo=Decimal('0'),
                maximo=Decimal('100'), decimales=2,
                etiqueta='El porcentaje de descuento', incluye_minimo=False)
        else:
            if self.porcentaje_descuento is not None:
                raise ValidationError({'porcentaje_descuento': (
                    'Un beneficio en especie no lleva porcentaje: lo que '
                    'entrega son los productos de la receta.')})

    def _validar_como_se_gatilla(self):
        if self.tipo_beneficio == TipoBeneficio.CANJEABLE:
            self.puntos_requeridos = validar_entero(
                self.puntos_requeridos, minimo=1, maximo=1_000_000,
                etiqueta='Los puntos requeridos')
        else:
            if self.puntos_requeridos is not None:
                raise ValidationError({'puntos_requeridos': (
                    'Un beneficio automático no pide puntos: lo aplica el '
                    'sistema al cobrar.')})

    def _validar_vigencia(self):
        hoy = timezone.localdate()
        if self.vigencia_desde is None:
            raise ValidationError(
                {'vigencia_desde': 'La fecha de inicio de vigencia es obligatoria.'})
        if self._state.adding:
            if self.vigencia_desde < hoy:
                raise ValidationError({'vigencia_desde': (
                    'La vigencia no puede empezar antes de hoy.')})
            if (self.vigencia_desde - hoy).days > 730:
                raise ValidationError({'vigencia_desde': (
                    'La vigencia no puede empezar a más de 2 años vista.')})
        try:
            validar_anio_de_negocio(self.vigencia_hasta, 'La fecha de fin de vigencia')
        except ValidationError as error:
            raise ValidationError({'vigencia_hasta': error.messages})
        validar_orden_de_fechas(
            self.vigencia_desde, self.vigencia_hasta,
            'la fecha de inicio de vigencia', 'La fecha de fin de vigencia')

    def _validar_superposicion(self):
        """Dos automáticos de descuento superpuestos serían ambiguos.

        Si la misma categoría tuviera dos beneficios AUTOMATICO + DESCUENTO
        vigentes a la vez, el sistema no sabría cuál aplicar al cobrar.
        """
        if not (self.tipo_beneficio == TipoBeneficio.AUTOMATICO
                and self.modo_entrega == ModoEntrega.DESCUENTO):
            return
        if self.id_categoria_id is None:
            return

        otros = type(self).objects.filter(
            id_categoria_id=self.id_categoria_id,
            tipo_beneficio=TipoBeneficio.AUTOMATICO,
            modo_entrega=ModoEntrega.DESCUENTO,
            borrado_logico=False,
        ).exclude(pk=self.pk)

        for otro in otros:
            empieza_despues = (otro.vigencia_hasta is not None
                               and self.vigencia_desde > otro.vigencia_hasta)
            termina_antes = (self.vigencia_hasta is not None
                             and self.vigencia_hasta < otro.vigencia_desde)
            if not (empieza_despues or termina_antes):
                raise ValidationError({'vigencia_desde': (
                    f'Se superpone con el beneficio automático de descuento '
                    f'"{otro.descripcion_beneficio}" (vigente desde '
                    f'{otro.vigencia_desde:%d/%m/%Y}). Cortá la vigencia de ese '
                    'beneficio o corré la de este.')})

    # ------------------------------------------------------------------
    def motivos_que_impiden_la_baja(self):
        return []

    def motivos_que_impiden_reactivar(self):
        if self.id_categoria.borrado_logico:
            return [f'su categoría "{self.id_categoria}" está dada de baja: '
                    'reactivala primero']
        return []

    def validar_receta(self):
        """ESPECIE exige receta; DESCUENTO no la admite.

        Se controla al guardar la receta y antes de dar de alta el
        beneficio, no en ``clean()``: en el alta el beneficio todavía no
        tiene PK y no se le pueden colgar renglones.
        """
        tiene_receta = BeneficioProducto.objects.filter(id_beneficio=self).exists()
        if self.es_en_especie and not tiene_receta:
            raise ValidationError(
                'Un beneficio en especie necesita al menos un producto en la '
                'receta: es lo que se le entrega al cliente.')
        if not self.es_en_especie and tiene_receta:
            raise ValidationError(
                'Un beneficio de descuento no lleva receta de productos.')


class BeneficioProducto(models.Model):
    """Renglón de la receta de un beneficio en especie.

    El DER le da clave primaria compuesta ``(id_beneficio, id_producto)``.
    Como Django no maneja PK compuestas, va una PK técnica más un UNIQUE
    sobre las dos columnas —el mismo patrón de PERFILES_PERMISOS—, que
    garantiza lo que importa: un producto no se repite dentro del mismo
    beneficio.
    """

    id_beneficio_producto = models.AutoField('ID', primary_key=True)
    id_beneficio = models.ForeignKey(
        Beneficio, on_delete=models.CASCADE, db_column='id_beneficio',
        verbose_name='Beneficio', related_name='renglones')
    id_producto = models.ForeignKey(
        'stock.Producto', on_delete=models.RESTRICT, db_column='id_producto',
        verbose_name='Producto', related_name='renglones_beneficio')
    cantidad = models.IntegerField('Cantidad', default=1)

    def delete(self, *args, **kwargs):
        """Un beneficio en especie conserva su receta (DD-18).

        Reemplaza a ``fn_beneficio_especie_receta``. Un beneficio que
        entrega un producto y no dice CUÁL no se puede cobrar: dejarlo sin
        renglones lo vuelve inaplicable sin que nadie se entere.
        """
        beneficio = self.id_beneficio
        if (beneficio and beneficio.modo_entrega == 'ESPECIE'
                and not beneficio.borrado_logico):
            quedan = BeneficioProducto.objects.filter(
                id_beneficio=beneficio).exclude(pk=self.pk).exists()
            if not quedan:
                raise ValidationError(
                    'El beneficio "%s" se entrega en especie y quedaría sin '
                    'receta (DD-18).' % beneficio)
        return super().delete(*args, **kwargs)

    class Meta:
        db_table = 'beneficios_productos'
        verbose_name = 'Producto del beneficio'
        verbose_name_plural = 'Productos del beneficio'
        ordering = ['id_beneficio', 'id_producto']
        constraints = [
            CheckEnBase(
                check=CheckSQL('cantidad > 0'), name='ck_benprod_cantidad'),
            models.UniqueConstraint(
                fields=['id_beneficio', 'id_producto'],
                name='uq_beneficios_productos'),
        ]

    def __str__(self):
        return f'{self.cantidad} × {self.id_producto}'

    def _exigir_vendible(self):
        """Un beneficio en especie entrega algo que se vende (FID-01).

        Un insumo (harina, café en grano) se usa para preparar: el mostrador
        lo rechaza con «es un insumo», así que el beneficio quedaría
        imposible de canjear.
        """
        if self.id_producto_id and not self.id_producto.es_vendible:
            raise ValidationError({'id_producto': (
                f'"{self.id_producto}" es un insumo: se usa para preparar, no '
                'se vende. Elegí un producto que se venda en el mostrador.')})

    def save(self, *args, **kwargs):
        self._exigir_vendible()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.cantidad = validar_entero(
            self.cantidad, minimo=1, maximo=99, etiqueta='La cantidad')
        if self.id_producto_id and self.id_producto.borrado_logico:
            raise ValidationError({'id_producto': (
                f'El producto "{self.id_producto}" está dado de baja: elegí '
                'uno vigente.')})
        self._exigir_vendible()


# ===========================================================================
#  CLIENTES Y PUNTOS
# ===========================================================================
class EstadoCredito(models.TextChoices):
    SIN_CREDITO = 'SIN_CREDITO', 'Sin cuenta corriente'
    HABILITADO = 'HABILITADO', 'Puede llevar fiado'
    SANCIONADO = 'SANCIONADO', 'Suspendido'
    INCOBRABLE = 'INCOBRABLE', 'Incobrable'


class Cliente(ConSaldosDerivados, ConSelloDeModificacion,
              SinBajaFisica, models.Model):
    """Cliente del programa de fidelización (Alternativa II)."""

    # Los mueve el libro de puntos, nunca una pantalla (DD-04).
    SALDOS_DERIVADOS = {'puntos_acumulados': 0, 'puntos_disponibles': 0}

    id_cliente = models.AutoField('ID', primary_key=True)
    id_categoria = models.ForeignKey(
        'fidelizacion.CategoriaFidelizacion', on_delete=models.RESTRICT,
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
    # --- Cuenta corriente (fiado, DD-91). Quién puede llevar sin pagar. ---
    estado_credito = models.CharField(
        'Cuenta corriente', max_length=12, choices=EstadoCredito.choices,
        default=EstadoCredito.SIN_CREDITO)
    limite_credito = campo_importe(
        'Tope de fiado', default=Decimal('0'), validators=[NO_NEGATIVO],
        help_text='Cuánto puede deber como máximo. 0 = sin tope fijo.')
    nota_credito = models.CharField(
        'Nota del fiado', max_length=120, null=True, blank=True,
        help_text='Cómo reconocerlo o qué se acordó: "Cobra la jubilación '
                  'el 10".')
    plazo_credito_dias = models.PositiveSmallIntegerField(
        'Días para pagar', default=30,
        help_text='El plazo que se le propone en la caja cuando lleva fiado.')
    # La cuenta de la app (Firebase) con la que el cliente se vinculó. Una
    # vez vinculada, se lo encuentra por acá y el correo deja de importar.
    # No se carga ni se edita en ninguna pantalla: lo escribe la API móvil.
    uid_firebase = models.CharField(
        'Cuenta de la app', max_length=128, null=True, blank=True,
        editable=False)
    qr_cliente = models.CharField('Código QR', max_length=64)
    puntos_acumulados = models.IntegerField(
        'Puntos acumulados', default=0, editable=False)
    puntos_disponibles = models.IntegerField(
        'Puntos disponibles', default=0, editable=False)
    fecha_alta = models.DateField('Fecha de alta', default=timezone.localdate)
    domicilio = models.CharField(
        'Domicilio', max_length=120, null=True, blank=True)
    id_localidad = models.ForeignKey(
        'geografia.Localidad', on_delete=models.RESTRICT,
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
            CheckEnBase(
                check=CheckSQL(
                    "estado_credito IN ('SIN_CREDITO','HABILITADO',"
                    "'SANCIONADO','INCOBRABLE') AND limite_credito >= 0 "
                    'AND plazo_credito_dias BETWEEN 1 AND 366'),
                name='ck_clientes_credito'),
            models.UniqueConstraint(
                fields=['qr_cliente'], name='uq_clientes_qr_cliente'),
            models.UniqueConstraint(
                fields=['telefono_cliente'],
                name='uq_clientes_telefono_cliente'),
            models.UniqueConstraint(
                fields=['dni_cliente'], name='uq_clientes_dni_cliente'),
            # Una cuenta de la app, una sola ficha.
            models.UniqueConstraint(
                fields=['uid_firebase'],
                condition=models.Q(uid_firebase__isnull=False),
                name='uq_clientes_uid_firebase'),
        ]

    def clean(self):
        """Capa 2: lo que la base no puede controlar sola.

        Las tres reglas de acá tienen algo en común: ninguna se puede
        escribir como CHECK. La edad se mide contra la fecha de hoy, y el
        duplicado dado de baja necesita mirar OTRA fila de la tabla.
        """
        super().clean()

        validar_edad(self.fecha_nacimiento, timezone.localdate())
        try:
            exigir_dni_creible(self.dni_cliente)
        except ValidationError as error:
            raise ValidationError({'dni_cliente': error.messages})

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
        if self.email_cliente and not self.borrado_logico:
            otro = self.otro_con_el_mismo_correo()
            if otro is not None:
                raise ValidationError({'email_cliente': (
                    f'El cliente con email {self.email_cliente.strip()} ya existe: '
                    f'"{otro}". Cada cliente necesita su propio correo: con él '
                    'entra a la app del club y ve sus puntos.')})

    def otro_con_el_mismo_correo(self):
        """Otro cliente VIGENTE con este correo (sin distinguir mayúsculas).

        El correo es con lo que el cliente entra a la app: con dos fichas
        vigentes que lo comparten, la app no sabría cuál mostrarle. No hay
        un índice único en la base porque pueden quedar repetidos de antes;
        esos los lista ``manage.py clientes_correo_repetido``.
        """
        if not self.email_cliente:
            return None
        return (type(self).objects
                .filter(borrado_logico=False,
                        email_cliente__iexact=self.email_cliente.strip())
                .exclude(pk=self.pk).first())

    @classmethod
    def correos_repetidos(cls):
        """Los correos que comparten dos o más clientes vigentes, con cuántos."""
        from django.db.models import Count
        from django.db.models.functions import Lower

        return (cls.objects.filter(borrado_logico=False, email_cliente__isnull=False)
                .exclude(email_cliente='')
                .annotate(correo=Lower('email_cliente'))
                .values('correo').annotate(cantidad=Count('pk'))
                .filter(cantidad__gt=1).order_by('correo'))

    @property
    def nombre_completo(self):
        return '%s, %s' % (self.apellido_cliente, self.nombre_cliente)

    @property
    def edad(self):
        """Años cumplidos, o None si no se cargó la fecha."""
        from nucleo.validaciones import anios_cumplidos

        if self.fecha_nacimiento is None:
            return None
        return anios_cumplidos(self.fecha_nacimiento, timezone.localdate())

    def __str__(self):
        return '%s, %s' % (self.apellido_cliente, self.nombre_cliente)

    # ------------------------------------------------------------------
    #  Alta: categoría de ingreso y código QR
    # ------------------------------------------------------------------
    PREFIJO_QR = 'CAF-'

    @classmethod
    def generar_qr(cls):
        """Un código nuevo para la tarjeta del cliente: ``CAF-`` y 10
        caracteres al azar (``secrets``), que no se puede adivinar a
        partir del de otro cliente y no repite ningún dato personal."""
        while True:
            codigo = cls.PREFIJO_QR + secrets.token_hex(5).upper()
            if not cls.objects.filter(qr_cliente=codigo).exists():
                return codigo

    def save(self, *args, **kwargs):
        """El cliente nuevo entra por la categoría de ingreso (umbral 0) y
        recibe su código QR: ninguno de los dos lo elige quien lo carga."""
        if self._state.adding:
            if not self.id_categoria_id:
                ingreso = (CategoriaFidelizacion.objects
                           .filter(puntos_minimos=0, borrado_logico=False)
                           .first())
                if ingreso is None:
                    raise ValidationError(
                        'No hay una categoría de ingreso (umbral 0) vigente: '
                        'sin ella el cliente no tiene por dónde entrar.')
                self.id_categoria = ingreso
            if not (self.qr_cliente or '').strip():
                self.qr_cliente = self.generar_qr()
        return super().save(*args, **kwargs)

    # ------------------------------------------------------------------
    #  Identificación en el mostrador (DFD 4 bloque C, DFD 9 bloque A)
    # ------------------------------------------------------------------
    @classmethod
    def identificar(cls, texto):
        """El cliente VIGENTE que corresponde a lo que se tipeó o escaneó:
        su código QR, su teléfono o su DNI. None si no hay ninguno."""
        import re

        from nucleo.validaciones import normalizar_telefono

        texto = (texto or '').strip()
        if not texto or '\x00' in texto:
            return None
        vigentes = cls.objects.filter(borrado_logico=False).select_related(
            'id_categoria')
        por_qr = vigentes.filter(qr_cliente__iexact=texto).first()
        if por_qr is not None:
            return por_qr
        digitos = re.sub(r'\D', '', texto)
        if 7 <= len(digitos) <= 8:
            por_dni = vigentes.filter(dni_cliente=digitos).first()
            if por_dni is not None:
                return por_dni
        try:
            telefono = normalizar_telefono(texto)
        except ValidationError:
            telefono = None
        if telefono:
            return vigentes.filter(telefono_cliente=telefono).first()
        return None

    # ------------------------------------------------------------------
    #  Baja lógica (RN-8.1.1): la historia de puntos queda
    # ------------------------------------------------------------------
    def motivos_que_impiden_la_baja(self):
        return []

    def motivos_que_impiden_reactivar(self):
        # Reactivarlo no puede dejar dos fichas vigentes con el mismo correo.
        otro = self.otro_con_el_mismo_correo()
        if otro is not None:
            return [f'su correo ({self.email_cliente}) ya es de "{otro}": '
                    'cambiáselo o unificá las fichas primero']
        return []


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
        'ventas.Venta', on_delete=models.RESTRICT, db_column='id_venta',
        null=True, blank=True, related_name='movimientos_puntos',
        verbose_name='Venta')
    id_beneficio = models.ForeignKey(
        'fidelizacion.Beneficio', on_delete=models.RESTRICT,
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

        from fidelizacion.models import CategoriaFidelizacion

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
            # El reverso de un CANJE es uno solo por beneficio y venta.
            # El de la ACUMULACION puede ir en tramos —una devolución
            # parcial, después otra—, y el tope lo pone el servicio: la
            # suma de los reversos nunca pasa lo que la venta acumuló.
            models.UniqueConstraint(
                fields=['id_venta', 'id_beneficio'],
                condition=models.Q(tipo_movimiento='ANULACION',
                                   id_beneficio__isnull=False),
                name='uq_anulacion_canje'),
        ]

    def __str__(self):
        return f'{self.tipo_movimiento} {self.puntos:+} ({self.id_cliente})'


class TipoMovimientoCuenta(models.TextChoices):
    FIADO = 'FIADO', 'Compra fiada'
    COBRO = 'COBRO', 'Pago del cliente'
    INTERES = 'INTERES', 'Recargo por mora'
    CANCELACION = 'CANCELACION', 'Baja por anulación o devolución'
    INCOBRABLE = 'INCOBRABLE', 'Pérdida por incobrable'


class MovimientoCuenta(models.Model):
    """El libro de la cuenta corriente de un cliente (DD-91).

    Cada compra fiada es una fila FIADO con su vencimiento y lo que le falta
    pagar (``saldo_pendiente``). Todo lo que la achica —un pago, la anulación
    de la venta, una devolución, darla por perdida— es otra fila que apunta
    a ese fiado (``id_fiado``). El recargo por mora (INTERES) no agranda la
    deuda: se cobra en el momento, junto con el pago.

    Lo que debe un cliente es la suma de ``saldo_pendiente`` de sus fiados.
    """

    id_movimiento_cuenta = models.AutoField('ID', primary_key=True)
    id_cliente = models.ForeignKey(
        'fidelizacion.Cliente', on_delete=models.RESTRICT, db_column='id_cliente',
        related_name='movimientos_cuenta', verbose_name='Cliente')
    tipo_movimiento = models.CharField('Tipo', max_length=12, choices=TipoMovimientoCuenta.choices)
    monto = campo_importe('Importe')
    id_venta = models.ForeignKey(
        'ventas.Venta', on_delete=models.RESTRICT, db_column='id_venta', null=True, blank=True,
        related_name='movimientos_cuenta', verbose_name='Venta')
    id_fiado = models.ForeignKey(
        'self', on_delete=models.RESTRICT, db_column='id_fiado', null=True, blank=True,
        related_name='aplicaciones', verbose_name='Fiado al que se aplica')
    id_medio_pago = models.ForeignKey(
        'ventas.MedioPago', on_delete=models.RESTRICT, db_column='id_medio_pago', null=True, blank=True,
        related_name='+', verbose_name='Medio de pago')
    id_turno_caja = models.ForeignKey(
        'ventas.TurnoCaja', on_delete=models.RESTRICT, db_column='id_turno_caja', null=True, blank=True,
        related_name='+', verbose_name='Turno de caja')
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='+', verbose_name='Usuario')
    fecha_movimiento = models.DateTimeField('Fecha', default=timezone.now)
    fecha_vencimiento = models.DateField('Vence', null=True, blank=True)
    saldo_pendiente = campo_importe('Falta pagar', null=True, blank=True)
    puntos_acreditados = models.BooleanField('Puntos acreditados', default=False)
    motivo = models.CharField('Motivo', max_length=120, null=True, blank=True)

    class Meta:
        db_table = 'movimientos_cuenta'
        verbose_name = 'movimiento de cuenta corriente'
        verbose_name_plural = 'movimientos de cuenta corriente'
        ordering = ['id_movimiento_cuenta']
        indexes = [models.Index(fields=['id_cliente', 'tipo_movimiento'], name='ix_movcuenta_cliente')]
        constraints = [
            CheckEnBase(
                check=CheckSQL("tipo_movimiento IN ('FIADO','COBRO','INTERES','CANCELACION','INCOBRABLE') "
                               'AND monto > 0'),
                name='ck_movcuenta_tipo'),
            # Un fiado nace de una venta, vence y lleva su saldo; lo demás no.
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_movimiento = 'FIADO' AND id_venta IS NOT NULL AND id_fiado IS NULL "
                    'AND fecha_vencimiento IS NOT NULL AND saldo_pendiente IS NOT NULL '
                    'AND saldo_pendiente >= 0 AND saldo_pendiente <= monto) '
                    "OR (tipo_movimiento <> 'FIADO' AND fecha_vencimiento IS NULL "
                    'AND saldo_pendiente IS NULL AND NOT puntos_acreditados)'),
                name='ck_movcuenta_fiado'),
            # Lo que achica una deuda dice cuál; el recargo no achica ninguna.
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_movimiento IN ('COBRO','CANCELACION','INCOBRABLE')) = (id_fiado IS NOT NULL)"),
                name='ck_movcuenta_aplicacion'),
            # La plata entra por un medio de pago; sólo el efectivo va a un turno.
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_movimiento IN ('COBRO','INTERES')) = (id_medio_pago IS NOT NULL) "
                    'AND (id_turno_caja IS NULL OR id_medio_pago IS NOT NULL)'),
                name='ck_movcuenta_medio'),
            models.UniqueConstraint(
                fields=['id_venta'], condition=models.Q(tipo_movimiento='FIADO'),
                name='uq_movcuenta_fiado_venta'),
        ]

    def __str__(self):
        return f'{self.get_tipo_movimiento_display()} · {self.id_cliente} · {self.monto}'
