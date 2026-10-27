"""Datos maestros del sistema — proceso N° 10 del DFD.

Son los catálogos que usan todos los demás procesos: rubros, productos,
tipos de egreso, medios de pago, cajas registradoras, categorías de
fidelización y beneficios (con su receta en especie).

Reglas que comparten todos:

  * BAJA LÓGICA (RN-8.1.1). Nada se borra: ``borrado_logico`` pasa a True
    y la fila queda. Los comprobantes históricos que apuntan a un dato
    dado de baja se siguen leyendo igual, y la baja se puede deshacer.
  * NOMBRE ÚNICO POR CLAVE NORMALIZADA (DD-50): "Café", "CAFE" y "cafe "
    son el mismo rubro. Lo garantiza un índice único por expresión sobre
    ``f_clave_normalizada()``.
  * SELLO DE MODIFICACIÓN (DD-17): ``fecha_modificacion`` y
    ``usuario_modificacion`` los escribe el ``save()`` del modelo, vía el
    mixin ``ConSelloDeModificacion``, leyendo del contexto del pedido el
    usuario que dejó el middleware.

Toda la lógica transaccional y de validación vive en Django: en el
``clean()`` de cada modelo (capa 2 de la sección 4.4.V), en los mixins de
``cafeto/reglas.py`` y en los servicios de ``operaciones/``. Cada modelo
trae además un ``motivos_que_impiden_la_baja()`` que explica, en
castellano y con números, por qué no se puede dar de baja todavía.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from cafeto.reglas import (
    ConSaldosDerivados,
    ConSelloDeModificacion,
    ManagerSinBajaFisica,
    SinBajaFisica,
)
from cafeto.motivos import MotivoCompleto
from cafeto.restricciones import CheckEnBase, CheckSQL, ClaveNormalizada
from cafeto.validaciones import (
    NO_NEGATIVO,
    clave_normalizada,
    exigir_nombre_unico,
    texto_de_catalogo,
    validar_codigo_barras,
    validar_decimal,
    validar_entero,
    validar_orden_de_fechas,
)
from usuarios.models import sello_fecha_modificacion, sello_usuario_modificacion


class Maestra(ConSelloDeModificacion, SinBajaFisica, models.Model):
    """Lo que tienen en común las tablas de catálogo con baja lógica.

    Los dos mixins aportan una invariante cada uno:

    * ``ConSelloDeModificacion`` anota cuándo y quién tocó la fila (DD-17).
    * ``SinBajaFisica``: acá la baja es ``borrado_logico``, el DELETE no
      corre (RN-8.1.1).
    """

    borrado_logico = models.BooleanField('Borrado lógico', default=False)
    fecha_modificacion = sello_fecha_modificacion()

    # El manager también corta el DELETE: QuerySet.delete() borra de un
    # saque y NO pasa por el delete() de cada instancia.
    objects = ManagerSinBajaFisica()

    class Meta:
        abstract = True

    @property
    def vigente(self):
        return not self.borrado_logico

    def motivos_que_impiden_la_baja(self):
        """Dependientes vigentes que obligan a resolver algo antes (V-G10)."""
        return []

    def motivos_que_impiden_reactivar(self):
        """Padres dados de baja que impedirían reactivar este registro."""
        return []


# ===========================================================================
#  RUBROS
# ===========================================================================
class Rubro(Maestra):
    """Agrupación comercial de los productos (Cafetería, Minisuper...)."""

    id_rubro = models.AutoField('ID', primary_key=True)
    nombre_rubro = models.CharField('Rubro', max_length=40)
    descripcion_rubro = models.CharField(
        'Descripción', max_length=120, null=True, blank=True)
    # 'activo' es distinto de 'borrado_logico': un rubro inactivo no se
    # ofrece al dar de alta un producto, pero sus productos siguen vigentes.
    activo = models.BooleanField('Se ofrece en el alta de productos', default=True)
    usuario_modificacion = sello_usuario_modificacion('rubros_modificados')

    class Meta:
        db_table = 'rubros'
        verbose_name = 'Rubro'
        verbose_name_plural = 'Rubros'
        ordering = ['nombre_rubro']
        constraints = [
            CheckEnBase(
                check=CheckSQL("trim(nombre_rubro) <> ''"),
                name='ck_rubros_textos'),
            models.UniqueConstraint(
                ClaveNormalizada('nombre_rubro'),
                name='uq_rubros_nombre_norm'),
        ]

    def __str__(self):
        return self.nombre_rubro

    @property
    def clave(self):
        return clave_normalizada(self.nombre_rubro)

    def clean(self):
        super().clean()
        self.nombre_rubro = texto_de_catalogo(
            self.nombre_rubro, 2, 40, 'El nombre del rubro')
        self.descripcion_rubro = texto_de_catalogo(
            self.descripcion_rubro, 1, 120, 'La descripción', obligatorio=False)

        exigir_nombre_unico(self, 'nombre_rubro', etiqueta='un rubro')

    def motivos_que_impiden_la_baja(self):
        vigentes = Producto.objects.filter(
            id_rubro=self, borrado_logico=False).count()
        if vigentes:
            return [f'tiene {vigentes} producto(s) vigente(s); dalos de baja '
                    'o cambialos de rubro primero']
        return []


# ===========================================================================
#  MARCAS
# ===========================================================================
class Marca(Maestra):
    """Marca comercial bajo la que se vende un producto (DD-55).

    Por qué es una tabla y no un texto adentro del nombre del producto: la
    marca es un dato de LA MARCA, no del producto. Si vive embebida en
    "Café molido La Morenita 500 g" no se puede filtrar por marca, no se
    puede corregir el nombre de una marca en un solo lugar, y sobre todo
    no se puede detectar que "Café molido La morenita" y "Café molido La
    Morenita 500 g" son el mismo artículo cargado dos veces.

    Un producto tiene UNA marca y una marca tiene MUCHOS productos: la
    relación es 1:N. No hace falta tabla intermedia, y no conviene: un
    producto con dos marcas no existe en este negocio, y admitirlo
    impediría exigir que la terna marca + nombre + presentación sea única.
    """

    id_marca = models.AutoField('ID', primary_key=True)
    nombre_marca = models.CharField('Marca', max_length=50)
    descripcion_marca = models.CharField(
        'Descripción', max_length=120, null=True, blank=True)
    usuario_modificacion = sello_usuario_modificacion('marcas_modificadas')

    class Meta:
        db_table = 'marcas'
        verbose_name = 'Marca'
        verbose_name_plural = 'Marcas'
        ordering = ['nombre_marca']
        constraints = [
            CheckEnBase(
                check=CheckSQL("trim(nombre_marca) <> ''"),
                name='ck_marcas_textos'),
            models.UniqueConstraint(
                ClaveNormalizada('nombre_marca'),
                name='uq_marcas_nombre_norm'),
        ]

    def __str__(self):
        return self.nombre_marca

    @property
    def clave(self):
        return clave_normalizada(self.nombre_marca)

    def clean(self):
        super().clean()
        self.nombre_marca = texto_de_catalogo(
            self.nombre_marca, 2, 50, 'El nombre de la marca')
        self.descripcion_marca = texto_de_catalogo(
            self.descripcion_marca, 1, 120, 'La descripción', obligatorio=False)

        # "Coca Cola", "coca cola" y "Coca-cola" son la misma marca. Se
        # corta acá para que el aviso salga en el campo y no como un
        # error 500 del índice único.
        exigir_nombre_unico(self, 'nombre_marca', etiqueta='una marca')

    def motivos_que_impiden_la_baja(self):
        vigentes = Producto.objects.filter(
            id_marca=self, borrado_logico=False).count()
        if vigentes:
            return [f'tiene {vigentes} producto(s) vigente(s); dalos de baja '
                    'o cambialos de marca primero']
        return []


# ===========================================================================
#  PRODUCTOS
# ===========================================================================
class UnidadMedida(models.TextChoices):
    """Cómo se mide el producto. Valores en MAYÚSCULAS (DD-29)."""
    UNIDAD = 'UNIDAD', 'Unidad'
    KG = 'KG', 'Kilogramo'
    G = 'G', 'Gramo'
    L = 'L', 'Litro'
    ML = 'ML', 'Mililitro'


class Producto(ConSaldosDerivados, Maestra):
    """Artículo de cafetería o de minisuper.

    ``stock_actual`` NO se edita: lo mueven los movimientos de stock
    (RN-5.1.1) y lo protege el mixin ``ConSaldosDerivados``.
    ``precio_costo`` se carga una vez en el alta y después lo actualiza la
    compra (RN-4.2.4).
    """

    # Lo mueve el libro de movimientos de stock, nunca una pantalla
    # (lo hace cumplir ConSaldosDerivados, en cafeto/reglas.py).
    SALDOS_DERIVADOS = {'stock_actual': 0}

    id_producto = models.AutoField('ID', primary_key=True)
    id_rubro = models.ForeignKey(
        Rubro, on_delete=models.RESTRICT, db_column='id_rubro',
        verbose_name='Rubro', related_name='productos')
    id_proveedor = models.ForeignKey(
        'proveedores.Proveedor', on_delete=models.RESTRICT,
        db_column='id_proveedor',
        verbose_name='Proveedor', related_name='productos')
    id_marca = models.ForeignKey(
        Marca, on_delete=models.RESTRICT, db_column='id_marca',
        verbose_name='Marca', related_name='productos')
    nombre_producto = models.CharField(
        'Nombre del producto', max_length=50,
        help_text='Sin la marca ni el contenido: van en sus propios campos.')
    contenido = models.DecimalField(
        'Contenido', max_digits=10, decimal_places=3, default=Decimal('1'),
        validators=[NO_NEGATIVO])
    unidad_medida = models.CharField(
        'Unidad de medida', max_length=10,
        choices=UnidadMedida.choices, default=UnidadMedida.UNIDAD)
    precio_venta = models.DecimalField(
        'Precio de venta', max_digits=12, decimal_places=2,
        validators=[NO_NEGATIVO])
    precio_costo = models.DecimalField(
        'Precio de costo', max_digits=12, decimal_places=2, default=Decimal('0'),
        validators=[NO_NEGATIVO])
    stock_actual = models.IntegerField('Stock actual', default=0, editable=False)
    stock_minimo = models.IntegerField('Stock mínimo', default=0)
    codigo_barras = models.CharField(
        'Código de barras', max_length=20, null=True, blank=True)
    controla_vencimiento = models.BooleanField(
        'Controla vencimiento por lote', default=False)
    usuario_modificacion = sello_usuario_modificacion('productos_modificados')

    class Meta:
        db_table = 'productos'
        verbose_name = 'Producto'
        verbose_name_plural = 'Productos'
        ordering = ['nombre_producto']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'precio_venta >= 0 AND precio_costo >= 0 '
                    'AND stock_minimo >= 0'),
                name='ck_productos_valores'),
            CheckEnBase(
                check=CheckSQL('stock_actual >= 0'),
                name='ck_productos_stock'),
            CheckEnBase(
                check=CheckSQL(
                    "unidad_medida IN ('UNIDAD','KG','G','L','ML')"),
                name='ck_productos_unidad'),
            CheckEnBase(
                check=CheckSQL(
                    'codigo_barras IS NULL OR '
                    "codigo_barras ~ '^([0-9]{8}|[0-9]{12,14})$'"),
                name='ck_productos_codigo_barras'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(nombre_producto) <> '' AND trim(unidad_medida) <> ''"),
                name='ck_productos_textos'),
            CheckEnBase(
                check=CheckSQL('contenido > 0'),
                name='ck_productos_contenido'),
            models.UniqueConstraint(
                fields=['codigo_barras'], name='uq_productos_codigo_barras'),
            # Lo que identifica al producto es la TERNA marca + nombre +
            # presentación, no un nombre largo escrito a mano. Con el nombre
            # solo, "Café molido La morenita" y "Café molido La Morenita
            # 500 g" pasaban como dos artículos distintos (DD-55).
            models.UniqueConstraint(
                'id_marca', ClaveNormalizada('nombre_producto'), 'contenido',
                ClaveNormalizada('unidad_medida'),
                name='uq_productos_identidad'),
        ]

    def __str__(self):
        return self.descripcion_completa

    @property
    def descripcion_completa(self):
        """Marca · nombre · presentación, que es como se nombra en el mostrador.

        Ej.: "La Morenita · Café molido · 500 G". Es lo que antes se
        escribía a mano en un solo campo; ahora se arma de las tres
        columnas, así que no puede quedar desprolijo ni a medias.
        """
        marca = self.id_marca.nombre_marca if self.id_marca_id else ''
        partes = [marca, self.nombre_producto, self.presentacion]
        return ' · '.join(p for p in partes if p)

    @property
    def presentacion(self):
        """El contenido sin ceros de más: 500 G, 1,5 L, 1 UNIDAD."""
        if self.contenido is None:
            return ''
        texto = format(self.contenido.normalize(), 'f')
        # Sólo se recortan los ceros de la PARTE DECIMAL. Un rstrip a secas
        # convertía "500" en "5", que es un producto distinto.
        if '.' in texto:
            texto = texto.rstrip('0').rstrip('.')
        return '%s %s' % (texto.replace('.', ','), self.unidad_medida)

    @property
    def clave(self):
        return clave_normalizada(self.nombre_producto)

    def clean(self):
        super().clean()
        self.nombre_producto = texto_de_catalogo(
            self.nombre_producto, 2, 50, 'El nombre del producto')
        self.codigo_barras = validar_codigo_barras(self.codigo_barras)

        if self.unidad_medida not in UnidadMedida.values:
            raise ValidationError(
                {'unidad_medida': 'Elegí una unidad de medida de la lista.'})

        self.precio_venta = validar_decimal(
            self.precio_venta, minimo=Decimal('0'),
            maximo=Decimal('9999999999.99'), decimales=2,
            etiqueta='El precio de venta', incluye_minimo=False)
        self.precio_costo = validar_decimal(
            self.precio_costo if self.precio_costo is not None else Decimal('0'),
            minimo=Decimal('0'), maximo=Decimal('9999999999.99'), decimales=2,
            etiqueta='El precio de costo')
        self.stock_minimo = validar_entero(
            self.stock_minimo, minimo=0, maximo=99999,
            etiqueta='El stock mínimo')
        self.contenido = validar_decimal(
            self.contenido, minimo=Decimal('0'), maximo=Decimal('9999999'),
            decimales=3, etiqueta='El contenido', incluye_minimo=False)

        # La identidad de un producto es la TERNA marca + presentación
        # (DD-55), no el nombre solo: "Café" de La Morenita y "Café" de
        # otra marca son dos artículos distintos, y 500 g y 1 kg también.
        # Lo que NO puede repetirse es la terna completa, y sin que las
        # mayúsculas, los acentos ni la puntuación la disfracen.
        exigir_nombre_unico(
            self, 'nombre_producto',
            ambito=('id_marca', 'contenido', 'unidad_medida'),
            etiqueta='un producto de esa marca y presentación')

        self._validar_cambio_de_control_de_vencimiento()

    def _validar_cambio_de_control_de_vencimiento(self):
        """No se puede cambiar el control de vencimiento con stock de por medio.

        De SÍ a NO con lotes cargados dejaría lotes huérfanos; de NO a SÍ
        con stock cargado dejaría stock sin lote al que imputarlo. En los
        dos casos el inventario quedaría sin poder cuadrar.
        """
        if self._state.adding:
            return
        anterior = type(self).objects.filter(pk=self.pk).values(
            'controla_vencimiento', 'stock_actual').first()
        if anterior is None or anterior['controla_vencimiento'] == self.controla_vencimiento:
            return

        from operaciones.models import Lote

        if anterior['controla_vencimiento'] and not self.controla_vencimiento:
            con_stock = Lote.objects.filter(
                id_producto=self, stock_lote__gt=0).count()
            if con_stock:
                raise ValidationError({'controla_vencimiento': (
                    f'No se puede dejar de controlar el vencimiento: hay '
                    f'{con_stock} lote(s) con stock. Agotalos o ajustalos a 0 '
                    'primero.')})
        elif not anterior['controla_vencimiento'] and self.controla_vencimiento:
            if anterior['stock_actual'] > 0:
                raise ValidationError({'controla_vencimiento': (
                    f'No se puede empezar a controlar el vencimiento con '
                    f'{anterior["stock_actual"]} unidad(es) en stock: ese stock '
                    'quedaría sin lote. Ajustalo a 0 primero.')})

    def motivos_que_impiden_la_baja(self):
        from operaciones.models import Lote

        motivos = []
        if self.stock_actual > 0:
            motivos.append(
                f'tiene {self.stock_actual} unidad(es) en stock; registrá la '
                'merma o el ajuste a 0 antes de darlo de baja')
        recetas = BeneficioProducto.objects.filter(
            id_producto=self, id_beneficio__borrado_logico=False).count()
        if recetas:
            motivos.append(
                f'forma parte de {recetas} beneficio(s) en especie vigente(s)')
        lotes = Lote.objects.filter(id_producto=self, stock_lote__gt=0).count()
        if lotes:
            motivos.append(f'tiene {lotes} lote(s) con stock sin agotar')
        return motivos

    def motivos_que_impiden_reactivar(self):
        motivos = []
        if self.id_rubro.borrado_logico:
            motivos.append(
                f'su rubro "{self.id_rubro}" está dado de baja: reactivalo primero')
        if self.id_proveedor.borrado_logico:
            motivos.append(
                f'su proveedor "{self.id_proveedor}" está dado de baja: '
                'reactivalo primero')
        if self.id_marca.borrado_logico:
            motivos.append(
                f'su marca "{self.id_marca}" está dada de baja: '
                'reactivala primero')
        return motivos


# ===========================================================================
#  TIPOS DE EGRESO
# ===========================================================================
class TipoEgreso(Maestra):
    """Clasificación de los egresos (Proveedores, Servicios, Impuestos...)."""

    id_tipo_egreso = models.AutoField('ID', primary_key=True)
    nombre_tipo_egreso = models.CharField('Tipo de egreso', max_length=30)
    descripcion_tipo_egreso = models.CharField(
        'Descripción', max_length=120, null=True, blank=True)
    usuario_modificacion = sello_usuario_modificacion('tipos_egreso_modificados')

    class Meta:
        db_table = 'tipos_egreso'
        verbose_name = 'Tipo de egreso'
        verbose_name_plural = 'Tipos de egreso'
        ordering = ['nombre_tipo_egreso']
        constraints = [
            CheckEnBase(
                check=CheckSQL("trim(nombre_tipo_egreso) <> ''"),
                name='ck_tipos_egreso_textos'),
            models.UniqueConstraint(
                ClaveNormalizada('nombre_tipo_egreso'),
                name='uq_tipos_egreso_nombre_norm'),
        ]

    def __str__(self):
        return self.nombre_tipo_egreso

    @property
    def clave(self):
        return clave_normalizada(self.nombre_tipo_egreso)

    def clean(self):
        super().clean()
        self.nombre_tipo_egreso = texto_de_catalogo(
            self.nombre_tipo_egreso, 3, 30, 'El nombre del tipo de egreso')
        exigir_nombre_unico(self, 'nombre_tipo_egreso',
                            etiqueta='un tipo de egreso')
        self.descripcion_tipo_egreso = texto_de_catalogo(
            self.descripcion_tipo_egreso, 1, 120, 'La descripción',
            obligatorio=False)

    def motivos_que_impiden_la_baja(self):
        from operaciones.models import Egreso

        pendientes = Egreso.objects.filter(
            id_tipo_egreso=self,
            estado_pago__in=('PENDIENTE_PAGO', 'PENDIENTE_ACREDITACION'),
        ).count()
        if pendientes:
            return [f'tiene {pendientes} egreso(s) sin acreditar '
                    '(pendientes de pago o de acreditación)']
        return []


# ===========================================================================
#  MEDIOS DE PAGO
# ===========================================================================
class MedioPago(Maestra):
    """Forma de cobro. "Efectivo" es el único que mueve el cajón (DD-03)."""

    # El dato inicial protegido: su recargo es siempre 0 y no se da de baja,
    # porque el vuelto y el arqueo se calculan sobre el importe en efectivo.
    NOMBRE_PROTEGIDO = 'Efectivo'

    id_medio_pago = models.AutoField('ID', primary_key=True)
    nombre_medio_pago = models.CharField('Medio de pago', max_length=25)
    recargo_porcentaje = models.DecimalField(
        'Recargo (%)', max_digits=5, decimal_places=2, default=Decimal('0'),
        validators=[NO_NEGATIVO])
    usuario_modificacion = sello_usuario_modificacion('medios_pago_modificados')

    class Meta:
        db_table = 'medios_pago'
        verbose_name = 'Medio de pago'
        verbose_name_plural = 'Medios de pago'
        ordering = ['nombre_medio_pago']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'recargo_porcentaje >= 0 AND recargo_porcentaje <= 100'),
                name='ck_medios_pago_recargo'),
            CheckEnBase(
                check=CheckSQL("trim(nombre_medio_pago) <> ''"),
                name='ck_medios_pago_textos'),
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

    def clean(self):
        super().clean()
        self.nombre_medio_pago = texto_de_catalogo(
            self.nombre_medio_pago, 3, 25, 'El nombre del medio de pago')
        exigir_nombre_unico(self, 'nombre_medio_pago',
                            etiqueta='un medio de pago')
        self.recargo_porcentaje = validar_decimal(
            self.recargo_porcentaje, minimo=Decimal('0'),
            maximo=Decimal('100'), decimales=2, etiqueta='El recargo')

        if self.es_efectivo and self.recargo_porcentaje != Decimal('0'):
            raise ValidationError({'recargo_porcentaje': (
                'El efectivo no puede tener recargo: el vuelto y el arqueo '
                'de caja se calculan sobre el importe cobrado.')})

    def motivos_que_impiden_la_baja(self):
        motivos = []
        if self.es_efectivo:
            # Mensaje COMPLETO y no fragmento: el efectivo no se bloquea por
            # tener dependientes —se bloquea por lo que ES—, así que la frase
            # no encaja detrás del "No se puede dar de baja X porque..." que
            # arma el CRUD para los demás casos.
            motivos.append(MotivoCompleto(
                "El medio de pago 'Efectivo' es el método por defecto del "
                'sistema y no puede ser deshabilitado.'))
        quedan = type(self).objects.filter(borrado_logico=False).exclude(
            pk=self.pk).count()
        if not quedan:
            motivos.append('es el último medio de pago vigente')
        return motivos


# ===========================================================================
#  CAJAS REGISTRADORAS
# ===========================================================================
class CajaRegistradora(Maestra):
    """Mueble de cobro. Una caja registradora no puede tener dos turnos abiertos (DD-42)."""

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
        from operaciones.models import TurnoCaja

        motivos = []
        if TurnoCaja.objects.filter(id_caja_registradora=self, estado_turno='ABIERTA').exists():
            motivos.append('tiene un turno de caja ABIERTO; cerralo primero')
        quedan = type(self).objects.filter(borrado_logico=False).exclude(
            pk=self.pk).count()
        if not quedan:
            motivos.append('es la última caja registradora vigente')
        return motivos


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
            self.nombre_categoria, 3, 15, 'El nombre de la categoría')
        exigir_nombre_unico(self, 'nombre_categoria',
                            etiqueta='una categoría')
        self.puntos_minimos = validar_entero(
            self.puntos_minimos, minimo=0, maximo=10_000_000,
            etiqueta='Los puntos mínimos')
        self.importe_por_punto = validar_decimal(
            self.importe_por_punto, minimo=Decimal('0'),
            maximo=Decimal('9999999999.99'), decimales=2,
            etiqueta='El importe por punto', incluye_minimo=False)

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
        from operaciones.models import Cliente

        motivos = []
        if self.es_de_ingreso:
            motivos.append(
                'es la categoría de ingreso (umbral 0): todo cliente nuevo '
                'entra por ella')
        clientes = Cliente.objects.filter(
            id_categoria=self, borrado_logico=False).count()
        if clientes:
            motivos.append(f'tiene {clientes} cliente(s) vigente(s)')
        beneficios = Beneficio.objects.filter(
            id_categoria=self, borrado_logico=False).count()
        if beneficios:
            motivos.append(f'tiene {beneficios} beneficio(s) vigente(s)')
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
        Producto, through='BeneficioProducto', related_name='beneficios',
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
        from operaciones.models import DetalleVenta, MovimientoPuntos, Venta

        return (
            Venta.objects.filter(id_beneficio=self).exists()
            or DetalleVenta.objects.filter(id_beneficio=self).exists()
            or MovimientoPuntos.objects.filter(id_beneficio=self).exists()
        )

    def clean(self):
        super().clean()
        self.descripcion_beneficio = texto_de_catalogo(
            self.descripcion_beneficio, 5, 120, 'La descripción del beneficio')

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
        Producto, on_delete=models.RESTRICT, db_column='id_producto',
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

    def clean(self):
        super().clean()
        self.cantidad = validar_entero(
            self.cantidad, minimo=1, maximo=99, etiqueta='La cantidad')
        if self.id_producto_id and self.id_producto.borrado_logico:
            raise ValidationError({'id_producto': (
                f'El producto "{self.id_producto}" está dado de baja: elegí '
                'uno vigente.')})
