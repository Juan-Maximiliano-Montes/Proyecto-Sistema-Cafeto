"""Módulo Negocio: datos del comercio, parámetros y panel de inicio."""
from django.apps import AppConfig


class NegocioConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'negocio'
    verbose_name = 'Negocio'
