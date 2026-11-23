"""La planilla REAL del local, con sus mañas (auditoría de noviembre 2026).

Lo que se prueba acá salió de importar los archivos que usa Lucrecia
(«Artículos_2026-11-14.xlsx», exportado de Fácil Virtual) y de intentar
cobrar en el mostrador lo que entró:

  RuidoDeExcelTest         Excel no guarda 0,02 sino 0.020000000000000046:
                           el importador veía decimales donde no hay
  FraccionablesTest        el queso y el fiambre se venden por peso, pero
                           las salchichas x 6 NO: se reconoce por evidencia
                           de la fila, nunca por el rubro entero
  StockAnomaloTest         8693.475 «kg» de queso son gramos mal cargados
  CostoCeroTest            un producto que se vende con costo $0 infla el
                           margen hasta que se le cargue el costo
  ElaboradoSinRecetaTest   los 85 artículos de cafetería entran sin receta
                           y hay que poder venderlos y editarlos igual
"""
import io
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import Workbook

from nucleo.importar import Importador
from nucleo.tests import fabrica as f
from ventas import servicios as ventas
from stock.importacion import STOCK_FRACCIONABLE_MAXIMO
from stock.models import Producto, TipoProducto, UnidadMedida


def xlsx(filas, nombre='articulos.xlsx'):
    libro = Workbook()
    for fila in filas:
        libro.active.append(fila)
    salida = io.BytesIO()
    libro.save(salida)
    return SimpleUploadedFile(nombre, salida.getvalue())


# ===========================================================================
class RuidoDeExcelTest(SimpleTestCase):
    """El valor de una celda NO es el número que se ve en la pantalla."""

    def test_el_ruido_de_punto_flotante_se_limpia(self):
        # Los cuatro casos salieron de la planilla del local.
        self.assertEqual(Importador._texto(0.020000000000000046), '0.02')
        self.assertEqual(Importador._texto(1.2499999999999996), '1.25')
        self.assertEqual(Importador._texto(-2.498e-16), '0')
        self.assertEqual(Importador._texto(8693.475), '8693.475')

    def test_nunca_sale_en_notacion_cientifica(self):
        """``leer_numero`` rechaza la científica a propósito: si el
        importador la escribiera, la fila entera quedaría en cuarentena."""
        for valor in (0.0000001, 1e-7, 12345678.5):
            self.assertNotIn('e', Importador._texto(valor).lower())

    def test_un_entero_sigue_entrando_sin_decimales(self):
        self.assertEqual(Importador._texto(1500.0), '1500')
        self.assertEqual(Importador._texto(100.0), '100')

    def test_un_valor_imposible_no_rompe_la_lectura(self):
        self.assertEqual(Importador._texto(float('nan')), '')
        self.assertEqual(Importador._texto(float('inf')), '')


# ===========================================================================
class BaseImportar(TestCase):

    def setUp(self):
        f.entrar(self.client, f.usuario('30111240', 'Administrador'))
        self.prov = f.proveedor()
        self.url = reverse('importar', args=['productos'])

    def subir(self, archivo):
        return self.client.post(self.url, {'archivo': archivo}, follow=True)

    def confirmar(self, **extra):
        return self.client.post(reverse('importar_confirmar', args=['productos']),
                                extra, follow=True)

    def avisos(self, pantalla):
        return ' '.join(a for r in pantalla.context['listas'] for a in r.avisos)

    def como_facil_virtual(self, *filas):
        """La planilla tal como la exporta Fácil Virtual: sin columna
        «Fraccionable» ni «Unidad», que es cuando se reconoce por la fila."""
        return xlsx([['Código de barras', 'Descripción', 'Rubro', 'Precio Costo',
                      '% IVA', 'Precio Venta', 'Proveedor', 'Stock']] + list(filas))


