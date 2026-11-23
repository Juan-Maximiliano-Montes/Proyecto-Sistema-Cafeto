"""El mostrador: buscar, armar el ticket y cobrar.

La pantalla es una sola página que habla con cinco puntos JSON:

    catalogo   lo vendible, buscado sin acentos y de a páginas
    cliente    identifica al cliente del club por QR, teléfono o DNI
    alta_cliente  lo registra si es nuevo (DFD 9 bloque A)
    cotizar    el ticket calculado por el servidor, sin escribir nada
    cobrar     la venta, de una vez y con token de un solo uso

El navegador nunca calcula un precio, un descuento ni un vuelto: arma la
lista de productos y cantidades, y lo demás lo devuelve ``cotizar()`` con
los precios de la base. Así lo que se ve antes de cobrar es exactamente lo
que se va a registrar, y no hay forma de mandar un precio "a mano".

Los errores de negocio (sin stock, cantidad fraccionada de algo que va por
unidad, descuento por encima del tope) vuelven con ``ok: false`` y el texto
para mostrar en rojo. Un error del programa vuelve con 500 y la pantalla
dice que no se pudo, sin inventar un ticket.
"""
import uuid
from stock.models import MARCA_GENERICA
from nucleo.validaciones import id_valido, sanear_codigo
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from negocio.models import ParametrosNegocio
from nucleo.busqueda import filtrar_por_texto
from nucleo.formularios import leer_numero, sobran_decimales
from seguridad.decorators import requiere_alguno, requiere_permiso
from seguridad.servicios import (
    autorizar_con_clave,
    foto,
    instantanea,
    registrar_acceso_denegado,
    registrar_auditoria,
)
from stock import imagenes
from stock.models import TIPOS_VENDIBLES, Lote, Producto
from ventas import puesto
from ventas import servicios as ventas
from ventas.models import MedioPago, MotivoDescuento, Venta
from nucleo.templatetags.cafeto import pesos
from nucleo import json_seguro

POR_PAGINA = 24
MAXIMO_DE_RENGLONES = 60


def _error(texto, estado=200):
    return JsonResponse({'ok': False, 'error': texto}, status=estado)


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _error_de(error):
    """Un pedido mal armado (que la pantalla nunca manda) es 400; un aviso de
    negocio sigue en 200, que es lo que el mostrador muestra al lado del
    ticket en lugar de «sin conexión»."""
    return _error(_texto(error), 400 if isinstance(error, json_seguro.PedidoMalArmado) else 200)


def _vencidos_de(productos):
    """Lo que está en lotes ya vencidos, por producto, en una consulta:
    eso no se vende, así que no cuenta como stock apto."""
    ids = [p.pk for p in productos if p.controla_vencimiento]
    if not ids:
        return {}
    return dict(Lote.objects.filter(id_producto__in=ids, stock_lote__gt=0,
                                    fecha_vencimiento__lt=timezone.localdate())
                .values('id_producto').annotate(t=Sum('stock_lote')).values_list('id_producto', 't'))


def _producto_json(p, vencidos=None):
    vencido = (vencidos or {}).get(p.pk) or Decimal('0')
    apto = max(p.stock_actual - vencido, Decimal('0')) if p.tiene_stock_propio else None
    # Un combo o un elaborado no tiene stock propio: alcanza lo que permitan
    # sus componentes aptos (con todas las medialunas vencidas, el desayuno
    # con medialunas tampoco se puede vender).
    from stock.servicios import disponible
    sin_componentes = not p.tiene_stock_propio and disponible(p) == 0
    return {
        'id': p.pk,
        'codigo': p.codigo_barras or '',
        'nombre': p.nombre_producto,
        'detalle': ' · '.join(x for x in (
            p.id_marca.nombre_marca if p.id_marca_id and p.id_marca.nombre_marca != MARCA_GENERICA else '',
            p.presentacion) if x),
        'precio': str(p.precio_venta),
        'rubro': p.id_rubro.nombre_rubro if p.id_rubro_id else '',
        'fraccionable': not p.exige_cantidad_entera,
        'unidad': p.unidad_medida if not p.exige_cantidad_entera else 'u.',
        'stock': (str(apto) if p.tiene_stock_propio else None),
        'vencido': str(vencido) if vencido else None,
        'agotado': bool(p.tiene_stock_propio and apto <= 0) or sin_componentes,
        'sin_componentes': sin_componentes,
        'ajustable': bool(p.tiene_stock_propio and p.stock_actual <= 0 and not p.controla_vencimiento),
        'rebaja': bool(p.controla_vencimiento and (apto is None or apto > 0) and p.rebaja_vigente()),
        'imagen': imagenes.url_de(p),
    }


