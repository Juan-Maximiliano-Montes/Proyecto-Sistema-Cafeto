"""Conteos, mermas y traspasos: la mercadería que se mueve sin vender.

Nada de esto toca el stock al cargarse: queda PENDIENTE y lo mueve la
aprobación de alguien con ``autorizar_ajustes`` (``stock.servicios``). Así
una merma mal cargada se corrige antes de descuadrar el inventario.

El conteo es **ciego**: quien cuenta ve lo que cargó, no lo que el sistema
cree que hay. La diferencia la ve recién quien aprueba.
"""
from datetime import date

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from nucleo import idempotencia
from nucleo.formularios import leer_numero
from nucleo.paginacion import TAMANIOS_TABLA, paginar
from seguridad.decorators import requiere_alguno, requiere_permiso
from seguridad.servicios import foto, instantanea, registrar_auditoria
from stock import servicios
from stock.models import (
    Rubro,
    MOTIVOS_POR_TIPO,
    AjusteStock,
    EstadoAjuste,
    Lote,
    MotivoAjuste,
    Producto,
    TipoAjuste,
)

PERMISOS_DE_AJUSTES = ('contar_stock', 'registrar_merma', 'registrar_traspaso',
                       'autorizar_ajustes')
FILAS = 6
CATALOGO_CON_STOCK = '?tipos=REVENTA,INSUMO,PRODUCIDO'


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _motivos(tipo):
    return [(v, e) for v, e in MotivoAjuste.choices if v in MOTIVOS_POR_TIPO[tipo]]


def _conteo_abierto():
    return (AjusteStock.objects
            .filter(tipo_ajuste=TipoAjuste.CONTEO,
                    estado_ajuste__in=(EstadoAjuste.EN_CURSO, EstadoAjuste.PENDIENTE))
            .select_related('id_usuario').first())


def _producto(texto):
    return Producto.objects.filter(pk=texto if str(texto).isdigit() else 0,
                                   borrado_logico=False).first()


def _lote(producto, texto):
    """El lote elegido; si no se eligió y el producto va por lote, el que
    vence primero (FEFO), que es el que se está usando en la góndola."""
    if not texto or not str(texto).isdigit():
        if producto.controla_vencimiento:
            return servicios.lotes_fefo(producto).first()
        return None
    lote = Lote.objects.filter(pk=texto, id_producto=producto).first()
    if lote is None:
        raise ValidationError(f'Ese lote no es de "{producto}".')
    return lote


def _catalogo():
    return reverse('stock:buscar_productos') + CATALOGO_CON_STOCK


# ===========================================================================
#  LISTADO
# ===========================================================================
@requiere_alguno(*PERMISOS_DE_AJUSTES)
def ajustes(request):
    usuario = request.usuario_actual
    consulta = (AjusteStock.objects.select_related('id_usuario', 'id_usuario_autorizante')
                .order_by('-fecha_ajuste', '-pk'))
    estado = request.GET.get('estado')
    if estado is None:
        estado = 'PENDIENTE' if usuario.puede('autorizar_ajustes') else ''
    if estado in EstadoAjuste.values:
        consulta = consulta.filter(estado_ajuste=estado)
    tipo = request.GET.get('tipo', '')
    if tipo in TipoAjuste.values:
        consulta = consulta.filter(tipo_ajuste=tipo)
    if not usuario.puede('autorizar_ajustes'):
        consulta = consulta.filter(id_usuario=usuario)   # cada uno ve lo suyo
    contexto = paginar(request, consulta, TAMANIOS_TABLA, por_defecto=25)
    contexto.update({
        'estado': estado, 'tipo': tipo, 'estados': EstadoAjuste.choices,
        'tipos': TipoAjuste.choices, 'conteo': _conteo_abierto(),
        'pendientes': AjusteStock.objects.filter(estado_ajuste=EstadoAjuste.PENDIENTE).count(),
    })
    return render(request, 'stock/ajustes.html', contexto)


