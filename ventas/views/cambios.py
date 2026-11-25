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
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from nucleo.formularios import leer_numero
from seguridad.decorators import requiere_alguno, requiere_permiso
from seguridad.servicios import (autorizar_con_clave, foto, instantanea, registrar_acceso_denegado,
                                 registrar_auditoria)
from fidelizacion.models import Cliente
from stock.models import Producto
from ventas import cambios as servicio
from ventas.anulacion import motivo_de as anulacion_motivo
from ventas import puesto
from ventas.models import Cambio, MedioPago, MotivoCambio, Venta
from nucleo.templatetags.cafeto import pesos
from nucleo.validaciones import id_valido
from nucleo.textos import pluralizar, segun

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
        producto = Producto.objects.filter(pk=id_valido(id_producto)).first()
        cantidad = leer_numero(texto or '1', 3)
        if producto is None:
            raise ValidationError('Uno de los productos que se lleva ya no existe.')
        if cantidad is None or cantidad <= 0:
            raise ValidationError(f'La cantidad de "{producto}" no es válida.')
        entregados.append(servicio.Entregado(producto, cantidad))
    return devueltos, entregados


def _con_recargo(venta, medio):
    return venta.pagos.filter(id_medio_pago=medio, recargo_porcentaje__gt=0).exists()


