"""Los clientes del club desde la app: su QR real, sus puntos y sus movimientos.

    GET  /clientes/yo/          la ficha de la cuenta
    POST /clientes/vincular/    la vincula (con el DNI si hace falta) o la da de alta

Cómo se encuentra la ficha de una cuenta de la app (``api_movil.firebase``):

1. Por el ``uid`` de Firebase (``Cliente.uid_firebase``): una vez
   vinculada, el correo ya no importa.
2. Si no, por el correo VERIFICADO entre las fichas vigentes. Si hay una, se
   le guarda el uid. Si hay más de una, no se elige: se pide el DNI (409
   ``CORREO_REPETIDO``), y entre esas fichas tiene que haber exactamente una
   con ese DNI. Los DNI que no coinciden tienen freno (5 por hora).
3. Si no hay ninguna, ``vincular`` la da de alta con el MISMO formulario
   que la web (``ClienteForm``): entra por la categoría de ingreso y el
   sistema le genera el ``qr_cliente`` que lee la caja.

Nunca se vincula por teléfono ni por DNI solos: no están verificados, y
cualquiera podría quedarse con los puntos de otro. Para una ficha que se
cargó en la caja sin correo, el cajero le carga el correo con el cliente
presente (pantalla de Clientes) y la app la encuentra sola.
"""
import math
import re
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Min
from django.http import JsonResponse
from django.utils import dateformat, timezone
from django.views.decorators.csrf import csrf_exempt

from api_movil import catalogo, firebase, respuestas
from api_movil.models import IntentoDeVinculacion
from fidelizacion.forms import ClienteForm
from fidelizacion.models import Cliente
from nucleo.validaciones import normalizar_telefono
from seguridad.servicios import instantanea, registrar_auditoria

MOVIMIENTOS = 20
INTENTOS_CON_DNI = 5
VENTANA_DE_INTENTOS = timedelta(hours=1)

SIN_VINCULAR = 'Todavía no vinculaste tu cuenta con el club Cafeto.'
CORREO_REPETIDO = ('Hay más de una ficha de cliente con tu correo. Escribí tu DNI '
                   'para encontrar la tuya o acercate a la caja.')
DNI_NO_COINCIDE = ('Ese DNI no es el de ninguna de las fichas con tu correo. '
                   'Revisalo o acercate a la caja.')
DEMASIADOS_INTENTOS = ('Probaste demasiados DNI. Esperá un rato o acercate a la '
                       'caja para que te vinculen la ficha.')
TELEFONO_DE_OTRO_CLIENTE = ('Ese teléfono ya está en otra ficha del club. '
                            'Acercate a la caja para que la vinculen con tu cuenta.')
DNI_DE_OTRO_CLIENTE = ('Ese DNI ya está en otra ficha del club. '
                       'Acercate a la caja para que la vinculen con tu cuenta.')
FICHA_DADA_DE_BAJA = 'Tu ficha del club está dada de baja. Acercate a la caja.'
FICHA_DE_OTRA_CUENTA = ('Esa ficha del club ya está vinculada a otra cuenta de la '
                        'app. Acercate a la caja.')

# Los campos de la app y los del formulario de la web. El DNI sirve para dos
# cosas: elegir entre fichas con el mismo correo (``buscar_ficha``) y, en el
# alta, cumplir con «el teléfono o el DNI» (RN-7.1.2) sin dar el teléfono.
CAMPOS = {'apellido': 'apellido_cliente', 'nombre': 'nombre_cliente',
          'telefono': 'telefono_cliente', 'dni': 'dni_cliente',
          'fecha_nacimiento': 'fecha_nacimiento'}


# ---------------------------------------------------------------------------
#  La cuenta del cliente
# ---------------------------------------------------------------------------
def _miles(valor):
    """5000 -> '5.000'; 83.33 -> '83,33'."""
    texto = f'{Decimal(valor):,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    return texto[:-3] if texto.endswith(',00') else texto


def _concepto(movimiento):
    beneficio = movimiento.id_beneficio
    if movimiento.tipo_movimiento == 'ACUMULACION':
        return 'Compra en caja'
    if movimiento.tipo_movimiento == 'CANJE':
        return f'Canje: {beneficio}'
    if movimiento.tipo_movimiento == 'ANULACION':
        return f'Se devolvió el canje: {beneficio}' if beneficio else 'Devolución de una compra'
    if movimiento.tipo_movimiento == 'VENCIMIENTO':
        return 'Vencimiento de puntos'
    return 'Ajuste del local'


