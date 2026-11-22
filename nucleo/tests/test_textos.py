"""pluralizar y segun: «1 pago venció», «3 pagos vencieron», sin «(s)»."""
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.template import Context, Template
from django.test import SimpleTestCase

from nucleo.textos import pluralizar, segun


class PluralizarTest(SimpleTestCase):

    def test_singular_solo_para_uno(self):
        self.assertEqual(pluralizar(1, 'pago'), '1 pago')
        self.assertEqual(pluralizar(0, 'ticket'), '0 tickets')
        self.assertEqual(pluralizar(3, 'renglón', 'renglones'), '3 renglones')
        self.assertEqual(pluralizar(Decimal('1.000'), 'unidad', 'unidades'), '1.000 unidad')
        self.assertEqual(segun(2, 'venció', 'vencieron'), 'vencieron')
        self.assertEqual(segun(None, 'queda', 'quedan'), 'quedan')

    def test_filtros_de_plantilla(self):
        html = Template('{% load cafeto %}{{ n|pluralizar:"renglón,renglones" }} {{ n|segun:"se vendió,se vendieron" }}'
                        ' · {{ n|segun:"bajala, corregila|bajalas, corregilas" }}')
        self.assertEqual(html.render(Context({'n': 1})), '1 renglón se vendió · bajala, corregila')
        self.assertEqual(html.render(Context({'n': 4})), '4 renglones se vendieron · bajalas, corregilas')

    def test_no_quedan_plurales_mecanicos_a_la_vista(self):
        """Fuera de Vender (alcance de la entrega), comentarios y comandos de consola."""
        raiz = Path(settings.BASE_DIR)
        quedan = []
        for archivo in list(raiz.glob('*/templates/**/*.html')) + list(raiz.glob('*/static/cafeto/js/*.js')) + [
                p for p in raiz.glob('*/**/*.py') if '/tests/' not in str(p) and '/management/' not in str(p)
                and '/migrations/' not in str(p)]:
            if archivo.name == 'mostrador.html':
                continue
            for numero, linea in enumerate(archivo.read_text(encoding='utf-8').splitlines(), 1):
                limpia = linea.strip()
                if limpia.startswith(('#', '*', '{#', '//', '"""', "'''")) or 'No se puede dar de baja "Cafetería"' in linea:
                    continue
                if re.search(r"(?<!\w)[a-záéíóúñ]+\((s|es)\)", linea):
                    quedan.append(f'{archivo.relative_to(raiz)}:{numero}')
        self.assertEqual(quedan, [])
