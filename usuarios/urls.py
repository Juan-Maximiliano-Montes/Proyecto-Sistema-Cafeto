from django.urls import path

from . import views

app_name = 'usuarios'

urlpatterns = [
    # --- Seguridad / sesión ---
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('cambiar-clave/', views.cambiar_clave_view, name='cambiar_clave'),

    # --- CRUD de Usuarios ---
    path('', views.lista_usuarios, name='lista'),
    path('crear/', views.crear_usuario, name='crear'),
    path('editar/<int:id_usuario>/', views.editar_usuario, name='editar'),
    path('eliminar/<int:id_usuario>/', views.eliminar_usuario, name='eliminar'),
    path('reactivar/<int:id_usuario>/', views.reactivar_usuario, name='reactivar'),
    path('restablecer-clave/<int:id_usuario>/', views.restablecer_clave_usuario,
         name='restablecer_clave'),

    # --- CRUD de Perfiles ---
    path('perfiles/', views.lista_perfiles, name='perfiles'),
    path('perfiles/crear/', views.crear_perfil, name='crear_perfil'),
    path('perfiles/editar/<int:id_perfil>/', views.editar_perfil,
         name='editar_perfil'),
    path('perfiles/eliminar/<int:id_perfil>/', views.eliminar_perfil,
         name='eliminar_perfil'),
    path('perfiles/reactivar/<int:id_perfil>/', views.reactivar_perfil,
         name='reactivar_perfil'),

    # --- Catálogo de Permisos (SÓLO LECTURA) ---
    # No hay crear/editar/eliminar: el catálogo lo define el código, no la
    # pantalla. La asignación a perfiles se hace en 'editar_perfil'.
    path('permisos/', views.lista_permisos, name='permisos'),

    # --- AJAX ---
    path('verificar-dni/', views.verificar_dni, name='verificar_dni'),
    path('detalle/<int:id_usuario>/', views.detalle_usuario, name='detalle'),
]
