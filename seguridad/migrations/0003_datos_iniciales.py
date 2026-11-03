"""Datos de puesta en marcha de seguridad: permisos, perfiles y el admin.

Son los datos sin los que el sistema no se puede usar: sin permisos
ninguna vista deja pasar, sin perfil no hay a quién asignarlos y sin el
usuario inicial nadie puede entrar a cargar el resto.

El catálogo de PERMISOS es el que consultan los @requiere_permiso de
las vistas y el exige() de nucleo/crud.py: cada código existe
porque hay una vista que lo pide. Se carga completo, con su módulo, para
que la matriz de la pantalla de Perfiles diga la verdad.

Los PERFILES siguen el Principio de Menor Privilegio:

    Administrador  -> el catálogo entero, y es_sistema=True
    Encargado      -> la operación completa y las autorizaciones
                      (aprobar mermas, supervisar cierres, devolver dinero)
    Operador       -> el mostrador: vender, caja, cambios, cargar mermas
                      y producciones (que aprueba la encargada)

Ninguno de los dos operativos recibe permisos de Seguridad: son
exclusivos del administrador.

es_sistema es la marca que protege al perfil administrador: le da
acceso total y lo vuelve imposible de renombrar o dar de baja. Antes eso
se deducía de que el perfil se llamara "Administrador", un texto que el
propio administrador puede editar — ver Perfil.es_sistema.

El usuario inicial es el TITULAR del sistema y nace con
debe_cambiar_clave=True: la primera vez que entre, el middleware lo manda
a poner una clave propia.

    DNI:   30000000
    Clave: cafeto2025

Las altas son IDEMPOTENTES (get_or_create por la clave natural), así
que volver a aplicar la migración no duplica nada.
"""
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.db import migrations

CLAVE_POR_DEFECTO = 'cafeto2025'
DNI_ADMINISTRADOR = '30000000'

