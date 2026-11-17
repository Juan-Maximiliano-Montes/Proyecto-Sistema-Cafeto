"""Rutas del módulo Negocio: parámetros del local y reportes."""
from django.urls import path

from .views import archivo, parametros, reportes

app_name = 'negocio'

urlpatterns = [
    path('parametros/', parametros.parametros, name='parametros'),
    path('reportes/', reportes.reportes, name='reportes'),
    path('reportes/exportar/', reportes.exportar_reporte, name='exportar_reporte'),
    path('resumen-del-mes/', reportes.resumen_del_mes, name='resumen_del_mes'),
    path('archivo-historico/', archivo.archivo_historico, name='archivo'),
    path('archivo-historico/exportar/', archivo.exportar_archivo, name='archivo_exportar'),
]
