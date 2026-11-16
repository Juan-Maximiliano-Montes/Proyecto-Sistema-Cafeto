"""La marca reservada «Genérica» (DD-86). Alta idempotente."""
from django.db import migrations

NOMBRE = 'Genérica'


def cargar(apps, schema_editor):
    Marca = apps.get_model('stock', 'Marca')
    #  El UNIQUE de marcas no distingue acentos ni mayúsculas: si ya había
    #  una "generica" cargada a mano, es ésa (y se le fija el nombre).
    marca = (Marca._base_manager.filter(nombre_marca__iexact=NOMBRE).first()
             or Marca._base_manager.filter(nombre_marca__iexact='Generica').first())
    if marca is None:
        Marca._base_manager.create(
            nombre_marca=NOMBRE,
            descripcion_marca='Productos que todavía no tienen la marca cargada')
        return
    marca.nombre_marca = NOMBRE
    marca.borrado_logico = False
    marca.save(update_fields=['nombre_marca', 'borrado_logico'])


def nada(apps, schema_editor):
    """No se borra al deshacer: puede tener productos."""


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0006_codigos_iva_y_genericos'),
    ]

    operations = [
        migrations.RunPython(cargar, nada),
    ]
