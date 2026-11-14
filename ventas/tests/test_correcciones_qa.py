"""Correcciones de la prueba integral del 04/11/2026 (Ventas y Stock).

    FotosVentaTest       la venta, el arqueo y el cierre dejan antes y después
    MermaTest            quien carga un ajuste no lo aprueba, salvo la dueña
    CuotasTest           recargo sólo en un plan en cuotas (Ley 25.065); las
                         cuotas sólo se muestran en la tarjeta de crédito
    DevolucionTest       se devuelve por el mismo medio y por todo lo cobrado
"""
import json
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria
from stock import servicios as stock
from stock.models import TipoAjuste
from ventas import caja
from ventas import cambios
from ventas import servicios as ventas
from ventas.models import MedioPago, MovimientoCaja

D = Decimal


def plan(nombre='Crédito 3 cuotas', cuotas=3, recargo='10'):
    return MedioPago.objects.create(
        nombre_medio_pago=nombre, es_tarjeta_credito=True,
        cantidad_cuotas=cuotas, recargo_porcentaje=D(recargo))


class Base(TestCase):

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.admin = f.usuario('40000003', 'Administrador', nombre='Ana')
        self.prov = f.proveedor()
        self.gaseosa = f.producto('Gaseosa', prov=self.prov, precio='1000', costo='500')
        f.comprar(self.encargada, self.prov, (self.gaseosa, 50, '500'))
        self.turno = f.abrir_turno(self.operador, f.caja_minisuper(), fondo='5000')

    def asiento(self, operacion):
        return LogAuditoria.objects.filter(operacion_realizada=operacion).latest('pk')


# ===========================================================================
class FotosVentaTest(Base):

    def setUp(self):
        super().setUp()
        f.entrar(self.client, self.operador)
        self.client.post(reverse('ventas:elegir_caja'), {'caja': f.caja_minisuper().pk})

    def test_la_venta_guarda_cabecera_renglones_y_pagos(self):
        respuesta = self.client.post(reverse('ventas:cobrar'), json.dumps({
            'token': uuid.uuid4().hex,
            'items': [{'producto': self.gaseosa.pk, 'cantidad': '2'}],
            'pagos': [{'medio': f.efectivo().pk}]}), content_type='application/json').json()
        self.assertTrue(respuesta['ok'], respuesta)
        asiento = self.asiento('ALTA DE VENTA')
        self.assertIsNone(asiento.registro_anterior)
        self.assertEqual(asiento.registro_nuevo['total_venta'], '2000.00')
        self.assertEqual(len(asiento.registro_nuevo['renglones']), 1)
        self.assertEqual(len(asiento.registro_nuevo['pagos']), 1)

    def test_el_arqueo_guarda_lo_declarado(self):
        token = self.client.get(reverse('ventas:caja')).context['form_arqueo'].initial['token_operacion']
        self.client.post(reverse('ventas:declarar_arqueo'),
                         {'token_operacion': token, 'monto_contado': '5000'})
        arqueo = self.asiento('ARQUEO DE TURNO DE CAJA')
        self.assertEqual(arqueo.registro_anterior['estado_turno'], 'ABIERTA')
        self.assertEqual(arqueo.registro_nuevo['arqueo_declarado']['monto_contado'], '5000')


