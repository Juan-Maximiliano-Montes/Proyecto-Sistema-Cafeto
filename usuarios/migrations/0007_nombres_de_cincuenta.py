"""Apellido y nombre pasan a VARCHAR(50) (DD-54).

30 caracteres alcanzaban justo. Un apellido compuesto argentino
—"Rodríguez de la Fuente"— no entra, y el sistema lo rechazaba sin que
hubiera nada mal en el dato. Se amplía a 50, el mismo largo que se adopta
para CLIENTES, de modo que las dos tablas que guardan personas usen el
mismo criterio.

Ampliar un VARCHAR no toca los datos existentes.
"""

from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0006_alinear_con_el_der'),
    ]

    operations = [
        migrations.AlterField(
            model_name='usuario',
            name='apellido',
            field=models.CharField(max_length=50, validators=[django.core.validators.RegexValidator(message='Sólo se admiten letras y espacios.', regex='^[A-Za-zÁÉÍÓÚáéíóúÑñÜü ]+$')], verbose_name='Apellido'),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='nombre',
            field=models.CharField(max_length=50, validators=[django.core.validators.RegexValidator(message='Sólo se admiten letras y espacios.', regex='^[A-Za-zÁÉÍÓÚáéíóúÑñÜü ]+$')], verbose_name='Nombre'),
        ),
    ]
