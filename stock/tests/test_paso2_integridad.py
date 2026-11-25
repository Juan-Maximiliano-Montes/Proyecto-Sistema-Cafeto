"""Paso 2 de la integridad del backend: stock e importación de la planilla real.

  BuscadorTest            ?para= con NUL, y unidad + costo en cada resultado
  MarcaPropiaTest         Marca.es_propia: modelo, reservada, formulario, fábrica
  VisibleSiTest           campos que dependen del tipo y se descartan si sobran
  CantidadConComaTest     la receta acepta «0,250»
  CostoVigenteTest        precios, utilidad y margen usan el costo de la receta
  ResolverAjusteTest      una decisión inválida nunca se toma por un rechazo
  ConteoTest              idempotencia y alta rápida por peso
  FotosTest               un producto dado de baja no se toca
  ImportacionTest         las reglas nuevas del importador (STK-05 y blindaje)
"""
import io
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from nucleo import idempotencia
from nucleo.tests import fabrica as f
from nucleo.validaciones import validar_codigo_barras
from stock import precios
from stock.forms import ComposicionFormSet, MarcaForm, ProductoForm
from stock.models import (
    AjusteStock,
    ComposicionProducto,
    EstadoAjuste,
    Marca,
    Producto,
    TipoProducto,
)
from stock.reservados import marca_generica

D = Decimal


def xlsx(filas):
    libro = Workbook()
    for fila in filas:
        libro.active.append(fila)
    salida = io.BytesIO()
    libro.save(salida)
    return SimpleUploadedFile('articulos.xlsx', salida.getvalue())


