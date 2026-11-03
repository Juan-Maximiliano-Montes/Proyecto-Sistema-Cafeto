"""Rutas del módulo Ventas y Caja."""
from django.urls import path

from nucleo.crud import rutas_crud

from .views import cambios, caja, consultas, mostrador

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
    # El mostrador
    path('mostrador/', mostrador.mostrador, name='mostrador'),
    path('mostrador/catalogo/', mostrador.catalogo, name='catalogo'),
    path('mostrador/cotizar/', mostrador.cotizar, name='cotizar'),
    path('mostrador/cobrar/', mostrador.cobrar, name='cobrar'),
    path('mostrador/cliente/', mostrador.identificar_cliente, name='identificar_cliente'),
    path('mostrador/cliente/alta/', mostrador.alta_cliente, name='alta_cliente'),
    path('ventas/<int:pk>/ticket/', mostrador.ticket, name='ticket'),
    path('cambios/', cambios.cambios, name='cambios'),
    # Consultas
    path('ventas/', consultas.ventas, name='ventas'),
    path('ventas/exportar/', consultas.exportar_ventas, name='exportar_ventas'),
    path('ventas/<int:pk>/', consultas.ficha_venta, name='ficha_venta'),
    path('ventas/<int:pk>/anular/', consultas.anular_venta, name='anular_venta'),
    # Supervisión
    path('turnos/', caja.turnos, name='turnos'),
    path('turnos/exportar/', caja.exportar_turnos, name='exportar_turnos'),
    path('turnos/<int:pk>/', caja.ficha_turno, name='ficha_turno'),
    path('turnos/<int:pk>/recaudacion/', caja.recaudacion, name='recaudacion'),
]
urlpatterns += rutas_crud('medios_pago') + rutas_crud('cajas_registradoras')
