"""Importar productos desde Excel, con planillas como las que arma una persona."""
import io
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from nucleo.tests import fabrica
from stock.models import Producto


def xlsx(filas, nombre='lista.xlsx'):
    libro = Workbook()
    for fila in filas:
        libro.active.append(fila)
    salida = io.BytesIO()
    libro.save(salida)
    return SimpleUploadedFile(nombre, salida.getvalue())


class ImportarProductosTest(TestCase):

    def setUp(self):
        fabrica.entrar(self.client, fabrica.usuario('30111240', 'Administrador'))
        self.prov = fabrica.proveedor()
        self.url = reverse('importar', args=['productos'])

    def subir(self, archivo):
        return self.client.post(self.url, {'archivo': archivo}, follow=True)

    def confirmar(self):
        return self.client.post(reverse('importar_confirmar', args=['productos']), follow=True)

    def test_una_planilla_desprolija_entra_bien(self):
        """Columnas en otro orden, mayúsculas, una columna de más, una fila
        vacía, precios como número, con coma decimal y con el signo $."""
        p = self.prov.nombre_proveedor
        pantalla = self.subir(xlsx([
            ['PRECIO DE VENTA', 'nombre', 'MARCA', 'Rubro', 'costo', 'Proveedor',
             'Código de Barras', 'Unidad', 'Contenido', 'Fraccionable', 'Controla Vencimiento', 'Notas'],
            [1200, 'Alfajor de maicena', 'Havanna', 'Minisuper', 700, p, 7790895000997, '', '', '', '', 'x'],
            [1500.5, 'Galletitas surtidas', 'Bagley', 'Minisuper', 900.25, p, '', '', '', 'No', 'no', ''],
            ['1.200,50', 'Chocolate con leche', 'Arcor', 'minisuper', '800,75', p, '', 'G', 100, '', '', ''],
            ['$ 2.300', 'Caramelos masticables', 'Arcor', 'Minisuper', '$1.000', p, '', '', '', '', '', ''],
            [None] * 12,
            [8500, 'Queso cremoso', 'La Paulina', 'Minisuper', 6000, self.prov.cuit_proveedor, '', 'KG', 1, 'Sí', 'SI', ''],
        ]))
        self.assertEqual(pantalla.context['errores'], 0, [r.errores for r in pantalla.context['cuarentena']])
        self.assertEqual(pantalla.context['altas'], 5)
        self.confirmar()
        precios = dict(Producto.objects.filter(id_proveedor=self.prov)
                       .values_list('nombre_producto', 'precio_venta'))
        self.assertEqual(precios['Alfajor de maicena'], D('1200'))
        self.assertEqual(precios['Galletitas surtidas'], D('1500.50'))
        self.assertEqual(precios['Chocolate con leche'], D('1200.50'))
        self.assertEqual(precios['Caramelos masticables'], D('2300'))
        alfajor = Producto.objects.get(nombre_producto='Alfajor de maicena')
        self.assertEqual(alfajor.codigo_barras, '7790895000997')
        queso = Producto.objects.get(nombre_producto='Queso cremoso')
        self.assertTrue(queso.fraccionable and queso.controla_vencimiento)

    def test_con_un_titulo_arriba_de_los_encabezados(self):
        pantalla = self.subir(xlsx([
            ['Lista de precios de noviembre'], [],
            ['Nombre', 'Marca', 'Rubro', 'Precio de venta', 'Proveedor'],
            ['Alfajor', 'Havanna', 'Minisuper', 1200, self.prov.nombre_proveedor]]))
        self.assertEqual(pantalla.context['altas'], 1)

    def test_un_error_no_importa_nada_y_dice_en_que_fila(self):
        p = self.prov.nombre_proveedor
        pantalla = self.subir(xlsx([
            ['Nombre', 'Marca', 'Rubro', 'Precio de venta', 'Proveedor'],
            ['Alfajor', 'Havanna', 'Minisuper', 1200, p],
            ['Turrón', 'Arcor', 'Minisuper', 900, 'Proveedor Fantasma'],
            ['Alfajor', 'Havanna', 'Minisuper', 1200, p],
            ['Chicle', 'Beldent', 'Minisuper', -50, p]]))
        self.assertEqual(pantalla.context['errores'], 3)
        self.assertContains(pantalla, 'mismo nombre, marca y presentación que la fila 2')
        self.confirmar()
        self.assertFalse(Producto.objects.filter(nombre_producto='Alfajor').exists())

    def test_avisa_si_el_precio_queda_bajo_el_costo(self):
        pantalla = self.subir(xlsx([
            ['Nombre', 'Marca', 'Rubro', 'Precio de venta', 'Costo', 'Proveedor'],
            ['Chupetín', 'Arcor', 'Minisuper', 300, 500, self.prov.nombre_proveedor]]))
        self.assertEqual(pantalla.context['errores'], 0)
        self.assertContains(pantalla, 'por debajo del costo')

    def test_el_ejemplo_de_la_planilla_modelo_es_valido(self):
        modelo = self.client.get(reverse('importar_modelo', args=['productos']))
        pantalla = self.subir(SimpleUploadedFile('modelo.xlsx', modelo.content))
        self.assertEqual(pantalla.context['errores'], 0,
                         [r.errores for r in pantalla.context['cuarentena']])

    def test_archivos_que_no_sirven_se_explican(self):
        for archivo, mensaje in (
                (xlsx([['Producto', 'Precio'], ['Alfajor', 1200]]), 'faltan columnas obligatorias'),
                (xlsx([['Nombre', 'Marca', 'Rubro']]), 'no tiene filas con datos'),
                (SimpleUploadedFile('lista.xls', b'viejo'), '.xlsx (Excel) o .csv'),
                (SimpleUploadedFile('lista.xlsx', b'no es un excel'), 'No se pudo abrir')):
            with self.subTest(mensaje=mensaje):
                self.assertContains(self.subir(archivo), mensaje)


