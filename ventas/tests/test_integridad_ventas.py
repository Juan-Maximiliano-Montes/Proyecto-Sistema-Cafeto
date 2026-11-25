"""VEN-01, VEN-02 y VEN-04: cambios y reintentos que no rompen la integridad.

    VEN-01  la diferencia de un cambio no se deja en cuenta corriente sin
            cliente, y con cliente pasa por todos los controles del fiado
    VEN-02  un token ya usado sólo es un reintento si el pedido es el mismo
    VEN-04  lo que se lleva en un cambio pasa por las reglas de una venta
            (vendible, stock total, rebaja por vencimiento) y la titular no
            vuelve a poner la clave para devolver plata
"""
import uuid
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from fidelizacion import credito
from fidelizacion.forms import ClienteForm
from fidelizacion.models import Cliente, MovimientoCuenta
from nucleo.tests import fabrica as f
from ventas import cambios
from ventas import servicios as ventas
from ventas.models import Cambio, DetalleCambio, MedioPago, Venta


def cliente(telefono='3874555111', dni='20111222'):
    formulario = ClienteForm({'apellido_cliente': 'Gómez', 'nombre_cliente': 'Elsa',
                              'telefono_cliente': telefono, 'dni_cliente': dni})
    assert formulario.is_valid(), formulario.errors
    return formulario.save()


class Base(TestCase):

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.admin = f.usuario('40000003', 'Administrador', nombre='Ana')
        self.cajon = f.caja_cafeteria()
        self.prov = f.proveedor()
        self.gaseosa = f.producto('Gaseosa', prov=self.prov, precio='1000', costo='500')
        self.vino = f.producto('Vino', prov=self.prov, precio='5000', costo='2000')
        self.grano = f.producto('Café en grano', 'INSUMO', prov=self.prov, costo='20000',
                                rubro_nombre='Cafetería', unidad='KG')
        f.comprar(self.encargada, self.prov, (self.gaseosa, 50, '500'), (self.vino, 5, '2000'),
                  (self.grano, 3, '20000'))
        self.turno = f.abrir_turno(self.operador, self.cajon, fondo='5000')
        self.cc = MedioPago.objects.get(nombre_medio_pago=MedioPago.NOMBRE_CUENTA_CORRIENTE)

    def vender(self, cantidad='1', cliente=None, medio=None, producto=None, **extra):
        return ventas.registrar(
            self.turno, self.operador, [ventas.Item(producto or self.gaseosa, D(cantidad))],
            [ventas.Pago(medio or f.efectivo())], cliente=cliente, **extra)[0]

    def cambiar(self, venta, entregados, medio, autorizante=None, devuelve='1'):
        return cambios.registrar(
            self.turno, self.operador, venta, 'CLIENTE_ARREPENTIDO',
            [cambios.Devuelto(venta.detalles.get(), D(devuelve), True)], entregados,
            medio_pago=medio, autorizante=autorizante)

    def entrar(self, usuario):
        sesion = f.entrar(self.client, usuario)
        self.client.post(reverse('ventas:elegir_caja'), {'caja': self.cajon.pk})
        return sesion