# (codigo, nombre, descripcion). El módulo sale del código (ver
# ``modulo_de``): así un permiso nuevo no puede quedar en el módulo
# equivocado por un error de tipeo.
PERMISOS = [
    ('ver_usuarios', 'Ver usuarios',
     'Acceder al directorio general de usuarios'),
    ('crear_usuario', 'Crear usuarios',
     'Alta de usuarios nuevos'),
    ('editar_usuario', 'Editar usuarios',
     'Modificar el correo de un usuario'),
    ('baja_usuario', 'Baja de usuarios',
     'Dar de baja lógica un usuario'),
    ('restablecer_clave', 'Restablecer claves',
     'Generar una clave provisoria nueva'),
    ('ver_perfiles', 'Ver perfiles',
     'Acceder al CRUD de perfiles'),
    ('crear_perfil', 'Crear perfiles',
     'Alta de perfiles nuevos'),
    ('editar_perfil', 'Editar perfiles',
     'Modificar un perfil y sus permisos'),
    ('eliminar_perfil', 'Eliminar perfiles',
     'Borrar un perfil que no tenga usuarios'),
    ('ver_permisos', 'Ver permisos',
     'Acceder al CRUD de permisos'),
    ('ver_proveedores', 'Ver proveedores',
     'Acceder al listado de proveedores en modo lectura'),
    ('reactivar_usuario', 'Reactivar usuarios',
     'Deshacer la baja lógica de un usuario'),
    ('crear_proveedor', 'Crear proveedores',
     'Alta de proveedores nuevos'),
    ('editar_proveedor', 'Editar proveedores',
     'Modificar los datos de un proveedor'),
    ('eliminar_proveedor', 'Eliminar proveedores',
     'Dar de baja lógica un proveedor'),
    ('reactivar_proveedor', 'Reactivar proveedores',
     'Deshacer la baja lógica de un proveedor'),
    ('reactivar_perfil', 'Reactivar perfiles',
     'Deshacer la baja lógica de un perfil'),
    ('ver_rubros', 'Ver rubros',
     'Acceder al listado de rubros en modo lectura'),
    ('crear_rubro', 'Crear rubros',
     'Dar de alta rubros'),
    ('editar_rubro', 'Editar rubros',
     'Modificar los datos de un/a rubro'),
    ('eliminar_rubro', 'Eliminar rubros',
     'Dar de baja lógica un/a rubro'),
    ('reactivar_rubro', 'Reactivar rubros',
     'Deshacer la baja lógica de un/a rubro'),
    ('ver_marcas', 'Ver marcas',
     'Acceder al listado de marcas en modo lectura'),
    ('crear_marca', 'Crear marcas',
     'Dar de alta marcas'),
    ('editar_marca', 'Editar marcas',
     'Modificar los datos de un/a marca'),
    ('eliminar_marca', 'Eliminar marcas',
     'Dar de baja lógica un/a marca'),
    ('reactivar_marca', 'Reactivar marcas',
     'Deshacer la baja lógica de un/a marca'),
    ('ver_productos', 'Ver productos',
     'Acceder al listado de productos en modo lectura'),
    ('crear_producto', 'Crear productos',
     'Dar de alta productos'),
    ('editar_producto', 'Editar productos',
     'Modificar los datos de un/a producto'),
    ('eliminar_producto', 'Eliminar productos',
     'Dar de baja lógica un/a producto'),
    ('reactivar_producto', 'Reactivar productos',
     'Deshacer la baja lógica de un/a producto'),
    ('ver_tipos_egreso', 'Ver tipos de egreso',
     'Acceder al listado de tipos de egreso en modo lectura'),
    ('crear_tipo_egreso', 'Crear tipos de egreso',
     'Dar de alta tipos de egreso'),
    ('editar_tipo_egreso', 'Editar tipos de egreso',
     'Modificar los datos de un/a tipo de egreso'),
    ('eliminar_tipo_egreso', 'Eliminar tipos de egreso',
     'Dar de baja lógica un/a tipo de egreso'),
    ('reactivar_tipo_egreso', 'Reactivar tipos de egreso',
     'Deshacer la baja lógica de un/a tipo de egreso'),
    ('ver_medios_pago', 'Ver medios de pago',
     'Acceder al listado de medios de pago en modo lectura'),
    ('crear_medio_pago', 'Crear medios de pago',
     'Dar de alta medios de pago'),
    ('editar_medio_pago', 'Editar medios de pago',
     'Modificar los datos de un/a medio de pago'),
    ('eliminar_medio_pago', 'Eliminar medios de pago',
     'Dar de baja lógica un/a medio de pago'),
    ('reactivar_medio_pago', 'Reactivar medios de pago',
     'Deshacer la baja lógica de un/a medio de pago'),
    ('ver_cajas_registradoras', 'Ver cajas registradoras',
     'Acceder al listado de cajas registradoras en modo lectura'),
    ('crear_caja_registradora', 'Crear cajas registradoras',
     'Dar de alta cajas registradoras'),
    ('editar_caja_registradora', 'Editar cajas registradoras',
     'Modificar los datos de un/a caja registradora'),
    ('eliminar_caja_registradora', 'Eliminar cajas registradoras',
     'Dar de baja lógica un/a caja registradora'),
    ('reactivar_caja_registradora', 'Reactivar cajas registradoras',
     'Deshacer la baja lógica de un/a caja registradora'),
    ('ver_categorias', 'Ver categorías de fidelización',
     'Acceder al listado de categorías de fidelización en modo lectura'),
    ('crear_categoria', 'Crear categorías de fidelización',
     'Dar de alta categorías de fidelización'),
    ('editar_categoria', 'Editar categorías de fidelización',
     'Modificar los datos de un/a categoría de fidelización'),
    ('eliminar_categoria', 'Eliminar categorías de fidelización',
     'Dar de baja lógica un/a categoría de fidelización'),
    ('reactivar_categoria', 'Reactivar categorías de fidelización',
     'Deshacer la baja lógica de un/a categoría de fidelización'),
    ('ver_beneficios', 'Ver beneficios',
     'Acceder al listado de beneficios en modo lectura'),
    ('crear_beneficio', 'Crear beneficios',
     'Dar de alta beneficios'),
    ('editar_beneficio', 'Editar beneficios',
     'Modificar los datos de un/a beneficio'),
    ('eliminar_beneficio', 'Eliminar beneficios',
     'Dar de baja lógica un/a beneficio'),
    ('reactivar_beneficio', 'Reactivar beneficios',
     'Deshacer la baja lógica de un/a beneficio'),
    ('ver_provincias', 'Ver provincias',
     'Acceder al listado de provincias en modo lectura'),
    ('crear_provincia', 'Crear provincias',
     'Dar de alta provincias'),
    ('editar_provincia', 'Editar provincias',
     'Modificar los datos de un/a provincia'),
    ('ver_localidades', 'Ver localidades',
     'Acceder al listado de localidades en modo lectura'),
    ('crear_localidad', 'Crear localidades',
     'Dar de alta localidades'),
    ('editar_localidad', 'Editar localidades',
     'Modificar los datos de un/a localidad'),
    ('ver_auditoria', 'Ver el registro de actividad',
     'Acceder al Registro de Actividad (auditoría) en modo lectura'),
    ('cambiar_perfil_usuario', 'Cambiar el perfil de un usuario',
     'Cambio permanente de perfil, con re-autenticación'),
    ('asignar_perfil_temporal', 'Asignar perfiles temporales',
     'Prestar un perfil por un rango de fechas (reemplazos)'),
]

