"""Ganancia, resumen del mes y lo que la automatización puede completar (DD-87, DD-88)."""
import io
import json
from decimal import Decimal as D

from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from compras.models import Proveedor
from negocio import ganancia, mensual
from nucleo.tests import fabrica as f
from stock.models import Producto
from stock.reservados import marca_generica, proveedor_general
from ventas import servicios as ventas

TOKEN = 'credencial-de-prueba-larga-0123456789abcdef'


class Base(TestCase):

    def setUp(self):
        self.admin = f.usuario('30111270', 'Administrador')
        self.prov = f.proveedor()
        self.alfajor = f.producto('Alfajor', prov=self.prov, precio='1000', costo='600', codigo_barras='7790895000997')
        f.comprar(self.admin, self.prov, (self.alfajor, 20, '600'))
        self.turno = f.abrir_turno(self.admin)

    def vender(self, cantidad='2'):
        return ventas.registrar(self.turno, self.admin, [ventas.Item(self.alfajor, D(cantidad))],
                                [ventas.Pago(f.efectivo())])[0]


class GananciaTest(Base):

    def test_la_cuenta_y_los_dos_porcentajes(self):
        self.vender('2')
        venta = self.vender('1')
        cuenta = ganancia.de_ventas(venta.__class__.objects.all())
        self.assertEqual((cuenta['facturado'], cuenta['costo'], cuenta['ganancia']),
                         (D('3000'), D('1800'), D('1200')))
        self.assertEqual((cuenta['margen'], cuenta['rentabilidad']), (D('66.7'), D('40.0')))
        self.assertEqual(ganancia.por_medio_de_pago(venta.__class__.objects.all())[0][1], D('3000'))

    def test_la_pantalla_de_ventas_la_muestra_solo_a_quien_ve_reportes(self):
        self.vender('2')
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(reverse('ventas:ventas'))
        self.assertContains(pantalla, 'Ganancia bruta')
        self.assertEqual(pantalla.context['ganancia']['ganancia'], D('800'))
        self.assertContains(pantalla, 'Esta semana')
        cajero = f.usuario('30111271', 'Operador')
        f.entrar(self.client, cajero)
        if self.client.get(reverse('ventas:ventas')).status_code == 200:
            self.assertNotContains(self.client.get(reverse('ventas:ventas')), 'Ganancia bruta')
        self.assertEqual(self.client.get(reverse('ventas:exportar_renglones')).status_code, 302)

    def test_el_excel_de_renglones_con_ganancia(self):
        self.vender('2')
        f.entrar(self.client, self.admin)
        excel = self.client.get(reverse('ventas:exportar_renglones'))
        filas = [fila for fila in load_workbook(io.BytesIO(excel.content)).active.iter_rows(values_only=True)]
        encabezado = next(i for i, fila in enumerate(filas) if fila and fila[0] == 'Fecha')
        self.assertEqual(list(filas[encabezado]), [
            'Fecha', 'Hora', 'Ticket', 'Caja', 'Código', 'Descripción', 'Rubro', 'Cantidad',
            'Unitario', 'Subtotal', 'Costo', '% Margen', 'Ganancia'])
        renglon = filas[encabezado + 1]
        self.assertEqual((renglon[4], renglon[9], renglon[10], renglon[12]), ('7790895000997', 2000, 1200, 800))

    def test_el_resumen_del_mes(self):
        venta = self.vender('2')
        cuenta = mensual.del_mes(venta.fecha_venta.year, venta.fecha_venta.month)
        self.assertEqual(cuenta['total_entro'], D('2000'))
        self.assertEqual(cuenta['resultado'], D('800'))
        # La factura de la compra del setUp está impaga: figura en "a pagar".
        self.assertEqual(cuenta['total_a_pagar'], D('12000'))
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(reverse('negocio:resumen_del_mes'))
        self.assertContains(pantalla, 'Resultado del mes')
        self.assertEqual(self.client.get(reverse('negocio:resumen_del_mes'), {'mes': 'cualquiera'}).status_code, 200)


class VincularAlRecibirTest(Base):

    def test_el_que_no_tenia_proveedor_pasa_al_de_la_compra_y_el_que_tenia_no(self):
        suelto = f.producto('Cerveza', prov=proveedor_general(), costo='900')
        otro = Proveedor.objects.create(nombre_proveedor='Vinería del Centro', cuit_proveedor='20-12345678-6')
        f.comprar(self.admin, otro, (suelto, 6, '900'), (self.alfajor, 2, '600'))
        suelto.refresh_from_db()
        self.alfajor.refresh_from_db()
        self.assertEqual(suelto.id_proveedor, otro)
        self.assertEqual(self.alfajor.id_proveedor, self.prov)

    def test_sin_tildar_no_se_vincula(self):
        from compras import servicios as compras
        suelto = f.producto('Cerveza', prov=proveedor_general(), costo='900')
        compras.recepcionar(self.prov, self.admin, 'FACTURA_B', 1, 9001,
                            [compras.RenglonRecibido(suelto, D('6'), D('900'))], vincular_sin_proveedor=False)
        suelto.refresh_from_db()
        self.assertTrue(suelto.id_proveedor.es_el_general)