# ===========================================================================
#  VEN-01: la diferencia a cuenta corriente
# ===========================================================================
class DiferenciaACuentaCorrienteTest(Base):

    def test_sin_cliente_no_se_deja_en_cuenta_corriente(self):
        venta = self.vender()
        with self.assertRaisesMessage(ValidationError, 'no tiene cliente'):
            self.cambiar(venta, [cambios.Entregado(self.vino, D('1'))], self.cc)
        self.assertFalse(Cambio.objects.exists())
        self.assertFalse(MovimientoCuenta.objects.exists())

    def test_la_pantalla_no_ofrece_cuenta_corriente_sin_cliente(self):
        venta = self.vender()
        renglon = venta.detalles.get()
        self.entrar(self.operador)
        respuesta = self.client.post(reverse('ventas:cambios'), {
            'venta': venta.pk, 'accion': 'calcular', 'motivo': 'CLIENTE_ARREPENTIDO',
            f'devuelve_{renglon.pk}': '1', 'entrega_producto_0': self.vino.pk,
            'entrega_cantidad_0': '1'})
        self.assertEqual(respuesta.context['a_cobrar'], D('4000'))
        self.assertNotIn(self.cc, list(respuesta.context['medios']))
        self.assertIn(f.efectivo(), list(respuesta.context['medios']))

    def test_la_pantalla_si_la_ofrece_con_cliente(self):
        elsa = cliente()
        venta = self.vender(cliente=elsa)
        renglon = venta.detalles.get()
        self.entrar(self.operador)
        respuesta = self.client.post(reverse('ventas:cambios'), {
            'venta': venta.pk, 'accion': 'calcular', 'motivo': 'CLIENTE_ARREPENTIDO',
            f'devuelve_{renglon.pk}': '1', 'entrega_producto_0': self.vino.pk,
            'entrega_cantidad_0': '1'})
        self.assertIn(self.cc, list(respuesta.context['medios']))

    def test_con_cliente_habilitado_queda_como_fiado(self):
        elsa = cliente()
        credito.cambiar_estado(elsa, 'HABILITADO', self.admin)
        venta = self.vender(cliente=elsa)
        cambio = self.cambiar(venta, [cambios.Entregado(self.vino, D('1'))], self.cc)
        self.assertEqual(cambio.importe_cobrado, D('4000'))
        self.assertEqual(credito.saldo(elsa), D('4000'))
        self.assertEqual(cambio.deuda_generada.monto, D('4000'))

    def test_sin_cuenta_corriente_pide_la_firma_de_quien_la_gestiona(self):
        elsa = cliente()
        venta = self.vender(cliente=elsa)
        with self.assertRaisesMessage(ValidationError, 'todavía no tiene cuenta corriente'):
            self.cambiar(venta, [cambios.Entregado(self.vino, D('1'))], self.cc)
        self.assertFalse(Cambio.objects.exists())
        self.vino.refresh_from_db()
        self.assertEqual(self.vino.stock_actual, D('5'))

    def test_sobre_el_tope_no_se_fia_la_diferencia(self):
        elsa = cliente()
        credito.cambiar_estado(elsa, 'HABILITADO', self.admin, limite=D('1000'))
        venta = self.vender(cliente=elsa)
        with self.assertRaisesMessage(ValidationError, 'tope'):
            self.cambiar(venta, [cambios.Entregado(self.vino, D('1'))], self.cc)
        self.assertEqual(credito.saldo(elsa), D('0'))

    def test_un_incobrable_no_recibe_fiado_por_un_cambio(self):
        elsa = cliente()
        credito.cambiar_estado(elsa, 'HABILITADO', self.admin)
        venta = self.vender(cliente=elsa)
        Cliente.objects.filter(pk=elsa.pk).update(estado_credito='INCOBRABLE')
        with self.assertRaisesMessage(ValidationError, 'incobrable'):
            self.cambiar(venta, [cambios.Entregado(self.vino, D('1'))], self.cc)

    def test_una_venta_ya_fiada_no_suma_otro_fiado(self):
        elsa = cliente()
        credito.cambiar_estado(elsa, 'HABILITADO', self.admin)
        venta = self.vender(cliente=elsa, medio=self.cc)
        with self.assertRaisesMessage(ValidationError, 'ya quedó fiada'):
            self.cambiar(venta, [cambios.Entregado(self.vino, D('1'))], self.cc)


# ===========================================================================
#  VEN-04: lo que se lleva y quién autoriza
# ===========================================================================
class LoQueSeLlevaTest(Base):

    def test_un_insumo_no_se_entrega(self):
        venta = self.vender()
        with self.assertRaisesMessage(ValidationError, 'insumo'):
            self.cambiar(venta, [cambios.Entregado(self.grano, D('1'))], f.efectivo(),
                         autorizante=self.encargada)

    def test_el_stock_se_controla_sobre_la_suma_de_lo_que_se_lleva(self):
        venta = self.vender()
        with self.assertRaises(ValidationError):
            self.cambiar(venta, [cambios.Entregado(self.vino, D('3')),
                                 cambios.Entregado(self.vino, D('3'))], f.efectivo())
        self.assertFalse(Cambio.objects.exists())
        self.vino.refresh_from_db()
        self.assertEqual(self.vino.stock_actual, D('5'))

    def test_la_rebaja_por_vencimiento_tambien_vale_en_un_cambio(self):
        yogur = f.producto('Yogur', prov=self.prov, precio='1200', costo='500',
                           controla_vencimiento=True, dias_rebaja_vencimiento=10,
                           porcentaje_rebaja_vencimiento=D('50'))
        f.comprar(self.encargada, self.prov, (yogur, 10, '500', [(f.en_dias(3), 10)]))
        self.assertEqual(cambios.precio_de_entrega(yogur, D('1')), D('600.00'))
        # Un producto sin rebaja vigente se entrega a precio de lista.
        self.assertEqual(cambios.precio_de_entrega(self.vino, D('1')), D('5000'))

        venta = self.vender()
        cambio = self.cambiar(venta, [cambios.Entregado(yogur, D('1'))], f.efectivo(),
                              autorizante=self.encargada)
        # Devuelve 1000 y se lleva 600: se le devuelven 400, no 0.
        self.assertEqual(cambio.importe_devuelto, D('400'))
        renglon = DetalleCambio.objects.get(id_cambio=cambio, sentido='ENTREGA')
        self.assertEqual(renglon.precio_unitario, D('600.00'))


