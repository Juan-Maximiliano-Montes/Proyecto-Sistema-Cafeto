"""Rutas del módulo Ventas y Caja."""
from django.urls import path

from nucleo.crud import rutas_crud

from .views import autorizaciones, cambios, caja, consultas, mostrador

app_name = 'ventas'

urlpatterns = [
    # El puesto de caja
    path('caja/', caja.puesto_de_caja, name='caja'),
    path('caja/elegir/', caja.elegir_caja, name='elegir_caja'),
    path('caja/abrir/', caja.abrir_turno, name='abrir_turno'),
    path('caja/efectivo/', caja.mover_efectivo, name='mover_efectivo'),
    path('caja/arqueo/', caja.declarar_arqueo, name='declarar_arqueo'),
    path('caja/cierre/', caja.cierre, name='cierre'),
    path('caja/cierre/descartar/', caja.descartar_arqueo, name='descartar_arqueo'),
    path('caja/cierre/autorizacion/', caja.pedir_autorizacion_cierre, name='pedir_autorizacion_cierre'),
    path('caja/arqueo-parcial/', caja.corte_x, name='corte_x'),
    path('caja/arqueo-parcial/<int:pk>/', caja.comprobante_x, name='comprobante_x'),
    path('caja/movimientos/<int:pk>/vale/', caja.vale_movimiento, name='vale_movimiento'),
    # El mostrador
    path('mostrador/', mostrador.mostrador, name='mostrador'),
    path('mostrador/catalogo/', mostrador.catalogo, name='catalogo'),
    path('mostrador/cotizar/', mostrador.cotizar, name='cotizar'),
    path('mostrador/ajuste-express/', mostrador.ajuste_express, name='ajuste_express'),
    path('mostrador/cobrar/', mostrador.cobrar, name='cobrar'),
    path('mostrador/cliente/', mostrador.identificar_cliente, name='identificar_cliente'),
    path('mostrador/cliente/alta/', mostrador.alta_cliente, name='alta_cliente'),
    path('ventas/<int:pk>/ticket/', mostrador.ticket, name='ticket'),
    # Autorización a distancia (DD-94)
    path('mostrador/autorizacion/', autorizaciones.pedir, name='pedir_autorizacion'),
    path('mostrador/autorizacion/<int:pk>/', autorizaciones.estado, name='estado_autorizacion'),
    path('mostrador/autorizacion/<int:pk>/pin/', autorizaciones.pin, name='pin_autorizacion'),
    path('mostrador/autorizacion/<int:pk>/cancelar/', autorizaciones.cancelar, name='cancelar_autorizacion'),
    path('autorizaciones/', autorizaciones.bandeja, name='autorizaciones'),
    path('autorizaciones/pendientes/', autorizaciones.pendientes_json, name='pendientes_autorizacion'),
    path('autorizar/<str:token>/', autorizaciones.enlace, name='autorizar_a_distancia'),
    path('cambios/', cambios.cambios, name='cambios'),
    path('cambios/<int:pk>/comprobante/', cambios.comprobante_cambio, name='comprobante_cambio'),
    # Consultas
    path('ventas/', consultas.ventas, name='ventas'),
    path('ventas/exportar/', consultas.exportar_ventas, name='exportar_ventas'),
    path('ventas/exportar-renglones/', consultas.exportar_renglones, name='exportar_renglones'),
    path('ventas/<int:pk>/', consultas.ficha_venta, name='ficha_venta'),
    path('ventas/<int:pk>/anular/', consultas.anular_venta, name='anular_venta'),
    # Supervisión
    path('turnos/', caja.turnos, name='turnos'),
    path('turnos/exportar/', caja.exportar_turnos, name='exportar_turnos'),
    path('turnos/<int:pk>/', caja.ficha_turno, name='ficha_turno'),
    path('turnos/<int:pk>/recaudacion/', caja.recaudacion, name='recaudacion'),
    path('turnos/<int:pk>/revisar/', caja.revisar_turno, name='revisar_turno'),
]
urlpatterns += rutas_crud('medios_pago') + rutas_crud('cajas_registradoras')