def cuenta(cliente):
    """Lo que la app muestra en «Mi QR» y «Mis puntos»."""
    todas = list(catalogo.categorias_vigentes())
    actual = cliente.id_categoria
    proxima = next((c for c in todas if c.puntos_minimos > actual.puntos_minimos), None)
    movimientos = (cliente.movimientos_puntos.select_related('id_beneficio')
                   .order_by('-fecha_movimiento', '-pk')[:MOVIMIENTOS])
    return {
        'qr': cliente.qr_cliente,
        'categoria': actual.nombre_categoria,
        'puntos_disponibles': cliente.puntos_disponibles,
        'puntos_acumulados': cliente.puntos_acumulados,
        'proxima_categoria': proxima.nombre_categoria if proxima else None,
        'puntos_proxima_categoria': proxima.puntos_minimos if proxima else None,
        # Todas las que existan (hoy también Platinum), para que la app no
        # dependa de una lista fija.
        'categorias': [{
            'nombre': c.nombre_categoria,
            'puntos_minimos': c.puntos_minimos,
            'desde': ('Desde que creás tu cuenta' if c.es_de_ingreso
                      else f'Desde {_miles(c.puntos_minimos)} puntos'),
            'tasa': f'1 pto c/ ${_miles(c.importe_por_punto)}',
        } for c in todas],
        'movimientos': [{
            'id': m.pk,
            'fecha': dateformat.format(timezone.localtime(m.fecha_movimiento), 'j b'),
            'concepto': _concepto(m),
            'puntos': m.puntos,
        } for m in movimientos],
    }


# ---------------------------------------------------------------------------
#  Cómo se encuentra (y se vincula) la ficha
# ---------------------------------------------------------------------------
def _vincular(request, ficha, de_la_app, como):
    """Le guarda a la ficha el uid de la cuenta y deja el asiento."""
    antes = instantanea(ficha)
    ficha.uid_firebase = de_la_app.uid
    ficha.save(update_fields=['uid_firebase'])
    registrar_auditoria(
        request, 'VINCULACION', 'CLIENTE', instancia=ficha, anterior=antes,
        nuevo=instantanea(ficha),
        detalle=f'Vinculó su cuenta de la app {como} ({de_la_app.correo}).')
    return ficha


def _la_del_dni(de_la_app, fichas, dni):
    """Entre las fichas que comparten el correo, la única con ese DNI."""
    recientes = IntentoDeVinculacion.objects.filter(
        uid_firebase=de_la_app.uid, fecha__gt=timezone.now() - VENTANA_DE_INTENTOS)
    if recientes.count() >= INTENTOS_CON_DNI:
        primero = recientes.aggregate(m=Min('fecha'))['m']
        raise firebase.CuentaRechazada(
            429, DEMASIADOS_INTENTOS, codigo='DEMASIADOS_INTENTOS',
            espera=primero + VENTANA_DE_INTENTOS - timezone.now())
    digitos = re.sub(r'\D', '', str(dni or ''))
    coinciden = [f for f in fichas if digitos and f.dni_cliente == digitos]
    if len(coinciden) != 1:
        IntentoDeVinculacion.objects.create(uid_firebase=de_la_app.uid)
        raise firebase.CuentaRechazada(409, DNI_NO_COINCIDE, codigo='DNI_NO_COINCIDE')
    return coinciden[0]


def buscar_ficha(request, de_la_app, dni=None):
    """La ficha vigente de la cuenta, o ``None`` si todavía no tiene.

    Levanta ``CuentaRechazada`` cuando no corresponde elegir una: correo
    repetido sin DNI, DNI que no coincide, ficha de baja o de otra cuenta.
    """
    vinculada = (Cliente.objects.select_related('id_categoria')
                 .filter(uid_firebase=de_la_app.uid).first())
    if vinculada is not None:
        if vinculada.borrado_logico:
            raise firebase.CuentaRechazada(404, FICHA_DADA_DE_BAJA,
                                           codigo='FICHA_DADA_DE_BAJA')
        return vinculada

    fichas = list(Cliente.objects.select_related('id_categoria')
                  .filter(borrado_logico=False, email_cliente__iexact=de_la_app.correo)
                  .order_by('pk'))
    if not fichas:
        return None
    if len(fichas) == 1:
        ficha, como = fichas[0], 'con su correo'
    elif dni is None:
        raise firebase.CuentaRechazada(409, CORREO_REPETIDO, codigo='CORREO_REPETIDO')
    else:
        ficha, como = _la_del_dni(de_la_app, fichas, dni), 'con su correo y su DNI'
    if ficha.uid_firebase:
        # Ya es de otra cuenta (la propia se buscó arriba).
        raise firebase.CuentaRechazada(409, FICHA_DE_OTRA_CUENTA,
                                       codigo='FICHA_DE_OTRA_CUENTA')
    return _vincular(request, ficha, de_la_app, como)