class QuienAutorizaLaDevolucionTest(Base):

    def datos(self, venta, **extra):
        return {'venta': venta.pk, 'accion': 'confirmar', 'motivo': 'PRODUCTO_DANADO',
                'token': uuid.uuid4().hex, 'medio': f.efectivo().pk,
                f'devuelve_{venta.detalles.get().pk}': '1',
                f'reingresa_{venta.detalles.get().pk}': '0', **extra}

    def test_la_titular_no_vuelve_a_poner_la_clave(self):
        titular = f.titular()
        venta = self.vender()
        self.entrar(titular)
        self.client.post(reverse('ventas:cambios'), self.datos(venta))
        cambio = venta.cambios.get()
        self.assertEqual(cambio.importe_devuelto, D('1000'))
        self.assertEqual(cambio.id_usuario_autorizante, titular)

    def test_quien_vendio_no_autoriza_la_devolucion_de_su_propia_venta(self):
        venta = Venta.objects.get(pk=ventas.registrar(
            self.turno, self.encargada, [ventas.Item(self.gaseosa, D('1'))],
            [ventas.Pago(f.efectivo())])[0].pk)
        self.entrar(self.encargada)
        respuesta = self.client.post(reverse('ventas:cambios'), self.datos(
            venta, autoriza_usuario='40000002', autoriza_clave=f.CLAVE))
        self.assertIn('otra persona', respuesta.context['error'])
        self.assertFalse(venta.cambios.exists())

    def test_otra_persona_con_permiso_si_autoriza(self):
        venta = self.vender()
        self.entrar(self.operador)
        self.client.post(reverse('ventas:cambios'), self.datos(
            venta, autoriza_usuario='40000002', autoriza_clave=f.CLAVE))
        self.assertEqual(venta.cambios.get().id_usuario_autorizante, self.encargada)


# ===========================================================================
#  VEN-02: el mismo token, el mismo pedido
# ===========================================================================
class ReintentoConElMismoTokenTest(Base):

    def registrar(self, token, items, cliente=None):
        return ventas.registrar(self.turno, self.operador, items, [ventas.Pago(f.efectivo())],
                                token=token, cliente=cliente)

    def test_el_mismo_pedido_devuelve_la_venta_ya_hecha(self):
        token = uuid.uuid4()
        primera, _ = self.registrar(token, [ventas.Item(self.gaseosa, D('2'))])
        segunda, cotizacion = self.registrar(token, [ventas.Item(self.gaseosa, D('2'))])
        self.assertEqual(primera.pk, segunda.pk)
        self.assertIsNone(cotizacion)
        self.assertEqual(Venta.objects.count(), 1)

    def test_otros_productos_con_el_mismo_token_no_es_un_reintento(self):
        token = uuid.uuid4()
        self.registrar(token, [ventas.Item(self.gaseosa, D('2'))])
        with self.assertRaisesMessage(ValidationError, 'iniciá una venta nueva'):
            self.registrar(token, [ventas.Item(self.vino, D('1'))])
        self.assertEqual(Venta.objects.count(), 1)

    def test_otra_cantidad_con_el_mismo_token_no_es_un_reintento(self):
        token = uuid.uuid4()
        self.registrar(token, [ventas.Item(self.gaseosa, D('2'))])
        with self.assertRaisesMessage(ValidationError, 'Recargá la pantalla'):
            self.registrar(token, [ventas.Item(self.gaseosa, D('3'))])

    def test_otro_cliente_con_el_mismo_token_no_es_un_reintento(self):
        token = uuid.uuid4()
        elsa = cliente()
        self.registrar(token, [ventas.Item(self.gaseosa, D('1'))], cliente=elsa)
        with self.assertRaisesMessage(ValidationError, 'otro cliente'):
            self.registrar(token, [ventas.Item(self.gaseosa, D('1'))])
        with self.assertRaisesMessage(ValidationError, 'otro cliente'):
            self.registrar(token, [ventas.Item(self.gaseosa, D('1'))],
                           cliente=cliente('3874555222', '20111333'))

    def test_el_mismo_producto_en_dos_renglones_es_el_mismo_pedido(self):
        token = uuid.uuid4()
        self.registrar(token, [ventas.Item(self.gaseosa, D('3'))])
        venta, _ = self.registrar(token, [ventas.Item(self.gaseosa, D('1')),
                                          ventas.Item(self.gaseosa, D('2'))])
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(venta.detalles.get().cantidad, D('3'))
