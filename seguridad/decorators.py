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

from nucleo.errores import error_json, quiere_json


def nombre_legible(codigo_permiso):
    """El nombre en castellano del permiso (``Permiso.nombre``), para
    mostrárselo a la persona. Si el permiso no está cargado en la base,
    queda el código técnico: más vale eso que un aviso vacío."""
    from seguridad.models import Permiso

    nombre = (Permiso.objects.filter(codigo=codigo_permiso)
              .values_list('nombre', flat=True).first())
    return nombre or codigo_permiso


def requiere_permiso(codigo_permiso, redirigir_a='home'):
    """Deja pasar sólo si el usuario logueado tiene ese permiso.

    Uso::

        @requiere_permiso('crear_usuario')
        def crear_usuario(request):
            ...

        # con destino de rebote propio (para quedarse en el módulo):
        @requiere_permiso('registrar_egreso', redirigir_a='compras:egresos')
        def registrar_egreso(request):
            ...

    Si el usuario NO tiene el permiso: corta la ejecución de la vista,
    deja el intento en el Registro de Actividad con resultado RECHAZADA
    (DD-62), muestra un ``messages.error`` de acceso denegado y redirige a
    ``redirigir_a``. El destino por defecto es el inicio (``home``), que
    ninguna vista protegida por permiso puede rebotar a sí misma: rebotar a
    un listado protegido con el mismo permiso era un bucle de redirecciones.

    El aviso dice el NOMBRE del permiso en castellano («Registrar un
    egreso»), no su código técnico.
    """

    def decorador(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            usuario = getattr(request, 'usuario_actual', None)

            # Sin sesión no debería llegar acá (lo frena el middleware),
            # pero si pasara, lo mandamos al login.
            if usuario is None:
                if quiere_json(request):
                    return error_json('Tenés que iniciar sesión.', 401)
                return redirect('seguridad:login')

            if not usuario.tiene_permiso(codigo_permiso):
                # DD-62: el intento queda en el Registro de Actividad.
                from seguridad.servicios import registrar_acceso_denegado
                registrar_acceso_denegado(request, codigo_permiso)
                nombre = nombre_legible(codigo_permiso)
                if quiere_json(request):
                    return error_json('Tu perfil no tiene habilitado el permiso necesario para esta '
                                      f'acción ("{nombre}").', 403)
                messages.error(
                    request,
                    'Acceso denegado: tu perfil no tiene habilitado el '
                    f'permiso necesario para esta acción ("{nombre}").'
                )
                return redirect(redirigir_a)

            # Todo OK: ejecutamos la vista real.
            return vista(request, *args, **kwargs)

        return envoltura

    return decorador


def requiere_alguno(*codigos, redirigir_a='home'):
    """Deja pasar si el perfil tiene AL MENOS UNO de esos permisos.

    Para las pantallas que comparten varios procesos: al puesto de caja
    entra quien vende, quien abre, quien cierra o quien mueve efectivo; lo
    que cada uno puede HACER ahí adentro lo vuelve a controlar cada acción.
    """
    def decorador(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            usuario = getattr(request, 'usuario_actual', None)
            if usuario is None:
                if quiere_json(request):
                    return error_json('Tenés que iniciar sesión.', 401)
                return redirect('seguridad:login')
            if not any(usuario.puede(c) for c in codigos):
                from seguridad.servicios import registrar_acceso_denegado
                registrar_acceso_denegado(request, *codigos)
                if quiere_json(request):
                    return error_json('Tu perfil no tiene habilitada esta acción.', 403)
                messages.error(
                    request,
                    'Acceso denegado: tu perfil no tiene habilitada esta '
                    'pantalla.')
                return redirect(redirigir_a)
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador
