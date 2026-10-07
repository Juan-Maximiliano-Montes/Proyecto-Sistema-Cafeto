"""Piezas para declarar en los modelos las restricciones que el ORM no sabe
escribir solo.

Django es el dueño del esquema: las tablas, los CHECK, los UNIQUE y los
índices los crean sus migraciones, y no hay ningún guion SQL aparte que
haya que mantener en paralelo. Lo que sigue haciendo falta es una forma de
escribir, DESDE el modelo, las condiciones que el ORM no puede expresar.

El ORM alcanza para los casos simples (``Q(campo__gt=0)``), pero no para
condiciones como ``trim(nombre) <> ''`` o ``f_clave_normalizada(nombre)``:
no tiene forma de escribir ``<>`` ni de llamar a una función propia dentro
de un CHECK. Estas dos clases cubren ese hueco sin renunciar a que Django
siga administrando las migraciones: la restricción se declara en el
``Meta`` del modelo, viaja en una migración y se puede revertir como
cualquier otra.

Qué NO son
----------
No son la validación del sistema. Las reglas de negocio se validan en
Python —``clean()`` de cada modelo, mixins de ``cafeto/reglas.py``,
servicios de ``operaciones/``— y se muestran en castellano. Estas
restricciones son la última red: lo que la base rechaza si algo llegara a
escribirse sin pasar por el ORM.
"""
from django.db import models
from django.utils.deconstruct import deconstructible


@deconstructible
class CheckSQL(models.Expression):
    """Condición booleana escrita tal cual va a la base.

    Se usa como ``check=`` de un ``CheckConstraint`` cuando la condición no
    se puede expresar con ``Q`` sin cambiarle el texto::

        models.CheckConstraint(
            check=CheckSQL("trim(nombre_perfil) <> ''"),
            name='ck_perfiles_textos',
        )

    El texto va tal cual a PostgreSQL, con el nombre que le pone el
    ``Meta`` del modelo. Nombra las COLUMNAS de la base
    (``nombre_perfil``), no los atributos del modelo, porque un CHECK vive
    dentro de la tabla.

    ``conditional = True`` es lo que le avisa a Django que esta expresión
    devuelve un booleano y puede ir donde iría un ``Q``.
    """

    # Marca de "esto es una condición": sin esto, CheckConstraint la rechaza.
    conditional = True

    def __init__(self, sql):
        super().__init__(output_field=models.BooleanField())
        self.sql = sql

    def as_sql(self, compiler, connection):
        # Sin parámetros: el texto ya viene completo desde el esquema.
        return self.sql, []

    def __repr__(self):
        return f'CheckSQL({self.sql!r})'

    def __eq__(self, otro):
        return isinstance(otro, CheckSQL) and otro.sql == self.sql

    def __hash__(self):
        return hash((self.__class__, self.sql))


class CheckEnBase(models.CheckConstraint):
    """CheckConstraint que se controla en la BASE, no en ``full_clean()``.

    Desde Django 4.1, ``full_clean()`` valida también los CheckConstraint:
    arma una consulta sintética ``SELECT 1 WHERE <condición>`` con los
    valores de la instancia puestos como literales. Eso funciona cuando la
    condición es un ``Q``, porque Django sabe reemplazar cada campo por su
    valor; no funciona con un ``CheckSQL``, que nombra las columnas tal
    cual y necesita la tabla.

    Esta subclase apaga esa validación. No se pierde nada: la condición
    sigue creada en PostgreSQL y rechaza cualquier fila que la viole
    —venga del ORM, del shell o de un procedimiento—, que es justamente el
    rol de la tercera capa. El mensaje claro para el usuario lo da la capa
    1 (el formulario) y la capa 2 (``clean()`` del modelo), que es donde
    corresponde.
    """

    def validate(self, model, instance, exclude=None, using=None):
        return


class ClaveNormalizada(models.Func):
    """Llama a ``f_clave_normalizada(texto)``, la función de la base (DD-50).

    Compara los nombres de catálogo como los compara una persona: sin
    distinguir mayúsculas, acentos ni espacios repetidos. Se usa dentro de
    un ``UniqueConstraint`` para reproducir los índices únicos por clave
    normalizada::

        models.UniqueConstraint(
            ClaveNormalizada('nombre'),
            name='uq_perfiles_nombre_norm',
        )

    La función la crea la primera migración de ``usuarios`` (junto con la
    extensión ``unaccent``), antes de cualquier índice que la use.
    """

    function = 'f_clave_normalizada'
    arity = 1
    output_field = models.TextField()


def clave_normalizada(texto):
    """Misma normalización que ``f_clave_normalizada``, pero en Python.

    Sirve para buscar duplicados desde el formulario y poder dar un mensaje
    claro ("Ya existe el rubro 'Café'") en lugar de dejar que explote el
    índice único. Las dos implementaciones tienen que coincidir: la de
    Python avisa, la de PostgreSQL garantiza.
    """
    import re
    import unicodedata

    if texto is None:
        return None
    sin_bordes = str(texto).strip()
    un_espacio = re.sub(r'\s+', ' ', sin_bordes)
    # unaccent descompone y descarta los diacríticos; NFKD hace lo mismo.
    sin_tildes = ''.join(
        c for c in unicodedata.normalize('NFKD', un_espacio)
        if not unicodedata.combining(c)
    )
    return sin_tildes.lower()
