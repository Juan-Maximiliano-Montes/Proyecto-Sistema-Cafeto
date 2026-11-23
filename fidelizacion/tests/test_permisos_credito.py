"""Quién entra a Cuentas corrientes y quién puede cobrar (auditoría 2026-11).

La pantalla es de dos puestos distintos:

* ``cobrar_credito``    recibe la plata del fiado;
* ``gestionar_credito`` fija el tope, suspende la cuenta y la da por
  incobrable.

Pedía sólo el primero, así que un perfil de gestión —la titular delega eso
sin darle la caja— veía el ítem en el menú y al entrar se comía un «acceso
denegado». Y al revés: entrar por gestión NO habilita a tocar la plata.
"""
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from fidelizacion import credito
from fidelizacion.tests.test_credito import Base
from nucleo.tests import fabrica as f
from seguridad.tests.test_auditoria_permisos import perfil_con


class PermisosDeCuentasTest(Base):

    def setUp(self):
        super().setUp()
        self.habilitar(limite=D('50000'))
        self.venta = self.fiar()
        self.gestora = f.usuario('40100003', 'Operador', nombre='Nora')
        self.gestora.id_perfil = perfil_con('Fiado', 'gestionar_credito')
        self.gestora.save()
        self.cobradora = f.usuario('40100004', 'Operador', nombre='Pili')
        self.cobradora.id_perfil = perfil_con('Cobranza', 'cobrar_credito')
        self.cobradora.save()
        self.ajena = f.usuario('40100005', 'Operador', nombre='Sol')
        self.ajena.id_perfil = perfil_con('Sólo mirar', 'ver_ventas')
        self.ajena.save()
        self.transferencia = f.medio('Transferencia')

    def como(self, usuario):
        self.client = self.client_class()
        f.entrar(self.client, usuario)
        return self.client

    # ------------------------------------------------------------------
    def test_quien_gestiona_el_fiado_entra_al_listado_y_a_la_cuenta(self):
        cliente_web = self.como(self.gestora)
        self.assertEqual(cliente_web.get(reverse('fidelizacion:cuentas')).status_code, 200)
        self.assertEqual(cliente_web.get(
            reverse('fidelizacion:cuenta', args=[self.elsa.pk])).status_code, 200)

    def test_quien_cobra_sigue_entrando(self):
        cliente_web = self.como(self.cobradora)
        self.assertEqual(cliente_web.get(reverse('fidelizacion:cuentas')).status_code, 200)
        self.assertEqual(cliente_web.get(
            reverse('fidelizacion:cuenta', args=[self.elsa.pk])).status_code, 200)

    def test_sin_ninguno_de_los_dos_no_entra(self):
        cliente_web = self.como(self.ajena)
        self.assertNotEqual(cliente_web.get(reverse('fidelizacion:cuentas')).status_code, 200)
        self.assertNotEqual(cliente_web.get(
            reverse('fidelizacion:cuenta', args=[self.elsa.pk])).status_code, 200)

    def test_el_recibo_lo_abren_los_dos_puestos(self):
        credito.cobrar(self.elsa, D('1000'), f.efectivo(), self.cajera, turno=self.turno)
        movimiento = (self.elsa.movimientos_cuenta
                      .filter(tipo_movimiento=credito.COBRO).order_by('pk').first())
        self.assertIsNotNone(movimiento)
        for usuario in (self.gestora, self.cobradora):
            cliente_web = self.como(usuario)
            respuesta = cliente_web.get(reverse('fidelizacion:recibo', args=[movimiento.pk]))
            self.assertEqual(respuesta.status_code, 200, usuario)

    def test_el_recibo_no_lo_abre_un_ajeno(self):
        credito.cobrar(self.elsa, D('1000'), f.efectivo(), self.cajera, turno=self.turno)
        movimiento = (self.elsa.movimientos_cuenta
                      .filter(tipo_movimiento=credito.COBRO).order_by('pk').first())
        cliente_web = self.como(self.ajena)
        self.assertNotEqual(
            cliente_web.get(reverse('fidelizacion:recibo', args=[movimiento.pk])).status_code, 200)

    # ------------------------------------------------------------------
    def test_entrar_por_gestion_NO_habilita_a_cobrar(self):
        """El control que hace falta: la pantalla se comparte, la plata no."""
        antes = credito.saldo(self.elsa)
        cliente_web = self.como(self.gestora)
        respuesta = cliente_web.post(
            reverse('fidelizacion:cuenta', args=[self.elsa.pk]),
            {'accion': 'cobrar', 'monto': '1000', 'medio': f.efectivo().pk,
             'token_operacion': self.token(cliente_web)}, follow=True)
        self.assertContains(respuesta, 'no registra cobros')
        self.assertEqual(credito.saldo(self.elsa), antes)

    def test_el_formulario_de_cobro_no_se_le_dibuja(self):
        cliente_web = self.como(self.gestora)
        html = cliente_web.get(reverse('fidelizacion:cuenta', args=[self.elsa.pk])).content.decode()
        self.assertNotIn('value="cobrar"', html)
        self.assertIn('no registra cobros', html)
        # Lo suyo sí: el tope, la suspensión, el incobrable.
        self.assertIn('value="estado"', html)

    def test_quien_cobra_sigue_viendo_el_formulario_y_cobrando(self):
        cliente_web = self.como(self.cobradora)
        html = cliente_web.get(reverse('fidelizacion:cuenta', args=[self.elsa.pk])).content.decode()
        self.assertIn('value="cobrar"', html)
        # Por transferencia: el efectivo sale del cajón y pide un turno
        # abierto en ESTE dispositivo, que es otra regla y otro test.
        respuesta = cliente_web.post(
            reverse('fidelizacion:cuenta', args=[self.elsa.pk]),
            {'accion': 'cobrar', 'monto': '500', 'medio': self.transferencia.pk,
             'token_operacion': self.token(cliente_web)}, follow=True)
        self.assertNotContains(respuesta, 'no registra cobros')
        self.assertEqual(credito.saldo(self.elsa), D('1500'))

    # ------------------------------------------------------------------
    def token(self, cliente_web):
        pantalla = cliente_web.get(reverse('fidelizacion:cuenta', args=[self.elsa.pk]))
        return pantalla.context['token']
