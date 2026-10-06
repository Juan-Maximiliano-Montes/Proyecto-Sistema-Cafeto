"""Seguridad dinámica por permisos (Hito 2 — ampliación).

Acá vive el decorador ``requiere_permiso``. La idea:

    El middleware ya garantizó que HAY una sesión válida y activa
    (``request.usuario_actual``). Este decorador da el segundo paso:
    antes de dejar entrar a una vista concreta (listado, alta, edición,
    baja, reseteo), comprueba que el PERFIL del usuario logueado tenga
    ese permiso puntual.

El chequeo se delega en ``Usuario.tiene_permiso()`` (models.py), que
consulta la relación N a N por el ORM y además deja pasar SIEMPRE al
perfil Administrador.
"""
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def requiere_permiso(codigo_permiso, redirigir_a='usuarios:lista'):
    """Deja pasar sólo si el usuario logueado tiene ese permiso.

    Uso::

        @requiere_permiso('crear_usuario')
        def crear_usuario(request):
            ...

        # con destino de rebote propio (para no caer en un bucle cuando
        # la vista protegida es justamente el listado):
        @requiere_permiso('ver_usuarios', redirigir_a='mi_perfil')
        def lista_usuarios(request):
            ...

    Si el usuario NO tiene el permiso: corta la ejecución de la vista,
    muestra un ``messages.error`` de acceso denegado y redirige a
    ``redirigir_a``.
    """

    def decorador(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            usuario = getattr(request, 'usuario_actual', None)

            # Sin sesión no debería llegar acá (lo frena el middleware),
            # pero si pasara, lo mandamos al login.
            if usuario is None:
                return redirect('usuarios:login')

            if not usuario.tiene_permiso(codigo_permiso):
                messages.error(
                    request,
                    'Acceso denegado: tu perfil no tiene habilitado el '
                    f'permiso necesario para esta acción ("{codigo_permiso}").'
                )
                return redirect(redirigir_a)

            # Todo OK: ejecutamos la vista real.
            return vista(request, *args, **kwargs)

        return envoltura

    return decorador
