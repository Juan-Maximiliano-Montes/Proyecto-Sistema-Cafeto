"""Que un reintento desde el celular no repita una operación.

Todo POST que mueve mercadería (recepción, merma, traspaso, producción)
trae la cabecera ``Idempotency-Key``: la app genera la clave al abrir el
formulario y la repite si reintenta (por ejemplo, después de un corte de
wifi que se llevó la respuesta). Es la misma idea que el token de un solo
uso de la web (``nucleo/idempotencia.py``), pero guardada por usuario en la
base (``OperacionDelCelular``), porque la API no tiene sesión.

* La clave se anota ANTES de operar y en la misma transacción: si dos
  envíos iguales llegan a la vez, el índice único hace esperar al segundo
  y, cuando el primero confirma, lo rechaza.
* Si la operación falla (una regla, un 404, un 409), se deshace junto con
  la clave: el mismo formulario se puede corregir y reenviar.
* Las claves duran 24 horas; después se limpian.
"""
import re
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from api_movil import respuestas
from api_movil.models import OperacionDelCelular

VIGENCIA = timedelta(hours=24)
YA_SE_HABIA_CARGADO = 'Esa operación ya se había cargado. No se repitió.'
FALTA_LA_CLAVE = ('Falta la clave de la operación (Idempotency-Key). '
                  'Actualizá la app y probá de nuevo.')
# Lo que genera la app: letras, números y separadores; ni espacios ni nada
# que pueda colarse en un registro.
CLAVE_VALIDA = re.compile(r'^[A-Za-z0-9._:-]{8,100}$')


class _Repetida(Exception):
    pass


class _Fallida(Exception):
    def __init__(self, respuesta):
        super().__init__(respuesta.status_code)
        self.respuesta = respuesta


def texto_del_error(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def una_sola_vez(request, operacion, hacer):
    """Corre ``hacer()`` si la clave del pedido no se usó con éxito.

    ``hacer`` devuelve ``(respuesta, id de lo que creó)``. Una respuesta de
    error o un ``ValidationError`` (que vuelve como 400 con ``detail``)
    deshacen todo, la clave incluida.
    """
    clave = request.headers.get('Idempotency-Key', '').strip()
    if not CLAVE_VALIDA.match(clave):
        return respuestas.error(FALTA_LA_CLAVE, 400)
    try:
        with transaction.atomic():
            OperacionDelCelular.objects.filter(
                fecha__lt=timezone.now() - VIGENCIA).delete()
            try:
                with transaction.atomic():
                    anotada = OperacionDelCelular.objects.create(
                        id_usuario=request.usuario_actual, clave=clave,
                        operacion=operacion)
            except IntegrityError:
                raise _Repetida
            respuesta, id_registro = hacer()
            if respuesta.status_code >= 400:
                raise _Fallida(respuesta)
            anotada.id_registro = id_registro
            anotada.save(update_fields=['id_registro'])
    except _Repetida:
        return respuestas.error(YA_SE_HABIA_CARGADO, 409)
    except _Fallida as fallida:
        return fallida.respuesta
    except ValidationError as error:
        return respuestas.error(texto_del_error(error), 400)
    return respuesta