@override_settings(CAFETO_N8N_TOKEN=TOKEN)
class AutomatizacionTest(Base):

    def pedir(self, nombre, *args, cuerpo=None):
        url = reverse(f'api_servicio:{nombre}', args=args)
        cabeceras = {'HTTP_AUTHORIZATION': f'Bearer {TOKEN}'}
        if cuerpo is None:
            return self.client.get(url, **cabeceras)
        return self.client.post(url, json.dumps(cuerpo), content_type='application/json', **cabeceras)

    def test_el_resumen_trae_ganancia_medios_y_vencimientos(self):
        self.vender('2')
        datos = self.pedir('resumen').json()
        self.assertEqual(datos['ganancia_bruta_hoy'], '800.00')
        self.assertEqual(datos['desglose_medios_pago_hoy'][0]['total'], '2000.00')
        self.assertIsInstance(datos['vencimientos_proximos'], list)

    def test_la_marca_solo_reemplaza_a_la_generica(self):
        generico = f.producto('Galletitas de agua', prov=self.prov, marca_nombre='Genérica', codigo_barras='1234')
        self.assertEqual(self.pedir('productos_sin_marca').json()['productos'][0]['id'], generico.pk)
        respuesta = self.pedir('enriquecer_producto', generico.pk, cuerpo={'marca': '  arcor '}).json()
        self.assertEqual((respuesta['marca'], respuesta['actualizado']), ('arcor', ['marca']))
        otra = self.pedir('enriquecer_producto', generico.pk, cuerpo={'marca': 'Bagley'}).json()
        self.assertEqual(otra['marca'], 'arcor')
        self.assertIn('marca', otra['omitido'])
        self.assertEqual(self.pedir('enriquecer_producto', generico.pk, cuerpo={}).status_code, 400)

    def test_la_marca_no_se_pone_si_lo_dejaria_igual_a_otro(self):
        f.producto('Galletitas de agua', prov=self.prov, marca_nombre='Arcor')
        generico = f.producto('Galletitas de agua', prov=self.prov, marca_nombre='Genérica')
        respuesta = self.pedir('enriquecer_producto', generico.pk, cuerpo={'marca': 'Arcor'}).json()
        self.assertEqual(respuesta['actualizado'], [])
        generico.refresh_from_db()
        self.assertEqual(generico.id_marca, marca_generica())

    def test_la_factura_leida_queda_pre_cargada_y_no_registrada(self):
        from compras.models import Compra
        antes = Compra.objects.count()
        respuesta = self.pedir('precargar_compra', cuerpo={
            'cuit_proveedor': '30712345671', 'nombre_proveedor': 'Distribuidora Norte',
            'tipo_comprobante': 'FACTURA_B', 'punto_venta': '3', 'numero': '4521',
            'renglones': [
                {'codigo_barras': '7790895000997|', 'descripcion': 'ALFAJOR', 'cantidad': 12, 'costo_unitario': 650},
                {'codigo_barras': '000', 'descripcion': 'ALGO QUE NO ESTÁ', 'cantidad': 1, 'costo_unitario': 10}]})
        self.assertEqual(respuesta.status_code, 201)
        datos = respuesta.json()
        self.assertEqual((datos['proveedor_reconocido'], datos['renglones_reconocidos'], datos['registrada']),
                         (True, 1, False))
        self.assertEqual(len(datos['renglones_sin_reconocer']), 1)
        self.assertEqual(Compra.objects.count(), antes)
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(datos['url'])
        self.assertContains(pantalla, 'la pre-cargó una automatización')
        self.assertContains(pantalla, 'ALGO QUE NO ESTÁ')
        renglones = json.loads(pantalla.context['renglones_json'])
        self.assertEqual((renglones[0]['producto'], renglones[0]['cantidad'], renglones[0]['costo']),
                         (self.alfajor.pk, '12', '650.00'))
        self.assertEqual(pantalla.context['datos']['proveedor'], str(self.prov.pk))
        alterado = self.client.get(reverse('compras:recepcion'), {'precarga': 'cualquier-cosa'}, follow=True)
        self.assertContains(alterado, 'venció o no es válido')
        self.assertEqual(self.pedir('precargar_compra', cuerpo={'renglones': []}).status_code, 400)