# ===========================================================================
#  LA PANTALLA
# ===========================================================================
@requiere_permiso('vender', redirigir_a='ventas:caja')
def mostrador(request):
    turno = puesto.turno_de(request)
    if turno is None:
        return redirect('ventas:caja')
    medios = MedioPago.objects.filter(borrado_logico=False).order_by(
        '-afecta_caja', 'nombre_medio_pago')
    from stock.servicios import puede_aprobar_lo_propio
    return render(request, 'ventas/mostrador.html', {
        'turno': turno,
        'ajusta_sola': puede_aprobar_lo_propio(request.usuario_actual),
        'medios': [{'id': m.pk, 'nombre': m.nombre_medio_pago,
                    'recargo': str(m.recargo_porcentaje or 0),
                    'efectivo': m.afecta_caja, 'fiado': m.es_cuenta_corriente} for m in medios],
        'morosos': _cantidad_de_morosos(),
        'motivos': [(v, e) for v, e in MotivoDescuento.choices
                    if v not in (MotivoDescuento.VENCIMIENTO,
                                 MotivoDescuento.BENEFICIO)],
        'tope': request.usuario_actual.perfil_efectivo.tope_descuento_porcentaje,
        'puede_crear_cliente': request.usuario_actual.puede('crear_cliente'),
        'token': uuid.uuid4().hex,
    })


# Lo que se prepara en el local (cafetería) y lo que se revende (minisuper).
TIPOS_DE_CAFETERIA = ('ELABORADO', 'PRODUCIDO', 'COMBO')
DIAS_MAS_VENDIDOS = 30
TOPE_MAS_VENDIDOS = 12


def _mas_vendidos(productos):
    """Los que más salieron en los últimos 30 días (por unidades), que es
    lo que la cajera necesita a mano: una sola consulta, sin paginar."""
    desde = timezone.localdate() - timedelta(days=DIAS_MAS_VENDIDOS)
    return list(productos.filter(
        renglones_venta__id_venta__estado_venta='COMPLETADA',
        renglones_venta__id_venta__fecha_venta__gte=desde)
        .annotate(vendido=Sum('renglones_venta__cantidad'))
        .order_by('-vendido', 'nombre_producto')[:TOPE_MAS_VENDIDOS])


@require_GET
@requiere_permiso('vender', redirigir_a='ventas:caja')
def catalogo(request):
    productos = (Producto.objects
                 .filter(borrado_logico=False, tipo_producto__in=TIPOS_VENDIBLES)
                 .select_related('id_marca', 'id_rubro')
                 .order_by('nombre_producto'))
    busqueda = request.GET.get('q', '').strip()
    sector = request.GET.get('sector', '')
    # Lo que deja el lector alrededor del código (un "|", el mismo código
    # leído dos veces) se limpia igual que al cargarlo (DD-86).
    codigo = sanear_codigo(busqueda) if busqueda else None
    por_codigo = productos.filter(codigo_barras=codigo) if codigo and codigo.isdigit() else None
    if por_codigo is not None and por_codigo.exists():
        productos = por_codigo          # lo leyó el lector de códigos
    elif busqueda:
        # Al buscar se busca en todo, sin importar la pestaña.
        productos = filtrar_por_texto(
            productos, ('nombre_producto', 'id_marca__nombre_marca',
                        'id_rubro__nombre_rubro'), busqueda)
    elif sector == 'cafeteria':
        productos = productos.filter(tipo_producto__in=TIPOS_DE_CAFETERIA)
    elif sector == 'minisuper':
        productos = productos.exclude(tipo_producto__in=TIPOS_DE_CAFETERIA)
    elif sector == 'mas_vendidos':
        vendidos = _mas_vendidos(productos)
        if vendidos:
            vencidos = _vencidos_de(vendidos)
            return JsonResponse({'productos': [_producto_json(p, vencidos) for p in vendidos],
                                 'hay_mas': False, 'pagina': 1})
        # Local recién abierto, sin ventas todavía: se muestra el catálogo.
    pagina = id_valido(request.GET.get('pagina')) or 1
    desde = (pagina - 1) * POR_PAGINA
    tanda = list(productos[desde:desde + POR_PAGINA + 1])
    vencidos = _vencidos_de(tanda[:POR_PAGINA])
    return JsonResponse({
        'productos': [_producto_json(p, vencidos) for p in tanda[:POR_PAGINA]],
        'hay_mas': len(tanda) > POR_PAGINA,
        'pagina': pagina,
    })


