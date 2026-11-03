"""Panel de inicio, parámetros, reportes, importación y precios."""
import io
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from compras.models import Proveedor
from negocio.models import ParametrosNegocio
from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria
from stock.models import Marca, Producto

D = Decimal


def planilla(filas):
    from openpyxl import Workbook
    libro = Workbook()
    for fila in filas:
        libro.active.append(fila)
    salida = io.BytesIO()
    libro.save(salida)
    return SimpleUploadedFile('lista.xlsx', salida.getvalue())


class Base(TestCase):

    def setUp(self):
        self.admin = f.usuario('30000001', 'Administrador', nombre='Lucre')
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.prov = f.proveedor()


class InicioTest(Base):

    def test_cada_perfil_ve_lo_suyo(self):
        f.entrar(self.client, self.operador)
        respuesta = self.client.get(reverse('home'))
        self.assertContains(respuesta, 'Hola, Cami')
        self.assertNotContains(respuesta, 'Vendido hoy')
        self.client.logout()
        f.entrar(self.client, self.admin)
        self.assertContains(self.client.get(reverse('home')), 'Vendido hoy')


class ParametrosTest(Base):

    def test_solo_el_administrador_y_queda_auditado(self):
        f.entrar(self.client, self.operador)
        self.assertRedirects(self.client.get(reverse('negocio:parametros')), reverse('home'),
                             fetch_redirect_response=False)
        self.client.logout()
        f.entrar(self.client, self.admin)
        datos = {'nombre_fantasia': 'Cafeto', 'razon_social': 'Cafeto Las Lomitas',
                 'condicion_iva': 'MONOTRIBUTO', 'redondeo_ventas': '1.00',
                 'tolerancia_arqueo': '1.000', 'fondo_caja_sugerido': '15.000',
                 'dias_para_cambios': '15', 'dias_aviso_vencimiento': '5'}
        self.client.post(reverse('negocio:parametros'), datos)
        parametros = ParametrosNegocio.vigentes()
        self.assertEqual(parametros.tolerancia_arqueo, D('1000'))
        self.assertEqual(parametros.dias_para_cambios, 15)
        self.assertTrue(LogAuditoria.objects.filter(operacion_realizada__startswith='MODIFICACION DE PARAMETROS').exists())


class ReportesTest(Base):

    def test_todos_los_reportes_cargan_y_exportan(self):
        from negocio.reportes import REPORTES
        from ventas import servicios
        alfajor = f.producto('Alfajor', prov=self.prov, precio='1200')
        f.comprar(self.admin, self.prov, (alfajor, 5, '700'))
        turno = f.abrir_turno(self.admin)
        servicios.registrar(turno, self.admin, [servicios.Item(alfajor, D('2'))],
                            [servicios.Pago(f.efectivo())])
        f.entrar(self.client, self.admin)
        for reporte in REPORTES:
            with self.subTest(reporte=reporte.clave):
                self.assertEqual(self.client.get(reverse('negocio:reportes'), {'ver': reporte.clave}).status_code, 200)
        productos = self.client.get(reverse('negocio:reportes'), {'ver': 'productos'})
        self.assertEqual(productos.context['totales'][4], D('1000'))     # ganancia: 2 × (1200 − 700)
        excel = self.client.get(reverse('negocio:exportar_reporte'), {'ver': 'productos', 'formato': 'xlsx'})
        self.assertIn('spreadsheetml', excel['Content-Type'])


class ImportarProductosTest(Base):

    def subir(self, filas):
        return self.client.post(reverse('importar', args=['productos']),
                                {'archivo': planilla(filas)}, follow=True)

    def test_vista_previa_y_todo_o_nada(self):
        f.entrar(self.client, self.admin)
        respuesta = self.subir([
            ['Nombre', 'Marca', 'Rubro', 'Precio de venta', 'Proveedor'],
            ['Alfajor', 'Havanna', 'minisuper', '1.500', '30-71234567-1'],
            ['Turrón', 'Arcor', 'Rubro que no existe', '500', '30-71234567-1'],
        ])
        self.assertEqual(respuesta.context['errores'], 1)
        self.assertContains(respuesta, 'no existe')
        self.assertNotContains(respuesta, 'Confirmar la importación')
        self.client.post(reverse('importar_confirmar', args=['productos']))
        self.assertFalse(Producto.objects.exists())

    def test_alta_y_despues_actualizacion_de_precio(self):
        f.entrar(self.client, self.admin)
        filas = [['Producto', 'Marca', 'Rubro', 'Precio', 'Proveedor'],
                 ['Alfajor', 'Havanna', 'Minisuper', '1500', 'Distribuidora Norte']]
        self.subir(filas)
        self.client.post(reverse('importar_confirmar', args=['productos']))
        producto = Producto.objects.get()
        self.assertEqual(producto.precio_venta, D('1500'))
        self.assertTrue(Marca.objects.filter(nombre_marca='Havanna').exists())
        filas[1][3] = '1800'
        respuesta = self.subir(filas)
        self.assertEqual(respuesta.context['cambios'], 1)
        self.client.post(reverse('importar_confirmar', args=['productos']))
        producto.refresh_from_db()
        self.assertEqual(producto.precio_venta, D('1800'))
        self.assertEqual(Producto.objects.count(), 1)

    def test_faltan_columnas_obligatorias(self):
        f.entrar(self.client, self.admin)
        respuesta = self.subir([['Nombre', 'Precio'], ['Alfajor', '10']])
        self.assertContains(respuesta, 'faltan columnas obligatorias')

    def test_la_planilla_modelo(self):
        f.entrar(self.client, self.admin)
        respuesta = self.client.get(reverse('importar_modelo', args=['productos']))
        self.assertIn('spreadsheetml', respuesta['Content-Type'])

    def test_el_operador_no_importa(self):
        f.entrar(self.client, self.operador)
        respuesta = self.client.get(reverse('importar', args=['productos']))
        self.assertRedirects(respuesta, reverse('home'), fetch_redirect_response=False)


class ImportarProveedoresTest(Base):

    def test_por_cuit(self):
        f.entrar(self.client, self.admin)
        self.client.post(reverse('importar', args=['proveedores']), {'archivo': planilla([
            ['Razón social', 'CUIT', 'Teléfono', 'Localidad'],
            ['Distribuidora Norte', '30712345671', '(0387) 412-3456', 'Salta Capital'],
            ['Panificadora Sur', '20-12345678-6', '', ''],
        ])})
        self.client.post(reverse('importar_confirmar', args=['proveedores']))
        self.prov.refresh_from_db()
        self.assertEqual(self.prov.telefono_proveedor, '3874123456')
        self.assertEqual(Proveedor.objects.count(), 2)


class PreciosTest(Base):

    def test_vista_previa_y_aplicacion(self):
        a = f.producto('Alfajor', prov=self.prov, precio='1000')
        f.entrar(self.client, self.admin)
        previa = self.client.get(reverse('stock:precios'), {'porcentaje': '8', 'redondeo': '50'})
        self.assertEqual(previa.context['filas'][0][2], D('1100'))
        a.refresh_from_db()
        self.assertEqual(a.precio_venta, D('1000'))
        self.client.post(reverse('stock:precios'), {'porcentaje': '8', 'redondeo': '50',
                                                    'token_operacion': previa.context['token']})
        a.refresh_from_db()
        self.assertEqual(a.precio_venta, D('1100'))
