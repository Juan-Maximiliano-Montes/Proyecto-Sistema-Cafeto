"""Cuentas corrientes (fiado): quién debe, cobrar y decidir (DD-91)."""
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import CharField, F, Func, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from fidelizacion import credito
from fidelizacion.models import Cliente, EstadoCredito, MovimientoCuenta
from nucleo import idempotencia
from nucleo.formularios import leer_numero, sobran_decimales
from nucleo.templatetags.cafeto import pesos
from nucleo.validaciones import clave_normalizada
from seguridad.decorators import requiere_permiso
from seguridad.servicios import registrar_auditoria
from ventas import puesto
from ventas.models import MedioPago


def _numero(texto, etiqueta):
    """Como se escribe en Argentina: '50.000' son cincuenta mil, no
    cincuenta (el mismo lector que el mostrador)."""
    texto = (texto or '').replace('\u00a0', '')
    valor = leer_numero(texto, 2)
    if valor is None:
        if sobran_decimales(texto, 2):
            raise ValidationError(f'{etiqueta} admite hasta 2 decimales.')
        raise ValidationError(f'{etiqueta} no es un número.')
    return valor


def _puntos(cantidad):
    cantidad = str(cantidad).strip()
    return f'{cantidad} punto' if cantidad == '1' else f'{cantidad} puntos'


def _foto(cliente, s=None):
    s = s or credito.situacion(cliente)
    return {'id_cliente': cliente.pk, 'estado_credito': cliente.estado_credito,
            'limite_credito': str(cliente.limite_credito), 'nota_credito': cliente.nota_credito,
            'debe': str(s['saldo']), 'puntos_disponibles': cliente.puntos_disponibles,
            'plazo_credito_dias': cliente.plazo_credito_dias}


def _estados(cliente):
    """Las opciones de «¿Le fiás?». El estado actual siempre está y viene
    elegido: si es INCOBRABLE (que no se elige a mano) aparece tal cual, y
    guardar el tope o la nota no lo rehabilita sin querer."""
    opciones = [(EstadoCredito.HABILITADO, 'Puede llevar fiado'), (EstadoCredito.SANCIONADO, 'Suspendido'),
                (EstadoCredito.SIN_CREDITO, 'Sin cuenta corriente')]
    if cliente.estado_credito == EstadoCredito.INCOBRABLE:
        opciones.insert(0, (EstadoCredito.INCOBRABLE, 'Incobrable — no se le fía'))
    return opciones


@requiere_permiso('cobrar_credito', redirigir_a='home')
def cuentas(request):
    """Las cuentas corrientes, en cuatro vistas con su contador (con deuda,
    vencidos o suspendidos, incobrables, todos): ninguna queda escondida
    detrás del buscador."""
    hoy = timezone.localdate()
    todas = credito.listado(hoy)
    ver = request.GET.get('ver') or {'vencidos': 'mora', 'incobrables': 'incobrables',
                                     'todos': 'todos'}.get(request.GET.get('estado', ''))
    if ver not in dict(credito.VISTAS):
        ver = 'deuda'
    filas = [c for c in todas if ver in c.vistas]
    if ver == 'incobrables':
        filas.sort(key=lambda c: (c.perdido_el is None, -(c.perdido_el.timestamp() if c.perdido_el else 0)))
    busqueda = request.GET.get('q', '').strip()[:60]
    hallados = []
    if busqueda:
        hallado = Cliente.identificar(busqueda)
        if hallado is not None:
            hallados = [hallado]
        else:
            consulta = Cliente.objects.filter(borrado_logico=False)
            for parte in busqueda.split()[:3]:
                consulta = consulta.filter(Q(apellido_cliente__icontains=parte) | Q(nombre_cliente__icontains=parte))
            hallados = list(consulta.order_by('apellido_cliente', 'nombre_cliente')[:12])
    vistas = [{'clave': clave, 'etiqueta': etiqueta, 'activa': clave == ver,
               'cantidad': sum(1 for c in todas if clave in c.vistas),
               'url': '?ver=' + clave} for clave, etiqueta in credito.VISTAS]
    return render(request, 'fidelizacion/cuentas.html', {
        'filas': filas, 'busqueda': busqueda, 'hallados': hallados, 'ver': ver, 'vistas': vistas,
        'total': sum((c.debe for c in todas), Decimal('0')),
        'vencido': sum((c.vencido for c in todas), Decimal('0')),
        'en_mora': sum(1 for c in todas if 'mora' in c.vistas),
        'perdido': sum((c.perdido for c in todas), Decimal('0')),
        'incobrables': sum(1 for c in todas if 'incobrables' in c.vistas),
    })


