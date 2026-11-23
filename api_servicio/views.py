"""Lo que una automatización puede pedirle al sistema (DD-84)."""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from api_servicio.autenticacion import de_servicio
from nucleo import json_seguro
from nucleo.textos import pluralizar, segun

TOPE = 200



def _error(mensaje, estado):
    """El error de la API de servicio: ``detail`` (lo que lee n8n) y ``error``
    (lo que lee la web), con el mismo texto."""
    return JsonResponse({'ok': False, 'detail': mensaje, 'error': mensaje}, status=estado)

@de_servicio
@require_GET
def resumen(request):
    """Los números y los avisos del día, como los ve la dueña en el inicio."""
    from negocio import panel
    from seguridad.models import Usuario

    duenia = Usuario.objects.filter(es_titular=True, activo=True).first()
    if duenia is None:
        return JsonResponse({'detail': 'El sistema no tiene titular.'}, status=409)
    numeros, avisos = panel.del_dia(duenia)
    from negocio import ganancia

    hoy = timezone.localdate()
    ventas = ganancia.completadas(hoy, hoy)
    del_dia = ganancia.de_ventas(ventas)
    return JsonResponse({
        'ventas_hoy': str(del_dia['facturado']),
        'ganancia_bruta_hoy': str(del_dia['ganancia']),
        'renglones_sin_costo_hoy': del_dia['sin_costo'],
        'desglose_medios_pago_hoy': [{'medio': medio, 'total': str(total)}
                                     for medio, total in ganancia.por_medio_de_pago(ventas)],
        'vencimientos_proximos': _vencimientos(hoy),
        'proveedores_inactivos_6m': _inactivos(hoy),
        'cuentas_corrientes': _cuentas_corrientes(hoy),
        'cajas_a_revisar': _cajas_a_revisar(),
        'fecha': timezone.localdate().isoformat(),
        'numeros': [{'titulo': n['titulo'], 'valor': str(n['valor']), 'detalle': n.get('detalle', '')}
                    for n in numeros],
        'avisos': [{'clave': a['clave'], 'tipo': a['tipo'], 'texto': a['texto']} for a in avisos],
    })


DIAS_DE_VENCIMIENTOS = 3


def _cajas_a_revisar():
    """Los cierres con una diferencia que nadie aprobó (DD-97)."""
    from ventas import caja

    return [{'turno': t.pk, 'caja': str(t.id_caja_registradora),
             'cerro': f'{t.id_usuario_cierre.nombre} {t.id_usuario_cierre.apellido}'.strip(),
             'fecha_cierre': timezone.localtime(t.fecha_cierre).isoformat(),
             'diferencia': str(t.diferencia_caja), 'motivo': t.observacion_diferencia or ''}
            for t in caja.pendientes_de_revision()[:TOPE]]


def _cuentas_corrientes(hoy):
    from fidelizacion import credito

    return credito.alertas(hoy, DIAS_DE_VENCIMIENTOS)


def _inactivos(hoy):
    from compras import habitual

    return [{'id': p.pk, 'nombre': p.nombre_proveedor, 'cuit': p.cuit_proveedor,
             'ultima_compra': p.ultima_compra.isoformat() if p.ultima_compra else None,
             'productos_a_su_nombre': p.productos.filter(borrado_logico=False).count()}
            for p in habitual.inactivos(hoy)[:TOPE] if not p.es_el_general]


def _vencimientos(hoy):
    """Lo que hay que pagar de acá a tres días (y lo que ya venció)."""
    from compras.models import Compra, Egreso

    limite = hoy + timedelta(days=DIAS_DE_VENCIMIENTOS)
    filas = [{'tipo': 'gasto', 'a_quien': str(e.id_proveedor or e.destinatario or e.id_tipo_egreso),
              'concepto': e.concepto, 'vence': e.fecha_vencimiento.isoformat(),
              'vencido': e.fecha_vencimiento < hoy, 'monto': str(e.monto)}
             for e in Egreso.objects.filter(estado_pago='PENDIENTE_PAGO', fecha_vencimiento__lte=limite)
                                    .select_related('id_proveedor', 'id_tipo_egreso')
                                    .order_by('fecha_vencimiento')[:TOPE]]
    compras = Compra.con_pagado(
        Compra.objects.exclude(estado_compra='PAGADA')
        .filter(fecha_vencimiento_pago__lte=limite).select_related('id_proveedor')
        .order_by('fecha_vencimiento_pago'))[:TOPE]
    for compra in compras:
        if compra.saldo_pendiente > 0:
            filas.append({'tipo': 'compra', 'a_quien': str(compra.id_proveedor),
                          'concepto': f'{compra.get_tipo_comprobante_display()} {compra.numero_formateado}',
                          'vence': compra.fecha_vencimiento_pago.isoformat(),
                          'vencido': compra.fecha_vencimiento_pago < hoy,
                          'monto': str(compra.saldo_pendiente)})
    return sorted(filas, key=lambda f: f['vence'])


