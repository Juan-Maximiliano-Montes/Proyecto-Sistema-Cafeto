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
    from nucleo.validaciones import clave_normalizada
    from seguridad.servicios import registrar_auditoria
    from stock import imagenes
    from stock.models import MARCA_GENERICA, Marca, Producto

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
    from django.conf import settings
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
                                    f'«{factura.resumen_factura}»: queda para revisar y confirmar.')
    ruta = reverse('compras:recepcion') + f'?factura={factura.pk}'
    base = ((getattr(settings, 'CAFETO_URL_PUBLICA', '') or '').strip()
            or request.build_absolute_uri('/')).rstrip('/')
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
