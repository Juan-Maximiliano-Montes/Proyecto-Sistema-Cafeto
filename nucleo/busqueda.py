"""Búsqueda de texto como la entiende una persona.

"coca 500" encuentra "Coca-Cola 500 ml"; "cafe" encuentra "Café con
leche"; "LOPEZ" encuentra "López". Las dos puntas pasan por la misma
``f_clave_normalizada`` con la que la base compara los duplicados
(DD-50): minúsculas, sin acentos, la puntuación como espacio.

Cada palabra tiene que aparecer en ALGUNO de los campos (Y entre
palabras, O entre campos): así se puede escribir la marca y la
presentación juntas sin tener que saber en qué columna está cada una.
"""
from django.db.models import Q, TextField
from django.db.models.functions import Cast

from nucleo.restricciones import ClaveNormalizada
from nucleo.validaciones import clave_normalizada

MAXIMO_DE_PALABRAS = 6


def filtrar_por_texto(consulta, campos, texto):
    palabras = clave_normalizada(texto or '').split()[:MAXIMO_DE_PALABRAS]
    if not palabras or not campos:
        return consulta
    alias = {}
    for indice, campo in enumerate(campos):
        nombre = f'_busqueda_{indice}'
        alias[nombre] = ClaveNormalizada(Cast(campo, output_field=TextField()))
    consulta = consulta.alias(**alias)
    for palabra in palabras:
        condicion = Q()
        for nombre in alias:
            condicion |= Q(**{f'{nombre}__contains': palabra})
        consulta = consulta.filter(condicion)
    return consulta