# Los permisos de los PROCESOS (vender, contar, aprobar...). No siguen el
# molde ver/crear/editar de los catálogos, así que llevan el módulo escrito.
# (codigo, nombre, descripcion, modulo)
PERMISOS_DE_PROCESO = [
    # --- Ventas y caja ---
    ('vender', 'Vender', 'Cobrar en el mostrador', 'VENTAS'),
    ('abrir_caja', 'Abrir la caja', 'Abrir el turno de un cajón', 'VENTAS'),
    ('cerrar_caja', 'Cerrar la caja', 'Arqueo y cierre del turno', 'VENTAS'),
    ('supervisar_cierre', 'Supervisar cierres',
     'Autorizar un cierre con diferencia mayor a la tolerada', 'VENTAS'),
    ('mover_efectivo', 'Refuerzos y retiros',
     'Traer cambio al cajón o sacar efectivo', 'VENTAS'),
    ('registrar_cambio', 'Registrar cambios',
     'Cambios y devoluciones de mercadería', 'VENTAS'),
    ('autorizar_devolucion', 'Autorizar devoluciones de dinero',
     'Autorizar que se le reintegre dinero a un cliente', 'VENTAS'),
    ('ver_ventas', 'Ver ventas', 'Consultar tickets y su detalle', 'VENTAS'),
    ('ver_caja', 'Ver turnos de caja',
     'Consultar turnos, arqueos y movimientos de efectivo', 'VENTAS'),
    # --- Stock ---
    ('ver_stock', 'Ver stock', 'Movimientos, lotes, vencimientos y alertas',
     'STOCK'),
    ('contar_stock', 'Contar stock', 'Cargar conteos de inventario',
     'STOCK'),
    ('registrar_merma', 'Registrar mermas',
     'Cargar mermas (quedan pendientes de aprobación)', 'STOCK'),
    ('registrar_traspaso', 'Registrar traspasos',
     'Pasar mercadería de un producto a otro', 'STOCK'),
    ('autorizar_ajustes', 'Autorizar ajustes',
     'Aprobar o rechazar conteos, mermas y traspasos', 'STOCK'),
    ('registrar_produccion', 'Registrar producciones',
     'Cargar las tandas de lo que se fabrica en el local', 'STOCK'),
    ('imprimir_ofertas', 'Imprimir ofertas',
     'Etiquetas del sector de productos próximos a vencer', 'STOCK'),
    ('actualizar_precios', 'Actualizar precios en masa',
     'Aumentos por rubro, marca o proveedor', 'STOCK'),
    ('importar_productos', 'Importar productos',
     'Alta y actualización de productos desde Excel', 'STOCK'),
    # --- Compras ---
    ('ver_compras', 'Ver compras', 'Consultar facturas de compra',
     'COMPRAS'),
    ('registrar_compra', 'Registrar compras',
     'Ingreso de mercadería con su factura', 'COMPRAS'),
    ('gestionar_ordenes', 'Órdenes de compra',
     'Armar, enviar y cancelar pedidos a proveedores', 'COMPRAS'),
    ('ver_egresos', 'Ver egresos', 'Consultar pagos y vencimientos',
     'COMPRAS'),
    ('registrar_egreso', 'Registrar egresos',
     'Obligaciones y pagos a proveedores y servicios', 'COMPRAS'),
    ('registrar_gasto_caja', 'Gastos de caja',
     'Pagar un gasto con el efectivo del cajón', 'COMPRAS'),
    ('conciliar_egresos', 'Conciliar con el banco',
     'Confirmar o rechazar transferencias', 'COMPRAS'),
    ('importar_proveedores', 'Importar proveedores',
     'Alta y actualización de proveedores desde Excel', 'COMPRAS'),
    # --- Negocio ---
    ('ver_reportes', 'Ver reportes', 'Ventas, márgenes, caja y stock',
     'NEGOCIO'),
    ('editar_parametros', 'Editar parámetros',
     'Datos del comercio y reglas de operación', 'NEGOCIO'),
]