@de_servicio
@require_GET
def ordenes_emitidas(request):
    """Las órdenes emitidas en los últimos días, con a quién mandarlas.

    Quien las manda lleva la cuenta de cuáles ya mandó (el ``id``): el
    sistema no guarda "enviada", porque que salió el mensaje no lo sabe él.
    """
    from compras.models import OrdenCompra

    try:
        dias = max(1, min(int(request.GET.get('dias', 2)), 30))
    except ValueError:
        dias = 2
    ordenes = (OrdenCompra.objects.filter(
        estado_orden='EMITIDA', fecha_orden__gte=timezone.localdate() - timedelta(days=dias))
        .select_related('id_proveedor').order_by('pk')[:TOPE])
    return JsonResponse({'ordenes': [{
        'id': o.pk, 'fecha': o.fecha_orden.isoformat(), 'total': str(o.total_orden),
        'pdf': f'/api/servicio/ordenes/{o.pk}/pdf/',
        'proveedor': {'nombre': o.id_proveedor.nombre_proveedor,
                      'correo': o.id_proveedor.email_proveedor,
                      'telefono': o.id_proveedor.telefono_proveedor},
    } for o in ordenes]})


@de_servicio
@require_GET
def orden_pdf(request, pk):
    from compras.models import OrdenCompra
    from compras.views.ordenes import pdf_de_la_orden

    orden = get_object_or_404(
        OrdenCompra.objects.select_related('id_proveedor'), pk=pk, estado_orden='EMITIDA')
    respuesta = HttpResponse(pdf_de_la_orden(orden), content_type='application/pdf')
    respuesta['Content-Disposition'] = f'attachment; filename="orden-de-compra-{orden.pk}.pdf"'
    return respuesta


@de_servicio
@require_GET
def productos_sin_foto(request):
    """Productos con código de barras y sin foto: los que se pueden buscar."""
    from stock.models import Producto

    productos = (Producto.objects.filter(borrado_logico=False, imagen__isnull=True)
                 .exclude(codigo_barras__isnull=True).select_related('id_marca')
                 .order_by('pk')[:TOPE])
    return JsonResponse({'productos': [{
        'id': p.pk, 'codigo_barras': p.codigo_barras, 'nombre': p.nombre_producto,
        'marca': p.id_marca.nombre_marca if p.id_marca_id else None,
    } for p in productos]})


@de_servicio
@require_POST
def foto_de_producto(request, pk):
    """Le pone a un producto la dirección https:// de su foto."""
    from seguridad.servicios import registrar_auditoria
    from stock import imagenes
    from stock.models import Producto

    producto = get_object_or_404(Producto, pk=pk, borrado_logico=False)
    try:
        datos = json_seguro.del_pedido(request)
        url = json_seguro.campo(datos, 'url', str, 'La dirección de la foto ("url")')
        antes = producto.imagen
        imagenes.poner_url(producto, url)
    except (ValueError, TypeError, ValidationError) as error:
        mensaje = ' '.join(getattr(error, 'messages', ['El cuerpo tiene que ser {"url": "https://…"}.']))
        return JsonResponse({'detail': mensaje}, status=400)
    registrar_auditoria(
        None, 'MODIFICACION', 'PRODUCTO', instancia=producto,
        anterior={'id_producto': producto.pk, 'imagen': antes},
        nuevo={'id_producto': producto.pk, 'imagen': producto.imagen},
        detalle=f'Una automatización le puso la foto a {producto}.')
    return JsonResponse({'id': producto.pk, 'imagen': producto.imagen})


@de_servicio
@require_GET
def productos_sin_marca(request):
    """Productos con código y la marca «Genérica»: los que se pueden completar."""
    from stock.models import MARCA_GENERICA, Producto

    productos = (Producto.objects.filter(borrado_logico=False, id_marca__nombre_marca=MARCA_GENERICA)
                 .exclude(codigo_barras__isnull=True).order_by('pk')[:TOPE])
    return JsonResponse({'productos': [{
        'id': p.pk, 'codigo_barras': p.codigo_barras, 'nombre': p.nombre_producto,
        'sin_foto': not p.imagen} for p in productos]})


