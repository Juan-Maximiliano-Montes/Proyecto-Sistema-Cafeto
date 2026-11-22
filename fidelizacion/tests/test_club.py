"""Club Cafeto (DFD 9 y DFD 4 bloque C): de punta a punta.

    ClienteTest          alta con QR y categoría de ingreso, identificación
    AcumulacionTest      puntos por compra y ascenso de categoría
    BeneficiosTest       descuento automático, canjes y regalos
    DevolucionTest       reverso proporcional de puntos y reintegro justo
    PantallasTest        mostrador, alta rápida, ABM y reportes
"""
import json
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from fidelizacion.forms import ClienteForm
from fidelizacion.models import (
    Beneficio,
    BeneficioProducto,
    CategoriaFidelizacion,
    Cliente,
    MovimientoPuntos,
)
from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria
from ventas import cambios
from ventas import servicios as ventas

D = Decimal


def categoria(nombre):
    return CategoriaFidelizacion.objects.get(nombre_categoria=nombre)


def beneficio(cat, descripcion, tipo, modo, porcentaje=None, puntos=None,
              receta=()):
    nuevo = Beneficio(id_categoria=categoria(cat),
                      descripcion_beneficio=descripcion, tipo_beneficio=tipo,
                      modo_entrega=modo, porcentaje_descuento=porcentaje,
                      puntos_requeridos=puntos)
    nuevo.full_clean()
    nuevo.save()
    for producto, cantidad in receta:
        BeneficioProducto.objects.create(id_beneficio=nuevo,
                                         id_producto=producto,
                                         cantidad=cantidad)
    return nuevo


def cliente(apellido='López', nombre='Sofía', telefono='3874123456',
            dni=None):
    formulario = ClienteForm({'apellido_cliente': apellido,
                              'nombre_cliente': nombre,
                              'telefono_cliente': telefono,
                              'dni_cliente': dni or ''})
    assert formulario.is_valid(), formulario.errors
    return formulario.save()


class Base(TestCase):

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.prov = f.proveedor()
        self.alfajor = f.producto('Alfajor', prov=self.prov, precio='1000')
        self.cafe = f.producto('Café en lata', prov=self.prov, precio='2000')
        f.comprar(self.encargada, self.prov, (self.alfajor, 50, '500'),
                  (self.cafe, 20, '900'))
        self.turno = f.abrir_turno(self.operador)
        self.sofia = cliente()

    def vender(self, *items, beneficios=(), cliente_=None, pagos=None):
        lista = [ventas.Item(p, D(str(c)), *resto) for p, c, *resto in items]
        return ventas.registrar(
            self.turno, self.operador, lista,
            pagos if pagos is not None else [ventas.Pago(f.efectivo())],
            cliente=cliente_ if cliente_ is not None else self.sofia,
            beneficios=beneficios)

    def refrescar(self):
        self.sofia.refresh_from_db()
        return self.sofia


# ===========================================================================
class ClienteTest(Base):

    def test_el_alta_le_da_la_categoria_de_ingreso_y_un_qr(self):
        self.assertEqual(self.sofia.id_categoria.puntos_minimos, 0)
        self.assertTrue(self.sofia.qr_cliente.startswith('CAF-'))
        self.assertEqual(len(self.sofia.qr_cliente), 14)
        otro = cliente('Díaz', 'Carlos', '3874999999')
        self.assertNotEqual(otro.qr_cliente, self.sofia.qr_cliente)

    def test_el_dni_se_guarda_sin_puntos(self):
        carlos = cliente('Díaz', 'Carlos', '', dni='40.125.874')
        self.assertEqual(carlos.dni_cliente, '40125874')

    def test_hace_falta_telefono_o_dni(self):
        formulario = ClienteForm({'apellido_cliente': 'Ruiz',
                                  'nombre_cliente': 'Ana'})
        self.assertFalse(formulario.is_valid())
        self.assertIn('teléfono o el DNI', str(formulario.errors))

    def test_el_mismo_telefono_no_se_registra_dos_veces(self):
        formulario = ClienteForm({'apellido_cliente': 'Otra',
                                  'nombre_cliente': 'Persona',
                                  'telefono_cliente': '(0387) 412-3456'})
        self.assertFalse(formulario.is_valid())
        self.assertIn('ya existe', str(formulario.errors))

    def test_se_identifica_por_qr_telefono_o_dni(self):
        carlos = cliente('Díaz', 'Carlos', '3874999999', dni='40125874')
        self.assertEqual(Cliente.identificar(self.sofia.qr_cliente), self.sofia)
        self.assertEqual(Cliente.identificar('0387 15 412-3456'), self.sofia)
        self.assertEqual(Cliente.identificar('40.125.874'), carlos)
        self.assertIsNone(Cliente.identificar('nadie'))

    def test_el_dado_de_baja_no_se_identifica(self):
        Cliente.objects.filter(pk=self.sofia.pk).update(borrado_logico=True)
        self.assertIsNone(Cliente.identificar(self.sofia.qr_cliente))