@requiere_alguno(*PERMISOS_DE_AJUSTES)
def ajuste(request, pk):
    """El detalle de un ajuste y, si corresponde, aprobarlo o rechazarlo."""
    ajuste = get_object_or_404(
        AjusteStock.objects.select_related('id_usuario', 'id_usuario_autorizante'), pk=pk)
    usuario = request.usuario_actual
    puede_resolver = usuario.puede('autorizar_ajustes')
    if not puede_resolver and ajuste.id_usuario_id != usuario.pk:
        messages.error(request, 'Ese ajuste lo cargó otra persona.')
        return redirect('stock:ajustes')
    renglones = list(ajuste.detalles.select_related('id_producto', 'id_lote',
                                                    'id_producto_destino'))
    # Conteo ciego: el teórico y la diferencia los ve quien aprueba.
    ve_diferencias = puede_resolver or ajuste.estado_ajuste in (
        EstadoAjuste.APROBADO, EstadoAjuste.RECHAZADO)
    return render(request, 'stock/ajuste.html', {
        'ajuste': ajuste, 'renglones': renglones,
        've_diferencias': ve_diferencias,
        'puede_resolver': puede_resolver and ajuste.estado_ajuste == EstadoAjuste.PENDIENTE,
        'puede_aprobar': (ajuste.id_usuario_id != usuario.pk
                          or servicios.puede_aprobar_lo_propio(usuario)),
        'importe': sum((r.importe or 0 for r in renglones), 0) if ve_diferencias else None,
        'token': idempotencia.emitir(request),
    })


@require_POST
@requiere_permiso('autorizar_ajustes', redirigir_a='stock:ajustes')
def resolver(request, pk):
    ajuste = get_object_or_404(AjusteStock, pk=pk)
    antes = instantanea(ajuste)
    if not idempotencia.consumir(request, request.POST.get('token_operacion')):
        messages.info(request, 'Esa resolución ya se había procesado.')
        return redirect('stock:ajuste', pk=pk)
    observacion = request.POST.get('observacion', '')
    try:
        if request.POST.get('decision') == 'aprobar':
            ajuste = servicios.aprobar(ajuste, request.usuario_actual, observacion)
        else:
            ajuste = servicios.rechazar(ajuste, request.usuario_actual, observacion)
    except ValidationError as error:
        messages.error(request, _texto(error))
        return redirect('stock:ajuste', pk=pk)
    registrar_auditoria(
        request, 'APROBACION' if ajuste.estado_ajuste == EstadoAjuste.APROBADO else 'RECHAZO',
        'AJUSTE DE STOCK', instancia=ajuste,
        anterior=antes, nuevo=foto(ajuste, renglones=ajuste.detalles.all()),
        detalle=f'{ajuste}. {observacion}'.strip())
    resolucion = ajuste.get_estado_ajuste_display().lower()
    if ajuste.tipo_ajuste == TipoAjuste.MERMA:          # la merma: aprobada
        resolucion = resolucion[:-1] + 'a'
    messages.success(request, f'{ajuste.get_tipo_ajuste_display()} N° {ajuste.pk} '
                              f'{resolucion}.')
    return redirect('stock:ajustes')


