from django.apps import AppConfig


class MaestrosConfig(AppConfig):
    """Datos maestros del sistema (DFD 10 — Mantener datos maestros)."""
    default_auto_field = 'django.db.models.AutoField'
    name = 'maestros'
    verbose_name = 'Datos maestros'