@de_servicio
@require_POST
def enriquecer_producto(request, pk):
    """Completa lo que el producto todavía no tiene: la foto y/o la marca.

    La marca sólo reemplaza a «Genérica»: una marca que cargó una persona
    no la pisa una automatización. Si con la marca nueva el producto
    quedaría igual a otro (misma marca, nombre y presentación), no se toca.
    """
    from django.db import transaction
    from seguridad.servicios import registrar_auditoria
    from stock import imagenes
    from stock.models import Producto

    producto = get_object_or_404(Producto.objects.select_related('id_marca'), pk=pk, borrado_logico=False)
    try:
        datos = json_seguro.del_pedido(request)
        url = json_seguro.campo(datos, 'url', str, 'La dirección de la foto ("url")')
        marca = json_seguro.campo(datos, 'marca', str, 'La marca ("marca")')
        if not (url or (marca or '').strip()):
            raise ValueError
    except json_seguro.PedidoMalArmado as error:
        return _error(' '.join(error.messages), 400)
    except ValueError:
        return _error('El cuerpo lleva "url", "marca" o las dos.', 400)
    antes = {'id_producto': producto.pk, 'imagen': producto.imagen, 'marca': producto.id_marca.nombre_marca}
    hecho, omitido = [], {}
    try:
        with transaction.atomic():
            if url:
                imagenes.poner_url(producto, url)
                hecho.append('foto')
            if (marca or '').strip():
                from stock.reservados import asignar_marca
                puesta, motivo = asignar_marca(producto, marca)
                if puesta:
                    hecho.append('marca')
                else:
                    omitido['marca'] = motivo
    except ValidationError as error:
        return JsonResponse({'detail': ' '.join(error.messages)}, status=400)
    if hecho:
        registrar_auditoria(
            None, 'MODIFICACION', 'PRODUCTO', instancia=producto, anterior=antes,
            nuevo={'id_producto': producto.pk, 'imagen': producto.imagen,
                   'marca': producto.id_marca.nombre_marca},
            detalle=f'Una automatización le completó {" y ".join(hecho)} a {producto}.')
    return JsonResponse({'id': producto.pk, 'imagen': producto.imagen,
                         'marca': producto.id_marca.nombre_marca,
                         'actualizado': hecho, 'omitido': omitido})


@de_servicio
@require_POST
def precargar_compra(request):
    """Deja la factura leída en la bandeja de «Compras» y devuelve el enlace
    a la recepción ya escrita. No registra la compra (DD-88, DD-98).

    Opcionales: ``origen`` (WHATSAPP, CORREO u OTRO) y ``remitente`` (el
    número o la dirección de donde vino). Un reintento con la misma factura
    no la deja dos veces: devuelve la que ya estaba esperando (200)."""
    from django.urls import reverse

    from compras import facturas

    try:
        datos = json_seguro.del_pedido(request)
        factura, resumen, nueva = facturas.recibir(
            datos, origen=json_seguro.campo(datos, 'origen', str, 'El origen'),
            remitente=json_seguro.campo(datos, 'remitente', str, 'El remitente'))
    except (ValueError, TypeError, AttributeError):
        return _error('La factura no tiene la forma esperada: revisá encabezado y renglones.', 400)
    except ValidationError as error:
        return _error(' '.join(error.messages), 400)
    if nueva:
        from seguridad.servicios import instantanea, registrar_auditoria
        registrar_auditoria(request, 'ALTA', 'FACTURA RECIBIDA', instancia=factura,
                            nuevo=instantanea(factura), modulo='COMPRAS',
                            detalle=f'Llegó por {factura.get_origen_factura_display()} la factura '
                                    f'"{factura.resumen_factura}": queda para revisar y confirmar.')
    ruta = reverse('compras:recepcion') + f'?factura={factura.pk}'
    base = _base_publica(request)
    return JsonResponse({**resumen, 'id': factura.pk, 'url': base + ruta, 'registrada': False,
                         'en_bandeja': True, 'ya_estaba': not nueva,
                         'pendientes': facturas.pendientes().count()},
                        status=201 if nueva else 200)