# ===========================================================================
class FraccionablesTest(BaseImportar):

    def test_el_stock_con_decimales_reales_entra_por_kilo(self):
        pantalla = self.subir(self.como_facil_virtual(
            ['', 'QUESO CREMOSO', 'Fiambrería', 6000, 21, 8500,
             self.prov.nombre_proveedor, 2.3500000000000005]))
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['cuarentena']])
        self.assertIn('fraccionable', self.avisos(pantalla))
        self.confirmar()
        queso = Producto.objects.get(nombre_producto='QUESO CREMOSO')
        self.assertTrue(queso.fraccionable)
        self.assertEqual(queso.unidad_medida, UnidadMedida.KG)
        self.assertEqual(queso.contenido, D('1'))

    def test_el_nombre_tambien_alcanza(self):
        pantalla = self.subir(self.como_facil_virtual(
            ['', 'ACEITUNAS SUELTAS', 'Fiambrería', 3000, 21, 4500, self.prov.nombre_proveedor, 4],
            ['', 'ASADO X KG', 'Carnicería', 7000, 21, 9900, self.prov.nombre_proveedor, 10]))
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['cuarentena']])
        self.confirmar()
        for nombre in ('ACEITUNAS SUELTAS', 'ASADO X KG'):
            self.assertTrue(Producto.objects.get(nombre_producto=nombre).fraccionable, nombre)

    def test_un_envasado_de_fiambreria_NO_se_reclasifica(self):
        """El bug que esto previene: marcar por rubro entero le ponía
        «precio por kilo» a las salchichas x 6 y al queso rallado en sobre,
        y el mostrador cobraba un kilo al vender un paquete."""
        pantalla = self.subir(self.como_facil_virtual(
            ['7790040000011', 'SALCHICHAS X 6', 'Fiambrería', 1200, 21, 1800,
             self.prov.nombre_proveedor, 12],
            ['7790040000028', 'QUESO RALLADO SOBRE 40G', 'Fiambrería', 500, 21, 800,
             self.prov.nombre_proveedor, 30],
            ['7790040000035', 'ACEITUNAS DOY PACK', 'Fiambrería', 900, 21, 1400,
             self.prov.nombre_proveedor, 8],
            ['7790040000042', 'MILANESAS DE POLLO CAJA', 'Carnicería', 2500, 21, 3900,
             self.prov.nombre_proveedor, 6]))
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['cuarentena']])
        self.assertNotIn('fraccionable', self.avisos(pantalla))
        self.confirmar()
        self.assertEqual(Producto.objects.filter(fraccionable=True).count(), 0)
        for producto in Producto.objects.exclude(id_proveedor=None):
            self.assertEqual(producto.unidad_medida, UnidadMedida.UNIDAD, producto)

    def test_si_la_planilla_trae_la_columna_manda_la_planilla(self):
        """Con «Fraccionable: No» no se reconoce nada: lo dijo una persona."""
        pantalla = self.subir(xlsx([
            ['Descripción', 'Rubro', 'Precio Venta', 'Proveedor', 'Stock', 'Fraccionable'],
            ['QUESO CREMOSO SUELTO', 'Fiambrería', 8500, self.prov.nombre_proveedor, 2.35, 'No']]))
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['cuarentena']])
        self.confirmar()
        self.assertFalse(Producto.objects.get(nombre_producto='QUESO CREMOSO SUELTO').fraccionable)

    def test_un_elaborado_no_se_reclasifica_aunque_el_nombre_lo_diga(self):
        """``ck_productos_fraccionable`` sólo admite reventa e insumo."""
        pantalla = self.subir(xlsx([
            ['Descripción', 'Rubro', 'Precio Venta', 'Stock'],
            ['CAFETERIA CAFE SUELTO', 'Comidas Elaboradas', 1500, 2.5]]))
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['cuarentena']])
        self.confirmar()
        cafe = Producto.objects.get(nombre_producto='CAFETERIA CAFE SUELTO')
        self.assertEqual(cafe.tipo_producto, TipoProducto.ELABORADO)
        self.assertFalse(cafe.fraccionable)

    def test_importar_dos_veces_no_da_de_alta_un_duplicado(self):
        """La misma planilla, dos veces: la segunda ACTUALIZA.

        El producto entra «por kilo», y la identidad de un producto incluye
        su presentación (DD-55): si la segunda pasada lo buscara sólo por la
        presentación de la planilla ("por unidad"), no lo encontraría y
        daría de alta un segundo queso cremoso.
        """
        planilla = lambda precio: self.como_facil_virtual(      # noqa: E731
            ['', 'QUESO CREMOSO', 'Fiambrería', 6000, 21, precio,
             self.prov.nombre_proveedor, 2.35])
        self.subir(planilla(8500))
        self.confirmar()
        pantalla = self.subir(planilla(9200))
        self.assertEqual(pantalla.context['altas'], 0)
        self.assertEqual(pantalla.context['cambios'], 1)
        self.confirmar()
        quesos = Producto.objects.filter(nombre_producto='QUESO CREMOSO')
        self.assertEqual(quesos.count(), 1)
        self.assertEqual(quesos.get().precio_venta, D('9200'))

    def test_un_producto_que_ya_estaba_por_unidad_se_encuentra_igual(self):
        """Se cargó a mano por unidad y la planilla lo trae con decimales:
        es el mismo producto, no uno nuevo."""
        f.producto('QUESO CREMOSO', prov=self.prov, precio='8500', costo='6000',
                   rubro_nombre='Minisuper', marca_nombre='Genérica')
        pantalla = self.subir(self.como_facil_virtual(
            ['', 'QUESO CREMOSO', 'Minisuper', 6000, 21, 9200,
             self.prov.nombre_proveedor, 2.35]))
        self.assertEqual(pantalla.context['altas'], 0)
        self.assertEqual(pantalla.context['cambios'], 1)
        self.confirmar()
        self.assertEqual(Producto.objects.filter(nombre_producto='QUESO CREMOSO').count(), 1)

    def test_el_stock_con_decimales_manda_aunque_el_nombre_no_diga_nada(self):
        """La regla es: decimales reales O el nombre. Con la planilla de
        Fácil Virtual —que no trae Fraccionable ni Unidad— un stock con
        decimales alcanza, porque es la única señal que hay de que el
        artículo se pesa.

        El precio pasa a ser POR KILO, así que la fila lo avisa y el
        resumen de la vista previa lo repite: si el artículo en realidad
        viene envasado, hay que corregirlo antes de importar o declararlo
        en una columna «Fraccionable». Sin la columna no hay forma de
        distinguir 2,350 kg de fiambre de un 2,5 mal cargado.
        """
        pantalla = self.subir(self.como_facil_virtual(
            ['1003', 'PASTILLAS', 'Kiosco', 100, 0, 200, self.prov.nombre_proveedor, '2,5']))
        self.assertIn('fraccionable', self.avisos(pantalla))
        self.assertIn('el precio es POR KILO', ' '.join(pantalla.context['resumen']))
        self.confirmar()
        self.assertTrue(Producto.objects.get(nombre_producto='PASTILLAS').fraccionable)

    def test_el_ruido_de_excel_no_inventa_un_fraccionable(self):
        """8 unidades guardadas como 8.000000000000002 son 8 unidades."""
        pantalla = self.subir(self.como_facil_virtual(
            ['7790040000059', 'FIDEOS GUISEROS 500G', 'Fiambrería', 900, 21, 1400,
             self.prov.nombre_proveedor, 8.000000000000002]))
        self.assertNotIn('fraccionable', self.avisos(pantalla))
        self.confirmar()
        self.assertFalse(Producto.objects.get(nombre_producto='FIDEOS GUISEROS 500G').fraccionable)


