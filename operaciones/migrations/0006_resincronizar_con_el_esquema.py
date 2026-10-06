"""Vuelve a alinear con 01_esquema.sql lo que Django no sabe declarar.

Las migraciones 0002 y 0004 ya habían puesto los DEFAULT de columna y las
claves foráneas con el nombre y las acciones del DER. Al entrar MARCAS y la
presentación del producto (DD-55) aparecieron objetos que esas migraciones
no podían conocer:

  * ``marcas.borrado_logico`` y ``marcas.fecha_modificacion`` necesitan su
    DEFAULT en la base, porque Django los aplica en Python y los
    procedimientos de ``04_procedimientos.sql`` insertan en SQL puro.
  * ``productos.id_marca`` nació con la clave foránea que genera Django:
    nombre de hash y sin acciones referenciales.

En vez de parchear lo nuevo a mano se repite la operación completa: leer el
esquema y dejar la base como él dice. Es idempotente, así que se puede
volver a correr cada vez que el DER crezca.

El comando ``manage.py comparar_esquema`` es el que comprueba que esto haya
quedado bien: tiene que seguir cerrando en 0 diferencias sin justificar.
"""
from django.db import migrations

from operaciones.esquema import aplicar_claves_foraneas, aplicar_defaults


def resincronizar(apps, schema_editor):
    aplicar_defaults(schema_editor)
    aplicar_claves_foraneas(schema_editor)


def sin_vuelta_atras(apps, schema_editor):
    """No hay nada que deshacer.

    Lo que esta migración hace es poner la base igual al esquema del DER.
    Revertirla sería desalinearla a propósito; si se quiere volver atrás, se
    revierten las migraciones que crearon las columnas.
    """
    return


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0005_cliente_apellido_y_nombre'),
        ('maestros', '0004_marcas_y_presentacion'),
    ]

    operations = [
        migrations.RunPython(resincronizar, reverse_code=sin_vuelta_atras),
    ]
