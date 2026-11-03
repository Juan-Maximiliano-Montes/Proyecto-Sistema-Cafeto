"""Pantallas del módulo Stock, una por proceso.

    catalogos   productos, rubros y marcas (CRUD genérico) y el buscador
    alertas     bajo mínimo, por vencer y el sector de ofertas
    ajustes     conteos, mermas y traspasos, y su aprobación
    produccion  las tandas de lo que se fabrica en el local

Se importa ``catalogos`` acá porque registra sus CRUD al cargar la app.
"""
from .catalogos import (  # noqa: F401
    MARCAS,
    POR_PAGINA,
    PRODUCTOS,
    RUBROS,
    buscar_productos,
)
