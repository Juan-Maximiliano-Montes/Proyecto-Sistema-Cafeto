"""¿Está lista la conexión con las cuentas de los clientes de la app?

    docker compose exec web python manage.py probar_firebase

Revisa, en orden, lo que necesita ``/api/movil/clientes/`` para verificar
el token de Firebase de un cliente, y dice en castellano qué falta:

1. la librería ``firebase-admin`` (se instala al reconstruir la imagen);
2. la variable ``CAFETO_FIREBASE_CREDENCIAL`` (docker compose la pone en
   ``/run/secrets/firebase.json``);
3. el archivo en esa ruta (``secretos/firebase.json`` en el NAS o la PC);
4. que el archivo sea una clave de cuenta de servicio y se pueda leer.

No sale a internet ni verifica ningún token: sólo prepara lo que el
sistema usaría. Termina con error si falta algo, así sirve en un script.
"""
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

COMO_BAJAR_LA_CLAVE = (
    'Bajala desde la consola de Firebase: Configuración del proyecto > '
    'Cuentas de servicio > Generar nueva clave privada, y guardala como '
    'secretos/firebase.json (ver el README, «La app móvil»).')


class Command(BaseCommand):
    help = ('Dice si está todo listo para verificar las cuentas de los '
            'clientes de la app (Firebase), o qué falta.')

    def handle(self, *args, **opciones):
        faltan = []
        try:
            import firebase_admin
            from firebase_admin import credentials
        except ImportError:
            credentials = None
            faltan.append('Falta la librería firebase-admin. Está en requirements.txt: '
                          'reconstruí la imagen con "docker compose up -d --build".')
        else:
            self.stdout.write(f'Librería firebase-admin {firebase_admin.__version__}: instalada.')

        ruta = getattr(settings, 'FIREBASE_CREDENCIAL', '')
        datos = None
        if not ruta:
            faltan.append(
                'Falta la variable CAFETO_FIREBASE_CREDENCIAL con la ruta, dentro del '
                'contenedor, de la cuenta de servicio (docker compose la pone en '
                '/run/secrets/firebase.json: reiniciá con "docker compose up -d").')
        elif not os.path.isfile(ruta):
            faltan.append(f'No está el archivo {ruta}. {COMO_BAJAR_LA_CLAVE}')
        else:
            self.stdout.write(f'Archivo de la cuenta de servicio: {ruta}')
            try:
                with open(ruta, encoding='utf-8') as archivo:
                    datos = json.load(archivo)
            except (OSError, ValueError):
                faltan.append(f'El archivo {ruta} no se puede leer como JSON. '
                              f'{COMO_BAJAR_LA_CLAVE}')
            else:
                if not isinstance(datos, dict) or datos.get('type') != 'service_account':
                    datos = None
                    faltan.append(
                        f'El archivo {ruta} no es la clave de una cuenta de servicio '
                        '(es otro JSON, quizás la configuración de la app). '
                        f'{COMO_BAJAR_LA_CLAVE}')

        if datos is not None and credentials is not None:
            try:
                credentials.Certificate(ruta)
            except (OSError, ValueError) as error:
                faltan.append(f'La clave de {ruta} no sirve: {error}. {COMO_BAJAR_LA_CLAVE}')

        if faltan:
            raise CommandError('Las cuentas de los clientes de la app todavía no '
                               'andan:\n- ' + '\n- '.join(faltan))

        self.stdout.write(self.style.SUCCESS(
            f'Todo bien: cuenta de servicio {datos.get("client_email")} del proyecto '
            f'{datos.get("project_id")}. /api/movil/clientes/ ya puede verificar '
            'las cuentas de los clientes.'))
