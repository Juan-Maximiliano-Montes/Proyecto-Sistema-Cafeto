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
import json
import uuid

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from negocio.models import ParametrosNegocio
from nucleo.busqueda import filtrar_por_texto
from nucleo.formularios import leer_numero
from seguridad.decorators import requiere_permiso
from seguridad.servicios import (
    autorizar_con_clave,
    registrar_acceso_denegado,
    registrar_auditoria,
)
from stock.models import TIPOS_VENDIBLES, Producto, Rubro
from ventas import puesto
from ventas import servicios as ventas
from ventas.models import MedioPago, MotivoDescuento, Venta

POR_PAGINA = 24
MAXIMO_DE_RENGLONES = 60


def _error(texto, estado=200):
    return JsonResponse({'ok': False, 'error': texto}, status=estado)


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _producto_json(p):
    return {
        'id': p.pk,
        'codigo': p.codigo_barras or '',
        'nombre': p.nombre_producto,
        'detalle': ' · '.join(x for x in (
            p.id_marca.nombre_marca if p.id_marca_id else '', p.presentacion) if x),
        'precio': str(p.precio_venta),
        'rubro': p.id_rubro.nombre_rubro if p.id_rubro_id else '',
        'fraccionable': not p.exige_cantidad_entera,
        'unidad': p.unidad_medida if not p.exige_cantidad_entera else 'u.',
        'stock': (str(p.stock_actual) if p.tiene_stock_propio else None),
        'agotado': bool(p.tiene_stock_propio and p.stock_actual <= 0),
        'rebaja': bool(p.controla_vencimiento and p.rebaja_vigente()),
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
    return render(request, 'ventas/mostrador.html', {
        'turno': turno,
        'rubros': Rubro.objects.filter(borrado_logico=False,
                                       productos__tipo_producto__in=TIPOS_VENDIBLES,
                                       productos__borrado_logico=False)
                               .distinct().order_by('nombre_rubro'),
        'medios': [{'id': m.pk, 'nombre': m.nombre_medio_pago,
                    'recargo': str(m.recargo_porcentaje or 0),
                    'efectivo': m.afecta_caja} for m in medios],
        'motivos': [(v, e) for v, e in MotivoDescuento.choices
                    if v not in (MotivoDescuento.VENCIMIENTO,
                                 MotivoDescuento.BENEFICIO)],
        'tope': request.usuario_actual.perfil_efectivo.tope_descuento_porcentaje,
        'puede_crear_cliente': request.usuario_actual.puede('crear_cliente'),
        'token': uuid.uuid4().hex,
    })


@require_GET
@requiere_permiso('vender', redirigir_a='ventas:caja')
def catalogo(request):
    productos = (Producto.objects
                 .filter(borrado_logico=False, tipo_producto__in=TIPOS_VENDIBLES)
                 .select_related('id_marca', 'id_rubro')
                 .order_by('nombre_producto'))
    rubro = request.GET.get('rubro', '')
    if rubro.isdigit():
        productos = productos.filter(id_rubro_id=rubro)
    busqueda = request.GET.get('q', '').strip()
    por_codigo = productos.filter(codigo_barras=busqueda) if busqueda else None
    if por_codigo is not None and por_codigo.exists():
        productos = por_codigo          # lo leyó el lector de códigos
    else:
        productos = filtrar_por_texto(
            productos, ('nombre_producto', 'id_marca__nombre_marca',
                        'id_rubro__nombre_rubro'), busqueda)
    try:
        pagina = max(1, int(request.GET.get('pagina', 1)))
    except ValueError:
        pagina = 1
    desde = (pagina - 1) * POR_PAGINA
    tanda = list(productos[desde:desde + POR_PAGINA + 1])
    return JsonResponse({
        'productos': [_producto_json(p) for p in tanda[:POR_PAGINA]],
        'hay_mas': len(tanda) > POR_PAGINA,
        'pagina': pagina,
    })


# ===========================================================================
#  LECTURA DEL PEDIDO
# ===========================================================================
def _leer_pedido(request):
    """El JSON del navegador -> (items, pagos, datos). Valida forma, no reglas."""
    try:
        datos = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        raise ValidationError('El pedido llegó mal armado. Recargá la página.')
    renglones = datos.get('items') or []
    if not isinstance(renglones, list) or len(renglones) > MAXIMO_DE_RENGLONES:
        raise ValidationError('El ticket tiene demasiados renglones.')

    ids = {r.get('producto') for r in renglones if isinstance(r, dict)}
    productos = Producto.objects.in_bulk([i for i in ids if isinstance(i, int)])
    items = []
    for renglon in renglones:
        if not isinstance(renglon, dict) or renglon.get('producto') not in productos:
            raise ValidationError('Uno de los productos del ticket ya no existe.')
        cantidad = leer_numero(renglon.get('cantidad'), 3)
        if cantidad is None or cantidad <= 0:
            raise ValidationError(
                f'La cantidad de "{productos[renglon["producto"]]}" no es válida.')
        descuento = leer_numero(renglon.get('descuento') or '0', 2)
        if descuento is None:
            raise ValidationError('Uno de los descuentos no es un número.')
        items.append(ventas.Item(
            productos[renglon['producto']], cantidad, descuento,
            renglon.get('motivo') or None,
            (renglon.get('detalle') or '')[:120] or None))

    pagos = []
    medios = MedioPago.objects.in_bulk(
        [p.get('medio') for p in (datos.get('pagos') or [])
         if isinstance(p, dict) and isinstance(p.get('medio'), int)])
    for pago in datos.get('pagos') or []:
        if not isinstance(pago, dict) or pago.get('medio') not in medios:
            raise ValidationError('Uno de los medios de pago no existe.')
        monto = pago.get('monto')
        recibido = pago.get('recibido')
        monto = leer_numero(monto, 2) if monto not in (None, '') else None
        recibido = leer_numero(recibido, 2) if recibido not in (None, '') else None
        pagos.append(ventas.Pago(medios[pago['medio']], monto, recibido))
    return items, pagos, datos


def _fidelizacion(datos):
    """El cliente identificado y los beneficios elegidos del pedido.

    Del navegador viajan sólo ids: qué beneficios corresponden y cuánto
    valen lo decide ``ventas.cotizar`` contra la base.
    """
    from fidelizacion.models import Cliente

    cliente = None
    id_cliente = datos.get('cliente')
    if id_cliente not in (None, ''):
        if not isinstance(id_cliente, int):
            raise ValidationError('El cliente del ticket no es válido.')
        cliente = Cliente.objects.filter(pk=id_cliente).first()
        if cliente is None:
            raise ValidationError('El cliente del ticket ya no existe.')
    beneficios = datos.get('beneficios') or []
    if (not isinstance(beneficios, list) or len(beneficios) > 10
            or not all(isinstance(b, int) for b in beneficios)):
        raise ValidationError('Los beneficios del ticket no son válidos.')
    return cliente, beneficios


def _cliente_json(cliente):
    return {
        'id': cliente.pk,
        'nombre': f'{cliente.nombre_cliente} {cliente.apellido_cliente}',
        'categoria': str(cliente.id_categoria),
        'disponibles': cliente.puntos_disponibles,
        'acumulados': cliente.puntos_acumulados,
    }


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


def _autorizante(request, datos, para, auditar=True):
    autoriza = datos.get('autoriza') or {}
    if not isinstance(autoriza, dict) or not autoriza.get('usuario'):
        return None
    return autorizar_con_clave(
        request, autoriza.get('usuario'), autoriza.get('clave'), 'vender',
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
@require_POST
@requiere_permiso('vender', redirigir_a='ventas:caja')
def cotizar(request):
    try:
        items, pagos, datos = _leer_pedido(request)
        if not items:
            return JsonResponse({'ok': True, 'vacio': True})
        # La vista previa se recalcula con cada cambio del ticket: el
        # rechazo que cuenta para la auditoría es el del cobro (DD-62).
        autorizante = _autorizante(request, datos, 'autorizar el descuento',
                                   auditar=False)
        cliente, beneficios = _fidelizacion(datos)
        cotizacion = ventas.cotizar(items, pagos, operador=request.usuario_actual,
                                    autorizante=autorizante, cliente=cliente,
                                    beneficios=beneficios)
    except ValidationError as error:
        return _error(_texto(error))
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
        autorizante = _autorizante(request, datos, 'autorizar el descuento')
        cliente, beneficios = _fidelizacion(datos)
        venta, cotizacion = ventas.registrar(
            turno, request.usuario_actual, items, pagos, token=token,
            autorizante=autorizante, cliente=cliente, beneficios=beneficios)
    except ValidationError as error:
        return _error(_texto(error))
    if cotizacion is not None:
        # Sólo la venta nueva: la repetida (mismo token) ya quedó asentada.
        registrar_auditoria(
            request, 'ALTA', 'VENTA', instancia=venta,
            detalle=(f'Venta N° {venta.pk} por $ {venta.total_venta}'
                     + (f', descuento autorizado por {autorizante}'
                        if autorizante is not None and cotizacion.pide_autorizacion
                        else '') + '.'))
    puntos = None
    if cotizacion is not None and cotizacion.cliente is not None:
        puntos = _asentar_fidelizacion(request, venta, cotizacion)
    return JsonResponse({
        'ok': True,
        'puntos': puntos,
        'repetida': cotizacion is None,
        'venta': venta.pk,
        'total': str(venta.total_venta),
        'vuelto': str(venta.monto_vuelto),
        'ticket': reverse('ventas:ticket', args=[venta.pk]),
    })


def _asentar_fidelizacion(request, venta, cotizacion):
    """La auditoría del DFD 9 (canjes y ascenso) y lo que se le avisa al
    cliente: los puntos que ganó, los que le quedan y su categoría nueva."""
    cliente = cotizacion.cliente
    for beneficio in cotizacion.canjes:
        registrar_auditoria(
            request, 'CANJE', 'BENEFICIO', instancia=beneficio,
            modulo='FIDELIZACION',
            detalle=(f'{cliente} canjeó «{beneficio}» por '
                     f'{beneficio.puntos_requeridos} puntos en la venta '
                     f'N° {venta.pk}.'))
    resultado = cotizacion.resultado_puntos
    if resultado.ascendio:
        registrar_auditoria(
            request, 'RECATEGORIZACION', 'CLIENTE', instancia=cliente,
            detalle=(f'{cliente} pasa de {resultado.categoria_anterior} a '
                     f'{resultado.categoria_nueva} con la venta N° {venta.pk}.'))
    return {
        'cliente': f'{cliente.nombre_cliente} {cliente.apellido_cliente}',
        'ganados': resultado.puntos,
        'canjeados': cotizacion.puntos_a_canjear,
        'disponibles': resultado.disponibles,
        'categoria': str(resultado.categoria_nueva),
        'ascendio': resultado.ascendio,
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
        datos = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return _error('El pedido llegó mal armado. Recargá la página.')
    if not isinstance(datos, dict):
        return _error('El pedido llegó mal armado. Recargá la página.')
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
        detalle=f'Alta de {cliente} desde el mostrador (QR {cliente.qr_cliente}).')
    return JsonResponse({
        'ok': True, 'cliente': _cliente_json(cliente),
        'tarjeta': reverse('fidelizacion:tarjeta', args=[cliente.pk]),
    })


@requiere_permiso('vender', redirigir_a='ventas:caja')
def ticket(request, pk):
    """El comprobante interno para imprimir (no es la factura fiscal)."""
    venta = get_object_or_404(
        Venta.objects.select_related('id_usuario', 'id_usuario_anulacion',
                                     'id_usuario_solicitante',
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
    from ventas.anulacion import motivo_de

    return render(request, 'ventas/ticket.html', {
        'venta': venta,
        'motivo_anulacion': motivo_de(venta),
        'puntos': puntos,
        'renglones': venta.detalles.all(),
        'pagos': venta.pagos.select_related('id_medio_pago'),
        'negocio': ParametrosNegocio.vigentes(),
    })
