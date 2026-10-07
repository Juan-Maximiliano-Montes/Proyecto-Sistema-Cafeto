"""La pantalla de Beneficios no puede crecer con el catálogo de productos.

El fallo que esto cubre: el combo de producto de la receta dibujaba un
``<option>`` por cada producto vigente, y el formset lo hacía dos veces
por renglón (la fila más el molde oculto de los renglones nuevos). Medido
sobre la pantalla de Beneficios:

    productos        armado       peso      <option>
    ────────────────────────────────────────────────
            0         137 ms     37 KB            21
          500         798 ms    103 KB         1.021
        2.000       3.319 ms    303 KB         4.021
        5.000       8.366 ms    705 KB        10.021

Con 5.000 productos el servidor tardaba 8,4 segundos y el navegador se
quedaba clavado parseando el HTML: eso es el "congelamiento" del reporte.

La prueba que importa es ``test_el_peso_no_crece_con_el_catalogo``: no
mira un número absoluto —que depende de la máquina— sino que el tamaño de
la página sea el MISMO con 5 productos y con 400. Si alguien vuelve a
poner un ``<select>`` que liste el catálogo, esa comparación se rompe.
"""
import datetime
import json
from decimal import Decimal

from django.test import RequestFactory, TestCase

from maestros.crud import armar_formset, listar
from maestros.forms import RecetaFormSet
from maestros.models import (Beneficio, BeneficioProducto,
                             CategoriaFidelizacion, Marca, ModoEntrega,
                             Producto, Rubro, TipoBeneficio)
from maestros.views import BENEFICIOS, TOPE_DE_BUSQUEDA, buscar_productos
from proveedores.models import Proveedor
from usuarios.models import Perfil, Permiso, PerfilPermiso, Usuario

CUIT_QA = '30-71234567-1'


class BaseReceta(TestCase):

    def setUp(self):
        self.categoria = CategoriaFidelizacion.objects.create(
            nombre_categoria='Bronce RL', puntos_minimos=8800,
            importe_por_punto=Decimal('100'))
        self.rubro = Rubro.objects.create(nombre_rubro='Cafetería RL')
        self.marca = Marca.objects.create(nombre_marca='La Morenita RL')
        self.proveedor = Proveedor.objects.create(
            nombre_proveedor='La Salteña RL', cuit_proveedor=CUIT_QA)

        self.perfil = Perfil.objects.create(nombre='Admin RL', es_sistema=True)
        self.usuario = Usuario(dni_usuario='48000001', apellido='Haro',
                               nombre='Lucrecia', correo='rl@cafeto.test',
                               id_perfil=self.perfil)
        self.usuario.set_clave('Clave.Segura.2026')
        self.usuario.save()

    def cargar_productos(self, cuantos, desde=0):
        Producto.objects.bulk_create([
            Producto(nombre_producto='Producto RL %05d' % i,
                     id_rubro=self.rubro, id_marca=self.marca,
                     id_proveedor=self.proveedor,
                     precio_venta=Decimal('100'), precio_costo=Decimal('50'),
                     stock_minimo=1)
            for i in range(desde, desde + cuantos)])

    def pedir_lista(self):
        pedido = RequestFactory().get(BENEFICIOS.url('lista'))
        pedido.usuario_actual = self.usuario
        return listar(pedido, 'beneficios')


class PesoDeLaPantallaTest(BaseReceta):

    def test_el_peso_no_crece_con_el_catalogo(self):
        """Lo que define el arreglo: la página no depende de cuántos haya."""
        self.cargar_productos(5)
        con_pocos = len(self.pedir_lista().content)

        self.cargar_productos(395, desde=5)
        con_muchos = len(self.pedir_lista().content)

        self.assertEqual(Producto.objects.count(), 400)
        self.assertEqual(
            con_pocos, con_muchos,
            'la pantalla crece con el catálogo: alguien volvió a dibujar '
            'los productos dentro del combo')

    def test_el_combo_nace_sin_opciones(self):
        self.cargar_productos(50)
        html = armar_formset(BENEFICIOS).as_p()
        # Sólo la opción vacía del placeholder, ninguna de producto.
        self.assertEqual(html.count('<option'), 1)
        self.assertIn('data-busca-en', html)

    def test_en_la_edicion_el_combo_trae_lo_ya_elegido(self):
        """Si no, editar una receta perdería el producto cargado."""
        self.cargar_productos(50)
        producto = Producto.objects.first()
        beneficio = Beneficio.objects.create(
            id_categoria=self.categoria, descripcion_beneficio='Combo RL',
            tipo_beneficio=TipoBeneficio.CANJEABLE,
            modo_entrega=ModoEntrega.ESPECIE, puntos_requeridos=50,
            vigencia_desde=datetime.date.today())
        BeneficioProducto.objects.create(
            id_beneficio=beneficio, id_producto=producto, cantidad=2)

        html = RecetaFormSet(instance=beneficio).as_p()

        self.assertIn('value="%d" selected' % producto.pk, html)
        # El elegido, el placeholder del renglón cargado y el del vacío.
        self.assertLessEqual(html.count('<option'), 4)


