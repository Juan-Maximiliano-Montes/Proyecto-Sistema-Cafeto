"""Rutas del sistema Cafeto: una entrada por módulo del DER.

    /                    inicio (según el perfil)
    /perfil/             "Mi Perfil", la ficha personal
    /login/, /usuarios/, /perfiles/, /permisos/, /actividad/   Seguridad
    /geografia/          provincias y localidades
    /stock/              productos, rubros, marcas, alertas, ajustes, producción
    /ventas/             caja, mostrador, cambios, ventas y turnos
    /compras/            proveedores, compras, órdenes, pagos y gastos
    /negocio/            datos del local y reportes
    /fidelizacion/       clientes del club, categorías y beneficios
    /api/movil/          la API de la app móvil (JSON, con su propio token)

El /admin/ de Django sólo existe en desarrollo (DEBUG): en producción es
una puerta trasera que saltea la auditoría y los permisos del sistema.
"""
from django.conf import settings
from django.urls import include, path

from negocio.views.inicio import inicio
from seguridad import views as seguridad_views

urlpatterns = [
    path('', include('seguridad.urls')),
    path('geografia/', include('geografia.urls')),
    path('stock/', include('stock.urls')),
    path('ventas/', include('ventas.urls')),
    path('compras/', include('compras.urls')),
    path('fidelizacion/', include('fidelizacion.urls')),
    path('negocio/', include('negocio.urls')),
    path('api/movil/', include('api_movil.urls')),
    path('', include('nucleo.urls')),
    path('perfil/', seguridad_views.mi_perfil, name='mi_perfil'),
    path('', inicio, name='home'),
]

if settings.DEBUG:
    from django.contrib import admin

    urlpatterns.insert(0, path('admin/', admin.site.urls))
