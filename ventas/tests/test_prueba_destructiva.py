"""Lo que encontró la prueba destructiva del 10/11/2026, en el navegador.

Cada caso es algo que rompió el sistema o lo dejó inconsistente, y la
prueba asegura que no vuelva:

    DecimalesTest   una cantidad con más decimales de los que guarda la base
                    (0,0005 kg) tiraba un error 500 al cobrar
    StockYCajaTest  una merma de 500 con 12 en góndola se cargaba igual; el
                    rechazo de un retiro decía cuánto había en el cajón; un
                    pago podía anotarse con vencimiento de hace 5 años
"""
import json
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from nucleo.formularios import leer_numero, sobran_decimales
from nucleo.tests import fabrica as f
from ventas import servicios as ventas
from ventas.models import Venta

D = Decimal


class LecturaDeNumerosTest(SimpleTestCase):

    def test_no_acepta_mas_decimales_que_los_que_se_guardan(self):
        self.assertIsNone(leer_numero('0,0005', 3))
        self.assertIsNone(leer_numero('1500,555', 2))
        self.assertEqual(leer_numero('0,005', 3), D('0.005'))
        self.assertEqual(leer_numero('1.500', 2), D('1500'))
        self.assertTrue(sobran_decimales('0.0005', 3))
        self.assertFalse(sobran_decimales('abc', 3))

    def test_ni_notacion_cientifica_ni_numeros_que_no_entran_en_la_base(self):
        """Segunda ronda: un "recibido" de 25 cifras daba error 500."""
        self.assertIsNone(leer_numero('1e3', 2))
        self.assertIsNone(leer_numero('9' * 25, 2))
        self.assertIsNone(leer_numero('10000000000', 2))
        self.assertEqual(leer_numero('9999999999,99', 2), D('9999999999.99'))

    def test_ni_infinito_ni_nan(self):
        for texto in ('Infinity', '-inf', 'NaN', 'sNaN'):
            with self.subTest(texto=texto):
                self.assertIsNone(leer_numero(texto, 2))


class DecimalesTest(TestCase):

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        prov = f.proveedor()
        self.jamon = f.producto('Jamón cocido', prov=prov, precio='14000', costo='9800',
                                unidad='KG', fraccionable=True)
        f.comprar(self.encargada, prov, (self.jamon, '2', '9800'))
        self.turno = f.abrir_turno(self.operador, f.caja_minisuper(), fondo='5000')
        f.entrar(self.client, self.operador)
        self.client.post(reverse('ventas:elegir_caja'), {'caja': f.caja_minisuper().pk})

    def cobrar(self, cantidad, monto):
        return self.client.post(reverse('ventas:cobrar'), json.dumps({
            'token': uuid.uuid4().hex,
            'items': [{'producto': self.jamon.pk, 'cantidad': cantidad}],
            'pagos': [{'medio': f.medio('Tarjeta de débito').pk, 'monto': monto}]}),
            content_type='application/json')

    def test_medio_gramo_se_rechaza_con_un_mensaje_no_con_un_error_500(self):
        for cantidad, monto in (('0.0005', '7.00'), ('0,1234', '1727.60')):
            with self.subTest(cantidad=cantidad):
                respuesta = self.cobrar(cantidad, monto)
                self.assertEqual(respuesta.status_code, 200)
                datos = respuesta.json()
                self.assertFalse(datos['ok'])
                self.assertIn('hasta 3 decimales', datos['error'])
        self.assertFalse(Venta.objects.exists())

    def test_un_gramo_si_se_vende(self):
        datos = self.cobrar('0.001', '14.00').json()
        self.assertTrue(datos['ok'], datos)

    def test_el_servicio_tambien_lo_frena(self):
        """Defensa en profundidad: aunque llegue por otro camino que no sea
        la pantalla (importación, shell), el servicio no lo deja pasar."""
        with self.assertRaisesMessage(ValidationError, 'hasta 3 decimales'):
            ventas.cotizar([ventas.Item(self.jamon, D('0.0005'))], [])


