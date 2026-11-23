"""Rutas del módulo Stock e Inventario."""
from django.urls import path

from nucleo.crud import rutas_crud

from .views import (
    ajustes,
    alertas,
    catalogos,
    fotos,
    precios,
    produccion,
    sugerencias,
)

app_name = 'stock'

urlpatterns = (rutas_crud('productos') + rutas_crud('rubros')
               + rutas_crud('marcas'))

urlpatterns += [
    # Consultas que alimentan los selectores de producto (JSON).
    path('productos/<int:pk>/foto/', fotos.foto_de_producto, name='producto_foto'),
    path('api/productos/', catalogos.buscar_productos, name='buscar_productos'),
    path('api/codigo-interno/', catalogos.codigo_interno, name='codigo_interno'),
    path('api/lotes/', alertas.lotes_de_producto, name='lotes_de_producto'),
    # Alertas y ofertas
    path('alertas/', alertas.alertas, name='alertas'),
    path('alertas/exportar/', alertas.exportar_alertas, name='exportar_alertas'),
    path('ofertas/', alertas.ofertas, name='ofertas'),
    path('ofertas/etiquetas/', alertas.etiquetas_de_oferta, name='etiquetas_oferta'),
    path('conteo/buscar/', ajustes.conteo_buscar, name='conteo_buscar'),
    path('productos/etiquetas/', alertas.etiquetas_de_gondola, name='etiquetas_gondola'),
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
    # Sugerencias de mejora del catálogo (las aprueba una persona)
    path('sugerencias/', sugerencias.sugerencias, name='sugerencias'),
    path('sugerencias/<int:pk>/aplicar/', sugerencias.aplicar, name='aplicar_sugerencia'),
    path('sugerencias/<int:pk>/descartar/', sugerencias.descartar, name='descartar_sugerencia'),
]