# ===========================================================================
#  CONTEO
# ===========================================================================
@requiere_permiso('contar_stock', redirigir_a='stock:ajustes')
def conteo(request):
    abierto = _conteo_abierto()
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'empezar':
                abierto = servicios.iniciar_conteo(
                    request.usuario_actual, request.POST.get('motivo') or MotivoAjuste.DIFERENCIA_CONTEO)
                registrar_auditoria(request, 'INICIO', 'CONTEO', instancia=abierto,
                                    nuevo=instantanea(abierto),
                                    detalle=f'Empezó {abierto}.')
            elif abierto is None:
                raise ValidationError('No hay ningún conteo en curso.')
            elif accion == 'cargar':
                producto = _producto(request.POST.get('producto'))
                if producto is None:
                    raise ValidationError('Elegí el producto que contaste.')
                contada = leer_numero(request.POST.get('contada'), 3)
                if contada is None:
                    raise ValidationError('Escribí cuánto contaste.')
                nuevo_lote = None
                if request.POST.get('lote') == 'nuevo':
                    try:
                        vence = date.fromisoformat(request.POST.get('vence', ''))
                    except ValueError:
                        raise ValidationError('Indicá la fecha de vencimiento del lote nuevo.')
                    nuevo_lote = {'fecha_vencimiento': vence,
                                  'numero_lote': request.POST.get('numero_lote', '').strip() or None}
                    lote = None
                else:
                    lote = _lote(producto, request.POST.get('lote'))
                # Góndola + depósito: si el producto ya estaba anotado en este
                # conteo y la casilla está tildada, lo nuevo se suma.
                ya = (abierto.detalles.filter(id_producto=producto, id_lote=lote).first()
                      if nuevo_lote is None else None)
                sumado = ya is not None and request.POST.get('sumar') == '1'
                total = contada + ya.cantidad_contada if sumado else contada
                servicios.cargar_conteo(abierto, producto, total, lote=lote,
                                        nuevo_lote=nuevo_lote)
                if sumado:
                    messages.success(request, (
                        f'Sumado: {producto.cantidad_con_unidad(ya.cantidad_contada)} + '
                        f'{producto.cantidad_con_unidad(contada)} = '
                        f'{producto.cantidad_con_unidad(total)} de {producto}.'))
                else:
                    messages.success(request, f'Anotado: {producto.cantidad_con_unidad(contada)} de {producto}.'
                                     + (' Reemplazó lo que estaba anotado.' if ya is not None else ''))
            elif accion == 'alta_rapida':
                producto, contada = _alta_rapida(request)
                servicios.cargar_conteo(abierto, producto, contada)
                registrar_auditoria(request, 'ALTA', 'PRODUCTO', instancia=producto, nuevo=instantanea(producto),
                                    detalle=f'Alta rápida de {producto} desde el conteo N° {abierto.pk}.')
                messages.success(request, f'Producto nuevo cargado y anotado: '
                                          f'{producto.cantidad_con_unidad(contada)} de {producto}.')
            elif accion == 'enviar':
                antes = instantanea(abierto)
                abierto = servicios.enviar_conteo(abierto)
                registrar_auditoria(request, 'ENVIO', 'CONTEO', instancia=abierto,
                                    anterior=antes,
                                    nuevo=foto(abierto, renglones=abierto.detalles.all()),
                                    detalle=f'Envió a revisión {abierto}.')
                messages.success(request, 'Conteo enviado. Ahora lo revisa quien autoriza los ajustes.')
                return redirect('stock:ajustes')
        except ValidationError as error:
            messages.error(request, _texto(error))
        return redirect('stock:conteo')

    return render(request, 'stock/conteo.html', {
        'conteo': abierto,
        'renglones': (abierto.detalles.select_related('id_producto', 'id_lote')
                      .order_by('-pk') if abierto else []),
        'motivos': _motivos(TipoAjuste.CONTEO),
        'catalogo': _catalogo(),
        'rubros': Rubro.objects.filter(borrado_logico=False).order_by('nombre_rubro'),
        'puede_crear': request.usuario_actual.tiene_permiso('crear_producto'),
    })


@transaction.atomic
def _alta_rapida(request):
    """Un producto que apareció en la góndola y no estaba cargado: se da de
    alta con lo mínimo y se devuelve junto con lo contado (DD-90).

    Marca «Genérica» (o la que se haya encontrado) y «Proveedor General»:
    se completan después, al recibir la primera compra.
    """
    from stock.reservados import asignar_marca, marca_generica, proveedor_general

    if not request.usuario_actual.tiene_permiso('crear_producto'):
        raise ValidationError('Tu perfil no puede dar de alta productos: avisale a quien pueda '
                              'y dejá ese producto aparte.')
    datos = request.POST
    contada = leer_numero(datos.get('contada'), 3)
    if contada is None:
        raise ValidationError('Escribí cuánto contaste.')
    producto = Producto(
        codigo_barras=datos.get('codigo'), nombre_producto=datos.get('nombre', ''),
        id_rubro=Rubro.objects.filter(pk=datos.get('rubro') or 0, borrado_logico=False).first(),
        id_marca=marca_generica(), id_proveedor=proveedor_general(),
        precio_venta=leer_numero(datos.get('precio'), 2) or 0,
        precio_costo=leer_numero(datos.get('costo'), 2) or 0)
    if producto.id_rubro is None:
        raise ValidationError('Elegí el rubro.')
    producto.full_clean()
    producto.save()
    asignar_marca(producto, datos.get('marca'))
    return producto, contada


