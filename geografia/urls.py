"""Rutas del módulo Geografía.

Provincias y localidades son un catálogo de referencia sin baja lógica:
tienen alta y modificación, nada más.
"""
from nucleo.crud import ACCIONES_SIN_BAJA, rutas_crud

app_name = 'geografia'

urlpatterns = (rutas_crud('provincias', ACCIONES_SIN_BAJA)
               + rutas_crud('localidades', ACCIONES_SIN_BAJA))
