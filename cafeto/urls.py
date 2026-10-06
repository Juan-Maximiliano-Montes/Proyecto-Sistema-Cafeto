"""
URL configuration for cafeto project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from usuarios import views as usuarios_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('usuarios/', include('usuarios.urls')),
    path('proveedores/', include('proveedores.urls')),
    path('maestros/', include('maestros.urls')),

    # Registro de Actividad: la auditoría en modo lectura. Vive en
    # /actividad/ y no en /operaciones/, porque es el nombre que usa
    # quien entra, no el de la app que la implementa.
    path('actividad/', include('operaciones.urls')),

    # "Mi Perfil": ficha personal en modo lectura. Vive en la raíz (/perfil/)
    # porque la usan los perfiles operativos que NO entran a /usuarios/.
    path('perfil/', usuarios_views.mi_perfil, name='mi_perfil'),

    # La raíz del sitio: 'home' decide si te manda al directorio o a tu ficha
    # (el middleware manda al login si todavía no hay sesión).
    path('', usuarios_views.home, name='home'),
]
