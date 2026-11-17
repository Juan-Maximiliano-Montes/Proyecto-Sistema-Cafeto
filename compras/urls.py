"""Rutas del módulo Compras."""
from django.urls import path

from nucleo.crud import rutas_crud

from .views import egresos, facturas, ordenes, recepciones

app_name = 'compras'

urlpatterns = rutas_crud('proveedores') + rutas_crud('tipos_egreso') + [
    # Compras y recepción de mercadería
    path('compras/', recepciones.compras, name='compras'),
    path('compras/recibir/', recepciones.recepcion, name='recepcion'),
    path('nueva/', recepciones.recepcion, name='nueva_compra'),
    path('escanear-factura/', facturas.escanear, name='escanear_factura'),
    path('escanear-factura/proveedor/', facturas.alta_proveedor, name='alta_proveedor_factura'),
    path('escanear-factura/producto/', facturas.alta_producto, name='alta_producto_factura'),
    path('facturas-recibidas/<int:pk>/descartar/', facturas.descartar_factura, name='descartar_factura'),
    path('compras/exportar/', recepciones.exportar_compras, name='exportar_compras'),
    path('compras/<int:pk>/', recepciones.ficha_compra, name='ficha_compra'),
    # Órdenes de compra
    path('ordenes/', ordenes.ordenes, name='ordenes'),
    path('ordenes/nueva/', ordenes.nueva_orden, name='nueva_orden'),
    path('ordenes/<int:pk>/', ordenes.orden, name='orden'),
    path('ordenes/<int:pk>/estado/', ordenes.cambiar_estado, name='estado_orden'),
    path('ordenes/<int:pk>/pdf/', ordenes.orden_pdf, name='orden_pdf'),
    # Pagos y gastos
    path('egresos/', egresos.egresos, name='egresos'),
    path('egresos/registrar/', egresos.registrar, name='registrar_egreso'),
    path('egresos/<int:pk>/pagar/', egresos.pagar, name='pagar_egreso'),
    path('egresos/<int:pk>/conciliar/', egresos.conciliar, name='conciliar_egreso'),
    path('egresos/exportar/', egresos.exportar_egresos, name='exportar_egresos'),
]
