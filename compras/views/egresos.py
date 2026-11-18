"""Pagos y gastos: lo que hay que pagar, lo pagado y la conciliación.

Cuatro procesos sobre ``compras.egresos``:

    pagar ahora        el egreso pagado en el acto (contra entrega, servicios)
    anotar             la obligación que vence más adelante
    gasto de caja      la plata que sale del cajón (hielo, el plomero)
    conciliar          la transferencia que el banco confirmó o rechazó

Lo que se paga en efectivo sale del cajón del puesto: hace falta que la
caja de este dispositivo tenga un turno abierto.
"""
from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from compras import egresos as servicio
from compras.models import Compra, Egreso, Proveedor, TipoEgreso
from nucleo import idempotencia
from nucleo.exportar import ColumnaExportable, exportar
from nucleo.formularios import leer_numero
from nucleo.paginacion import TAMANIOS_TABLA, paginar
from seguridad.decorators import requiere_alguno, requiere_permiso
from seguridad.servicios import instantanea, registrar_acceso_denegado, registrar_auditoria
from ventas import puesto
from ventas.models import MedioPago, MovimientoCaja
from nucleo.templatetags.cafeto import pesos
from nucleo.validaciones import id_valido

PERMISOS = ('ver_egresos', 'registrar_egreso', 'registrar_gasto_caja',
            'conciliar_egresos')
RESUELTOS = ('ACREDITADO', 'RECHAZADO')
PESTANIAS = (('a_pagar', 'A pagar', 'bi-calendar-event'),
             ('por_acreditar', 'Por acreditar', 'bi-bank'),
             ('historial', 'Historial', 'bi-clock-history'))


