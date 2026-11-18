"""Leer una factura de proveedor sin servicios pagos (DD-98).

Dos caminos, en este orden:

1. **La automatización** (``CAFETO_N8N_WEBHOOK_FACTURA``). Si está
   configurada, el archivo se le manda a n8n, que puede usar el lector que
   quiera (un OCR, un modelo de IA) y contesta con la factura ya leída.
2. **El lector local**, que no depende de nada de afuera y es el que se usa
   si n8n no está o no contesta:

   * un texto (``text/*``) se lee tal cual;
   * un PDF: se sacan los textos de sus páginas (los flujos comprimidos con
     ``FlateDecode``, que es como los arma casi cualquier programa) y, si
     trae el código QR de ARCA como enlace, sus datos (CUIT, tipo, punto
     de venta, número y fecha), que son los oficiales;
   * una foto: sólo si el equipo tiene instalado el OCR libre Tesseract
     (``tesseract-ocr-spa``). Si no está, se avisa: leer letras de una
     imagen sin un lector de texto no se puede.

Lo leído es siempre una PROPUESTA: la compra se registra recién cuando una
persona la controla contra el papel y la confirma.
"""
import base64
import json
import logging
import re
import shutil
import subprocess
import zlib
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError

log = logging.getLogger(__name__)

MAXIMO_BYTES = 8 * 1024 * 1024
SEGUNDOS_PARA_N8N = 25

# Tipos de comprobante de ARCA (tabla de comprobantes de factura electrónica).
TIPOS_ARCA = {1: 'FACTURA_A', 6: 'FACTURA_B', 11: 'FACTURA_C', 51: 'FACTURA_A'}


# ---------------------------------------------------------------------------
#  Del archivo al texto
# ---------------------------------------------------------------------------
def texto_de(contenido, tipo, nombre=''):
    """``(texto, qr)`` del archivo. ``qr`` es el dict del QR de ARCA o None."""
    if not contenido:
        raise ValidationError('El archivo llegó vacío.')
    if len(contenido) > MAXIMO_BYTES:
        raise ValidationError('El archivo pesa más de 8 MB: sacale una foto más liviana o mandá el PDF.')
    tipo = (tipo or '').lower()
    nombre = (nombre or '').lower()
    if tipo == 'application/pdf' or nombre.endswith('.pdf') or contenido[:5] == b'%PDF-':
        texto, crudo = _texto_de_pdf(contenido)
        return texto, qr_de_arca(crudo) or qr_de_arca(texto)
    if tipo.startswith('text/') or nombre.endswith(('.txt', '.csv')):
        texto = _decodificar(contenido)
        return texto, qr_de_arca(texto)
    if tipo.startswith('image/') or nombre.endswith(('.jpg', '.jpeg', '.png', '.webp', '.heic')):
        texto = _ocr(contenido)
        return texto, qr_de_arca(texto)
    raise ValidationError('Ese archivo no es una foto, un PDF ni un texto.')


def _decodificar(contenido):
    for codificacion in ('utf-8', 'latin-1'):
        try:
            return contenido.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return contenido.decode('utf-8', 'ignore')