def _filas_entrega(request):
    """Las filas de "se lleva", con lo ya elegido si se está recalculando."""
    filas = []
    for i in range(MAXIMO_ENTREGADOS):
        elegido = request.POST.get(f'entrega_producto_{i}', '')
        producto = (Producto.objects.filter(pk=id_valido(elegido)).first()
                    if id_valido(elegido) else None)
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
                 .filter(pk=id_valido(numero)).first())
        if venta is None:
            messages.error(request, f'No existe la venta N° {numero}.')

    contexto = {
        'turno': turno, 'venta': venta, 'numero': numero,
        'motivos': MotivoCambio.choices,
        'medios': MedioPago.objects.filter(borrado_logico=False),
        'filas_entrega': _filas_entrega(request),
        'catalogo': reverse('stock:buscar_productos') + '?tipos=REVENTA,ELABORADO,PRODUCIDO,COMBO',
        'datos': request.POST,
        'comprobante': (Cambio.objects.filter(pk=id_valido(request.GET['comprobante'])).first()
                        if id_valido(request.GET.get('comprobante')) else None),
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
        # Se devuelve por el mismo medio con que se pagó; se cobra en un
        # solo pago (DD-65, DD-66).
        if saldo < 0:
            contexto['medios'] = servicio.medios_de_reintegro(venta)
            contexto['con_recargo'] = []
            for m in contexto['medios']:
                if _con_recargo(venta, m):
                    try:
                        contexto['con_recargo'].append(
                            (m, servicio.reintegro(venta, m, -saldo)))
                    except ValidationError:
                        pass   # se avisa al confirmar, con el medio elegido
        elif saldo > 0:
            contexto['medios'] = contexto['medios'].filter(cantidad_cuotas=1)
            if venta.id_cliente_id is None:
                # VEN-01: sin cliente no hay a quién anotarle la deuda.
                contexto['medios'] = contexto['medios'].exclude(
                    nombre_medio_pago=MedioPago.NOMBRE_CUENTA_CORRIENTE)
        if accion != 'confirmar':
            return render(request, 'ventas/cambios.html', contexto)

        if turno is None:
            raise ValidationError('La caja de este dispositivo no tiene un turno abierto.')
        medio = None
        if saldo:
            medio = contexto['medios'].filter(pk=id_valido(request.POST.get('medio'))).first()
            if medio is None:
                raise ValidationError(
                    'Elegí con qué medio se ' + ('cobra.' if saldo > 0 else
                                                 'devuelve: el mismo con que se pagó.'))
        autorizante = None
        operador = request.usuario_actual
        if saldo < 0:
            a_devolver = servicio.reintegro(venta, medio, -saldo)
            if operador.es_titular and operador.tiene_permiso('autorizar_devolucion'):
                # VEN-04: la titular ya está identificada con su sesión: no
                # se le vuelve a pedir la clave para autorizarse a sí misma.
                autorizante = operador
            else:
                autorizante = autorizar_con_clave(
                    request, request.POST.get('autoriza_usuario'),
                    request.POST.get('autoriza_clave'), 'autorizar_devolucion',
                    distinto_de=venta.id_usuario,
                    para=f'devolver {pesos(a_devolver)} al cliente')
        elif medio is not None and medio.es_cuenta_corriente:
            # VEN-01: fiar la diferencia puede pedir la firma de quien
            # gestiona el fiado (cliente sin cuenta, en mora o sobre el tope).
            if venta.id_cliente_id is None:
                raise ValidationError(
                    'Esta venta no tiene cliente: la diferencia no se puede '
                    'dejar en cuenta corriente. Elegí otro medio.')
            if request.POST.get('autoriza_usuario'):
                autorizante = autorizar_con_clave(
                    request, request.POST.get('autoriza_usuario'),
                    request.POST.get('autoriza_clave'), 'gestionar_credito',
                    para='dejar la diferencia en cuenta corriente')
        try:
            token = uuid.UUID(contexto['token'])
        except ValueError:
            raise ValidationError('El formulario venció. Volvé a buscar la venta.')
        cliente_antes = (instantanea(Cliente.objects.get(pk=venta.id_cliente_id))
                         if venta.id_cliente_id else None)
        cambio = servicio.registrar(
            turno, request.usuario_actual, venta, request.POST.get('motivo'),
            devueltos, entregados, request.POST.get('detalle_motivo'),
            medio_pago=medio, autorizante=autorizante, token=token)
    except ValidationError as error:
        contexto['error'] = _texto(error)
        return render(request, 'ventas/cambios.html', contexto)

    registrar_auditoria(
        request, 'ALTA', 'CAMBIO', instancia=cambio,
        nuevo=foto(cambio, renglones=cambio.detalles.all()),
        detalle=(f'Cambio N° {cambio.pk} de la venta N° {venta.pk}: '
                 f'{cambio.get_motivo_display()}. Devuelto {pesos(cambio.importe_devuelto)}, '
                 f'cobrado {pesos(cambio.importe_cobrado)}'
                 + (f'; autorizó {cambio.id_usuario_autorizante}' if cambio.id_usuario_autorizante else '')
                 + '.'))
    reverso = getattr(cambio, 'reverso_puntos', None)
    if reverso is not None and reverso.hubo:
        cliente = Cliente.objects.get(pk=venta.id_cliente_id)
        partes = []
        if reverso.acumulacion:
            partes.append(f"se {segun(reverso.acumulacion, 'descuenta', 'descuentan')} "
                         f"{pluralizar(reverso.acumulacion, 'punto')} de la compra devuelta")
        if reverso.canjes:
            partes.append(f"se le {segun(reverso.canjes, 'devuelve', 'devuelven')} "
                         f"{pluralizar(reverso.canjes, 'punto')} del canje")
        texto = '; '.join(partes)
        registrar_auditoria(
            request, 'REVERSO DE PUNTOS', 'CLIENTE', instancia=cliente,
            anterior=cliente_antes, nuevo=instantanea(cliente),
            detalle=(f'{cliente}: {texto} (venta N° {venta.pk}, cambio N° {cambio.pk}). '
                     f'Disponibles: {reverso.disponibles}.'))
        messages.info(request, f'Puntos de {cliente.nombre_cliente} {cliente.apellido_cliente}: '
                               f'{texto}. Le quedan {reverso.disponibles} disponibles.')
    if cambio.importe_devuelto and cambio.id_medio_pago.afecta_caja:
        messages.success(request, f'Cambio N° {cambio.pk} registrado. Devolvé {pesos(cambio.importe_devuelto)} al cliente.')
    elif cambio.importe_devuelto:
        messages.success(request, f'Cambio N° {cambio.pk} registrado. Hacé la devolución de '
                                  f'{pesos(cambio.importe_devuelto)} por {cambio.id_medio_pago} '
                                  '(en el posnet o la cuenta): no sale del cajón.')
    elif cambio.importe_cobrado:
        messages.success(request, f'Cambio N° {cambio.pk} registrado. Cobrale {pesos(cambio.importe_cobrado)} al cliente.')
    else:
        messages.success(request, f'Cambio N° {cambio.pk} registrado, sin diferencia de dinero.')
    return redirect(f"{reverse('ventas:cambios')}?comprobante={cambio.pk}")


@requiere_alguno('registrar_cambio', 'ver_ventas')
def comprobante_cambio(request, pk):
    """El comprobante del cambio o la devolución: uno para el cliente y otro
    para el cajón (si salió plata), con la firma de quien recibe."""
    from negocio.models import ParametrosNegocio

    cambio = get_object_or_404(
        Cambio.objects.select_related('id_venta__id_cliente', 'id_usuario', 'id_usuario_autorizante',
                                      'id_medio_pago', 'id_turno_caja__id_caja_registradora'), pk=pk)
    usuario = request.usuario_actual
    if cambio.id_usuario_id != usuario.pk and not usuario.puede('ver_ventas'):
        registrar_acceso_denegado(request, 'ver_ventas')
        messages.error(request, 'Ese cambio lo registró otra persona.')
        return redirect('ventas:cambios')
    renglones = list(cambio.detalles.select_related('id_producto').order_by('sentido', 'pk'))
    return render(request, 'ventas/comprobante_cambio.html', {
        'cambio': cambio, 'cliente': cambio.id_venta.id_cliente,
        'devuelve': [r for r in renglones if r.sentido == 'DEVUELVE'],
        'entrega': [r for r in renglones if r.sentido == 'ENTREGA'],
        'negocio': ParametrosNegocio.vigentes(), 'impreso': timezone.now(),
    })
