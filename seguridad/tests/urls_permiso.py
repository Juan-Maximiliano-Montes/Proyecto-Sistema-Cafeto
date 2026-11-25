"""Una ruta de prueba protegida con ``requiere_permiso`` y su destino por
defecto (test_permiso_y_clave_provisoria)."""
from django.http import HttpResponse
from django.urls import include, path

from seguridad.decorators import requiere_permiso


@requiere_permiso('ver_usuarios')
def protegida(request):
    return HttpResponse('adentro')


urlpatterns = [
    path('prueba/protegida/', protegida),
    path('', include('cafeto.urls')),
]
