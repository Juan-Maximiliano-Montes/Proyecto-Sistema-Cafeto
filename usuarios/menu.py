"""El menú del sistema, agrupado por lo que la persona va a hacer.

Antes había un desplegable llamado **"Datos maestros"** con las nueve
pantallas adentro. "Mantener datos maestros" es el nombre del proceso N° 10
del DFD: sirve para hablar entre quienes diseñan el sistema, no para quien
lo usa. La dueña del negocio no entra a "mantener datos maestros"; entra a
cargar un producto, a dar de alta un proveedor o a revisar los medios de
pago.

Así que el menú se arma por ÁREA DE TRABAJO, con el vocabulario del
negocio:

    Inventario y Menú    lo que se vende y quién lo provee
    Club Cafeto          el programa de clientes
    Ajustes del Local    lo que se toca una vez y casi no se vuelve a mirar
    Equipo y Permisos    quién entra y qué puede hacer

Dos criterios de orden dentro de cada grupo:

* primero lo que se usa TODOS LOS DÍAS (Productos antes que Rubros);
* las pantallas de referencia —las que existen para que las otras
  funcionen— van al final.

Cada ítem aparece sólo si el perfil tiene su permiso ``ver_*``. Si un grupo
queda sin ningún ítem visible, el grupo entero no se dibuja: un Operador no
tiene por qué enterarse de que existe la pantalla de Perfiles.
"""

# (clave del CRUD o nombre de ruta, etiqueta, ícono, permiso)
#
# Las entradas con "ruta:" apuntan a una pantalla con URL propia (las que
# existían antes de la Fase 4); las demás son CRUD genéricos y se resuelven
# por su clave en el registro de maestros.crud.
GRUPOS = [
    ('Inventario y Menú', 'bi-box-seam', [
        ('productos',          None),
        ('marcas',             None),
        ('rubros',             None),
        ('ruta:proveedores:lista', ('Proveedores', 'bi-truck',
                                    'ver_proveedores')),
    ]),
    ('Club Cafeto', 'bi-award', [
        ('categorias',         None),
        ('beneficios',         None),
    ]),
    ('Ajustes del Local', 'bi-sliders', [
        ('medios_pago',        None),
        ('cajas_registradoras', None),
        ('tipos_egreso',       None),
        ('provincias',         None),
        ('localidades',        None),
    ]),
    ('Equipo y Permisos', 'bi-shield-lock', [
        ('ruta:usuarios:lista',    ('Usuarios', 'bi-people', 'ver_usuarios')),
        ('ruta:usuarios:perfiles', ('Perfiles', 'bi-person-vcard',
                                    'ver_perfiles')),
        ('ruta:usuarios:permisos', ('Permisos', 'bi-key', 'ver_permisos')),
        # Va último del grupo: no se entra todos los días, se entra cuando
        # hay algo que revisar.
        ('ruta:operaciones:actividad', ('Registro de Actividad',
                                        'bi-clock-history', 'ver_auditoria')),
    ]),
]


def armar(usuario):
    """Devuelve [{titulo, icono, items: [{titulo, icono, url}]}, ...].

    Sólo con lo que ``usuario`` puede ver. Los grupos vacíos se descartan.
    """
    from django.urls import reverse

    from maestros.crud import REGISTRO

    menu = []
    for titulo_grupo, icono_grupo, entradas in GRUPOS:
        items = []
        for clave, propio in entradas:
            if clave.startswith('ruta:'):
                etiqueta, icono, permiso = propio
                if not usuario.tiene_permiso(permiso):
                    continue
                items.append({'titulo': etiqueta, 'icono': icono,
                              'url': reverse(clave[len('ruta:'):])})
                continue

            configuracion = REGISTRO.get(clave)
            if configuracion is None:
                continue
            if not usuario.tiene_permiso(configuracion.permiso('ver')):
                continue
            items.append({'titulo': configuracion.titulo,
                          'icono': configuracion.icono,
                          'url': configuracion.url('lista')})

        if items:
            menu.append({'titulo': titulo_grupo, 'icono': icono_grupo,
                         'items': items})
    return menu
