"""Módulo Geografía: provincias y localidades.

Son un catálogo de referencia: una provincia no deja de existir, y
borrarla lógicamente sólo escondería direcciones históricas. Por eso NO
tienen baja lógica y su CRUD es alta y modificación (regla 4.4.a).
"""

import re

from django.core.exceptions import ValidationError
from django.db import models

from nucleo.restricciones import CheckEnBase, CheckSQL, ClaveNormalizada
from nucleo.validaciones import (
    exigir_nombre_unico,
    texto_de_catalogo,
    validar_codigo_postal,
)


# ===========================================================================
#  UBICACIONES
# ===========================================================================
class Provincia(models.Model):
    """Jurisdicción argentina. Catálogo de referencia (DD-16)."""

    id_provincia = models.AutoField('ID', primary_key=True)
    nombre_provincia = models.CharField('Provincia', max_length=60)

    class Meta:
        db_table = 'provincias'
        verbose_name = 'Provincia'
        verbose_name_plural = 'Provincias'
        ordering = ['nombre_provincia']
        constraints = [
            CheckEnBase(
                check=CheckSQL("trim(nombre_provincia) <> ''"),
                name='ck_provincias_textos'),
            models.UniqueConstraint(
                ClaveNormalizada('nombre_provincia'),
                name='uq_provincias_nombre_norm'),
        ]

    def __str__(self):
        return self.nombre_provincia

    def clean(self):
        super().clean()
        self.nombre_provincia = texto_de_catalogo(
            self.nombre_provincia, 3, 60, 'El nombre de la provincia',
            exige_letra=True)
        if re.search(r'\d', self.nombre_provincia):
            raise ValidationError({
                'nombre_provincia': 'El nombre de la provincia no puede llevar números.',
            })
        exigir_nombre_unico(self, 'nombre_provincia',
                            etiqueta='una provincia')


class Localidad(models.Model):
    """Localidad perteneciente a una provincia."""

    id_localidad = models.AutoField('ID', primary_key=True)
    id_provincia = models.ForeignKey(
        Provincia, on_delete=models.RESTRICT, db_column='id_provincia',
        verbose_name='Provincia', related_name='localidades')
    nombre_localidad = models.CharField('Localidad', max_length=80)
    codigo_postal = models.CharField(
        'Código postal', max_length=8, null=True, blank=True)

    class Meta:
        db_table = 'localidades'
        verbose_name = 'Localidad'
        verbose_name_plural = 'Localidades'
        ordering = ['nombre_localidad']
        constraints = [
            CheckEnBase(
                check=CheckSQL("trim(nombre_localidad) <> ''"),
                name='ck_localidades_textos'),
            # Hay localidades homónimas en provincias distintas: el nombre
            # es único DENTRO de su provincia (DD-50).
            models.UniqueConstraint(
                'id_provincia', ClaveNormalizada('nombre_localidad'),
                name='uq_localidades_nombre_norm'),
        ]

    def __str__(self):
        return f'{self.nombre_localidad} ({self.id_provincia.nombre_provincia})'

    def clean(self):
        super().clean()
        self.nombre_localidad = texto_de_catalogo(
            self.nombre_localidad, 2, 80, 'El nombre de la localidad',
            exige_letra=True)
        self.codigo_postal = validar_codigo_postal(self.codigo_postal)
        # Una localidad es única DENTRO de su provincia: "Rosario" existe
        # en Santa Fe y podría existir en otra.
        exigir_nombre_unico(self, 'nombre_localidad',
                            ambito=('id_provincia',),
                            etiqueta='una localidad en esa provincia')