# ===========================================================================
#  LECTURA DEL PEDIDO
# ===========================================================================
def _leer_pedido(request):
    """El JSON del navegador -> (items, pagos, datos). Valida forma, no reglas."""
    return leer_datos(json_seguro.del_pedido(request))


def leer_datos(datos):
    """Un pedido ya decodificado -> (items, pagos, datos). Lo usa también la
    autorización a distancia, que guarda el ticket y lo vuelve a cotizar."""
    renglones = json_seguro.campo(datos, 'items', list, 'Los renglones del ticket', [])
    if len(renglones) > MAXIMO_DE_RENGLONES:
        raise ValidationError('El ticket tiene demasiados renglones.')

    ids = {r.get('producto') for r in renglones if isinstance(r, dict)}
    productos = Producto.objects.in_bulk([i for i in ids if isinstance(i, int)])
    items = []
    for renglon in renglones:
        if not isinstance(renglon, dict) or renglon.get('producto') not in productos:
            raise ValidationError('Uno de los productos del ticket ya no existe.')
        cantidad = leer_numero(renglon.get('cantidad'), 3)
        if sobran_decimales(renglon.get('cantidad'), 3):
            raise ValidationError(
                f'La cantidad de "{productos[renglon["producto"]]}" admite hasta '
                '3 decimales (un gramo).')
        if cantidad is None or cantidad <= 0:
            raise ValidationError(
                f'La cantidad de "{productos[renglon["producto"]]}" no es válida.')
        descuento = leer_numero(renglon.get('descuento') or '0', 2)
        if descuento is None:
            raise ValidationError('Uno de los descuentos no es un número (o tiene más de 2 decimales).')
        items.append(ventas.Item(
            productos[renglon['producto']], cantidad, descuento,
            json_seguro.campo(renglon, 'motivo', str, 'El motivo del descuento'),
            (json_seguro.campo(renglon, 'detalle', str, 'El detalle del renglón', ''))[:120] or None))

    pagos = []
    lista_de_pagos = json_seguro.campo(datos, 'pagos', list, 'Los pagos', [])
    medios = MedioPago.objects.in_bulk(
        [id_valido(p.get('medio')) for p in lista_de_pagos
         if isinstance(p, dict) and isinstance(p.get('medio'), int) and id_valido(p.get('medio'))])
    for pago in lista_de_pagos:
        if not isinstance(pago, dict) or pago.get('medio') not in medios:
            raise ValidationError('Uno de los medios de pago no existe.')
        monto = pago.get('monto')
        recibido = pago.get('recibido')
        monto = leer_numero(monto, 2) if monto not in (None, '') else None
        recibido = leer_numero(recibido, 2) if recibido not in (None, '') else None
        pagos.append(ventas.Pago(medios[pago['medio']], monto, recibido))
    return items, pagos, datos


def _huella(items, pagos, cliente, beneficios, datos):
    from ventas import a_distancia

    return a_distancia.huella(items, pagos, cliente, beneficios, _vence_fiado(datos))


def _vence_fiado(datos):
    """La fecha en que el cliente dijo que paga lo fiado (o None)."""
    from datetime import date

    texto = json_seguro.campo(datos, 'vence_fiado', str, 'La fecha en que va a pagar')
    if not texto:
        return None
    try:
        return date.fromisoformat(str(texto))
    except ValueError:
        raise ValidationError('La fecha en que va a pagar no es válida.')


def _fidelizacion(datos):
    """El cliente identificado y los beneficios elegidos del pedido.

    Del navegador viajan sólo ids: qué beneficios corresponden y cuánto
    valen lo decide ``ventas.cotizar`` contra la base.
    """
    from fidelizacion.models import Cliente

    cliente = None
    id_cliente = json_seguro.campo(datos, 'cliente', int, 'El cliente del ticket')
    if id_cliente is not None:
        if id_valido(id_cliente) is None:
            raise ValidationError('El cliente del ticket no es válido.')
        cliente = Cliente.objects.filter(pk=id_cliente).first()
        if cliente is None:
            raise ValidationError('El cliente del ticket ya no existe.')
    beneficios = json_seguro.campo(datos, 'beneficios', list, 'Los beneficios del ticket', [])
    if (len(beneficios) > 10
            or not all(isinstance(b, int) and not isinstance(b, bool) for b in beneficios)):
        raise ValidationError('Los beneficios del ticket no son válidos.')
    return cliente, beneficios