# Lo que cada perfil operativo recibe de entrada. Se ajusta desde la
# pantalla de Perfiles; esto es el punto de partida razonable.
PERMISOS_OPERADOR = [
    'vender', 'abrir_caja', 'cerrar_caja', 'registrar_cambio',
    'registrar_merma', 'registrar_produccion', 'contar_stock', 'ver_stock',
    'imprimir_ofertas', 'ver_productos',
]
PERMISOS_ENCARGADO = PERMISOS_OPERADOR + [
    'supervisar_cierre', 'mover_efectivo', 'autorizar_devolucion',
    'ver_ventas', 'ver_caja', 'registrar_traspaso', 'autorizar_ajustes',
    'actualizar_precios', 'importar_productos', 'ver_compras',
    'registrar_compra', 'gestionar_ordenes', 'ver_egresos',
    'registrar_egreso', 'registrar_gasto_caja', 'conciliar_egresos',
    'importar_proveedores', 'ver_reportes',
    'crear_producto', 'editar_producto', 'ver_rubros', 'crear_rubro',
    'editar_rubro', 'ver_marcas', 'crear_marca', 'editar_marca',
    'ver_proveedores', 'crear_proveedor', 'editar_proveedor',
    'ver_tipos_egreso', 'ver_medios_pago', 'ver_cajas_registradoras',
    'ver_provincias', 'ver_localidades', 'crear_localidad',
]

# Qué módulo del DER es dueño de cada entidad. Los de SEGURIDAD son
# exclusivos del perfil administrador (ver seguridad.models).
MODULO_POR_ENTIDAD = {
    'usuario': 'SEGURIDAD', 'perfil': 'SEGURIDAD', 'permiso': 'SEGURIDAD',
    'auditoria': 'SEGURIDAD', 'clave': 'SEGURIDAD',
    'provincia': 'GEOGRAFIA', 'localidad': 'GEOGRAFIA',
    'rubro': 'STOCK', 'marca': 'STOCK', 'producto': 'STOCK',
    'medio_pago': 'VENTAS', 'medios_pago': 'VENTAS',
    'caja_registradora': 'VENTAS', 'cajas_registradoras': 'VENTAS',
    'proveedor': 'COMPRAS', 'tipo_egreso': 'COMPRAS',
    'tipos_egreso': 'COMPRAS',
    'categoria': 'FIDELIZACION', 'beneficio': 'FIDELIZACION',
}