# ===========================================================================
class AcumulacionTest(Base):

    def test_un_punto_cada_cien_pesos_de_la_categoria(self):
        venta, cotizacion = self.vender((self.alfajor, 3))   # $3.000 / 100
        self.assertEqual(cotizacion.resultado_puntos.puntos, 30)
        self.assertEqual(self.refrescar().puntos_disponibles, 30)
        self.assertEqual(venta.id_cliente, self.sofia)

    def test_al_pasar_el_umbral_sube_de_categoria(self):
        self.vender((self.cafe, 10))          # $20.000 -> 200 puntos
        self.vender((self.alfajor, 50))       # $50.000 -> 500
        _, cotizacion = self.vender((self.cafe, 10))   # 200 más: Silver es 1000
        self.assertEqual(self.refrescar().id_categoria.nombre_categoria, 'Bronze')
        f.comprar(self.encargada, self.prov, (self.cafe, 20, '900'))
        _, cotizacion = self.vender((self.cafe, 10))
        self.assertTrue(cotizacion.resultado_puntos.ascendio)
        self.assertEqual(self.refrescar().id_categoria.nombre_categoria, 'Silver')

    def test_sin_cliente_no_hay_puntos(self):
        venta, _ = ventas.registrar(self.turno, self.operador,
                                    [ventas.Item(self.alfajor, D('1'))],
                                    [ventas.Pago(f.efectivo())])
        self.assertFalse(MovimientoPuntos.objects.filter(id_venta=venta).exists())


# ===========================================================================
class BeneficiosTest(Base):

    def test_el_descuento_automatico_va_sobre_lo_que_no_tiene_otro(self):
        beneficio('Bronze', '5% para todo el club', 'AUTOMATICO', 'DESCUENTO',
                  porcentaje=D('5'))
        venta, cotizacion = self.vender(
            (self.alfajor, 2),                               # $2.000 sin descuento
            (self.cafe, 1, D('10'), 'PROMOCION'))            # ya tiene el 10 %
        self.assertEqual(venta.monto_descuento, D('100.00'))   # 5 % de 2.000
        self.assertEqual(venta.id_beneficio.descripcion_beneficio,
                         '5% para todo el club')
        self.assertEqual(venta.total_venta,
                         venta.subtotal - venta.monto_descuento
                         + venta.ajuste_redondeo + venta.monto_recargo)

    def test_el_canje_de_descuento_cuesta_puntos(self):
        canjeable = beneficio('Bronze', '20% canjeando', 'CANJEABLE',
                              'DESCUENTO', porcentaje=D('20'), puntos=50)
        with self.assertRaisesMessage(ValidationError, 'Puntos insuficientes'):
            self.vender((self.alfajor, 1), beneficios=[canjeable.pk])
        self.vender((self.alfajor, 10))                       # 100 puntos
        venta, cotizacion = self.vender((self.alfajor, 5),
                                        beneficios=[canjeable.pk])
        self.assertEqual(venta.monto_descuento, D('1000.00'))
        sofia = self.refrescar()
        # 100 - 50 del canje + 40 de esta compra ($4.000)
        self.assertEqual(sofia.puntos_disponibles, 90)
        self.assertEqual(sofia.puntos_acumulados, 140)   # el canje no baja el mérito

    def test_un_canje_peor_que_el_automatico_no_se_acepta(self):
        beneficio('Bronze', '10% del club', 'AUTOMATICO', 'DESCUENTO',
                  porcentaje=D('10'))
        poco = beneficio('Bronze', '5% canjeando', 'CANJEABLE', 'DESCUENTO',
                         porcentaje=D('5'), puntos=1)
        self.vender((self.alfajor, 1))
        with self.assertRaisesMessage(ValidationError, 'no le suma'):
            self.vender((self.alfajor, 1), beneficios=[poco.pk])

    def test_el_regalo_en_especie_sale_del_stock_y_no_se_cobra(self):
        regalo = beneficio('Bronze', 'Un café de regalo', 'CANJEABLE',
                           'ESPECIE', puntos=20, receta=[(self.cafe, 1)])
        self.vender((self.alfajor, 2))                     # 20 puntos
        venta, _ = self.vender((self.alfajor, 1), beneficios=[regalo.pk])
        renglon = venta.detalles.get(id_beneficio=regalo)
        self.assertEqual(renglon.subtotal, 0)
        self.assertEqual(renglon.motivo_descuento, 'BENEFICIO')
        self.assertEqual(venta.total_venta, D('1000'))
        self.cafe.refresh_from_db()
        self.assertEqual(self.cafe.stock_actual, 19)
        self.assertTrue(MovimientoPuntos.objects.filter(
            id_venta=venta, tipo_movimiento='CANJE', puntos=-20).exists())

    def test_el_regalo_automatico_no_cuesta_puntos(self):
        regalo = beneficio('Bronze', 'Alfajor de bienvenida', 'AUTOMATICO',
                           'ESPECIE', receta=[(self.alfajor, 1)])
        venta, _ = self.vender((self.cafe, 1), beneficios=[regalo.pk])
        self.assertEqual(venta.detalles.filter(id_beneficio=regalo).count(), 1)
        self.assertFalse(MovimientoPuntos.objects.filter(
            id_venta=venta, tipo_movimiento='CANJE').exists())

    def test_no_se_canjea_lo_de_una_categoria_superior(self):
        de_oro = beneficio('Gold', 'Café gold', 'CANJEABLE', 'ESPECIE',
                           puntos=1, receta=[(self.cafe, 1)])
        self.vender((self.alfajor, 1))
        with self.assertRaisesMessage(ValidationError, 'no está disponible'):
            self.vender((self.alfajor, 1), beneficios=[de_oro.pk])

    def test_sin_cliente_no_hay_beneficio(self):
        regalo = beneficio('Bronze', 'Alfajor de bienvenida', 'AUTOMATICO',
                           'ESPECIE', receta=[(self.alfajor, 1)])
        with self.assertRaisesMessage(ValidationError, 'identificá al cliente'):
            ventas.registrar(self.turno, self.operador,
                             [ventas.Item(self.cafe, D('1'))],
                             [ventas.Pago(f.efectivo())],
                             beneficios=[regalo.pk])


