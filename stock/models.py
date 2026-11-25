"""Módulo Stock e Inventario.

Diez tablas:

    RUBROS                  agrupación comercial (Cafetería, Minisuper)
    MARCAS                  la marca bajo la que se vende un producto
    PRODUCTOS               todo lo que se compra, se elabora o se vende
    COMPOSICION_PRODUCTO    la receta de un elaborado/producido y el
                            contenido de un combo
    LOTES                   partidas con vencimiento
    MOVIMIENTOS_STOCK       el libro de stock (sólo agregado)
    AJUSTES_STOCK           conteos, mermas y traspasos, con autorización
    DETALLES_AJUSTE         sus renglones
    PRODUCCIONES            las tandas de lo que se fabrica en el local
    SUGERENCIAS_CATALOGO    las mejoras que una automatización propone y
                            que aprueba (o descarta) una persona

Los cinco TIPOS de producto y qué admite cada uno
-------------------------------------------------

=============  =========  ======  =====  ===========  ===========  ==========
tipo           proveedor  precio  stock  vencimiento  fraccionable composición
=============  =========  ======  =====  ===========  ===========  ==========
REVENTA        sí         > 0     sí     opcional     opcional     no
INSUMO         sí         = 0     sí(*)  opcional     opcional     no
ELABORADO      no         > 0     no     no           no           insumos
PRODUCIDO      no         > 0     sí     opcional     no           insumos
COMBO          no         > 0     no     no           no           vendibles
=============  =========  ======  =====  ===========  ===========  ==========

(*) Un INSUMO puede quedar en negativo: el café que se sirve se descuenta
por receta, y si el conteo del depósito todavía no se cargó el sistema no
puede frenar la venta de un cortado. El negativo queda a la vista y lo
corrige el próximo conteo. Lo que se REVENDE o se PRODUCE, en cambio,
nunca baja de cero: no se puede vender lo que no está.

``stock_actual`` y ``stock_lote`` NO se escriben a mano: los mueve el
libro de movimientos (``MovimientoStock.save()``), en la misma transacción
que el asiento.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from nucleo import contexto
from nucleo.modelos import (
    Maestra,
    campo_cantidad,
    campo_importe,
    es_entero,
    sello_usuario_modificacion,
)
from nucleo.reglas import (
    ConSaldosDerivados,
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
    validar_codigo_barras,
    validar_decimal,
    validar_entero,
)
from nucleo.textos import pluralizar, segun

CERO = Decimal('0')


def _cantidad(valor):
    """3,000 -> '3'; 0,350 -> '0,35': la cantidad como se lee en pantalla."""
    texto = format(Decimal(valor).normalize(), 'f')
    if '.' in texto:
        texto = texto.rstrip('0').rstrip('.')
    return texto.replace('.', ',')


# ===========================================================================
#  REBAJA POR VENCIMIENTO (va en el rubro y, si hace falta, en el producto)
# ===========================================================================
def _validar_rebaja(instancia):
    """Días y porcentaje van juntos: los dos o ninguno."""
    dias = instancia.dias_rebaja_vencimiento
    porcentaje = instancia.porcentaje_rebaja_vencimiento
    if (dias is None) != (porcentaje is None):
        campo = ('porcentaje_rebaja_vencimiento' if porcentaje is None
                 else 'dias_rebaja_vencimiento')
        raise ValidationError({campo: (
            'La rebaja por vencimiento lleva los dos datos: cuántos días '
            'antes de vencer empieza y qué porcentaje descuenta.')})
    if dias is not None:
        validar_entero(dias, 1, 365, 'Los días de anticipación')
        validar_decimal(porcentaje, Decimal('0'), Decimal('99'), 2,
                        'El porcentaje de rebaja', incluye_minimo=False)


CHECK_REBAJA = (
    '(dias_rebaja_vencimiento IS NULL) = (porcentaje_rebaja_vencimiento IS NULL)'
    ' AND (dias_rebaja_vencimiento IS NULL OR dias_rebaja_vencimiento '
    'BETWEEN 1 AND 365)'
    ' AND (porcentaje_rebaja_vencimiento IS NULL OR '
    '(porcentaje_rebaja_vencimiento > 0 AND porcentaje_rebaja_vencimiento < 100))')


# ===========================================================================
#  RUBROS
# ===========================================================================
#  Registros reservados (DD-86). Un producto necesita marca, y lo que se
#  compra necesita proveedor; cuando todavía no se saben, van éstos. Los crea
#  la migración y no se pueden renombrar ni dar de baja: el alta simple y el
#  importador cuentan con ellos.
MARCA_GENERICA = 'Genérica'


class Rubro(Maestra):
    """Agrupación comercial de los productos (Cafetería, Minisuper...).

    La rebaja por vencimiento se define acá para todo el rubro (ej.: en
    Minisuper, 20 % desde 5 días antes de vencer) y cada producto puede
    tener la suya propia, que manda sobre la del rubro.
    """

    id_rubro = models.AutoField('ID', primary_key=True)
    nombre_rubro = models.CharField('Rubro', max_length=40)
    descripcion_rubro = models.CharField(
        'Descripción', max_length=120, null=True, blank=True)
    # 'activo' es distinto de 'borrado_logico': un rubro inactivo no se
    # ofrece al dar de alta un producto, pero sus productos siguen vigentes.
    activo = models.BooleanField('Se ofrece en el alta de productos', default=True)
    dias_rebaja_vencimiento = models.PositiveSmallIntegerField(
        'Rebaja: días antes de vencer', null=True, blank=True)
    porcentaje_rebaja_vencimiento = models.DecimalField(
        'Rebaja: porcentaje', max_digits=5, decimal_places=2,
        null=True, blank=True)
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
            CheckEnBase(check=CheckSQL(CHECK_REBAJA), name='ck_rubros_rebaja'),
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
            self.nombre_rubro, 2, 40, 'El nombre del rubro', exige_letra=True)
        self.descripcion_rubro = texto_de_catalogo(
            self.descripcion_rubro, 1, 120, 'La descripción', obligatorio=False)
        exigir_nombre_unico(self, 'nombre_rubro', etiqueta='un rubro')
        _validar_rebaja(self)

    def motivos_que_impiden_la_baja(self):
        vigentes = Producto.objects.filter(
            id_rubro=self, borrado_logico=False).count()
        if vigentes:
            return [f"tiene {pluralizar(vigentes, 'producto vigente', 'productos vigentes')}; "
                    f"{segun(vigentes, 'dalo de baja o cambialo', 'dalos de baja o cambialos')} de rubro primero"]
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
            self.nombre_marca, 2, 50, 'El nombre de la marca', exige_letra=True)
        if self.pk and self.nombre_marca != MARCA_GENERICA and type(self).objects.filter(
                pk=self.pk, nombre_marca=MARCA_GENERICA).exists():
            raise ValidationError({'nombre_marca': (
                f'"{MARCA_GENERICA}" es la marca reservada para los productos '
                'que todavía no tienen la suya: no se le cambia el nombre.')})
        self.descripcion_marca = texto_de_catalogo(
            self.descripcion_marca, 1, 120, 'La descripción', obligatorio=False)
        # "Coca Cola", "coca cola" y "Coca-cola" son la misma marca. Se
        # corta acá para que el aviso salga en el campo y no como un
        # error 500 del índice único.
        exigir_nombre_unico(self, 'nombre_marca', etiqueta='una marca')

    def motivos_que_impiden_la_baja(self):
        if self.nombre_marca == MARCA_GENERICA:
            return ['es la marca reservada para los productos que todavía no '
                    'tienen la suya']
        vigentes = Producto.objects.filter(
            id_marca=self, borrado_logico=False).count()
        if vigentes:
            return [f"tiene {pluralizar(vigentes, 'producto vigente', 'productos vigentes')}; "
                    f"{segun(vigentes, 'dalo de baja o cambialo', 'dalos de baja o cambialos')} de marca primero"]
        return []


# ===========================================================================
#  PRODUCTOS
# ===========================================================================
#  Las alícuotas de IVA que usa un comercio minorista (Ley 23.349, art. 28):
#  la general, la reducida y lo exento o no gravado.
ALICUOTAS_IVA = [
    (Decimal('0.0'), '0 % (exento o no gravado)'),
    (Decimal('10.5'), '10,5 %'),
    (Decimal('21.0'), '21 %'),
]


class UnidadMedida(models.TextChoices):
    """Cómo se mide el producto. Valores en MAYÚSCULAS (DD-29)."""
    UNIDAD = 'UNIDAD', 'Unidad'
    KG = 'KG', 'Kilogramo'
    G = 'G', 'Gramo'
    L = 'L', 'Litro'
    ML = 'ML', 'Mililitro'


class TipoProducto(models.TextChoices):
    REVENTA = 'REVENTA', 'Reventa (se compra y se vende igual)'
    INSUMO = 'INSUMO', 'Insumo (se usa para preparar, no se vende)'
    ELABORADO = 'ELABORADO', 'Elaborado al momento (café, licuado)'
    PRODUCIDO = 'PRODUCIDO', 'Producido por tanda (empanadas, pizzas)'
    COMBO = 'COMBO', 'Combo (varios productos a un precio)'


# Lo que la matriz de la cabecera del módulo dice, en conjuntos.
TIPOS_CON_PROVEEDOR = frozenset({TipoProducto.REVENTA, TipoProducto.INSUMO})
TIPOS_CON_STOCK = frozenset({TipoProducto.REVENTA, TipoProducto.INSUMO,
                             TipoProducto.PRODUCIDO})
TIPOS_VENDIBLES = frozenset({TipoProducto.REVENTA, TipoProducto.ELABORADO,
                             TipoProducto.PRODUCIDO, TipoProducto.COMBO})
TIPOS_CON_RECETA = frozenset({TipoProducto.ELABORADO, TipoProducto.PRODUCIDO})
TIPOS_COMPUESTOS = TIPOS_CON_RECETA | {TipoProducto.COMBO}
#  De los compuestos, cuáles NO se pueden vender sin su composición cargada.
#
#  Un combo es su contenido: sin renglones no es nada, y un producido se
#  fabrica por tanda descontando insumos, así que tampoco.
#
#  Un ELABORADO es distinto: el café, el licuado o la promo se preparan al
#  momento y lo que consumen puede no estar cargado todavía (los 85
#  artículos de «Comidas Elaboradas», «CAFETERIA » y «PROMO » que entran de
#  la planilla de Fácil Virtual vienen sin receta). Se venden igual —con el
#  precio y el costo que trae la planilla— y, cuando la receta se carga,
#  recién entonces descuentan insumos. Bloquearlos dejaba el mostrador sin
#  poder cobrar un café.
TIPOS_CON_COMPOSICION_OBLIGATORIA = frozenset({TipoProducto.PRODUCIDO,
                                               TipoProducto.COMBO})
# Qué puede llevar adentro cada tipo compuesto.
#
# Una RECETA (elaborado o producido) se arma sólo con INSUMOS: la harina, la
# muzzarella, el café en grano. Un producto de reventa (el yogur, la gaseosa)
# se vende tal cual, no se «cocina»: meterlo en una receta descontaría
# mercadería del mostrador y falsearía el costo del plato (STK-01).
COMPONENTES_ADMITIDOS = {
    TipoProducto.ELABORADO: frozenset({TipoProducto.INSUMO}),
    TipoProducto.PRODUCIDO: frozenset({TipoProducto.INSUMO}),
    TipoProducto.COMBO: frozenset({TipoProducto.REVENTA,
                                   TipoProducto.ELABORADO,
                                   TipoProducto.PRODUCIDO}),
}


def _en(valores):
    return '(%s)' % ', '.join(f"'{v}'" for v in sorted(valores))


class Producto(ConSaldosDerivados, Maestra):
    """Todo lo que se compra, se elabora, se produce o se vende.

    ``stock_actual`` NO se edita: lo mueven los movimientos de stock
    (RN-5.1.1) y lo protege el mixin ``ConSaldosDerivados``.
    ``precio_costo`` se carga en el alta y después lo actualiza la compra
    (RN-4.2.4) o la producción. Un ELABORADO o un COMBO no tiene costo
    propio: es la suma de su composición (``costo_vigente``).
    """

    SALDOS_DERIVADOS = {'stock_actual': Decimal('0')}

    id_producto = models.AutoField('ID', primary_key=True)
    tipo_producto = models.CharField(
        'Tipo de producto', max_length=10, choices=TipoProducto.choices,
        default=TipoProducto.REVENTA)
    id_rubro = models.ForeignKey(
        Rubro, on_delete=models.RESTRICT, db_column='id_rubro',
        verbose_name='Rubro', related_name='productos')
    id_proveedor = models.ForeignKey(
        'compras.Proveedor', on_delete=models.RESTRICT,
        db_column='id_proveedor', null=True, blank=True,
        verbose_name='Proveedor', related_name='productos',
        help_text='Sólo para lo que se compra (reventa e insumos).')
    id_marca = models.ForeignKey(
        Marca, on_delete=models.RESTRICT, db_column='id_marca',
        verbose_name='Marca', related_name='productos')
    nombre_producto = models.CharField(
        'Nombre del producto', max_length=100,
        help_text='Sin la marca ni el contenido: van en sus propios campos.')
    contenido = models.DecimalField(
        'Contenido', max_digits=10, decimal_places=3, default=Decimal('1'),
        validators=[NO_NEGATIVO])
    unidad_medida = models.CharField(
        'Unidad de medida', max_length=10,
        choices=UnidadMedida.choices, default=UnidadMedida.UNIDAD)
    fraccionable = models.BooleanField(
        'Se vende por peso o volumen', default=False,
        help_text='Queso, fiambre, café a granel: el precio es por kilo o '
                  'por litro y se vende cualquier cantidad (0,350 kg).')
    precio_venta = campo_importe(
        'Precio de venta', default=Decimal('0'), validators=[NO_NEGATIVO])
    precio_costo = campo_importe(
        'Precio de costo', default=Decimal('0'), validators=[NO_NEGATIVO])
    alicuota_iva = models.DecimalField(
        '% IVA', max_digits=4, decimal_places=1, default=Decimal('0.0'),
        choices=ALICUOTAS_IVA,
        help_text='Qué IVA lleva el producto. No cambia el precio de venta: '
                  'sirve para calcularlo y para los informes.')
    stock_actual = campo_cantidad('Stock actual', default=Decimal('0'),
                                  editable=False)
    stock_minimo = campo_cantidad('Stock mínimo', default=Decimal('0'),
                                  validators=[NO_NEGATIVO])
    codigo_barras = models.CharField(
        'Código', max_length=30, null=True, blank=True)
    controla_vencimiento = models.BooleanField(
        'Controla vencimiento por lote', default=False)
    # La foto NO se guarda en la base: acá va sólo dónde está. O una
    # dirección https:// (la carga una automatización a partir del código de
    # barras) o la ruta, dentro de MEDIA_ROOT, de una foto subida desde el
    # sistema ("productos/…"). Ver stock/imagenes.py (DD-83).
    imagen = models.CharField('Foto', max_length=300, null=True, blank=True)
    # Rebaja propia: si está vacía, rige la del rubro.
    dias_rebaja_vencimiento = models.PositiveSmallIntegerField(
        'Rebaja: días antes de vencer', null=True, blank=True)
    porcentaje_rebaja_vencimiento = models.DecimalField(
        'Rebaja: porcentaje', max_digits=5, decimal_places=2,
        null=True, blank=True)
    usuario_modificacion = sello_usuario_modificacion('productos_modificados')

    class Meta:
        db_table = 'productos'
        verbose_name = 'Producto'
        verbose_name_plural = 'Productos'
        ordering = ['nombre_producto']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'tipo_producto IN %s' % _en(TipoProducto.values)),
                name='ck_productos_tipo'),
            CheckEnBase(
                check=CheckSQL(
                    'precio_costo >= 0 AND stock_minimo >= 0'),
                name='ck_productos_valores'),
            # La matriz de tipos, renglón por renglón.
            CheckEnBase(
                check=CheckSQL(
                    '(tipo_producto IN %s) = (id_proveedor IS NOT NULL)'
                    % _en(TIPOS_CON_PROVEEDOR)),
                name='ck_productos_proveedor'),
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_producto = 'INSUMO' AND precio_venta = 0) OR "
                    "(tipo_producto <> 'INSUMO' AND precio_venta > 0)"),
                name='ck_productos_precio'),
            CheckEnBase(
                check=CheckSQL(
                    'tipo_producto IN %s OR (stock_actual = 0 AND '
                    'stock_minimo = 0 AND NOT controla_vencimiento)'
                    % _en(TIPOS_CON_STOCK)),
                name='ck_productos_sin_stock_propio'),
            CheckEnBase(
                check=CheckSQL(
                    "stock_actual >= 0 OR tipo_producto = 'INSUMO'"),
                name='ck_productos_stock'),
            CheckEnBase(
                check=CheckSQL(
                    "NOT fraccionable OR (tipo_producto IN ('REVENTA','INSUMO')"
                    " AND unidad_medida IN ('KG','L') AND contenido = 1)"),
                name='ck_productos_fraccionable'),
            CheckEnBase(check=CheckSQL(CHECK_REBAJA),
                        name='ck_productos_rebaja'),
            CheckEnBase(
                check=CheckSQL(
                    'dias_rebaja_vencimiento IS NULL OR controla_vencimiento'),
                name='ck_productos_rebaja_con_lote'),
            CheckEnBase(
                check=CheckSQL(
                    'unidad_medida IN %s' % _en(UnidadMedida.values)),
                name='ck_productos_unidad'),
            CheckEnBase(
                check=CheckSQL(
                    'codigo_barras IS NULL OR '
                    "codigo_barras ~ '^[0-9]{3,30}$'"),
                name='ck_productos_codigo_barras'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(nombre_producto) <> '' AND trim(unidad_medida) <> ''"),
                name='ck_productos_textos'),
            CheckEnBase(
                check=CheckSQL(
                    "imagen IS NULL OR imagen ~ '^(https://|productos/)'"),
                name='ck_productos_imagen'),
            CheckEnBase(
                check=CheckSQL('contenido > 0'),
                name='ck_productos_contenido'),
            CheckEnBase(
                check=CheckSQL('alicuota_iva IN (0, 10.5, 21)'),
                name='ck_productos_alicuota_iva'),
            models.UniqueConstraint(
                fields=['codigo_barras'], name='uq_productos_codigo_barras'),
            # Lo que identifica al producto es la TERNA marca + nombre +
            # presentación, no un nombre largo escrito a mano (DD-55).
            models.UniqueConstraint(
                'id_marca', ClaveNormalizada('nombre_producto'), 'contenido',
                ClaveNormalizada('unidad_medida'),
                name='uq_productos_identidad'),
        ]

    def __str__(self):
        return self.descripcion_completa

    # ------------------------------------------------------------------
    #  Cómo se nombra
    # ------------------------------------------------------------------
    @property
    def descripcion_completa(self):
        """Marca · nombre · presentación, que es como se nombra en el mostrador."""
        marca = self.id_marca.nombre_marca if self.id_marca_id else ''
        if marca == MARCA_GENERICA:
            # "Genérica" quiere decir "todavía no se cargó la marca": en el
            # mostrador y en el ticket no aporta nada.
            marca = ''
        partes = [marca, self.nombre_producto, self.presentacion]
        return ' · '.join(p for p in partes if p)

    @property
    def utilidad_porcentaje(self):
        """Cuánto se le carga al costo con IVA para llegar al precio.

        Precio = Costo × (1 + IVA/100) × (1 + Utilidad/100). None si no
        hay costo: no se puede dividir por cero.
        """
        base = (self.precio_costo or CERO) * (1 + (self.alicuota_iva or CERO) / 100)
        if not base or not self.precio_venta:
            return None
        return ((self.precio_venta / base - 1) * 100).quantize(Decimal('0.01'))

    @property
    def presentacion(self):
        """El contenido sin ceros de más: 500 G, 1,5 L, 1 UNIDAD."""
        if self.contenido is None:
            return ''
        return '%s %s' % (_cantidad(self.contenido), self.unidad_medida)

    @property
    def clave(self):
        return clave_normalizada(self.nombre_producto)

    # ------------------------------------------------------------------
    #  Lo que el tipo habilita
    # ------------------------------------------------------------------
    @property
    def tipo_corto(self):
        """'Reventa', 'Combo': el tipo sin la explicación del combo."""
        return self.get_tipo_producto_display().split(' (')[0]

    @property
    def precio_para_mostrar(self):
        """El precio de venta, salvo en un insumo: no se vende, y un
        "$ 0,00" en su tarjeta se leía como un precio olvidado."""
        if not self.es_vendible:
            return 'No se vende'
        return self.precio_venta

    CANTIDADES_DEL_PRODUCTO = ('stock_actual', 'stock_minimo')

    def valor_legible(self, campo, valor):
        """Para la ficha: el stock entero si va por unidad (6, no 6,000) y
        con sus tres decimales si va por peso o es un insumo (0,350). El
        contenido de la presentación queda como está."""
        if campo in self.CANTIDADES_DEL_PRODUCTO and valor is not None:
            from nucleo.templatetags.cafeto import cantidad_de
            return cantidad_de(valor, self)
        return valor

    @property
    def stock_para_mostrar(self):
        """El stock, salvo en lo que no lo tiene propio (elaborado, combo):
        ahí sale de los componentes y un "0" parecía un faltante."""
        if not self.tiene_stock_propio:
            return 'Según componentes'
        return max(self.stock_actual - self.stock_vencido, Decimal('0'))

    @property
    def stock_vencido(self):
        """Lo que está en lotes ya vencidos: no se vende. Si la lista ya lo
        calculó para todas las filas (``vencido_en_lotes``), se usa eso."""
        if not (self.tiene_stock_propio and self.controla_vencimiento):
            return Decimal('0')
        anotado = getattr(self, 'vencido_en_lotes', None)
        if anotado is not None or hasattr(self, 'vencido_en_lotes'):
            return anotado or Decimal('0')
        from stock.servicios import vencido
        return vencido(self)

    @property
    def aclaracion_de_stock(self):
        vencido = self.stock_vencido
        if not vencido:
            return ''
        from nucleo.templatetags.cafeto import cantidad_de
        return f'Además, {cantidad_de(vencido, self)} vencida{"s" if vencido != 1 else ""} (no se venden: hay que sacarlas)'

    @property
    def tiene_stock_propio(self):
        return self.tipo_producto in TIPOS_CON_STOCK

    @property
    def es_vendible(self):
        return self.tipo_producto in TIPOS_VENDIBLES

    @property
    def es_compuesto(self):
        return self.tipo_producto in TIPOS_COMPUESTOS

    @property
    def exige_composicion(self):
        """True si no se puede vender ni guardar sin composición cargada.

        Los combos y los producidos, sí. Un elaborado sin receta se vende
        igual (ver ``TIPOS_CON_COMPOSICION_OBLIGATORIA``).
        """
        return self.tipo_producto in TIPOS_CON_COMPOSICION_OBLIGATORIA

    @property
    def admite_negativo(self):
        return self.tipo_producto == TipoProducto.INSUMO

    @property
    def exige_cantidad_entera(self):
        """Lo que se cuenta por unidad no se vende en fracciones.

        Los insumos sí: la receta de un cortado lleva 0,018 kg de café.
        """
        return not self.fraccionable and self.tipo_producto != TipoProducto.INSUMO

    def cantidad_con_unidad(self, cantidad):
        """'7 unidades', '1 unidad', '0,35 KG': cómo se dice una cantidad de esto."""
        if self.exige_cantidad_entera:
            return f"{_cantidad(cantidad)} {segun(cantidad, 'unidad', 'unidades')}"
        return f'{_cantidad(cantidad)} {self.unidad_medida}'

    def costo_vigente(self):
        """Costo de una unidad AHORA.

        Para un compuesto se recorre la composición: el costo de un café
        con leche es lo que cuestan su café y su leche hoy. Se congela en
        el renglón de la venta, así el margen de ayer no cambia mañana.
        """
        if not self.es_compuesto:
            return self.precio_costo
        renglones = list(self.composicion.select_related('id_componente'))
        # Un elaborado todavía sin receta conserva el costo que se le
        # cargó a mano (o el que trajo la planilla): devolver $0 inflaba el
        # margen al 100 % en el tablero y en los reportes de ganancia.
        if not renglones and self.tipo_producto == TipoProducto.ELABORADO:
            return self.precio_costo
        total = CERO
        for item in renglones:
            total += item.cantidad * item.id_componente.costo_vigente()
        return total.quantize(Decimal('0.01'))

    def rebaja_vigente(self):
        """(días, porcentaje) de la rebaja por vencimiento, o None.

        La del producto manda; si no tiene, la del rubro.
        """
        if not self.controla_vencimiento:
            return None
        if self.dias_rebaja_vencimiento is not None:
            return (self.dias_rebaja_vencimiento,
                    self.porcentaje_rebaja_vencimiento)
        rubro = self.id_rubro
        if rubro.dias_rebaja_vencimiento is not None:
            return (rubro.dias_rebaja_vencimiento,
                    rubro.porcentaje_rebaja_vencimiento)
        return None

    # ------------------------------------------------------------------
    #  Validación
    # ------------------------------------------------------------------
    def clean(self):
        super().clean()
        self.nombre_producto = texto_de_catalogo(
            self.nombre_producto, 2, 100, 'El nombre del producto',
            exige_letra=True)
        self.codigo_barras = validar_codigo_barras(self.codigo_barras)
        self.imagen = (self.imagen or '').strip() or None
        if self.imagen and not self.imagen.startswith(('https://', 'productos/')):
            raise ValidationError({'imagen': 'La foto tiene que ser una dirección '
                                             'https:// o una foto subida desde el sistema.'})

        if self.unidad_medida not in UnidadMedida.values:
            raise ValidationError(
                {'unidad_medida': 'Elegí una unidad de medida de la lista.'})
        if self.tipo_producto not in TipoProducto.values:
            raise ValidationError(
                {'tipo_producto': 'Elegí un tipo de producto de la lista.'})

        self.precio_costo = validar_decimal(
            self.precio_costo if self.precio_costo is not None else CERO,
            minimo=CERO, maximo=Decimal('9999999999.99'), decimales=2,
            etiqueta='El precio de costo')
        self.stock_minimo = validar_decimal(
            self.stock_minimo if self.stock_minimo is not None else CERO,
            minimo=CERO, maximo=Decimal('99999'), decimales=3,
            etiqueta='El stock mínimo')
        self.contenido = validar_decimal(
            self.contenido, minimo=CERO, maximo=Decimal('9999999'),
            decimales=3, etiqueta='El contenido', incluye_minimo=False)

        self._validar_matriz_del_tipo()
        _validar_rebaja(self)

        # La identidad de un producto es la TERNA marca + presentación
        # (DD-55), sin que las mayúsculas, los acentos ni la puntuación la
        # disfracen.
        exigir_nombre_unico(
            self, 'nombre_producto',
            ambito=('id_marca', 'contenido', 'unidad_medida'),
            etiqueta='un producto de esa marca y presentación')

        self._validar_cambio_de_tipo()
        self._validar_cambio_de_control_de_vencimiento()

    def _validar_matriz_del_tipo(self):
        """Lo que la tabla de la cabecera del módulo permite a cada tipo."""
        tipo = self.tipo_producto
        errores = {}
        etiqueta = TipoProducto(tipo).label.split(' (')[0].lower()

        if tipo in TIPOS_CON_PROVEEDOR and self.id_proveedor_id is None:
            errores['id_proveedor'] = (
                f'Un producto de {etiqueta} se compra: indicá a qué proveedor.')
        if tipo not in TIPOS_CON_PROVEEDOR:
            # Lo elaborado en el local no tiene proveedor: se descarta en
            # silencio lo que haya quedado de un cambio de tipo.
            self.id_proveedor = None

        if tipo == TipoProducto.INSUMO:
            self.precio_venta = CERO
        else:
            self.precio_venta = validar_decimal(
                self.precio_venta, minimo=CERO,
                maximo=Decimal('9999999999.99'), decimales=2,
                etiqueta='El precio de venta', incluye_minimo=False)

        if tipo not in TIPOS_CON_STOCK:
            if self.controla_vencimiento:
                errores['controla_vencimiento'] = (
                    'Lo que se arma al momento (elaborados y combos) no '
                    'tiene stock propio ni lotes: vencen sus componentes.')
            self.stock_minimo = CERO

        if self.fraccionable:
            if tipo not in (TipoProducto.REVENTA, TipoProducto.INSUMO):
                errores['fraccionable'] = (
                    'Sólo se fracciona lo que se compra (reventa o insumo).')
            elif (self.unidad_medida not in (UnidadMedida.KG, UnidadMedida.L)
                  or self.contenido != Decimal('1')):
                errores['fraccionable'] = (
                    'Un producto fraccionable se carga por kilo o por litro '
                    '(contenido 1, unidad KG o L): el precio es por kilo y se '
                    'vende la cantidad que se pese.')

        if (self.dias_rebaja_vencimiento is not None
                and not self.controla_vencimiento):
            errores['dias_rebaja_vencimiento'] = (
                'La rebaja por vencimiento necesita que el producto controle '
                'vencimiento por lote.')
        if errores:
            raise ValidationError(errores)

    def _validar_cambio_de_tipo(self):
        """El tipo no cambia si ya hay historia que dependa de él."""
        if self._state.adding:
            return
        anterior = type(self).objects.filter(pk=self.pk).values_list(
            'tipo_producto', flat=True).first()
        if anterior is None or anterior == self.tipo_producto:
            return
        if MovimientoStock.objects.filter(id_producto=self).exists():
            raise ValidationError({'tipo_producto': (
                'El producto ya tiene movimientos de stock: su tipo no se '
                'cambia. Dalo de baja y cargá uno nuevo con el tipo correcto.')})
        if self.composicion.exists() and \
                self.tipo_producto not in TIPOS_COMPUESTOS:
            raise ValidationError({'tipo_producto': (
                'El producto tiene receta o contenido de combo: quitalos '
                'antes de cambiarle el tipo.')})
        if ComposicionProducto.objects.filter(id_componente=self).exists():
            raise ValidationError({'tipo_producto': (
                'El producto forma parte de la receta o el combo de otro: '
                'quitalo de ahí antes de cambiarle el tipo.')})

    def _validar_cambio_de_control_de_vencimiento(self):
        """No se cambia el control de vencimiento con stock de por medio."""
        if self._state.adding:
            return
        anterior = type(self).objects.filter(pk=self.pk).values(
            'controla_vencimiento', 'stock_actual').first()
        if anterior is None or anterior['controla_vencimiento'] == self.controla_vencimiento:
            return

        if anterior['controla_vencimiento'] and not self.controla_vencimiento:
            con_stock = Lote.objects.filter(
                id_producto=self, stock_lote__gt=0).count()
            if con_stock:
                raise ValidationError({'controla_vencimiento': (
                    f'No se puede dejar de controlar el vencimiento: hay '
                    f"{pluralizar(con_stock, 'lote')} con stock. "
                    f"{segun(con_stock, 'Agotalo o ajustalo', 'Agotalos o ajustalos')} a 0 primero.")})
        elif not anterior['controla_vencimiento'] and self.controla_vencimiento:
            if anterior['stock_actual'] != 0:
                raise ValidationError({'controla_vencimiento': (
                    'No se puede empezar a controlar el vencimiento con '
                    f'{_cantidad(anterior["stock_actual"])} en stock: ese '
                    'stock quedaría sin lote. Ajustalo a 0 primero.')})

    def validar_receta(self):
        """Con la composición ya guardada: ¿cierra la matriz del tipo?

        Lo llama el CRUD después de escribir los renglones, en la misma
        transacción: un elaborado sin receta no se podría vender, y un
        producto de reventa con receta no significaría nada.
        """
        cantidad = self.composicion.count()
        if self.exige_composicion and cantidad == 0:
            que = ('el contenido del combo' if self.tipo_producto ==
                   TipoProducto.COMBO else 'la receta')
            raise ValidationError(
                f'Falta {que}: cargá al menos un componente.')
        if not self.es_compuesto and cantidad:
            raise ValidationError(
                'Sólo los elaborados, los producidos y los combos llevan '
                'composición: quitá los renglones o cambiá el tipo.')
        if self.tipo_producto == TipoProducto.COMBO and cantidad == 1:
            unico = self.composicion.get()
            if unico.cantidad == 1:
                raise ValidationError(
                    'Un combo de un solo producto es ese producto: agregá '
                    'otro componente o subí la cantidad.')

    # ------------------------------------------------------------------
    #  Baja y reactivación
    # ------------------------------------------------------------------
    def motivos_que_impiden_la_baja(self):
        from fidelizacion.models import BeneficioProducto

        motivos = []
        if self.stock_actual > 0:
            motivos.append(
                f'tiene {self.cantidad_con_unidad(self.stock_actual)} en '
                'stock; registrá la merma o el ajuste a 0 antes de darlo de '
                'baja')
        usado_en = ComposicionProducto.objects.filter(
            id_componente=self, id_producto__borrado_logico=False).count()
        if usado_en:
            motivos.append(
                f"forma parte de {usado_en} {segun(usado_en, 'receta o combo vigente', 'recetas o combos vigentes')}")
        recetas = BeneficioProducto.objects.filter(
            id_producto=self, id_beneficio__borrado_logico=False).count()
        if recetas:
            motivos.append(
                f"forma parte de {pluralizar(recetas, 'beneficio en especie vigente', 'beneficios en especie vigentes')}")
        lotes = Lote.objects.filter(id_producto=self, stock_lote__gt=0).count()
        if lotes:
            motivos.append(f"tiene {pluralizar(lotes, 'lote')} con stock sin agotar")
        ordenes = (self.renglones_orden.filter(
            id_orden_compra__estado_orden__in=('PENDIENTE', 'EMITIDA', 'CON_DIFERENCIAS'))
            .values('id_orden_compra').distinct().count())
        if ordenes:
            motivos.append(
                f"está pedido en {pluralizar(ordenes, 'orden', 'órdenes')} de compra sin cerrar; "
                f"{segun(ordenes, 'recibila o cancelala', 'recibilas o cancelalas')} primero")
        ajustes = (self.renglones_ajuste.filter(
            id_ajuste__estado_ajuste__in=(EstadoAjuste.EN_CURSO, EstadoAjuste.PENDIENTE))
            .values('id_ajuste').distinct().count())
        if ajustes:
            motivos.append(
                f"está en {pluralizar(ajustes, 'ajuste')} de stock sin resolver; "
                f"{segun(ajustes, 'aprobalo o rechazalo', 'aprobalos o rechazalos')} primero")
        return motivos

    def motivos_que_impiden_reactivar(self):
        motivos = []
        if self.id_rubro.borrado_logico:
            motivos.append(
                f'su rubro "{self.id_rubro}" está dado de baja: reactivalo primero')
        if self.id_proveedor_id and self.id_proveedor.borrado_logico:
            motivos.append(
                f'su proveedor "{self.id_proveedor}" está dado de baja: '
                'reactivalo primero')
        if self.id_marca.borrado_logico:
            motivos.append(
                f'su marca "{self.id_marca}" está dada de baja: '
                'reactivala primero')
        return motivos


# ===========================================================================
#  COMPOSICIÓN: recetas y combos
# ===========================================================================
class ComposicionProducto(models.Model):
    """Un renglón de la receta de un elaborado/producido o de un combo.

    "Café con leche" (ELABORADO) = 0,018 kg de café + 0,150 l de leche.
    "Café + 2 medialunas" (COMBO) = 1 café + 2 medialunas.

    La cantidad va en la unidad del COMPONENTE: si el café se compra por
    kilo, 18 g son 0,018.

    No hay recetas dentro de recetas ni combos dentro de combos (la tabla
    ``COMPONENTES_ADMITIDOS``), así que un ciclo es imposible.
    """

    id_composicion = models.AutoField('ID', primary_key=True)
    id_producto = models.ForeignKey(
        Producto, on_delete=models.CASCADE, db_column='id_producto',
        related_name='composicion', verbose_name='Producto compuesto')
    id_componente = models.ForeignKey(
        Producto, on_delete=models.RESTRICT, db_column='id_componente',
        related_name='usado_en', verbose_name='Componente')
    cantidad = campo_cantidad('Cantidad')

    class Meta:
        db_table = 'composicion_producto'
        verbose_name = 'Componente'
        verbose_name_plural = 'Composición'
        constraints = [
            CheckEnBase(check=CheckSQL('cantidad > 0'),
                        name='ck_composicion_cantidad'),
            CheckEnBase(check=CheckSQL('id_producto <> id_componente'),
                        name='ck_composicion_no_si_mismo'),
            models.UniqueConstraint(
                fields=['id_producto', 'id_componente'],
                name='uq_composicion_componente'),
        ]

    def __str__(self):
        return f'{_cantidad(self.cantidad)} × {self.id_componente}'

    def save(self, *args, **kwargs):
        # El formset del CRUD guarda los renglones sin pasar por clean():
        # la matriz de tipos se controla igual.
        self.full_clean(exclude=['id_producto'] if not self.id_producto_id
                        else None, validate_unique=False,
                        validate_constraints=False)
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if not self.id_producto_id or not self.id_componente_id:
            return
        compuesto, componente = self.id_producto, self.id_componente
        admitidos = COMPONENTES_ADMITIDOS.get(compuesto.tipo_producto)
        if admitidos is None:
            raise ValidationError(
                f'"{compuesto}" no es un elaborado, un producido ni un '
                'combo: no lleva composición.')
        if componente.tipo_producto not in admitidos:
            nombres = ', '.join(sorted(TipoProducto(t).label.split(' (')[0].lower()
                                       for t in admitidos))
            tipo_componente = TipoProducto(componente.tipo_producto).label.split(' (')[0].lower()
            tipo_compuesto = compuesto.get_tipo_producto_display().split(' (')[0].lower()
            raise ValidationError({'id_componente': (
                f'"{componente}" es de tipo {tipo_componente}: un {tipo_compuesto} '
                f'sólo puede llevar {nombres}.')})
        if componente.borrado_logico:
            raise ValidationError({'id_componente': (
                f'"{componente}" está dado de baja.')})
        self.cantidad = validar_decimal(
            self.cantidad, minimo=CERO, maximo=Decimal('9999'), decimales=3,
            etiqueta='La cantidad', incluye_minimo=False)
        if componente.exige_cantidad_entera and not es_entero(self.cantidad):
            raise ValidationError({'cantidad': (
                f'"{componente}" se cuenta por unidad: la cantidad tiene que '
                'ser entera.')})


# ===========================================================================
#  LOTES
# ===========================================================================
class Lote(ConSaldosDerivados, SinBajaFisica, models.Model):
    """Partida de un producto con control de vencimiento.

    Nace de una compra (un renglón puede traer varios lotes), de una
    producción, o de un ajuste de inventario (el stock que ya estaba
    cuando el sistema empezó).
    """

    SALDOS_DERIVADOS = {'stock_lote': Decimal('0')}

    objects = ManagerSinBajaFisica()

    id_lote = models.AutoField('ID', primary_key=True)
    id_producto = models.ForeignKey(
        Producto, on_delete=models.RESTRICT,
        db_column='id_producto', related_name='lotes', verbose_name='Producto')
    id_detalle_compra = models.ForeignKey(
        'compras.DetalleCompra', on_delete=models.RESTRICT,
        db_column='id_detalle_compra', null=True, blank=True,
        related_name='lotes', verbose_name='Renglón de compra')
    id_produccion = models.ForeignKey(
        'stock.Produccion', on_delete=models.RESTRICT,
        db_column='id_produccion', null=True, blank=True,
        related_name='lotes', verbose_name='Producción')
    numero_lote = models.CharField(
        'N° de lote', max_length=40, null=True, blank=True)
    fecha_vencimiento = models.DateField('Vence el')
    cantidad_inicial = campo_cantidad('Cantidad inicial')
    stock_lote = campo_cantidad('Stock del lote', default=Decimal('0'),
                                editable=False)
    fecha_alta = models.DateTimeField('Alta', default=timezone.now)

    class Meta:
        db_table = 'lotes'
        verbose_name = 'Lote'
        verbose_name_plural = 'Lotes'
        ordering = ['fecha_vencimiento', 'id_lote']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'cantidad_inicial > 0 AND stock_lote >= 0'),
                name='ck_lotes_cantidades'),
            CheckEnBase(
                check=CheckSQL(
                    'NOT (id_detalle_compra IS NOT NULL AND '
                    'id_produccion IS NOT NULL)'),
                name='ck_lotes_origen'),
            CheckEnBase(
                check=CheckSQL(
                    'numero_lote IS NULL OR trim(numero_lote) <> \'\''),
                name='ck_lotes_textos'),
            # El mismo número de lote no se carga dos veces en el mismo
            # renglón de la factura.
            models.UniqueConstraint(
                'id_detalle_compra', ClaveNormalizada('numero_lote'),
                condition=models.Q(id_detalle_compra__isnull=False,
                                   numero_lote__isnull=False),
                name='uq_lotes_numero_por_renglon'),
            models.UniqueConstraint(
                fields=['id_produccion'],
                condition=models.Q(id_produccion__isnull=False),
                name='uq_lotes_por_produccion'),
        ]

    def __str__(self):
        vence = self.fecha_vencimiento.strftime('%d/%m/%Y')
        return f'Lote {self.numero_lote or self.pk} (vence {vence})'

    def clean(self):
        super().clean()
        if self.id_producto_id and not self.id_producto.controla_vencimiento:
            raise ValidationError(
                f'"{self.id_producto}" no controla vencimiento: no lleva lotes.')

    def dias_para_vencer(self, hoy=None):
        hoy = hoy or timezone.localdate()
        return (self.fecha_vencimiento - hoy).days


# ===========================================================================
#  EL LIBRO DE STOCK
# ===========================================================================
class TipoMovimientoStock(models.TextChoices):
    COMPRA = 'COMPRA', 'Compra'
    VENTA = 'VENTA', 'Venta'
    DEVOLUCION = 'DEVOLUCION', 'Devolución de un cliente'
    CAMBIO = 'CAMBIO', 'Entrega por cambio'
    MERMA = 'MERMA', 'Merma'
    AJUSTE = 'AJUSTE', 'Ajuste por conteo'
    TRASPASO = 'TRASPASO', 'Traspaso entre productos'
    PRODUCCION = 'PRODUCCION', 'Producción'
    ANULACION = 'ANULACION', 'Anulación de venta'


# Cada tipo, con su documento y su signo.
DOCUMENTO_DEL_MOVIMIENTO = {
    'COMPRA': 'id_compra', 'VENTA': 'id_venta',
    'DEVOLUCION': 'id_cambio', 'CAMBIO': 'id_cambio',
    'MERMA': 'id_ajuste', 'AJUSTE': 'id_ajuste', 'TRASPASO': 'id_ajuste',
    'PRODUCCION': 'id_produccion', 'ANULACION': 'id_venta',
}
DOCUMENTOS = ('id_compra', 'id_venta', 'id_cambio', 'id_ajuste',
              'id_produccion')


def _check_documento():
    renglones = []
    for tipo, documento in DOCUMENTO_DEL_MOVIMIENTO.items():
        otros = ' AND '.join(f'{d} IS NULL' for d in DOCUMENTOS
                             if d != documento)
        renglones.append(
            f"(tipo_movimiento = '{tipo}' AND {documento} IS NOT NULL "
            f'AND {otros})')
    return ' OR '.join(renglones)


class MovimientoStock(LibroInmutable, models.Model):
    """Asiento del libro de stock. Append-only.

    Cada asiento cuelga de UN documento —la compra, la venta, el cambio,
    el ajuste o la producción que lo originó— y de ninguno más: así
    cualquier unidad que entró o salió se puede explicar.
    """

    id_movimiento_stock = models.AutoField('ID', primary_key=True)
    id_producto = models.ForeignKey(
        Producto, on_delete=models.RESTRICT,
        db_column='id_producto', related_name='movimientos',
        verbose_name='Producto')
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='movimientos_stock', verbose_name='Usuario')
    id_lote = models.ForeignKey(
        Lote, on_delete=models.RESTRICT, db_column='id_lote',
        null=True, blank=True, related_name='movimientos', verbose_name='Lote')
    id_compra = models.ForeignKey(
        'compras.Compra', on_delete=models.RESTRICT, db_column='id_compra',
        null=True, blank=True, related_name='movimientos_stock')
    id_venta = models.ForeignKey(
        'ventas.Venta', on_delete=models.RESTRICT, db_column='id_venta',
        null=True, blank=True, related_name='movimientos_stock')
    id_cambio = models.ForeignKey(
        'ventas.Cambio', on_delete=models.RESTRICT, db_column='id_cambio',
        null=True, blank=True, related_name='movimientos_stock')
    id_ajuste = models.ForeignKey(
        'stock.AjusteStock', on_delete=models.RESTRICT, db_column='id_ajuste',
        null=True, blank=True, related_name='movimientos_stock')
    id_produccion = models.ForeignKey(
        'stock.Produccion', on_delete=models.RESTRICT,
        db_column='id_produccion', null=True, blank=True,
        related_name='movimientos_stock')
    tipo_movimiento = models.CharField(
        'Tipo', max_length=10, choices=TipoMovimientoStock.choices)
    cantidad = campo_cantidad('Cantidad')
    # Lo que valía cada unidad en el momento: con esto el libro valoriza el
    # stock y las mermas sin depender del costo de hoy.
    costo_unitario = campo_importe('Costo unitario', default=Decimal('0'))
    fecha_movimiento = models.DateTimeField('Fecha', default=timezone.now)

    objects = ManagerDeLibro()

    class Meta:
        db_table = 'movimientos_stock'
        verbose_name = 'Movimiento de stock'
        verbose_name_plural = 'Movimientos de stock'
        ordering = ['-fecha_movimiento', '-id_movimiento_stock']
        indexes = [
            models.Index(fields=['id_producto', 'fecha_movimiento'],
                         name='ix_movstock_producto_fecha'),
        ]
        constraints = [
            CheckEnBase(
                check=CheckSQL('tipo_movimiento IN %s'
                               % _en(TipoMovimientoStock.values)),
                name='ck_movstock_tipo'),
            CheckEnBase(
                check=CheckSQL(
                    "(tipo_movimiento IN ('COMPRA','DEVOLUCION','ANULACION') "
                    'AND cantidad > 0)'
                    " OR (tipo_movimiento IN ('VENTA','CAMBIO','MERMA') AND "
                    "cantidad < 0) OR (tipo_movimiento IN ('AJUSTE','TRASPASO',"
                    "'PRODUCCION') AND cantidad <> 0)"),
                name='ck_movstock_signo'),
            CheckEnBase(check=CheckSQL(_check_documento()),
                        name='ck_movstock_documento'),
            CheckEnBase(check=CheckSQL('costo_unitario >= 0'),
                        name='ck_movstock_costo'),
        ]

    def __str__(self):
        return f'{self.tipo_movimiento} {_cantidad(self.cantidad)} de {self.id_producto}'

    def save(self, *args, **kwargs):
        """Asienta el movimiento y mueve el stock, en la misma transacción.

        ``cantidad`` ya viene con signo —negativa si sale—, así que sumar
        alcanza. Va acá y no en un servicio porque el asiento y el saldo
        tienen que moverse juntos SIEMPRE: si quedara afuera, un
        ``create()`` suelto dejaría el stock desfasado y nadie se enteraría.

        Los controles son los que el cliente ve en rojo en el mostrador:
        no se vende lo que no hay, ni se vende media lata.
        """
        with transaction.atomic():
            producto = (Producto.objects.select_for_update()
                        .get(pk=self.id_producto_id))

            if not producto.tiene_stock_propio:
                raise ValidationError(
                    f'"{producto}" no tiene stock propio: se descuentan sus '
                    'componentes.')
            if producto.exige_cantidad_entera and not es_entero(self.cantidad):
                raise ValidationError(
                    f'"{producto}" se vende por unidad: la cantidad tiene que '
                    f'ser entera (se indicó {_cantidad(abs(self.cantidad))}).')

            if producto.controla_vencimiento and self.id_lote_id is None:
                raise ValidationError(
                    f'"{producto}" se gestiona por lote: el movimiento exige '
                    'indicar cuál (DD-20).')
            if not producto.controla_vencimiento and self.id_lote_id:
                raise ValidationError(
                    f'"{producto}" no se gestiona por lote: el movimiento no '
                    'lleva lote.')

            lote = None
            if self.id_lote_id:
                lote = (Lote.objects.select_for_update()
                        .filter(pk=self.id_lote_id, id_producto=producto)
                        .first())
                if lote is None:
                    raise ValidationError(
                        f'El lote indicado no pertenece a "{producto}".')
                if lote.stock_lote + self.cantidad < 0:
                    raise ValidationError(
                        f'Stock insuficiente en el lote {lote} de '
                        f'"{producto}": hay '
                        f'{producto.cantidad_con_unidad(lote.stock_lote)} y se '
                        f'piden {producto.cantidad_con_unidad(-self.cantidad)}.')

            nuevo = producto.stock_actual + self.cantidad
            if nuevo < 0 and not producto.admite_negativo:
                raise ValidationError(
                    f'Stock insuficiente de "{producto}": hay '
                    f'{producto.cantidad_con_unidad(producto.stock_actual)} y '
                    f'se piden {producto.cantidad_con_unidad(-self.cantidad)}.')

            if self.costo_unitario is None or self.costo_unitario == 0:
                self.costo_unitario = producto.precio_costo

            super().save(*args, **kwargs)

            producto.actualizar_saldo_derivado(stock_actual=nuevo)
            if lote is not None:
                lote.actualizar_saldo_derivado(
                    stock_lote=lote.stock_lote + self.cantidad)


# ===========================================================================
#  AJUSTES DE STOCK: conteo, merma y traspaso
# ===========================================================================
class TipoAjuste(models.TextChoices):
    CONTEO = 'CONTEO', 'Conteo de inventario'
    MERMA = 'MERMA', 'Merma'
    TRASPASO = 'TRASPASO', 'Traspaso entre productos'


class EstadoAjuste(models.TextChoices):
    EN_CURSO = 'EN_CURSO', 'Contando'
    PENDIENTE = 'PENDIENTE', 'Espera autorización'
    APROBADO = 'APROBADO', 'Aprobado'
    RECHAZADO = 'RECHAZADO', 'Rechazado'


class MotivoAjuste(models.TextChoices):
    INVENTARIO_INICIAL = 'INVENTARIO_INICIAL', 'Inventario inicial'
    DIFERENCIA_CONTEO = 'DIFERENCIA_CONTEO', 'Diferencia de conteo'
    VENCIMIENTO = 'VENCIMIENTO', 'Vencido'
    ROTURA = 'ROTURA', 'Rotura o daño'
    DETERIORO = 'DETERIORO', 'Deterioro (mal estado)'
    CONSUMO_INTERNO = 'CONSUMO_INTERNO', 'Consumo interno'
    USO_EN_CAFETERIA = 'USO_EN_CAFETERIA', 'Pasa a usarse como insumo'
    REEMPAQUE = 'REEMPAQUE', 'Reempaque o fraccionamiento'
    OTRO = 'OTRO', 'Otro (explicar)'


MOTIVOS_POR_TIPO = {
    TipoAjuste.CONTEO: {MotivoAjuste.INVENTARIO_INICIAL,
                        MotivoAjuste.DIFERENCIA_CONTEO},
    TipoAjuste.MERMA: {MotivoAjuste.VENCIMIENTO, MotivoAjuste.ROTURA,
                       MotivoAjuste.DETERIORO, MotivoAjuste.CONSUMO_INTERNO,
                       MotivoAjuste.OTRO},
    TipoAjuste.TRASPASO: {MotivoAjuste.USO_EN_CAFETERIA,
                          MotivoAjuste.REEMPAQUE, MotivoAjuste.OTRO},
}


def _check_motivos():
    return ' OR '.join(
        f"(tipo_ajuste = '{tipo}' AND motivo IN {_en(motivos)})"
        for tipo, motivos in MOTIVOS_POR_TIPO.items())


class AjusteStock(SinBajaFisica, models.Model):
    """Un conteo, una merma o un traspaso, con quién lo autorizó.

    El stock NO se mueve al cargarlo: se mueve recién cuando alguien con
    permiso lo APRUEBA. Así una merma cargada por un operador queda
    esperando a que la encargada la confirme, y el conteo del depósito no
    pisa el stock hasta que se revisaron las diferencias.

        CONTEO     EN_CURSO -> PENDIENTE -> APROBADO / RECHAZADO
        MERMA      PENDIENTE -> APROBADO / RECHAZADO
        TRASPASO   PENDIENTE -> APROBADO / RECHAZADO

    Quien tiene el permiso de autorizar puede aprobar lo que él mismo
    cargó: la encargada que tira las medialunas quemadas no necesita que
    nadie más la autorice. Queda escrito igual quién fue.
    """

    TRANSICIONES = {
        EstadoAjuste.EN_CURSO: {EstadoAjuste.PENDIENTE},
        EstadoAjuste.PENDIENTE: {EstadoAjuste.APROBADO,
                                 EstadoAjuste.RECHAZADO},
        EstadoAjuste.APROBADO: set(),
        EstadoAjuste.RECHAZADO: set(),
    }

    id_ajuste = models.AutoField('ID', primary_key=True)
    tipo_ajuste = models.CharField('Tipo', max_length=10,
                                   choices=TipoAjuste.choices)
    estado_ajuste = models.CharField(
        'Estado', max_length=10, choices=EstadoAjuste.choices,
        default=EstadoAjuste.PENDIENTE)
    motivo = models.CharField('Motivo', max_length=20,
                              choices=MotivoAjuste.choices)
    detalle_motivo = models.CharField(
        'Detalle', max_length=120, null=True, blank=True)
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='ajustes_cargados', verbose_name='Cargó')
    id_usuario_autorizante = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_autorizante', null=True, blank=True,
        related_name='ajustes_resueltos', verbose_name='Resolvió')
    fecha_ajuste = models.DateTimeField('Fecha', default=timezone.now)
    fecha_resolucion = models.DateTimeField('Resuelto el', null=True,
                                            blank=True)
    observacion_resolucion = models.CharField(
        'Observación de quien resolvió', max_length=120, null=True,
        blank=True)

    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'ajustes_stock'
        verbose_name = 'Ajuste de stock'
        verbose_name_plural = 'Ajustes de stock'
        ordering = ['-fecha_ajuste']
        constraints = [
            CheckEnBase(
                check=CheckSQL('tipo_ajuste IN %s' % _en(TipoAjuste.values)),
                name='ck_ajustes_tipo'),
            CheckEnBase(
                check=CheckSQL('estado_ajuste IN %s'
                               % _en(EstadoAjuste.values)),
                name='ck_ajustes_estado'),
            CheckEnBase(check=CheckSQL(_check_motivos()),
                        name='ck_ajustes_motivo'),
            CheckEnBase(
                check=CheckSQL(
                    "estado_ajuste <> 'EN_CURSO' OR tipo_ajuste = 'CONTEO'"),
                name='ck_ajustes_en_curso'),
            CheckEnBase(
                check=CheckSQL(
                    "(estado_ajuste IN ('APROBADO','RECHAZADO')) = "
                    '(id_usuario_autorizante IS NOT NULL AND '
                    'fecha_resolucion IS NOT NULL)'),
                name='ck_ajustes_resolucion'),
            CheckEnBase(
                check=CheckSQL(
                    "estado_ajuste <> 'RECHAZADO' OR "
                    "trim(coalesce(observacion_resolucion, '')) <> ''"),
                name='ck_ajustes_rechazo'),
            CheckEnBase(
                check=CheckSQL(
                    "motivo <> 'OTRO' OR "
                    "trim(coalesce(detalle_motivo, '')) <> ''"),
                name='ck_ajustes_detalle'),
            CheckEnBase(
                check=CheckSQL(
                    'fecha_resolucion IS NULL OR fecha_resolucion >= '
                    'fecha_ajuste'),
                name='ck_ajustes_fechas'),
            # Un solo conteo abierto a la vez: dos conteos simultáneos
            # congelarían teóricos distintos del mismo producto.
            models.UniqueConstraint(
                'tipo_ajuste',
                condition=models.Q(tipo_ajuste='CONTEO',
                                   estado_ajuste__in=('EN_CURSO',
                                                      'PENDIENTE')),
                name='uq_un_conteo_abierto'),
        ]

    def __str__(self):
        return (f'{self.get_tipo_ajuste_display()} N° {self.pk} '
                f'({self.get_estado_ajuste_display().lower()})')

    @property
    def editable(self):
        return self.estado_ajuste in (EstadoAjuste.EN_CURSO,
                                      EstadoAjuste.PENDIENTE)

    def clean(self):
        super().clean()
        if self.motivo not in MOTIVOS_POR_TIPO.get(self.tipo_ajuste, ()):
            raise ValidationError({'motivo': (
                'Ese motivo no corresponde a un '
                f'{self.get_tipo_ajuste_display().lower()}.')})
        self.detalle_motivo = texto_de_catalogo(
            self.detalle_motivo, 3, 120, 'El detalle',
            obligatorio=self.motivo == MotivoAjuste.OTRO)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            anterior = type(self).objects.filter(pk=self.pk).values_list(
                'estado_ajuste', flat=True).first()
            if (anterior is not None and anterior != self.estado_ajuste
                    and self.estado_ajuste not in self.TRANSICIONES[anterior]):
                raise ValidationError(
                    f'El ajuste {self.pk} no puede pasar de '
                    f'{EstadoAjuste(anterior).label.lower()} a '
                    f'{EstadoAjuste(self.estado_ajuste).label.lower()}.')
            if anterior in (EstadoAjuste.APROBADO, EstadoAjuste.RECHAZADO):
                raise ValidationError(
                    f'El ajuste {self.pk} ya está resuelto: no se modifica.')
        return super().save(*args, **kwargs)


class DetalleAjuste(models.Model):
    """Renglón de un ajuste.

    * CONTEO: ``cantidad_teorica`` (lo que decía el sistema al empezar a
      contar, congelado) y ``cantidad_contada``. La diferencia se calcula.
    * MERMA: ``cantidad`` que se pierde.
    * TRASPASO: ``cantidad`` que sale del producto de origen y
      ``cantidad_destino`` que entra al de destino (1 sachet de leche de
      góndola = 1 l del insumo "leche").

    ``costo_unitario`` se congela al cargar: valoriza la merma con lo que
    costaba ese día.
    """

    id_detalle_ajuste = models.AutoField('ID', primary_key=True)
    id_ajuste = models.ForeignKey(
        AjusteStock, on_delete=models.CASCADE, db_column='id_ajuste',
        related_name='detalles', verbose_name='Ajuste')
    id_producto = models.ForeignKey(
        Producto, on_delete=models.RESTRICT, db_column='id_producto',
        related_name='renglones_ajuste', verbose_name='Producto')
    id_lote = models.ForeignKey(
        Lote, on_delete=models.RESTRICT, db_column='id_lote', null=True,
        blank=True, related_name='renglones_ajuste', verbose_name='Lote')
    cantidad_teorica = campo_cantidad('Según el sistema', null=True,
                                      blank=True)
    cantidad_contada = campo_cantidad('Contado', null=True, blank=True)
    cantidad = campo_cantidad('Cantidad', null=True, blank=True)
    id_producto_destino = models.ForeignKey(
        Producto, on_delete=models.RESTRICT, db_column='id_producto_destino',
        null=True, blank=True, related_name='renglones_traspaso_destino',
        verbose_name='Pasa a')
    cantidad_destino = campo_cantidad('Cantidad que entra', null=True,
                                      blank=True)
    costo_unitario = campo_importe('Costo unitario', default=Decimal('0'))

    class Meta:
        db_table = 'detalles_ajuste'
        verbose_name = 'Renglón de ajuste'
        verbose_name_plural = 'Renglones de ajuste'
        constraints = [
            # Un renglón es de conteo o de movimiento, nunca las dos cosas.
            CheckEnBase(
                check=CheckSQL(
                    '(cantidad_teorica IS NOT NULL AND cantidad_contada IS '
                    'NOT NULL AND cantidad_contada >= 0 AND cantidad IS NULL '
                    'AND id_producto_destino IS NULL) OR (cantidad_teorica IS '
                    'NULL AND cantidad_contada IS NULL AND cantidad IS NOT NULL '
                    'AND cantidad > 0)'),
                name='ck_detajuste_forma'),
            CheckEnBase(
                check=CheckSQL(
                    '(id_producto_destino IS NULL) = (cantidad_destino IS '
                    'NULL) AND (cantidad_destino IS NULL OR cantidad_destino '
                    '> 0) AND (id_producto_destino IS NULL OR '
                    'id_producto_destino <> id_producto)'),
                name='ck_detajuste_traspaso'),
            CheckEnBase(check=CheckSQL('costo_unitario >= 0'),
                        name='ck_detajuste_costo'),
            models.UniqueConstraint(
                fields=['id_ajuste', 'id_producto', 'id_lote'],
                nulls_distinct=False,
                name='uq_detajuste_producto_lote'),
        ]

    def __str__(self):
        return f'{self.id_producto}: {self.descripcion_cantidad}'

    @property
    def diferencia(self):
        if self.cantidad_teorica is None:
            return None
        return self.cantidad_contada - self.cantidad_teorica

    @property
    def descripcion_cantidad(self):
        if self.cantidad_teorica is not None:
            return (f'contado {_cantidad(self.cantidad_contada)} '
                    f'(diferencia {_cantidad(self.diferencia)})')
        return _cantidad(self.cantidad)

    @property
    def importe(self):
        """Lo que vale el renglón: la merma o la diferencia, a costo."""
        base = self.diferencia if self.cantidad is None else -self.cantidad
        return (base * self.costo_unitario).quantize(Decimal('0.01'))

    def _exigir_ajuste_editable(self):
        if contexto.en_pasaje_historico():
            return
        if not self.id_ajuste.editable:
            raise ValidationError(
                f'{self.id_ajuste} ya está resuelto: sus renglones no se '
                'modifican.')

    def clean(self):
        super().clean()
        producto = self.id_producto
        if not producto.tiene_stock_propio:
            raise ValidationError({'id_producto': (
                f'"{producto}" no tiene stock propio: no se cuenta ni se '
                'registra merma; eso se hace con sus componentes.')})
        if producto.controla_vencimiento and self.id_lote_id is None:
            raise ValidationError({'id_lote': (
                f'"{producto}" se gestiona por lote: indicá cuál.')})
        for campo in ('cantidad', 'cantidad_contada', 'cantidad_destino'):
            valor = getattr(self, campo)
            destino = (self.id_producto_destino if campo == 'cantidad_destino'
                       else producto)
            if (valor is not None and destino is not None
                    and destino.exige_cantidad_entera and not es_entero(valor)):
                raise ValidationError({campo: (
                    f'"{destino}" se cuenta por unidad: la cantidad tiene que '
                    'ser entera.')})
        if self.id_producto_destino_id:
            if not self.id_producto_destino.tiene_stock_propio:
                raise ValidationError({'id_producto_destino': (
                    'El destino del traspaso tiene que tener stock propio.')})

    def save(self, *args, **kwargs):
        """Congela el teórico del conteo y el costo, una sola vez (DD-08)."""
        self._exigir_ajuste_editable()
        if self._state.adding:
            # Del saldo de la BASE, no de la instancia que trajo la
            # pantalla: puede ser de antes de la última venta.
            producto = Producto.objects.get(pk=self.id_producto_id)
            if self.cantidad_contada is not None:
                self.cantidad_teorica = (
                    Lote.objects.get(pk=self.id_lote_id).stock_lote
                    if self.id_lote_id else producto.stock_actual)
            self.costo_unitario = producto.precio_costo
        else:
            anterior = type(self).objects.filter(pk=self.pk).values(
                'cantidad_teorica', 'costo_unitario').first()
            if anterior is not None:
                self.cantidad_teorica = anterior['cantidad_teorica']
                self.costo_unitario = anterior['costo_unitario']
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._exigir_ajuste_editable()
        return super().delete(*args, **kwargs)


# ===========================================================================
#  PRODUCCIONES
# ===========================================================================
class Produccion(LibroInmutable, models.Model):
    """Una tanda de algo que se fabrica en el local: 24 empanadas de carne.

    Al registrarla entran las unidades producidas y salen los insumos de la
    receta multiplicados por la cantidad. El costo unitario se congela con
    lo que costaban los insumos ese día. Es un libro: una tanda mal cargada
    no se edita, se corrige con un ajuste.
    """

    id_produccion = models.AutoField('ID', primary_key=True)
    id_producto = models.ForeignKey(
        Producto, on_delete=models.RESTRICT, db_column='id_producto',
        related_name='producciones', verbose_name='Producto')
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='producciones', verbose_name='Registró')
    fecha_produccion = models.DateTimeField('Fecha', default=timezone.now)
    cantidad = campo_cantidad('Cantidad producida')
    costo_unitario = campo_importe('Costo unitario', default=Decimal('0'))
    fecha_vencimiento = models.DateField('Vence el', null=True, blank=True)
    observacion = models.CharField('Observación', max_length=120, null=True,
                                   blank=True)

    objects = ManagerDeLibro()

    class Meta:
        db_table = 'producciones'
        verbose_name = 'Producción'
        verbose_name_plural = 'Producciones'
        ordering = ['-fecha_produccion']
        constraints = [
            CheckEnBase(check=CheckSQL('cantidad > 0 AND costo_unitario >= 0'),
                        name='ck_producciones_valores'),
            CheckEnBase(
                check=CheckSQL(
                    'fecha_vencimiento IS NULL OR fecha_vencimiento >= '
                    'fecha_produccion::DATE'),
                name='ck_producciones_vencimiento'),
        ]

    def __str__(self):
        return (f'{_cantidad(self.cantidad)} × {self.id_producto} '
                f'({self.fecha_produccion:%d/%m %H:%M})')


# ===========================================================================
#  SUGERENCIAS DE MEJORA DEL CATÁLOGO
# ===========================================================================
#  La bandeja de la mejora continua (Etapa 2). Una automatización mira el
#  catálogo heredado y PROPONE: "este producto parece de Lácteos", "éste se
#  vendió 14 veces sin receta", "éste hace un año que no se mueve". No
#  cambia nada. El cambio lo aplica una persona desde «Sugerencias de
#  mejora», con el mismo `full_clean()` y las mismas reglas que si lo
#  editara a mano, y queda en el Registro de Actividad con su nombre.
#
#  Mismo patrón que `compras.FacturaRecibida`: estado PENDIENTE hasta que
#  alguien la resuelve, y una huella con UNIQUE parcial para que n8n pueda
#  reintentar todas las noches sin llenar la bandeja de repetidas.
class TipoSugerencia(models.TextChoices):
    RUBRO = 'RUBRO', 'Cambiar rubro'
    MARCA = 'MARCA', 'Completar marca'
    RECETA = 'RECETA', 'Cargar receta'
    PRECIO = 'PRECIO', 'Revisar precio'
    BAJA_INACTIVO = 'BAJA_INACTIVO', 'Dar de baja (sin movimiento)'


class EstadoSugerencia(models.TextChoices):
    PENDIENTE = 'PENDIENTE', 'Para revisar'
    ACEPTADA = 'ACEPTADA', 'Aplicada'
    DESCARTADA = 'DESCARTADA', 'Descartada'


ORIGEN_AUTOMATIZACION = 'AUTOMATIZACION'


class SugerenciaCatalogo(models.Model):
    """Una mejora propuesta sobre un producto, a la espera de una persona.

    ``propuesta`` es lo que se haría, en datos y no en prosa
    (``{"rubro": "Almacén"}``, ``{"precio_venta": "3500.00"}``), y
    ``motivo`` es por qué, en castellano, para que quien aprueba entienda
    de dónde salió sin tener que confiar a ciegas.

    ``huella`` es el SHA-256 de (producto, tipo, propuesta): mientras la
    sugerencia esté PENDIENTE no entra otra igual. Una vez resuelta sí,
    porque si la realidad volvió a cambiar vale preguntar de nuevo.
    """

    id_sugerencia = models.AutoField('ID', primary_key=True)
    tipo_sugerencia = models.CharField(
        'Qué se propone', max_length=15, choices=TipoSugerencia.choices)
    id_producto = models.ForeignKey(
        Producto, on_delete=models.RESTRICT, db_column='id_producto',
        related_name='sugerencias', verbose_name='Producto')
    origen = models.CharField('Quién la propuso', max_length=20,
                              default=ORIGEN_AUTOMATIZACION)
    motivo = models.CharField('Por qué', max_length=200)
    propuesta = models.JSONField('Lo que se propone', default=dict, blank=True)
    huella = models.CharField('Huella', max_length=64)
    estado_sugerencia = models.CharField(
        'Estado', max_length=10, choices=EstadoSugerencia.choices,
        default=EstadoSugerencia.PENDIENTE)
    fecha_creacion = models.DateTimeField('Propuesta el', default=timezone.now)
    id_usuario_resolucion = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario_resolucion', null=True, blank=True,
        related_name='sugerencias_resueltas', verbose_name='Resolvió')
    fecha_resolucion = models.DateTimeField('Resuelta', null=True, blank=True)

    class Meta:
        db_table = 'sugerencias_catalogo'
        verbose_name = 'Sugerencia de catálogo'
        verbose_name_plural = 'Sugerencias de catálogo'
        ordering = ['tipo_sugerencia', 'fecha_creacion']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    'tipo_sugerencia IN %s AND estado_sugerencia IN %s AND '
                    "trim(motivo) <> ''"
                    % (_en(TipoSugerencia.values), _en(EstadoSugerencia.values))),
                name='ck_sugerencias_dominios'),
            CheckEnBase(
                check=CheckSQL(
                    "((estado_sugerencia = 'PENDIENTE') = (id_usuario_resolucion IS NULL)) AND "
                    '((id_usuario_resolucion IS NULL) = (fecha_resolucion IS NULL)) AND '
                    '(fecha_resolucion IS NULL OR fecha_resolucion >= fecha_creacion)'),
                name='ck_sugerencias_resolucion'),
            # Idempotencia: n8n puede reintentar sin duplicar jamás.
            models.UniqueConstraint(
                fields=['huella'],
                condition=models.Q(estado_sugerencia='PENDIENTE'),
                name='uq_sugerencias_pendiente'),
        ]

    def __str__(self):
        return f'{self.get_tipo_sugerencia_display()} · {self.id_producto}'

    @property
    def pendiente(self):
        return self.estado_sugerencia == EstadoSugerencia.PENDIENTE

    @property
    def valor_actual(self):
        """Lo que el producto tiene HOY, para mostrarlo al lado de lo
        propuesto: sin eso, aprobar es firmar en blanco."""
        producto = self.id_producto
        if self.tipo_sugerencia == TipoSugerencia.RUBRO:
            return producto.id_rubro.nombre_rubro
        if self.tipo_sugerencia == TipoSugerencia.MARCA:
            return producto.id_marca.nombre_marca
        if self.tipo_sugerencia == TipoSugerencia.PRECIO:
            return _cantidad(producto.precio_venta)
        if self.tipo_sugerencia == TipoSugerencia.RECETA:
            renglones = producto.composicion.count()
            return (f"{pluralizar(renglones, 'insumo')} en la receta"
                    if renglones else 'Sin receta')
        return 'Vigente'

    @property
    def valor_propuesto(self):
        """Lo mismo, del lado de la propuesta."""
        datos = self.propuesta if isinstance(self.propuesta, dict) else {}
        if self.tipo_sugerencia == TipoSugerencia.RUBRO:
            return str(datos.get('rubro') or '')
        if self.tipo_sugerencia == TipoSugerencia.MARCA:
            return str(datos.get('marca') or '')
        if self.tipo_sugerencia == TipoSugerencia.PRECIO:
            return _cantidad(datos.get('precio_venta') or 0)
        if self.tipo_sugerencia == TipoSugerencia.RECETA:
            insumos = datos.get('insumos') or []
            return ', '.join(str(i) for i in insumos) if insumos else 'A definir'
        return 'Dado de baja'
