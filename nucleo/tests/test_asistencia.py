"""Ayudas de carga (DD-70): aviso de parecidos y alta rápida del dato padre.

    ComparacionTest   la medida de "parecido" separa lo que tiene que separar
    ParecidosTest     el servidor devuelve iguales y parecidos, sin escribir
    AltaRapidaTest    el "+" da de alta la marca sin salir del producto
"""
from django.test import TestCase
from django.urls import reverse

from nucleo.asistencia import clave, puntaje
from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria
from stock.models import Marca


class ComparacionTest(TestCase):

    def nota(self, a, b):
        return puntaje(clave(a), clave(b))

    def test_lo_que_tiene_que_avisar(self):
        self.assertEqual(self.nota('Coca-Cola', 'coca cola'), 1.0)
        self.assertEqual(self.nota('Café', 'CAFE'), 1.0)
        self.assertGreaterEqual(self.nota('Coka Cola', 'Coca-Cola'), 0.78)
        self.assertGreaterEqual(self.nota('Leche', 'Leche entera'), 0.78)
        self.assertGreaterEqual(self.nota('La Serenisima', 'La Serenísima'), 0.78)

    def test_lo_que_no(self):
        self.assertLess(self.nota('Leche', 'Yerba'), 0.78)
        self.assertLess(self.nota('Pan', 'Pancho'), 0.78)
        self.assertLess(self.nota('Leche', 'Dulce de leche repostero'), 0.78)


class Base(TestCase):

    def setUp(self):
        self.admin = f.usuario('40000003', 'Administrador', nombre='Ana')
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.prov = f.proveedor()
        self.leche = f.producto('Leche entera', tipo='INSUMO', prov=self.prov,
                                marca_nombre='La Serenísima')
        f.entrar(self.client, self.admin)

    def parecidos(self, clave_crud, campo, valor, **extra):
        url = reverse(f'stock:{clave_crud}_parecidos') if clave_crud != 'proveedores' \
            else reverse('compras:proveedores_parecidos')
        datos = {'campo': campo, 'valor': valor}
        datos.update(extra)
        return self.client.get(url, datos).json()['resultados']


class ParecidosTest(Base):

    def test_avisa_un_producto_parecido(self):
        resultados = self.parecidos('productos', 'nombre_producto', 'Leche')
        self.assertEqual([r['id'] for r in resultados], [self.leche.pk])
        self.assertFalse(resultados[0]['igual'])

    def test_avisa_el_igual_aunque_cambien_tildes_y_mayusculas(self):
        resultados = self.parecidos('marcas', 'nombre_marca', 'la serenisima')
        self.assertTrue(resultados[0]['igual'])

    def test_no_se_avisa_a_si_mismo_al_editar(self):
        resultados = self.parecidos('productos', 'nombre_producto', 'Leche entera',
                                    excluir=self.leche.pk)
        self.assertEqual(resultados, [])

    def test_el_cuit_se_compara_exacto_y_normalizado(self):
        resultados = self.parecidos('proveedores', 'cuit_proveedor', '30712345671')
        self.assertEqual([r['id'] for r in resultados], [self.prov.pk])

    def test_avisa_tambien_lo_dado_de_baja(self):
        self.leche.borrado_logico = True
        self.leche.save()
        resultados = self.parecidos('productos', 'nombre_producto', 'Leche')
        self.assertTrue(resultados[0]['baja'])

    def test_un_campo_no_vigilado_no_devuelve_nada(self):
        self.assertEqual(self.parecidos('productos', 'presentacion', 'x'), [])

    def test_el_formulario_marca_los_campos(self):
        html = self.client.get(reverse('stock:productos_lista')).content.decode()
        self.assertIn('data-parecidos="/stock/productos/parecidos/?campo=nombre_producto"', html)
        self.assertIn('js-alta-rapida', html)


class AltaRapidaTest(Base):

    def test_el_mas_trae_el_alta_de_marca(self):
        html = self.client.get(reverse('stock:marcas_crear') + '?rapido=1').content.decode()
        self.assertIn('name="rapido-nombre_marca"', html)
        self.assertIn('Guardar marca', html)

    def test_guardar_devuelve_la_marca_y_queda_auditada(self):
        respuesta = self.client.post(reverse('stock:marcas_crear'),
                                     {'rapido': '1', 'rapido-nombre_marca': 'Cabrales'})
        datos = respuesta.json()
        self.assertTrue(datos['ok'])
        marca = Marca.objects.get(pk=datos['id'])
        self.assertEqual(marca.nombre_marca, 'Cabrales')
        asiento = LogAuditoria.objects.filter(operacion_realizada='ALTA DE MARCA').latest('pk')
        self.assertIn('alta rápida', asiento.detalle_operacion)
        self.assertIsNotNone(asiento.registro_nuevo)

    def test_un_duplicado_vuelve_con_el_error_en_el_campo(self):
        respuesta = self.client.post(reverse('stock:marcas_crear'),
                                     {'rapido': '1', 'rapido-nombre_marca': 'la serenisima'})
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('error-campo', respuesta.content.decode())

    def test_sin_permiso_de_alta_no_aparece_el_mas(self):
        f.entrar(self.client, self.operador)
        respuesta = self.client.get(reverse('stock:productos_lista'))
        if respuesta.status_code == 200:
            self.assertNotIn('js-alta-rapida', respuesta.content.decode())
