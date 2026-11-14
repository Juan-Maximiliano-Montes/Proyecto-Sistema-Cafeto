"""Las tareas del equipo en el celular: las mismas que en la web, más cortas.

    GET  /panel/                          lo que el inicio de la web muestra
    GET  /stock/productos/<id>/lotes/     lotes con stock, vencidos incluidos
    GET  /stock/ofertas/                  el sector de ofertas de hoy
    GET  /compras/ordenes/?estado=POR_RECIBIR
    GET  /compras/ordenes/<id>/           lo pedido contra lo ya recibido
    POST /compras/recepciones/            recibir contra la orden
    GET  /stock/conteo/   POST /stock/conteo/   POST .../renglones/   POST .../enviar/
    POST /stock/mermas/   POST /stock/traspasos/
    GET  /stock/ajustes/?estado=PENDIENTE   POST .../<id>/aprobar/   POST .../<id>/rechazar/
    GET  /stock/producibles/              POST /stock/producciones/

Cada endpoint llama al MISMO servicio que la pantalla de la web
(``stock.servicios``, ``compras.servicios``, ``negocio.panel``): las reglas
no se reescriben acá. Y deja el mismo asiento en el Registro de Actividad,
con la IP del celular en el detalle (``desde_el_celular``).

Lo que mueve mercadería (recepción, merma, traspaso y producción) va con
``Idempotency-Key`` (``api_movil.idempotencia``). Los ``ValidationError``
de los servicios vuelven como 400 con ``detail``: la app muestra el texto
tal cual.
"""
from django.core.exceptions import ValidationError
from django.db.models import Count, Prefetch
from django.http import JsonResponse
from django.utils import timezone

from api_movil import catalogo, fichas, idempotencia, pedidos, respuestas
from api_movil.autenticacion import CON_SESION, desde_el_celular, para_el_equipo
from api_movil.respuestas import numero
from compras import servicios as compras
from compras.models import OrdenCompra
from negocio import panel as panel_del_dia
from nucleo.templatetags.cafeto import pesos
from seguridad.servicios import foto, instantanea, registrar_auditoria
from stock import servicios
from stock.models import (
    MOTIVOS_POR_TIPO,
    AjusteStock,
    ComposicionProducto,
    DetalleAjuste,
    EstadoAjuste,
    MotivoAjuste,
    Producto,
    TipoAjuste,
    TipoProducto,
)

POR_RECIBIR = ('EMITIDA', 'CON_DIFERENCIAS')
SIN_CONTEO = 'No hay ningún conteo en curso.'
VER_LOTES = ('ver_productos', 'ver_stock', 'contar_stock', 'registrar_merma',
             'registrar_traspaso')

# El panel del celular muestra números y avisos; los enlaces de la web
# (Reportes) quedan en la web.
FORMATOS = {'pesos': 'PESOS', 'numero': 'NUMERO'}
DESTINO_DEL_AVISO = {
    'BAJO_MINIMO': 'ALERTAS', 'VENCIDOS': 'ALERTAS', 'POR_VENCER': 'ALERTAS',
    'AJUSTES': 'AJUSTES', 'ORDENES': 'ORDENES',
    'PAGOS': 'WEB', 'SEGURIDAD': 'WEB', 'CORREOS_REPETIDOS': 'WEB',
    'RECONTEOS': 'WEB',
}


def _error(error):
    return respuestas.error(idempotencia.texto_del_error(error), 400)


def _cuerpo(request):
    datos = respuestas.cuerpo(request)
    if datos is None:
        raise ValidationError(respuestas.DATOS_INVALIDOS)
    return datos


# ---------------------------------------------------------------------------
#  Panel, lotes y ofertas
# ---------------------------------------------------------------------------
@para_el_equipo(GET=CON_SESION)
def panel(request):
    indicadores, avisos = panel_del_dia.del_dia(request.usuario_actual)
    return JsonResponse({
        'indicadores': [{
            'clave': i['clave'], 'titulo': i['titulo'], 'valor': numero(i['valor']),
            'formato': FORMATOS[i['formato']], 'detalle': i['detalle'],
        } for i in indicadores if i['formato'] in FORMATOS],
        'avisos': [{'tipo': a['tipo'], 'texto': a['texto'],
                    'destino': DESTINO_DEL_AVISO[a['clave']]} for a in avisos],
    })