def _cliente_json(cliente):
    return {
        'id': cliente.pk,
        'nombre': f'{cliente.nombre_cliente} {cliente.apellido_cliente}',
        'categoria': str(cliente.id_categoria),
        'disponibles': cliente.puntos_disponibles,
        'acumulados': cliente.puntos_acumulados,
        'credito': _credito_json(cliente),
    }


def _cantidad_de_morosos():
    from fidelizacion import credito

    return len(credito.deudores(solo_en_mora=True))


def _credito_json(cliente):
    """Lo que la caja necesita saber del fiado de este cliente (DD-91)."""
    from fidelizacion import credito

    s = credito.situacion(cliente)
    return {'estado': s['estado'], 'debe': str(s['saldo']), 'vencido': str(s['vencido']),
            'dias': s['dias_de_atraso'], 'nota': s['nota'], 'limite': str(s['limite']),
            'en_mora': s['en_mora'], 'aviso': credito.aviso_de_mora(cliente, s=s),
            'plazo': cliente.plazo_credito_dias,
            'cuenta': reverse('fidelizacion:cuenta', args=[cliente.pk])}


def _beneficio_json(b, aplicados, cabecera):
    return {
        'id': b.pk,
        'descripcion': b.descripcion_beneficio,
        'tipo': b.tipo_beneficio,
        'modo': b.modo_entrega,
        'porcentaje': str(b.porcentaje_descuento) if b.porcentaje_descuento else None,
        'puntos': b.puntos_requeridos,
        'categoria': str(b.id_categoria),
        'aplicado': b in aplicados or (cabecera is not None and b.pk == cabecera.pk),
        'automatico_descuento': (b.tipo_beneficio == 'AUTOMATICO'
                                 and b.modo_entrega == 'DESCUENTO'),
    }


def _autorizante(request, datos, para, auditar=True, huella=None, bloquear=False):
    """Quien autoriza este ticket: el que puso su clave en la caja o el que
    aprobó a distancia (DD-94). La solicitud usada queda en
    ``request.solicitud_remota`` para marcarla con la venta."""
    from ventas import a_distancia

    request.solicitud_remota = None
    if datos.get('remota') not in (None, ''):
        solicitud = a_distancia.aprobada_para(datos.get('remota'), request.usuario_actual, huella,
                                              bloquear=bloquear)
        request.solicitud_remota = solicitud
        return solicitud.id_usuario_autorizante
    autoriza = json_seguro.campo(datos, 'autoriza', dict, 'La autorización', {})
    usuario = json_seguro.campo(autoriza, 'usuario', str, 'El usuario de quien autoriza')
    if not usuario:
        return None
    return autorizar_con_clave(
        request, usuario, json_seguro.campo(autoriza, 'clave', str, 'La clave de quien autoriza'), 'vender',
        distinto_de=request.usuario_actual, para=para, auditar=auditar)


def _cotizacion_json(cotizacion):
    return {
        'ok': True,
        'renglones': [{
            'producto': r.producto.pk,
            'nombre': str(r.producto),
            'nombre_corto': r.producto.nombre_producto,
            'cantidad': str(r.cantidad),
            'precio': str(r.precio_unitario),
            'bruto': str(r.bruto),
            'descuento': str(r.descuento),
            'porcentaje': str(r.porcentaje),
            'motivo': r.motivo,
            'motivo_texto': MotivoDescuento(r.motivo).label if r.motivo else '',
            'subtotal': str(r.subtotal),
            'pide_autorizacion': r.pide_autorizacion,
            'beneficio': r.beneficio.pk if r.beneficio else None,
        } for r in cotizacion.renglones],
        'cliente': (_cliente_json(cotizacion.cliente)
                    if cotizacion.cliente else None),
        'beneficios': [_beneficio_json(b, cotizacion.aplicados,
                                       cotizacion.beneficio_cabecera)
                       for b in cotizacion.ofrecidos],
        'descuento': str(cotizacion.descuento),
        'descuento_texto': (f'{cotizacion.beneficio_cabecera} '
                            f'({cotizacion.porcentaje_cabecera:g} %)'
                            if cotizacion.beneficio_cabecera else ''),
        'puntos_a_canjear': cotizacion.puntos_a_canjear,
        'cobros': [{
            'medio': c.medio.pk, 'nombre': c.medio.nombre_medio_pago,
            'monto': str(c.monto), 'recargo': str(c.recargo),
            'importe': str(c.importe),
            'recibido': str(c.recibido) if c.recibido is not None else None,
            'vuelto': str(c.vuelto) if c.vuelto is not None else None,
        } for c in cotizacion.cobros],
        'subtotal': str(cotizacion.subtotal),
        'redondeo': str(cotizacion.redondeo),
        'recargo': str(cotizacion.recargo),
        'a_cobrar': str(cotizacion.a_cobrar),
        'total': str(cotizacion.total),
        'vuelto': str(cotizacion.vuelto),
        'pide_autorizacion': cotizacion.pide_autorizacion,
    }