@requiere_permiso('cobrar_credito', redirigir_a='home')
def cuenta(request, pk):
    """La cuenta de un cliente: lo que debe, su historia, y lo que se puede
    hacer (cobrar; y, quien gestiona el fiado, decidir)."""
    cliente = get_object_or_404(Cliente, pk=pk)
    usuario = request.usuario_actual
    gestiona = credito.puede_gestionar(usuario)
    turno = puesto.turno_de(request)
    hoy = timezone.localdate()
    if request.method == 'POST':
        token = request.POST.get('token_operacion')
        accion = request.POST.get('accion')
        antes = _foto(cliente)
        recibo = None
        try:
            if not idempotencia.consumir(request, token):
                raise ValidationError('Eso ya se había registrado: no se repitió.')
            if accion == 'cobrar':
                medio = MedioPago.objects.filter(pk=request.POST.get('medio') or 0, borrado_logico=False).first()
                puntos = request.POST.get('puntos')
                resultado = credito.cobrar(
                    cliente, _numero(request.POST.get('monto'), 'El importe'), medio, usuario, turno=turno,
                    interes_porcentaje=_numero(request.POST.get('interes') or '0', 'El recargo'),
                    acreditar_puntos=(puntos == '1') if request.POST.get('puntos_decidido') else None)
                detalle = (f'Cobró {pesos(resultado["cobrado"])} de la cuenta corriente de {cliente} con {medio}'
                           + (f', más {pesos(resultado["interes"])} de recargo' if resultado['interes'] else '')
                           + f'. Queda debiendo {pesos(resultado["queda"])}.'
                           + (f' Se acreditaron {_puntos(resultado["puntos"])}.' if resultado['puntos'] else ''))
                operacion = 'COBRO'
                recibo = resultado.get('recibo')
                aviso = (f'Listo: cobraste {pesos(resultado["total"])}.'
                         + (f' Queda debiendo {pesos(resultado["queda"])}.' if resultado['queda']
                            else ' Quedó al día.'))
            elif accion == 'estado':
                credito.cambiar_estado(
                    cliente, request.POST.get('estado'), usuario,
                    limite=_numero(request.POST.get('limite') or '0', 'El tope'),
                    nota=request.POST.get('nota', ''),
                    plazo=request.POST.get('plazo') or None)
                estado_antes = cliente.get_estado_credito_display()
                cliente.refresh_from_db()
                cambio = (f' Pasó de «{estado_antes}» a «{cliente.get_estado_credito_display()}».'
                          if cliente.estado_credito != antes['estado_credito'] else '')
                detalle, operacion = f'Cambió la cuenta corriente de {cliente}.{cambio}', 'MODIFICACION'
                aviso = 'Guardado.' + (f' Ahora: {cliente.get_estado_credito_display().lower()}.' if cambio else '')
            elif accion == 'quitar_puntos':
                cantidad = request.POST.get('cantidad')
                credito.quitar_puntos(cliente, cantidad, usuario)
                detalle, operacion = f'Le quitó {_puntos(cantidad)} del Club a {cliente} por mora.', 'MODIFICACION'
                aviso = f'Se le {"quitó" if str(cantidad).strip() == "1" else "quitaron"} {_puntos(cantidad)}.'
            elif accion == 'incobrable':
                perdido = credito.declarar_incobrable(cliente, request.POST.get('motivo'), usuario)
                detalle = (f'Dio por perdida la deuda de {cliente}: {pesos(perdido)}. '
                           f'Motivo: {request.POST.get("motivo", "").strip()[:120]}')
                operacion, aviso = 'BAJA', f'La deuda de {pesos(perdido)} quedó como pérdida.'
            else:
                raise ValidationError('No se entendió qué querías hacer.')
        except ValidationError as error:
            idempotencia.devolver(request, token)
            messages.error(request, ' '.join(error.messages))
        else:
            cliente.refresh_from_db()
            registrar_auditoria(request, operacion, 'CUENTA CORRIENTE', instancia=cliente, modulo='FIDELIZACION',
                                anterior=antes, nuevo=_foto(cliente),
                                detalle=detalle + (f' Recibo N° {recibo}.' if accion == 'cobrar' and recibo else ''))
            messages.success(request, aviso)
            if accion == 'cobrar' and recibo:
                return redirect(f"{reverse('fidelizacion:cuenta', args=[cliente.pk])}?recibo={recibo}")
        return redirect('fidelizacion:cuenta', pk=cliente.pk)

    s = credito.situacion(cliente, hoy)
    movimientos = list(MovimientoCuenta.objects.filter(id_cliente=cliente)
                       .select_related('id_medio_pago', 'id_usuario').order_by('-id_movimiento_cuenta')[:61])
    # El recibo va en el primer COBRO de cada operación (el más viejo). Se
    # trae uno de más para saber si el último de la lista empieza un cobro.
    for i, m in enumerate(movimientos):
        previo = movimientos[i + 1] if i + 1 < len(movimientos) else None
        m.es_recibo = m.tipo_movimiento == credito.COBRO and not credito.mismo_cobro(previo, m)
    movimientos = movimientos[:60]
    recibo_nuevo = request.GET.get('recibo', '')
    pendientes = list(credito.pendientes(cliente).select_related('id_venta'))
    for fiado in pendientes:
        fiado.atraso = (hoy - fiado.fecha_vencimiento).days if fiado.fecha_vencimiento < hoy else 0
    return render(request, 'fidelizacion/cuenta.html', {
        'cliente': cliente, 's': s, 'pendientes': pendientes, 'gestiona': gestiona, 'turno': turno,
        'aviso': credito.aviso_de_mora(cliente, hoy),
        'movimientos': movimientos,
        'recibo_nuevo': (MovimientoCuenta.objects.filter(pk=recibo_nuevo, id_cliente=cliente,
                                                          tipo_movimiento=credito.COBRO).first()
                         if recibo_nuevo.isdigit() else None),
        'medios': [m for m in MedioPago.objects.filter(borrado_logico=False).order_by('-afecta_caja', 'nombre_medio_pago')
                   if not m.es_cuenta_corriente],
        'puntos_por_defecto': not any(f.atraso for f in pendientes),
        'estados': _estados(cliente),
        'token': idempotencia.emitir(request),
    })