@para_el_equipo(GET=VER_LOTES)
def lotes(request, pk):
    """Los lotes con stock, del que sale primero al último: también los
    vencidos, que son justamente los que se tiran."""
    producto = Producto.objects.filter(pk=pk, borrado_logico=False).first()
    if producto is None:
        return respuestas.error('Ese producto ya no existe.', 404)
    hoy = timezone.localdate()
    return respuestas.lista([fichas.lote(lote, hoy)
                             for lote in servicios.lotes_fefo(producto)])


@para_el_equipo(GET=('imprimir_ofertas', 'ver_stock'))
def ofertas(request):
    """El sector de ofertas: una fila por lote rebajado hoy (``en_oferta``)."""
    hoy = timezone.localdate()
    filas = []
    for lote, porcentaje, precio in servicios.en_oferta(hoy=hoy):
        producto = lote.id_producto
        filas.append({
            'id': f'{producto.pk}-{lote.pk}',
            'producto': {'id': producto.pk, 'nombre': producto.nombre_producto,
                         'presentacion': catalogo.presentacion(producto)},
            'lote': fichas.lote(lote, hoy),
            'precio': numero(producto.precio_venta),
            'precio_oferta': numero(precio),
            'porcentaje': numero(porcentaje),
        })
    return respuestas.lista(filas)


# ---------------------------------------------------------------------------
#  Recibir mercadería
# ---------------------------------------------------------------------------
@para_el_equipo(GET='registrar_compra')
def ordenes(request):
    estado = request.GET.get('estado', '').strip().upper() or 'POR_RECIBIR'
    if estado != 'POR_RECIBIR':
        return respuestas.error('Desde el celular se listan las órdenes por '
                                'recibir (estado=POR_RECIBIR).', 400)
    filas = (OrdenCompra.objects.filter(estado_orden__in=POR_RECIBIR)
             .select_related('id_proveedor')
             .annotate(cantidad_renglones=Count('detalles'))
             .order_by('fecha_orden', 'pk'))
    return respuestas.lista([fichas.orden(orden) for orden in filas])


@para_el_equipo(GET='registrar_compra')
def orden(request, pk):
    elegida = OrdenCompra.objects.select_related('id_proveedor').filter(pk=pk).first()
    if elegida is None:
        return respuestas.error('Esa orden ya no existe.', 404)
    return JsonResponse(fichas.orden(elegida, fichas.renglones_de_la_orden(elegida)))


def _renglones_recibidos(datos):
    resultado = []
    for crudo in pedidos.renglones(datos):
        producto = pedidos.producto(crudo.get('producto'))
        lotes = []
        for lote in crudo.get('lotes') or []:
            if not isinstance(lote, dict):
                raise ValidationError(f'Los lotes de "{producto}" llegaron mal armados.')
            lotes.append(compras.LoteRecibido(
                pedidos.fecha(lote.get('fecha_vencimiento'),
                              f'el vencimiento de un lote de "{producto}"', obligatoria=True),
                pedidos.numero(lote.get('cantidad'), f'la cantidad de un lote de "{producto}"'),
                (pedidos.texto(lote.get('numero_lote')) or '')[:40] or None))
        resultado.append(compras.RenglonRecibido(
            producto,
            pedidos.numero(crudo.get('cantidad'), f'la cantidad de "{producto}"'),
            pedidos.numero(crudo.get('costo_unitario'), f'el costo de "{producto}"', 2),
            lotes))
    return resultado


