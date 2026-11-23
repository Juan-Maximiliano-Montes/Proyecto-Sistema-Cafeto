"""Pagos y gastos dice lo mismo que pasa con la plata (DD-96).

* Una factura cubierta por transferencias en acreditación no aparece «a
  pagar» con Falta $ 0,00: invitaría a pagarla dos veces. Si el banco
  rechaza la transferencia, vuelve sola.
* El historial muestra sólo lo resuelto; lo que espera al banco está en
  «Por acreditar».
* Lo vencido dice «venció», no «vence», y llega al inicio en rojo.
"""
from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras import egresos
from compras.models import Compra, TipoEgreso
from negocio import panel
from nucleo.tests import fabrica as f


class APagarCoherenteTest(TestCase):

    def setUp(self):
        self.admin = f.usuario('40400001', 'Administrador', nombre='Ana')
        self.prov = f.proveedor()
        self.yerba = f.producto('Yerba', prov=self.prov, precio='3000', costo='2000')
        self.hoy = timezone.localdate()
        self.transferencia = f.medio('Transferencia')
        self.tipo = TipoEgreso.objects.get(nombre_tipo_egreso='Proveedores')
        self.entera = f.comprar(self.admin, self.prov, (self.yerba, 10, '2000'))       # $ 20.000
        self.mitad = f.comprar(self.admin, self.prov, (self.yerba, 5, '2000'))         # $ 10.000
        Compra.objects.filter(pk__in=[self.entera.pk, self.mitad.pk]).update(
            fecha_compra=self.hoy - timedelta(days=10), fecha_vencimiento_pago=self.hoy - timedelta(days=5))
        self.transferida = egresos.registrar_egreso(
            self.tipo, self.transferencia, self.entera.total_compra, 'Pago entero',
            proveedor=self.prov, compra=self.entera, fecha=self.hoy - timedelta(days=6))
        egresos.registrar_egreso(self.tipo, self.transferencia, D('4000'), 'Pago parcial',
                                 proveedor=self.prov, compra=self.mitad)
        f.entrar(self.client, self.admin)

    def test_lo_cubierto_por_transferencias_no_se_vuelve_a_pagar(self):
        por_pagar = {c.pk: c for c in egresos.compras_por_pagar()}
        self.assertNotIn(self.entera.pk, por_pagar)
        self.assertEqual((por_pagar[self.mitad.pk].en_acreditacion, por_pagar[self.mitad.pk].falta),
                         (D('4000'), D('6000')))
        pantalla = self.client.get(reverse('compras:egresos'), {'ver': 'a_pagar'})
        self.assertNotContains(pantalla, self.entera.numero_formateado)
        self.assertContains(pantalla, 'En acreditación')
        self.assertContains(pantalla, 'Falta pagar')
        self.assertContains(pantalla, 'Venció')
        # Ofrecer pagar una factura ya cubierta, ni por la URL.
        self.assertIsNone(self.client.get(reverse('compras:egresos'),
                                          {'ver': 'a_pagar', 'compra': self.entera.pk}).context['compra_elegida'])

    def test_si_el_banco_rechaza_vuelve_a_pagar(self):
        egresos.conciliar(self.transferida, False, self.hoy)
        self.assertIn(self.entera.pk, {c.pk for c in egresos.compras_por_pagar()})

    def test_el_historial_es_solo_lo_resuelto(self):
        historial = self.client.get(reverse('compras:egresos'), {'ver': 'historial'})
        self.assertEqual(list(historial.context['filas']), [])
        cuentas = {v['clave']: v['cantidad'] for v in historial.context['vistas']}
        self.assertEqual(cuentas, {'a_pagar': 1, 'por_acreditar': 2, 'historial': 0})
        egresos.conciliar(self.transferida, True, self.hoy)
        historial = self.client.get(reverse('compras:egresos'), {'ver': 'historial'})
        self.assertEqual([e.pk for e in historial.context['filas']], [self.transferida.pk])
        self.assertNotContains(historial, 'name="resultado"')

    def test_obligacion_vencida_dice_vencio(self):
        egresos.registrar_obligacion(self.tipo, 'Luz del bimestre', D('15000'), self.hoy - timedelta(days=3),
                                     destinatario='Edesa')
        egresos.registrar_obligacion(self.tipo, 'Agua', D('8000'), self.hoy, destinatario='Aguas del Norte')
        pantalla = self.client.get(reverse('compras:egresos'), {'ver': 'a_pagar'})
        self.assertContains(pantalla, '>Vencido</span>')
        self.assertContains(pantalla, 'Vence hoy')
        self.assertContains(pantalla, '<dt>Venció</dt>', html=False)

    def test_el_inicio_avisa_lo_vencido_y_lo_que_el_banco_no_confirmo(self):
        _numeros, avisos = panel.del_dia(self.admin, self.hoy)
        por_clave = {a['clave']: a for a in avisos}
        self.assertEqual(por_clave['PAGOS_VENCIDOS']['tipo'], 'error')
        self.assertIn('1 pago a proveedores o servicios ya venció (por $\u00a06.000,00)',
                      por_clave['PAGOS_VENCIDOS']['texto'])
        self.assertEqual(avisos[0]['clave'], 'PAGOS_VENCIDOS')
        self.assertIn('1 pago enviado hace más de 3 días', por_clave['SIN_ACREDITAR']['texto'])
        inicio = self.client.get(reverse('home'))
        self.assertContains(inicio, '?ver=a_pagar')
        self.assertContains(inicio, '?ver=por_acreditar')

    def test_no_se_paga_con_la_cuenta_corriente_de_los_clientes(self):
        from django.core.exceptions import ValidationError
        from ventas.models import MedioPago
        cc = MedioPago.objects.get(nombre_medio_pago=MedioPago.NOMBRE_CUENTA_CORRIENTE)
        with self.assertRaisesMessage(ValidationError, 'fiado de los clientes'):
            egresos.registrar_egreso(self.tipo, cc, D('100'), 'Algo', destinatario='Alguien')
        pantalla = self.client.get(reverse('compras:egresos'))
        self.assertNotIn(cc, list(pantalla.context['medios']))