FACIL_VIRTUAL = ['Código de barras', 'Descripción', 'Rubro', 'Precio Costo', '% IVA', '% Utilidad',
                 'Precio Venta', 'Proveedor', 'Stock', 'Stock mínimo', 'Stock máximo', 'Control Stock', 'Activo']


class ExportacionDeFacilVirtualTest(TestCase):
    """La planilla de artículos del sistema anterior entra tal cual sale (DD-86)."""

    def setUp(self):
        fabrica.entrar(self.client, fabrica.usuario('30111241', 'Administrador'))
        self.url = reverse('importar', args=['productos'])

    def subir(self, filas):
        return self.client.post(self.url, {'archivo': xlsx([FACIL_VIRTUAL] + filas)}, follow=True)

    def confirmar(self, solo_validas=True):
        return self.client.post(reverse('importar_confirmar', args=['productos']),
                                {'solo_validas': '1'} if solo_validas else {}, follow=True)

    def fila(self, codigo, nombre, rubro='Almacén', costo=500, iva=21, precio=1000, stock=-3):
        return [codigo, nombre, rubro, costo, iva, 40, precio, '', stock, 2, 0, 'S', 'S']

    def test_sin_marca_ni_proveedor_y_con_un_rubro_nuevo(self):
        pantalla = self.subir([self.fila(7790895000997, 'ALFAJOR TRIPLE'),
                               self.fila('045', 'PAN FRANCES X KG', iva='10,5')])
        self.assertEqual(pantalla.context['errores'], 0, [r.errores for r in pantalla.context['cuarentena']])
        self.assertContains(pantalla, 'no existía: se crea')
        self.assertContains(pantalla, '2 con stock negativo')
        self.confirmar(solo_validas=False)
        alfajor = Producto.objects.get(codigo_barras='7790895000997')
        self.assertEqual(alfajor.id_marca.nombre_marca, 'Genérica')
        self.assertTrue(alfajor.id_proveedor.es_el_general)
        self.assertEqual(alfajor.id_rubro.nombre_rubro, 'Almacén')
        self.assertEqual((alfajor.alicuota_iva, alfajor.stock_actual, alfajor.stock_minimo), (D('21'), D('0'), D('2')))
        self.assertEqual(str(alfajor), 'ALFAJOR TRIPLE · 1 UNIDAD')
        self.assertEqual(Producto.objects.get(codigo_barras='045').alicuota_iva, D('10.5'))

    def test_los_codigos_sucios_se_limpian_y_el_doble_escaneo_delata_al_repetido(self):
        pantalla = self.subir([self.fila('7798126071631|', 'YERBA SUAVE'),
                               self.fila('8999002671962', 'PILAS AA X 2'),
                               self.fila('89990026719628999002671962', 'PILAS ENERGIZER AA')])
        self.assertEqual(pantalla.context['errores'], 1)
        self.assertContains(pantalla, 'se limpió')
        self.assertContains(pantalla, 'está repetido')
        self.confirmar()
        self.assertTrue(Producto.objects.filter(codigo_barras='7798126071631').exists())
        self.assertFalse(Producto.objects.filter(nombre_producto='PILAS ENERGIZER AA').exists())

    def test_entran_las_validas_y_la_cuarentena_se_baja_en_excel(self):
        pantalla = self.subir([self.fila('1001', 'CARAMELOS SURTIDOS'),
                               self.fila('1002', 'JUGO SIN PRECIO', precio=0),
                               self.fila('1003', 'ESPONJA MULTIUSO'),
                               self.fila('1004', 'ESPONJA MULTIUSO')])
        self.assertEqual((pantalla.context['errores'], pantalla.context['listas_total']), (2, 2))
        self.assertContains(pantalla, 'Importar las 2 filas válidas ahora')
        # Sin pedirlo expresamente, una fila mal sigue frenando todo.
        self.confirmar(solo_validas=False)
        self.assertFalse(Producto.objects.exists())
        despues = self.confirmar()
        self.assertEqual(Producto.objects.count(), 2)
        self.assertContains(despues, 'Quedaron 2 fila(s) en cuarentena')
        self.assertEqual(despues.context['errores'], 2)
        self.assertEqual(despues.context['listas_total'], 0)
        excel = self.client.get(reverse('importar_errores', args=['productos']))
        self.assertIn('spreadsheetml', excel['Content-Type'])
        from openpyxl import load_workbook
        hoja = load_workbook(io.BytesIO(excel.content)).active
        filas = list(hoja.iter_rows(values_only=True))
        self.assertEqual(len(filas), 3)
        self.assertEqual(filas[0][-1], 'Por qué no entró')
        self.assertIn('mayor que 0', filas[1][-1])
        # El Excel corregido se vuelve a subir con el mismo importador.
        corregido = [list(filas[0][:-2]), list(filas[1][:-2]), list(filas[2][:-2])]
        corregido[1][corregido[0].index('Precio de venta')] = 900
        corregido[2][corregido[0].index('Nombre')] = 'ESPONJA MULTIUSO VERDE'
        self.client.post(reverse('importar_descartar', args=['productos']))
        otra = self.client.post(self.url, {'archivo': xlsx(corregido)}, follow=True)
        self.assertEqual(otra.context['errores'], 0, [r.errores for r in otra.context['cuarentena']])
        self.confirmar(solo_validas=False)
        self.assertEqual(Producto.objects.count(), 4)

    def test_la_cuarentena_no_devuelve_formulas(self):
        """DD-103: lo que vino de la planilla subida vuelve a otra planilla;
        una celda que empieza con +, - o @ sale como texto (con el apóstrofo
        de siempre) y, al volver a subirla, el apóstrofo no queda en el dato.
        (Un «=» ya llega como fórmula, sin valor, y se lee vacío.)"""
        self.subir([self.fila('1001', '@SUMA(A1:A9)', precio=0),
                    self.fila('1002', '+CARAMELOS', precio=0)])
        self.confirmar()
        excel = self.client.get(reverse('importar_errores', args=['productos']))
        from openpyxl import load_workbook
        filas = list(load_workbook(io.BytesIO(excel.content)).active.iter_rows(values_only=True))
        nombre = filas[0].index('Nombre')
        self.assertEqual(filas[1][nombre], "'@SUMA(A1:A9)")
        self.assertEqual(filas[2][nombre], "'+CARAMELOS")
        self.assertFalse(any(str(c).startswith(('=', '+', '-', '@')) for fila in filas for c in fila if c))
        from nucleo.importar import _sin_apostrofo
        self.assertEqual(_sin_apostrofo("'+CARAMELOS"), '+CARAMELOS')
        self.assertEqual(_sin_apostrofo("'Hola"), "'Hola")
        self.assertEqual(_sin_apostrofo("'"), "'")

    def test_reimportar_actualiza_por_codigo_y_no_duplica(self):
        self.subir([self.fila('1001', 'CARAMELOS SURTIDOS', precio=1000)])
        self.confirmar(solo_validas=False)
        pantalla = self.subir([self.fila('1001', 'CARAMELOS SURTIDOS X 100', precio=1300)])
        self.assertEqual((pantalla.context['altas'], pantalla.context['cambios']), (0, 1))
        self.confirmar(solo_validas=False)
        self.assertEqual(Producto.objects.get().precio_venta, D('1300'))

    def test_una_hoja_que_declara_medir_una_celda_se_lee_entera(self):
        import zipfile
        original = xlsx([FACIL_VIRTUAL, self.fila('1001', 'CARAMELOS SURTIDOS')]).read()
        salida = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as entra, zipfile.ZipFile(salida, 'w') as sale:
            for item in entra.infolist():
                datos = entra.read(item.filename)
                if item.filename == 'xl/worksheets/sheet1.xml':
                    import re
                    datos = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1"', datos)
                sale.writestr(item, datos)
        pantalla = self.client.post(
            self.url, {'archivo': SimpleUploadedFile('fv.xlsx', salida.getvalue())}, follow=True)
        self.assertEqual(pantalla.context['altas'], 1)


