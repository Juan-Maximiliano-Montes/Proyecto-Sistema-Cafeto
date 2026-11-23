"""La bandeja de mejora continua del catálogo (Etapa 2).

El sistema anterior dejó productos en «Sin Clasificar», elaborados sin
receta, costos en cero y stock que nunca se contó. Sanear eso de un saque
es parar el local una semana; no sanearlo es convivir para siempre con
informes que mienten.

La salida es progresiva: una automatización mira el catálogo todas las
noches y PROPONE —«esto parece de Lácteos», «esto se vendió 14 veces sin
receta»—, y una persona aprueba o descarta de a poco, cuando tiene cinco
minutos. Lo que la automatización NO hace es cambiar el catálogo: la
política de ``api_servicio`` no cambia (DD-84), y todo criterio de negocio
sigue pasando por una persona.

Cuando se APLICA una sugerencia, el cambio pasa por el mismo camino que si
lo hiciera alguien a mano: ``full_clean()`` y ``save()`` del modelo, con
sus reglas y sus mensajes. Nada se escribe por un costado.
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from nucleo.textos import pluralizar, segun
from nucleo.validaciones import clave_normalizada
from stock.models import (
    ORIGEN_AUTOMATIZACION,
    EstadoSugerencia,
    Producto,
    Rubro,
    SugerenciaCatalogo,
    TipoSugerencia,
)

CERO = Decimal('0')
LARGO_MOTIVO = 200


# ---------------------------------------------------------------------------
#  Huella: la misma propuesta no entra dos veces
# ---------------------------------------------------------------------------
def huella(producto_id, tipo, propuesta):
    """SHA-256 canónico de (producto, tipo, propuesta).

    Canónico quiere decir que el orden de las claves del JSON no cambia la
    huella: n8n puede armar el cuerpo como quiera y la repetida se sigue
    reconociendo.
    """
    canon = json.dumps({'p': int(producto_id), 't': str(tipo),
                        'd': propuesta if isinstance(propuesta, dict) else {}},
                       sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


# ---------------------------------------------------------------------------
#  Forma mínima de cada propuesta
# ---------------------------------------------------------------------------
def _texto(propuesta, clave, etiqueta, largo):
    valor = ' '.join(str(propuesta.get(clave) or '').split())[:largo]
    if not valor:
        raise ValidationError(f'{etiqueta} no puede venir vacío.')
    return valor


def _precio(propuesta):
    try:
        precio = Decimal(str(propuesta.get('precio_venta'))).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError('El precio propuesto ("precio_venta") tiene que ser un número.')
    if precio <= CERO:
        raise ValidationError('El precio propuesto tiene que ser mayor que cero.')
    return precio


def validar_propuesta(tipo, propuesta):
    """Devuelve la propuesta ya limpia, o ``ValidationError``.

    Es la primera puerta: una propuesta sin forma no llega a la bandeja, y
    nadie termina aprobando un precio en cero porque n8n mandó un ``null``.
    """
    if not isinstance(propuesta, dict):
        raise ValidationError('La propuesta tiene que ser un objeto ({…}).')
    if tipo == TipoSugerencia.RUBRO:
        return {'rubro': _texto(propuesta, 'rubro', 'El rubro propuesto', 40)}
    if tipo == TipoSugerencia.MARCA:
        return {'marca': _texto(propuesta, 'marca', 'La marca propuesta', 50)}
    if tipo == TipoSugerencia.PRECIO:
        return {'precio_venta': str(_precio(propuesta))}
    if tipo == TipoSugerencia.RECETA:
        insumos = propuesta.get('insumos') or []
        if not isinstance(insumos, list):
            raise ValidationError('Los insumos propuestos ("insumos") tienen que ser una lista.')
        return {'insumos': [' '.join(str(i).split())[:100] for i in insumos if str(i).strip()][:20]}
    if tipo == TipoSugerencia.BAJA_INACTIVO:
        dias = propuesta.get('dias_sin_movimiento')
        limpia = {}
        if dias is not None:
            try:
                limpia['dias_sin_movimiento'] = max(0, int(dias))
            except (TypeError, ValueError):
                raise ValidationError('Los días sin movimiento tienen que ser un número entero.')
        return limpia
    raise ValidationError('Ese tipo de sugerencia no existe.')


# ---------------------------------------------------------------------------
#  Proponer
# ---------------------------------------------------------------------------
def proponer(producto, tipo, motivo, propuesta, origen=ORIGEN_AUTOMATIZACION):
    """Deja la sugerencia en la bandeja. ``(sugerencia, nueva)``.

    Idempotente: si la misma propuesta ya está esperando, la devuelve con
    ``nueva=False`` en lugar de dejar una segunda. Así el flujo nocturno
    puede correr todas las noches sin que la bandeja crezca sola.
    """
    tipo = str(tipo or '').strip().upper()
    if tipo not in TipoSugerencia.values:
        raise ValidationError('Ese tipo de sugerencia no existe.')
    if producto is None or producto.borrado_logico:
        raise ValidationError('El producto no existe o está dado de baja.')
    limpia = validar_propuesta(tipo, propuesta)
    texto = ' '.join(str(motivo or '').split())[:LARGO_MOTIVO]
    if not texto:
        raise ValidationError('La sugerencia tiene que decir por qué se propone.')
    marca = huella(producto.pk, tipo, limpia)
    existente = SugerenciaCatalogo.objects.filter(
        huella=marca, estado_sugerencia=EstadoSugerencia.PENDIENTE).first()
    if existente is not None:
        return existente, False
    try:
        with transaction.atomic():
            sugerencia = SugerenciaCatalogo(
                tipo_sugerencia=tipo, id_producto=producto,
                origen=(str(origen or ORIGEN_AUTOMATIZACION).strip()[:20]
                        or ORIGEN_AUTOMATIZACION),
                motivo=texto, propuesta=limpia, huella=marca)
            sugerencia.full_clean(exclude=['id_usuario_resolucion'])
            sugerencia.save()
    except IntegrityError:
        # Llegó dos veces al mismo tiempo: la otra ya quedó esperando.
        return (SugerenciaCatalogo.objects.get(
            huella=marca, estado_sugerencia=EstadoSugerencia.PENDIENTE), False)
    return sugerencia, True


def pendientes(tipo=None):
    """Las sugerencias que esperan a una persona, de productos vigentes."""
    consulta = (SugerenciaCatalogo.objects
                .filter(estado_sugerencia=EstadoSugerencia.PENDIENTE,
                        id_producto__borrado_logico=False)
                .select_related('id_producto', 'id_producto__id_rubro',
                                'id_producto__id_marca')
                .order_by('tipo_sugerencia', 'fecha_creacion'))
    if tipo:
        consulta = consulta.filter(tipo_sugerencia=tipo)
    return consulta


def aviso(cantidad):
    if not cantidad:
        return ''
    return (f"{pluralizar(cantidad, 'sugerencia')} de mejora del catálogo "
            f"{segun(cantidad, 'espera', 'esperan')} tu visto bueno.")


# ---------------------------------------------------------------------------
#  Aplicar y descartar (siempre con una persona atrás)
# ---------------------------------------------------------------------------
def _para_resolver(pk):
    """La sugerencia pendiente, bloqueada para resolverla, o ValidationError."""
    sugerencia = (SugerenciaCatalogo.objects.select_for_update()
                  .filter(pk=pk).first())
    if sugerencia is None:
        raise ValidationError('Esa sugerencia no existe.')
    if not sugerencia.pendiente:
        raise ValidationError(
            f'Esa sugerencia ya está {sugerencia.get_estado_sugerencia_display().lower()}.')
    return sugerencia


def _aplicar_rubro(producto, propuesta):
    nombre = str(propuesta.get('rubro') or '')
    clave = clave_normalizada(nombre)
    rubro = next((r for r in Rubro.objects.filter(borrado_logico=False)
                  if clave_normalizada(r.nombre_rubro) == clave), None)
    if rubro is None:
        # No se crea un rubro desde acá: inventar rubros sin que nadie los
        # piense es justamente cómo se llegó a «Otros Rubros».
        raise ValidationError(
            f'No hay un rubro vigente llamado "{nombre}". Crealo primero en '
            'Rubros y después aplicá la sugerencia.')
    anterior = producto.id_rubro
    producto.id_rubro = rubro
    producto.full_clean()
    producto.save()
    return f'Pasó de "{anterior}" a "{rubro}".'


def _aplicar_marca(producto, propuesta):
    from stock.reservados import asignar_marca

    puesta, detalle = asignar_marca(producto, propuesta.get('marca'))
    if not puesta:
        raise ValidationError(f'No se le puso la marca: {detalle}')
    return f'Quedó con la marca "{detalle}".'


def _aplicar_precio(producto, propuesta):
    anterior = producto.precio_venta
    producto.precio_venta = _precio(propuesta)
    producto.full_clean()
    producto.save()
    return (f'El precio pasó de {anterior} a {producto.precio_venta}.')


def _aplicar_baja(producto):
    motivos = producto.motivos_que_impiden_la_baja()
    if motivos:
        raise ValidationError(
            f'No se puede dar de baja "{producto}": {"; ".join(motivos)}.')
    # Mismo camino que la baja del CRUD (``nucleo.crud.dar_de_baja``): se
    # escribe sólo la marca de baja. Nunca un DELETE (RN-8.1.1).
    producto.borrado_logico = True
    producto.save(update_fields=['borrado_logico'])
    return 'Quedó dado de baja (baja lógica: su historia sigue entera).'


def _aplicar_receta(producto):
    """La receta la carga una persona: acá sólo se da por hecha.

    Las cantidades exactas de un café con leche no las sabe ninguna
    automatización. La pantalla ofrece el botón «Ir a cargar la receta»;
    esta sugerencia se cierra cuando la receta ya está.
    """
    renglones = producto.composicion.count()
    if not renglones:
        raise ValidationError(
            f'"{producto}" todavía no tiene receta cargada. Cargala desde '
            'Productos (con sus cantidades) y después aplicá la sugerencia.')
    return f"Ya tiene {pluralizar(renglones, 'insumo')} en la receta."


@transaction.atomic
def aceptar(pk, usuario):
    """Aplica la sugerencia de verdad. ``(sugerencia, qué pasó)``.

    Todo pasa por ``full_clean()`` y ``save()``: si el cambio rompe una
    regla, levanta ``ValidationError`` y la sugerencia sigue pendiente. No
    hay forma de que aprobar deje la base en un estado que el sistema no
    hubiera aceptado escribiendo a mano.
    """
    sugerencia = _para_resolver(pk)
    producto = (Producto.objects.select_for_update()
                .select_related('id_rubro', 'id_marca')
                .get(pk=sugerencia.id_producto_id))
    if producto.borrado_logico and sugerencia.tipo_sugerencia != TipoSugerencia.BAJA_INACTIVO:
        raise ValidationError(f'"{producto}" está dado de baja: no se le aplica nada.')
    propuesta = sugerencia.propuesta if isinstance(sugerencia.propuesta, dict) else {}
    if sugerencia.tipo_sugerencia == TipoSugerencia.RUBRO:
        resultado = _aplicar_rubro(producto, propuesta)
    elif sugerencia.tipo_sugerencia == TipoSugerencia.MARCA:
        resultado = _aplicar_marca(producto, propuesta)
    elif sugerencia.tipo_sugerencia == TipoSugerencia.PRECIO:
        resultado = _aplicar_precio(producto, propuesta)
    elif sugerencia.tipo_sugerencia == TipoSugerencia.BAJA_INACTIVO:
        resultado = _aplicar_baja(producto)
    else:
        resultado = _aplicar_receta(producto)
    sugerencia.estado_sugerencia = EstadoSugerencia.ACEPTADA
    sugerencia.id_usuario_resolucion = usuario
    sugerencia.fecha_resolucion = timezone.now()
    sugerencia.save(update_fields=['estado_sugerencia', 'id_usuario_resolucion',
                                   'fecha_resolucion'])
    sugerencia.id_producto = producto
    return sugerencia, resultado


@transaction.atomic
def descartar(pk, usuario):
    """La persona dice que no: la sugerencia se cierra y no vuelve."""
    sugerencia = _para_resolver(pk)
    sugerencia.estado_sugerencia = EstadoSugerencia.DESCARTADA
    sugerencia.id_usuario_resolucion = usuario
    sugerencia.fecha_resolucion = timezone.now()
    sugerencia.save(update_fields=['estado_sugerencia', 'id_usuario_resolucion',
                                   'fecha_resolucion'])
    return sugerencia
