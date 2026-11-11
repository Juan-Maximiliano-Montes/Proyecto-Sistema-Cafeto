"""La mercadería que llega: la factura, sus renglones y sus lotes.

El formulario arma los renglones en el navegador (producto, cantidad,
costo y, si va por lote, cada lote con su vencimiento) y los manda juntos
en un campo JSON. El servidor los vuelve a validar uno por uno y llama a
``compras.servicios.recepcionar``, que guarda todo o nada: si un lote no
cuadra, no entra ni la factura.

Si hay un error, la pantalla vuelve con lo cargado tal cual: tipear de
nuevo una factura de treinta renglones porque un vencimiento estaba mal es
lo que hace que nadie quiera cargar las compras.
"""
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from compras import servicios
from compras.models import Compra, OrdenCompra, Proveedor, TipoComprobante
from nucleo import idempotencia
from nucleo.exportar import ColumnaExportable, exportar
from nucleo.formularios import leer_numero
from nucleo.paginacion import TAMANIOS_TABLA, paginar
from seguridad.decorators import requiere_alguno, requiere_permiso
from seguridad.servicios import foto, registrar_auditoria
from stock.models import Producto
from nucleo.templatetags.cafeto import pesos

MAXIMO_DE_RENGLONES = 120


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _fecha(texto, etiqueta, obligatoria=False):
    if not texto:
        if obligatoria:
            raise ValidationError(f'Indicá {etiqueta}.')
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise ValidationError(f'{etiqueta.capitalize()} no es una fecha válida.')


def _leer_renglones(texto):
    """El JSON del formulario -> [RenglonRecibido]. Valida la forma."""
    try:
        crudos = json.loads(texto or '[]')
    except ValueError:
        raise ValidationError('Los renglones llegaron mal armados. Recargá la página.')
    if not isinstance(crudos, list) or len(crudos) > MAXIMO_DE_RENGLONES:
        raise ValidationError('La compra tiene demasiados renglones.')
    productos = Producto.objects.in_bulk(
        [r.get('producto') for r in crudos if isinstance(r, dict)
         and isinstance(r.get('producto'), int)])
    renglones = []
    for crudo in crudos:
        producto = productos.get(crudo.get('producto')) if isinstance(crudo, dict) else None
        if producto is None:
            raise ValidationError('Uno de los productos ya no existe.')
        cantidad = leer_numero(crudo.get('cantidad'), 3)
        costo = leer_numero(crudo.get('costo'), 2)
        if cantidad is None or cantidad <= 0:
            raise ValidationError(f'La cantidad de "{producto}" no es válida.')
        if costo is None:
            raise ValidationError(f'El costo de "{producto}" no es válido.')
        lotes = []
        crudos_lote = [l for l in (crudo.get('lotes') or []) if isinstance(l, dict)]
        for lote in crudos_lote:
            vence = _fecha(lote.get('vence'), f'el vencimiento de un lote de "{producto}"', True)
            # Un solo lote sin cantidad: es todo lo recibido (se tipea la
            # fecha y nada más, que es el caso de todos los días).
            texto = lote.get('cantidad') or (crudo.get('cantidad') if len(crudos_lote) == 1 else '')
            cantidad_lote = leer_numero(texto, 3)
            if cantidad_lote is None or cantidad_lote <= 0:
                raise ValidationError(f'La cantidad de un lote de "{producto}" no es válida.')
            lotes.append(servicios.LoteRecibido(vence, cantidad_lote,
                                                (lote.get('numero') or '').strip()[:30] or None))
        renglones.append(servicios.RenglonRecibido(producto, cantidad, costo, lotes))
    return renglones


def _aviso_de_diferencias(orden):
    partes = []
    for fila in orden.diferencias():
        producto, dif = fila['producto'], fila['diferencia']
        if dif < 0:
            partes.append(f'faltan {producto.cantidad_con_unidad(-dif)} de {producto}')
        elif dif > 0:
            partes.append(f'sobran {producto.cantidad_con_unidad(dif)} de {producto}')
    return (f'Lo recibido no coincide con la orden N° {orden.pk}: '
            + '; '.join(partes) + '. La orden queda con diferencias hasta que '
            'llegue el resto o se cierre con lo acordado con el proveedor.')


