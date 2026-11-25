"""API-01 / HTX-01: abrir ``/<id>/editar/`` a mano (un favorito, un enlace
pegado) no tiene página propia: vuelve a la lista, buscando esa fila y
diciendo cuál abrir. El modal sigue pidiendo su fragmento con ``formulario=1``.
"""
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.urls import reverse

from nucleo.tests import fabrica as f
from stock.models import Producto, Rubro


class EditarPorGetDirectoTest(TestCase):

    def setUp(self):
        self.duenia = f.titular()
        f.entrar(self.client, self.duenia)
        self.prov = f.proveedor()

    def destino(self, url):
        partes = urlsplit(self.client.get(url).headers['Location'])
        return partes.path, {k: v[0] for k, v in parse_qs(partes.query).items()}

    def test_un_producto_con_codigo_vuelve_buscando_su_codigo(self):
        producto = f.producto('Alfajor', prov=self.prov, codigo_barras='7790895000997')
        ruta, consulta = self.destino(reverse('stock:productos_editar', args=[producto.pk]))
        self.assertEqual(ruta, reverse('stock:productos_lista'))
        self.assertEqual(consulta, {'busqueda': '7790895000997', 'abrir': str(producto.pk)})

    def test_sin_codigo_busca_por_el_nombre(self):
        producto = f.producto('Alfajor triple', prov=self.prov)
        self.assertFalse(producto.codigo_barras)
        _, consulta = self.destino(reverse('stock:productos_editar', args=[producto.pk]))
        self.assertEqual(consulta, {'busqueda': 'Alfajor triple', 'abrir': str(producto.pk)})

    def test_lo_que_no_tiene_codigo_busca_por_su_nombre_y_va_codificado(self):
        rubro = Rubro.objects.get(nombre_rubro='Cafetería')
        url = reverse('stock:rubros_editar', args=[rubro.pk])
        self.assertEqual(self.client.get(url).headers['Location'],
                         f'{reverse("stock:rubros_lista")}?busqueda=Cafeter%C3%ADa&abrir={rubro.pk}')

    def test_la_lista_a_la_que_vuelve_muestra_la_fila(self):
        producto = f.producto('Alfajor', prov=self.prov, codigo_barras='7790895000997')
        respuesta = self.client.get(reverse('stock:productos_editar', args=[producto.pk]), follow=True)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Alfajor')
        self.assertEqual(respuesta.context['busqueda'], '7790895000997')
        self.assertFalse(respuesta.context['esperando'])      # la lista a pedido ya trae filas

    def test_el_modal_sigue_pidiendo_su_fragmento(self):
        producto = f.producto('Alfajor', prov=self.prov)
        respuesta = self.client.get(reverse('stock:productos_editar', args=[producto.pk]), {'formulario': '1'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, 'crud/_campos.html')

    def test_uno_dado_de_baja_sigue_pidiendo_reactivar_primero(self):
        producto = f.producto('Alfajor', prov=self.prov)
        Producto.objects.filter(pk=producto.pk).update(borrado_logico=True)
        respuesta = self.client.get(reverse('stock:productos_editar', args=[producto.pk]))
        self.assertRedirects(respuesta, reverse('stock:productos_lista'), fetch_redirect_response=False)
