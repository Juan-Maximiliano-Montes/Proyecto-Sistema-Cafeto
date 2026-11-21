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

El /admin/ de Django existe en desarrollo y, en producción, sólo si
CAFETO_ADMIN_URL dice en qué ruta: es una puerta trasera que saltea la
auditoría y los permisos del sistema.
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
    path('api/servicio/', include('api_servicio.urls')),
    path('', include('nucleo.urls')),
    path('perfil/', seguridad_views.mi_perfil, name='mi_perfil'),
    path('', inicio, name='home'),
]

# Las fotos de los productos. Son públicas a propósito: la app del cliente
# las pide sin sesión, igual que el catálogo. Sólo se sirve esa carpeta.
from django.urls import re_path
from django.views.static import serve
from cafeto.settings import ruta_del_admin


def foto_subida(request, path):
    return serve(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns.append(re_path(r'^media/(?P<path>productos/[\w.-]+)$', foto_subida, name='media'))

# Las páginas de error con la identidad de Cafeto (DD-100): nunca un traceback.
handler400 = 'nucleo.errores.pagina_400'
handler403 = 'nucleo.errores.pagina_403'
handler404 = 'nucleo.errores.pagina_404'
handler500 = 'nucleo.errores.pagina_500'

if ruta_del_admin():
    from django.contrib import admin

    urlpatterns.insert(0, path(ruta_del_admin(), admin.site.urls))