def _renglones_de_la_orden(orden):
    """La planilla de control: lo pedido. Si la orden ya tuvo una entrega
    parcial, sólo lo que falta (DD-64)."""
    faltan = None
    if orden.estado_orden == 'CON_DIFERENCIAS':
        faltan = {f['producto'].pk: -f['diferencia'] for f in orden.diferencias()
                  if f['diferencia'] < 0}
    renglones = []
    for d in orden.detalles.select_related('id_producto'):
        cantidad = d.cantidad_pedida if faltan is None else faltan.get(d.id_producto_id)
        if not cantidad:
            continue
        renglones.append({
            'producto': d.id_producto_id, 'nombre': str(d.id_producto),
            'controla': d.id_producto.controla_vencimiento,
            'fraccionable': not d.id_producto.exige_cantidad_entera,
            'cantidad': str(cantidad.normalize()), 'costo': str(d.precio_unitario),
            'lotes': [],
        })
    return renglones


SALTO_DE_COSTO = 3


def _costos_que_llaman_la_atencion(renglones):
    """Renglones cuyo costo es más del triple, o menos de un tercio, del anterior.

    Casi siempre es un cero de más o de menos al tipear. No se prohíbe (los
    precios cambian): se pide que quien carga lo mire y lo confirme.
    """
    raros = []
    for renglon in renglones:
        anterior = renglon.producto.precio_costo
        try:
            nuevo = Decimal(renglon.costo_unitario)
        except (InvalidOperation, TypeError):
            continue
        if not anterior or anterior <= 0 or nuevo <= 0:
            continue
        if nuevo > anterior * SALTO_DE_COSTO or nuevo * SALTO_DE_COSTO < anterior:
            raros.append({'producto': renglon.producto, 'anterior': anterior, 'nuevo': nuevo})
    return raros


@requiere_permiso('registrar_compra', redirigir_a='home')
def recepcion(request):
    proveedores = Proveedor.objects.filter(borrado_logico=False).exclude(
        tipo_proveedor='SERVICIOS').order_by('nombre_proveedor')
    orden = None
    if request.GET.get('orden', '').isdigit():
        orden = OrdenCompra.objects.filter(
            pk=request.GET['orden'],
            estado_orden__in=('EMITIDA', 'CON_DIFERENCIAS')).first()
    datos = request.POST if request.method == 'POST' else {}
    contexto = {
        'proveedores': proveedores, 'tipos': TipoComprobante.choices,
        'orden': orden, 'datos': datos, 'hoy': timezone.localdate().isoformat(),
        'catalogo': reverse('stock:buscar_productos') + '?tipos=REVENTA,INSUMO',
        'renglones_json': datos.get('renglones') or json.dumps(
            _renglones_de_la_orden(orden) if orden else []),
    }

    if request.method == 'POST':
        token = request.POST.get('token_operacion')
        try:
            proveedor = proveedores.filter(pk=request.POST.get('proveedor') or 0).first()
            if proveedor is None:
                raise ValidationError('Elegí el proveedor.')
            if request.POST.get('orden', '').isdigit():
                orden = OrdenCompra.objects.filter(pk=request.POST['orden']).first()
            punto = request.POST.get('punto_venta', '').strip()
            numero = request.POST.get('numero', '').strip()
            if not punto.isdigit() or not numero.isdigit():
                raise ValidationError('El punto de venta y el número del comprobante van en números.')
            renglones = _leer_renglones(request.POST.get('renglones'))
            otros = leer_numero(request.POST.get('otros') or '0', 2)
            if otros is None:
                raise ValidationError('Los otros conceptos no son un número.')
            raros = _costos_que_llaman_la_atencion(renglones)
            if raros and not request.POST.get('confirmo_costos'):
                contexto['costos_raros'] = raros
                raise ValidationError(
                    'Hay costos muy distintos de los que venías pagando. Miralos '
                    'abajo: si están bien, tildá la confirmación y guardá de nuevo.')
            if not idempotencia.consumir(request, token):
                messages.info(request, 'Esa compra ya se había cargado.')
                return redirect('compras:compras')
            compra = servicios.recepcionar(
                proveedor, request.usuario_actual, request.POST.get('tipo_comprobante'),
                punto, numero, renglones,
                fecha_compra=_fecha(request.POST.get('fecha'), 'la fecha de la factura'),
                importe_otros_conceptos=otros, orden=orden,
                fecha_vencimiento_pago=_fecha(request.POST.get('vence_pago'), 'el vencimiento del pago'))
        except ValidationError as error:
            idempotencia.devolver(request, token)
            contexto['error'] = _texto(error)
        else:
            registrar_auditoria(
                request, 'ALTA', 'COMPRA', instancia=compra,
                nuevo=foto(compra, renglones=compra.detalles.all()),
                detalle=f'{compra.get_tipo_comprobante_display()} {compra.numero_formateado} '
                        f'de {proveedor}: {pesos(compra.total_compra)}, {len(renglones)} renglón(es).')
            messages.success(request, f'Compra cargada: entró la mercadería de {proveedor}.')
            if orden is not None:
                orden.refresh_from_db()
                if orden.estado_orden == 'CON_DIFERENCIAS':
                    messages.warning(request, _aviso_de_diferencias(orden))
                    return redirect('compras:orden', pk=orden.pk)
            return redirect('compras:compras')

    contexto['token'] = request.POST.get('token_operacion') or idempotencia.emitir(request)
    return render(request, 'compras/recepcion.html', contexto)


