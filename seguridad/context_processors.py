"""Context processors del módulo de Seguridad.

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
def permisos_del_usuario(request):
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return {'permisos_usuario': frozenset(), 'es_administrador': False}
    codigos = usuario.codigos_de_permiso()
    if codigos is None:
        # Perfil de sistema: pasa siempre. Se arma igual el conjunto
        # completo para que un `{% if 'x' in permisos_usuario %}` funcione.
        from seguridad.models import Permiso
        codigos = frozenset(Permiso.objects.values_list('codigo', flat=True))
        return {'permisos_usuario': codigos, 'es_administrador': True}
    # Por el perfil EFECTIVO: quien cubre con un perfil temporal ve los
    # botones de ese perfil, no los del suyo.
    return {'permisos_usuario': codigos, 'es_administrador': False}


def menu_del_sistema(request):
    """Arma el menú lateral según los permisos del perfil.

    La estructura y el vocabulario viven en ``seguridad/menu.py``; acá sólo
    se la pide para el usuario logueado y se la deja en el contexto.

    Es presentación. El acceso real lo corta el decorador de cada vista,
    que revalida el permiso en el servidor aunque se escriba la URL.
    """
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return {'menu_sistema': []}

    from .menu import armar  # import diferido: las apps aún no cargaron
    return {'menu_sistema': armar(usuario, request.path)}