class AltaSimpleDeProductoTest(TestCase):
    """El alta pide lo que se sabe con el producto en la mano (DD-86)."""

    def setUp(self):
        fabrica.entrar(self.client, fabrica.usuario('30111242', 'Administrador'))

    def crear(self, **datos):
        base = {'nombre_producto': 'Galletitas de agua', 'id_rubro': fabrica.rubro().pk,
                'precio_costo': '500', 'alicuota_iva': '21.0', 'precio_venta': '900'}
        base.update(datos)
        return self.client.post(reverse('stock:productos_crear'), base, follow=True)

    def test_con_siete_datos_queda_un_producto_completo(self):
        self.crear(codigo_barras='12 34')
        producto = Producto.objects.get()
        self.assertEqual(producto.codigo_barras, '1234')
        self.assertEqual(producto.id_marca.nombre_marca, 'Genérica')
        self.assertTrue(producto.id_proveedor.es_el_general)
        self.assertEqual((producto.tipo_producto, producto.contenido, producto.unidad_medida),
                         ('REVENTA', D('1'), 'UNIDAD'))
        self.assertEqual(producto.alicuota_iva, D('21'))
        self.assertEqual(producto.utilidad_porcentaje, D('48.76'))

    def test_el_formulario_pliega_lo_demas(self):
        pantalla = self.client.get(reverse('stock:productos_lista'))
        html = pantalla.content.decode()
        self.assertIn('Más opciones', html)
        self.assertLess(html.index('name="precio_venta"'), html.index('class="mas-opciones'))
        self.assertGreater(html.index('name="id_marca"'), html.index('class="mas-opciones'))
        self.assertIn('Generar código interno', html)
        self.assertIn('data-paso="50"', html)

    def test_el_codigo_interno_es_el_primero_libre(self):
        url = reverse('stock:codigo_interno')
        self.assertEqual(self.client.get(url).json(), {'codigo': '1000'})
        self.crear(codigo_barras='1000')
        self.crear(codigo_barras='1001', nombre_producto='Galletitas dulces')
        self.assertEqual(self.client.get(url).json(), {'codigo': '1002'})

    def test_los_reservados_no_se_renombran_ni_se_dan_de_baja(self):
        from django.core.exceptions import ValidationError
        from stock.reservados import marca_generica, proveedor_general
        marca, proveedor = marca_generica(), proveedor_general()
        self.assertTrue(marca.motivos_que_impiden_la_baja())
        self.assertTrue(proveedor.motivos_que_impiden_la_baja())
        marca.nombre_marca = 'Otra cosa'
        with self.assertRaises(ValidationError):
            marca.full_clean()
        proveedor.nombre_proveedor = 'Otro'
        with self.assertRaises(ValidationError):
            proveedor.full_clean()