# ===========================================================================
#  COTIZAR Y COBRAR
# ===========================================================================
def _producto_para_ajuste(datos):
    """El producto y la cantidad de un ajuste desde el mostrador, con sus
    controles: lo usan el ajuste y el pedido de autorización a distancia.

    Entre esos controles está que NO haya un conteo abierto (el índice
    ``uq_un_conteo_abierto`` deja uno solo). Se mira acá, antes de pedir
    nada: si no, el pedido de autorización a distancia salía, le llegaba el
    WhatsApp a la titular, ella lo aprobaba… y al ejecutarlo el ajuste
    fallaba con «ya hay un conteo abierto». La cajera quedaba esperando una
    autorización que ya estaba dada y que no servía para nada.
    """
    from stock.models import AjusteStock, EstadoAjuste, TipoAjuste

    producto = Producto.objects.filter(pk=id_valido(datos.get('producto')), borrado_logico=False).first()
    cantidad = leer_numero(str(datos.get('cantidad') or ''), 3)
    if producto is None or not producto.tiene_stock_propio:
        raise ValidationError('Ese producto no lleva stock.')
    if AjusteStock.objects.filter(
            tipo_ajuste=TipoAjuste.CONTEO,
            estado_ajuste__in=(EstadoAjuste.EN_CURSO, EstadoAjuste.PENDIENTE)).exists():
        raise ValidationError(
            'Hay un conteo de stock abierto: este ajuste no se puede hacer hasta que se '
            'termine o se resuelva. Anotá el producto en ese conteo.')
    if producto.stock_actual > 0:
        raise ValidationError(f'{producto} ya tiene stock: actualizá la pantalla.')
    if producto.controla_vencimiento:
        raise ValidationError('Ese producto va por lote con vencimiento: cargalo desde el conteo de stock.')
    if cantidad is None or cantidad <= 0:
        raise ValidationError('Escribí cuántas unidades hay.')
    return producto, cantidad


@require_POST
@requiere_permiso('vender', redirigir_a='ventas:caja')
def ajuste_express(request):
    """El producto figura en 0 pero está en la mano del cliente (DD-90).

    Se cuenta lo que hay en la góndola y queda asentado como un conteo de
    una sola línea, aprobado en el acto. Lo aprueba quien puede autorizar
    ajustes: la titular con su sesión, o cualquier otra persona con el
    permiso poniendo su clave (nunca la misma que está en la caja, salvo la
    titular: el mismo control que cualquier ajuste, DD-80).
    """
    from django.db import transaction

    from stock import servicios as stock
    from stock.models import MotivoAjuste

    try:
        datos = json_seguro.del_pedido(request)
        producto, cantidad = _producto_para_ajuste(datos)
        operador = request.usuario_actual
        solicitud = None
        if stock.puede_aprobar_lo_propio(operador):
            autorizante = operador
        elif json_seguro.campo(datos, 'remota', (int, str), 'La autorización a distancia'):
            # Lo aprobó a distancia quien puede aprobar ajustes (DD-94).
            from ventas import a_distancia
            solicitud = a_distancia.aprobada_para(
                datos.get('remota'), operador, a_distancia.huella_ajuste(producto, cantidad), bloquear=True)
            autorizante = solicitud.id_usuario_autorizante
        else:
            autoriza = json_seguro.campo(datos, 'autoriza', dict, 'La autorización', {})
            autorizante = autorizar_con_clave(
                request, json_seguro.campo(autoriza, 'usuario', str, 'El usuario de quien autoriza'),
                json_seguro.campo(autoriza, 'clave', str, 'La clave de quien autoriza'), 'autorizar_ajustes',
                distinto_de=operador, para=f'ajustar el stock de {producto} desde el mostrador')
        with transaction.atomic():
            conteo = stock.iniciar_conteo(operador, MotivoAjuste.DIFERENCIA_CONTEO)
            stock.cargar_conteo(conteo, producto, cantidad)
            stock.enviar_conteo(conteo)
            stock.aprobar(conteo, autorizante, 'Ajuste desde el mostrador: figuraba sin stock y había en la góndola.')
            if solicitud is not None:
                from ventas import a_distancia
                a_distancia.marcar_usada(solicitud)
        registrar_auditoria(
            request, 'APROBACION', 'AJUSTE DE STOCK', instancia=conteo, modulo='STOCK',
            anterior={'id_producto': producto.pk, 'stock_actual': '0'},
            nuevo={'id_producto': producto.pk, 'stock_actual': str(cantidad), 'autorizo': str(autorizante)},
            detalle=f'Ajuste desde el mostrador: {producto} pasó de 0 a '
                    f'{producto.cantidad_con_unidad(cantidad)}. Lo autorizó {autorizante}'
                    + (f' a distancia ({solicitud.get_via_aprobacion_display().lower()}, pedido N° {solicitud.pk})'
                       if solicitud is not None else '') + '.')
        return JsonResponse({'ok': True, 'stock': str(cantidad)})
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'No se entendió el pedido.'}, status=400)
    except ValidationError as error:
        return _error_de(error)


