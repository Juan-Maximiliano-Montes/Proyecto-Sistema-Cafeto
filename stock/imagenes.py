"""La foto de un producto (DD-83).

La base guarda sólo DÓNDE está la foto (``Producto.imagen``), nunca el
binario:

  * una dirección ``https://…`` — la pone una automatización (n8n busca la
    foto del envase por el código de barras) o se pega a mano;
  * ``productos/<archivo>.webp`` — una foto subida desde el sistema, con la
    cámara de la tablet o del celular o desde un archivo. Se guarda en
    ``MEDIA_ROOT`` ya achicada: una foto de celular pesa varios megas y en
    una tarjeta del mostrador se ve a 200 píxeles.

Todo lo que toca la foto pasa por acá: la pantalla, la app y la API de
servicio usan estas tres funciones.
"""
import secrets
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

LADO_MAXIMO = 800                       # píxeles del lado más largo
CALIDAD = 80
PESO_MAXIMO = 10 * 1024 * 1024          # lo que se acepta subir (una foto de celular)
CARPETA = 'productos'
LARGO_MAXIMO_URL = 300


def url_de(producto):
    """La dirección para un <img>, o None si el producto no tiene foto."""
    imagen = producto.imagen
    if not imagen:
        return None
    if imagen.startswith('https://'):
        return imagen
    return settings.MEDIA_URL + imagen


def _borrar_archivo(imagen):
    if imagen and imagen.startswith(CARPETA + '/'):
        ruta = Path(settings.MEDIA_ROOT) / imagen
        try:
            ruta.unlink()
        except OSError:
            pass


def _guardar(producto, valor):
    anterior = producto.imagen
    producto.imagen = valor
    producto.save(update_fields=['imagen'])
    if anterior != valor:
        _borrar_archivo(anterior)
    return producto


def guardar_foto(producto, archivo):
    """Achica la foto subida, la deja en MEDIA_ROOT y la anota en el producto."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if archivo is None:
        raise ValidationError('Elegí una foto o sacala con la cámara.')
    if archivo.size > PESO_MAXIMO:
        raise ValidationError('La foto pesa más de 10 MB. Probá con otra.')
    try:
        foto = Image.open(archivo)
        foto = ImageOps.exif_transpose(foto)      # la cámara guarda "girada"
        foto = foto.convert('RGB')
        foto.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValidationError('Ese archivo no es una foto que se pueda leer (JPG, PNG o WEBP).')
    carpeta = Path(settings.MEDIA_ROOT) / CARPETA
    carpeta.mkdir(parents=True, exist_ok=True)
    # Nombre nuevo en cada subida: la app y el navegador guardan la foto en
    # caché por su dirección, y así una foto cambiada se ve enseguida.
    nombre = f'{producto.pk}-{secrets.token_hex(6)}.webp'
    foto.save(carpeta / nombre, 'WEBP', quality=CALIDAD)
    return _guardar(producto, f'{CARPETA}/{nombre}')


def poner_url(producto, url):
    """Anota una foto que vive en otro servidor (sólo https)."""
    url = (url or '').strip()
    if not url.startswith('https://') or len(url) > LARGO_MAXIMO_URL or ' ' in url:
        raise ValidationError(
            f'La dirección de la foto tiene que empezar con https:// y tener hasta {LARGO_MAXIMO_URL} caracteres.')
    return _guardar(producto, url)


def quitar(producto):
    return _guardar(producto, None)