@para_el_equipo(POST='registrar_compra')
def recepciones(request):
    """Recibe contra la orden con ``compras.servicios.recepcionar``.

    Van sólo los renglones que llegaron; lo que falta queda como faltante y
    la orden, CON_DIFERENCIAS, para que la Administradora lo resuelva con
    el proveedor (DD-64).
    """
    usuario = request.usuario_actual

    def hacer():
        datos = _cuerpo(request)
        pk = pedidos.entero(datos.get('orden'))
        elegida = (OrdenCompra.objects.select_related('id_proveedor')
                   .filter(pk=pk).first() if pk else None)
        if elegida is None:
            return respuestas.error('Esa orden ya no existe.', 404), None
        if not elegida.admite_recepcion:
            return respuestas.error(
                f'La orden N° {elegida.pk} ya no espera mercadería '
                f'({elegida.get_estado_orden_display().lower()}).', 409), None
        renglones = _renglones_recibidos(datos)
        # Como en la pantalla: un costo que salta al triple o cae a un tercio
        # casi siempre es un cero de más o de menos. No se guarda nada (y la
        # clave queda libre) hasta que se confirme.
        raros = compras.costos_que_llaman_la_atencion(renglones)
        if raros and datos.get('confirmo_costos') is not True:
            return respuestas.error(
                'Hay costos muy distintos de los que venías pagando. Revisalos: si '
                'están bien, confirmalos y volvé a enviar.', 409, codigo='COSTOS_RAROS',
                costos=[{'producto': {'id': raro['producto'].pk,
                                      'nombre': raro['producto'].nombre_producto},
                         'anterior': numero(raro['anterior']),
                         'nuevo': numero(raro['nuevo'])} for raro in raros]), None
        otros = datos.get('importe_otros_conceptos')
        compra = compras.recepcionar(
            elegida.id_proveedor, usuario, datos.get('tipo_comprobante'),
            datos.get('punto_venta'), datos.get('numero_comprobante'), renglones,
            fecha_compra=pedidos.fecha(datos.get('fecha_compra'), 'la fecha del comprobante'),
            importe_otros_conceptos=(pedidos.numero(otros, 'los otros conceptos', 2)
                                     if otros not in (None, '') else 0),
            orden=elegida)
        registrar_auditoria(
            request, 'ALTA', 'COMPRA', instancia=compra,
            nuevo=foto(compra, renglones=compra.detalles.all()),
            detalle=f'{compra.get_tipo_comprobante_display()} {compra.numero_formateado} '
                    f'de {elegida.id_proveedor}: {pesos(compra.total_compra)}, '
                    f'{len(renglones)} renglón(es). ' + desde_el_celular(request))
        elegida.refresh_from_db()
        return JsonResponse({
            'id': compra.pk,
            'orden': {'id': elegida.pk, 'estado': elegida.estado_orden},
            'diferencias': [{
                'producto': {'id': fila['producto'].pk,
                             'nombre': fila['producto'].nombre_producto},
                'pedido': numero(fila['pedido']),
                'recibido': numero(fila['recibido']),
                'diferencia': numero(fila['diferencia']),
            } for fila in elegida.diferencias() if fila['diferencia']],
        }, status=201), compra.pk

    return idempotencia.una_sola_vez(request, 'RECEPCION', hacer)


# ---------------------------------------------------------------------------
#  Conteo de inventario (a ciegas)
# ---------------------------------------------------------------------------
def _conteo(abierto, estado=200):
    return JsonResponse({'conteo': fichas.conteo(abierto, timezone.localdate())},
                        status=estado)


@para_el_equipo(GET='contar_stock', POST='contar_stock')
def conteo(request):
    if request.method == 'GET':
        return _conteo(fichas.conteo_abierto())
    try:
        datos = _cuerpo(request)
        abierto = servicios.iniciar_conteo(
            request.usuario_actual,
            datos.get('motivo') or MotivoAjuste.DIFERENCIA_CONTEO)
    except ValidationError as error:
        return _error(error)
    registrar_auditoria(request, 'INICIO', 'CONTEO', instancia=abierto,
                        nuevo=instantanea(abierto),
                        detalle=f'Empezó {abierto}. ' + desde_el_celular(request))
    return _conteo(abierto, 201)


@para_el_equipo(POST='contar_stock')
def renglon_de_conteo(request):
    """Anota lo contado. Si el renglón ya estaba, se reemplaza lo contado."""
    try:
        abierto = fichas.conteo_abierto()
        if abierto is None:
            raise ValidationError(SIN_CONTEO)
        datos = _cuerpo(request)
        producto = pedidos.producto(datos.get('producto'))
        contada = pedidos.numero(datos.get('contada'), 'cuánto contaste')
        nuevo = datos.get('nuevo_lote')
        nuevo_lote, lote = None, None
        if nuevo:
            if not isinstance(nuevo, dict):
                raise ValidationError('El lote nuevo llegó mal armado.')
            nuevo_lote = {
                'fecha_vencimiento': pedidos.fecha(
                    nuevo.get('fecha_vencimiento'),
                    'la fecha de vencimiento del lote nuevo', obligatoria=True),
                'numero_lote': (pedidos.texto(nuevo.get('numero_lote')) or '')[:40] or None,
            }
        else:
            lote = pedidos.lote_de(producto, datos.get('lote'))
        servicios.cargar_conteo(abierto, producto, contada, lote=lote,
                                nuevo_lote=nuevo_lote)
    except ValidationError as error:
        return _error(error)
    return _conteo(abierto)


