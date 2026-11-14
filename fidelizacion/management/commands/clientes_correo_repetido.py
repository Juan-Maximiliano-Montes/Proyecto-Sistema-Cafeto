"""Los clientes vigentes que comparten el correo, para unificarlos.

    docker compose exec web python manage.py clientes_correo_repetido

El correo es con lo que el cliente entra a la app del club: con dos fichas
vigentes que lo comparten, la app no sabe cuál mostrarle (le pide el DNI o
que pase por la caja). Desde que ``Cliente.clean`` lo impide no se cargan
repetidos nuevos; este comando muestra los que quedaron de antes.

Sólo lista: unificar es una decisión de la persona (qué ficha queda, a cuál
se le cambia el correo), y se hace desde la pantalla de Clientes.
"""
from django.core.management.base import BaseCommand

from fidelizacion.models import Cliente


class Command(BaseCommand):
    help = 'Lista los clientes vigentes que comparten el mismo correo.'

    def handle(self, *args, **opciones):
        repetidos = list(Cliente.correos_repetidos())
        if not repetidos:
            self.stdout.write(self.style.SUCCESS(
                'No hay clientes vigentes que compartan el correo.'))
            return
        fichas = sum(fila['cantidad'] for fila in repetidos)
        self.stdout.write(
            f'{fichas} cliente(s) comparten {len(repetidos)} correo(s). '
            'Unificalos desde la pantalla de Clientes:\n')
        for fila in repetidos:
            self.stdout.write(self.style.WARNING(f'{fila["correo"]} ({fila["cantidad"]} fichas)'))
            for cliente in (Cliente.objects.filter(borrado_logico=False,
                                                   email_cliente__iexact=fila['correo'])
                            .select_related('id_categoria').order_by('pk')):
                vinculada = ' · vinculada a la app' if cliente.uid_firebase else ''
                self.stdout.write(
                    f'  N° {cliente.pk} · {cliente} · DNI {cliente.dni_cliente or "-"} · '
                    f'tel. {cliente.telefono_cliente or "-"} · {cliente.id_categoria} · '
                    f'{cliente.puntos_disponibles} puntos{vinculada}')