@requiere_alguno(*PERMISOS_DE_AJUSTES)
def conteo_buscar(request):
    """Lo que leyó el escáner en el conteo: el producto, sus lotes y cuánto
    lleva anotado; o ``existe: false`` para ofrecer el alta rápida."""
    from nucleo.validaciones import sanear_codigo

    codigo = sanear_codigo(request.GET.get('codigo', ''))
    if not codigo or not codigo.isdigit() or not 3 <= len(codigo) <= 30:
        return JsonResponse({'existe': False, 'codigo': '', 'valido': False})
    producto = Producto.objects.filter(codigo_barras=codigo).first()
    if producto is None:
        return JsonResponse({'existe': False, 'codigo': codigo, 'valido': True})
    if producto.borrado_logico or not producto.tiene_stock_propio:
        return JsonResponse({'existe': True, 'contable': False, 'nombre': str(producto), 'motivo': (
            'Está dado de baja: reactivalo en Productos.' if producto.borrado_logico
            else 'No lleva stock propio (se prepara en el local): no se cuenta.')})
    abierto = _conteo_abierto()
    anotado = {d.id_lote_id: d.cantidad_contada
               for d in abierto.detalles.filter(id_producto=producto)} if abierto else {}
    lotes = ([{'id': l.pk, 'texto': f'vence {l.fecha_vencimiento:%d/%m/%Y}',
               'anotado': str(anotado[l.pk]) if l.pk in anotado else None}
              for l in servicios.lotes_fefo(producto)] if producto.controla_vencimiento else [])
    return JsonResponse({
        'existe': True, 'contable': True, 'id': producto.pk, 'nombre': str(producto),
        'unidad': 'u.' if producto.exige_cantidad_entera else producto.unidad_medida,
        'entera': producto.exige_cantidad_entera, 'por_lote': producto.controla_vencimiento,
        'lotes': lotes, 'anotado': str(anotado[None]) if None in anotado else None})


# ===========================================================================
#  MERMA Y TRASPASO
# ===========================================================================
@requiere_permiso('registrar_merma', redirigir_a='stock:ajustes')
def merma(request):
    contexto = {'motivos': _motivos(TipoAjuste.MERMA), 'filas': range(FILAS),
                'catalogo': _catalogo(), 'datos': request.POST,
                'puede_aprobar': (request.usuario_actual.puede('autorizar_ajustes')
                                  and servicios.puede_aprobar_lo_propio(request.usuario_actual))}
    if request.method == 'POST':
        token = request.POST.get('token_operacion')
        try:
            renglones = []
            for i in range(FILAS):
                producto = _producto(request.POST.get(f'producto_{i}'))
                if producto is None:
                    continue
                cantidad = leer_numero(request.POST.get(f'cantidad_{i}'), 3)
                if cantidad is None:
                    raise ValidationError(f'Escribí la cantidad de "{producto}".')
                renglones.append((producto, cantidad, _lote(producto, request.POST.get(f'lote_{i}'))))
            if not idempotencia.consumir(request, token):
                messages.info(request, 'Esa merma ya se había cargado.')
                return redirect('stock:ajustes')
            aprobar_ya = contexto['puede_aprobar'] and request.POST.get('aprobar_ya') == '1'
            ajuste = servicios.registrar_merma(
                request.usuario_actual, request.POST.get('motivo'), renglones,
                request.POST.get('detalle'), aprobar_ya=aprobar_ya)
        except ValidationError as error:
            idempotencia.devolver(request, token)
            contexto['error'] = _texto(error)
        else:
            registrar_auditoria(request, 'ALTA', 'MERMA', instancia=ajuste,
                                nuevo=foto(ajuste, renglones=ajuste.detalles.all()),
                                detalle=f'Cargó {ajuste}.')
            messages.success(request, f'Merma N° {ajuste.pk} cargada'
                             + (' y aprobada.' if aprobar_ya else '. Espera autorización.'))
            return redirect('stock:ajustes')
    contexto['token'] = request.POST.get('token_operacion') or idempotencia.emitir(request)
    contexto['elegidos'] = _elegidos(request, FILAS)
    return render(request, 'stock/merma.html', contexto)