# ===========================================================================
class MermaTest(Base):

    def merma(self, usuario):
        return stock.registrar_merma(
            usuario, 'ROTURA', [(self.gaseosa, D('1'), None)], 'Se cayó')

    def test_el_encargado_no_aprueba_su_propia_merma(self):
        ajuste = self.merma(self.encargada)
        with self.assertRaisesMessage(ValidationError, 'lo cargaste vos'):
            stock.aprobar(ajuste, self.encargada)

    def test_la_aprueba_otra_persona(self):
        ajuste = self.merma(self.encargada)
        aprobado = stock.aprobar(ajuste, self.admin)
        self.assertEqual(aprobado.id_usuario_autorizante, self.admin)

    def test_la_duena_aprueba_la_suya(self):
        duenia = f.titular()
        ajuste = self.merma(duenia)
        self.assertEqual(stock.aprobar(ajuste, duenia).estado_ajuste, 'APROBADO')

    def test_otro_administrador_no_aprueba_la_suya(self):
        ajuste = self.merma(self.admin)
        with self.assertRaisesMessage(ValidationError, 'lo cargaste vos'):
            stock.aprobar(ajuste, self.admin)

    def test_aprobar_ya_solo_para_la_duena(self):
        with self.assertRaisesMessage(ValidationError, 'lo cargaste vos'):
            stock.registrar_merma(self.encargada, 'ROTURA',
                                  [(self.gaseosa, D('1'), None)], 'Se cayó', aprobar_ya=True)
        f.entrar(self.client, self.encargada)
        pantalla = self.client.get(reverse('stock:merma'))
        self.assertFalse(pantalla.context['puede_aprobar'])
        self.assertEqual(TipoAjuste.MERMA, 'MERMA')


