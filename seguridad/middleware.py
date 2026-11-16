"""Interceptor de seguridad del sistema Cafeto.

Un middleware es un "filtro" que Django ejecuta en TODAS las peticiones,
ANTES de llegar a la vista. Es el lugar para la seguridad porque así
ninguna pantalla queda desprotegida por olvido.

En orden:

  1. Deja pasar el login, el logout y los archivos estáticos (y el /admin
     de Django, que sólo existe en desarrollo). También la API de la app
     móvil: no usa la sesión web sino su propio token, y cada una de sus
     vistas controla el token y el permiso (``api_movil.autenticacion``).
  2. Si NO hay sesión iniciada -> al login.
  3. Si hay sesión, la valida EN EL SERVIDOR contra la tabla SESIONES: que
     sea la vigente del usuario (el token coincide), que no haya vencido
     y que no lleve demasiado tiempo sin uso. Si no vale, se cierra y se
     avisa por qué.
  4. Si el usuario arrastra una clave provisoria (``debe_cambiar_clave``)
     -> SIEMPRE a la pantalla de cambio de clave, hasta que la cambie.

Deja disponibles ``request.usuario_actual`` y ``request.sesion_negocio``
para que las vistas y los templates los usen sin volver a consultarlos.
"""
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from seguridad.models import Usuario
from seguridad.servicios import validar_sesion

# Debajo de esto vive la API de la app móvil (cafeto/urls.py).
PREFIJO_API_MOVIL = '/api/movil/'


def _espera_json(request):
    return (request.headers.get('x-requested-with') == 'XMLHttpRequest'
            or 'application/json' in request.headers.get('accept', ''))


class SeguridadMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ruta = request.path
        request.usuario_actual = None
        request.sesion_negocio = None

        # 1) Rutas que nunca se filtran
        exentas = (reverse('seguridad:login'), reverse('seguridad:logout'),
                   reverse('seguridad:olvide_clave'))
        estaticos = settings.STATIC_URL or '/static/'
        # El enlace del correo se abre sin sesión: el token es la credencial.
        elegir = reverse('seguridad:elegir_clave', args=['x', 'y']).rsplit('x/', 1)[0]
        fotos = (settings.MEDIA_URL or '/media/') + 'productos/'
        # Lo mismo el enlace con que la dueña aprueba desde el celular (DD-94).
        autorizar = reverse('ventas:autorizar_a_distancia', args=['x']).rsplit('x/', 1)[0]
        if (ruta in exentas or ruta.startswith(estaticos) or ruta.startswith(elegir)
                or ruta.startswith(autorizar)
                or ruta.startswith(fotos)
                or ruta.startswith(PREFIJO_API_MOVIL)
                or ruta.startswith('/api/servicio/')
                or (settings.DEBUG and ruta.startswith('/admin'))):
            return self.get_response(request)

        # 2) ¿Hay alguien logueado?
        id_usuario = request.session.get('usuario_id')
        usuario = (Usuario.objects.select_related(
            'id_perfil', 'id_perfil_temporal').filter(pk=id_usuario).first()
            if id_usuario else None)

        aviso = None
        if usuario is not None and not usuario.activo:
            usuario, aviso = None, 'Tu usuario fue dado de baja.'

        # 3) ¿La sesión sigue valiendo en el servidor?
        if usuario is not None:
            sesion, aviso = validar_sesion(request, usuario)
            if sesion is None:
                usuario = None
            else:
                request.sesion_negocio = sesion

        if usuario is None:
            request.session.flush()
            if _espera_json(request):
                return JsonResponse(
                    {'error': aviso or 'Tenés que iniciar sesión.'},
                    status=401)
            if aviso:
                messages.warning(request, aviso)
            return redirect('seguridad:login')

        request.usuario_actual = usuario

        # 4) Interceptor de clave provisoria
        url_cambio = reverse('seguridad:cambiar_clave')
        if usuario.debe_cambiar_clave and ruta != url_cambio:
            return redirect('seguridad:cambiar_clave')

        return self.get_response(request)