# ===========================================================================
class StockAnomaloTest(BaseImportar):

    def setUp(self):
        # La titular: así el inventario inicial queda aprobado en el acto y
        # el stock que se carga (o no) se ve de verdad. Con otra persona
        # queda pendiente de aprobación y todo daría 0 por otra razón.
        super().setUp()
        self.client = self.client_class()
        f.entrar(self.client, f.titular())

    def test_mas_de_doscientos_kilos_no_se_carga_y_se_avisa(self):
        pantalla = self.subir(self.como_facil_virtual(
            ['', 'QUESO CREMOSO', 'Fiambrería', 6000, 21, 8500,
             self.prov.nombre_proveedor, 8693.475]))
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['cuarentena']])
        resumen = ' '.join(pantalla.context['resumen'])
        self.assertIn(f'más de {STOCK_FRACCIONABLE_MAXIMO:.0f} kg', resumen)
        self.assertIn('gramos', resumen)
        self.confirmar(stock_inicial='1')
        queso = Producto.objects.get(nombre_producto='QUESO CREMOSO')
        self.assertTrue(queso.fraccionable)
        self.assertEqual(queso.stock_actual, D('0'),
                         'ocho toneladas de queso no entran a la heladera')

    def test_un_stock_de_kilos_creible_si_se_carga(self):
        pantalla = self.subir(self.como_facil_virtual(
            ['', 'QUESO CREMOSO', 'Fiambrería', 6000, 21, 8500,
             self.prov.nombre_proveedor, 2.35]))
        self.assertIn('se puede cargar como inventario inicial',
                      ' '.join(pantalla.context['resumen']))
        self.confirmar(stock_inicial='1')
        self.assertEqual(Producto.objects.get(nombre_producto='QUESO CREMOSO').stock_actual,
                         D('2.350'))


