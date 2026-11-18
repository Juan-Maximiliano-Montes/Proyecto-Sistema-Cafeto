"""Una compra pre-cargada por una automatización (DD-88).

Una foto de la factura, leída por un servicio de IA, llega como JSON. Acá
NO se registra nada: se reconocen el proveedor y los productos y se arma un
enlace a la pantalla de recepción con todo ya escrito. La compra existe
recién cuando una persona la mira y la confirma: lo que lee una IA de una
foto se equivoca en un número cada tanto, y un número mal en una compra es
stock y costo mal.

El borrador viaja DENTRO del enlace, firmado con la clave del sistema, y
vence solo. Si la factura llegó por WhatsApp o correo, además queda en la
bandeja de FACTURAS_RECIBIDAS hasta que alguien la confirme o la descarte
(DD-98).
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


def _fecha_iso(texto):
    from datetime import date
    try:
        return date.fromisoformat(str(texto or '')[:10]).isoformat()
    except ValueError:
        return ''


def _producto_por_nombre(descripcion):
    """Un producto que se llama exactamente así (sin acentos ni puntuación)."""
    from nucleo.validaciones import clave_normalizada

    clave = clave_normalizada(descripcion or '')
    if not clave:
        return None
    candidatos = [p for p in Producto.objects.filter(borrado_logico=False, tipo_producto__in=TIPOS_CON_PROVEEDOR)
                  .filter(nombre_producto__icontains=clave.split(' ')[0])[:50]
                  if clave_normalizada(p.nombre_producto) == clave
                  or clave_normalizada(str(p)) == clave]
    return candidatos[0] if len(candidatos) == 1 else None


def _proveedor_por_nombre(nombre):
    from nucleo.validaciones import clave_normalizada

    clave = clave_normalizada(nombre or '')
    if not clave:
        return None
    for proveedor in Proveedor.objects.filter(borrado_logico=False)[:500]:
        if clave_normalizada(proveedor.nombre_proveedor) == clave:
            return proveedor
    return None


def reconocer(datos):
    """El JSON de la factura -> (borrador, resumen de lo reconocido).

    Reconoce al proveedor por CUIT (o, si no lo trae, por el nombre exacto)
    y cada producto por su código de barras (o por el nombre exacto). Lo
    que no se reconoce queda aparte, con sus datos, para darlo de alta.
    """
    if not isinstance(datos, dict) or not isinstance(datos.get('renglones'), list):
        raise ValidationError('El cuerpo tiene que traer "renglones" (una lista).')
    if not datos['renglones'] or len(datos['renglones']) > MAXIMO_DE_RENGLONES:
        raise ValidationError(f'Van entre 1 y {MAXIMO_DE_RENGLONES} renglones.')
    if not all(isinstance(renglon, dict) for renglon in datos['renglones']):
        raise ValidationError('Cada renglón tiene que ser un objeto ({"descripcion", "cantidad", '
                              '"costo_unitario", …}).')

    proveedor = None
    try:
        cuit = normalizar_cuit(datos.get('cuit_proveedor') or '')
        proveedor = Proveedor.objects.filter(cuit_proveedor=cuit, borrado_logico=False).first()
    except (ValidationError, ValueError, TypeError):
        cuit = None
    if proveedor is None and not cuit:
        proveedor = _proveedor_por_nombre(datos.get('nombre_proveedor'))
    if proveedor is not None and not proveedor.vende_mercaderia:
        proveedor = None

    tipo = str(datos.get('tipo_comprobante') or '').upper().replace(' ', '_')
    renglones, sin_reconocer, detalle, vistos = [], [], [], set()
    for crudo in datos['renglones']:
        if not isinstance(crudo, dict):
            continue
        codigo = sanear_codigo(str(crudo.get('codigo_barras') or ''))
        cantidad, costo = _numero(crudo.get('cantidad'), 3), _numero(crudo.get('costo_unitario'), 2)
        producto = (Producto.objects.filter(
            codigo_barras=codigo, borrado_logico=False, tipo_producto__in=TIPOS_CON_PROVEEDOR).first()
            if codigo and codigo.isdigit() else None)
        if producto is None and not codigo:
            producto = _producto_por_nombre(crudo.get('descripcion'))
        lotes = [{'vence': _fecha_iso(l.get('vence') or l.get('vencimiento')),
                  'numero': str(l.get('numero') or l.get('lote') or '')[:30]}
                 for l in (crudo.get('lotes') or []) if isinstance(l, dict)]
        if crudo.get('vencimiento'):
            lotes.append({'vence': _fecha_iso(crudo['vencimiento']), 'numero': str(crudo.get('lote') or '')[:30]})
        lotes = [l for l in lotes if l['vence']]
        valido = cantidad is not None and cantidad > 0 and costo is not None and costo >= 0
        if producto is None or producto.pk in vistos or not valido:
            sin_reconocer.append(' · '.join(str(p) for p in (
                crudo.get('descripcion'), codigo, crudo.get('cantidad')) if p)[:80])
            if valido and (producto is None or producto.pk not in vistos):
                detalle.append({'descripcion': ' '.join(str(crudo.get('descripcion') or '').split())[:100],
                                'codigo': codigo if codigo and codigo.isdigit() else '',
                                'cantidad': format(cantidad.normalize(), 'f'), 'costo': str(costo), 'lotes': lotes})
            continue
        vistos.add(producto.pk)
        renglones.append([producto.pk, format(cantidad.normalize(), 'f'), str(costo),
                          ' '.join(str(crudo.get('marca_detectada') or '').split())[:50], lotes])

    borrador = {
        'p': proveedor.pk if proveedor else None,
        'n': str(datos.get('nombre_proveedor') or '')[:50], 'c': cuit or '',
        't': tipo if tipo in TipoComprobante.values else '',
        'v': str(datos.get('punto_venta') or '')[:5], 'u': str(datos.get('numero') or '')[:8],
        'f': _fecha_iso(datos.get('fecha')), 'w': _fecha_iso(datos.get('vence')),
        'r': renglones, 's': sin_reconocer[:MAXIMO_DE_RENGLONES], 'x': detalle[:MAXIMO_DE_RENGLONES],
    }
    return borrador, {
        'proveedor_reconocido': bool(proveedor),
        'renglones_reconocidos': len(renglones), 'renglones_sin_reconocer': sin_reconocer}


def firmar(borrador):
    return signing.dumps(borrador, salt=SAL, compress=True)


def armar(datos):
    """El JSON de la factura -> (testigo firmado, resumen de lo reconocido)."""
    borrador, resumen = reconocer(datos)
    return firmar(borrador), resumen


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
        'producto': r[0], 'nombre': str(productos[r[0]]),
        'controla': productos[r[0]].controla_vencimiento,
        'fraccionable': not productos[r[0]].exige_cantidad_entera,
        'unidad': 'u.' if productos[r[0]].exige_cantidad_entera else productos[r[0]].unidad_medida,
        'cantidad': r[1], 'costo': r[2],
        'lotes': ([{'vence': l['vence'], 'cantidad': '', 'numero': l.get('numero', '')} for l in r[4]]
                  if len(r) > 4 and productos[r[0]].controla_vencimiento else []),
    } for r in borrador['r'] if r[0] in productos]
    datos = {'proveedor': str(borrador['p'] or ''), 'alta_proveedor': '' if borrador['p'] else '1',
             'tipo_comprobante': borrador['t'], 'punto_venta': borrador['v'], 'numero': borrador['u'],
             'vincular': '1'}
    if borrador.get('f'):
        datos['fecha'] = borrador['f']
    if borrador.get('w'):
        datos['vence_pago'] = borrador['w']
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