class StockInicialAlImportarTest(TestCase):
    """El stock de la planilla entra por un conteo de inventario inicial (DD-89)."""

    def setUp(self):
        self.url = reverse('importar', args=['productos'])
        self.filas = [FACIL_VIRTUAL,
                      ['1001', 'CARAMELOS', 'Kiosco', 100, 0, 40, 200, '', 12, 0, 0, 'S', 'S'],
                      ['1002', 'CHICLES', 'Kiosco', 100, 0, 40, 200, '', -4, 0, 0, 'S', 'S'],
                      ['1003', 'PASTILLAS', 'Kiosco', 100, 0, 40, 200, '', '2,5', 0, 0, 'S', 'S']]

    def importar(self, usuario, **extra):
        fabrica.entrar(self.client, usuario)
        pantalla = self.client.post(self.url, {'archivo': xlsx(self.filas)}, follow=True)
        self.assertContains(pantalla, '1 producto(s) nuevos traen un stock')
        self.assertContains(pantalla, '1 con stock negativo y 1')
        return self.client.post(reverse('importar_confirmar', args=['productos']), extra, follow=True)

    def stocks(self):
        return {p.codigo_barras: p.stock_actual for p in Producto.objects.all()}

    def test_la_titular_lo_carga_y_queda_el_movimiento(self):
        from stock.models import MovimientoStock
        respuesta = self.importar(fabrica.titular(), stock_inicial='1')
        self.assertContains(respuesta, 'Se cargó el stock inicial de 1 producto')
        self.assertEqual(self.stocks(), {'1001': D('12'), '1002': D('0'), '1003': D('0')})
        movimiento = MovimientoStock.objects.get()
        self.assertEqual((movimiento.cantidad, movimiento.id_ajuste.motivo), (D('12'), 'INVENTARIO_INICIAL'))

    def test_otra_persona_lo_deja_pendiente_de_aprobacion(self):
        from stock.models import AjusteStock
        respuesta = self.importar(fabrica.usuario('30111260', 'Administrador'), stock_inicial='1')
        self.assertContains(respuesta, 'pendiente de aprobación')
        self.assertEqual(set(self.stocks().values()), {D('0')})
        self.assertEqual(AjusteStock.objects.get().estado_ajuste, 'PENDIENTE')

    def test_sin_tildar_no_se_carga_nada(self):
        from stock.models import AjusteStock
        self.importar(fabrica.titular())
        self.assertEqual(set(self.stocks().values()), {D('0')})
        self.assertFalse(AjusteStock.objects.exists())


