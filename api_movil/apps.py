"""API de la app móvil «Sistema Cafeto Móvil»: catálogo, consulta y ABM livianos."""
from django.apps import AppConfig


class ApiMovilConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api_movil'
    verbose_name = 'API de la app móvil'
