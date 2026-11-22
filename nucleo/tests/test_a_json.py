"""El filtro a_json: tipos de Django sin TypeError y escapado dentro de <template>."""
import datetime
import json
import uuid
from decimal import Decimal

from django.template import Context, Template
from django.test import SimpleTestCase

from nucleo.templatetags.cafeto import a_json


class AJsonTest(SimpleTestCase):

    def test_decimal_fechas_horas_y_uuid(self):
        codigo = uuid.UUID('12345678-1234-5678-1234-567812345678')
        texto = a_json({'monto': Decimal('1234.50'), 'dia': datetime.date(2026, 11, 21),
                        'momento': datetime.datetime(2026, 11, 21, 20, 1, 5), 'codigo': codigo})
        self.assertEqual(json.loads(texto), {'monto': '1234.50', 'dia': '2026-11-21',
                                             'momento': '2026-11-21T20:01:05', 'codigo': str(codigo)})
        self.assertIs(type(texto), str)

    def test_en_la_plantilla_no_cierra_el_template(self):
        html = Template('{% load cafeto %}<template id="datosTablero">{{ datos|a_json }}</template>').render(
            Context({'datos': {'nombre': '</template><script>alert(1)</script> & <b>'}}))
        self.assertEqual(html.count('</template>'), 1)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;/template&gt;&lt;script&gt;', html)
        self.assertIn('&amp;', html)