class ElaboradosYConteoAgilTest(TestCase):
    """DD-90: lo que se prepara entra como ELABORADO; el conteo con lector."""

    def setUp(self):
        self.admin = fabrica.usuario('30111280', 'Administrador')
        fabrica.entrar(self.client, self.admin)

    def test_lo_preparado_entra_como_elaborado_y_sin_proveedor(self):
        filas = [FACIL_VIRTUAL,
                 ['501', 'TOSTADO JAMON Y QUESO', 'Comidas Elaboradas', 0, 0, 0, 3500, '', 0, 0, 0, 'N', 'S'],
                 ['502', 'PROMO CAFE + MEDIALUNA', 'Kiosco', 0, 0, 0, 2500, '', 0, 0, 0, 'N', 'S'],
                 ['503', 'CARAMELOS', 'Kiosco', 100, 0, 40, 200, '', 5, 0, 0, 'S', 'S']]
        pantalla = self.client.post(reverse('importar', args=['productos']), {'archivo': xlsx(filas)}, follow=True)
        self.assertContains(pantalla, '2 producto(s) entran como elaborados')
        self.client.post(reverse('importar_confirmar', args=['productos']), follow=True)
        tipos = {p.codigo_barras: (p.tipo_producto, p.id_proveedor_id is None) for p in Producto.objects.all()}
        self.assertEqual(tipos['501'], ('ELABORADO', True))
        self.assertEqual(tipos['502'], ('ELABORADO', True))
        self.assertEqual(tipos['503'][0], 'REVENTA')
        self.assertFalse(Producto.objects.get(codigo_barras='501').tiene_stock_propio)

    def conteo(self, **datos):
        return self.client.post(reverse('stock:conteo'), datos, follow=True)

    def test_el_lector_encuentra_suma_y_reemplaza(self):
        from stock.models import DetalleAjuste
        producto = fabrica.producto('Yerba', prov=fabrica.proveedor(), codigo_barras='7790387011457')
        self.conteo(accion='empezar', motivo='DIFERENCIA_CONTEO')
        url = reverse('stock:conteo_buscar')
        hallado = self.client.get(url, {'codigo': ' 7790387011457|'}).json()
        self.assertEqual((hallado['existe'], hallado['id'], hallado['anotado']), (True, producto.pk, None))
        self.conteo(accion='cargar', producto=producto.pk, contada='6', sumar='1')
        respuesta = self.conteo(accion='cargar', producto=producto.pk, contada='12', sumar='1')
        self.assertContains(respuesta, 'Sumado')
        self.assertEqual(DetalleAjuste.objects.get().cantidad_contada, D('18'))
        self.assertEqual(self.client.get(url, {'codigo': '7790387011457'}).json()['anotado'], '18.000')
        self.conteo(accion='cargar', producto=producto.pk, contada='7', sumar='')
        self.assertEqual(DetalleAjuste.objects.get().cantidad_contada, D('7'))
        self.assertEqual(self.client.get(url, {'codigo': '999888'}).json(), {'existe': False, 'codigo': '999888', 'valido': True})
        self.assertFalse(self.client.get(url, {'codigo': 'yerba'}).json()['valido'])

    def test_un_codigo_que_no_existe_se_da_de_alta_y_queda_contado(self):
        from stock.models import DetalleAjuste
        rubro = fabrica.rubro()
        self.conteo(accion='empezar', motivo='INVENTARIO_INICIAL')
        pantalla = self.conteo(accion='alta_rapida', codigo='7791234567898', nombre='Galletitas de salvado',
                               marca='Granix', rubro=rubro.pk, precio='1500', costo='', contada='9')
        self.assertContains(pantalla, 'Producto nuevo cargado y anotado')
        producto = Producto.objects.get(codigo_barras='7791234567898')
        self.assertEqual((producto.id_marca.nombre_marca, producto.tipo_producto), ('Granix', 'REVENTA'))
        self.assertTrue(producto.id_proveedor.es_el_general)
        self.assertEqual(DetalleAjuste.objects.get().cantidad_contada, D('9'))
        mal = self.conteo(accion='alta_rapida', codigo='7791234567899', nombre='Sin precio', rubro=rubro.pk,
                          precio='', contada='2')
        self.assertFalse(Producto.objects.filter(codigo_barras='7791234567899').exists())
        self.assertContains(mal, 'mayor que 0')


