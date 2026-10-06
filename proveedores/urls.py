"""URLs del módulo de Proveedores y de las ubicaciones.

Dos bloques:

  * el CRUD de proveedores, con sus rutas propias de siempre;
  * las pantallas de provincias y localidades, que son parte de "Mantener
    datos maestros" (DFD 10) y usan la máquina genérica de
    ``maestros.crud``. Como son un catálogo geográfico sin baja lógica,
    tienen tres rutas en vez de cinco.
"""
from django.urls import path

from maestros import crud

from . import views

app_name = 'proveedores'


def rutas_de_catalogo(clave):
    """Alta y modificación: un catálogo geográfico no tiene baja."""
    return [
        path(f'{clave}/', crud.listar, {'clave': clave},
             name=f'{clave}_lista'),
        path(f'{clave}/crear/', crud.crear, {'clave': clave},
             name=f'{clave}_crear'),
        path(f'{clave}/<int:pk>/editar/', crud.editar, {'clave': clave},
             name=f'{clave}_editar'),
    ]


urlpatterns = [
    # --- CRUD de proveedores (pantalla propia, anterior a la Fase 4) ---
    path('', views.lista_proveedores, name='lista'),
    path('crear/', views.crear_proveedor, name='crear'),
    path('editar/<int:id_proveedor>/', views.editar_proveedor, name='editar'),
    path('eliminar/<int:id_proveedor>/', views.eliminar_proveedor, name='eliminar'),
    path('reactivar/<int:id_proveedor>/', views.reactivar_proveedor, name='reactivar'),
]

# --- Ubicaciones (DFD 10) ---
for _clave in ('provincias', 'localidades'):
    urlpatterns += rutas_de_catalogo(_clave)
