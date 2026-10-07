"""URLs de la app de operaciones.

Dos rutas, las dos de LECTURA:

    /actividad/            el listado con los filtros
    /actividad/<pk>/       la ficha de un registro (fragmento para el modal)

No hay ``crear/``, ``editar/`` ni ``baja/``, y no es un olvido:
LOG_AUDITORIA es un libro append-only y el mixin ``LibroInmutable`` rechaza
cualquier UPDATE o DELETE. La ausencia de esas rutas es parte de la
garantía: lo que no se puede nombrar, no se puede pedir.
"""
from django.urls import path

from . import views

app_name = 'operaciones'

urlpatterns = [
    path('', views.RegistroActividadListView.as_view(), name='actividad'),
    path('<int:pk>/', views.ficha_actividad, name='ficha_actividad'),
]
