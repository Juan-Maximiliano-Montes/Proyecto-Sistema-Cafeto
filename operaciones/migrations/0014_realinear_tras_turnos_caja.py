"""Deja la base igual al esquema del DER después del renombre a TURNOS_CAJA.

La misma maniobra de la 0009, por los mismos tres motivos:

  * Las claves foráneas que Django rehace al renombrar una columna salen con
    su nombre de hash y sin acciones referenciales; el DER las quiere como
    `fk_ventas_id_turno_caja` con su `ON DELETE` / `ON UPDATE`.
  * Los DEFAULT de columna se vuelven a aplicar; la operación es idempotente.
  * Los triggers y los procedimientos guardan su cuerpo como TEXTO, así que
    el `ALTER TABLE ... RENAME` no los toca: `sp_abrir_caja`,
    `sp_cerrar_caja`, `fn_libro_inmutable` y compañía seguirían hablando de
    una tabla `cajas` que ya no existe. Se reinstala la lógica completa
    desde los guiones de `04 - Base de Datos`, ya reescritos.

Lo comprueba `manage.py comparar_esquema`: tiene que seguir cerrando en 0
diferencias sin justificar.
"""
from django.db import migrations

from operaciones.esquema import (aplicar_claves_foraneas, aplicar_defaults,
                                 desinstalar_logica, instalar_logica)


def realinear(apps, schema_editor):
    aplicar_defaults(schema_editor)
    aplicar_claves_foraneas(schema_editor)
    instalar_logica(schema_editor)


def sin_vuelta_atras(apps, schema_editor):
    """Lo único que se puede deshacer solo es sacar la lógica.

    Las tablas las administra Django y las devuelve al estado anterior la
    migración que corresponda.
    """
    desinstalar_logica(schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0013_fks_al_turno'),
    ]

    operations = [
        migrations.RunPython(realinear, reverse_code=sin_vuelta_atras),
    ]