class BuscadorDeProductosTest(BaseReceta):

    def _buscar(self, q='', usuario=True):
        pedido = RequestFactory().get('/maestros/api/productos/', {'q': q})
        if usuario:
            pedido.usuario_actual = self.usuario
        respuesta = buscar_productos(pedido)
        cuerpo = json.loads(respuesta.content) if respuesta.status_code == 200 else {}
        return respuesta.status_code, cuerpo

    def test_devuelve_como_mucho_el_tope(self):
        self.cargar_productos(TOPE_DE_BUSQUEDA * 3)
        codigo, datos = self._buscar()
        self.assertEqual(codigo, 200)
        self.assertEqual(len(datos['resultados']), TOPE_DE_BUSQUEDA)

    def test_filtra_por_nombre(self):
        self.cargar_productos(50)
        codigo, datos = self._buscar('00042')
        self.assertEqual(
            [r['texto'] for r in datos['resultados']].count('') , 0)
        self.assertTrue(
            all('00042' in r['texto'] for r in datos['resultados']))

    def test_no_ofrece_productos_dados_de_baja(self):
        """V-G7: una receta no se arma con algo que ya no está vigente."""
        self.cargar_productos(3)
        muerto = Producto.objects.first()
        muerto.borrado_logico = True
        muerto.save(update_fields=['borrado_logico'])

        _codigo, datos = self._buscar()
        self.assertNotIn(muerto.pk, [r['id'] for r in datos['resultados']])

    def test_sin_sesion_no_contesta(self):
        self.assertEqual(self._buscar(usuario=False)[0], 401)

    def test_sin_permiso_no_contesta(self):
        operador = Perfil.objects.create(nombre='Operador RL')
        PerfilPermiso.objects.create(
            perfil=operador,
            permiso=Permiso.objects.get_or_create(
                codigo='ver_qa_rl', defaults={'nombre': 'QA RL',
                                              'modulo': 'GENERAL'})[0])
        self.usuario.id_perfil = operador
        self.usuario.save(update_fields=['id_perfil'])

        self.assertEqual(self._buscar()[0], 403)


class ValidacionDelProductoTest(BaseReceta):
    """Acortar lo que se DIBUJA no puede aflojar lo que se ACEPTA."""

    def _post(self, pk_producto):
        return {'renglones-TOTAL_FORMS': '1', 'renglones-INITIAL_FORMS': '0',
                'renglones-MIN_NUM_FORMS': '0', 'renglones-MAX_NUM_FORMS': '1000',
                'renglones-0-id_producto': str(pk_producto),
                'renglones-0-cantidad': '1'}

    def setUp(self):
        super().setUp()
        self.cargar_productos(5)
        self.beneficio = Beneficio.objects.create(
            id_categoria=self.categoria, descripcion_beneficio='Combo val RL',
            tipo_beneficio=TipoBeneficio.CANJEABLE,
            modo_entrega=ModoEntrega.ESPECIE, puntos_requeridos=50,
            vigencia_desde=datetime.date.today())

    def test_un_id_que_no_existe_se_rechaza(self):
        formset = RecetaFormSet(self._post(999999), instance=self.beneficio)
        self.assertFalse(formset.is_valid())
        self.assertIn('id_producto', formset.errors[0])

    def test_un_producto_dado_de_baja_se_rechaza(self):
        """Aunque nunca aparezca en el buscador, el id podría mandarse."""
        muerto = Producto.objects.first()
        muerto.borrado_logico = True
        muerto.save(update_fields=['borrado_logico'])

        formset = RecetaFormSet(self._post(muerto.pk), instance=self.beneficio)
        self.assertFalse(formset.is_valid())
        self.assertIn('id_producto', formset.errors[0])

    def test_un_producto_vigente_entra(self):
        vivo = Producto.objects.filter(borrado_logico=False).first()
        formset = RecetaFormSet(self._post(vivo.pk), instance=self.beneficio)
        self.assertTrue(formset.is_valid(), formset.errors)