# ===========================================================================
class CuotasTest(Base):

    def test_un_solo_pago_no_lleva_recargo(self):
        credito = f.medio('Tarjeta de crédito')
        self.assertTrue(credito.es_tarjeta_credito)
        credito.recargo_porcentaje = D('12')
        with self.assertRaisesMessage(ValidationError, 'Ley 25.065'):
            credito.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            MedioPago.objects.filter(pk=credito.pk).update(recargo_porcentaje=D('12'))

    def test_solo_la_tarjeta_de_credito_tiene_cuotas(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            MedioPago.objects.filter(nombre_medio_pago='QR').update(cantidad_cuotas=3)
        qr = f.medio('QR')
        qr.cantidad_cuotas = 3
        qr.full_clean()                       # no es crédito: vuelve a 1
        self.assertEqual(qr.cantidad_cuotas, 1)

    def test_un_plan_en_cuotas_lleva_su_recargo(self):
        venta, _ = ventas.registrar(self.turno, self.operador,
                                    [ventas.Item(self.gaseosa, D('2'))],
                                    [ventas.Pago(plan())])
        self.assertEqual(venta.monto_recargo, D('200'))
        self.assertEqual(venta.total_venta, D('2200'))

    def test_la_lista_no_le_pone_cuotas_al_efectivo(self):
        """En la base es 1 (un solo pago); en pantalla, "1" se leía como
        "1 cuota" al lado del efectivo."""
        self.assertEqual(f.efectivo().cuotas_para_mostrar, 'No aplica')
        self.assertEqual(f.medio('Tarjeta de crédito').cuotas_para_mostrar, 'Un pago')
        self.assertEqual(plan().cuotas_para_mostrar, '3 cuotas')
        f.entrar(self.client, self.admin)
        html = self.client.get(reverse('ventas:medios_pago_lista')).content.decode()
        self.assertIn('No aplica', html)
        self.assertIn('3 cuotas', html)

    def test_la_ficha_del_efectivo_no_muestra_cuotas_ni_recargo(self):
        f.entrar(self.client, self.admin)
        ficha = self.client.get(reverse('ventas:medios_pago_ver', args=[f.efectivo().pk]))
        etiquetas = [etiqueta for etiqueta, _ in ficha.context['datos']]
        self.assertNotIn('Cuotas', etiquetas)
        self.assertNotIn('Recargo (%)', etiquetas)
        ficha = self.client.get(reverse('ventas:medios_pago_ver', args=[plan().pk]))
        self.assertIn('Cuotas', [etiqueta for etiqueta, _ in ficha.context['datos']])

    def test_las_cuotas_de_un_medio_usado_no_se_cambian(self):
        medio = plan()
        ventas.registrar(self.turno, self.operador,
                         [ventas.Item(self.gaseosa, D('1'))], [ventas.Pago(medio)])
        medio.cantidad_cuotas = 6
        with self.assertRaisesMessage(ValidationError, 'ya se usó'):
            medio.full_clean()


# ===========================================================================
class DevolucionTest(Base):

    def vender(self, medio, cantidad='2'):
        venta, _ = ventas.registrar(self.turno, self.operador,
                                    [ventas.Item(self.gaseosa, D(cantidad))],
                                    [ventas.Pago(medio)])
        return venta

    def devolver(self, venta, medio, cantidad='1'):
        renglon = venta.detalles.get()
        return cambios.registrar(
            self.turno, self.operador, venta, 'CLIENTE_ARREPENTIDO',
            [cambios.Devuelto(renglon, D(cantidad), True)],
            medio_pago=medio, autorizante=self.encargada)

    def test_se_devuelve_por_el_mismo_medio(self):
        venta = self.vender(f.medio('Transferencia'))
        with self.assertRaisesMessage(ValidationError, 'mismo medio'):
            self.devolver(venta, f.efectivo())

    def test_en_cuotas_se_devuelve_tambien_el_recargo_y_no_sale_del_cajon(self):
        medio = plan()
        venta = self.vender(medio)                      # 2000 + 10 %
        antes = caja.saldo(self.turno)
        cambio = self.devolver(venta, medio)            # 1000 + 100
        self.assertEqual(cambio.importe_devuelto, D('1100'))
        self.assertEqual(caja.saldo(self.turno), antes)
        self.assertFalse(MovimientoCaja.objects.filter(id_cambio=cambio).exists())

    def test_no_se_devuelve_mas_de_lo_cobrado_por_ese_medio(self):
        medio = plan()
        venta = self.vender(medio)
        self.devolver(venta, medio)
        cambio = self.devolver(venta, medio)
        self.assertEqual(cambio.importe_devuelto, D('1100'))
        self.assertEqual(cambios.medios_de_reintegro(venta).get(), medio)

    def test_en_efectivo_sale_del_cajon(self):
        venta = self.vender(f.efectivo())
        antes = caja.saldo(self.turno)
        self.devolver(venta, f.efectivo())
        self.assertEqual(caja.saldo(self.turno), antes - D('1000'))

    def test_la_diferencia_de_un_cambio_no_se_cobra_en_cuotas(self):
        venta = self.vender(f.efectivo(), '1')
        renglon = venta.detalles.get()
        caro = f.producto('Vino', prov=self.prov, precio='5000', costo='2000')
        f.comprar(self.encargada, self.prov, (caro, 5, '2000'), numero=99)
        with self.assertRaisesMessage(ValidationError, 'un solo pago'):
            cambios.registrar(self.turno, self.operador, venta, 'CLIENTE_ARREPENTIDO',
                              [cambios.Devuelto(renglon, D('1'), True)],
                              [cambios.Entregado(caro, D('1'))], medio_pago=plan())


# ===========================================================================
class LecturaDelRegistroTest(FotosVentaTest):
    """Los renglones y los pagos de la venta se leen como tabla, no como
    el diccionario crudo de Python (prueba del 05/11/2026)."""

    def test_los_renglones_y_pagos_se_ven_como_tabla(self):
        self.test_la_venta_guarda_cabecera_renglones_y_pagos()
        asiento = self.asiento('ALTA DE VENTA')
        f.entrar(self.client, self.admin)
        html = self.client.get(
            f"{reverse('seguridad:actividad')}{asiento.pk}/").content.decode()
        self.assertNotIn("{'", html)
        self.assertNotIn('{&#x27;', html)
        self.assertIn('subtabla-actividad', html)
        self.assertIn('$ 1.000,00', html)        # precio unitario
        self.assertIn('$ 2.000,00', html)        # total de la venta
        self.assertNotIn('2000.00', html)


class TarjetaDeInsumoTest(Base):

    def test_un_insumo_no_muestra_precio_de_venta(self):
        f.producto('Café en grano', tipo='INSUMO', prov=self.prov)
        f.entrar(self.client, self.admin)
        html = self.client.get(reverse('stock:productos_lista') + '?tipo=INSUMO').content.decode()
        self.assertIn('No se vende', html)
        self.assertNotIn('$ 0,00', html)