@de_servicio
@require_POST
def leer_factura(request):
    """El lector local de facturas, para la automatización (DD-98).

    Recibe ``{"nombre", "tipo", "archivo"}`` (el archivo en base64) y
    devuelve lo leído en el formato de ``pre-cargar``. No guarda nada ni
    llama a otra automatización."""
    import base64
    import binascii

    from compras import lector_facturas

    try:
        datos = json_seguro.del_pedido(request)
        archivo = json_seguro.campo(datos, 'archivo', str, 'El archivo ("archivo", en base64)', '')
        tipo = json_seguro.campo(datos, 'tipo', str, 'El tipo del archivo ("tipo")')
        nombre = json_seguro.campo(datos, 'nombre', str, 'El nombre del archivo ("nombre")')
        contenido = base64.b64decode(archivo, validate=False)
    except json_seguro.PedidoMalArmado as error:
        return _error(' '.join(error.messages), 400)
    except (ValueError, binascii.Error):
        return _error('El archivo no está en base64.', 400)
    try:
        texto, qr = lector_facturas.texto_de(contenido, tipo, nombre)
    except ValidationError as error:
        return _error(' '.join(error.messages), 422)
    lectura = lector_facturas.interpretar(texto, qr)
    # Lo que la automatización necesita para el paso siguiente (pre-cargar).
    for clave in ('origen', 'remitente'):
        if datos.get(clave):
            lectura[clave] = str(datos[clave])[:80]
    if not lectura.get('renglones'):
        return JsonResponse({'detail': 'Se leyó el archivo pero no se encontraron renglones con cantidad y precio.',
                             **lectura}, status=422)
    return JsonResponse(lectura)


@de_servicio
@require_POST
def mantenimiento_mensual(request):
    """La rutina del día 1: archiva lo viejo y ordena los proveedores.

    1. ``pasar_a_historico --ejecutar`` (la única baja física del sistema).
    2. Los productos de proveedores sin movimiento hace más de 180 días
       pasan al último que sí los vendió (``compras.habitual``).

    Con ``?simular=1`` informa lo que haría y no cambia nada. No da de baja
    a ningún proveedor: eso lo decide una persona.
    """
    import io
    import re

    from django.core.management import call_command
    from django.db import transaction

    from compras import habitual
    from seguridad.servicios import registrar_auditoria

    simular = request.GET.get('simular') == '1'
    salida = io.StringIO()
    call_command('pasar_a_historico', *([] if simular else ['--ejecutar']), stdout=salida, no_color=True)
    texto = salida.getvalue()
    total = re.search(r'(\d+) fila\(s\)', texto)
    hoy = timezone.localdate()
    with transaction.atomic():
        cambios = habitual.reasignar_los_de_inactivos(hoy)
        reasignados = [{'producto': str(p), 'antes': str(a), 'ahora': str(n)} for p, a, n in cambios]
        if simular:
            transaction.set_rollback(True)
    inactivos = _inactivos(hoy)
    if not simular:
        registrar_auditoria(
            None, 'MANTENIMIENTO', 'MENSUAL', modulo='NUCLEO',
            anterior={'proveedor_habitual': [{'producto': r['producto'], 'proveedor': r['antes']}
                                             for r in reasignados[:TOPE]]},
            nuevo={'archivadas': int(total.group(1)) if total else 0, 'reasignados': reasignados[:TOPE]},
            detalle=(f"Mantenimiento mensual automático: {pluralizar(int(total.group(1)) if total else 0, 'fila')} "
                     f"al histórico, {pluralizar(len(reasignados), 'producto')} "
                     f"{segun(len(reasignados), 'cambió', 'cambiaron')} de proveedor habitual, "
                     f"{pluralizar(len(inactivos), 'proveedor', 'proveedores')} sin movimiento hace más de 6 meses."))
    return JsonResponse({
        'simulacion': simular,
        'filas_al_historico': int(total.group(1)) if total else 0,
        'detalle_del_historico': [linea.strip() for linea in texto.splitlines() if linea.strip()][:60],
        'productos_reasignados': reasignados[:TOPE], 'cantidad_reasignados': len(reasignados),
        'proveedores_inactivos_6m': inactivos,
    })