@require_POST
@requiere_permiso('vender', redirigir_a='ventas:caja')
def cotizar(request):
    try:
        items, pagos, datos = _leer_pedido(request)
        if not items:
            return JsonResponse({'ok': True, 'vacio': True})
        # La vista previa se recalcula con cada cambio del ticket: el
        # rechazo que cuenta para la auditoría es el del cobro (DD-62).
        cliente, beneficios = _fidelizacion(datos)
        autorizante = _autorizante(request, datos, 'autorizar el descuento', auditar=False,
                                   huella=_huella(items, pagos, cliente, beneficios, datos))
        cotizacion = ventas.cotizar(items, pagos, operador=request.usuario_actual,
                                    autorizante=autorizante, cliente=cliente,
                                    beneficios=beneficios)
    except ValidationError as error:
        return _error_de(error)
    return JsonResponse(_cotizacion_json(cotizacion))


@require_POST
@requiere_permiso('vender', redirigir_a='ventas:caja')
def cobrar(request):
    turno = puesto.turno_de(request)
    if turno is None:
        return _error('La caja de este dispositivo no tiene un turno abierto: '
                      'abrila para cobrar.')
    try:
        items, pagos, datos = _leer_pedido(request)
        try:
            token = uuid.UUID(str(datos.get('token')))
        except ValueError:
            raise ValidationError('El ticket no tiene un identificador válido. Recargá la página.')
        cliente, beneficios = _fidelizacion(datos)
        # Un reintento del mismo ticket (se perdió la respuesta) devuelve la
        # venta que ya existe: no se vuelve a pedir ni a gastar la autorización.
        request.solicitud_remota = None
        autorizante = None if Venta.objects.filter(token_operacion=token).exists() else _autorizante(
            request, datos, 'autorizar la venta (descuento o fiado)',
            huella=_huella(items, pagos, cliente, beneficios, datos), bloquear=True)
        cliente_antes = instantanea(cliente) if cliente is not None else None
        venta, cotizacion = ventas.registrar(
            turno, request.usuario_actual, items, pagos, token=token,
            autorizante=autorizante, cliente=cliente, beneficios=beneficios,
            vence_fiado=_vence_fiado(datos))
    except ValidationError as error:
        return _error_de(error)
    remota = getattr(request, 'solicitud_remota', None)
    if cotizacion is not None and remota is not None:
        from ventas import a_distancia

        a_distancia.marcar_usada(remota, venta)
    if cotizacion is not None:
        # Sólo la venta nueva: la repetida (mismo token) ya quedó asentada.
        registrar_auditoria(
            request, 'ALTA', 'VENTA', instancia=venta,
            nuevo=foto(venta, renglones=venta.detalles.all(), pagos=venta.pagos.all()),
            detalle=(f'Venta N° {venta.pk} por {pesos(venta.total_venta)}'
                     + (f', autorizada por {autorizante}'
                        + (f' a distancia ({remota.get_via_aprobacion_display().lower()}, '
                           f'pedido N° {remota.pk})' if remota is not None else '')
                        if autorizante is not None and cotizacion.pide_autorizacion
                        else '')
                     + (f'. Fiado a {cotizacion.cliente}: {pesos(cotizacion.fiado)}, paga el '
                        f'{cotizacion.movimiento_fiado.fecha_vencimiento:%d/%m/%Y}'
                        if cotizacion.movimiento_fiado else '') + '.'))
        if cotizacion.movimiento_fiado and getattr(venta, 'credito_habilitado', False):
            registrar_auditoria(
                request, 'MODIFICACION', 'CUENTA CORRIENTE', instancia=cotizacion.cliente, modulo='FIDELIZACION',
                anterior={'estado_credito': 'SIN_CREDITO'}, nuevo={'estado_credito': 'HABILITADO'},
                detalle=f'{cotizacion.cliente} queda habilitado para llevar fiado: lo autorizó '
                        f'{venta.credito_firmado_por} en la venta N° {venta.pk}.')
    puntos = None
    if cotizacion is not None and cotizacion.cliente is not None:
        puntos = _asentar_fidelizacion(request, venta, cotizacion, cliente_antes)
    return JsonResponse({
        'ok': True,
        'puntos': puntos,
        'repetida': cotizacion is None,
        'venta': venta.pk,
        'total': str(venta.total_venta),
        'vuelto': str(venta.monto_vuelto),
        'ticket': reverse('ventas:ticket', args=[venta.pk]),
        'fiado': ({'monto': str(cotizacion.fiado),
                   'vence': cotizacion.movimiento_fiado.fecha_vencimiento.isoformat(),
                   'cliente': f'{cotizacion.cliente.nombre_cliente} {cotizacion.cliente.apellido_cliente}'}
                  if cotizacion is not None and cotizacion.movimiento_fiado else None),
    })


