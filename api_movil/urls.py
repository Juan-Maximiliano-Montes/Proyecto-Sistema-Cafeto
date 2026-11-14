"""Rutas de la API de la app móvil, debajo de /api/movil/ (ver cafeto/urls.py).

La barra final es parte del contrato: la app la manda siempre.
"""
from django.urls import path

from api_movil import views, views_catalogo, views_clientes, views_tareas

app_name = 'api_movil'

urlpatterns = [
    # Equipo: sesión y ABM liviano
    path('sesion/', views.sesion, name='sesion'),
    path('marcas/', views.marcas, name='marcas'),
    path('marcas/<int:pk>/', views.marca, name='marca'),
    path('rubros/', views.rubros, name='rubros'),
    path('rubros/<int:pk>/', views.rubro, name='rubro'),
    # Catálogo público
    path('catalogo/destacados/', views_catalogo.destacados, name='destacados'),
    path('catalogo/productos/', views_catalogo.productos, name='productos'),
    path('catalogo/rubros/', views_catalogo.rubros, name='rubros_del_catalogo'),
    path('catalogo/beneficios/', views_catalogo.beneficios, name='beneficios'),
    # Equipo: consulta y alertas
    path('stock/productos/', views_catalogo.consulta, name='consulta'),
    path('stock/alertas/', views_catalogo.alertas, name='alertas'),
    # Equipo: tareas
    path('panel/', views_tareas.panel, name='panel'),
    path('stock/productos/<int:pk>/lotes/', views_tareas.lotes, name='lotes'),
    path('stock/ofertas/', views_tareas.ofertas, name='ofertas'),
    path('compras/ordenes/', views_tareas.ordenes, name='ordenes'),
    path('compras/ordenes/<int:pk>/', views_tareas.orden, name='orden'),
    path('compras/recepciones/', views_tareas.recepciones, name='recepciones'),
    path('stock/conteo/', views_tareas.conteo, name='conteo'),
    path('stock/conteo/renglones/', views_tareas.renglon_de_conteo,
         name='renglon_de_conteo'),
    path('stock/conteo/enviar/', views_tareas.enviar_conteo, name='enviar_conteo'),
    path('stock/mermas/', views_tareas.mermas, name='mermas'),
    path('stock/traspasos/', views_tareas.traspasos, name='traspasos'),
    path('stock/ajustes/', views_tareas.ajustes, name='ajustes'),
    path('stock/ajustes/<int:pk>/aprobar/', views_tareas.aprobar, name='aprobar'),
    path('stock/ajustes/<int:pk>/rechazar/', views_tareas.rechazar, name='rechazar'),
    path('stock/producibles/', views_tareas.producibles, name='producibles'),
    path('stock/producciones/', views_tareas.producciones, name='producciones'),
    # Clientes (con el token de Firebase)
    path('clientes/yo/', views_clientes.yo, name='cliente'),
    path('clientes/vincular/', views_clientes.vincular, name='vincular_cliente'),
]