@requiere_permiso('registrar_traspaso', redirigir_a='stock:ajustes')
def traspaso(request):
    filas = 3
    contexto = {'motivos': _motivos(TipoAjuste.TRASPASO), 'catalogo': _catalogo(),
                'datos': request.POST,
                'puede_aprobar': (request.usuario_actual.puede('autorizar_ajustes')
                                  and servicios.puede_aprobar_lo_propio(request.usuario_actual))}
    if request.method == 'POST':
        token = request.POST.get('token_operacion')
        try:
            renglones = []
            for i in range(filas):
                origen = _producto(request.POST.get(f'producto_{i}'))
                if origen is None:
                    continue
                destino = _producto(request.POST.get(f'destino_{i}'))
                if destino is None:
                    raise ValidationError(f'Elegí a qué producto pasa "{origen}".')
                cantidad = leer_numero(request.POST.get(f'cantidad_{i}'), 3)
                cantidad_destino = leer_numero(request.POST.get(f'cantidad_destino_{i}'), 3)
                if cantidad is None or cantidad_destino is None:
                    raise ValidationError(f'Completá las cantidades de "{origen}".')
                renglones.append((origen, cantidad, destino, cantidad_destino,
                                  _lote(origen, request.POST.get(f'lote_{i}'))))
            if not idempotencia.consumir(request, token):
                messages.info(request, 'Ese traspaso ya se había cargado.')
                return redirect('stock:ajustes')
            aprobar_ya = contexto['puede_aprobar'] and request.POST.get('aprobar_ya') == '1'
            ajuste = servicios.registrar_traspaso(
                request.usuario_actual, request.POST.get('motivo'), renglones,
                request.POST.get('detalle'), aprobar_ya=aprobar_ya)
        except ValidationError as error:
            idempotencia.devolver(request, token)
            contexto['error'] = _texto(error)
        else:
            registrar_auditoria(request, 'ALTA', 'TRASPASO', instancia=ajuste,
                                nuevo=foto(ajuste, renglones=ajuste.detalles.all()),
                                detalle=f'Cargó {ajuste}.')
            messages.success(request, f'Traspaso N° {ajuste.pk} cargado'
                             + (' y aprobado.' if aprobar_ya else '. Espera autorización.'))
            return redirect('stock:ajustes')
    contexto['token'] = request.POST.get('token_operacion') or idempotencia.emitir(request)
    contexto['elegidos'] = _elegidos(request, filas, con_destino=True)
    return render(request, 'stock/traspaso.html', contexto)


def _elegidos(request, filas, con_destino=False):
    """Lo ya cargado en cada fila, para no perderlo si hubo un error."""
    resultado = []
    for i in range(filas):
        fila = {'indice': i, 'producto': _producto(request.POST.get(f'producto_{i}', '')),
                'cantidad': request.POST.get(f'cantidad_{i}', ''),
                'lote': request.POST.get(f'lote_{i}', '')}
        if con_destino:
            fila['destino'] = _producto(request.POST.get(f'destino_{i}', ''))
            fila['cantidad_destino'] = request.POST.get(f'cantidad_destino_{i}', '')
        resultado.append(fila)
    return resultado
