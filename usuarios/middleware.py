"""Interceptor de seguridad del sistema Cafeto.

Un middleware es un "filtro" que Django ejecuta en TODAS las peticiones,
ANTES de llegar a la vista. Es el lugar ideal para la seguridad porque
así ninguna pantalla queda desprotegida por olvido.

Este middleware hace tres cosas, en orden:

  1. Deja pasar libremente el login, el logout, el /admin y los archivos
     estáticos.
  2. Si NO hay sesión iniciada  -> te manda al login.
  3. Si hay sesión PERO el usuario tiene debe_cambiar_clave == True
     -> te manda SIEMPRE a la pantalla de cambio de clave, hasta que la
     cambies. Éste es el "interceptor" que pide la consigna.

Además deja el usuario logueado disponible en ``request.usuario_actual``
para que las vistas y los templates lo usen sin volver a consultarlo.
"""
from django.shortcuts import redirect
from django.urls import reverse

from .models import Usuario


class SeguridadMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ruta = request.path

        # 1) Rutas que nunca se filtran
        exentas = (
            reverse('usuarios:login'),
            reverse('usuarios:logout'),
        )
        if ruta.startswith('/admin') or ruta.startswith('/static') or ruta in exentas:
            return self.get_response(request)

        # 2) ¿Hay alguien logueado?
        id_usuario = request.session.get('usuario_id')
        usuario = (Usuario.objects.select_related('id_perfil')
                   .filter(pk=id_usuario).first() if id_usuario else None)
        request.usuario_actual = usuario

        # borrado_logico = True significa "dado de baja": no puede seguir.
        if usuario is None or usuario.borrado_logico:
            request.session.flush()
            return redirect('usuarios:login')

        # 3) Interceptor de clave provisoria
        url_cambio = reverse('usuarios:cambiar_clave')
        if usuario.debe_cambiar_clave and ruta != url_cambio:
            return redirect('usuarios:cambiar_clave')

        return self.get_response(request)
