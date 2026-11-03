"""Rutas transversales: la importación desde Excel de cualquier módulo."""
from django.urls import path

from nucleo import vistas_importacion as importacion

urlpatterns = [
    path('importar/<slug:clave>/', importacion.importar, name='importar'),
    path('importar/<slug:clave>/confirmar/', importacion.confirmar, name='importar_confirmar'),
    path('importar/<slug:clave>/descartar/', importacion.descartar, name='importar_descartar'),
    path('importar/<slug:clave>/modelo/', importacion.modelo, name='importar_modelo'),
]