def _asentar_fidelizacion(request, venta, cotizacion, cliente_antes=None):
    """La auditoría del DFD 9 (canjes y ascenso) y lo que se le avisa al
    cliente: los puntos que ganó, los que le quedan y su categoría nueva."""
    from fidelizacion.models import Cliente

    cliente = cotizacion.cliente
    for beneficio in cotizacion.canjes:
        canje = venta.movimientos_puntos.filter(id_beneficio=beneficio).first()
        registrar_auditoria(
            request, 'CANJE', 'BENEFICIO', instancia=canje or beneficio,
            nuevo=instantanea(canje or beneficio),
            modulo='FIDELIZACION',
            detalle=(f'{cliente} canjeó "{beneficio}" por '
                     f'{beneficio.puntos_requeridos} puntos en la venta '
                     f'N° {venta.pk}.'))
    resultado = cotizacion.resultado_puntos
    if resultado.ascendio:
        registrar_auditoria(
            request, 'RECATEGORIZACION', 'CLIENTE', instancia=cliente,
            anterior=cliente_antes,
            nuevo=instantanea(Cliente.objects.get(pk=cliente.pk)),
            detalle=(f'{cliente} pasa de {resultado.categoria_anterior} a '
                     f'{resultado.categoria_nueva} con la venta N° {venta.pk}.'))
    diferidos = bool(cotizacion.movimiento_fiado)
    if diferidos:
        # Compra fiada: los puntos llegan al pagarla; se informa lo que tiene hoy.
        cliente.refresh_from_db()
    return {
        'cliente': f'{cliente.nombre_cliente} {cliente.apellido_cliente}',
        'ganados': resultado.puntos,
        'canjeados': cotizacion.puntos_a_canjear,
        'disponibles': cliente.puntos_disponibles if diferidos else resultado.disponibles,
        'categoria': str(cliente.id_categoria if diferidos else resultado.categoria_nueva),
        'ascendio': resultado.ascendio,
        'diferidos': diferidos,
    }


# ===========================================================================
#  CLIENTE DEL CLUB (DFD 4 bloque C, DFD 9 bloque A)
# ===========================================================================
@require_GET
@requiere_permiso('vender', redirigir_a='ventas:caja')
def identificar_cliente(request):
    """Busca al cliente por lo que dio en el mostrador: el QR de su
    tarjeta, su teléfono o su DNI."""
    from fidelizacion.models import Cliente

    texto = request.GET.get('q', '')[:64]
    cliente = Cliente.identificar(texto)
    if cliente is None:
        return _error('No hay ningún cliente del club con ese QR, teléfono o '
                      'DNI. Si es nuevo, registralo.')
    return JsonResponse({'ok': True, 'cliente': _cliente_json(cliente)})