class StockYCajaTest(TestCase):
    """Merma imposible, saldo revelado por un retiro, vencimiento mal tipeado."""

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.prov = f.proveedor()
        self.agua = f.producto('Agua mineral', prov=self.prov, precio='1500', costo='700')
        f.comprar(self.encargada, self.prov, (self.agua, '12', '700'))
        self.turno = f.abrir_turno(self.operador, f.caja_minisuper(), fondo='5000')

    def test_una_merma_mayor_que_lo_que_hay_se_rechaza_al_cargarla(self):
        from stock import servicios as stock
        with self.assertRaisesMessage(ValidationError, 'no pueden salir 500'):
            stock.registrar_merma(self.encargada, 'ROTURA', [(self.agua, D('500'), None)],
                                  detalle='Se cayó el pallet')
        stock.registrar_merma(self.encargada, 'ROTURA', [(self.agua, D('12'), None)],
                              detalle='Se cayó el pallet')

    def test_el_retiro_rechazado_no_revela_el_saldo(self):
        from ventas import caja
        with self.assertRaises(ValidationError) as error:
            caja.retiro(self.turno, self.encargada, D('99999999'), 'Depósito')
        texto = ' '.join(error.exception.messages)
        self.assertIn('No hay suficiente efectivo', texto)
        self.assertNotIn('5.000', texto)

    def test_un_vencimiento_de_hace_cinco_anios_es_un_anio_mal_tipeado(self):
        from datetime import timedelta
        from django.utils import timezone
        from compras import egresos
        from compras.models import TipoEgreso
        tipo = TipoEgreso.objects.first()
        hoy = timezone.localdate()
        with self.assertRaisesMessage(ValidationError, 'revisá el año'):
            egresos.registrar_obligacion(tipo, 'Luz de octubre', D('100'),
                                         hoy - timedelta(days=5 * 365), destinatario='EDESA')
        with self.assertRaisesMessage(ValidationError, 'revisá el año'):
            egresos.registrar_obligacion(tipo, 'Luz de octubre', D('100'),
                                         hoy + timedelta(days=5 * 365), destinatario='EDESA')
        egresos.registrar_obligacion(tipo, 'Luz de octubre', D('100'),
                                     hoy - timedelta(days=20), destinatario='EDESA')

    def test_sin_concepto_dice_que_falta_el_concepto(self):
        from django.utils import timezone
        from compras import egresos
        from compras.models import TipoEgreso
        with self.assertRaisesMessage(ValidationError, 'Escribí el concepto'):
            egresos.registrar_obligacion(TipoEgreso.objects.first(), '', D('100'),
                                         timezone.localdate(), destinatario='EDESA')


class MediosDePagoEnPantallaTest(TestCase):

    def test_el_recargo_se_lee_como_porcentaje_y_el_efectivo_no_ofrece_baja(self):
        from ventas.models import MedioPago
        admin = f.usuario('40000003', 'Administrador', nombre='Ana')
        plan = MedioPago.objects.create(nombre_medio_pago='Crédito 3 cuotas', es_tarjeta_credito=True,
                                        cantidad_cuotas=3, recargo_porcentaje=D('9.50'))
        self.assertEqual(plan.recargo_para_mostrar, '9,5 %')
        self.assertEqual(f.efectivo().recargo_para_mostrar, 'Sin recargo')
        f.entrar(self.client, admin)
        html = self.client.get(reverse('ventas:medios_pago_lista')).content.decode()
        self.assertIn('9,5 %', html)
        self.assertNotIn(f'/ventas/medios_pago/{f.efectivo().pk}/baja/', html)
        self.assertIn(f'/ventas/medios_pago/{plan.pk}/baja/', html)


class PaginadorDeComprasTest(TestCase):

    def test_el_pie_cuenta_compras_no_pesos(self):
        encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        prov = f.proveedor()
        agua = f.producto('Agua mineral', prov=prov, precio='1500', costo='700')
        f.comprar(encargada, prov, (agua, '12', '700'))
        f.entrar(self.client, encargada)
        respuesta = self.client.get(reverse('compras:compras'))
        self.assertEqual(respuesta.context['total'], 1)
        self.assertEqual(respuesta.context['total_comprado'], D('8400'))


class RecepcionTest(TestCase):
    """Una factura de 1990 entraba; un número 0 decía "ya está cargada"."""

    def setUp(self):
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.prov = f.proveedor()
        self.agua = f.producto('Agua mineral', prov=self.prov, precio='1500', costo='700')

    def recibir(self, **cambios):
        from datetime import date
        from compras import servicios as compras
        datos = dict(punto_venta=9, numero_comprobante=99991, fecha_compra=date.today(),
                     fecha_vencimiento_pago=None)
        datos.update(cambios)
        return compras.recepcionar(
            self.prov, self.encargada, 'FACTURA_A', datos['punto_venta'],
            datos['numero_comprobante'],
            [compras.RenglonRecibido(self.agua, D('5'), D('700'), [])],
            fecha_compra=datos['fecha_compra'],
            fecha_vencimiento_pago=datos['fecha_vencimiento_pago'])

    def test_cada_rechazo_dice_su_motivo(self):
        from datetime import date, timedelta
        casos = (({'fecha_compra': date(1990, 1, 1)}, 'tiene más de 30 días'),
                 ({'fecha_compra': date.today() - timedelta(days=31)}, 'tiene más de 30 días'),
                 ({'fecha_vencimiento_pago': date(2020, 1, 1)}, 'no puede vencer antes'),
                 ({'punto_venta': 0}, 'punto de venta va de 1'),
                 ({'numero_comprobante': 0}, 'número del comprobante va de 1'))
        for cambios, motivo in casos:
            with self.subTest(**{k: str(v) for k, v in cambios.items()}):
                with self.assertRaisesMessage(ValidationError, motivo):
                    self.recibir(**cambios)

    def test_el_duplicado_real_si_dice_ya_esta_cargada(self):
        self.recibir()
        with self.assertRaisesMessage(ValidationError, 'ya está cargada'):
            self.recibir()


