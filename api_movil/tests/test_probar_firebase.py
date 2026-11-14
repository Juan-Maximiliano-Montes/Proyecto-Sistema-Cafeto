"""``manage.py probar_firebase``: dice en castellano qué falta, todo junto.

No necesita la base ni sale a internet: mira la librería, la variable y el
archivo. Sin ``firebase-admin`` instalado (la imagen sin reconstruir) lo
avisa igual que lo demás.
"""
import json
import os
import tempfile
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class ProbarFirebaseTest(SimpleTestCase):

    def probar(self):
        call_command('probar_firebase', stdout=StringIO())

    @override_settings(FIREBASE_CREDENCIAL='')
    def test_sin_la_variable(self):
        with self.assertRaisesMessage(CommandError, 'CAFETO_FIREBASE_CREDENCIAL'):
            self.probar()

    @override_settings(FIREBASE_CREDENCIAL='/no/existe/firebase.json')
    def test_sin_el_archivo_dice_como_bajarlo(self):
        with self.assertRaises(CommandError) as error:
            self.probar()
        self.assertIn('No está el archivo /no/existe/firebase.json', str(error.exception))
        self.assertIn('Generar nueva clave privada', str(error.exception))

    def test_un_json_que_no_es_una_cuenta_de_servicio(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, 'firebase.json')
            with open(ruta, 'w', encoding='utf-8') as archivo:
                json.dump({'apiKey': 'de-la-app', 'projectId': 'cafeto'}, archivo)
            with override_settings(FIREBASE_CREDENCIAL=ruta), \
                    self.assertRaisesMessage(CommandError, 'no es la clave de una cuenta de servicio'):
                self.probar()
