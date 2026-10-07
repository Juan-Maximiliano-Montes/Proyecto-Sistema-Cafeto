"""Context processors del módulo de usuarios.

``permisos_del_usuario`` deja disponible en TODOS los templates:

    permisos_usuario   set con los códigos de permiso del perfil logueado
    es_administrador    True si el perfil está marcado como perfil del
                        sistema (``Perfil.es_sistema``)

Sirve para esconder botones/enlaces con
``{% if 'crear_usuario' in permisos_usuario or es_administrador %}``.

Es sólo cosmético: la seguridad de verdad la hace ``requiere_permiso``
en la vista. Ocultar el botón evita que el usuario intente algo que
después le va a rebotar.
"""
from .models import PerfilPermiso


def permisos_del_usuario(request):
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return {'permisos_usuario': set(), 'es_administrador': False}

    codigos = set(
        PerfilPermiso.objects
        .filter(perfil=usuario.id_perfil)
        .values_list('permiso__codigo', flat=True)
    )
    return {
        'permisos_usuario': codigos,
        # Por la marca, no por el nombre: el nombre del perfil se edita
        # desde la pantalla de Perfiles (ver Perfil.es_sistema).
        'es_administrador': usuario.id_perfil.es_sistema,
    }


def menu_del_sistema(request):
    """Arma el menú de la barra de navegación según los permisos del perfil.

    La estructura y el vocabulario viven en ``usuarios/menu.py``; acá sólo
    se la pide para el usuario logueado y se la deja en el contexto.

    Es presentación. El acceso real lo corta el decorador de cada vista,
    que revalida el permiso en el servidor aunque se escriba la URL.
    """
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return {'menu_sistema': []}

    from .menu import armar      # import diferido: las apps aún no cargaron
    return {'menu_sistema': armar(usuario)}