# ===========================================================================
#  CONSULTA
# ===========================================================================
def _compras_filtradas(request):
    compras = (Compra.objects.select_related('id_proveedor', 'id_usuario')
               .order_by('-fecha_compra', '-pk'))
    filtro = request.GET
    if filtro.get('proveedor', '').isdigit():
        compras = compras.filter(id_proveedor_id=filtro['proveedor'])
    if filtro.get('estado') in dict(Compra.ESTADOS):
        compras = compras.filter(estado_compra=filtro['estado'])
    for clave, campo in (('desde', 'fecha_compra__gte'), ('hasta', 'fecha_compra__lte')):
        try:
            if filtro.get(clave):
                compras = compras.filter(**{campo: date.fromisoformat(filtro[clave])})
        except ValueError:
            pass
    return compras


@requiere_alguno('ver_compras', 'registrar_compra')
def compras(request):
    consulta = _compras_filtradas(request)
    contexto = paginar(request, consulta, TAMANIOS_TABLA, por_defecto=25)
    contexto.update({
        'filtro': request.GET, 'estados': Compra.ESTADOS,
        'proveedores': Proveedor.objects.filter(compras__isnull=False).distinct()
                                         .order_by('nombre_proveedor'),
        # 'total' es del paginador (cuántas filas): con la suma en pesos el
        # pie decía "1–13 de 1.144.560,40".
        'total_comprado': consulta.aggregate(t=Sum('total_compra'))['t'],
    })
    return render(request, 'compras/compras.html', contexto)


@requiere_alguno('ver_compras', 'registrar_compra')
def ficha_compra(request, pk):
    compra = get_object_or_404(Compra.objects.select_related('id_proveedor', 'id_usuario',
                                                             'id_orden_compra'), pk=pk)
    return render(request, 'compras/_ficha_compra.html', {
        'compra': compra,
        'renglones': compra.detalles.select_related('id_producto').prefetch_related('lotes'),
        'egresos': compra.egresos.select_related('id_medio_pago').order_by('pk'),
    })


@requiere_alguno('ver_compras', 'registrar_compra')
def exportar_compras(request):
    columnas = [
        ColumnaExportable('N°', 'numero'), ColumnaExportable('Fecha', 'fecha'),
        ColumnaExportable('Proveedor'), ColumnaExportable('Comprobante'),
        ColumnaExportable('Total', 'dinero'), ColumnaExportable('Saldo', 'dinero'),
        ColumnaExportable('Vence el pago', 'fecha'), ColumnaExportable('Estado'),
    ]
    filas = ([c.pk, c.fecha_compra, str(c.id_proveedor),
              f'{c.get_tipo_comprobante_display()} {c.numero_formateado}',
              c.total_compra, c.saldo_pendiente, c.fecha_vencimiento_pago,
              c.get_estado_compra_display()]
             for c in _compras_filtradas(request))
    formato = request.GET.get('formato')
    respuesta = exportar(formato, 'Compras', columnas, filas)
    registrar_auditoria(request, 'EXPORTACION', 'COMPRAS', modulo='COMPRAS',
                        detalle='Exportó el listado de compras.')
    return respuesta