@para_el_equipo(POST='contar_stock')
def enviar_conteo(request):
    abierto = fichas.conteo_abierto()
    if abierto is None:
        return respuestas.error(SIN_CONTEO, 400)
    antes = instantanea(abierto)
    try:
        abierto = servicios.enviar_conteo(abierto)
    except ValidationError as error:
        return _error(error)
    registrar_auditoria(request, 'ENVIO', 'CONTEO', instancia=abierto,
                        anterior=antes,
                        nuevo=foto(abierto, renglones=abierto.detalles.all()),
                        detalle=f'Envió a revisión {abierto}. ' + desde_el_celular(request))
    return _conteo(fichas.conteo_abierto())


# ---------------------------------------------------------------------------
#  Mermas y traspasos (quedan PENDIENTES hasta que alguien los apruebe)
# ---------------------------------------------------------------------------
def _motivo(datos, tipo):
    motivo = datos.get('motivo')
    if motivo not in MOTIVOS_POR_TIPO[tipo]:
        raise ValidationError(
            f'Elegí el motivo de{"l traspaso" if tipo == TipoAjuste.TRASPASO else " la merma"}.')
    return motivo


@para_el_equipo(POST='registrar_merma')
def mermas(request):
    usuario = request.usuario_actual

    def hacer():
        datos = _cuerpo(request)
        motivo = _motivo(datos, TipoAjuste.MERMA)
        renglones = []
        for crudo in pedidos.renglones(datos):
            producto = pedidos.producto(crudo.get('producto'))
            renglones.append((
                producto,
                pedidos.numero(crudo.get('cantidad'), f'la cantidad de "{producto}"'),
                pedidos.lote_de(producto, crudo.get('lote'))))
        ajuste = servicios.registrar_merma(usuario, motivo, renglones,
                                           pedidos.texto(datos.get('detalle')))
        registrar_auditoria(request, 'ALTA', 'MERMA', instancia=ajuste,
                            nuevo=foto(ajuste, renglones=ajuste.detalles.all()),
                            detalle=f'Cargó {ajuste}. ' + desde_el_celular(request))
        return JsonResponse(fichas.resultado(ajuste), status=201), ajuste.pk

    return idempotencia.una_sola_vez(request, 'MERMA', hacer)


@para_el_equipo(POST='registrar_traspaso')
def traspasos(request):
    usuario = request.usuario_actual

    def hacer():
        datos = _cuerpo(request)
        motivo = _motivo(datos, TipoAjuste.TRASPASO)
        renglones = []
        for crudo in pedidos.renglones(datos):
            origen = pedidos.producto(crudo.get('producto'))
            destino = pedidos.producto(crudo.get('destino'))
            renglones.append((
                origen,
                pedidos.numero(crudo.get('cantidad'), f'la cantidad de "{origen}"'),
                destino,
                pedidos.numero(crudo.get('cantidad_destino'),
                               f'la cantidad que entra de "{destino}"'),
                pedidos.lote_de(origen, crudo.get('lote'))))
        ajuste = servicios.registrar_traspaso(usuario, motivo, renglones,
                                              pedidos.texto(datos.get('detalle')))
        registrar_auditoria(request, 'ALTA', 'TRASPASO', instancia=ajuste,
                            nuevo=foto(ajuste, renglones=ajuste.detalles.all()),
                            detalle=f'Cargó {ajuste}. ' + desde_el_celular(request))
        return JsonResponse(fichas.resultado(ajuste), status=201), ajuste.pk

    return idempotencia.una_sola_vez(request, 'TRASPASO', hacer)