class EtiquetasYMostradorTest(Base):

    def test_las_etiquetas_de_gondola(self):
        f.entrar(self.client, self.admin)
        url = reverse('stock:etiquetas_gondola')
        self.assertContains(self.client.get(url), 'Elegí un rubro')
        pantalla = self.client.get(url, {'q': 'alfa'})
        self.assertContains(pantalla, 'etiqueta-gondola')
        self.assertContains(pantalla, '7790895000997')
        self.assertContains(pantalla, 'Imprimir 1 etiqueta')
        self.assertEqual(len(self.client.get(url, {'cambios': '1'}).context['productos']), 1)
        self.assertContains(self.client.get(url, {'q': 'no existe'}), 'No hay productos')

    def test_el_mostrador_trae_el_visor_y_los_atajos(self):
        from django.template.loader import get_template
        fuente = get_template('ventas/mostrador.html').template.source
        for texto in ('visor-vuelto', "e.key === 'F9'", "e.key === 'F4'", "e.key === 'F2'",
                      # Cobrar confirma con el resumen; después, imprimir o no, en grande (DD-101).
                      '¿Confirmamos el cobro?', 'Imprimir ticket <kbd>P</kbd>',
                      'No imprimir · Nueva venta <kbd>Enter</kbd>',
                      'El ticket queda guardado en Ventas'):
            self.assertIn(texto, fuente)
        self.assertNotIn('id="autoTicket"', fuente)

    def test_el_lector_con_basura_encuentra_el_producto(self):
        f.entrar(self.client, self.admin)
        for leido in ('7790895000997|', '77908950009977790895000997'):
            datos = self.client.get(reverse('ventas:catalogo'), {'q': leido}).json()
            self.assertEqual(len(datos['productos']), 1, leido)


class ProveedorHabitualTest(Base):
    """DD-89: el habitual cambia sólo cuando las compras cambiaron de verdad."""

    def setUp(self):
        super().setUp()
        self.vineria = Proveedor.objects.create(nombre_proveedor='Vinería del Centro', cuit_proveedor='20-12345678-6')

    def habitual(self):
        self.alfajor.refresh_from_db()
        return self.alfajor.id_proveedor

    def test_una_compra_aislada_no_lo_cambia_y_la_tercera_seguida_si(self):
        f.comprar(self.admin, self.vineria, (self.alfajor, 2, '600'))
        f.comprar(self.admin, self.vineria, (self.alfajor, 2, '600'))
        self.assertEqual(self.habitual(), self.prov)
        self.alfajor.refresh_from_db()
        self.assertEqual(self.alfajor.stock_actual, D('24'))
        self.assertEqual(Producto.objects.filter(nombre_producto='Alfajor').count(), 1)
        f.comprar(self.admin, self.vineria, (self.alfajor, 2, '600'))
        self.assertEqual(self.habitual(), self.vineria)

    def test_una_compra_al_habitual_en_el_medio_corta_la_racha(self):
        f.comprar(self.admin, self.vineria, (self.alfajor, 2, '600'))
        f.comprar(self.admin, self.vineria, (self.alfajor, 2, '600'))
        f.comprar(self.admin, self.prov, (self.alfajor, 2, '600'))
        f.comprar(self.admin, self.vineria, (self.alfajor, 2, '600'))
        self.assertEqual(self.habitual(), self.prov)

    def test_si_al_habitual_no_se_le_compra_hace_seis_meses_cambia_en_la_primera(self):
        from datetime import timedelta
        from compras.models import Compra
        from django.utils import timezone
        Compra.objects.update(fecha_compra=timezone.localdate() - timedelta(days=200))
        f.comprar(self.admin, self.vineria, (self.alfajor, 2, '600'))
        self.assertEqual(self.habitual(), self.vineria)

    def test_un_habitual_elegido_a_mano_al_que_nunca_se_le_compro_no_es_inactivo(self):
        nuevo = f.producto('Cerveza', prov=self.prov, costo='900')
        f.comprar(self.admin, self.vineria, (nuevo, 6, '900'))
        nuevo.refresh_from_db()
        self.assertEqual(nuevo.id_proveedor, self.prov)

    def test_los_inactivos_y_la_reasignacion(self):
        from datetime import timedelta
        from compras import habitual
        from compras.models import Compra, Egreso
        from django.utils import timezone
        viejo = timezone.localdate() - timedelta(days=200)
        self.assertNotIn(self.prov, habitual.inactivos())
        Compra.objects.update(fecha_compra=viejo, estado_compra='PAGADA')
        Proveedor.objects.update(fecha_alta=viejo)
        self.assertIn(self.prov, habitual.inactivos())
        self.assertEqual(habitual.reasignar_los_de_inactivos(), [])       # tiene stock y nadie más se lo vendió
        self.alfajor.__class__.objects.filter(pk=self.alfajor.pk).update(stock_actual=0)
        cambios = habitual.reasignar_los_de_inactivos()
        self.assertEqual(len(cambios), 1)
        self.assertTrue(self.habitual().es_el_general)


