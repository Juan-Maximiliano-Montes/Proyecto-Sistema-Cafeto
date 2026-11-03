"""Rutas del módulo Negocio: parámetros del local y reportes."""
from django.urls import path

from .views import parametros, reportes

app_name = 'negocio'

urlpatterns = [
    path('parametros/', parametros.parametros, name='parametros'),
    path('reportes/', reportes.reportes, name='reportes'),
    path('reportes/exportar/', reportes.exportar_reporte, name='exportar_reporte'),
]