# ===========================================================================
class BuscadorTest(TestCase):

    def setUp(self):
        self.prov = f.proveedor()
        self.harina = f.producto('Harina 000', 'INSUMO', prov=self.prov,
                                 costo='900', unidad='KG')
        self.cafe = f.producto('Café con leche', 'ELABORADO', precio='2500')
        f.receta(self.cafe, (self.harina, '0.5'))
        f.entrar(self.client, f.titular())
        self.url = reverse('stock:buscar_productos')

    def test_para_con_byte_nul_no_rompe(self):
        respuesta = self.client.get(self.url, {'para': 'rece\x00ta'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['results'], [])

    def test_cada_resultado_trae_unidad_y_costo_como_texto(self):
        respuesta = self.client.get(self.url, {'para': 'receta'})
        fila = respuesta.json()['results'][0]
        self.assertEqual(fila['unidad'], 'KG')
        self.assertEqual(fila['costo_unitario'], '900.00')

    def test_el_costo_de_un_compuesto_es_el_de_su_receta(self):
        filas = self.client.get(self.url, {'tipos': 'ELABORADO'}).json()['results']
        self.assertEqual(filas[0]['costo_unitario'], '450.00')


# ===========================================================================
class MarcaPropiaTest(TestCase):

    @staticmethod
    def _armar(nombre, tipo, marca):
        return Producto(nombre_producto=nombre, tipo_producto=tipo, id_marca=marca,
                        id_rubro=f.rubro(), precio_venta=D('2500'), precio_costo=D('0'),
                        contenido=D('1'), unidad_medida='UNIDAD')

    def test_la_reservada_es_propia_y_se_repara_sola(self):
        marca = marca_generica()
        self.assertTrue(marca.es_propia)
        Marca.objects.filter(pk=marca.pk).update(es_propia=False)
        self.assertTrue(marca_generica().es_propia)
        marca.refresh_from_db()
        self.assertTrue(marca.es_propia)

    def test_la_migracion_marco_las_marcas_del_local(self):
        for nombre in ('Genérica',):
            self.assertTrue(Marca.objects.get(nombre_marca=nombre).es_propia)

    def test_un_elaborado_con_marca_de_fabricante_se_rechaza(self):
        ajena = Marca.objects.create(nombre_marca='La Morenita')
        with self.assertRaises(ValidationError) as error:
            self._armar('Café', 'ELABORADO', ajena).full_clean()
        self.assertIn('id_marca', error.exception.message_dict)

    def test_un_producido_tambien(self):
        ajena = Marca.objects.create(nombre_marca='Arcor')
        with self.assertRaises(ValidationError):
            self._armar('Pizza', 'PRODUCIDO', ajena).full_clean()

    def test_con_marca_propia_entra(self):
        self.assertTrue(f.producto('Café', 'ELABORADO', precio='2500').id_marca.es_propia)

    def test_la_reventa_admite_cualquier_marca(self):
        Marca.objects.create(nombre_marca='Arcor')
        f.producto('Caramelos', 'REVENTA', marca_nombre='Arcor', prov=f.proveedor())

    def test_marcar_una_marca_como_propia_la_habilita(self):
        propia = Marca.objects.create(nombre_marca='La Casa', es_propia=True)
        self._armar('Torta', 'PRODUCIDO', propia).full_clean()

    def test_el_formulario_de_marca_la_ofrece(self):
        self.assertIn('es_propia', MarcaForm().fields)

    def test_el_selector_de_marcas_marca_las_propias_con_data_propia(self):
        ajena = Marca.objects.create(nombre_marca='La Morenita')
        html = str(ProductoForm()['id_marca'])
        propia = marca_generica()
        self.assertIn(f'value="{propia.pk}" data-propia="1"', html)
        self.assertNotIn(f'value="{ajena.pk}" data-propia', html)

    def test_la_fabrica_da_marca_propia_a_lo_que_se_prepara(self):
        for tipo in ('ELABORADO', 'PRODUCIDO'):
            self.assertTrue(f.producto(f'Algo {tipo}', tipo).id_marca.es_propia)


# ===========================================================================
class VisibleSiTest(TestCase):

    def setUp(self):
        self.rubro = f.rubro()
        self.marca = marca_generica()

    def _datos(self, tipo, **extra):
        datos = {'nombre_producto': 'Café de prueba', 'id_rubro': self.rubro.pk,
                 'tipo_producto': tipo, 'precio_venta': '2500',
                 'id_marca': self.marca.pk}
        datos.update(extra)
        return datos

    def test_el_tipo_va_primero(self):
        self.assertEqual(ProductoForm.CAMPOS_SIMPLES[0], 'tipo_producto')
        self.assertEqual(list(ProductoForm().fields)[0], 'tipo_producto')

    def test_cada_campo_declara_en_que_tipos_se_ve(self):
        campos = ProductoForm().fields
        self.assertEqual(campos['id_proveedor'].visible_si, 'tipo_producto=INSUMO,REVENTA')
        self.assertEqual(campos['fraccionable'].visible_si, 'tipo_producto=INSUMO,REVENTA')
        self.assertEqual(campos['stock_minimo'].visible_si,
                         'tipo_producto=INSUMO,PRODUCIDO,REVENTA')
        self.assertEqual(campos['precio_venta'].visible_si,
                         'tipo_producto=COMBO,ELABORADO,PRODUCIDO,REVENTA')
        for nombre in ('controla_vencimiento', 'dias_rebaja_vencimiento',
                       'porcentaje_rebaja_vencimiento', 'utilidad'):
            self.assertTrue(campos[nombre].visible_si, nombre)

    def test_un_elaborado_descarta_lo_que_no_le_corresponde(self):
        formulario = ProductoForm(self._datos(
            'ELABORADO', stock_minimo='7', fraccionable='on',
            controla_vencimiento='on', dias_rebaja_vencimiento='3',
            porcentaje_rebaja_vencimiento='10'))
        self.assertTrue(formulario.is_valid(), formulario.errors)
        limpio = formulario.cleaned_data
        self.assertEqual(limpio['stock_minimo'], D('0'))
        self.assertFalse(limpio['fraccionable'])
        self.assertFalse(limpio['controla_vencimiento'])
        self.assertIsNone(limpio['dias_rebaja_vencimiento'])
        self.assertIsNone(limpio['porcentaje_rebaja_vencimiento'])
        self.assertIsNone(limpio['id_proveedor'])

    def test_un_insumo_descarta_el_precio_de_venta(self):
        formulario = ProductoForm(self._datos(
            'INSUMO', precio_venta='999', utilidad='40',
            id_proveedor=f.proveedor().pk))
        self.assertTrue(formulario.is_valid(), formulario.errors)
        self.assertEqual(formulario.cleaned_data['precio_venta'], D('0'))

    def test_un_error_en_un_campo_oculto_no_traba_el_formulario(self):
        formulario = ProductoForm(self._datos('ELABORADO', stock_minimo='abc'))
        self.assertTrue(formulario.is_valid(), formulario.errors)

    def test_una_reventa_conserva_lo_suyo(self):
        formulario = ProductoForm(self._datos(
            'REVENTA', stock_minimo='7', id_proveedor=f.proveedor().pk))
        self.assertTrue(formulario.is_valid(), formulario.errors)
        self.assertEqual(formulario.cleaned_data['stock_minimo'], D('7'))


# ===========================================================================
class CantidadConComaTest(TestCase):

    def setUp(self):
        self.harina = f.producto('Harina', 'INSUMO', prov=f.proveedor(), unidad='KG')
        self.pizza = f.producto('Pizza', 'PRODUCIDO', precio='8000')

    def _formset(self, cantidad):
        return ComposicionFormSet({
            'composicion-TOTAL_FORMS': '1', 'composicion-INITIAL_FORMS': '0',
            'composicion-MIN_NUM_FORMS': '0', 'composicion-MAX_NUM_FORMS': '1000',
            'composicion-0-id_componente': str(self.harina.pk),
            'composicion-0-cantidad': cantidad}, instance=self.pizza)

    def test_acepta_la_coma_decimal_argentina(self):
        formset = self._formset('0,250')
        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertEqual(formset.forms[0].cleaned_data['cantidad'], D('0.250'))

    def test_sigue_rechazando_lo_que_no_es_un_numero(self):
        self.assertFalse(self._formset('mucho').is_valid())

    def test_se_muestra_con_coma(self):
        ComposicionProducto.objects.create(
            id_producto=self.pizza, id_componente=self.harina, cantidad=D('0.25'))
        self.assertIn('0,25', ComposicionFormSet(instance=self.pizza).as_p())


# ===========================================================================
class CostoVigenteTest(TestCase):

    def setUp(self):
        self.prov = f.proveedor()
        self.grano = f.producto('Café en grano', 'INSUMO', prov=self.prov,
                                costo='1000', unidad='KG')
        # Costo propio cargado a mano ($50) distinto al de la receta ($500).
        self.cortado = f.producto('Cortado', 'ELABORADO', precio='1500', costo='50')
        f.receta(self.cortado, (self.grano, '0.5'))

    def test_la_utilidad_sale_del_costo_de_la_receta(self):
        self.cortado.refresh_from_db()
        # 1500 sobre 500 de costo (IVA 0) = 200 %. Con el $50 a mano, 2900 %.
        self.assertEqual(self.cortado.utilidad_porcentaje, D('200.00'))

    def test_el_aumento_masivo_avisa_el_bajo_costo_con_el_costo_real(self):
        productos = precios.alcanzados()
        # −60 % deja 600: arriba de los $500 de la receta, no hay pérdida
        # (con el $50 cargado a mano tampoco). −70 % deja 450: sí.
        self.assertNotIn(self.cortado, precios.quedan_bajo_costo(productos, D('-60'), D('1')))
        self.assertIn(self.cortado, precios.quedan_bajo_costo(
            precios.alcanzados(), D('-70'), D('1')))

    def test_sin_receta_cargada_el_elaborado_conserva_su_costo(self):
        solo = f.producto('Tostado', 'ELABORADO', precio='2000', costo='700')
        self.assertEqual(solo.costo_vigente(), D('700'))

    def test_con_prefetch_no_hay_una_consulta_por_producto(self):
        for i in range(5):
            otro = f.producto(f'Café {i}', 'ELABORADO', precio='1500')
            f.receta(otro, (self.grano, '0.1'))
        lista = list(Producto.objects.filter(tipo_producto='ELABORADO')
                     .prefetch_related('composicion__id_componente'))
        with self.assertNumQueries(0):
            for p in lista:
                p.costo_vigente()

    def test_la_api_de_margen_usa_el_costo_vigente(self):
        from api_servicio import views
        motivo, utilidad = views._motivo_de_margen(self.cortado, 15, False)
        self.assertIsNone(motivo)                # 200 % con el costo de la receta
        self.assertEqual(utilidad, D('200.00'))


# ===========================================================================
class AjustesTest(TestCase):

    def setUp(self):
        self.titular = f.titular()
        f.entrar(self.client, self.titular)
        prov = f.proveedor()
        self.producto = f.producto('Yerba', 'REVENTA', prov=prov)
        f.comprar(self.titular, prov, (self.producto, 10, 600))

    def _conteo_enviado(self):
        from stock import servicios
        conteo = servicios.iniciar_conteo(self.titular)
        servicios.cargar_conteo(conteo, self.producto, D('8'))
        return servicios.enviar_conteo(conteo)

    def test_una_decision_invalida_no_es_un_rechazo(self):
        ajuste = self._conteo_enviado()
        respuesta = self.client.post(
            reverse('stock:resolver', args=[ajuste.pk]),
            {'decision': 'cualquier-cosa'})
        self.assertEqual(respuesta.status_code, 302)
        ajuste.refresh_from_db()
        self.assertEqual(ajuste.estado_ajuste, EstadoAjuste.PENDIENTE)

    def test_sin_decision_tampoco(self):
        ajuste = self._conteo_enviado()
        self.client.post(reverse('stock:resolver', args=[ajuste.pk]), {})
        ajuste.refresh_from_db()
        self.assertEqual(ajuste.estado_ajuste, EstadoAjuste.PENDIENTE)


# ===========================================================================
class ConteoTest(TestCase):

    def setUp(self):
        self.titular = f.titular()
        f.entrar(self.client, self.titular)
        self.rubro = f.rubro()
        self.url = reverse('stock:conteo')
        self.client.post(self.url, {'accion': 'empezar'})

    def _emitir(self, token='tok-123'):
        sesion = self.client.session
        sesion[idempotencia.CLAVE] = [token]
        sesion.save()
        return token

    def test_el_alta_rapida_con_decimales_entra_por_kilo_y_fraccionable(self):
        self.client.post(self.url, {
            'accion': 'alta_rapida', 'nombre': 'Queso de prueba', 'rubro': self.rubro.pk,
            'precio': '8500', 'costo': '6000', 'contada': '2,350'})
        producto = Producto.objects.get(nombre_producto='Queso de prueba')
        self.assertEqual(producto.unidad_medida, 'KG')
        self.assertTrue(producto.fraccionable)

    def test_el_alta_rapida_entera_sigue_por_unidad(self):
        self.client.post(self.url, {
            'accion': 'alta_rapida', 'nombre': 'Galletitas de prueba', 'rubro': self.rubro.pk,
            'precio': '1500', 'costo': '900', 'contada': '4'})
        producto = Producto.objects.get(nombre_producto='Galletitas de prueba')
        self.assertEqual(producto.unidad_medida, 'UNIDAD')
        self.assertFalse(producto.fraccionable)

    def test_el_mismo_token_no_carga_dos_veces(self):
        prov = f.proveedor()
        producto = f.producto('Yerba', 'REVENTA', prov=prov)
        token = self._emitir()
        datos = {'accion': 'cargar', 'producto': producto.pk, 'contada': '3',
                 'sumar': '1', 'token_operacion': token}
        self.client.post(self.url, datos)
        self.client.post(self.url, datos)
        conteo = AjusteStock.objects.get(estado_ajuste=EstadoAjuste.EN_CURSO)
        self.assertEqual(conteo.detalles.get().cantidad_contada, D('3'))

    def test_un_token_ajeno_no_hace_nada(self):
        producto = f.producto('Yerba', 'REVENTA', prov=f.proveedor())
        self.client.post(self.url, {'accion': 'cargar', 'producto': producto.pk,
                                    'contada': '3', 'token_operacion': 'inventado'})
        conteo = AjusteStock.objects.get(estado_ajuste=EstadoAjuste.EN_CURSO)
        self.assertEqual(conteo.detalles.count(), 0)

    def test_sin_token_sigue_funcionando_como_antes(self):
        producto = f.producto('Yerba', 'REVENTA', prov=f.proveedor())
        self.client.post(self.url, {'accion': 'cargar', 'producto': producto.pk,
                                    'contada': '3'})
        conteo = AjusteStock.objects.get(estado_ajuste=EstadoAjuste.EN_CURSO)
        self.assertEqual(conteo.detalles.count(), 1)


# ===========================================================================
class FotosTest(TestCase):

    def test_un_producto_dado_de_baja_no_se_toca(self):
        f.entrar(self.client, f.titular())
        producto = f.producto('Viejo', 'REVENTA', prov=f.proveedor())
        Producto.objects.filter(pk=producto.pk).update(borrado_logico=True)
        respuesta = self.client.post(
            reverse('stock:producto_foto', args=[producto.pk]), {'quitar': '1'})
        self.assertEqual(respuesta.status_code, 404)


# ===========================================================================
class CodigoBarrasTest(TestCase):

    def test_un_codigo_corto_aclara_lo_de_los_ceros(self):
        with self.assertRaises(ValidationError) as error:
            validar_codigo_barras('7')
        self.assertIn('ceros', ' '.join(error.exception.messages))

    def test_un_codigo_largo_no_habla_de_ceros(self):
        with self.assertRaises(ValidationError) as error:
            validar_codigo_barras('1' * 31)
        self.assertNotIn('ceros', ' '.join(error.exception.messages))


# ===========================================================================
class ImportacionTest(TestCase):

    def setUp(self):
        f.entrar(self.client, f.usuario('30111240', 'Administrador'))
        self.prov = f.proveedor()
        self.url = reverse('importar', args=['productos'])

    def subir(self, filas, encabezado=None):
        encabezado = encabezado or ['Código de barras', 'Descripción', 'Rubro', 'Precio Costo',
                                    '% IVA', 'Precio Venta', 'Proveedor', 'Stock']
        return self.client.post(self.url, {'archivo': xlsx([encabezado] + list(filas))},
                                follow=True)

    def confirmar(self, **extra):
        return self.client.post(reverse('importar_confirmar', args=['productos']),
                                extra, follow=True)

    def avisos(self, pantalla):
        return ' '.join(a for r in pantalla.context['listas'] for a in r.avisos)

    def errores(self, pantalla):
        return ' '.join(e for r in pantalla.context['cuarentena'] for e in r.errores)

    # ------------------------------------------------------------- proveedor
    def test_un_proveedor_de_servicios_no_se_puede_nombrar(self):
        from compras.models import Proveedor
        luz = Proveedor.objects.create(nombre_proveedor='Edenor', cuit_proveedor='30-50001091-2',
                                       tipo_proveedor='SERVICIOS')
        pantalla = self.subir([['', 'ALGO', 'Minisuper', 100, 21, 200, luz.nombre_proveedor, '']])
        self.assertIn('No hay ningún proveedor', self.errores(pantalla))

    def test_el_proveedor_reservado_no_se_puede_nombrar(self):
        from stock.reservados import proveedor_general
        general = proveedor_general()
        pantalla = self.subir([['', 'ALGO', 'Minisuper', 100, 21, 200, general.nombre_proveedor, '']])
        self.assertIn('No hay ningún proveedor', self.errores(pantalla))

    def test_sin_proveedor_sigue_entrando_con_el_general(self):
        pantalla = self.subir([['', 'ALGO', 'Minisuper', 100, 21, 200, '', '']])
        self.assertEqual(pantalla.context['errores'], 0)
        self.confirmar()
        self.assertEqual(Producto.objects.get(nombre_producto='ALGO').id_proveedor.cuit_proveedor,
                         '30-00000000-7')

    # --------------------------------------------------------------- costo
    def test_un_cambio_no_pisa_el_costo_de_un_producto_existente(self):
        existente = f.producto('Yerba', 'REVENTA', prov=self.prov, precio='1000', costo='600',
                               marca_nombre='Genérica')
        pantalla = self.subir([['', 'Yerba', 'Minisuper', 9999, 21, 1500, '', '']])
        self.assertIn('no se aplica', self.avisos(pantalla))
        self.confirmar()
        existente.refresh_from_db()
        self.assertEqual(existente.precio_costo, D('600'))
        self.assertEqual(existente.precio_venta, D('1500'))

    # ------------------------------------------------------------ vencimiento
    def test_un_producto_que_vence_no_carga_stock_sin_lote(self):
        encabezado = ['Nombre', 'Rubro', 'Precio de venta', 'Controla vencimiento', 'Stock',
                      'Proveedor']
        pantalla = self.subir(
            [['Yogur', 'Minisuper', 900, 'Sí', 12, self.prov.nombre_proveedor]], encabezado)
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['cuarentena']])
        self.assertIn('lote', self.avisos(pantalla))
        self.confirmar(stock_inicial='on')
        yogur = Producto.objects.get(nombre_producto='Yogur')
        self.assertEqual(yogur.stock_actual, 0)
        self.assertFalse(AjusteStock.objects.exists())

    # ----------------------------------------------------------------- combo
    def test_un_combo_explicito_se_rechaza(self):
        encabezado = ['Nombre', 'Rubro', 'Tipo', 'Precio de venta']
        pantalla = self.subir([['Combo desayuno', 'Cafetería', 'Combo', 4000]], encabezado)
        self.assertIn('combo', self.errores(pantalla).lower())
        self.assertEqual(pantalla.context['altas'], 0)

    def test_un_nombre_que_empieza_con_combo_entra_como_elaborado_con_aviso(self):
        for nombre in ('COMBO DESAYUNO', 'COMBOS MERIENDA'):
            pantalla = self.subir([['', nombre, 'Cafetería', 0, 21, 4000, '', '']])
            self.assertIn('combo', self.avisos(pantalla).lower())
        self.confirmar()
        for nombre in ('COMBO DESAYUNO', 'COMBOS MERIENDA'):
            producto = Producto.objects.filter(nombre_producto=nombre).first()
            if producto:
                self.assertEqual(producto.tipo_producto, TipoProducto.ELABORADO)

    # ---------------------------------------------------------------- tipos
    def _tipo_de(self, nombre, rubro='Minisuper', codigo='', stock=''):
        pantalla = self.subir([[codigo, nombre, rubro, 100, 21, 300, '', stock]])
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['cuarentena']])
        self.confirmar()
        return Producto.objects.get(nombre_producto=nombre).tipo_producto

    def test_los_nombres_de_cafeteria_sin_codigo_comercial_son_elaborados(self):
        for nombre in ('Medialunas cafeteria', 'Cafetreia tostado', 'Jugo bebdia'):
            self.assertEqual(self._tipo_de(nombre), TipoProducto.ELABORADO, nombre)

    def test_con_ean13_comercial_y_stock_sigue_siendo_reventa(self):
        self.assertEqual(
            self._tipo_de('Cafe molido cafeteria', codigo='7790895000997', stock=5),
            TipoProducto.REVENTA)

    def test_con_ean13_comercial_pero_sin_stock_es_elaborado(self):
        self.assertEqual(
            self._tipo_de('Cafe en vaso cafeteria', codigo='7790895000997', stock=0),
            TipoProducto.ELABORADO)

    def test_los_platos_de_cafeteria_en_rubros_sueltos_son_elaborados(self):
        for nombre in ('Cortado doble', 'Licuado de banana', 'Tostado de jamon y queso',
                       'Medialuna con jamon', 'Porcion de torta', 'Cafe latte', 'Submarino'):
            self.assertEqual(self._tipo_de(nombre, rubro='Otros Rubros'),
                             TipoProducto.ELABORADO, nombre)
        self.assertEqual(self._tipo_de('Espresso', rubro='Sin Clasificar'),
                         TipoProducto.ELABORADO)

    def test_las_mismas_palabras_en_un_rubro_de_mercaderia_no_cambian_nada(self):
        self.assertEqual(self._tipo_de('Licuado en polvo', rubro='Minisuper'),
                         TipoProducto.REVENTA)

    # ----------------------------------------------------------- fraccionable
    def test_xkg_y_por_kg_alcanzan(self):
        for nombre in ('JAMON COCIDO XKG', 'QUESO POR KG'):
            self.subir([['', nombre, 'Fiambrería', 100, 21, 300, '', 5]])
            self.confirmar()
            self.assertTrue(Producto.objects.get(nombre_producto=nombre).fraccionable, nombre)

    def test_un_congelado_con_ean13_y_stock_negativo_decimal_no_es_por_kilo(self):
        self.subir([['7790895000997', 'HAMBURGUESAS CONGELADAS', 'Alimentos Congelados',
                     100, 21, 300, '', -2.5]])
        self.confirmar()
        self.assertFalse(Producto.objects.get(
            nombre_producto='HAMBURGUESAS CONGELADAS').fraccionable)

    def test_un_negativo_decimal_sin_ean_si_se_toma_como_peso(self):
        self.subir([['', 'MILANESAS A GRANEL', 'Alimentos Congelados', 100, 21, 300, '', -2.5]])
        self.confirmar()
        self.assertTrue(Producto.objects.get(nombre_producto='MILANESAS A GRANEL').fraccionable)

    # ---------------------------------------------------------------- precio
    def test_un_precio_de_reventa_menor_a_50_avisa(self):
        pantalla = self.subir([['', 'CARAMELO', 'Minisuper', 1, 21, 20, '', '']])
        self.assertIn('muy bajo', self.avisos(pantalla))

    def test_un_precio_normal_no_avisa_eso(self):
        pantalla = self.subir([['', 'CARAMELO', 'Minisuper', 10, 21, 300, '', '']])
        self.assertNotIn('muy bajo', self.avisos(pantalla))