def _texto(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _fecha(texto):
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        raise ValidationError('Una de las fechas no es válida.')


def _contexto_formularios(request):
    return {
        'tipos': TipoEgreso.objects.filter(borrado_logico=False).order_by('nombre_tipo_egreso'),
        # La cuenta corriente es el fiado de los clientes: con eso no se le
        # paga a nadie.
        'medios': MedioPago.objects.filter(borrado_logico=False)
                  .exclude(nombre_medio_pago=MedioPago.NOMBRE_CUENTA_CORRIENTE)
                  .order_by('-afecta_caja', 'nombre_medio_pago'),
        'proveedores': Proveedor.objects.filter(borrado_logico=False).order_by('nombre_proveedor'),
        'turno': puesto.turno_de(request),
        'token': idempotencia.emitir(request),
        'hoy': timezone.localdate(),
    }


@requiere_alguno(*PERMISOS)
def egresos(request):
    hoy = timezone.localdate()
    pestania = request.GET.get('ver', 'a_pagar')
    contexto = _contexto_formularios(request)
    base = Egreso.objects.select_related('id_tipo_egreso', 'id_proveedor', 'id_medio_pago', 'id_compra')
    if pestania == 'por_acreditar':
        consulta = base.filter(estado_pago='PENDIENTE_ACREDITACION').order_by('fecha_egreso', 'pk')
    elif pestania == 'historial':
        # Sólo lo resuelto: lo que espera al banco está en «Por acreditar».
        consulta = base.filter(estado_pago__in=RESUELTOS).order_by('-fecha_egreso', '-pk')
        busqueda = request.GET.get('q', '').strip()
        if busqueda:
            consulta = consulta.filter(Q(concepto__icontains=busqueda)
                                       | Q(destinatario__icontains=busqueda)
                                       | Q(id_proveedor__nombre_proveedor__icontains=busqueda))
    else:
        pestania = 'a_pagar'
        consulta = base.filter(estado_pago='PENDIENTE_PAGO').order_by('fecha_vencimiento', 'pk')
        contexto['compras_impagas'] = servicio.compras_por_pagar()[:50]
    contexto.update(paginar(request, consulta, TAMANIOS_TABLA, por_defecto=25))
    contexto.update({
        'pestania': pestania, 'filtro': request.GET,
        'vistas': [{'clave': clave, 'etiqueta': etiqueta, 'activa': clave == pestania, 'url': f'?ver={clave}',
                    'cantidad': cantidad} for (clave, etiqueta, _icono), cantidad in zip(PESTANIAS, (
                        base.filter(estado_pago='PENDIENTE_PAGO').count()
                        + servicio.compras_por_pagar().count(),
                        base.filter(estado_pago='PENDIENTE_ACREDITACION').count(),
                        base.filter(estado_pago__in=RESUELTOS).count()))],
        'vence_pronto': hoy + timedelta(days=7),
        'compra_elegida': servicio.compras_por_pagar().filter(pk=id_valido(request.GET.get('compra'))).first(),
        'dias_sin_confirmar': servicio.DIAS_SIN_CONFIRMAR,
        'vale': (MovimientoCaja.objects.filter(pk=id_valido(request.GET['vale']), id_usuario=request.usuario_actual,
                                               tipo_movimiento='GASTO').first()
                 if id_valido(request.GET.get('vale')) else None),
    })
    return render(request, 'compras/egresos.html', contexto)


def _proveedor_y_compra(request):
    compra = None
    if id_valido(request.POST.get('compra')):
        compra = Compra.objects.filter(pk=id_valido(request.POST['compra'])).first()
    proveedor = Proveedor.objects.filter(pk=id_valido(request.POST.get('proveedor'))).first()
    if compra is not None:
        proveedor = compra.id_proveedor
    return proveedor, compra


@require_POST
@requiere_alguno('registrar_egreso', 'registrar_gasto_caja')
def registrar(request):
    """Pagar ahora, anotar para después o gasto de caja: el mismo formulario."""
    usuario = request.usuario_actual
    modo = request.POST.get('modo')
    token = request.POST.get('token_operacion')
    permiso = 'registrar_gasto_caja' if modo == 'caja' else 'registrar_egreso'
    if not usuario.puede(permiso):
        registrar_acceso_denegado(request, permiso)
        messages.error(request, 'Tu perfil no puede registrar este tipo de egreso.')
        return redirect('compras:egresos')
    try:
        tipo = TipoEgreso.objects.filter(pk=id_valido(request.POST.get('tipo')), borrado_logico=False).first()
        if tipo is None:
            raise ValidationError('Elegí el tipo de egreso.')
        monto = leer_numero(request.POST.get('monto'), 2)
        if monto is None or monto <= 0:
            raise ValidationError('El monto tiene que ser mayor que cero.')
        concepto = request.POST.get('concepto', '')
        proveedor, compra = _proveedor_y_compra(request)
        destinatario = request.POST.get('destinatario')
        comprobante = request.POST.get('comprobante')
        if not idempotencia.consumir(request, token):
            messages.info(request, 'Ese egreso ya se había registrado.')
            return redirect('compras:egresos')
        if modo == 'anotar':
            egreso = servicio.registrar_obligacion(
                tipo, concepto, monto, _fecha(request.POST.get('vence')),
                destinatario=destinatario, proveedor=proveedor, compra=compra,
                comprobante=comprobante)
        else:
            medio = MedioPago.objects.filter(pk=id_valido(request.POST.get('medio')), borrado_logico=False).first()
            if modo == 'caja':
                medio = MedioPago.objects.filter(afecta_caja=True, borrado_logico=False).first()
                egreso = servicio.gasto_de_caja(
                    puesto.turno_de(request), usuario, tipo, monto, concepto, medio,
                    destinatario=destinatario, proveedor=proveedor, comprobante=comprobante)
            else:
                egreso = servicio.registrar_egreso(
                    tipo, medio, monto, concepto, comprobante=comprobante,
                    proveedor=proveedor, destinatario=destinatario, compra=compra,
                    turno=puesto.turno_de(request) if medio and medio.afecta_caja else None,
                    usuario=usuario)
    except ValidationError as error:
        idempotencia.devolver(request, token)
        messages.error(request, _texto(error))
        return redirect('compras:egresos')
    registrar_auditoria(request, 'ALTA', 'EGRESO', instancia=egreso,
                        nuevo=instantanea(egreso),
                        detalle=f'{egreso.get_estado_pago_display()}: {pesos(egreso.monto)} · {egreso.concepto}.')
    messages.success(request, {
        'anotar': 'Anotado: aparece en «A pagar» hasta que se pague.',
        'caja': f'Gasto registrado: salieron {pesos(egreso.monto)} del cajón.',
    }.get(modo, f'Pago registrado ({egreso.get_estado_pago_display().lower()}).'))
    return _con_vale(egreso)


def _con_vale(egreso, destino='compras:egresos'):
    """Si la plata salió del cajón, se vuelve con el vale para imprimir."""
    vale = egreso.movimientos_caja.values_list('pk', flat=True).first()
    return redirect(f"{reverse(destino)}?vale={vale}") if vale else redirect(destino)


@require_POST
@requiere_permiso('registrar_egreso', redirigir_a='compras:egresos')
def pagar(request, pk):
    egreso = get_object_or_404(Egreso, pk=pk)
    antes = instantanea(egreso)
    token = request.POST.get('token_operacion')
    try:
        medio = MedioPago.objects.filter(pk=id_valido(request.POST.get('medio')), borrado_logico=False).first()
        if not idempotencia.consumir(request, token):
            return redirect('compras:egresos')
        egreso = servicio.pagar_obligacion(
            egreso, medio, request.usuario_actual,
            turno=puesto.turno_de(request) if medio and medio.afecta_caja else None,
            comprobante=request.POST.get('comprobante'))
    except ValidationError as error:
        idempotencia.devolver(request, token)
        messages.error(request, _texto(error))
        return redirect('compras:egresos')
    registrar_auditoria(request, 'PAGO', 'EGRESO', instancia=egreso,
                        anterior=antes, nuevo=instantanea(egreso),
                        detalle=f'Pagó {pesos(egreso.monto)} con {egreso.id_medio_pago}: {egreso.concepto}.')
    messages.success(request, f'Pagado: {egreso.concepto}.')
    return _con_vale(egreso)


@require_POST
@requiere_permiso('conciliar_egresos', redirigir_a='compras:egresos')
def conciliar(request, pk):
    egreso = get_object_or_404(Egreso, pk=pk)
    antes = instantanea(egreso)
    acreditado = request.POST.get('resultado') == 'acreditado'
    try:
        egreso = servicio.conciliar(
            egreso, acreditado, _fecha(request.POST.get('fecha')) or timezone.localdate(),
            request.POST.get('referencia'))
    except ValidationError as error:
        messages.error(request, _texto(error))
        return redirect(f"{reverse('compras:egresos')}?ver=por_acreditar")
    registrar_auditoria(request, 'CONCILIACION', 'EGRESO', instancia=egreso,
                        anterior=antes, nuevo=instantanea(egreso),
                        detalle=f'{egreso.get_estado_pago_display()}: {pesos(egreso.monto)} · '
                                f'{egreso.referencia_bancaria or "sin referencia"}.')
    messages.success(request, f'Egreso {egreso.get_estado_pago_display().lower()}.'
                     + ('' if acreditado else ' Si hay que volver a pagar, se registra un pago nuevo.'))
    return redirect(f"{reverse('compras:egresos')}?ver=por_acreditar")


@requiere_alguno(*PERMISOS)
def exportar_egresos(request):
    consulta = (Egreso.objects.select_related('id_tipo_egreso', 'id_proveedor', 'id_medio_pago')
                .order_by('-fecha_egreso', '-pk'))
    columnas = [ColumnaExportable('N°', 'numero'), ColumnaExportable('Tipo'),
                ColumnaExportable('Concepto'), ColumnaExportable('A quién'),
                ColumnaExportable('Monto', 'dinero'), ColumnaExportable('Medio'),
                ColumnaExportable('Pagado el', 'fecha'), ColumnaExportable('Vence', 'fecha'),
                ColumnaExportable('Estado'), ColumnaExportable('Comprobante')]
    filas = ([e.pk, str(e.id_tipo_egreso), e.concepto,
              str(e.id_proveedor or e.destinatario or ''), e.monto,
              str(e.id_medio_pago or ''), e.fecha_egreso, e.fecha_vencimiento,
              e.get_estado_pago_display(), e.comprobante or '']
             for e in consulta.iterator(chunk_size=500))
    formato = request.GET.get('formato')
    respuesta = exportar(formato, 'Pagos y gastos', columnas, filas)
    registrar_auditoria(request, 'EXPORTACION', 'EGRESOS', modulo='COMPRAS',
                        detalle='Exportó los pagos y gastos.')
    return respuesta