# ---------------------------------------------------------------------------
#  Autorizar ajustes (DD-67: quien carga no aprueba, salvo la Administradora)
# ---------------------------------------------------------------------------
@para_el_equipo(GET='autorizar_ajustes')
def ajustes(request):
    estado = request.GET.get('estado', '').strip().upper() or EstadoAjuste.PENDIENTE
    if estado != EstadoAjuste.PENDIENTE:
        return respuestas.error('Desde el celular se autorizan los ajustes '
                                'pendientes (estado=PENDIENTE).', 400)
    filas = (AjusteStock.objects.filter(estado_ajuste=estado)
             .select_related('id_usuario')
             .prefetch_related(Prefetch(
                 'detalles', queryset=DetalleAjuste.objects.select_related(
                     'id_producto', 'id_lote', 'id_producto_destino').order_by('pk')))
             .order_by('fecha_ajuste', 'pk'))
    hoy = timezone.localdate()
    return respuestas.lista([fichas.ajuste(a, request.usuario_actual, hoy)
                             for a in filas])


def _resolver(request, pk, aprobar):
    ajuste = AjusteStock.objects.filter(pk=pk).first()
    if ajuste is None:
        return respuestas.error('Ese ajuste ya no existe.', 404)
    if ajuste.estado_ajuste != EstadoAjuste.PENDIENTE:
        return respuestas.error(
            f'{ajuste.get_tipo_ajuste_display()} N° {ajuste.pk} ya está '
            f'{ajuste.get_estado_ajuste_display().lower()}: no se resuelve de nuevo.', 409)
    antes = instantanea(ajuste)
    try:
        observacion = pedidos.texto(_cuerpo(request).get('observacion')) or ''
        if aprobar:
            ajuste = servicios.aprobar(ajuste, request.usuario_actual, observacion)
        else:
            ajuste = servicios.rechazar(ajuste, request.usuario_actual, observacion)
    except ValidationError as error:
        return _error(error)
    registrar_auditoria(
        request, 'APROBACION' if aprobar else 'RECHAZO', 'AJUSTE DE STOCK',
        instancia=ajuste, anterior=antes,
        nuevo=foto(ajuste, renglones=ajuste.detalles.all()),
        detalle=f'{ajuste}. {observacion}'.strip() + ' ' + desde_el_celular(request))
    return JsonResponse(fichas.resultado(ajuste))


@para_el_equipo(POST='autorizar_ajustes')
def aprobar(request, pk):
    return _resolver(request, pk, aprobar=True)


@para_el_equipo(POST='autorizar_ajustes')
def rechazar(request, pk):
    return _resolver(request, pk, aprobar=False)


# ---------------------------------------------------------------------------
#  Producción por tanda
# ---------------------------------------------------------------------------
@para_el_equipo(GET='registrar_produccion')
def producibles(request):
    """Lo que se fabrica en el local y tiene receta: sin receta no se sabe
    qué insumos descontar."""
    filas = (Producto.objects
             .filter(tipo_producto=TipoProducto.PRODUCIDO, borrado_logico=False,
                     composicion__isnull=False)
             .distinct()
             .prefetch_related(Prefetch(
                 'composicion', queryset=ComposicionProducto.objects
                 .select_related('id_componente').order_by('pk')))
             .order_by('nombre_producto', 'pk'))
    return respuestas.lista([{
        'producto': fichas.producto(producto),
        'receta': [{'insumo': fichas.producto(item.id_componente),
                    'cantidad': numero(item.cantidad)}
                   for item in producto.composicion.all()],
    } for producto in filas])


@para_el_equipo(POST='registrar_produccion')
def producciones(request):
    usuario = request.usuario_actual

    def hacer():
        datos = _cuerpo(request)
        producto = pedidos.producto(datos.get('producto'))
        produccion = servicios.registrar_produccion(
            producto,
            pedidos.numero(datos.get('cantidad'), 'la cantidad producida'),
            usuario,
            pedidos.fecha(datos.get('fecha_vencimiento'), 'la fecha de vencimiento'),
            pedidos.texto(datos.get('observacion')))
        registrar_auditoria(
            request, 'ALTA', 'PRODUCCION', instancia=produccion,
            nuevo=instantanea(produccion),
            detalle=f'Tanda de {producto.cantidad_con_unidad(produccion.cantidad)} '
                    f'de {producto} a {pesos(produccion.costo_unitario)} c/u. '
                    + desde_el_celular(request))
        lote = produccion.lotes.first()
        return JsonResponse({
            'producto': {'id': producto.pk, 'nombre': producto.nombre_producto},
            'cantidad': numero(produccion.cantidad),
            'lote': fichas.lote(lote, timezone.localdate()) if lote else None,
        }, status=201), produccion.pk

    return idempotencia.una_sola_vez(request, 'PRODUCCION', hacer)
