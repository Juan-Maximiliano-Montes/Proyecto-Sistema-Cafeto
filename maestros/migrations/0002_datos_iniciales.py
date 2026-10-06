"""Catálogos de puesta en marcha (los mismos de 05_datos_iniciales.sql).

Son los datos de referencia sin los cuales el sistema no puede operar: no
se puede cobrar sin un medio de pago, ni abrir caja sin un puesto, ni dar
de alta un producto sin un rubro. Todo lo demás se carga desde las
pantallas.

Por qué acá y no ejecutando 05_datos_iniciales.sql: esa migración se
correría de nuevo en cada base y duplicaría las filas. Estas altas son
IDEMPOTENTES —``get_or_create`` por la clave natural—, así que se pueden
volver a aplicar sin romper nada y respetan lo que ya exista cargado.
"""
from decimal import Decimal

from django.db import migrations


MEDIOS_PAGO = [
    # (nombre, recargo). Nacen todos en 0: el recargo es un dato del
    # negocio y lo fija la Administradora desde la pantalla cuando acuerda
    # un plan. En Efectivo tiene que quedar siempre en 0, porque el vuelto
    # y el arqueo se calculan sobre el importe cobrado (DD-45).
    ('Efectivo', Decimal('0')),
    ('Transferencia', Decimal('0')),
    ('Tarjeta de débito', Decimal('0')),
    ('Tarjeta de crédito', Decimal('0')),
    ('QR', Decimal('0')),
]

# El local arranca con el único mostrador de cobro que tiene hoy (DD-42).
PUESTOS_CAJA = ['Caja Mostrador']

TIPOS_EGRESO = [
    ('Proveedores', 'Pago de facturas de mercadería'),
    ('Servicios', 'Energía, agua, gas e internet'),
    ('Impuestos', 'Ingresos Brutos, IVA, Monotributo / ARCA'),
    ('Sueldos', 'Sueldos y retiros del personal'),
    ('Gastos varios', 'Mantenimiento, papelería y otros gastos menores'),
]

RUBROS = [
    ('Cafetería', 'Cafés, infusiones y bebidas preparadas'),
    ('Minisuper', 'Almacén, bebidas envasadas y artículos de conveniencia'),
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
    PuestoCaja = apps.get_model('maestros', 'PuestoCaja')
    TipoEgreso = apps.get_model('maestros', 'TipoEgreso')
    Rubro = apps.get_model('maestros', 'Rubro')
    Categoria = apps.get_model('maestros', 'CategoriaFidelizacion')

    for nombre, recargo in MEDIOS_PAGO:
        MedioPago.objects.get_or_create(
            nombre_medio_pago=nombre,
            defaults={'recargo_porcentaje': recargo})

    for nombre in PUESTOS_CAJA:
        PuestoCaja.objects.get_or_create(nombre_puesto=nombre)

    for nombre, descripcion in TIPOS_EGRESO:
        TipoEgreso.objects.get_or_create(
            nombre_tipo_egreso=nombre,
            defaults={'descripcion_tipo_egreso': descripcion})

    for nombre, descripcion in RUBROS:
        Rubro.objects.get_or_create(
            nombre_rubro=nombre,
            defaults={'descripcion_rubro': descripcion})

    for nombre, minimos, importe in CATEGORIAS:
        Categoria.objects.get_or_create(
            nombre_categoria=nombre,
            defaults={'puntos_minimos': minimos,
                      'importe_por_punto': importe})


def borrar(apps, schema_editor):
    """Deshace la carga.

    Usa ``_base_manager`` y un DELETE directo porque el trigger
    ``trg_sin_baja_fisica`` prohíbe el borrado físico de estas tablas en
    operación normal; al revertir una migración, en cambio, lo que se
    quiere es dejar la base como estaba.
    """
    MedioPago = apps.get_model('maestros', 'MedioPago')
    PuestoCaja = apps.get_model('maestros', 'PuestoCaja')
    TipoEgreso = apps.get_model('maestros', 'TipoEgreso')
    Rubro = apps.get_model('maestros', 'Rubro')
    Categoria = apps.get_model('maestros', 'CategoriaFidelizacion')

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET LOCAL cafeto.pasaje_historico = 'on'")
        MedioPago.objects.filter(
            nombre_medio_pago__in=[n for n, _ in MEDIOS_PAGO]).delete()
        PuestoCaja.objects.filter(nombre_puesto__in=PUESTOS_CAJA).delete()
        TipoEgreso.objects.filter(
            nombre_tipo_egreso__in=[n for n, _ in TIPOS_EGRESO]).delete()
        Rubro.objects.filter(nombre_rubro__in=[n for n, _ in RUBROS]).delete()
        Categoria.objects.filter(
            nombre_categoria__in=[n for n, _, _ in CATEGORIAS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('maestros', '0001_initial'),
        # Después de los DEFAULT: los insert cuentan con ellos.
        ('operaciones', '0002_columna_generada_y_defaults'),
    ]

    operations = [
        migrations.RunPython(cargar, reverse_code=borrar),
    ]
