"""Módulo Compras y Proveedores: proveedores, órdenes, compras y egresos."""
from importlib import import_module

from django.apps import AppConfig


class ComprasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'compras'
    verbose_name = 'Compras y Proveedores'

    def ready(self):
        # Registra sus pantallas de CRUD genérico en nucleo.crud.REGISTRO,
        # que el menú y los permisos leen aunque nadie haya pedido una URL.
        import_module(f'{self.name}.importacion')
        import_module(f'{self.name}.views')
