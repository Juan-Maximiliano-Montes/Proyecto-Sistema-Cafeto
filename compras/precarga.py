"""Una compra pre-cargada por una automatización (DD-88).

Una foto de la factura, leída por un servicio de IA, llega como JSON. Acá
NO se registra nada: se reconocen el proveedor y los productos y se arma un
enlace a la pantalla de recepción con todo ya escrito. La compra existe
recién cuando una persona la mira y la confirma: lo que lee una IA de una
foto se equivoca en un número cada tanto, y un número mal en una compra es
stock y costo mal.

El borrador viaja DENTRO del enlace, firmado con la clave del sistema: no
hay tabla nueva, no se puede alterar y vence solo.
"""
from decimal import Decimal, InvalidOperation

from django.core import signing
from django.core.exceptions import ValidationError

from compras.models import Proveedor, TipoComprobante
from nucleo.validaciones import normalizar_cuit, sanear_codigo
from stock.models import TIPOS_CON_PROVEEDOR, Producto

SAL = 'cafeto.precarga-de-compra'
VIGENCIA = 7 * 24 * 3600
MAXIMO_DE_RENGLONES = 80


def _numero(valor, decimales):
    try:
        numero = Decimal(str(valor).replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return numero.quantize(Decimal(1).scaleb(-decimales)) if numero.is_finite() else None


def armar(datos):
    """El JSON de la factura -> (testigo firmado, resumen de lo reconocido)."""
    if not isinstance(datos, dict) or not isinstance(datos.get('renglones'), list):
        raise ValidationError('El cuerpo tiene que traer "renglones" (una lista).')
    if not datos['renglones'] or len(datos['renglones']) > MAXIMO_DE_RENGLONES:
        raise ValidationError(f'Van entre 1 y {MAXIMO_DE_RENGLONES} renglones.')

    proveedor = None
    try:
        cuit = normalizar_cuit(datos.get('cuit_proveedor') or '')
        proveedor = Proveedor.objects.filter(cuit_proveedor=cuit, borrado_logico=False).first()
    except (ValidationError, ValueError, TypeError):
        cuit = None
    if proveedor is not None and not proveedor.vende_mercaderia:
        proveedor = None

    tipo = str(datos.get('tipo_comprobante') or '').upper().replace(' ', '_')
    renglones, sin_reconocer, vistos = [], [], set()
    for crudo in datos['renglones']:
        if not isinstance(crudo, dict):
            continue
        codigo = sanear_codigo(str(crudo.get('codigo_barras') or ''))
        cantidad, costo = _numero(crudo.get('cantidad'), 3), _numero(crudo.get('costo_unitario'), 2)
        producto = (Producto.objects.filter(
            codigo_barras=codigo, borrado_logico=False, tipo_producto__in=TIPOS_CON_PROVEEDOR).first()
            if codigo and codigo.isdigit() else None)
        if (producto is None or producto.pk in vistos or cantidad is None or cantidad <= 0
                or costo is None or costo < 0):
            sin_reconocer.append(' · '.join(str(p) for p in (
                crudo.get('descripcion'), codigo, crudo.get('cantidad')) if p)[:80])
            continue
        vistos.add(producto.pk)
        renglones.append([producto.pk, str(cantidad.normalize()), str(costo),
                          ' '.join(str(crudo.get('marca_detectada') or '').split())[:50]])

    borrador = {
        'p': proveedor.pk if proveedor else None,
        'n': str(datos.get('nombre_proveedor') or '')[:50], 'c': cuit or '',
        't': tipo if tipo in TipoComprobante.values else '',
        'v': str(datos.get('punto_venta') or '')[:5], 'u': str(datos.get('numero') or '')[:8],
        'r': renglones, 's': sin_reconocer[:MAXIMO_DE_RENGLONES],
    }
    return signing.dumps(borrador, salt=SAL, compress=True), {
        'proveedor_reconocido': bool(proveedor),
        'renglones_reconocidos': len(renglones), 'renglones_sin_reconocer': sin_reconocer}


def leer(testigo):
    """El borrador de un enlace, o None si está alterado o vencido."""
    try:
        return signing.loads(testigo, salt=SAL, max_age=VIGENCIA)
    except signing.BadSignature:
        return None


def para_la_pantalla(borrador):
    """(datos del encabezado, renglones) como los espera la recepción."""
    productos = Producto.objects.in_bulk([r[0] for r in borrador['r']])
    renglones = [{
        'producto': pk, 'nombre': str(productos[pk]),
        'controla': productos[pk].controla_vencimiento,
        'fraccionable': not productos[pk].exige_cantidad_entera,
        'cantidad': cantidad, 'costo': costo, 'lotes': [],
    } for pk, cantidad, costo, *_ in borrador['r'] if pk in productos]
    datos = {'proveedor': str(borrador['p'] or ''), 'alta_proveedor': '' if borrador['p'] else '1', 'tipo_comprobante': borrador['t'],
             'punto_venta': borrador['v'], 'numero': borrador['u'], 'vincular': '1'}
    return datos, renglones


def proveedor_detectado(borrador):
    """El proveedor que leyó la automatización y todavía no existe, listo
    para validar (sin guardar). None si no hay datos suficientes."""
    from compras.models import TipoProveedor

    if borrador.get('p') or not (borrador.get('n') and borrador.get('c')):
        return None
    return Proveedor(nombre_proveedor=borrador['n'], cuit_proveedor=borrador['c'],
                     tipo_proveedor=TipoProveedor.MERCADERIA)


def marcas_detectadas(borrador):
    """{id de producto: marca leída} de los renglones que la traen."""
    return {r[0]: r[3] for r in borrador['r'] if len(r) > 3 and r[3]}
