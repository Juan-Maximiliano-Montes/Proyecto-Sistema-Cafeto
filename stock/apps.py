"""Módulo Stock e Inventario: productos, lotes, movimientos y ajustes de stock."""
from django.apps import AppConfig


class StockConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'stock'
    verbose_name = 'Stock e Inventario'

    def ready(self):
        # Registra sus pantallas de CRUD genérico en nucleo.crud.REGISTRO,
        # que el menú y los permisos leen aunque nadie haya pedido una URL.
        from . import importacion, views  # noqa: F401