# ===========================================================================
#  DIAGNÓSTICO PROGRESIVO DEL CATÁLOGO (Etapa 2)
# ===========================================================================
#  Cuatro consultas de SÓLO LECTURA para que la automatización acompañe el
#  saneamiento de lo que vino del sistema anterior sin frenar el mostrador:
#  qué elaborado se vende todo el día sin receta, qué se vende a pérdida o
#  sin costo, qué conviene contar hoy y qué quedó sin rubro. Ninguna cambia
#  nada: lo que se hace con esto lo decide una persona (o una SUGERENCIA,
#  que también aprueba una persona).
RUBROS_SIN_CLASIFICAR = ('Sin Clasificar', 'Otros Rubros')
DIAS_DE_ROTACION = 30
MOTIVOS_DE_MARGEN = ('SIN_COSTO', 'A_PERDIDA', 'MARGEN_BAJO')


def _entero(request, clave, defecto, minimo, maximo):
    try:
        return max(minimo, min(int(request.GET.get(clave, defecto)), maximo))
    except (TypeError, ValueError):
        return defecto


#  Los nombres de servicio de docker-compose.yml: adentro de la red de Docker
#  resuelven, afuera no existen. n8n le pega a la API por ``http://web:8000``,
#  así que cuando el enlace lo arma un flujo, el host del pedido ES uno de
#  éstos.
#
#  La lista es cerrada a propósito. Descartar "cualquier nombre sin punto"
#  sería más amplio, pero de más: el sistema corre en un NAS del local que
#  puede llamarse ``cafeto`` y resolverse por la red interna, y ése es un
#  enlace que SÍ abre desde el celular de la dueña. Si mañana se renombra un
#  contenedor en docker-compose.yml, se agrega acá.
HOSTS_DE_DOCKER = {'web', 'db', 'n8n', 'waha', 'adminer', 'nginx'}


def _partir_host(host):
    """``"web:8000"`` -> ``("web", "8000")``; ``"[::1]:8000"`` -> ``("::1", "8000")``."""
    nombre = (host or '').strip().lower()
    if nombre.startswith('['):
        cierre = nombre.find(']')
        if cierre > 0:
            puerto = nombre[cierre + 1:].lstrip(':')
            return nombre[1:cierre], puerto
        return nombre[1:], ''
    cabeza, dos_puntos, cola = nombre.rpartition(':')
    if dos_puntos and cola.isdigit():
        return cabeza, cola
    return nombre, ''


def _es_alcanzable(host):
    """¿Ese nombre lo puede abrir alguien que no esté dentro de Docker?"""
    nombre = _partir_host(host)[0]
    return bool(nombre) and nombre not in HOSTS_DE_DOCKER


def _base_publica(request):
    """La dirección con la que se arma el enlace que viaja por WhatsApp.

    Manda ``CAFETO_URL_PUBLICA``: es la misma variable con la que se arman
    el enlace de autorización a distancia (``ventas.views.autorizaciones``),
    el de recuperación de clave y las fotos de la app móvil. Si está, no se
    mira nada más.

    Sin ella se usaba el host del pedido, y ahí aparecía el problema: el
    flujo «Recetas y márgenes» lo dispara n8n DESDE Docker, contra
    ``http://web:8000``, así que a la dueña le llegaba por WhatsApp
    ``http://web:8000/stock/productos/412/editar/`` —un nombre que no
    resuelve ni en el celular ni en el navegador de la caja—. Un enlace que
    no abre es peor que no mandar enlace: parece que el sistema está caído.

    Así que el host del pedido se usa salvo que sea uno de los nombres de
    servicio de Docker; en ese caso se cae a los nombres que el propio
    sistema declara como suyos (``DJANGO_ALLOWED_HOSTS``) y, en última
    instancia, a ``localhost`` con el puerto del pedido, que al menos abre
    en la máquina donde corre el sistema.
    """
    from django.conf import settings

    configurada = (getattr(settings, 'CAFETO_URL_PUBLICA', '') or '').strip()
    if configurada:
        return configurada.rstrip('/')

    esquema = 'https' if request.is_secure() else 'http'
    host = request.get_host()
    if _es_alcanzable(host):
        return f'{esquema}://{host}'.rstrip('/')
    for declarado in getattr(settings, 'ALLOWED_HOSTS', None) or []:
        candidato = str(declarado).lstrip('.')
        if candidato != '*' and _es_alcanzable(candidato):
            return f'{esquema}://{candidato}'
    puerto = _partir_host(host)[1]
    return f'{esquema}://localhost:{puerto}' if puerto else f'{esquema}://localhost'


def _url_del_producto(base, producto):
    from django.urls import reverse

    return base + reverse('stock:productos_editar', args=[producto.pk])


