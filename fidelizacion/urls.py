"""Rutas del módulo Clientes y Fidelización (DFD 9)."""
from django.urls import path

from fidelizacion import views
from nucleo.crud import rutas_crud

app_name = 'fidelizacion'

urlpatterns = (rutas_crud('categorias') + rutas_crud('beneficios')
               + rutas_crud('clientes') + [
                   path('clientes/<int:pk>/tarjeta/', views.tarjeta,
                        name='tarjeta'),
               ])