@require_POST
@requiere_permiso('crear_cliente', redirigir_a='ventas:mostrador')
def alta_cliente(request):
    """Alta rápida desde el mostrador, con el mismo formulario y las mismas
    reglas que la pantalla de Clientes (no repite DNI ni teléfono)."""
    from fidelizacion.forms import ClienteForm

    try:
        datos = json_seguro.del_pedido(request)
    except json_seguro.PedidoMalArmado as error:
        return _error_de(error)
    campos = ('apellido_cliente', 'nombre_cliente', 'telefono_cliente',
              'dni_cliente', 'email_cliente', 'fecha_nacimiento')
    formulario = ClienteForm({c: str(datos.get(c) or '')[:120] for c in campos})
    if not formulario.is_valid():
        errores = []
        for campo, mensajes in formulario.errors.items():
            etiqueta = (formulario.fields[campo].label
                        if campo in formulario.fields else '')
            errores += [f'{etiqueta}: {m}' if etiqueta else m
                        for m in mensajes]
        return _error(' '.join(errores))
    cliente = formulario.save()
    registrar_auditoria(
        request, 'ALTA', 'CLIENTE', instancia=cliente,
        nuevo=instantanea(cliente),
        detalle=f'Alta de {cliente} desde el mostrador (QR {cliente.qr_cliente}).')
    return JsonResponse({
        'ok': True, 'cliente': _cliente_json(cliente),
        'tarjeta': reverse('fidelizacion:tarjeta', args=[cliente.pk]),
    })


# Quien ve las ventas (la titular, quien supervisa) tiene que poder abrir el
# ticket aunque no venda: es el mismo criterio que ``comprobante_cambio``, y
# es lo que la propia vista controla adentro (``usuario.puede('ver_ventas')``
# para un ticket de otra persona). Pidiendo 'vender' acá, quien supervisa se
# comía un «acceso denegado» con el permiso de ver ventas puesto.
@requiere_alguno('vender', 'ver_ventas', redirigir_a='ventas:caja')
def ticket(request, pk):
    """El comprobante interno para imprimir (no es la factura fiscal)."""
    venta = get_object_or_404(
        Venta.objects.select_related('id_usuario', 'id_usuario_anulacion',
                                     'id_usuario_solicitante', 'id_cliente', 'id_beneficio',
                                     'id_turno_caja__id_caja_registradora'),
        pk=pk)
    usuario = request.usuario_actual
    if venta.id_usuario_id != usuario.pk and not usuario.puede('ver_ventas'):
        registrar_acceso_denegado(request, 'ver_ventas')
        return redirect('ventas:mostrador')
    puntos = None
    if venta.id_cliente_id:
        movimientos = {m.tipo_movimiento: m for m in venta.movimientos_puntos.all()}
        puntos = {
            'cliente': venta.id_cliente,
            'ganados': getattr(movimientos.get('ACUMULACION'), 'puntos', 0),
            'canjeados': -sum(m.puntos for m in venta.movimientos_puntos.all()
                              if m.tipo_movimiento == 'CANJE'),
        }
        # El saldo que tenía al salir esta venta, no el de hoy: el ticket
        # reimpreso tiene que decir lo mismo que el original.
        from django.db.models import Max, Sum

        from fidelizacion.models import MovimientoPuntos
        ultimo = venta.movimientos_puntos.filter(
            tipo_movimiento__in=('ACUMULACION', 'CANJE')).aggregate(
            m=Max('id_movimiento_punto'))['m']
        puntos['saldo'] = (MovimientoPuntos.objects.filter(
            id_cliente_id=venta.id_cliente_id,
            id_movimiento_punto__lte=ultimo or 0).aggregate(
            t=Sum('puntos'))['t'] or 0) if ultimo else None
    from fidelizacion import credito
    from fidelizacion.models import MovimientoCuenta
    from ventas.anulacion import motivo_de

    # Fiado (DD-91): lo que debía antes, el cargo de esta compra, lo que
    # pasó a deber y cuándo vence, como el día que salió el ticket.
    cuenta = None
    fiado = MovimientoCuenta.objects.filter(id_venta=venta, tipo_movimiento=credito.FIADO).first()
    if fiado is not None:
        anterior = credito.saldo_antes_de(fiado.id_cliente_id, fiado.pk)
        cuenta = {'anterior': anterior, 'cargo': fiado.monto, 'nuevo': anterior + fiado.monto,
                  'vence': fiado.fecha_vencimiento}
    return render(request, 'ventas/ticket.html', {
        'venta': venta, 'cuenta': cuenta, 'cliente': venta.id_cliente,
        'motivo_anulacion': motivo_de(venta),
        'puntos': puntos,
        'renglones': venta.detalles.all(),
        'pagos': venta.pagos.select_related('id_medio_pago'),
        'negocio': ParametrosNegocio.vigentes(),
        'impreso': timezone.now(),
    })