def _vendido_desde(desde):
    """El filtro de los renglones de venta COMPLETADA desde ``desde``."""
    from django.db.models import Q

    return Q(renglones_venta__id_venta__estado_venta='COMPLETADA',
             renglones_venta__id_venta__fecha_venta__gte=desde)


def _con_movimiento_reciente(consulta, desde):
    """Marca ``reciente`` en lo que se vendió o se compró desde ``desde``."""
    from django.db.models import Exists, OuterRef, Q

    from compras.models import DetalleCompra
    from ventas.models import DetalleVenta

    ventas = DetalleVenta.objects.filter(
        id_producto=OuterRef('pk'), id_venta__estado_venta='COMPLETADA',
        id_venta__fecha_venta__gte=desde)
    compras = DetalleCompra.objects.filter(
        id_producto=OuterRef('pk'), id_compra__fecha_compra__gte=desde)
    return consulta.annotate(
        vendido_hace_poco=Exists(ventas),
        comprado_hace_poco=Exists(compras)).annotate(
        reciente=Q(vendido_hace_poco=True) | Q(comprado_hace_poco=True))


@de_servicio
@require_GET
def productos_sin_receta(request):
    """Elaborados que se venden y todavía no tienen la receta cargada.

    Un ELABORADO sin receta se vende igual (la matriz del tipo lo permite:
    el mostrador no se frena), pero no descuenta insumos y su margen es el
    que trajo la planilla. Los que más facturaron son los que más conviene
    cargar primero.

    Si en la ventana no hubo ventas, igual devuelve los pendientes —los de
    mayor precio, y después los últimos cargados—: la lista existe para
    terminar el trabajo, no para quedar vacía.
    """
    from django.db.models import Count, DecimalField, Sum, Value
    from django.db.models.functions import Coalesce

    from stock.models import Producto, TipoProducto

    dias = _entero(request, 'dias', 7, 1, 90)
    top = _entero(request, 'top', 5, 1, TOPE)
    desde = timezone.localdate() - timedelta(days=dias)
    pendientes = Producto.objects.filter(
        borrado_logico=False, tipo_producto=TipoProducto.ELABORADO,
        composicion__isnull=True)
    vendidos = _vendido_desde(desde)
    productos = (pendientes.select_related('id_rubro').annotate(
        veces_vendido=Count('renglones_venta', filter=vendidos),
        unidades_vendidas=Coalesce(
            Sum('renglones_venta__cantidad', filter=vendidos),
            Value(0, output_field=DecimalField(max_digits=12, decimal_places=3))),
        facturado=Coalesce(
            Sum('renglones_venta__subtotal', filter=vendidos),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2))),
    ).order_by('-facturado', '-unidades_vendidas', '-precio_venta', '-pk')[:top])
    base = _base_publica(request)
    return JsonResponse({
        'total_pendientes': pendientes.count(), 'dias': dias,
        'productos': [{
            'id': p.pk, 'nombre': str(p), 'rubro': p.id_rubro.nombre_rubro,
            'veces_vendido': p.veces_vendido,
            'unidades_vendidas': str(p.unidades_vendidas),
            'facturado': str(p.facturado), 'precio_costo': str(p.precio_costo),
            'precio_venta': str(p.precio_venta), 'url': _url_del_producto(base, p),
        } for p in productos]})


def _motivo_de_margen(producto, piso, sin_receta):
    """``(motivo, utilidad)``; motivo ``None`` si el margen está bien."""
    from decimal import Decimal

    from stock.models import TipoProducto

    cero = Decimal('0')
    costo = producto.precio_costo or cero
    utilidad = producto.utilidad_porcentaje
    con_costo_propio = producto.tipo_producto in (TipoProducto.REVENTA,
                                                  TipoProducto.PRODUCIDO)
    if costo <= cero and (con_costo_propio or sin_receta):
        # Sin costo no hay margen que mirar: lo que diga la ganancia de
        # este producto es mentira hasta que alguien lo cargue.
        return 'SIN_COSTO', utilidad
    if costo > cero and producto.precio_venta <= costo:
        return 'A_PERDIDA', utilidad
    if utilidad is not None and utilidad < piso:
        return 'MARGEN_BAJO', utilidad
    return None, utilidad