@require_GET
@requiere_permiso('vender', redirigir_a='home')
def morosos(request):
    """El "radar" del mostrador: quiénes están en mora, para reconocerlos."""
    filas = credito.deudores(solo_en_mora=True)[:40]
    return JsonResponse({'morosos': [{
        'id': c.pk, 'nombre': f'{c.apellido_cliente}, {c.nombre_cliente}', 'nota': c.nota_credito or '',
        'debe': str(c.debe), 'vencido': str(c.vencido), 'dias': c.dias_de_atraso,
        'suspendido': c.estado_credito == EstadoCredito.SANCIONADO} for c in filas]})


@require_GET
@requiere_permiso('vender', redirigir_a='home')
def parecidos(request):
    """Antes de dar de alta un cliente: ¿no será uno que ya está (y debe)?

    Mira el DNI, el teléfono y el nombre, incluso entre los dados de baja.
    """
    dni = ''.join(c for c in request.GET.get('dni', '') if c.isdigit())[:8]
    telefono = ''.join(c for c in request.GET.get('telefono', '') if c.isdigit())[:15]
    apellido = clave_normalizada(request.GET.get('apellido', ''))
    nombre = clave_normalizada(request.GET.get('nombre', ''))
    hallados = {}
    if len(dni) >= 7:
        for c in Cliente.objects.filter(dni_cliente=dni):
            hallados[c.pk] = (c, 'el mismo DNI')
    if len(telefono) >= 8:
        for c in Cliente.objects.filter(telefono_cliente__endswith=telefono[-8:]):
            hallados.setdefault(c.pk, (c, 'el mismo teléfono'))
    if len(apellido) >= 3 and len(nombre) >= 3:
        # Sin acentos ni mayúsculas, igual que los índices únicos de la base:
        # "gomez" encuentra a "Gómez".
        mismos = (Cliente.objects
                  .annotate(clave_apellido=Func(F('apellido_cliente'), function='f_clave_normalizada',
                                                output_field=CharField()))
                  .filter(clave_apellido=apellido)[:40])
        for c in mismos:
            if clave_normalizada(c.nombre_cliente).split(' ')[0] == nombre.split(' ')[0]:
                hallados.setdefault(c.pk, (c, 'el mismo nombre'))
    salida = []
    for cliente, por in list(hallados.values())[:5]:
        s = credito.situacion(cliente)
        salida.append({'id': cliente.pk, 'nombre': f'{cliente.nombre_cliente} {cliente.apellido_cliente}',
                       'por': por, 'de_baja': cliente.borrado_logico, 'debe': str(s['saldo']),
                       'aviso': credito.aviso_de_mora(cliente), 'nota': s['nota']})
    return JsonResponse({'parecidos': salida})


@requiere_permiso('cobrar_credito', redirigir_a='home')
def recibo(request, pk):
    """Recibo de pago de cuenta corriente, para imprimir y darle al cliente."""
    cobro = get_object_or_404(MovimientoCuenta.objects.select_related(
        'id_cliente', 'id_usuario', 'id_medio_pago', 'id_turno_caja__id_caja_registradora'),
        pk=pk, tipo_movimiento=credito.COBRO)
    primero = credito.ancla(cobro)
    if primero.pk != cobro.pk:
        return redirect('fidelizacion:recibo', pk=primero.pk)
    from negocio.models import ParametrosNegocio
    return render(request, 'fidelizacion/recibo.html', {
        'cobro': cobro, 'cliente': cobro.id_cliente, 'r': credito.recibo(cobro),
        'negocio': ParametrosNegocio.vigentes(), 'impreso': timezone.now(),
    })
