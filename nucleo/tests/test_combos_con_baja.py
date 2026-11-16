"""Un combo nunca cae en la primera opción por accidente (antipatrón del
<select> desincronizado): si el registro apunta a algo dado de baja o
inactivo, eso se ve elegido y marcado, y guardar otra cosa no lo cambia."""
from decimal import Decimal as D

from django.forms.models import model_to_dict
from django.test import TestCase

from compras.models import Proveedor
from nucleo.tests import fabrica as f
from stock.forms import ProductoForm
from stock.models import Marca, Rubro


class CombosConBajaTest(TestCase):

    def setUp(self):
        self.prov = f.proveedor()
        self.marca = Marca.objects.create(nombre_marca='Marca Vieja')
        self.producto = f.producto('Galletita', prov=self.prov, marca_nombre='Marca Vieja')
        Proveedor.objects.filter(pk=self.prov.pk).update(borrado_logico=True)
        Marca.objects.filter(pk=self.marca.pk).update(borrado_logico=True)
        Rubro.objects.filter(pk=self.producto.id_rubro_id).update(activo=False)
        self.producto.refresh_from_db()

    def test_se_ve_elegido_y_marcado(self):
        html = str(ProductoForm(instance=self.producto))
        for campo, sufijo in (('id_proveedor', 'dado de baja'), ('id_marca', 'dado de baja'),
                              ('id_rubro', 'inactivo')):
            pk = getattr(self.producto, campo + '_id')
            self.assertRegex(html, rf'<option value="{pk}" selected>[^<]*\({sufijo}\)</option>', campo)

    def test_guardar_otra_cosa_no_lo_cambia(self):
        datos = {k: v for k, v in model_to_dict(self.producto).items() if v is not None}
        datos['precio_venta'] = '1500'
        formulario = ProductoForm(datos, instance=self.producto)
        self.assertTrue(formulario.is_valid(), formulario.errors)
        guardado = formulario.save()
        self.assertEqual((guardado.id_proveedor_id, guardado.id_marca_id, guardado.id_rubro_id),
                         (self.prov.pk, self.marca.pk, self.producto.id_rubro_id))

    def test_no_se_ofrece_a_quien_no_lo_tenia(self):
        otro = f.producto('Turrón', prov=f.proveedor('Otra Distribuidora', '30-71234568-9'))
        html = str(ProductoForm(instance=otro))
        self.assertNotIn('Marca Vieja', html)
        self.assertNotIn(f'<option value="{self.prov.pk}"', html)


class StockVencidoALaVistaTest(TestCase):
    """Lo vencido no se muestra como stock: el mostrador y la lista de
    productos dicen cuánto hay apto y cuánto vencido, por separado."""

    def setUp(self):
        from stock.models import Lote
        self.admin = f.usuario('40200001', 'Administrador', nombre='Ana')
        prov = f.proveedor()
        self.yogur = f.producto('Yogur', prov=prov, controla_vencimiento=True)
        f.comprar(self.admin, prov, (self.yogur, 8, '500', [(f.en_dias(10), 3), (f.en_dias(20), 5)]))
        Lote.objects.filter(id_producto=self.yogur, stock_lote=5).update(fecha_vencimiento=f.en_dias(-2))
        f.abrir_turno(self.admin)
        f.entrar(self.client, self.admin)
        self.client.post('/ventas/caja/elegir/', {'caja': f.caja_cafeteria().pk})

    def test_el_mostrador_separa_lo_apto_de_lo_vencido(self):
        from django.urls import reverse
        datos = self.client.get(reverse('ventas:catalogo'), {'q': 'Yogur'}).json()['productos'][0]
        self.assertEqual((D(datos['stock']), D(datos['vencido']), datos['agotado']), (3, 5, False))

    def test_la_lista_de_productos_muestra_lo_apto_y_aclara_lo_vencido(self):
        from stock.views.catalogos import PRODUCTOS, con_stock_vencido
        from stock.models import Producto
        fila = con_stock_vencido(Producto.objects.filter(pk=self.yogur.pk)).get()
        self.assertEqual(fila.stock_para_mostrar, 3)
        self.assertIn('5 vencidas', fila.aclaracion_de_stock)
        self.assertEqual(self.yogur.stock_vencido, 5)

    def test_un_combo_sin_componentes_aptos_no_se_ofrece(self):
        from django.urls import reverse
        from stock.models import Lote
        Lote.objects.filter(id_producto=self.yogur).update(fecha_vencimiento=f.en_dias(-1))
        combo = f.producto('Combo Yogur', tipo='COMBO', prov=None, costo='0', controla_vencimiento=False)
        f.receta(combo, (self.yogur, '2'))
        datos = {p['nombre']: p for p in self.client.get(reverse('ventas:catalogo'), {'q': 'Yogur'}).json()['productos']}
        self.assertTrue(datos['Combo Yogur']['agotado'])
        self.assertTrue(datos['Combo Yogur']['sin_componentes'])
        self.assertTrue(datos['Yogur']['agotado'])