def _rechazo(rechazo):
    respuesta = respuestas.error(rechazo.detalle, rechazo.estado, codigo=rechazo.codigo)
    if rechazo.espera is not None:
        respuesta['Retry-After'] = str(max(1, math.ceil(rechazo.espera.total_seconds())))
    return respuesta


def _cuenta_del_pedido(request):
    """``(cuenta de la app, None)`` o ``(None, respuesta de error)``."""
    try:
        return firebase.cuenta_del_pedido(request), None
    except firebase.NoDisponible:
        return None, respuestas.error(firebase.NO_DISPONIBLE, 503,
                                      codigo=firebase.SIN_CONFIGURAR)
    except firebase.CuentaRechazada as rechazo:
        return None, _rechazo(rechazo)


def _es_de_otro_cliente(telefono):
    try:
        digitos = normalizar_telefono(str(telefono or ''))
    except ValidationError:     # uno que ni se entiende ya lo marca el formulario
        return False
    return bool(digitos) and Cliente.objects.filter(telefono_cliente=digitos).exists()


def _dni_de_otro_cliente(dni):
    digitos = re.sub(r'\D', '', str(dni or ''))
    return 7 <= len(digitos) <= 8 and Cliente.objects.filter(dni_cliente=digitos).exists()


# ---------------------------------------------------------------------------
#  Endpoints
# ---------------------------------------------------------------------------
@csrf_exempt
def yo(request):
    if request.method != 'GET':
        return respuestas.metodo_no_permitido(('GET',))
    de_la_app, error = _cuenta_del_pedido(request)
    if error:
        return error
    try:
        ficha = buscar_ficha(request, de_la_app)
    except firebase.CuentaRechazada as rechazo:
        return _rechazo(rechazo)
    if ficha is None:
        return respuestas.error(SIN_VINCULAR, 404)
    return JsonResponse(cuenta(ficha))


@csrf_exempt
def vincular(request):
    if request.method != 'POST':
        return respuestas.metodo_no_permitido(('POST',))
    de_la_app, error = _cuenta_del_pedido(request)
    if error:
        return error
    datos = respuestas.cuerpo(request)
    if datos is None:
        return respuestas.error(respuestas.DATOS_INVALIDOS, 400)
    try:
        ficha = buscar_ficha(request, de_la_app, dni=datos.get('dni'))
    except firebase.CuentaRechazada as rechazo:
        return _rechazo(rechazo)
    if ficha is not None:
        return JsonResponse(cuenta(ficha))

    formulario = ClienteForm({
        **{del_form: datos.get(campo) or '' for campo, del_form in CAMPOS.items()},
        # El correo es el VERIFICADO de la cuenta, nunca uno tipeado.
        'email_cliente': de_la_app.correo,
    })
    if not formulario.is_valid():
        de_la_app_campos = {del_form: campo for campo, del_form in CAMPOS.items()}
        errores = {de_la_app_campos.get(campo, campo): mensajes
                   for campo, mensajes in formulario.errors.items()
                   if campo != '__all__'}
        limpios = formulario.cleaned_data
        if _es_de_otro_cliente(datos.get('telefono')):
            cuerpo = {'codigo': 'TELEFONO_DE_OTRO_CLIENTE',
                      'detail': TELEFONO_DE_OTRO_CLIENTE}
        elif _dni_de_otro_cliente(datos.get('dni')):
            cuerpo = {'codigo': 'DNI_DE_OTRO_CLIENTE', 'detail': DNI_DE_OTRO_CLIENTE}
        else:
            cuerpo = {'detail': ' '.join(formulario.non_field_errors())
                      or 'Revisá los datos marcados.'}
            # Sólo falta con qué identificarlo en la caja: la app lo pide ahí
            # mismo (teléfono o DNI) en lugar de mostrar el texto del formulario.
            if not errores and not (limpios.get('telefono_cliente')
                                    or limpios.get('dni_cliente')):
                cuerpo['codigo'] = 'FALTA_TELEFONO_O_DNI'
        if errores:
            cuerpo['errores'] = errores
        return JsonResponse(cuerpo, status=400)
    cliente = formulario.save(commit=False)
    cliente.uid_firebase = de_la_app.uid
    try:
        with transaction.atomic():
            cliente.save()
    except IntegrityError:
        # La misma cuenta se dio de alta recién en otro pedido: es esa.
        ficha = buscar_ficha(request, de_la_app)
        if ficha is None:
            raise
        return JsonResponse(cuenta(ficha))
    registrar_auditoria(
        request, 'ALTA', 'CLIENTE', instancia=cliente, nuevo=instantanea(cliente),
        detalle=f'Se sumó al club desde la app, con su cuenta verificada ({de_la_app.correo}).')
    return JsonResponse(cuenta(Cliente.objects.select_related('id_categoria')
                               .get(pk=cliente.pk)), status=201)
