"""Auditoría visual de noviembre (DD-102): aire bajo la barra, tablas que
no recortan la primera columna, reportes anclados, stock entero en lo que
se vende por unidad e identificadores sin separador de miles."""
import re
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from nucleo.templatetags.cafeto import cantidad_de
from nucleo.tests import fabrica as f
from stock.forms import ProductoForm
from stock.models import Producto

RAIZ = Path(settings.BASE_DIR)
CSS = (RAIZ / 'nucleo/static/cafeto/css/cafeto.css').read_text()
JS = (RAIZ / 'nucleo/static/cafeto/js/cafeto.js').read_text()


class LayoutGlobalTest(TestCase):

    def test_hay_aire_debajo_de_la_barra_y_las_anclas_no_quedan_tapadas(self):
        self.assertIn('main.pagina { padding: 2.25rem 1.5rem 1.5rem;', CSS)
        self.assertIn('html { scroll-padding-top: calc(var(--alto-barra, 4rem) + 1rem); }', CSS)
        self.assertIn("setProperty('--alto-barra'", JS)

    def test_la_barra_se_achica_en_vez_de_empujar_la_pagina(self):
        self.assertIn('.barra > .ms-auto { min-width: 0; }', CSS)
        self.assertIn('.barra .chips-cajas { min-width: 0; flex: 0 1 auto; }', CSS)

    def test_una_tabla_que_no_entra_deja_fija_la_primera_columna(self):
        self.assertIn('.table-responsive { margin: 0;', CSS)
        self.assertIn('.table-responsive.desborda > .table > :is(thead, tbody, tfoot) > tr > :first-child', CSS)
        self.assertIn("caja.classList.toggle('desborda', caja.scrollWidth > caja.clientWidth + 1)", JS)
        self.assertNotRegex(CSS, r'\.table[^{]*\{[^}]*margin-left:\s*-')


class TablasEnSuCajaTest(TestCase):
    """Toda tabla de estas pantallas va dentro de una caja que se desplaza."""

    def setUp(self):
        f.entrar(self.client, f.titular())

    def _sin_tablas_sueltas(self, html):
        sueltas = re.findall(r'(?<!<div class="table-responsive">)\s*<table class="table[^"]*"', html)
        envueltas = len(re.findall(r'<div class="table-responsive">\s*<table', html))
        return len(re.findall(r'<table class="table', html)) - envueltas, sueltas

    def test_resumen_del_mes(self):
        html = self.client.get(reverse('negocio:resumen_del_mes')).content.decode()
        self.assertEqual(self._sin_tablas_sueltas(html)[0], 0)

    def test_reportes_y_sus_datos(self):
        html = self.client.get(reverse('negocio:reportes')).content.decode()
        self.assertEqual(self._sin_tablas_sueltas(html)[0], 0)

    def test_el_archivo_usa_tablas_densas_para_entrar_en_una_notebook(self):
        html = self.client.get(reverse('negocio:archivo'), {'libro': 'ventas'}).content.decode()
        self.assertEqual(html.count('tabla-densa'), 2)


class ReportesAncladosTest(TestCase):

    def setUp(self):
        f.entrar(self.client, f.titular())

    def test_pildoras_filtro_y_grafico_vuelven_al_reporte_elegido(self):
        html = self.client.get(reverse('negocio:reportes'), {'ver': 'medios'}).content.decode()
        self.assertIn('<section id="seccion-reportes"', html)
        bloque = html[html.index('id="seccion-reportes"'):]
        pildoras = re.findall(r'<a href="(\?ver=[^"]+)" class="btn btn-sm', bloque)
        self.assertGreater(len(pildoras), 5)
        self.assertTrue(all(p.endswith('#seccion-reportes') for p in pildoras), pildoras)
        self.assertRegex(bloque, r'<form method="get" class="herramientas" action="#seccion-reportes"[^>]*>')
        estilos = re.findall(r'href="(\?[^"]*grafico=(?:barras|torta)[^"]*)" class="btn', bloque)
        self.assertTrue(estilos and all(e.endswith('#seccion-reportes') for e in estilos), estilos)
        self.assertIn('aria-current="page"', bloque)

    def test_el_tablero_de_arriba_no_salta(self):
        html = self.client.get(reverse('negocio:reportes')).content.decode()
        arriba = html[:html.index('id="seccion-reportes"')]
        self.assertNotIn('#seccion-reportes', arriba)


