"""pluralizar y segun: «1 pago venció», «3 pagos vencieron», sin «(s)»."""
import ast
import io
import re
import tokenize
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
        """En toda la interfaz; quedan fuera los comentarios y los comandos de consola."""
        raiz = Path(settings.BASE_DIR)
        quedan = []
        for archivo in list(raiz.glob('*/templates/**/*.html')) + list(raiz.glob('templates/**/*.html')) + list(
                raiz.glob('*/static/cafeto/js/*.js')) + [
                p for p in raiz.glob('*/**/*.py') if '/tests/' not in str(p) and '/management/' not in str(p)
                and '/migrations/' not in str(p)]:
            for numero, linea in enumerate(archivo.read_text(encoding='utf-8').splitlines(), 1):
                limpia = linea.strip()
                if limpia.startswith(('#', '*', '{#', '//', '"""', "'''")) or 'No se puede dar de baja "Cafetería"' in linea:
                    continue
                if re.search(r"(?<!\w)[a-záéíóúñ]+\((s|es)\)", linea):
                    quedan.append(f'{archivo.relative_to(raiz)}:{numero}')
        self.assertEqual(quedan, [])


class ComillasTest(SimpleTestCase):
    """La interfaz nombra opciones en negrita o entre comillas rectas, nunca con « »."""

    @staticmethod
    def _cadenas_de_python(texto):
        arbol = ast.parse(texto)
        documentacion = {nodo.body[0].lineno for nodo in ast.walk(arbol)
                         if isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                         and nodo.body and isinstance(nodo.body[0], ast.Expr)
                         and isinstance(nodo.body[0].value, ast.Constant) and isinstance(nodo.body[0].value.value, str)}
        for token in tokenize.generate_tokens(io.StringIO(texto).readline):
            if token.type == tokenize.STRING and token.start[0] in documentacion:
                continue
            if token.type in (tokenize.STRING, getattr(tokenize, 'FSTRING_MIDDLE', -1)):
                yield token.start[0], token.string

    def test_no_quedan_comillas_angulares_a_la_vista(self):
        raiz = Path(settings.BASE_DIR)
        quedan = []
        for archivo in raiz.glob('*/**/*.py'):
            ruta = str(archivo)
            if '/tests/' in ruta or '/migrations/' in ruta or 'node_modules' in ruta:
                continue
            for numero, cadena in self._cadenas_de_python(archivo.read_text(encoding='utf-8')):
                if '«' in cadena or '»' in cadena:
                    quedan.append(f'{archivo.relative_to(raiz)}:{numero}')
        comentarios = re.compile(r'\{% comment %\}.*?\{% endcomment %\}|\{#.*?#\}|<!--.*?-->|/\*.*?\*/|(?<![:"\'])//[^\n]*', re.S)
        for archivo in (list(raiz.glob('*/templates/**/*.html')) + list(raiz.glob('*/templates/**/*.txt'))
                        + list(raiz.glob('templates/**/*.html')) + list(raiz.glob('*/static/cafeto/js/*.js'))):
            texto = archivo.read_text(encoding='utf-8')
            if archivo.suffix == '.html':
                texto = re.sub(r'<script\b.*?</script>', lambda m: comentarios.sub('', m.group(0)), texto, flags=re.S)
                texto = re.sub(r'\{% comment %\}.*?\{% endcomment %\}|\{#.*?#\}|<!--.*?-->', '', texto, flags=re.S)
            else:
                texto = comentarios.sub('', texto)
            if '«' in texto or '»' in texto:
                quedan.append(str(archivo.relative_to(raiz)))
        self.assertEqual(quedan, [])
