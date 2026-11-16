"""Quién puede usar la API de servicio (DD-84).

Una automatización (n8n) no es una persona: no tiene usuario, ni perfil, ni
sesión. Entra con UNA credencial propia, ``CAFETO_N8N_TOKEN``, que vive en
el entorno del servidor y viaja en ``Authorization: Bearer …``.

  * Sin la variable, la API entera responde 503: no existe hasta que alguien
    la habilita a propósito.
  * Se compara en tiempo constante (``secrets.compare_digest``), para que no
    se pueda adivinar la credencial midiendo cuánto tarda la respuesta.
  * Lo que esa credencial puede hacer es lo que hay en ``views.py`` y nada
    más: leer el resumen del día, leer órdenes emitidas y ponerle la foto a
    un producto. No vende, no paga, no toca stock ni usuarios.
"""
import secrets
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

LARGO_MINIMO = 32


def credencial():
    return (getattr(settings, 'CAFETO_N8N_TOKEN', '') or '').strip()


def de_servicio(vista):
    @csrf_exempt
    @wraps(vista)
    def envuelta(request, *args, **kwargs):
        esperada = credencial()
        if len(esperada) < LARGO_MINIMO:
            return JsonResponse({'detail': 'La API de servicio no está habilitada en este servidor.'}, status=503)
        recibida = request.headers.get('Authorization', '')
        if not recibida.startswith('Bearer ') or not secrets.compare_digest(
                recibida[7:].strip().encode(), esperada.encode()):
            return JsonResponse({'detail': 'Credencial de servicio inválida.'}, status=401)
        return vista(request, *args, **kwargs)
    return envuelta
