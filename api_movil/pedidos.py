"""Lo que llega en el cuerpo de un pedido del equipo, ya controlado.

Hacen lo que hacen los formularios de la web antes de llamar al servicio:
leer el id, el número o la fecha, y avisar en castellano si no se entiende.
Cada función levanta ``ValidationError`` con el texto para mostrar; la
vista lo devuelve como 400 con ``detail``. Las reglas de negocio (cuánto se
puede sacar, qué lote, qué motivo) las sigue diciendo el servicio.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from stock import servicios
from stock.models import Lote, Producto
from nucleo.validaciones import id_valido

MAXIMO_DE_RENGLONES = 120


def entero(valor):
    """Un id: ``7`` o ``"7"``. ``None`` si no es un entero positivo."""
    if isinstance(valor, (int, str)):
        return id_valido(valor)
    return None


def numero(valor, etiqueta, decimales=3):
    """Un número de JSON como ``Decimal``, sin redondear nada.

    Más decimales que los que guarda la base es un error, no un redondeo:
    lo mismo que ``leer_numero`` en la web.
    """
    if valor is None or valor == '' or isinstance(valor, bool):
        raise ValidationError(f'Falta {etiqueta}.')
    try:
        resultado = Decimal(str(valor).strip())
    except InvalidOperation:
        raise ValidationError(f'{etiqueta[:1].upper()}{etiqueta[1:]} no es un número.')
    if not resultado.is_finite():
        raise ValidationError(f'{etiqueta[:1].upper()}{etiqueta[1:]} no es un número.')
    if resultado.as_tuple().exponent < -decimales:
        raise ValidationError(
            f'{etiqueta[:1].upper()}{etiqueta[1:]} admite hasta {decimales} decimales.')
    return resultado


def fecha(valor, etiqueta, obligatoria=False):
    """Una fecha ``AAAA-MM-DD``; ``None`` si no vino y no es obligatoria."""
    if valor in (None, ''):
        if obligatoria:
            raise ValidationError(f'Indicá {etiqueta}.')
        return None
    try:
        return date.fromisoformat(str(valor))
    except ValueError:
        raise ValidationError(f'{etiqueta[:1].upper()}{etiqueta[1:]} no es una fecha válida.')


def texto(valor):
    """Un texto opcional; vacío es ``None``."""
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ValidationError('Los textos van entre comillas.')
    return valor.strip() or None


def producto(valor):
    """El producto vigente con ese id."""
    pk = entero(valor)
    elegido = (Producto.objects.select_related('id_rubro', 'id_marca')
               .filter(pk=pk, borrado_logico=False).first() if pk else None)
    if elegido is None:
        raise ValidationError('Uno de los productos ya no existe o está dado de baja.')
    return elegido


def lote_de(producto_, valor):
    """El lote elegido; si no se eligió y el producto va por lote, el que
    vence primero (FEFO), que es el que se está usando: como en la web."""
    if valor in (None, ''):
        if producto_.controla_vencimiento:
            return servicios.lotes_fefo(producto_).first()
        return None
    pk = entero(valor)
    lote = Lote.objects.filter(pk=pk, id_producto=producto_).first() if pk else None
    if lote is None:
        raise ValidationError(f'Ese lote no es de "{producto_}".')
    return lote


def renglones(datos):
    """La lista de renglones del pedido, cada uno un objeto."""
    crudos = datos.get('renglones')
    if not isinstance(crudos, list) or not crudos:
        raise ValidationError('Cargá al menos un producto.')
    if len(crudos) > MAXIMO_DE_RENGLONES:
        raise ValidationError('Son demasiados renglones para un solo pedido.')
    if not all(isinstance(crudo, dict) for crudo in crudos):
        raise ValidationError('Los renglones llegaron mal armados.')
    return crudos
