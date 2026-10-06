"""CLIENTES separa apellido y nombre, como ya lo hacía USUARIOS (DD-54).

El DER guardaba el cliente en un solo campo ``nombre_cliente`` de 60
caracteres, con el argumento de "unificar criterio con USUARIOS, donde
apellido (30) y nombre (30) suman 60". El número unificaba; el criterio no:
USUARIOS tiene dos columnas y CLIENTES una sola. La consecuencia práctica
es que el listado de clientes no se puede ordenar por apellido, que es como
se busca a una persona en el mostrador.

Ahora las dos tablas guardan personas igual: ``apellido`` y ``nombre``
separados, de 50 cada uno.

El reparto de los datos que ya estaban se hace con una regla simple y se
deja dicho cuál es: la PRIMERA palabra es el nombre y el resto el apellido,
que es como se escriben los nombres en castellano ("Lucía Fernández").
Cuando hay una sola palabra no hay nada que repartir y se copia en los dos
campos, para no dejar ninguno vacío; esos casos quedan a la vista para
corregirlos desde la pantalla.
"""
import cafeto.restricciones
from django.db import migrations, models


def repartir_nombre(apps, schema_editor):
    Cliente = apps.get_model('operaciones', 'Cliente')
    for cliente in Cliente.objects.all():
        partes = (cliente.nombre_cliente or '').split()
        if len(partes) >= 2:
            cliente.nombre_cliente = partes[0]
            cliente.apellido_cliente = ' '.join(partes[1:])[:50]
        elif partes:
            cliente.nombre_cliente = partes[0]
            cliente.apellido_cliente = partes[0]
        else:
            cliente.nombre_cliente = 'Sin nombre'
            cliente.apellido_cliente = 'Sin apellido'
        cliente.save(update_fields=['nombre_cliente', 'apellido_cliente'])

    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')


def volver_a_juntar(apps, schema_editor):
    Cliente = apps.get_model('operaciones', 'Cliente')
    for cliente in Cliente.objects.all():
        cliente.nombre_cliente = (
            '%s %s' % (cliente.nombre_cliente, cliente.apellido_cliente))[:60]
        cliente.save(update_fields=['nombre_cliente'])


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0004_claves_foraneas_del_der'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='cliente',
            options={'ordering': ['apellido_cliente', 'nombre_cliente'], 'verbose_name': 'Cliente', 'verbose_name_plural': 'Clientes'},
        ),
        # El CHECK viejo nombra una columna que está por cambiar: primero sale.
        migrations.RemoveConstraint(
            model_name='cliente',
            name='ck_clientes_textos',
        ),
        # La columna nace admitiendo nulo para poder llenarla; recién después
        # se la exige.
        migrations.AddField(
            model_name='cliente',
            name='apellido_cliente',
            field=models.CharField(max_length=50, null=True, verbose_name='Apellido'),
        ),
        migrations.AlterField(
            model_name='cliente',
            name='nombre_cliente',
            field=models.CharField(max_length=50, verbose_name='Nombre'),
        ),
        migrations.RunPython(repartir_nombre, reverse_code=volver_a_juntar),
        migrations.AlterField(
            model_name='cliente',
            name='apellido_cliente',
            field=models.CharField(max_length=50, verbose_name='Apellido'),
        ),
        migrations.AddConstraint(
            model_name='cliente',
            constraint=cafeto.restricciones.CheckEnBase(check=cafeto.restricciones.CheckSQL("trim(apellido_cliente) <> '' AND trim(nombre_cliente) <> '' AND trim(qr_cliente) <> ''"), name='ck_clientes_textos'),
        ),
    ]