# ===========================================================================
class DevolucionTest(Base):

    def devolver(self, venta, renglon, cantidad):
        return cambios.registrar(
            self.turno, self.operador, venta, 'CLIENTE_ARREPENTIDO',
            [cambios.Devuelto(renglon, D(str(cantidad)), True)],
            medio_pago=f.efectivo(), autorizante=self.encargada)

    def test_el_reverso_es_proporcional_y_va_en_tramos(self):
        venta, _ = self.vender((self.alfajor, 4))           # $4.000 -> 40
        renglon = venta.detalles.get()
        cambio = self.devolver(venta, renglon, 1)
        self.assertEqual(cambio.reverso_puntos.acumulacion, 10)
        self.assertEqual(self.refrescar().puntos_disponibles, 30)
        self.devolver(venta, renglon, 3)
        sofia = self.refrescar()
        self.assertEqual(sofia.puntos_disponibles, 0)
        self.assertEqual(sofia.puntos_acumulados, 0)
        anulado = sum(m.puntos for m in MovimientoPuntos.objects.filter(
            id_venta=venta, tipo_movimiento='ANULACION'))
        self.assertEqual(anulado, -40)

    def test_el_canje_se_devuelve_si_se_devuelve_todo(self):
        regalo = beneficio('Bronze', 'Un café de regalo', 'CANJEABLE',
                           'ESPECIE', puntos=20, receta=[(self.cafe, 1)])
        self.vender((self.alfajor, 2))                     # 20 puntos
        venta, _ = self.vender((self.alfajor, 1), beneficios=[regalo.pk])
        normal = venta.detalles.get(id_beneficio__isnull=True)
        gratis = venta.detalles.get(id_beneficio=regalo)
        cambio = self.devolver(venta, normal, 1)
        self.assertEqual(cambio.reverso_puntos.canjes, 0)    # el café se lo quedó
        cambio = cambios.registrar(
            self.turno, self.operador, venta, 'CLIENTE_ARREPENTIDO',
            [cambios.Devuelto(gratis, D('1'), True)])
        self.assertEqual(cambio.reverso_puntos.canjes, 20)
        self.assertEqual(self.refrescar().puntos_disponibles, 20)

    def test_con_el_descuento_del_club_se_reintegra_lo_que_pago(self):
        beneficio('Bronze', '10% del club', 'AUTOMATICO', 'DESCUENTO',
                  porcentaje=D('10'))
        venta, _ = self.vender((self.alfajor, 2))
        renglon = venta.detalles.get()
        self.assertEqual(renglon.precio_reintegrable, D('900.00'))
        cambio = self.devolver(venta, renglon, 1)
        self.assertEqual(cambio.importe_devuelto, D('900'))