def modulo_de(codigo):
    """'editar_tipo_egreso' -> 'COMPRAS'; 'ver_cajas_registradoras' -> 'VENTAS'."""
    resto = codigo.split('_', 1)[1]
    for entidad in sorted(MODULO_POR_ENTIDAD, key=len, reverse=True):
        if resto == entidad or resto.startswith(entidad):
            return MODULO_POR_ENTIDAD[entidad]
    raise ValueError(f'Permiso sin módulo: {codigo}')

PERFIL_DE_SISTEMA = 'Administrador'

# (nombre, descripcion, es_sistema, tope de descuento %). El tope NULL es
# "sin tope" y sólo lo tiene el perfil de sistema.
PERFILES = [
    ('Administrador', 'Acceso total al sistema', True, None),
    ('Encargado', 'Autoriza mermas, ajustes, cierres y descuentos mayores',
     False, Decimal('20')),
    ('Operador', 'Atención en mostrador: vende y cobra', False,
     Decimal('10')),
]


def cargar(apps, schema_editor):
    Perfil = apps.get_model('seguridad', 'Perfil')
    Permiso = apps.get_model('seguridad', 'Permiso')
    PerfilPermiso = apps.get_model('seguridad', 'PerfilPermiso')
    Usuario = apps.get_model('seguridad', 'Usuario')

    # 1) El catálogo de permisos ----------------------------------------
    permisos = []
    catalogo = [(c, n, d, modulo_de(c)) for c, n, d in PERMISOS]
    catalogo += PERMISOS_DE_PROCESO
    for codigo, nombre, descripcion, modulo in catalogo:
        permiso, _ = Permiso.objects.get_or_create(
            codigo=codigo,
            defaults={'nombre': nombre, 'descripcion': descripcion,
                      'modulo': modulo})
        permisos.append(permiso)

    # 2) Los perfiles base ----------------------------------------------
    perfiles = {}
    for nombre, descripcion, es_sistema, tope in PERFILES:
        perfiles[nombre], _ = Perfil.objects.get_or_create(
            nombre=nombre,
            defaults={'descripcion': descripcion, 'es_sistema': es_sistema,
                      'tope_descuento_porcentaje': tope})

    # El perfil de sistema lleva el catálogo COMPLETO. Además
    # Usuario.tiene_permiso() lo deja pasar siempre por la marca, pero
    # la matriz de la pantalla tiene que mostrar la verdad.
    administrador = perfiles[PERFIL_DE_SISTEMA]
    for permiso in permisos:
        PerfilPermiso.objects.get_or_create(
            perfil=administrador, permiso=permiso)

    por_codigo = {p.codigo: p for p in permisos}
    for nombre, codigos in (('Operador', PERMISOS_OPERADOR),
                            ('Encargado', PERMISOS_ENCARGADO)):
        for codigo in codigos:
            PerfilPermiso.objects.get_or_create(
                perfil=perfiles[nombre], permiso=por_codigo[codigo])

    # 3) El usuario administrador inicial -------------------------------
    Usuario.objects.get_or_create(
        dni_usuario=DNI_ADMINISTRADOR,
        defaults={
            'apellido': 'Montes',
            'nombre': 'Juan',
            'correo': 'admin@cafeto.com',
            'id_perfil': administrador,
            'nombre_usuario': 'montesj',
            'clave': make_password(CLAVE_POR_DEFECTO),
            'debe_cambiar_clave': True,
            # El primer administrador es el TITULAR: el dueño del sistema.
            'es_titular': True,
        })


def borrar(apps, schema_editor):
    """Deshace la carga.

    Va contra _base_manager y con un DELETE directo: en operación
    normal el mixin SinBajaFisica prohíbe el borrado físico de estas
    tablas, pero al revertir una migración lo que se quiere es dejar la
    base como estaba.
    """
    for modelo in ('Usuario', 'PerfilPermiso', 'Perfil', 'Permiso'):
        apps.get_model('seguridad', modelo)._base_manager.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('seguridad', '0002_initial'),
    ]

    operations = [
        migrations.RunPython(cargar, borrar),
    ]