# ===========================================================================
class CambiosEnLaVistaPreviaTest(BaseImportar):
    """La vista previa describe cada cambio: tiene que poder con TODOS los
    campos que el importador actualiza."""

    def existente(self, **extra):
        # Con la marca «Genérica», que es la que le pone el importador a una
        # planilla sin columna de marca: así la fila reconoce el producto
        # que ya está (la identidad incluye la marca, DD-55).
        extra.setdefault('marca_nombre', 'Genérica')
        return f.producto('GASEOSA 2.25L', prov=self.prov, precio='3500',
                          costo='2000', **extra)

    def test_una_fila_que_solo_cambia_el_IVA_no_rompe_la_pantalla(self):
        """Reventaba con un KeyError (error 500): «alicuota_iva» no estaba
        en la lista de nombres y la planilla de Fácil Virtual trae la
        columna «% IVA», así que pasa en cuanto una alícuota cambia."""
        self.existente()
        pantalla = self.subir(xlsx([
            ['Descripción', 'Rubro', '% IVA', 'Proveedor'],
            ['GASEOSA 2.25L', 'Minisuper', 21, self.prov.nombre_proveedor]]))
        self.assertEqual(pantalla.status_code, 200)
        self.assertEqual(pantalla.context['cambios'], 1)
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['cuarentena']])
        self.assertContains(pantalla, 'IVA 0 % → 21 %')
        self.confirmar()
        self.assertEqual(Producto.objects.get(nombre_producto='GASEOSA 2.25L').alicuota_iva,
                         D('21.0'))

    def test_cada_campo_que_se_actualiza_sabe_como_describirse(self):
        from stock.importacion import CAMPOS_QUE_SE_ACTUALIZAN, ImportadorProductos

        for campo in CAMPOS_QUE_SE_ACTUALIZAN:
            self.assertIn(campo, ImportadorProductos.NOMBRES_DE_CAMPO, campo)
            # Y describirlo no se cae con ningún campo, ni con valores vacíos.
            self.assertTrue(ImportadorProductos._describir(campo, D('0'), D('1')))
            self.assertTrue(ImportadorProductos._describir(campo, None, None))


# ===========================================================================
class CostoCeroTest(BaseImportar):

    def test_un_producto_que_se_vende_sin_costo_avisa(self):
        pantalla = self.subir(xlsx([
            ['Descripción', 'Rubro', 'Precio Venta', 'Precio Costo', 'Proveedor'],
            ['GASEOSA 2.25L', 'Minisuper', 3500, 0, self.prov.nombre_proveedor],
            ['CAFETERIA CORTADO', 'Comidas Elaboradas', 1500, '', '']]))
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['cuarentena']])
        avisos = self.avisos(pantalla)
        self.assertEqual(avisos.count('Entra sin costo ($0)'), 2, avisos)
        self.assertIn('100 %', avisos)

    def test_con_costo_cargado_no_avisa_nada(self):
        pantalla = self.subir(xlsx([
            ['Descripción', 'Rubro', 'Precio Venta', 'Precio Costo', 'Proveedor'],
            ['GASEOSA 2.25L', 'Minisuper', 3500, 2000, self.prov.nombre_proveedor]]))
        self.assertNotIn('Entra sin costo', self.avisos(pantalla))

    def test_un_insumo_sin_costo_no_avisa_porque_no_se_vende(self):
        pantalla = self.subir(xlsx([
            ['Descripción', 'Rubro', 'Tipo', 'Precio Costo', 'Proveedor'],
            ['Café en grano', 'Cafetería', 'Insumo', 0, self.prov.nombre_proveedor]]))
        self.assertNotIn('Entra sin costo', self.avisos(pantalla))