class AjusteExpressTest(TestCase):

    def setUp(self):
        self.cajera = fabrica.usuario('30111281', 'Operador')
        self.producto = fabrica.producto('Gaseosa', prov=fabrica.proveedor(), codigo_barras='4455')
        fabrica.abrir_turno(self.cajera)
        self.url = reverse('ventas:ajuste_express')

    def pedir(self, **cuerpo):
        import json
        return self.client.post(self.url, json.dumps(cuerpo), content_type='application/json').json()

    def test_la_cajera_necesita_que_otra_persona_lo_autorice(self):
        from stock.models import MovimientoStock
        fabrica.entrar(self.client, self.cajera)
        self.assertFalse(self.pedir(producto=self.producto.pk, cantidad='4')['ok'])
        encargada = fabrica.usuario('30111282', 'Administrador')
        respuesta = self.pedir(producto=self.producto.pk, cantidad='4',
                               autoriza={'usuario': encargada.nombre_usuario, 'clave': fabrica.CLAVE})
        self.assertTrue(respuesta['ok'], respuesta)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock_actual, D('4'))
        self.assertEqual(MovimientoStock.objects.count(), 1)
        self.assertFalse(self.pedir(producto=self.producto.pk, cantidad='4')['ok'])     # ya tiene stock

    def test_la_titular_lo_hace_sola(self):
        titular = fabrica.titular()
        fabrica.entrar(self.client, titular)
        self.assertTrue(self.pedir(producto=self.producto.pk, cantidad='3')['ok'])
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock_actual, D('3'))