class NombresDePersonasTest(SimpleTestCase):

    def test_un_apellido_no_lleva_numeros(self):
        from nucleo.validaciones import nombre_de_persona
        for valido in ("Pérez", "D'Angelo", 'Pérez-Gómez', 'De la Fuente', 'Müller', 'Ñandú'):
            with self.subTest(valido=valido):
                self.assertEqual(nombre_de_persona(valido), valido)
        for invalido in ('Pérez2000', 'Ana_', 'X', '<b>', 'Juan  @'):
            with self.subTest(invalido=invalido):
                with self.assertRaises(ValidationError):
                    nombre_de_persona(invalido)


class ListasAPedidoYMostradorTest(TestCase):
    """Pedido del 10/11: los maestros grandes no cargan filas hasta que se
    busca, y el mostrador abre en "Más vendidos" con Cafetería y Minisuper."""

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        prov = f.proveedor()
        self.agua = f.producto('Agua mineral', prov=prov, precio='1500', costo='700')
        self.galle = f.producto('Galletitas', prov=prov, precio='900', costo='400')
        f.comprar(self.encargada, prov, (self.agua, '12', '700'), (self.galle, '12', '400'))
        self.turno = f.abrir_turno(self.operador, f.caja_minisuper(), fondo='5000')

    def test_productos_no_consulta_hasta_que_se_pide(self):
        f.entrar(self.client, self.encargada)
        url = reverse('stock:productos_lista')
        espera = self.client.get(url)
        self.assertTrue(espera.context['esperando'])
        self.assertEqual(list(espera.context['filas']), [])
        self.assertContains(espera, 'Ver todos')
        self.assertContains(espera, 'id="modalCrear"')            # el alta sigue a mano
        self.assertEqual(len(self.client.get(url + '?todos=1').context['filas']), 2)
        self.assertEqual(len(self.client.get(url + '?busqueda=agua').context['filas']), 1)

    def test_un_catalogo_corto_se_muestra_de_una(self):
        f.entrar(self.client, self.encargada)
        respuesta = self.client.get(reverse('stock:marcas_lista'))
        self.assertFalse(respuesta.context['esperando'])

    def catalogo(self, **parametros):
        return [p['nombre'] for p in self.client.get(
            reverse('ventas:catalogo'), parametros).json()['productos']]

    def test_las_tres_pestanias_del_mostrador(self):
        f.entrar(self.client, self.operador)
        self.client.post(reverse('ventas:elegir_caja'), {'caja': f.caja_minisuper().pk})
        # Sin ventas todavía, "Más vendidos" muestra el catálogo.
        self.assertEqual(len(self.catalogo(sector='mas_vendidos')), 2)
        ventas.registrar(self.turno, self.operador, [ventas.Item(self.galle, D('3'))],
                         [ventas.Pago(f.efectivo())])
        self.assertEqual(self.catalogo(sector='mas_vendidos'), ['Galletitas'])
        self.assertEqual(len(self.catalogo(sector='minisuper')), 2)
        self.assertEqual(self.catalogo(sector='cafeteria'), [])
        # Buscar busca en todo, sin importar la pestaña.
        self.assertEqual(self.catalogo(sector='cafeteria', q='agua'), ['Agua mineral'])
        html = self.client.get(reverse('ventas:mostrador')).content.decode()
        for rotulo in ('Más vendidos', 'Cafetería', 'Minisuper'):
            self.assertIn(rotulo, html)
        self.assertNotIn('data-rubro', html)


class CantidadesEnormesTest(TestCase):

    def test_una_cantidad_gigante_se_rechaza_con_mensaje(self):
        """Segunda ronda: una producción de 9999…9 daba error 500."""
        from stock import servicios as stock
        prov = f.proveedor()
        agua = f.producto('Agua mineral', prov=prov, precio='1500', costo='700')
        with self.assertRaisesMessage(ValidationError, 'demasiado grande'):
            stock.exigir_cantidad_valida(agua, D('9' * 20))


class ParametrosRarosTest(TestCase):
    """Segunda ronda: valores absurdos en la dirección de la página."""

    def setUp(self):
        self.admin = f.usuario('40000003', 'Administrador', nombre='Ana')
        f.entrar(self.client, self.admin)

    def test_un_redondeo_que_no_es_numero_no_rompe_precios(self):
        respuesta = self.client.get(reverse('stock:precios') + '?porcentaje=10&redondeo=abc')
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('redondea', respuesta.context['error'])

    def test_el_periodo_de_un_reporte_se_acota(self):
        from django.utils import timezone
        respuesta = self.client.get(
            reverse('negocio:reportes') + '?ver=ventas_dia&desde=0001-01-01&hasta=9999-12-31')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['hasta'], timezone.localdate())
        self.assertLessEqual((respuesta.context['hasta'] - respuesta.context['desde']).days, 5 * 366)
