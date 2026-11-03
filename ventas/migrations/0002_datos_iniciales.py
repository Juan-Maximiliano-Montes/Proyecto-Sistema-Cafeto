"""Catálogos de puesta en marcha de Ventas: medios de pago y la caja.

No se puede cobrar sin un medio de pago, ni abrir caja sin una caja
registradora.

**Efectivo** es el medio de pago por defecto del sistema. Nace con recargo
0 y no se puede dar de baja: el vuelto y el arqueo de caja se calculan
sobre el importe cobrado (DD-45). Lo defiende
``MedioPago.motivos_que_impiden_la_baja()``.

Las altas son IDEMPOTENTES (``get_or_create`` por la clave natural).
"""
from decimal import Decimal

from django.db import migrations

# (nombre, recargo, entra al cajón). El recargo nace en 0: es un dato del
# negocio y lo fija la Administradora cuando acuerda un plan. Sólo el
# efectivo entra al cajón y cuenta en el arqueo.
MEDIOS_PAGO = [
    ('Efectivo', Decimal('0'), True),
    ('Transferencia', Decimal('0'), False),
    ('Tarjeta de débito', Decimal('0'), False),
    ('Tarjeta de crédito', Decimal('0'), False),
    ('QR', Decimal('0'), False),
]

# Un cajón por sector: la Cafetería y el Minisuper cobran por separado
# (cada uno con su turno y su arqueo).
CAJAS_REGISTRADORAS = ['Caja Cafetería', 'Caja Minisuper']


def cargar(apps, schema_editor):
    MedioPago = apps.get_model('ventas', 'MedioPago')
    CajaRegistradora = apps.get_model('ventas', 'CajaRegistradora')
    for nombre, recargo, afecta_caja in MEDIOS_PAGO:
        MedioPago.objects.get_or_create(
            nombre_medio_pago=nombre,
            defaults={'recargo_porcentaje': recargo,
                      'afecta_caja': afecta_caja})
    for nombre in CAJAS_REGISTRADORAS:
        CajaRegistradora.objects.get_or_create(
            nombre_caja_registradora=nombre)


def borrar(apps, schema_editor):
    """Deshace la carga (ver el borrar de seguridad/0003)."""
    for modelo in ('CajaRegistradora', 'MedioPago'):
        apps.get_model('ventas', modelo)._base_manager.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('ventas', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
