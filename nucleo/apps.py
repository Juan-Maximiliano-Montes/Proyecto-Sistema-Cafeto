"""Módulo Núcleo: componentes compartidos por todos los módulos."""
from django.apps import AppConfig


class NucleoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'nucleo'
    verbose_name = 'Núcleo'