@de_servicio
@require_GET
def productos_margen_bajo(request):
    """Lo que se vende sin costo, a pérdida o con el margen por el piso.

    El margen es sobre el costo CON IVA (``Producto.utilidad_porcentaje``),
    que es como se fija el precio en el local. Primero lo que se movió en
    los últimos 30 días: un margen caído en algo que no rota puede esperar.
    """
    from stock.models import TIPOS_VENDIBLES, Producto, TipoProducto

    piso = _entero(request, 'piso', 15, 0, 500)
    top = _entero(request, 'top', 15, 1, TOPE)
    desde = timezone.localdate() - timedelta(days=DIAS_DE_ROTACION)
    base = _base_publica(request)
    consulta = (Producto.objects
                .filter(borrado_logico=False,
                        tipo_producto__in=sorted(TIPOS_VENDIBLES))
                .select_related('id_rubro').prefetch_related('composicion'))
    filas = []
    for producto in _con_movimiento_reciente(consulta, desde):
        sin_receta = (producto.tipo_producto == TipoProducto.ELABORADO
                      and not producto.composicion.all())
        motivo, utilidad = _motivo_de_margen(producto, piso, sin_receta)
        if motivo is None:
            continue
        filas.append({
            'id': producto.pk, 'nombre': str(producto),
            'rubro': producto.id_rubro.nombre_rubro,
            'tipo': producto.tipo_producto, 'motivo': motivo,
            'precio_costo': str(producto.precio_costo),
            'precio_venta': str(producto.precio_venta),
            'utilidad_porcentaje': None if utilidad is None else str(utilidad),
            'se_movio_hace_poco': bool(producto.reciente),
            'url': _url_del_producto(base, producto),
            '_orden': (0 if producto.reciente else 1,
                       MOTIVOS_DE_MARGEN.index(motivo),
                       utilidad if utilidad is not None else 0),
        })
    filas.sort(key=lambda f: f['_orden'])
    for fila in filas:
        del fila['_orden']
    return JsonResponse({'piso': piso, 'dias': DIAS_DE_ROTACION,
                         'total': len(filas), 'productos': filas[:top]})


@de_servicio
@require_GET
def productos_conteo_rapido(request):
    """La «misión conteo de 5 minutos»: qué contar hoy y nada más.

    Lo que tiene stock propio, figura en cero (o en negativo) y sin embargo
    se sigue vendiendo: o el conteo del arranque nunca se hizo, o se vende
    sin descargar. Se cuentan de a cinco por día; el local no se para.
    """
    from django.db.models import Count, DecimalField, F, Q, Sum, Value
    from django.db.models.functions import Coalesce

    from stock.models import Producto, TipoProducto

    dias = _entero(request, 'dias', DIAS_DE_ROTACION, 1, 90)
    top = _entero(request, 'top', 5, 1, TOPE)
    hoy = timezone.localdate()
    desde = hoy - timedelta(days=dias)
    cantidad = DecimalField(max_digits=12, decimal_places=3)
    vendidos = _vendido_desde(desde)
    productos = (Producto.objects
                 .filter(borrado_logico=False,
                         tipo_producto__in=(TipoProducto.REVENTA,
                                            TipoProducto.PRODUCIDO))
                 .select_related('id_rubro', 'id_marca')
                 .annotate(vencido_en_lotes=Coalesce(
                     Sum('lotes__stock_lote',
                         filter=Q(lotes__stock_lote__gt=0,
                                  lotes__fecha_vencimiento__lt=hoy)),
                     Value(0, output_field=cantidad)))
                 .annotate(stock_apto=F('stock_actual') - F('vencido_en_lotes'))
                 .filter(stock_apto__lte=0)
                 .annotate(
                     veces_vendido=Count('renglones_venta', filter=vendidos),
                     unidades_vendidas=Coalesce(
                         Sum('renglones_venta__cantidad', filter=vendidos),
                         Value(0, output_field=cantidad)))
                 .order_by('-unidades_vendidas', '-veces_vendido',
                           'nombre_producto'))
    base = _base_publica(request)
    filas = list(productos[:top])
    return JsonResponse({
        'dias': dias, 'total': productos.count(),
        'productos': [{
            'id': p.pk, 'nombre': str(p), 'rubro': p.id_rubro.nombre_rubro,
            'codigo_barras': p.codigo_barras,
            'stock_actual': str(p.stock_actual),
            'unidades_vendidas': str(p.unidades_vendidas),
            'veces_vendido': p.veces_vendido,
            'se_vendio': bool(p.veces_vendido),
            'url': _url_del_producto(base, p),
        } for p in filas]})


