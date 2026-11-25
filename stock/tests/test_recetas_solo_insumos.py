"""STK-01: una receta sólo se arma con INSUMOS.

El escenario destructivo de la auditoría: al armar la receta de una pizza o
de un café con leche, el sistema aceptaba (y el buscador ofrecía) productos
de reventa de Minisúper como «Yogur La Serenísima», y al armar un combo
ofrecía materias primas. Estas pruebas fijan que ahora queda bloqueado en el
modelo, en el formulario de la receta y en el buscador de productos.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from nucleo.tests import fabrica as f
from stock.forms import ComposicionFormSet
from stock.models import ComposicionProducto, Producto

D = Decimal


class Base(TestCase):

    def setUp(self):
        self.prov = f.proveedor()
        self.harina = f.producto('Harina 000', 'INSUMO', prov=self.prov,
                                 costo='900', rubro_nombre='Cafetería',
                                 contenido='1', unidad='KG')
        self.yogur = f.producto('Yogur La Serenísima', 'REVENTA', prov=self.prov)
        self.pizza = f.producto('Pizza muzzarella', 'PRODUCIDO',
                                rubro_nombre='Cafetería', precio='8000')
        self.cafe = f.producto('Café con leche', 'ELABORADO',
                               rubro_nombre='Cafetería', precio='2500')
        self.empanada = f.producto('Empanada de carne', 'PRODUCIDO',
                                   rubro_nombre='Cafetería', precio='1200')
        self.combo = f.producto('Desayuno', 'COMBO', rubro_nombre='Cafetería',
                                precio='4000')


class ModeloDeLaRecetaTest(Base):
    """Backend: el modelo rechaza lo que no es insumo, sin importar la pantalla."""

    def _agregar(self, compuesto, componente, cantidad='1'):
        return ComposicionProducto.objects.create(
            id_producto=compuesto, id_componente=componente,
            cantidad=D(cantidad))

    def test_una_pizza_no_lleva_yogur_de_reventa(self):
        with self.assertRaises(ValidationError) as error:
            self._agregar(self.pizza, self.yogur)
        self.assertIn('id_componente', error.exception.message_dict)
        self.assertIn('insumo', ' '.join(error.exception.message_dict['id_componente']))
        self.assertFalse(ComposicionProducto.objects.filter(id_producto=self.pizza).exists())

    def test_un_elaborado_tampoco_lleva_reventa(self):
        with self.assertRaises(ValidationError):
            self._agregar(self.cafe, self.yogur)

    def test_una_receta_no_lleva_otros_elaborados_ni_producidos(self):
        with self.assertRaises(ValidationError):
            self._agregar(self.pizza, self.empanada)
        with self.assertRaises(ValidationError):
            self._agregar(self.pizza, self.cafe)

    def test_una_receta_si_lleva_insumos(self):
        self._agregar(self.pizza, self.harina, '0.3')
        self._agregar(self.cafe, self.harina, '0.01')
        self.assertEqual(self.pizza.composicion.count(), 1)

    def test_el_combo_sigue_llevando_lo_vendible_y_no_insumos(self):
        self._agregar(self.combo, self.yogur)
        self._agregar(self.combo, self.cafe)
        with self.assertRaises(ValidationError):
            self._agregar(self.combo, self.harina)


class FormularioDeLaRecetaTest(Base):
    """El formset de la receta (el que usa el modal del producto)."""

    def _post(self, componente):
        return {'composicion-TOTAL_FORMS': '1', 'composicion-INITIAL_FORMS': '0',
                'composicion-MIN_NUM_FORMS': '0', 'composicion-MAX_NUM_FORMS': '1000',
                'composicion-0-id_componente': str(componente.pk),
                'composicion-0-cantidad': '1'}

    def _formset(self, producto, componente):
        formset = ComposicionFormSet(self._post(componente), instance=producto)
        self.assertEqual(formset.get_default_prefix(), 'composicion')
        return formset

    def test_el_yogur_no_entra_en_la_receta_de_la_pizza(self):
        formset = self._formset(self.pizza, self.yogur)
        self.assertFalse(formset.is_valid())
        self.assertIn('id_componente', formset.errors[0])

    def test_el_insumo_entra_en_la_receta_de_la_pizza(self):
        formset = self._formset(self.pizza, self.harina)
        self.assertTrue(formset.is_valid(), formset.errors)

    def test_el_insumo_no_entra_en_un_combo(self):
        formset = self._formset(self.combo, self.harina)
        self.assertFalse(formset.is_valid())
        self.assertIn('id_componente', formset.errors[0])

    def test_el_selector_se_adapta_al_tipo_y_arranca_en_modo_receta(self):
        html = ComposicionFormSet(instance=self.pizza).as_p()
        self.assertIn('data-para="receta"', html)
        self.assertIn('data-para-segun="tipo_producto"', html)
        self.assertIn('ELABORADO:receta', html)
        self.assertIn('PRODUCIDO:receta', html)
        self.assertIn('COMBO:combo', html)
        # La URL ya no lleva un ?excluir= que dejaba pasar la reventa.
        self.assertNotIn('excluir=', html)


class BuscadorDeLaRecetaTest(Base):
    """El endpoint que alimenta el Select2 de la receta."""

    URL = 'stock:buscar_productos'

    def setUp(self):
        super().setUp()
        f.entrar(self.client, f.titular())

    def _ids(self, **filtros):
        respuesta = self.client.get(reverse(self.URL), filtros)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return {r['id'] for r in respuesta.json()['results']}

    def test_la_receta_ofrece_solo_insumos(self):
        ids = self._ids(para='receta')
        self.assertEqual(ids, {self.harina.pk})
        self.assertNotIn(self.yogur.pk, ids)

    def test_buscar_el_yogur_desde_una_receta_no_lo_encuentra(self):
        self.assertEqual(self._ids(para='receta', q='Yogur'), set())

    def test_el_combo_ofrece_lo_vendible_sin_insumos_ni_combos(self):
        ids = self._ids(para='combo')
        self.assertIn(self.yogur.pk, ids)
        self.assertIn(self.cafe.pk, ids)
        self.assertIn(self.pizza.pk, ids)
        self.assertNotIn(self.harina.pk, ids)
        self.assertNotIn(self.combo.pk, ids)

    def test_un_cliente_no_puede_ampliar_la_lista_con_otros_parametros(self):
        # Pedir tipos de más no sirve: ?para= manda y sólo se puede achicar.
        self.assertEqual(self._ids(para='receta', tipos='REVENTA,INSUMO'),
                         {self.harina.pk} & self._ids(tipos='INSUMO'))
        self.assertNotIn(self.yogur.pk, self._ids(para='receta', tipos='REVENTA'))
        self.assertEqual(self._ids(para='receta', excluir='INSUMO'), set())

    def test_un_selector_desconocido_se_rechaza_con_400(self):
        respuesta = self.client.get(reverse(self.URL), {'para': 'todo'})
        self.assertEqual(respuesta.status_code, 400)

    def test_sin_para_el_buscador_sigue_igual_para_el_resto_del_sistema(self):
        # Compras, ajustes y órdenes lo usan con ?tipos= / ?excluir=.
        ids = self._ids(tipos='REVENTA,INSUMO')
        self.assertTrue({self.harina.pk, self.yogur.pk} <= ids)
        self.assertEqual(Producto.objects.filter(pk__in=ids, tipo_producto='COMBO').count(), 0)
