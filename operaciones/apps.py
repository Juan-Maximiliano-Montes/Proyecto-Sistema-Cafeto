from django.apps import AppConfig


class OperacionesConfig(AppConfig):
    """Tablas operativas: caja, ventas, compras, stock, puntos y auditoría."""
    default_auto_field = 'django.db.models.AutoField'
    name = 'operaciones'
    verbose_name = 'Operaciones'
