"""Rutas del módulo Stock e Inventario."""
from django.urls import path

from nucleo.crud import rutas_crud

from .views import ajustes, alertas, catalogos, precios, produccion

app_name = 'stock'

urlpatterns = (rutas_crud('productos') + rutas_crud('rubros')
               + rutas_crud('marcas'))

urlpatterns += [
    # Consultas que alimentan los selectores de producto (JSON).
    path('api/productos/', catalogos.buscar_productos, name='buscar_productos'),
    path('api/lotes/', alertas.lotes_de_producto, name='lotes_de_producto'),
    # Alertas y ofertas
    path('alertas/', alertas.alertas, name='alertas'),
    path('alertas/exportar/', alertas.exportar_alertas, name='exportar_alertas'),
    path('ofertas/', alertas.ofertas, name='ofertas'),
    path('ofertas/etiquetas/', alertas.etiquetas_de_oferta, name='etiquetas_oferta'),
    # Ajustes
    path('ajustes/', ajustes.ajustes, name='ajustes'),
    path('ajustes/<int:pk>/', ajustes.ajuste, name='ajuste'),
    path('ajustes/<int:pk>/resolver/', ajustes.resolver, name='resolver'),
    path('conteo/', ajustes.conteo, name='conteo'),
    path('merma/', ajustes.merma, name='merma'),
    path('traspaso/', ajustes.traspaso, name='traspaso'),
    # Precios
    path('precios/', precios.actualizar_precios, name='precios'),
    # Producción
    path('produccion/', produccion.producciones, name='producciones'),
]