# ===========================================================================
class PantallasTest(Base):

    def setUp(self):
        super().setUp()
        f.entrar(self.client, self.operador)
        self.client.post(reverse('ventas:elegir_caja'),
                         {'caja': f.caja_minisuper().pk})

    def post_json(self, nombre, cuerpo):
        return self.client.post(reverse(nombre), json.dumps(cuerpo),
                                content_type='application/json').json()

    def test_identificar_y_cobrar_con_puntos_y_ascenso(self):
        datos = self.client.get(reverse('ventas:identificar_cliente'),
                                {'q': self.sofia.qr_cliente}).json()
        self.assertTrue(datos['ok'])
        CategoriaFidelizacion.objects.filter(nombre_categoria='Silver').update(
            puntos_minimos=10)
        venta = self.post_json('ventas:cobrar', {
            'token': uuid.uuid4().hex, 'cliente': datos['cliente']['id'],
            'items': [{'producto': self.alfajor.pk, 'cantidad': '2'}],
            'pagos': [{'medio': f.efectivo().pk}]})
        self.assertTrue(venta['ok'], venta)
        self.assertEqual(venta['puntos']['ganados'], 20)
        self.assertTrue(venta['puntos']['ascendio'])
        self.assertTrue(LogAuditoria.objects.filter(
            operacion_realizada='RECATEGORIZACION DE CLIENTE').exists())
        ticket = self.client.get(venta['ticket'])
        self.assertContains(ticket, 'Puntos sumados en esta compra')
        # Reimpreso después de otra compra, dice el saldo de ESA venta.
        self.vender((self.alfajor, 3))
        ticket = self.client.get(venta['ticket'])
        self.assertEqual(ticket.context['puntos']['saldo'], 20)

    def test_alta_rapida_desde_el_mostrador(self):
        datos = self.post_json('ventas:alta_cliente', {
            'apellido_cliente': 'Romero', 'nombre_cliente': 'Lucía',
            'telefono_cliente': '387 555-1234'})
        self.assertTrue(datos['ok'], datos)
        nueva = Cliente.objects.get(pk=datos['cliente']['id'])
        self.assertEqual(nueva.telefono_cliente, '3875551234')
        self.assertTrue(LogAuditoria.objects.filter(
            operacion_realizada='ALTA DE CLIENTE', id_registro=nueva.pk).exists())
        repetida = self.post_json('ventas:alta_cliente', {
            'apellido_cliente': 'Otra', 'nombre_cliente': 'Persona',
            'telefono_cliente': '387 555-1234'})
        self.assertFalse(repetida['ok'])
        self.assertIn('ya existe', repetida['error'])

    def test_el_operador_ve_y_carga_clientes_pero_no_los_da_de_baja(self):
        self.assertEqual(self.client.get(reverse('fidelizacion:clientes_lista'))
                         .status_code, 200)
        ficha = self.client.get(reverse('fidelizacion:clientes_ver',
                                        args=[self.sofia.pk]))
        self.assertContains(ficha, '<svg')
        self.client.post(reverse('fidelizacion:clientes_eliminar',
                                 args=[self.sofia.pk]))
        self.sofia.refresh_from_db()
        self.assertFalse(self.sofia.borrado_logico)
        # ni se lleva el padrón con los DNI y teléfonos
        exportar = self.client.get(reverse('fidelizacion:clientes_exportar'),
                                   {'formato': 'xlsx'})
        self.assertEqual(exportar.status_code, 302)

    def test_los_reportes_del_club(self):
        self.vender((self.alfajor, 2))
        f.entrar(self.client, self.encargada)
        for clave in ('ranking', 'consumo', 'segmentos'):
            respuesta = self.client.get(reverse('negocio:reportes'),
                                        {'ver': clave})
            self.assertEqual(respuesta.status_code, 200, clave)
            self.assertContains(respuesta, 'López, Sofía' if clave != 'segmentos'
                                else 'Bronze')