@override_settings(CAFETO_N8N_TOKEN=TOKEN)
class PrecargaConProveedorNuevoTest(Base):

    def test_el_proveedor_y_la_marca_se_cargan_al_confirmar(self):
        Producto.objects.filter(pk=self.alfajor.pk).update(id_marca=marca_generica())
        respuesta = self.client.post(
            reverse('api_servicio:precargar_compra'), json.dumps({
                'cuit_proveedor': '20-12345678-6', 'nombre_proveedor': 'Vinería del Centro',
                'tipo_comprobante': 'FACTURA_B', 'punto_venta': '2', 'numero': '77',
                'renglones': [{'codigo_barras': '7790895000997', 'cantidad': 6, 'costo_unitario': 650,
                               'marca_detectada': 'Havanna'}]}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {TOKEN}').json()
        self.assertFalse(respuesta['proveedor_reconocido'])
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(respuesta['url'])
        self.assertContains(pantalla, 'Dar de alta a este proveedor')
        self.assertContains(pantalla, '20-12345678-6')
        self.assertFalse(Proveedor.objects.filter(cuit_proveedor='20-12345678-6').exists())
        testigo = pantalla.context['testigo']
        guardar = self.client.post(reverse('compras:recepcion'), {
            'precarga': testigo, 'alta_proveedor': '1', 'proveedor': '', 'tipo_comprobante': 'FACTURA_B',
            'punto_venta': '2', 'numero': '77', 'vincular': '1', 'otros': '0',
            'token_operacion': pantalla.context['token'],
            'renglones': pantalla.context['renglones_json']}, follow=True)
        self.assertContains(guardar, 'Se dio de alta al proveedor', msg_prefix=str(guardar.context.get('error')))
        vineria = Proveedor.objects.get(cuit_proveedor='20-12345678-6')
        self.alfajor.refresh_from_db()
        self.assertEqual(self.alfajor.id_marca.nombre_marca, 'Havanna')
        self.assertEqual(self.alfajor.stock_actual, D('26'))
        self.assertEqual(vineria.compras.count(), 1)


@override_settings(CAFETO_N8N_TOKEN=TOKEN)
class MantenimientoMensualTest(Base):

    def test_archiva_ordena_proveedores_y_exige_la_credencial(self):
        url = reverse('api_servicio:mantenimiento_mensual')
        self.assertEqual(self.client.post(url).status_code, 401)
        self.assertEqual(self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {TOKEN}').status_code, 405)
        simulado = self.client.post(url + '?simular=1', HTTP_AUTHORIZATION=f'Bearer {TOKEN}').json()
        self.assertTrue(simulado['simulacion'])
        datos = self.client.post(url, HTTP_AUTHORIZATION=f'Bearer {TOKEN}').json()
        self.assertEqual((datos['simulacion'], datos['filas_al_historico'], datos['cantidad_reasignados']),
                         (False, 0, 0))
        self.assertEqual(datos['proveedores_inactivos_6m'], [])
        resumen = self.client.get(reverse('api_servicio:resumen'), HTTP_AUTHORIZATION=f'Bearer {TOKEN}').json()
        self.assertEqual(resumen['proveedores_inactivos_6m'], [])


class LoCobradoCierraConLoVendidoTest(Base):

    def test_el_recargo_de_la_tarjeta_esta_en_los_dos_lados(self):
        from ventas.models import MedioPago
        credito = MedioPago.objects.create(nombre_medio_pago='Crédito 3 cuotas', recargo_porcentaje=D('9'),
                                           afecta_caja=False, es_tarjeta_credito=True, cantidad_cuotas=3)
        venta = ventas.registrar(self.turno, self.admin, [ventas.Item(self.alfajor, D('3'))],
                                 [ventas.Pago(credito)])[0]
        self.vender('1')
        self.assertEqual(venta.total_venta, D('3270'))
        cuenta = mensual.del_mes(venta.fecha_venta.year, venta.fecha_venta.month)
        self.assertEqual(cuenta['total_entro'], cuenta['bruta']['facturado'])
        self.assertEqual((cuenta['total_entro'], cuenta['recargos']), (D('4270'), D('270')))
