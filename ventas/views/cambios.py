"""Cambios y devoluciones desde el mostrador.

En dos pasos, sin JavaScript de por medio:

1. Se busca el ticket por número y se marca qué devuelve el cliente y qué
   se lleva. "Calcular" muestra la diferencia, sin escribir nada.
2. "Confirmar" registra el cambio (``ventas.cambios.registrar``) con un
   token de un solo uso. Si hay que devolver plata, lo autoriza otra
   persona con su usuario y su clave, ahí mismo.

Si la venta no debió existir (se cobró dos veces, rechazaron la tarjeta),
desde acá mismo el cajero PIDE la anulación y la encargada la autoriza con
su clave (``ventas.views.consultas.anular_venta``, DD-61).
"""
import uuid
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse

from nucleo.formularios import leer_numero
from seguridad.decorators import requiere_permiso
from seguridad.servicios import autorizar_con_clave, registrar_auditoria
from stock.models import Producto
from ventas import cambios as servicio
from ventas.anulacion import motivo_de as anulacion_motivo
from ventas import puesto
from ventas.models import MedioPago, MotivoCambio, Venta

MAXIMO_ENTREGADOS = 5


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _leer(request, venta):
    """Lo que se marcó en el formulario -> (devueltos, entregados)."""
    datos = request.POST
    devueltos = []
    for renglon in venta.detalles.select_related('id_producto'):
        texto = datos.get(f'devuelve_{renglon.pk}', '').strip()
        if not texto:
            continue
        cantidad = leer_numero(texto, 3)
        if cantidad is None or cantidad < 0:
            raise ValidationError(
                f'La cantidad devuelta de "{renglon.descripcion_item}" no es válida.')
        if cantidad == 0:
            continue
        queda = servicio.devolvible(renglon)
        if cantidad > queda:
            raise ValidationError(
                f'De "{renglon.descripcion_item}" se pueden devolver como '
                f'máximo {renglon.id_producto.cantidad_con_unidad(queda)}.')
        devueltos.append(servicio.Devuelto(
            renglon, cantidad, datos.get(f'reingresa_{renglon.pk}') == '1'))

    entregados = []
    for i in range(MAXIMO_ENTREGADOS):
        id_producto = datos.get(f'entrega_producto_{i}', '')
        texto = datos.get(f'entrega_cantidad_{i}', '').strip()
        if not id_producto:
            continue
        producto = Producto.objects.filter(pk=id_producto if id_producto.isdigit() else 0).first()
        cantidad = leer_numero(texto or '1', 3)
        if producto is None:
            raise ValidationError('Uno de los productos que se lleva ya no existe.')
        if cantidad is None or cantidad <= 0:
            raise ValidationError(f'La cantidad de "{producto}" no es válida.')
        entregados.append(servicio.Entregado(producto, cantidad))
    return devueltos, entregados


def _filas_entrega(request):
    """Las filas de "se lleva", con lo ya elegido si se está recalculando."""
    filas = []
    for i in range(MAXIMO_ENTREGADOS):
        elegido = request.POST.get(f'entrega_producto_{i}', '')
        producto = (Producto.objects.filter(pk=elegido).first()
                    if elegido.isdigit() else None)
        filas.append({'indice': i, 'producto': producto,
                      'cantidad': request.POST.get(f'entrega_cantidad_{i}', '')})
    return filas


