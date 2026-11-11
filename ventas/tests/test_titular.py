"""El titular puede anular su propia venta, confirmando con su clave (DD-69).

La regla general sigue en pie (DD-61): quien vendió no autoriza la
anulación de su venta. La excepción es sólo para el titular, porque la
segregación de funciones protege a la dueña del error o el fraude de otro,
y nadie se defrauda a sí mismo.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria, Usuario
from ventas import servicios as ventas

D = Decimal


class TitularAnulaSuVentaTest(TestCase):

    def setUp(self):
        self.titular = f.usuario('40000010', 'Administrador', nombre='Lucrecia')
        Usuario.objects.filter(es_titular=True).update(es_titular=False)
        Usuario.objects.filter(pk=self.titular.pk).update(es_titular=True)
        self.titular.refresh_from_db()
        self.admin = f.usuario('40000011', 'Administrador', nombre='Ariel')
        self.prov = f.proveedor()
        self.alfajor = f.producto('Alfajor', prov=self.prov, precio='1000', costo='500')
        f.comprar(self.titular, self.prov, (self.alfajor, 20, '500'))

    def vender(self, quien):
        turno = f.abrir_turno(quien, fondo='5000')
        venta, _ = ventas.registrar(turno, quien, [ventas.Item(self.alfajor, D('1'))],
                                    [ventas.Pago(f.efectivo())])
        return venta

    def anular(self, venta, **extra):
        url = reverse('ventas:ficha_venta', args=[venta.pk])
        contexto = self.client.get(url).context
        datos = {'token_operacion': contexto['token_anulacion'],
                 'motivo': 'ERROR_DE_CARGA'}
        datos.update(extra)
        r = self.client.post(reverse('ventas:anular_venta', args=[venta.pk]), datos, follow=True)
        self.mensajes = [str(m) for m in r.context['messages']] if r.context else []
        venta.refresh_from_db()
        return venta, contexto

    def test_el_titular_anula_su_venta_con_su_clave(self):
        venta = self.vender(self.titular)
        f.entrar(self.client, self.titular)
        venta, contexto = self.anular(venta, autoriza_usuario='40000010',
                                      autoriza_clave=f.CLAVE)
        self.assertTrue(contexto['titular_propia'])
        self.assertTrue(venta.anulada, self.mensajes)
        self.assertEqual(venta.id_usuario_anulacion, self.titular)
        asiento = LogAuditoria.objects.get(operacion_realizada='ANULACION DE VENTA')
        self.assertIn('DD-69', asiento.detalle_operacion)

    def test_sin_confirmar_la_clave_no_se_anula(self):
        venta = self.vender(self.titular)
        f.entrar(self.client, self.titular)
        venta, _ = self.anular(venta)
        self.assertFalse(venta.anulada)

    def test_otro_administrador_sigue_sin_poder_con_su_venta(self):
        venta = self.vender(self.admin)
        f.entrar(self.client, self.admin)
        venta, contexto = self.anular(venta, autoriza_usuario='40000011',
                                      autoriza_clave=f.CLAVE)
        self.assertFalse(contexto['titular_propia'])
        self.assertFalse(venta.anulada)

    def test_el_titular_autoriza_la_venta_de_otro_como_siempre(self):
        venta = self.vender(self.admin)
        f.entrar(self.client, self.titular)
        venta, _ = self.anular(venta)
        self.assertTrue(venta.anulada)
