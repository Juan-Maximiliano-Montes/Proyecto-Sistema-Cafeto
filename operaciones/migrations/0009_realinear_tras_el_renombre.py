"""Deja la base igual al esquema del DER después del renombre.

Tres cosas quedan desalineadas cuando Django renombra una tabla y una
columna, y ninguna la sabe arreglar el autodetector:

  * La clave foránea del turno la había creado ``operaciones/0004`` con el
    nombre y las acciones del DER (``fk_cajas_id_puesto_caja``). Al
    renombrar la columna, Django la rehace con su propio nombre de hash y
    sin acciones referenciales.
  * Los DEFAULT de ``cajas_registradoras`` (``borrado_logico``,
    ``fecha_modificacion``) viajan con el renombre, pero se vuelven a
    aplicar por las dudas: la operación es idempotente.
  * Los triggers y los procedimientos nombran ``puestos_caja`` en su
    cuerpo. PostgreSQL guarda el cuerpo de una función como texto, así que
    el ``ALTER TABLE ... RENAME`` no lo toca: ``fn_sello_modificacion``,
    ``fn_sin_baja_fisica`` y ``sp_abrir_caja`` seguirían hablando de una
    tabla que ya no existe. Se reinstala la lógica completa desde los
    guiones de ``04 - Base de Datos``, que ya quedaron reescritos.

Es la misma maniobra de las migraciones 0006 y 0007, y la comprueba
``manage.py comparar_esquema``: tiene que seguir cerrando en 0 diferencias
sin justificar.
"""
from django.db import migrations

from operaciones.esquema import (aplicar_claves_foraneas, aplicar_defaults,
                                 desinstalar_logica, instalar_logica)


def realinear(apps, schema_editor):
    aplicar_defaults(schema_editor)
    aplicar_claves_foraneas(schema_editor)
    # Historica: los guiones que lee son los de HOY, y esta migracion
    # corre en su punto del historial. Lo que nombre algo que el DER
    # renombro despues se saltea; la migracion que introdujo ese
    # renombre reinstala todo, y esa si corre en modo estricto.
    instalar_logica(schema_editor, tolerante=True)


def sin_vuelta_atras(apps, schema_editor):
    """Revertir sería desalinear la base a propósito.

    Lo único que se puede deshacer sin dejar la base a medias es sacar la
    lógica; las tablas las administra Django y las devuelve al estado
    anterior la migración que corresponda.
    """
    desinstalar_logica(schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ('operaciones', '0008_caja_apunta_a_caja_registradora'),
        ('maestros', '0007_permisos_de_cajas_registradoras'),
    ]

    operations = [
        migrations.RunPython(realinear, reverse_code=sin_vuelta_atras),
    ]