def _ocr(contenido):
    binario = shutil.which('tesseract')
    if binario is None:
        raise ValidationError(
            'En este equipo no hay un lector de texto para fotos (OCR). Subí el PDF de la factura, '
            'mandala por WhatsApp a la automatización o cargala a mano.')
    try:
        idiomas = subprocess.run([binario, '--list-langs'], capture_output=True, timeout=10, check=False)
        idioma = 'spa' if b'\nspa' in idiomas.stdout else 'eng'      # el español, si está instalado
        salida = subprocess.run([binario, 'stdin', 'stdout', '-l', idioma, '--psm', '6'],
                                input=contenido, capture_output=True, timeout=40, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValidationError('El lector de texto no pudo con la foto. Probá con otra más nítida o con el PDF.')
    texto = _decodificar(salida.stdout)
    if not texto.strip():
        raise ValidationError('No se pudo leer texto en la foto. Probá con otra más nítida, de frente y con luz.')
    return texto


_STREAM = re.compile(rb'stream\r?\n(.*?)\r?\nendstream', re.S)
_CADENA = re.compile(rb'\((?:\\.|[^\\)])*\)')


MAXIMO_DESCOMPRIMIDO = 16 * 1024 * 1024
DEMASIADO_GRANDE = ('El PDF tiene un contenido comprimido demasiado grande para ser una factura. '
                    'Pedile al proveedor el PDF original o cargá la compra a mano.')


def _descomprimir(flujo, disponible):
    """Un flujo comprimido del PDF, sin dejar que se infle más de lo
    ``disponible`` (un PDF armado para estallar al descomprimirse)."""
    descompresor = zlib.decompressobj()
    datos = descompresor.decompress(flujo, disponible)
    if descompresor.unconsumed_tail:
        raise ValidationError(DEMASIADO_GRANDE)
    return datos


def _texto_de_pdf(contenido):
    """El texto visible de un PDF común y el contenido crudo (para el QR)."""
    partes, crudo = [], [contenido]
    disponible = MAXIMO_DESCOMPRIMIDO
    for flujo in _STREAM.findall(contenido):
        try:
            datos = _descomprimir(flujo, disponible)
        except zlib.error:
            datos = flujo
        disponible -= len(datos)
        if disponible <= 0:
            raise ValidationError(DEMASIADO_GRANDE)
        crudo.append(datos)
        if b'BT' not in datos:
            continue
        for bloque in re.findall(rb'BT(.*?)ET', datos, re.S):
            linea = []
            for token in re.finditer(rb'(\[(?:.|\n)*?\]\s*TJ|\((?:\\.|[^\\)])*\)\s*Tj|T\*|Td|TD|\')', bloque):
                pedazo = token.group(0)
                if pedazo in (b'T*', b'Td', b'TD', b"'"):
                    if linea:
                        partes.append(''.join(linea))
                        linea = []
                    continue
                for cadena in _CADENA.findall(pedazo):
                    linea.append(_cadena_pdf(cadena[1:-1]))
            if linea:
                partes.append(''.join(linea))
    texto = '\n'.join(p for p in partes if p.strip())
    if not texto.strip():
        raise ValidationError(
            'Ese PDF no trae texto (es una foto escaneada). Subí la foto directamente o cargala a mano.')
    return texto, b'\n'.join(crudo).decode('latin-1', 'ignore')


def _cadena_pdf(bruto):
    escapes = {b'n': b'\n', b'r': b'', b't': b' ', b'(': b'(', b')': b')', b'\\': b'\\'}
    salida, i = bytearray(), 0
    while i < len(bruto):
        c = bruto[i:i + 1]
        if c == b'\\' and i + 1 < len(bruto):
            siguiente = bruto[i + 1:i + 2]
            octal = re.match(rb'[0-7]{1,3}', bruto[i + 1:i + 4])
            if octal:
                salida.append(int(octal.group(0), 8) & 0xFF)
                i += 1 + len(octal.group(0))
                continue
            salida += escapes.get(siguiente, siguiente)
            i += 2
            continue
        salida += c
        i += 1
    return bytes(salida).decode('latin-1')


def qr_de_arca(texto):
    """Los datos del QR de una factura electrónica (RG 4291 de ARCA), si el
    archivo trae su enlace: https://www.afip.gob.ar/fe/qr/?p=<base64>."""
    if not texto:
        return None
    hallado = re.search(r'(?:afip|arca)\.gob\.ar/fe/qr/?\?p=([A-Za-z0-9+/=_-]{20,})', texto)
    if not hallado:
        return None
    crudo = hallado.group(1)
    try:
        datos = json.loads(base64.urlsafe_b64decode(crudo + '=' * (-len(crudo) % 4)).decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        try:
            datos = json.loads(base64.b64decode(crudo + '=' * (-len(crudo) % 4)).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None
    return datos if isinstance(datos, dict) else None


# ---------------------------------------------------------------------------
#  Del texto a la factura
# ---------------------------------------------------------------------------
_NUMERO = r'\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:[.,]\d+)?'
_FECHA = r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})'
_RENGLON = re.compile(
    r'^\s*(?:(?P<codigo>\d{8,14})\s+)?(?P<descripcion>.*?[A-Za-zÁÉÍÓÚÑáéíóúñ].*?)\s+'
    r'(?P<cantidad>' + _NUMERO + r')\s*(?:x\s*)?(?:u\.?\s*|un\.?\s*|kg\s*)?\$?\s*'
    r'(?P<precio>\d{1,3}(?:\.\d{3})*,\d{2}|\d+\.\d{2})'
    r'(?:\s+(?:\d{1,3}(?:[.,]\d+)?\s*%\s+)?\$?\s*(?:\d{1,3}(?:\.\d{3})*,\d{2}|\d+\.\d{2}))?\s*$')
_LOTE = re.compile(r'lote\s*(?:n[°º.]?\s*)?:?\s*(?P<lote>[A-Za-z0-9-]{1,30})?.*?'
                   r'(?:vto|vence|venc\w*)\.?\s*:?\s*' + _FECHA, re.I)
_NO_ES_RENGLON = re.compile(r'\b(subtotal|total|iva|percep|neto|importe|saldo|cae|bonif)\w*\b', re.I)


def _importe(texto):
    if texto is None:
        return None
    texto = texto.strip().replace('$', '').replace(' ', '')
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    return texto


def _fecha(d, m, a):
    anio = int(a) + (2000 if len(a) == 2 else 0)
    try:
        return date(anio, int(m), int(d)).isoformat()
    except ValueError:
        return None


def interpretar(texto, qr=None):
    """Lo que dice la factura, en el formato de ``compras.precarga``."""
    datos = {'renglones': []}
    lineas = [l.rstrip() for l in (texto or '').splitlines()]
    plano = '\n'.join(lineas)

    cuits = re.findall(r'C\.?\s*U\.?\s*I\.?\s*T\.?\s*(?:N[°º.]?)?\s*:?\s*(\d{2}-?\d{8}-?\d)', plano, re.I)
    if cuits:
        datos['cuit_proveedor'] = cuits[0]
    razon = re.search(r'(?:raz[oó]n\s+social|proveedor|vendedor)\s*:\s*(.+)', plano, re.I)
    if razon:
        datos['nombre_proveedor'] = razon.group(1).strip()[:50]
    letra = re.search(r'\bfactura\s*(?:tipo\s*)?["“]?([ABC])\b', plano, re.I) or re.search(
        r'^\s*([ABC])\s*$', plano, re.M)
    if letra:
        datos['tipo_comprobante'] = 'FACTURA_' + letra.group(1).upper()
    elif re.search(r'\bremito\b', plano, re.I):
        datos['tipo_comprobante'] = 'REMITO'
    numero = re.search(r'\b(\d{4,5})\s*-\s*(\d{8})\b', plano)
    punto = re.search(r'punto\s+de\s+venta\s*:?\s*(\d{1,5})', plano, re.I)
    nro = re.search(r'comp(?:robante)?\.?\s*n(?:ro|[°º])\.?\s*:?\s*(\d{1,8})\b', plano, re.I)
    if punto and nro:
        datos['punto_venta'], datos['numero'] = punto.group(1), nro.group(1)
    elif numero:
        datos['punto_venta'], datos['numero'] = numero.group(1), numero.group(2)
    emision = re.search(r'fecha(?:\s+de)?\s+emisi[oó]n\s*:?\s*' + _FECHA, plano, re.I) or re.search(
        r'\bfecha\s*:?\s*' + _FECHA, plano, re.I)
    if emision:
        datos['fecha'] = _fecha(*emision.groups()[-3:])
    vence = re.search(r'(?:vto\.?|vencimiento)(?:\s+(?:para\s+el\s+)?pago)?\s*:?\s*' + _FECHA, plano, re.I)
    if vence:
        datos['vence'] = _fecha(*vence.groups()[-3:])

    ultimo = None
    for linea in lineas:
        lote = _LOTE.search(linea)
        if lote and ultimo is not None:
            ultimo.setdefault('lotes', []).append(
                {'numero': (lote.group('lote') or '').strip()[:30], 'vence': _fecha(*lote.groups()[-3:])})
            continue
        if _NO_ES_RENGLON.search(linea) and not re.match(r'^\s*\d{8,14}\s', linea):
            continue
        hallado = _RENGLON.match(linea)
        if not hallado:
            continue
        descripcion = ' '.join(hallado.group('descripcion').split())
        if len(descripcion) < 3:
            continue
        ultimo = {'codigo_barras': hallado.group('codigo') or '', 'descripcion': descripcion[:80],
                  'cantidad': _importe(hallado.group('cantidad')),
                  'costo_unitario': _importe(hallado.group('precio'))}
        datos['renglones'].append(ultimo)

    if qr:
        # Lo del QR es lo oficial: manda sobre lo leído del texto.
        if qr.get('cuit'):
            datos['cuit_proveedor'] = str(qr['cuit'])
        if isinstance(qr.get('tipoCmp'), (int, str)) and TIPOS_ARCA.get(qr['tipoCmp']):
            datos['tipo_comprobante'] = TIPOS_ARCA[qr['tipoCmp']]
        if qr.get('ptoVta'):
            datos['punto_venta'] = str(qr['ptoVta'])
        if qr.get('nroCmp'):
            datos['numero'] = str(qr['nroCmp'])
        if isinstance(qr.get('fecha'), str) and re.match(r'\d{4}-\d{2}-\d{2}$', qr['fecha']):
            datos['fecha'] = qr['fecha']
    return datos


# ---------------------------------------------------------------------------
#  n8n
# ---------------------------------------------------------------------------
def por_n8n(contenido, tipo, nombre):
    """La factura leída por la automatización, o None si no está o no contestó."""
    url = getattr(settings, 'CAFETO_N8N_WEBHOOK_FACTURA', '')
    if not url:
        return None
    import urllib.request

    cuerpo = json.dumps({'nombre': nombre, 'tipo': tipo,
                         'archivo': base64.b64encode(contenido).decode('ascii')}).encode()
    pedido = urllib.request.Request(url, data=cuerpo, method='POST', headers={
        'Content-Type': 'application/json', 'X-Cafeto-Token': getattr(settings, 'CAFETO_N8N_TOKEN', '')})
    try:
        with urllib.request.urlopen(pedido, timeout=SEGUNDOS_PARA_N8N) as respuesta:
            datos = json.loads(respuesta.read(2 * 1024 * 1024).decode('utf-8'))
    except Exception:                                       # noqa: BLE001
        log.warning('n8n no leyó la factura %s: se usa el lector local', nombre)
        return None
    return datos if isinstance(datos, dict) and isinstance(datos.get('renglones'), list) else None


def leer(contenido, tipo, nombre):
    """``(datos, origen)``: la factura en el formato de ``compras.precarga``."""
    datos = por_n8n(contenido, tipo, nombre)
    if datos is not None:
        return datos, 'n8n'
    texto, qr = texto_de(contenido, tipo, nombre)
    return interpretar(texto, qr), 'local'