class StockSegunComoSeVendeTest(TestCase):

    def setUp(self):
        self.admin = f.titular()
        f.entrar(self.client, self.admin)
        self.prov = f.proveedor()
        self.alfajor = f.producto('Alfajor', prov=self.prov, stock_minimo=D('6'))
        self.queso = f.producto('Queso', prov=self.prov, unidad='KG', fraccionable=True,
                                stock_minimo=D('0.35'))
        self.cafe = f.producto('Café en grano', tipo='INSUMO', prov=self.prov, unidad='KG',
                               stock_minimo=D('2.5'))

    def test_el_filtro_sigue_la_regla_del_libro_de_stock(self):
        self.assertEqual(cantidad_de(D('6.000'), self.alfajor), '6')
        self.assertEqual(cantidad_de(D('1250.000'), self.alfajor), '1.250')
        self.assertEqual(cantidad_de(D('0.350'), self.queso), '0,350')
        self.assertEqual(cantidad_de(D('2.500'), self.cafe), '2,500')
        self.assertEqual(cantidad_de('Según componentes', self.alfajor), 'Según componentes')

    def test_la_ficha_del_producto(self):
        for producto, minimo in ((self.alfajor, '6'), (self.queso, '0,350')):
            html = self.client.get(reverse('stock:productos_ver', args=[producto.pk])).content.decode()
            self.assertRegex(html, rf'Stock mínimo</dt>\s*<dd>\s*{re.escape(minimo)}\s*</dd>', producto)
        html = self.client.get(reverse('stock:productos_ver', args=[self.alfajor.pk])).content.decode()
        self.assertNotIn('6,000', html)
        self.assertRegex(html, r'Contenido</dt>\s*<dd>\s*1,000\s*</dd>')

    def test_el_formulario_muestra_el_minimo_entero(self):
        self.queso.refresh_from_db()
        self.assertEqual(ProductoForm(instance=self.alfajor)['stock_minimo'].value(), 6)
        self.assertIn('value="6"', str(ProductoForm(instance=self.alfajor)['stock_minimo']))
        self.assertIn('value="0,350"', str(ProductoForm(instance=self.queso)['stock_minimo']))

    def _datos(self, producto, minimo, **extra):
        from django.forms.models import model_to_dict
        datos = {k: v for k, v in model_to_dict(producto).items() if v is not None}
        datos['stock_minimo'] = minimo
        datos.update(extra)
        return datos

    def test_por_unidad_no_admite_un_minimo_con_decimales(self):
        formulario = ProductoForm(self._datos(self.alfajor, '2,5'), instance=self.alfajor)
        self.assertFalse(formulario.is_valid())
        self.assertIn('va sin decimales', formulario.errors['stock_minimo'][0])
        self.assertTrue(ProductoForm(self._datos(self.alfajor, '7'), instance=self.alfajor).is_valid())

    def test_por_peso_y_los_insumos_si(self):
        for producto in (self.queso, self.cafe):
            formulario = ProductoForm(self._datos(producto, '1,750'), instance=producto)
            self.assertTrue(formulario.is_valid(), (producto, formulario.errors))

    def test_listado_y_alertas(self):
        Producto.objects.filter(pk=self.alfajor.pk).update(stock_minimo=D('6'))
        html = self.client.get(reverse('stock:productos_lista')).content.decode()
        self.assertNotIn('6,000', html)
        html = self.client.get(reverse('stock:alertas')).content.decode()
        self.assertRegex(html, r'<dt>Mínimo</dt><dd>6</dd>')
        self.assertRegex(html, r'<dt>Mínimo</dt><dd>0,350</dd>')


class IdentificadoresSinSeparadorDeMilesTest(TestCase):
    """Con USE_THOUSAND_SEPARATOR, {{ x.pk }} de 1234 se escribe «1.234»: en
    un value, un id o un parámetro de URL deja de apuntar al registro."""

    ATRIBUTO = re.compile(
        r'\b(value|data-(?!titulo)[a-z-]+|id|for|href|name|form)="[^"]*'
        r'\{\{ *[a-zA-Z_.]+\.(?:pk|id|[a-z_]+_id) *\}\}[^"]*"')

    def test_ninguna_plantilla_escribe_un_identificador_localizado(self):
        culpables = []
        for plantilla in RAIZ.glob('**/templates/**/*.html'):
            if 'node_modules' in plantilla.parts:
                continue
            for numero, linea in enumerate(plantilla.read_text().splitlines(), 1):
                if self.ATRIBUTO.search(linea):
                    culpables.append(f'{plantilla.relative_to(RAIZ)}:{numero}')
        self.assertEqual(culpables, [])

    def test_la_foto_de_un_producto_con_numero_alto(self):
        class Fila:
            pk = 7368
            foto = None
            imagen = None
        html = render_to_string('stock/_foto_producto.html', {'fila': Fila(), 'permisos_usuario': {'editar_producto'}})
        self.assertIn('id="fotoProducto7368"', html)
        self.assertNotIn('7.368', html)

    def test_pagar_una_compra_vuelve_a_su_tarjeta(self):
        fuente = (RAIZ / 'compras/templates/compras/egresos.html').read_text()
        self.assertIn('<article id="compra-{{ c.pk|stringformat:"d" }}"', fuente)
        self.assertIn('compra={{ c.pk|stringformat:"d" }}#compra-{{ c.pk|stringformat:"d" }}', fuente)
