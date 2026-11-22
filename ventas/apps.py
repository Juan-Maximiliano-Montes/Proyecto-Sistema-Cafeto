"""Módulo Ventas y Caja: medios de pago, cajas, turnos y ventas."""
from importlib import import_module

from django.apps import AppConfig


class VentasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ventas'
    verbose_name = 'Ventas y Caja'

    def ready(self):
        # Registra sus pantallas de CRUD genérico en nucleo.crud.REGISTRO,
        # que el menú y los permisos leen aunque nadie haya pedido una URL.
        import_module(f'{self.name}.views')
