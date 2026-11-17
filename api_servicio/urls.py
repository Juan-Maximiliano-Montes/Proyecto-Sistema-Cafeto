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
    path('compras/pre-cargar/', views.precargar_compra, name='precargar_compra'),
    path('compras/leer-factura/', views.leer_factura, name='leer_factura'),
    path('mantenimiento/mensual/', views.mantenimiento_mensual, name='mantenimiento_mensual'),
]
