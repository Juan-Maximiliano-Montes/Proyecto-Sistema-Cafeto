"""Módulo Compras y Proveedores.

PROVEEDORES y TIPOS_EGRESO (catálogos), ORDENES_COMPRA y COMPRAS con sus
renglones, y EGRESOS: las obligaciones y los pagos del negocio.

El estado de una compra NO se escribe a mano: se deriva de sus egresos
(DD-22). Y una compra no se puede pagar de más: la suma de sus egresos
vivos nunca supera el total de la factura.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.db import models
from django.utils import timezone

from nucleo.modelos import (
    Maestra,
    campo_cantidad,
    campo_importe,
    sello_fecha_modificacion,
    sello_usuario_modificacion,
)
from nucleo.reglas import (
    ConSaldosDerivados,
    ConSelloDeModificacion,
    ManagerSinBajaFisica,
    SinBajaFisica,
)
from nucleo.restricciones import CheckEnBase, CheckSQL, ClaveNormalizada
from nucleo.validaciones import (
    NO_NEGATIVO,
    VALIDADOR_CUIT,
    VALIDADOR_TELEFONO,
    clave_normalizada,
    exigir_nombre_unico,
    exigir_que_no_este_dado_de_baja,
    texto_de_catalogo,
    validar_cuit,
    validar_telefono,
)
from nucleo.templatetags.cafeto import pesos


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


class TipoProveedor(models.TextChoices):
    MERCADERIA = 'MERCADERIA', 'Mercadería e insumos'
    SERVICIOS = 'SERVICIOS', 'Servicios (luz, internet, mantenimiento)'
    MIXTO = 'MIXTO', 'Mercadería y servicios'


class Proveedor(ConSelloDeModificacion,
                SinBajaFisica, models.Model):
    """Proveedor de mercadería o de servicios. Tabla PROVEEDORES del DER.

    El tipo decide dónde aparece: a un proveedor de servicios se le pagan
    egresos, pero no se le cargan compras de mercadería ni es el proveedor
    de ningún producto.
    """

    # Alias: hace que 'Proveedor.CondicionPago' siga funcionando en views.py
    CondicionPago = CondicionPago
    TipoProveedor = TipoProveedor

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
    tipo_proveedor = models.CharField(
        'Tipo de proveedor', max_length=10, choices=TipoProveedor.choices,
        default=TipoProveedor.MERCADERIA)
    fecha_alta = models.DateTimeField(
        'Fecha de alta', default=timezone.now, editable=False)
    domicilio = models.CharField(
        'Domicilio', max_length=120, null=True, blank=True)
    id_localidad = models.ForeignKey(
        'geografia.Localidad', on_delete=models.RESTRICT, db_column='id_localidad',
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
                    "tipo_proveedor IN ('MERCADERIA','SERVICIOS','MIXTO')"),
                name='ck_proveedores_tipo'),
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

        # La razón social, única sin distinguir mayúsculas, acentos ni
        # puntuación. Va atada al campo para que el aviso salga al lado
        # del control y el formulario vuelva con los datos cargados.
        exigir_nombre_unico(self, 'nombre_proveedor',
                            etiqueta='un proveedor')

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
        if self.tipo_proveedor not in TipoProveedor.values:
            raise ValidationError({
                'tipo_proveedor': 'Elegí un tipo de proveedor de la lista.'})
        if self.pk and self.tipo_proveedor == TipoProveedor.SERVICIOS:
            from stock.models import Producto

            productos = Producto.objects.filter(
                id_proveedor=self, borrado_logico=False).count()
            if productos:
                raise ValidationError({'tipo_proveedor': (
                    f'Es el proveedor de {productos} producto(s): no puede '
                    'ser sólo de servicios. Usá "Mercadería y servicios".')})

    @property
    def vende_mercaderia(self):
        return self.tipo_proveedor != TipoProveedor.SERVICIOS

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
    @property
    def vigente(self):
        return not self.borrado_logico

    def motivos_que_impiden_reactivar(self):
        """Nada lo impide: la localidad no tiene baja y el CUIT sigue siendo suyo."""
        return []

    def motivos_que_impiden_la_baja(self):
        """Lista de motivos por los que este proveedor no se puede dar de baja.

        Vacía = se puede. Cada motivo ya viene redactado para mostrarlo tal
        cual al usuario, con la cantidad, para que sepa qué tiene que
        resolver antes.
        """
        from compras.models import Compra, OrdenCompra
        from stock.models import Producto

        motivos = []

        abiertas = OrdenCompra.objects.filter(
            id_proveedor=self, estado_orden__in=('PENDIENTE', 'EMITIDA', 'CON_DIFERENCIAS')
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
        from compras.models import Egreso

        pendientes = Egreso.objects.filter(
            id_tipo_egreso=self,
            estado_pago__in=('PENDIENTE_PAGO', 'PENDIENTE_ACREDITACION'),
        ).count()
        if pendientes:
            return [f'tiene {pendientes} egreso(s) sin acreditar '
                    '(pendientes de pago o de acreditación)']
        return []


# ===========================================================================
#  COMPRAS
# ===========================================================================
class OrdenCompra(SinBajaFisica, models.Model):
    """Pedido al proveedor. Ciclo PENDIENTE → EMITIDA → RECEPCIONADA (DD-44).

    Si lo recibido no coincide con lo pedido, la orden queda
    CON_DIFERENCIAS hasta que llegue el resto o la Administradora la cierre
    con la diferencia acordada con el proveedor (DD-64).
    """

    ESTADOS = (
        ('PENDIENTE', 'Pendiente'), ('EMITIDA', 'Emitida'),
        ('CON_DIFERENCIAS', 'Recibida con diferencias'),
        ('RECEPCIONADA', 'Recepcionada'), ('CANCELADA', 'Cancelada'),
    )

    id_orden_compra = models.AutoField('ID', primary_key=True)
    id_proveedor = models.ForeignKey(
        'compras.Proveedor', on_delete=models.RESTRICT,
        db_column='id_proveedor', related_name='ordenes',
        verbose_name='Proveedor')
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
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
        'EMITIDA': {'RECEPCIONADA', 'CON_DIFERENCIAS', 'CANCELADA'},
        'CON_DIFERENCIAS': {'RECEPCIONADA'},
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
                    "estado_orden IN ('PENDIENTE','EMITIDA','CON_DIFERENCIAS',"
                    "'RECEPCIONADA','CANCELADA')"),
                name='ck_ordenes_estado'),
            CheckEnBase(
                check=CheckSQL('total_orden >= 0'), name='ck_ordenes_total'),
            CheckEnBase(
                check=CheckSQL("trim(estado_orden) <> ''"),
                name='ck_ordenes_compra_textos'),
        ]

    def __str__(self):
        return f'Orden {self.pk} · {self.id_proveedor}'

    @property
    def admite_recepcion(self):
        return self.estado_orden in ('EMITIDA', 'CON_DIFERENCIAS')

    def diferencias(self):
        """La planilla de control: lo pedido contra lo recibido.

        Una fila por producto (los pedidos y los que llegaron sin pedirse),
        con lo recibido sumado de todas las compras de esta orden.
        ``diferencia`` < 0 es un faltante; > 0, un sobrante.
        """
        from django.db.models import Sum

        from stock.models import Producto

        pedido = {d.id_producto_id: d for d in
                  self.detalles.select_related('id_producto')}
        recibido = dict(
            DetalleCompra.objects.filter(id_compra__id_orden_compra=self)
            .values_list('id_producto').annotate(t=Sum('cantidad_recibida')))
        filas = []
        for pk in list(pedido) + [p for p in recibido if p not in pedido]:
            renglon = pedido.get(pk)
            producto = (renglon.id_producto if renglon is not None
                        else Producto.objects.get(pk=pk))
            cantidad_pedida = renglon.cantidad_pedida if renglon else Decimal('0')
            cantidad_recibida = recibido.get(pk) or Decimal('0')
            filas.append({
                'producto': producto, 'pedido': cantidad_pedida,
                'recibido': cantidad_recibida,
                'diferencia': cantidad_recibida - cantidad_pedida})
        return filas

    def hay_diferencias(self):
        return any(f['diferencia'] for f in self.diferencias())


class DetalleOrdenCompra(models.Model):
    """Renglón del pedido."""

    id_detalle_orden_compra = models.AutoField('ID', primary_key=True)
    id_orden_compra = models.ForeignKey(
        OrdenCompra, on_delete=models.CASCADE, db_column='id_orden_compra',
        related_name='detalles', verbose_name='Orden')
    id_producto = models.ForeignKey(
        'stock.Producto', on_delete=models.RESTRICT,
        db_column='id_producto', related_name='renglones_orden',
        verbose_name='Producto')
    cantidad_pedida = campo_cantidad('Cantidad pedida')
    precio_unitario = models.DecimalField(
        'Precio unitario', max_digits=12, decimal_places=2,
        validators=[NO_NEGATIVO])

    def _exigir_orden_editable(self):
        """Los renglones se cargan mientras la orden es borrador (DD-44)."""
        from nucleo import contexto

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

    @property
    def subtotal(self):
        return (self.cantidad_pedida * self.precio_unitario).quantize(
            Decimal('0.01'))


class TipoComprobante(models.TextChoices):
    FACTURA_A = 'FACTURA_A', 'Factura A'
    FACTURA_B = 'FACTURA_B', 'Factura B'
    FACTURA_C = 'FACTURA_C', 'Factura C'
    REMITO = 'REMITO', 'Remito'
    TICKET = 'TICKET', 'Ticket'
    OTRO = 'OTRO', 'Otro'


class Compra(ConSaldosDerivados, SinBajaFisica, models.Model):
    """Factura de compra recibida del proveedor.

    El comprobante se identifica como lo hace ARCA: tipo + punto de venta
    + número, por proveedor. Así la misma factura no se carga dos veces
    aunque se tipee "0001-00001234" o "1-1234".

    ``importe_otros_conceptos`` son las percepciones, impuestos internos o
    el flete que la factura suma por encima de la mercadería:
    ``total_compra`` = renglones + otros conceptos.
    """

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
        'compras.Proveedor', on_delete=models.RESTRICT,
        db_column='id_proveedor', related_name='compras',
        verbose_name='Proveedor')
    id_orden_compra = models.ForeignKey(
        OrdenCompra, on_delete=models.RESTRICT, db_column='id_orden_compra',
        null=True, blank=True, related_name='compras', verbose_name='Orden')
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='compras', verbose_name='Usuario')
    fecha_compra = models.DateField('Fecha', default=timezone.localdate)
    tipo_comprobante = models.CharField(
        'Comprobante', max_length=10, choices=TipoComprobante.choices,
        default=TipoComprobante.FACTURA_B)
    punto_venta = models.PositiveIntegerField('Punto de venta')
    numero_comprobante = models.PositiveIntegerField('Número')
    importe_otros_conceptos = campo_importe(
        'Otros conceptos (percepciones, flete)', default=Decimal('0'),
        validators=[NO_NEGATIVO])
    total_compra = campo_importe('Total', validators=[NO_NEGATIVO])
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
                    'total_compra > 0 AND importe_otros_conceptos >= 0 AND '
                    'importe_otros_conceptos < total_compra AND '
                    '(fecha_vencimiento_pago IS NULL OR '
                    'fecha_vencimiento_pago >= fecha_compra)'),
                name='ck_compras_valores'),
            CheckEnBase(
                check=CheckSQL(
                    "tipo_comprobante IN ('FACTURA_A','FACTURA_B','FACTURA_C',"
                    "'REMITO','TICKET','OTRO') AND punto_venta BETWEEN 1 AND "
                    '99999 AND numero_comprobante BETWEEN 1 AND 99999999'),
                name='ck_compras_comprobante'),
            models.UniqueConstraint(
                fields=['id_proveedor', 'tipo_comprobante', 'punto_venta',
                        'numero_comprobante'],
                name='uq_compras_comprobante'),
        ]

    def __str__(self):
        return f'{self.get_tipo_comprobante_display()} {self.numero_formateado} · {self.id_proveedor}'

    @property
    def numero_formateado(self):
        """0001-00001234, como figura impreso."""
        return f'{self.punto_venta:04d}-{self.numero_comprobante:08d}'

    @property
    def pagado(self):
        """Lo ya imputado por egresos vivos (no rechazados)."""
        from django.db.models import Sum

        return self.egresos.exclude(estado_pago='RECHAZADO').aggregate(
            t=Sum('monto'))['t'] or Decimal('0')

    @property
    def saldo_pendiente(self):
        return self.total_compra - self.pagado


class DetalleCompra(models.Model):
    """Renglón de la factura de compra."""

    id_detalle_compra = models.AutoField('ID', primary_key=True)
    id_compra = models.ForeignKey(
        Compra, on_delete=models.CASCADE, db_column='id_compra',
        related_name='detalles', verbose_name='Compra')
    id_producto = models.ForeignKey(
        'stock.Producto', on_delete=models.RESTRICT,
        db_column='id_producto', related_name='renglones_compra',
        verbose_name='Producto')
    cantidad_recibida = campo_cantidad('Cantidad recibida')
    precio_costo_unitario = campo_importe('Costo unitario',
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

    @property
    def subtotal(self):
        return (self.cantidad_recibida * self.precio_costo_unitario).quantize(
            Decimal('0.01'))


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
        'compras.TipoEgreso', on_delete=models.RESTRICT,
        db_column='id_tipo_egreso', related_name='egresos',
        verbose_name='Tipo de egreso')
    id_proveedor = models.ForeignKey(
        'compras.Proveedor', on_delete=models.RESTRICT,
        db_column='id_proveedor', null=True, blank=True,
        related_name='egresos', verbose_name='Proveedor')
    destinatario = models.CharField(
        'Destinatario', max_length=60, null=True, blank=True)
    id_compra = models.ForeignKey(
        Compra, on_delete=models.RESTRICT, db_column='id_compra',
        null=True, blank=True, related_name='egresos', verbose_name='Compra')
    id_medio_pago = models.ForeignKey(
        'ventas.MedioPago', on_delete=models.RESTRICT,
        db_column='id_medio_pago', null=True, blank=True,
        related_name='egresos', verbose_name='Medio de pago')
    monto = campo_importe('Monto', validators=[NO_NEGATIVO])
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

            self._exigir_que_no_pague_de_mas()

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

    def _exigir_que_no_pague_de_mas(self):
        """La suma de los egresos vivos de una compra no supera su total.

        Se bloquea la compra antes de sumar: dos pagos simultáneos de la
        misma factura no pueden leer el mismo saldo y pasar los dos.
        """
        from django.db.models import Sum

        if not self.id_compra_id or self.estado_pago == 'RECHAZADO':
            return
        compra = Compra.objects.select_for_update().get(pk=self.id_compra_id)
        otros = (Egreso.objects.filter(id_compra=compra)
                 .exclude(estado_pago='RECHAZADO').exclude(pk=self.pk)
                 .aggregate(t=Sum('monto'))['t'] or Decimal('0'))
        if otros + self.monto > compra.total_compra:
            saldo = compra.total_compra - otros
            raise ValidationError({'monto': (
                f'La compra {compra.numero_formateado} tiene un saldo de '
                f'{pesos(saldo)}: no se le puede imputar {pesos(self.monto)}.')})

    def clean(self):
        """A quién se le paga: un proveedor o un destinatario, uno solo.

        Es la regla ``ck_egresos_acreedor`` de la base, dicha en castellano
        antes de chocar contra ella: sin esto, un pago con los dos campos
        vacíos terminaba en un error 500 en vez de un aviso.
        """
        super().clean()
        if self.destinatario is not None:
            self.destinatario = self.destinatario.strip() or None
        if self.id_proveedor_id is None and self.destinatario is None:
            raise ValidationError(
                'Indicá a quién se le paga: elegí el proveedor o escribí el '
                'destinatario.')
        if self.id_proveedor_id is not None and self.destinatario is not None:
            raise ValidationError(
                'Se le paga a un proveedor o a otro destinatario, no a los '
                'dos: dejá uno solo.')

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
            # El mismo comprobante del mismo proveedor no se paga dos veces,
            # y la misma transferencia no cancela dos egresos.
            models.UniqueConstraint(
                'id_proveedor', ClaveNormalizada('comprobante'),
                condition=models.Q(id_proveedor__isnull=False,
                                   comprobante__isnull=False)
                & ~models.Q(estado_pago='RECHAZADO'),
                name='uq_egresos_comprobante'),
            models.UniqueConstraint(
                'id_medio_pago', ClaveNormalizada('referencia_bancaria'),
                condition=models.Q(referencia_bancaria__isnull=False)
                & ~models.Q(estado_pago='RECHAZADO'),
                name='uq_egresos_referencia'),
        ]

    def __str__(self):
        return f'{self.concepto} · {self.monto}'
