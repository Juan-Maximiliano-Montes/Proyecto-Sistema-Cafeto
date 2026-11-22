"""Módulo Clientes y Fidelización: categorías, beneficios, clientes y puntos."""
from importlib import import_module

from django.apps import AppConfig


class FidelizacionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'fidelizacion'
    verbose_name = 'Clientes y Fidelización'

    def ready(self):
        # Registra sus pantallas de CRUD genérico en nucleo.crud.REGISTRO,
        # que el menú y los permisos leen aunque nadie haya pedido una URL.
        import_module(f'{self.name}.views')
