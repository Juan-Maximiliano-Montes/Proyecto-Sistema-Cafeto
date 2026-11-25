"""FID-01: un beneficio en especie sólo entrega productos que se vendan.

El escenario destructivo de la auditoría: se podía cargar «Café en grano
(insumo)» como regalo de un beneficio; el socio lo elegía en la caja y el
mostrador abortaba la venta con «es un insumo: se usa para preparar, no se
vende». Estas pruebas fijan que ahora se rechaza al cargar el beneficio (modelo
y formulario) y que el buscador no ofrece insumos.
"""
import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from fidelizacion.forms import BeneficioProductoForm, RecetaFormSet
from fidelizacion.models import (
    Beneficio,
    BeneficioProducto,
    CategoriaFidelizacion,
    ModoEntrega,
    TipoBeneficio,
)
from nucleo.tests import fabrica as f


class Base(TestCase):

    def setUp(self):
        self.prov = f.proveedor()
        self.grano = f.producto('Café en grano', 'INSUMO', prov=self.prov,
                                costo='20000', rubro_nombre='Cafetería',
                                contenido='1', unidad='KG')
        self.alfajor = f.producto('Alfajor', 'REVENTA', prov=self.prov, precio='1000')
        self.cafe = f.producto('Café chico', 'ELABORADO',
                               rubro_nombre='Cafetería', precio='2000')
        categoria = CategoriaFidelizacion.objects.order_by('puntos_minimos').first()
        self.beneficio = Beneficio.objects.create(
            id_categoria=categoria, descripcion_beneficio='Regalo FID-01',
            tipo_beneficio=TipoBeneficio.CANJEABLE, modo_entrega=ModoEntrega.ESPECIE,
            puntos_requeridos=50, vigencia_desde=datetime.date.today())


class ModeloDelBeneficioTest(Base):

    def test_un_insumo_no_se_puede_regalar(self):
        with self.assertRaises(ValidationError) as error:
            BeneficioProducto.objects.create(
                id_beneficio=self.beneficio, id_producto=self.grano, cantidad=1)
        self.assertIn('id_producto', error.exception.message_dict)
        self.assertIn('insumo', ' '.join(error.exception.message_dict['id_producto']))
        self.assertFalse(self.beneficio.renglones.exists())

    def test_full_clean_tambien_lo_rechaza(self):
        renglon = BeneficioProducto(id_beneficio=self.beneficio,
                                    id_producto=self.grano, cantidad=1)
        with self.assertRaises(ValidationError) as error:
            renglon.full_clean()
        self.assertIn('id_producto', error.exception.message_dict)

    def test_lo_vendible_si_se_puede_regalar(self):
        for producto in (self.alfajor, self.cafe):
            BeneficioProducto.objects.create(
                id_beneficio=self.beneficio, id_producto=producto, cantidad=1)
        self.assertEqual(self.beneficio.renglones.count(), 2)


class FormularioDelBeneficioTest(Base):

    def _post(self, producto):
        return {'renglones-TOTAL_FORMS': '1', 'renglones-INITIAL_FORMS': '0',
                'renglones-MIN_NUM_FORMS': '0', 'renglones-MAX_NUM_FORMS': '1000',
                'renglones-0-id_producto': str(producto.pk),
                'renglones-0-cantidad': '1'}

    def test_el_insumo_se_rechaza_en_el_formset(self):
        formset = RecetaFormSet(self._post(self.grano), instance=self.beneficio)
        self.assertFalse(formset.is_valid())
        self.assertIn('id_producto', formset.errors[0])

    def test_lo_vendible_entra_en_el_formset(self):
        formset = RecetaFormSet(self._post(self.alfajor), instance=self.beneficio)
        self.assertTrue(formset.is_valid(), formset.errors)

    def test_el_queryset_del_campo_no_incluye_insumos(self):
        ids = set(BeneficioProductoForm().fields['id_producto'].queryset
                  .values_list('pk', flat=True))
        self.assertIn(self.alfajor.pk, ids)
        self.assertIn(self.cafe.pk, ids)
        self.assertNotIn(self.grano.pk, ids)

    def test_el_selector_pide_al_buscador_el_modo_beneficio(self):
        self.assertIn('data-para="beneficio"', RecetaFormSet(instance=self.beneficio).as_p())


class BuscadorDelBeneficioTest(Base):

    def setUp(self):
        super().setUp()
        f.entrar(self.client, f.titular())

    def _ids(self, **filtros):
        respuesta = self.client.get(reverse('stock:buscar_productos'), filtros)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return {r['id'] for r in respuesta.json()['results']}

    def test_el_buscador_de_beneficios_no_ofrece_insumos(self):
        ids = self._ids(para='beneficio')
        self.assertIn(self.alfajor.pk, ids)
        self.assertIn(self.cafe.pk, ids)
        self.assertNotIn(self.grano.pk, ids)

    def test_buscar_el_insumo_por_nombre_no_lo_encuentra(self):
        self.assertEqual(self._ids(para='beneficio', q='Café en grano'), set())

    def test_no_se_puede_ampliar_pidiendo_tipos_de_mas(self):
        self.assertNotIn(self.grano.pk, self._ids(para='beneficio', tipos='INSUMO,REVENTA'))
