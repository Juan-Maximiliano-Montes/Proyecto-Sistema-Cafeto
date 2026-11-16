"""Rutas del módulo Clientes y Fidelización (DFD 9)."""
from django.urls import path

from fidelizacion import views, vistas_credito
from nucleo.crud import rutas_crud

app_name = 'fidelizacion'

urlpatterns = (rutas_crud('categorias') + rutas_crud('beneficios')
               + rutas_crud('clientes') + [
                   path('clientes/<int:pk>/tarjeta/', views.tarjeta,
                        name='tarjeta'),
                   path('cuentas-corrientes/', vistas_credito.cuentas, name='cuentas'),
                   path('cuentas-corrientes/<int:pk>/', vistas_credito.cuenta, name='cuenta'),
                   path('cuentas-corrientes/morosos/', vistas_credito.morosos, name='morosos'),
                   path('cuentas-corrientes/parecidos/', vistas_credito.parecidos, name='parecidos'),
               ])
