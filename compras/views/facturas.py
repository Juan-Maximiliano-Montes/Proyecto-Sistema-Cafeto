"""Escanear la factura en «Recibir mercadería» y la bandeja de las que
llegaron por WhatsApp o correo (DD-98).

Nada de esto registra una compra: llena el formulario para que una persona
lo controle contra el papel y lo guarde. Las altas en un clic (proveedor y
producto) pasan por las mismas validaciones que el alta común y quedan en el
Registro de Actividad.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from compras import facturas, lector_facturas, precarga
from compras.models import Proveedor, TipoProveedor
from nucleo.formularios import leer_numero
from seguridad.decorators import requiere_permiso
from seguridad.servicios import foto, instantanea, registrar_auditoria
from stock.models import Producto, Rubro
from nucleo.validaciones import id_valido
from nucleo import json_seguro
from nucleo.templatetags.cafeto import pesos

UTILIDAD_SUGERIDA = Decimal('0.40')


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _mal_armado(error):
    return JsonResponse({'ok': False, 'error': _texto(error)}, status=400)


def para_escanear(borrador):
    """Lo que necesita el formulario para llenarse solo."""
    encabezado, renglones = precarga.para_la_pantalla(borrador)
    proveedor = Proveedor.objects.filter(pk=borrador['p']).first() if borrador.get('p') else None
    return {
        'ok': True,
        'proveedor': ({'id': proveedor.pk, 'nombre': str(proveedor), 'existe': True} if proveedor else
                      {'id': None, 'nombre': borrador.get('n') or '', 'cuit': borrador.get('c') or '',
                       'existe': False}),
        'tipo_comprobante': encabezado.get('tipo_comprobante') or '',
        'punto_venta': encabezado.get('punto_venta') or '',
        'numero': encabezado.get('numero') or '',
        'fecha': encabezado.get('fecha') or '',
        'vence': encabezado.get('vence_pago') or '',
        'renglones': renglones,
        'nuevos': borrador.get('x') or [],
        'sin_reconocer': [s for s in borrador.get('s') or []][:20],
    }


@require_POST
@requiere_permiso('registrar_compra', redirigir_a='compras:compras')
def escanear(request):
    """Foto, PDF o texto de la factura -> el formulario lleno (sin guardar)."""
    try:
        if request.POST.get('muestra') == '1':
            texto = facturas.factura_de_muestra()
            datos, origen = lector_facturas.interpretar(texto), 'muestra'
        else:
            archivo = request.FILES.get('archivo')
            if archivo is None:
                raise ValidationError('Elegí la foto o el PDF de la factura.')
            if archivo.size > lector_facturas.MAXIMO_BYTES:
                raise ValidationError('El archivo pesa más de 8 MB: sacale una foto más liviana o mandá el PDF.')
            datos, origen = lector_facturas.leer(archivo.read(), archivo.content_type, archivo.name)
        if not datos.get('renglones'):
            raise ValidationError('Se leyó la factura pero no se encontraron renglones con cantidad y precio. '
                                  'Cargalos a mano o probá con una foto más nítida.')
        borrador, resumen = precarga.reconocer(datos)
    except ValidationError as error:
        return JsonResponse({'ok': False, 'error': _texto(error)})
    respuesta = para_escanear(borrador)
    respuesta['origen'] = origen
    respuesta['testigo'] = precarga.firmar(borrador)
    return JsonResponse(respuesta)


@require_POST
@requiere_permiso('crear_proveedor', redirigir_a='compras:recepcion')
def alta_proveedor(request):
    """El proveedor leído de la factura, dado de alta en un clic."""
    try:
        datos = json_seguro.del_pedido(request)
    except json_seguro.PedidoMalArmado as error:
        return _mal_armado(error)
    proveedor = Proveedor(nombre_proveedor=str(datos.get('nombre') or '').strip()[:50],
                          cuit_proveedor=str(datos.get('cuit') or '').strip() or None,
                          tipo_proveedor=TipoProveedor.MERCADERIA)
    try:
        proveedor.full_clean()
        proveedor.save()
    except ValidationError as error:
        return JsonResponse({'ok': False, 'error': _texto(error)})
    registrar_auditoria(request, 'ALTA', 'PROVEEDOR', instancia=proveedor, nuevo=foto(proveedor),
                        detalle=f'Alta de {proveedor} desde la factura escaneada en "Recibir mercadería".')
    return JsonResponse({'ok': True, 'id': proveedor.pk, 'texto': str(proveedor),
                         'mensaje': f'Proveedor "{proveedor}" dado de alta. Completale el teléfono cuando puedas.'})


def rubro_sugerido(proveedor_id=None):
    """El rubro más usado entre los productos de ese proveedor (o de todos)."""
    from django.db.models import Count

    rubros = Producto.objects.filter(borrado_logico=False, id_rubro__activo=True, id_rubro__borrado_logico=False)
    if proveedor_id:
        propios = rubros.filter(id_proveedor_id=proveedor_id)
        if propios.exists():
            rubros = propios
    fila = rubros.values('id_rubro').annotate(n=Count('pk')).order_by('-n').first()
    return fila['id_rubro'] if fila else Rubro.objects.filter(borrado_logico=False, activo=True).values_list(
        'pk', flat=True).first()


TIPOS_DE_ALTA = ('REVENTA', 'INSUMO')
UNIDADES_DE_ALTA = ('UNIDAD', 'KG', 'L')


def _alicuota(valor):
    """La alícuota como ``Decimal`` de ``ALICUOTAS_IVA`` (21.0, 10.5, 0.0).

    Se normaliza acá y no se la pasa como texto a ``ProductoForm``: con
    ``localize=True`` el punto de "10.5" es separador de miles y el valor
    entraba multiplicado por 100 (CMP-01).
    """
    from stock.models import ALICUOTAS_IVA

    if valor in (None, ''):
        return Decimal('21.0')
    numero = leer_numero(str(valor), 1)
    for permitida, _etiqueta in ALICUOTAS_IVA:
        if numero is not None and numero == permitida:
            return permitida
    raise ValidationError('El IVA tiene que ser 21, 10,5 o 0.')


@require_POST
@requiere_permiso('crear_producto', redirigir_a='compras:recepcion')
def alta_producto(request):
    """Un renglón que no está en el catálogo, dado de alta con lo que dice la
    factura. Las reglas son las del modelo (``full_clean``), con los números
    ya como ``Decimal``: no pasan por un formulario con ``localize`` que los
    reinterprete."""
    from stock.models import TipoProducto, UnidadMedida
    from stock.reservados import marca_generica, proveedor_general

    try:
        datos = json_seguro.del_pedido(request)
    except json_seguro.PedidoMalArmado as error:
        return _mal_armado(error)
    costo = leer_numero(str(datos.get('costo') or ''), 2)
    precio = leer_numero(str(datos.get('precio_venta') or ''), 2)
    if costo is None or costo < 0:
        return JsonResponse({'ok': False, 'error': 'El costo leído no es un número.'})
    if precio is None or precio <= 0:
        precio = (costo * (1 + UTILIDAD_SUGERIDA)).quantize(Decimal('0.01'), ROUND_HALF_UP)
    tipo = str(datos.get('tipo_producto') or TipoProducto.REVENTA).strip().upper()
    unidad = str(datos.get('unidad_medida') or UnidadMedida.UNIDAD).strip().upper()
    if tipo not in TIPOS_DE_ALTA:
        return JsonResponse({'ok': False, 'error': 'Desde la factura sólo se dan de alta productos de reventa o insumos.'})
    if unidad not in UNIDADES_DE_ALTA:
        return JsonResponse({'ok': False, 'error': 'La unidad tiene que ser unidad, kilo o litro.'})
    try:
        alicuota = _alicuota(datos.get('alicuota_iva'))
        id_proveedor = id_valido(datos.get('proveedor'))
        proveedor = (Proveedor.objects.filter(pk=id_proveedor, borrado_logico=False)
                     .exclude(tipo_proveedor=TipoProveedor.SERVICIOS).first()) if id_proveedor else None
        id_rubro = id_valido(datos.get('rubro')) or rubro_sugerido(id_proveedor)
        rubro = Rubro.objects.filter(pk=id_rubro, borrado_logico=False, activo=True).first() if id_rubro else None
        if rubro is None:
            raise ValidationError('Elegí el rubro del producto.')
        if tipo != TipoProducto.INSUMO and precio < costo:
            raise ValidationError(
                f'El precio de venta ({pesos(precio)}) es menor que el costo ({pesos(costo)}): '
                'cargalo desde Productos si es a propósito.')
        producto = Producto(
            codigo_barras=str(datos.get('codigo') or '').strip() or None,
            nombre_producto=str(datos.get('nombre') or '')[:100],
            id_rubro=rubro, id_marca=marca_generica(),
            id_proveedor=proveedor or proveedor_general(),
            precio_costo=costo, precio_venta=precio,
            # IVA general en Argentina por defecto; se corrige en la ficha si es 10,5 o exento.
            alicuota_iva=alicuota, tipo_producto=tipo, unidad_medida=unidad,
            contenido=Decimal('1'), stock_minimo=Decimal('0'),
            controla_vencimiento=bool(datos.get('controla')))
        producto.full_clean(validate_constraints=False)
        if producto.codigo_barras and Producto.objects.filter(
                codigo_barras=producto.codigo_barras).exists():
            raise ValidationError(
                f'El código {producto.codigo_barras} ya lo tiene otro producto.')
        with transaction.atomic():
            producto.save()
    except ValidationError as error:
        return JsonResponse({'ok': False, 'error': _texto(error)})
    except IntegrityError:
        return JsonResponse({'ok': False, 'error': 'Ese producto ya está cargado (código o nombre repetido).'})
    registrar_auditoria(request, 'ALTA', 'PRODUCTO', instancia=producto, nuevo=instantanea(producto),
                        detalle=f'Alta de {producto} desde la factura escaneada en "Recibir mercadería": '
                                f'costo {pesos(producto.precio_costo)}, precio {pesos(producto.precio_venta)}.')
    return JsonResponse({'ok': True, 'producto': producto.pk, 'nombre': str(producto),
                         'controla': producto.controla_vencimiento,
                         'fraccionable': not producto.exige_cantidad_entera,
                         'unidad': 'u.' if producto.exige_cantidad_entera else producto.unidad_medida,
                         'precio_venta': str(producto.precio_venta), 'rubro': str(producto.id_rubro)})


@require_POST
@requiere_permiso('registrar_compra', redirigir_a='compras:compras')
def descartar_factura(request, pk):
    from compras.models import FacturaRecibida

    antes = instantanea(FacturaRecibida.objects.filter(pk=pk).first())
    try:
        factura = facturas.descartar(pk, request.usuario_actual)
    except ValidationError as error:
        messages.error(request, _texto(error))
    else:
        registrar_auditoria(request, 'DESCARTE', 'FACTURA RECIBIDA', instancia=factura, anterior=antes,
                            nuevo=instantanea(factura),
                            detalle=f'Descartó la factura recibida N° {factura.pk} ({factura.resumen_factura}): '
                                    'no se cargó como compra.')
        messages.success(request, 'Factura descartada: ya no aparece para revisar.')
    return redirect('compras:compras')
