"""Reglas de las plantillas que una prueba del servidor no ve.

También: un comentario {# … #} partido en varias líneas se muestra como texto.

``data-modal-auto`` abre la ventana sola al cargar la pantalla (por
ejemplo, para volver a mostrar un formulario con errores). Puesto en otro
elemento —un botón— Bootstrap fallaba, dejaba el fondo oscuro y trababa
la pantalla: fue lo que pasó con «Pagar» una factura de compra.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ETIQUETA = re.compile(r'<[a-zA-Z][^<>]*data-modal-auto[^<>]*>', re.S)


class PlantillasTest(SimpleTestCase):

    def test_data_modal_auto_solo_en_una_ventana(self):
        mal = []
        for archivo in Path(settings.BASE_DIR).rglob('templates/**/*.html'):
            for etiqueta in ETIQUETA.findall(archivo.read_text(encoding='utf-8')):
                if not re.search(r'class="[^"]*\bmodal\b', etiqueta):
                    mal.append(f'{archivo}: {etiqueta[:90]}')
        self.assertEqual(mal, [], '\n'.join(mal))

    def test_ningun_comentario_de_plantilla_ocupa_varias_lineas(self):
        """``{# … #}`` sólo sirve en una línea: partido en varias, Django no
        lo reconoce como comentario y lo muestra en la pantalla (pasó en el
        ingreso, sobre "¿Te olvidaste la clave?")."""
        mal = []
        for archivo in Path(settings.BASE_DIR).rglob('templates/**/*.html'):
            for numero, linea in enumerate(archivo.read_text(encoding='utf-8').splitlines(), 1):
                if '{#' in linea and '#}' not in linea.split('{#', 1)[1]:
                    mal.append(f'{archivo}:{numero}')
        self.assertEqual(mal, [], '\n'.join(mal))

    def test_ningun_campo_de_clave_confia_en_autocomplete_off(self):
        """Chrome ignora autocomplete="off" en un campo de clave y, al verlo,
        llenaba con el usuario guardado del equipo otro campo de la pantalla
        (en el mostrador, el DNI del titular en el Club Cafeto)."""
        campo = re.compile(r'<input[^>]*type="password"[^>]*>')
        mal = []
        for archivo in Path(settings.BASE_DIR).rglob('templates/**/*.html'):
            for etiqueta in campo.findall(archivo.read_text(encoding='utf-8')):
                if 'autocomplete="off"' in etiqueta or 'autocomplete' not in etiqueta:
                    mal.append(f'{archivo}: {etiqueta[:90]}')
        self.assertEqual(mal, [], '\n'.join(mal))
