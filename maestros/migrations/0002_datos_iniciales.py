"""Catálogos de puesta en marcha de los datos maestros.

Son los datos de referencia sin los cuales el sistema no puede operar: no
se puede cobrar sin un medio de pago, ni abrir caja sin una caja
registradora, ni dar de alta un producto sin un rubro y sin una marca.
Todo lo demás se carga desde las pantallas.

Dos cosas que conviene saber de lo que se carga acá:

  * **Efectivo** es el medio de pago por defecto del sistema. Nace con
    recargo 0 y no se puede dar de baja: el vuelto y el arqueo de caja se
    calculan sobre el importe cobrado (DD-45). Lo defiende
    ``MedioPago.motivos_que_impiden_la_baja()``.
  * **Bronze** es la categoría de ingreso (umbral 0): es la puerta por la
    que entra todo cliente nuevo, así que siempre tiene que existir una
    con umbral 0. Lo defiende ``CategoriaFidelizacion.clean()``.
  * **Sin marca** es la marca de los productos a granel y de elaboración
    propia. La identidad de un producto es la terna (marca, contenido,
    unidad), DD-55, así que todo producto necesita una marca —incluso el
    café que se vende suelto.

Las altas son IDEMPOTENTES (``get_or_create`` por la clave natural), así
que volver a aplicar la migración no duplica nada y respeta lo que ya
esté cargado.
"""
from decimal import Decimal

from django.db import migrations

# (nombre, recargo). Nacen todos en 0: el recargo es un dato del negocio y
# lo fija la Administradora desde la pantalla cuando acuerda un plan.
MEDIOS_PAGO = [
    ('Efectivo', Decimal('0')),
    ('Transferencia', Decimal('0')),
    ('Tarjeta de débito', Decimal('0')),
    ('Tarjeta de crédito', Decimal('0')),
    ('QR', Decimal('0')),
]

# El local arranca con el único mostrador de cobro que tiene hoy (DD-42).
CAJAS_REGISTRADORAS = ['Caja Mostrador']

RUBROS = [
    ('Cafetería', 'Cafés, infusiones y bebidas preparadas'),
    ('Minisuper', 'Almacén, bebidas envasadas y artículos de conveniencia'),
]

MARCAS = [
    ('Sin marca', 'Productos a granel y de elaboración propia'),
]

TIPOS_EGRESO = [
    ('Proveedores', 'Pago de facturas de mercadería'),
    ('Servicios', 'Energía, agua, gas e internet'),
    ('Impuestos', 'Ingresos Brutos, IVA, Monotributo / ARCA'),
    ('Sueldos', 'Sueldos y retiros del personal'),
    ('Gastos varios', 'Mantenimiento, papelería y otros gastos menores'),
]

# (nombre, puntos_minimos, importe_por_punto). Desde DD-49 la regla se
# expresa en PESOS POR PUNTO, que es como la piensa la dueña: la categoría
# de ingreso da 1 punto cada $100 y cada escalón pide menos pesos por el
# mismo punto.
CATEGORIAS = [
    ('Bronze', 0, Decimal('100.00')),
    ('Silver', 1000, Decimal('83.33')),
    ('Gold', 5000, Decimal('66.67')),
    ('Platinum', 15000, Decimal('50.00')),
]


def cargar(apps, schema_editor):
    MedioPago = apps.get_model('maestros', 'MedioPago')
    CajaRegistradora = apps.get_model('maestros', 'CajaRegistradora')
    Rubro = apps.get_model('maestros', 'Rubro')
    Marca = apps.get_model('maestros', 'Marca')
    TipoEgreso = apps.get_model('maestros', 'TipoEgreso')
    Categoria = apps.get_model('maestros', 'CategoriaFidelizacion')

    for nombre, recargo in MEDIOS_PAGO:
        MedioPago.objects.get_or_create(
            nombre_medio_pago=nombre,
            defaults={'recargo_porcentaje': recargo})

    for nombre in CAJAS_REGISTRADORAS:
        CajaRegistradora.objects.get_or_create(
            nombre_caja_registradora=nombre)

    for nombre, descripcion in RUBROS:
        Rubro.objects.get_or_create(
            nombre_rubro=nombre,
            defaults={'descripcion_rubro': descripcion})

    for nombre, descripcion in MARCAS:
        Marca.objects.get_or_create(
            nombre_marca=nombre,
            defaults={'descripcion_marca': descripcion})

    for nombre, descripcion in TIPOS_EGRESO:
        TipoEgreso.objects.get_or_create(
            nombre_tipo_egreso=nombre,
            defaults={'descripcion_tipo_egreso': descripcion})

    for nombre, minimos, importe in CATEGORIAS:
        Categoria.objects.get_or_create(
            nombre_categoria=nombre,
            defaults={'puntos_minimos': minimos,
                      'importe_por_punto': importe})


def borrar(apps, schema_editor):
    """Deshace la carga (ver el borrar de usuarios/0002)."""
    for modelo in ('CategoriaFidelizacion', 'TipoEgreso', 'Marca', 'Rubro',
                   'CajaRegistradora', 'MedioPago'):
        apps.get_model('maestros', modelo)._base_manager.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('maestros', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
