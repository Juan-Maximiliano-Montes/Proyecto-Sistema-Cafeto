"""Recupera el acceso del TITULAR del sistema, desde el servidor.

La clave del titular no se restablece desde la aplicación: si se pudiera,
cualquier otro administrador podría quedarse con la cuenta del dueño. El
único camino es éste, que exige acceso físico (o por SSH) al servidor
donde corre el sistema:

    docker compose exec web python manage.py recuperar_titular

Genera una clave provisoria al azar, obliga a cambiarla en el próximo
ingreso, cierra las sesiones abiertas del titular, levanta el bloqueo por
reintentos (sólo cuentan los intentos posteriores al último ingreso) y lo
deja asentado en el Registro de Actividad.

Si el sistema se quedó sin titular (no debería pasar), ``--dni`` designa
como titular a un administrador activo.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

from seguridad.models import Sesion, Usuario
from seguridad.servicios import registrar_auditoria


class Command(BaseCommand):
    help = 'Genera una clave provisoria para el titular del sistema.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dni', help='Sólo si no hay titular: DNI del administrador '
                          'activo que pasa a serlo.')

    @transaction.atomic
    def handle(self, *args, dni=None, **opciones):
        titular = Usuario.objects.select_for_update().filter(
            es_titular=True).first()

        if titular is None:
            if not dni:
                raise CommandError(
                    'El sistema no tiene titular. Indicá con --dni qué '
                    'administrador activo pasa a serlo.')
            titular = Usuario.objects.select_for_update().filter(
                dni_usuario=dni, activo=True,
                id_perfil__es_sistema=True).first()
            if titular is None:
                raise CommandError(
                    f'No hay un administrador activo con DNI {dni}.')
            Usuario.objects.filter(pk=titular.pk).update(es_titular=True)
            titular.es_titular = True
        elif dni and dni != titular.dni_usuario:
            raise CommandError(
                f'El titular es {titular}: --dni sólo sirve si no hay '
                'titular. Para cambiarlo, el titular la transfiere desde la '
                'pantalla de Usuarios.')

        clave = get_random_string(12)
        titular.set_clave(clave)
        titular.debe_cambiar_clave = True
        titular.save()
        Sesion.objects.filter(id_usuario=titular, activa=True).update(
            activa=False, fecha_cierre=timezone.now(),
            motivo_cierre='REVOCACION')
        registrar_auditoria(
            None, 'RECUPERACION DE CLAVE', 'TITULAR', instancia=titular,
            modulo='SEGURIDAD',
            detalle=(f'Clave provisoria del titular {titular} generada '
                     'desde el servidor (manage.py recuperar_titular).'))

        self.stdout.write(self.style.SUCCESS(
            f'Titular: {titular}\n'
            f'Usuario: {titular.nombre_usuario}\n'
            f'Clave provisoria: {clave}\n'
            'Se pide cambiarla en el próximo ingreso. Anotala ahora: no se '
            'vuelve a mostrar.'))
