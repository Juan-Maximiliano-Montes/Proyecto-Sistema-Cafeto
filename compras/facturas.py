"""Facturas que llegan solas: la bandeja y la factura de muestra (DD-98).

Una factura que n8n recibe por WhatsApp o por correo queda en
FACTURAS_RECIBIDAS, PENDIENTE, hasta que una persona la abre en «Recibir
mercadería», la controla contra el papel y la confirma (pasa a CONFIRMADA y
apunta a la compra) o la descarta. Una misma factura no espera dos veces:
la huella resume proveedor, tipo, punto de venta, número y renglones.
"""
import hashlib
import json
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from compras import precarga
from compras.models import EstadoFacturaRecibida, FacturaRecibida, OrigenFactura, Proveedor
from nucleo.templatetags.cafeto import pesos


def huella(datos):
    canon = {k: datos.get(k) for k in ('cuit_proveedor', 'nombre_proveedor', 'tipo_comprobante',
                                       'punto_venta', 'numero')}
    canon['r'] = [[str(r.get('codigo_barras') or ''), str(r.get('descripcion') or ''),
                   str(r.get('cantidad') or ''), str(r.get('costo_unitario') or '')]
                  for r in datos.get('renglones') or [] if isinstance(r, dict)]
    return hashlib.sha256(json.dumps(canon, sort_keys=True, default=str).encode()).hexdigest()


def _resumen(borrador):
    proveedor = Proveedor.objects.filter(pk=borrador['p']).first() if borrador.get('p') else None
    quien = str(proveedor) if proveedor else (borrador.get('n') or 'Proveedor sin reconocer')
    numero = (f" {borrador['v'].zfill(4)}-{borrador['u'].zfill(8)}"
              if borrador.get('v') and borrador.get('u') else '')
    cantidad = len(borrador['r']) + len(borrador.get('x') or [])
    return f'{quien}{numero} · {cantidad} renglón(es)'[:200]


def recibir(datos, origen=None, remitente=None):
    """Deja la factura leída en la bandeja. ``(factura, resumen, nueva)``."""
    borrador, resumen = precarga.reconocer(datos)
    origen = str(origen or '').upper()
    if origen not in OrigenFactura.values:
        origen = OrigenFactura.OTRO
    marca = huella(datos)
    existente = FacturaRecibida.objects.filter(
        huella_factura=marca, estado_factura=EstadoFacturaRecibida.PENDIENTE).first()
    if existente is not None:
        return existente, resumen, False
    try:
        with transaction.atomic():
            factura = FacturaRecibida.objects.create(
                origen_factura=origen, remitente=(str(remitente or '').strip()[:80] or None),
                resumen_factura=_resumen(borrador), datos_factura=borrador, huella_factura=marca)
    except IntegrityError:
        # Llegó dos veces al mismo tiempo: la otra ya quedó esperando.
        return (FacturaRecibida.objects.get(huella_factura=marca, estado_factura=EstadoFacturaRecibida.PENDIENTE),
                resumen, False)
    return factura, resumen, True


def pendientes():
    return FacturaRecibida.objects.filter(estado_factura=EstadoFacturaRecibida.PENDIENTE).order_by('fecha_recepcion')


def para_revisar(pk):
    """La factura pendiente, bloqueada para resolverla, o ValidationError."""
    factura = FacturaRecibida.objects.select_for_update().filter(pk=pk).first()
    if factura is None:
        raise ValidationError('Esa factura recibida no existe.')
    if not factura.pendiente:
        raise ValidationError(f'Esa factura ya está {factura.get_estado_factura_display().lower()}.')
    return factura


def confirmar(factura, compra, usuario):
    factura.estado_factura = EstadoFacturaRecibida.CONFIRMADA
    factura.id_compra = compra
    factura.id_usuario_resolucion = usuario
    factura.fecha_resolucion = timezone.now()
    factura.save(update_fields=['estado_factura', 'id_compra', 'id_usuario_resolucion', 'fecha_resolucion'])
    return factura


