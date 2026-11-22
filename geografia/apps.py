"""Módulo Geografía: provincias y localidades."""
from importlib import import_module

from django.apps import AppConfig


class GeografiaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'geografia'
    verbose_name = 'Geografía'

    def ready(self):
        # Registra sus pantallas de CRUD genérico en nucleo.crud.REGISTRO,
        # que el menú y los permisos leen aunque nadie haya pedido una URL.
        import_module(f'{self.name}.views')
