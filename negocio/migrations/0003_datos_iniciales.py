"""La fila única de PARAMETROS_NEGOCIO, con los datos del local.

Es la fila que leen los reportes (razón social, CUIT) y la operación
(redondeo, tolerancia del arqueo). Nace con los valores de Cafeto Las
Lomitas; el CUIT lo completa la dueña desde la pantalla de Parámetros.

Idempotente: si la fila ya existe, no la toca.
"""
from decimal import Decimal

from django.db import migrations


def cargar(apps, schema_editor):
    Parametros = apps.get_model('negocio', 'ParametrosNegocio')
    Localidad = apps.get_model('geografia', 'Localidad')
    quijano = Localidad.objects.filter(nombre_localidad='Campo Quijano').first()
    Parametros.objects.get_or_create(
        id_parametros=1,
        defaults={
            'razon_social': 'Cafeto Las Lomitas',
            'nombre_fantasia': 'Cafeto',
            'domicilio': 'Ruta Nacional 51',
            'id_localidad': quijano,
            'redondeo_ventas': Decimal('1.00'),
            'tolerancia_arqueo': Decimal('500.00'),
            'fondo_caja_sugerido': Decimal('10000.00'),
            'dias_para_cambios': 30,
            'dias_aviso_vencimiento': 7,
        })


def borrar(apps, schema_editor):
    apps.get_model('negocio', 'ParametrosNegocio')._base_manager.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('negocio', '0002_initial'),
        ('geografia', '0002_datos_iniciales'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
