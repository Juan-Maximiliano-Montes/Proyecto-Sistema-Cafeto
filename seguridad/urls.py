"""Rutas del módulo Seguridad y Acceso.

    /login/ /logout/ /cambiar-clave/     la sesión
    /usuarios/...                        CRUD de usuarios
    /perfiles/...                        CRUD de perfiles
    /permisos/                           catálogo (sólo lectura)
    /actividad/...                       Registro de Actividad (sólo lectura)

El Registro de Actividad no tiene crear/editar/baja, y no es un olvido:
LOG_AUDITORIA es un libro append-only. Lo que no se puede nombrar, no se
puede pedir. "Revisar alertas" no edita nada: agrega un asiento.
"""
from django.urls import path

from . import views, views_actividad

app_name = 'seguridad'

urlpatterns = [
    # --- Sesión ---
    path('login/', views.login_view, name='login'),
    path('olvide-clave/', views.olvide_clave, name='olvide_clave'),
    path('elegir-clave/<str:proposito>/<str:token>/', views.elegir_clave,
         name='elegir_clave'),
    path('logout/', views.logout_view, name='logout'),
    path('cambiar-clave/', views.cambiar_clave_view, name='cambiar_clave'),

    # --- Usuarios ---
    path('usuarios/', views.lista_usuarios, name='lista'),
    path('usuarios/crear/', views.crear_usuario, name='crear'),
    path('usuarios/exportar/', views.exportar_usuarios, name='exportar'),
    path('usuarios/<int:id_usuario>/editar/', views.editar_usuario,
         name='editar'),
    path('usuarios/<int:id_usuario>/baja/', views.eliminar_usuario,
         name='eliminar'),
    path('usuarios/<int:id_usuario>/reactivar/', views.reactivar_usuario,
         name='reactivar'),
    path('usuarios/<int:id_usuario>/restablecer-clave/',
         views.restablecer_clave_usuario, name='restablecer_clave'),
    path('usuarios/<int:id_usuario>/detalle/', views.detalle_usuario,
         name='detalle'),
    path('usuarios/verificar-dni/', views.verificar_dni,
         name='verificar_dni'),
    path('usuarios/<int:id_usuario>/perfil/', views.cambiar_perfil_usuario,
         name='cambiar_perfil'),
    path('usuarios/<int:id_usuario>/perfil-temporal/',
         views.asignar_perfil_temporal, name='perfil_temporal'),
    path('usuarios/<int:id_usuario>/perfil-temporal/quitar/',
         views.revocar_perfil_temporal, name='revocar_temporal'),
    path('usuarios/titularidad/', views.transferir_titularidad,
         name='transferir_titularidad'),

    # --- Perfiles ---
    path('perfiles/', views.lista_perfiles, name='perfiles'),
    path('perfiles/crear/', views.crear_perfil, name='crear_perfil'),
    path('perfiles/<int:id_perfil>/editar/', views.editar_perfil,
         name='editar_perfil'),
    path('perfiles/<int:id_perfil>/baja/', views.eliminar_perfil,
         name='eliminar_perfil'),
    path('perfiles/<int:id_perfil>/reactivar/', views.reactivar_perfil,
         name='reactivar_perfil'),

    # --- Permisos (catálogo de sólo lectura) ---
    path('permisos/', views.lista_permisos, name='permisos'),

    # --- Registro de Actividad ---
    path('actividad/', views_actividad.RegistroActividadListView.as_view(),
         name='actividad'),
    path('actividad/<int:pk>/', views_actividad.ficha_actividad,
         name='ficha_actividad'),
    path('actividad/exportar/', views_actividad.exportar_actividad,
         name='exportar_actividad'),
    path('actividad/alertas/revisadas/', views_actividad.revisar_alertas,
         name='revisar_alertas'),
]
