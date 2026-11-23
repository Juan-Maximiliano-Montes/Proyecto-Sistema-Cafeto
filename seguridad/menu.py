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
# Las entradas con "ruta:" apuntan a una pantalla de proceso con URL propia;
# las demás son CRUD genéricos y se resuelven por su clave en el registro
# de nucleo.crud. El permiso de una ruta puede ser una tupla: el ítem se ve
# si el perfil tiene CUALQUIERA de ellos (a la caja entra quien vende,
# quien abre, quien cierra...).
PUESTO_DE_CAJA = ('vender', 'abrir_caja', 'cerrar_caja', 'mover_efectivo',
                  'ver_caja')

GRUPOS = [
    ('Mostrador', 'bi-cart3', [
        ('ruta:ventas:mostrador', ('Vender', 'bi-cart3', 'vender')),
        ('ruta:ventas:caja', ('Caja', 'bi-cash-stack', PUESTO_DE_CAJA)),
        ('ruta:ventas:cambios', ('Cambios y devoluciones', 'bi-arrow-repeat',
                                 'registrar_cambio')),
        ('ruta:ventas:ventas', ('Ventas', 'bi-receipt', 'ver_ventas')),
        ('ruta:ventas:turnos', ('Turnos de caja', 'bi-clock-history',
                                'ver_caja')),
        ('ruta:ventas:autorizaciones', ('Autorizaciones a distancia', 'bi-phone',
                                        ('gestionar_credito', 'autorizar_ajustes', 'supervisar_cierre'))),
    ]),
    ('Stock', 'bi-boxes', [
        ('ruta:stock:alertas', ('Alertas y vencimientos', 'bi-bell',
                                'ver_stock')),
        ('ruta:stock:ajustes', ('Conteos y mermas', 'bi-clipboard-check',
                                ('contar_stock', 'registrar_merma',
                                 'registrar_traspaso', 'autorizar_ajustes'))),
        ('ruta:stock:producciones', ('Producción', 'bi-fire',
                                     'registrar_produccion')),
        ('ruta:stock:ofertas', ('Ofertas', 'bi-tags', 'imprimir_ofertas')),
    ]),
    ('Compras y Gastos', 'bi-bag', [
        ('ruta:compras:compras', ('Compras', 'bi-receipt-cutoff',
                                  ('ver_compras', 'registrar_compra'))),
        ('ruta:compras:ordenes', ('Órdenes de compra', 'bi-journal-text',
                                  'gestionar_ordenes')),
        # 'conciliar_egresos' también: quien marca en el banco qué pagos se
        # acreditaron trabaja en esta misma pantalla (pestaña «Por
        # acreditar»). Sin él en la lista, un perfil que SÓLO concilia
        # entraba por la URL pero no veía el ítem en el menú.
        ('ruta:compras:egresos', ('Pagos y gastos', 'bi-wallet2',
                                  ('ver_egresos', 'registrar_egreso',
                                   'registrar_gasto_caja',
                                   'conciliar_egresos'))),
    ]),
    ('Inventario y Menú', 'bi-box-seam', [
        ('productos',          None),
        ('marcas',             None),
        ('rubros',             None),
        ('proveedores',        None),
    ]),
    ('Club Cafeto', 'bi-award', [
        ('clientes',           None),
        # 'gestionar_credito' es quien fija el límite de fiado y lo da por
        # incobrable: su pantalla es ésta, aunque no cobre.
        ('ruta:fidelizacion:cuentas', ('Cuentas corrientes', 'bi-journal-text',
                                       ('cobrar_credito', 'gestionar_credito'))),
        ('categorias',         None),
        ('beneficios',         None),
    ]),
    ('Ajustes del Local', 'bi-sliders', [
        ('ruta:negocio:parametros', ('Datos del local', 'bi-shop-window',
                                     'editar_parametros')),
        ('medios_pago',        None),
        ('cajas_registradoras', None),
        ('tipos_egreso',       None),
        ('provincias',         None),
        ('localidades',        None),
    ]),
    ('Reportes', 'bi-graph-up', [
        ('ruta:negocio:reportes', ('Reportes', 'bi-graph-up', 'ver_reportes')),
        ('ruta:negocio:archivo', ('Archivo histórico', 'bi-archive', 'ver_reportes')),
    ]),
    ('Equipo y Permisos', 'bi-shield-lock', [
        ('ruta:seguridad:lista',    ('Usuarios', 'bi-people', 'ver_usuarios')),
        ('ruta:seguridad:perfiles', ('Perfiles', 'bi-person-vcard',
                                    'ver_perfiles')),
        ('ruta:seguridad:permisos', ('Permisos', 'bi-key', 'ver_permisos')),
        # Va último del grupo: no se entra todos los días, se entra cuando
        # hay algo que revisar.
        ('ruta:seguridad:actividad', ('Registro de Actividad',
                                        'bi-clock-history', 'ver_auditoria')),
    ]),
]


def armar(usuario, ruta_actual=''):
    """Devuelve [{titulo, icono, items: [{titulo, icono, url, activo}]}].

    Sólo con lo que ``usuario`` puede ver. Los grupos vacíos se descartan.
    ``activo`` marca la pantalla en la que se está (la URL más larga que
    sea prefijo de la actual, así /stock/productos/ no ilumina /stock/).
    """
    from django.urls import NoReverseMatch, reverse

    from nucleo.crud import REGISTRO

    menu = []
    for titulo_grupo, icono_grupo, entradas in GRUPOS:
        items = []
        for clave, propio in entradas:
            if clave.startswith('ruta:'):
                etiqueta, icono, permiso = propio
                permisos = permiso if isinstance(permiso, tuple) else (permiso,)
                if not any(usuario.puede(p) for p in permisos):
                    continue
                try:
                    url = reverse(clave[len('ruta:'):])
                except NoReverseMatch:
                    continue        # pantalla todavía no publicada
                items.append({'titulo': etiqueta, 'icono': icono, 'url': url})
                continue

            configuracion = REGISTRO.get(clave)
            if configuracion is None:
                continue
            if not usuario.puede(configuracion.permiso('ver')):
                continue
            items.append({'titulo': configuracion.titulo,
                          'icono': configuracion.icono,
                          'url': configuracion.url('lista')})

        if items:
            menu.append({'titulo': titulo_grupo, 'icono': icono_grupo,
                         'items': items})

    candidatos = [item for grupo in menu for item in grupo['items']
                  if ruta_actual.startswith(item['url'])]
    activo = max(candidatos, key=lambda item: len(item['url']), default=None)
    for grupo in menu:
        for item in grupo['items']:
            item['activo'] = item is activo
        # El grupo de la pantalla actual se dibuja abierto; los demás,
        # plegados (el panel lateral ya no llega hasta abajo).
        grupo['activo'] = any(item['activo'] for item in grupo['items'])
    return menu
