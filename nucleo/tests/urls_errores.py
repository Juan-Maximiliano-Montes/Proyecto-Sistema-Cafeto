"""Rutas de prueba que fallan a propósito (test_errores)."""
import decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.urls import include, path


def validacion(request):
    raise ValidationError('El stock no alcanza: quedan 2 unidades.')


def integridad(request):
    raise IntegrityError('duplicate key value violates unique constraint "uq_productos_codigo"')


def protegido(request):
    raise ProtectedError('referenciado', set())


def decimal_roto(request):
    return decimal.Decimal('abc') * 2 if decimal.getcontext().traps[decimal.InvalidOperation] else None


def rota(request):
    raise RuntimeError('detalle interno que no se tiene que ver: SELECT * FROM usuarios')


urlpatterns = [
    path('falla/validacion/', validacion),
    path('falla/integridad/', integridad),
    path('falla/protegido/', protegido),
    path('falla/decimal/', decimal_roto),
    path('falla/rota/', rota),
    path('', include('cafeto.urls')),
]

handler400 = 'nucleo.errores.pagina_400'
handler403 = 'nucleo.errores.pagina_403'
handler404 = 'nucleo.errores.pagina_404'
handler500 = 'nucleo.errores.pagina_500'


def sin_base(request):
    from django.db import OperationalError
    raise OperationalError('server closed the connection unexpectedly')


def solo_post(request):
    from django.http import HttpResponseNotAllowed
    return HttpResponseNotAllowed(['POST'])


def turno_duplicado(request):
    raise IntegrityError('duplicate key value violates unique constraint "uq_turno_abierto_por_caja"')


urlpatterns = [
    path('falla/sin-base/', sin_base),
    path('falla/solo-post/', solo_post),
    path('falla/turno-duplicado/', turno_duplicado),
] + urlpatterns