@transaction.atomic
def descartar(pk, usuario):
    factura = para_revisar(pk)
    factura.estado_factura = EstadoFacturaRecibida.DESCARTADA
    factura.id_usuario_resolucion = usuario
    factura.fecha_resolucion = timezone.now()
    factura.save(update_fields=['estado_factura', 'id_usuario_resolucion', 'fecha_resolucion'])
    return factura


def aviso(cantidad):
    if not cantidad:
        return ''
    return (f'Tenés {cantidad} factura(s) recibidas por WhatsApp/Correo listas para revisar y confirmar.'
            if cantidad != 1 else
            'Tenés 1 factura recibida por WhatsApp/Correo lista para revisar y confirmar.')


# ---------------------------------------------------------------------------
#  La factura de muestra (para mostrar el lector en un clic)
# ---------------------------------------------------------------------------
PROVEEDOR_DE_MUESTRA = ('Distribuidora de Muestra S.R.L.', '30-71000000-6')
PRODUCTO_DE_MUESTRA = ('Galletitas de muestra 300 g', '7790000000017', '980,00')


def factura_de_muestra():
    """El texto de una factura A armada con lo que hay cargado.

    Usa un proveedor de mercadería real (para que se reconozca por su CUIT)
    y hasta tres productos con código de barras; suma un producto que no
    existe, para mostrar el alta en un clic. El número sale de la hora, así
    no choca con una muestra anterior."""
    from stock.models import TIPOS_CON_PROVEEDOR, Producto

    proveedor = (Proveedor.objects.filter(borrado_logico=False, cuit_proveedor__isnull=False)
                 .exclude(tipo_proveedor='SERVICIOS').order_by('pk'))
    proveedor = next((p for p in proveedor if not p.es_el_general), None)
    nombre, cuit = (proveedor.nombre_proveedor, proveedor.cuit_proveedor) if proveedor else PROVEEDOR_DE_MUESTRA
    productos = list(Producto.objects.filter(borrado_logico=False, tipo_producto__in=TIPOS_CON_PROVEEDOR,
                                             codigo_barras__regex=r'^[0-9]{8,14}$').order_by('pk')[:3])
    hoy = timezone.localdate()
    ahora = timezone.localtime()
    numero = int(ahora.strftime('%y%j')) * 100 + ahora.minute
    lineas = [
        'FACTURA A                                     Cód. 01',
        f'Razón Social: {nombre}',
        f'CUIT: {cuit}',
        f'Punto de Venta: 0003   Comp. Nro: {numero:08d}',
        f'Fecha de Emisión: {hoy:%d/%m/%Y}',
        f'Fecha de Vto. para el pago: {hoy + timedelta(days=15):%d/%m/%Y}',
        'Código         Producto / Servicio                 Cantidad   Precio Unit.   Subtotal',
    ]
    for i, producto in enumerate(productos):
        cantidad = 6 * (i + 1)
        costo = producto.precio_costo if producto.precio_costo > 0 else producto.precio_venta * 6 / 10
        precio = pesos(costo).replace('$', '').replace('\xa0', '').strip()
        subtotal = pesos(costo * cantidad).replace('$', '').replace('\xa0', '').strip()
        lineas.append(f'{producto.codigo_barras}  {str(producto)[:34]:<34}  {cantidad:>6},00   {precio:>12}   {subtotal:>12}')
        if producto.controla_vencimiento:
            lineas.append(f'               Lote: L{hoy:%y%m}{i + 1} Vto: {hoy + timedelta(days=180):%d/%m/%Y}')
    nombre_nuevo, codigo_nuevo, costo_nuevo = PRODUCTO_DE_MUESTRA
    lineas.append(f'{codigo_nuevo}  {nombre_nuevo:<34}  {12:>6},00   {costo_nuevo:>12}   {"11.760,00":>12}')
    lineas.append(f'               Lote: L{hoy:%y%m}9 Vto: {hoy + timedelta(days=120):%d/%m/%Y}')
    lineas += ['', 'Subtotal neto gravado ... IVA 21 % ... Importe total',
               'CAE N°: 74123456789012   Fecha de Vto. de CAE: ' + f'{hoy + timedelta(days=10):%d/%m/%Y}']
    return '\n'.join(lineas)