@requiere_permiso('registrar_cambio', redirigir_a='home')
def cambios(request):
    turno = puesto.turno_de(request)
    numero = (request.POST.get('venta') or request.GET.get('venta') or '').strip()
    venta = None
    if numero:
        venta = (Venta.objects.select_related('id_usuario')
                 .filter(pk=int(numero) if numero.isdigit() else 0).first())
        if venta is None:
            messages.error(request, f'No existe la venta N° {numero}.')

    contexto = {
        'turno': turno, 'venta': venta, 'numero': numero,
        'motivos': MotivoCambio.choices,
        'medios': MedioPago.objects.filter(borrado_logico=False),
        'filas_entrega': _filas_entrega(request),
        'catalogo': reverse('stock:buscar_productos') + '?tipos=REVENTA,ELABORADO,PRODUCIDO,COMBO',
        'datos': request.POST,
    }
    if venta is not None:
        from ventas.views.consultas import contexto_anulacion

        contexto.update(contexto_anulacion(request, venta))
        contexto['motivo_anulacion'] = anulacion_motivo(venta)
        contexto['renglones'] = [
            (r, servicio.devolvible(r),
             request.POST.get(f'devuelve_{r.pk}', ''),
             request.POST.get(f'reingresa_{r.pk}', '1'))
            for r in venta.detalles.select_related('id_producto')]

    if request.method != 'POST' or venta is None:
        contexto['token'] = uuid.uuid4().hex
        return render(request, 'ventas/cambios.html', contexto)

    contexto['token'] = request.POST.get('token') or uuid.uuid4().hex
    accion = request.POST.get('accion')
    try:
        devueltos, entregados = _leer(request, venta)
        devuelto, entregado, saldo = servicio.balance(venta, devueltos, entregados)
        contexto.update({'calculado': True, 'devuelto': devuelto,
                         'entregado': entregado, 'saldo': saldo,
                         'a_devolver': -saldo if saldo < 0 else Decimal('0'),
                         'a_cobrar': saldo if saldo > 0 else Decimal('0')})
        if accion != 'confirmar':
            return render(request, 'ventas/cambios.html', contexto)

        if turno is None:
            raise ValidationError('La caja de este dispositivo no tiene un turno abierto.')
        medio = None
        if saldo:
            medio = MedioPago.objects.filter(pk=request.POST.get('medio') or 0,
                                             borrado_logico=False).first()
        autorizante = None
        if saldo < 0:
            autorizante = autorizar_con_clave(
                request, request.POST.get('autoriza_usuario'),
                request.POST.get('autoriza_clave'), 'autorizar_devolucion',
                para=f'devolver $ {-saldo} al cliente')
        try:
            token = uuid.UUID(contexto['token'])
        except ValueError:
            raise ValidationError('El formulario venció. Volvé a buscar la venta.')
        cambio = servicio.registrar(
            turno, request.usuario_actual, venta, request.POST.get('motivo'),
            devueltos, entregados, request.POST.get('detalle_motivo'),
            medio_pago=medio, autorizante=autorizante, token=token)
    except ValidationError as error:
        contexto['error'] = _texto(error)
        return render(request, 'ventas/cambios.html', contexto)

    registrar_auditoria(
        request, 'ALTA', 'CAMBIO', instancia=cambio,
        detalle=(f'Cambio N° {cambio.pk} de la venta N° {venta.pk}: '
                 f'{cambio.get_motivo_display()}. Devuelto $ {cambio.importe_devuelto}, '
                 f'cobrado $ {cambio.importe_cobrado}'
                 + (f'; autorizó {cambio.id_usuario_autorizante}' if cambio.id_usuario_autorizante else '')
                 + '.'))
    reverso = getattr(cambio, 'reverso_puntos', None)
    if reverso is not None and reverso.hubo:
        cliente = venta.id_cliente
        partes = []
        if reverso.acumulacion:
            partes.append(f'se descuentan {reverso.acumulacion} punto(s) de la compra devuelta')
        if reverso.canjes:
            partes.append(f'se le devuelven {reverso.canjes} punto(s) del canje')
        texto = '; '.join(partes)
        registrar_auditoria(
            request, 'REVERSO DE PUNTOS', 'CLIENTE', instancia=cliente,
            detalle=(f'{cliente}: {texto} (venta N° {venta.pk}, cambio N° {cambio.pk}). '
                     f'Disponibles: {reverso.disponibles}.'))
        messages.info(request, f'Puntos de {cliente.nombre_cliente} {cliente.apellido_cliente}: '
                               f'{texto}. Le quedan {reverso.disponibles} disponibles.')
    if cambio.importe_devuelto:
        messages.success(request, f'Cambio N° {cambio.pk} registrado. Devolvé $ {cambio.importe_devuelto} al cliente.')
    elif cambio.importe_cobrado:
        messages.success(request, f'Cambio N° {cambio.pk} registrado. Cobrale $ {cambio.importe_cobrado} al cliente.')
    else:
        messages.success(request, f'Cambio N° {cambio.pk} registrado, sin diferencia de dinero.')
    return redirect('ventas:cambios')