class EgresosPantallaYExportacionTest(TestCase):
    """DD-106: el monto sugerido es lo que falta, la exportación respeta la
    pestaña y la búsqueda, y cada formulario lleva su propio token."""

    setUp = APagarCoherenteTest.setUp

    def csv(self, **params):
        respuesta = self.client.get(reverse('compras:exportar_egresos'), {'formato': 'csv', **params})
        return b''.join(respuesta.streaming_content).decode('utf-8-sig')

    def test_el_monto_sugerido_es_lo_que_falta_y_no_el_saldo(self):
        pantalla = self.client.get(reverse('compras:egresos'), {'ver': 'a_pagar', 'compra': self.mitad.pk})
        # El control y el valor por separado: entre medio van los data-* que
        # usa el aviso de monto chico (DD-103), y la prueba es del MONTO.
        html = pantalla.content.decode()
        control = html[html.index('id="eMonto"'):html.index('id="eMonto"') + 300]
        self.assertIn('value="6000"', control)
        self.assertIn('data-minimo="0.01"', control)

    def test_exportar_respeta_la_pestania_y_la_busqueda(self):
        egresos.conciliar(self.transferida, True, self.hoy)
        pantalla = self.client.get(reverse('compras:egresos'), {'ver': 'historial', 'q': 'entero'}).content.decode()
        self.assertIn('?formato=xlsx&amp;ver=historial&amp;q=entero', pantalla)
        self.assertIn('hx-boost="false" href="/compras/egresos/exportar/?formato=pdf&amp;ver=historial', pantalla)
        csv = self.csv(ver='historial', q='entero')
        self.assertIn('Pago entero', csv)
        self.assertNotIn('Pago parcial', csv)
        acreditar = self.csv(ver='por_acreditar')
        self.assertIn('Pago parcial', acreditar)
        self.assertNotIn('Pago entero', acreditar)

    def test_cada_formulario_con_su_token(self):
        import re
        pantalla = self.client.get(reverse('compras:egresos'), {'ver': 'a_pagar'}).content.decode()
        tokens = re.findall(r'name="token_operacion" value="([0-9a-f]{32})"', pantalla)
        self.assertGreater(len(tokens), 1)
        self.assertEqual(len(tokens), len(set(tokens)))