@de_servicio
@require_GET
def productos_sin_clasificar(request):
    """Lo que quedó en «Sin Clasificar» u «Otros Rubros» al migrar.

    Un producto sin rubro de verdad no se encuentra en el mostrador, no
    entra en ningún informe por rubro y no hereda la rebaja por
    vencimiento del rubro. La comparación es por clave normalizada, así
    «otros rubros» y «Otros Rubros» son el mismo.
    """
    from nucleo.restricciones import ClaveNormalizada
    from nucleo.validaciones import clave_normalizada
    from stock.models import Producto

    top = _entero(request, 'top', 20, 1, TOPE)
    desde = timezone.localdate() - timedelta(days=DIAS_DE_ROTACION)
    claves = [clave_normalizada(nombre) for nombre in RUBROS_SIN_CLASIFICAR]
    consulta = (Producto.objects.filter(borrado_logico=False)
                .select_related('id_rubro', 'id_marca')
                .annotate(rubro_normalizado=ClaveNormalizada('id_rubro__nombre_rubro'))
                .filter(rubro_normalizado__in=claves))
    base = _base_publica(request)
    filas = sorted(_con_movimiento_reciente(consulta, desde),
                   key=lambda p: (0 if p.reciente else 1, p.nombre_producto))
    return JsonResponse({
        'total': len(filas), 'rubros': list(RUBROS_SIN_CLASIFICAR),
        'productos': [{
            'id': p.pk, 'codigo_barras': p.codigo_barras,
            'nombre': p.nombre_producto,
            'rubro_actual': p.id_rubro.nombre_rubro,
            'marca': p.id_marca.nombre_marca, 'tiene_foto': bool(p.imagen),
            'se_movio_hace_poco': bool(p.reciente),
            'url': _url_del_producto(base, p),
        } for p in filas[:top]]})


# ===========================================================================
#  SUGERENCIAS DE MEJORA DEL CATÁLOGO (Etapa 2)
# ===========================================================================
@de_servicio
@require_POST
def proponer_sugerencia(request):
    """Deja una propuesta de mejora en la bandeja, para que la apruebe una
    persona (DD-84: la automatización no cambia el catálogo por su cuenta).

    Cuerpo: ``{"producto", "tipo", "motivo", "propuesta"}``. Un reintento
    con la misma propuesta no la deja dos veces: la huella la reconoce y
    devuelve 200 con la que ya estaba esperando.
    """
    from seguridad.servicios import instantanea, registrar_auditoria
    from stock import sugerencias
    from stock.models import Producto

    try:
        datos = json_seguro.del_pedido(request)
        producto_id = json_seguro.campo(datos, 'producto', int, 'El producto ("producto")')
        tipo = json_seguro.campo(datos, 'tipo', str, 'El tipo ("tipo")', '')
        motivo = json_seguro.campo(datos, 'motivo', str, 'El motivo ("motivo")', '')
        propuesta = json_seguro.campo(datos, 'propuesta', dict, 'La propuesta ("propuesta")', {})
    except json_seguro.PedidoMalArmado as error:
        return _error(' '.join(error.messages), 400)
    if producto_id is None:
        return _error('El cuerpo lleva "producto", "tipo", "motivo" y "propuesta".', 400)
    producto = Producto.objects.filter(pk=producto_id, borrado_logico=False).first()
    if producto is None:
        return _error('Ese producto no existe o está dado de baja.', 404)
    try:
        sugerencia, nueva = sugerencias.proponer(producto, tipo, motivo, propuesta)
    except ValidationError as error:
        return _error(' '.join(error.messages), 400)
    if nueva:
        registrar_auditoria(
            None, 'ALTA', 'SUGERENCIA DE CATALOGO', instancia=sugerencia,
            nuevo=instantanea(sugerencia), modulo='STOCK',
            detalle=f'Una automatización propuso {sugerencia.get_tipo_sugerencia_display().lower()} '
                    f'en "{producto}": {sugerencia.motivo}')
    from django.urls import reverse

    return JsonResponse({
        'id': sugerencia.pk, 'tipo': sugerencia.tipo_sugerencia,
        'producto': producto.pk, 'motivo': sugerencia.motivo,
        'propuesta': sugerencia.propuesta, 'ya_estaba': not nueva,
        'pendientes': sugerencias.pendientes().count(),
        'url': _base_publica(request) + reverse('stock:sugerencias'),
    }, status=201 if nueva else 200)