# ===========================================================================
class ElaboradoSinRecetaTest(TestCase):
    """Los 85 artículos de «Comidas Elaboradas», «CAFETERIA » y «PROMO »
    entran de la planilla SIN composición. Tienen que poder venderse y
    editarse igual: la receta se carga cuando se pueda, no antes de cobrar
    el primer café."""

    def setUp(self):
        self.titular = f.titular()
        self.cafe = f.producto('CAFETERIA CORTADO', tipo='ELABORADO',
                               precio='1500', costo='400', rubro_nombre='Cafetería')
        self.turno = f.abrir_turno(self.titular)

    def vender(self, producto, cantidad):
        return ventas.registrar(self.turno, self.titular,
                                [ventas.Item(producto, D(cantidad))],
                                [ventas.Pago(f.efectivo())])

    def test_se_vende_sin_receta(self):
        cotizacion = ventas.cotizar([ventas.Item(self.cafe, D('2'))],
                                    operador=self.titular)
        self.assertEqual(cotizacion.total, D('3000'))
        venta, _ = self.vender(self.cafe, '2')
        self.assertEqual(venta.estado_venta, 'COMPLETADA')
        self.assertEqual(venta.total_venta, D('3000'))

    def test_el_costo_es_el_que_cargo_la_planilla(self):
        """Sin esto ``costo_vigente()`` devolvía $0 y el margen salía del
        100 % en el tablero y en los reportes."""
        self.assertEqual(self.cafe.costo_vigente(), D('400'))

    def test_se_le_puede_editar_el_precio(self):
        self.cafe.precio_venta = D('1800')
        self.cafe.full_clean()
        self.cafe.save()
        self.cafe.validar_receta()          # no levanta nada
        self.assertEqual(Producto.objects.get(pk=self.cafe.pk).precio_venta, D('1800'))

    def test_con_receta_cargada_descuenta_insumos_y_cuesta_lo_de_la_receta(self):
        grano = f.producto('Café en grano', tipo='INSUMO', costo='20000',
                           prov=f.proveedor(), unidad='KG', rubro_nombre='Cafetería')
        f.comprar(self.titular, grano.id_proveedor, (grano, '1', '20000'))
        f.receta(self.cafe, (grano, '0.018'))
        self.cafe.refresh_from_db()
        self.assertEqual(self.cafe.costo_vigente(), D('360.00'))
        self.vender(self.cafe, '1')
        grano.refresh_from_db()
        self.assertEqual(grano.stock_actual, D('0.982'))

    def test_un_combo_sin_contenido_sigue_sin_poder_venderse(self):
        combo = f.producto('PROMO CAFE Y MEDIALUNAS', tipo='COMBO', precio='2500',
                           rubro_nombre='Cafetería')
        with self.assertRaisesMessage(ValidationError, 'no tiene cargada su receta'):
            ventas.cotizar([ventas.Item(combo, D('1'))], operador=self.titular)
        with self.assertRaisesMessage(ValidationError, 'Falta el contenido del combo'):
            combo.validar_receta()

    def test_un_producido_sin_receta_sigue_sin_poder_venderse(self):
        empanadas = f.producto('Empanadas de carne', tipo='PRODUCIDO', precio='900',
                               rubro_nombre='Cafetería')
        with self.assertRaisesMessage(ValidationError, 'no tiene cargada su receta'):
            ventas.cotizar([ventas.Item(empanadas, D('1'))], operador=self.titular)
        with self.assertRaisesMessage(ValidationError, 'Falta la receta'):
            empanadas.validar_receta()
