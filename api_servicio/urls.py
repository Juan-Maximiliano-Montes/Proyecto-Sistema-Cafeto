from django.urls import path

from api_servicio import views

app_name = 'api_servicio'

urlpatterns = [
    path('resumen/', views.resumen, name='resumen'),
    path('ordenes/emitidas/', views.ordenes_emitidas, name='ordenes_emitidas'),
    path('ordenes/<int:pk>/pdf/', views.orden_pdf, name='orden_pdf'),
    path('productos/sin-foto/', views.productos_sin_foto, name='productos_sin_foto'),
    path('productos/<int:pk>/foto/', views.foto_de_producto, name='foto_de_producto'),
    path('productos/sin-marca/', views.productos_sin_marca, name='productos_sin_marca'),
    path('productos/<int:pk>/enriquecer/', views.enriquecer_producto, name='enriquecer_producto'),
    # Diagnóstico progresivo del catálogo (sólo lectura) y la bandeja de
    # sugerencias, que aprueba una persona.
    path('productos/sin-receta/', views.productos_sin_receta, name='productos_sin_receta'),
    path('productos/margen-bajo/', views.productos_margen_bajo, name='productos_margen_bajo'),
    path('productos/conteo-rapido/', views.productos_conteo_rapido, name='productos_conteo_rapido'),
    path('productos/sin-clasificar/', views.productos_sin_clasificar, name='productos_sin_clasificar'),
    path('productos/sugerencias/', views.proponer_sugerencia, name='proponer_sugerencia'),
    path('compras/pre-cargar/', views.precargar_compra, name='precargar_compra'),
    path('compras/leer-factura/', views.leer_factura, name='leer_factura'),
    path('mantenimiento/mensual/', views.mantenimiento_mensual, name='mantenimiento_mensual'),
]
