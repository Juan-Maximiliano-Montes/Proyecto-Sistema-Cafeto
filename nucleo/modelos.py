"""Piezas de modelo que comparten todos los módulos.

* ``sello_usuario_modificacion`` / ``sello_fecha_modificacion``: los
  atributos de control del DER (DD-17).
* ``Maestra``: base abstracta de los catálogos con baja lógica.
* ``campo_cantidad`` / ``campo_importe``: los dos tipos numéricos del
  sistema. Las cantidades llevan TRES decimales (se vende por peso: 0,350
  kg) y los importes, dos.

No importa ningún modelo de otro módulo: la referencia al usuario va como
texto (``'seguridad.Usuario'``), así el núcleo queda en la capa más baja y
cualquier módulo lo puede usar sin dependencias circulares.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import models
from django.utils import timezone

from nucleo.reglas import (
    ConSelloDeModificacion,
    ManagerSinBajaFisica,
    SinBajaFisica,
)


def sello_usuario_modificacion(related_name):
    """Campo ``usuario_modificacion`` tal como lo define el DER (DD-17).

    Es un INTEGER suelto en la base: NO lleva clave foránea, porque el
    sello no se traza y no tiene que frenar nada. Del lado de Django lo
    declaramos igual como ForeignKey —para poder escribir
    ``fila.usuario_modificacion.nombre``— pero con ``db_constraint=False``,
    que le dice a Django que no cree la restricción en PostgreSQL.
    """
    return models.ForeignKey(
        'seguridad.Usuario',
        db_column='usuario_modificacion',
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        null=True, blank=True, editable=False,
        related_name=related_name,
        verbose_name='Modificado por',
    )


def sello_fecha_modificacion():
    """``fecha_modificacion TIMESTAMPTZ NOT NULL`` (DD-17).

    No lleva ``auto_now``: quien pisa la fecha en cada UPDATE es el
    ``save()`` del modelo, vía el mixin ``ConSelloDeModificacion``. Acá
    sólo se aporta el valor del alta.
    """
    return models.DateTimeField(
        'Última modificación', default=timezone.now, editable=False)


def campo_cantidad(verbose_name, **opciones):
    """Cantidad de mercadería: NUMERIC(12,3).

    Tres decimales alcanzan para el gramo (0,001 kg) y el mililitro. Un
    producto que no se vende fraccionado igual exige cantidades enteras:
    eso lo controla el libro de stock, que sabe de qué producto se trata.
    """
    return models.DecimalField(verbose_name, max_digits=12, decimal_places=3,
                               **opciones)


def campo_importe(verbose_name, **opciones):
    """Importe en pesos: NUMERIC(12,2)."""
    return models.DecimalField(verbose_name, max_digits=12, decimal_places=2,
                               **opciones)


CENTAVO = Decimal('0.01')
MILESIMA = Decimal('0.001')


def redondear_importe(valor, paso=CENTAVO):
    """Redondeo comercial (mitad para arriba) al paso pedido: $0,01 o $1."""
    paso = Decimal(paso)
    return (Decimal(valor) / paso).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP) * paso


def es_entero(cantidad):
    return Decimal(cantidad) == Decimal(cantidad).to_integral_value()


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
