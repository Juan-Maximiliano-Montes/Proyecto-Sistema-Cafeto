"""FID-03 y FID-04: las bajas y reactivaciones no rompen la integridad.

    FID-03  un cliente que debe plata no se da de baja
    FID-04  un beneficio automático de descuento no se reactiva encima de
            otro vigente de su categoría
"""
import datetime
from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase

from fidelizacion import credito
from fidelizacion.forms import ClienteForm
from fidelizacion.models import (
    Beneficio,
    CategoriaFidelizacion,
    ModoEntrega,
    TipoBeneficio,
)
from nucleo.tests import fabrica as f
from ventas import servicios as ventas
from ventas.models import MedioPago


def cliente():
    formulario = ClienteForm({'apellido_cliente': 'Gómez', 'nombre_cliente': 'Elsa',
                              'telefono_cliente': '3874555111', 'dni_cliente': '20111222'})
    assert formulario.is_valid(), formulario.errors
    return formulario.save()


class BajaConDeudaTest(TestCase):

    def setUp(self):
        self.cajera = f.usuario('40100001', 'Operador', nombre='Cami')
        self.admin = f.usuario('40100002', 'Administrador', nombre='Ana')
        prov = f.proveedor()
        self.alfajor = f.producto('Alfajor', prov=prov, precio='1000', costo='600')
        f.comprar(self.admin, prov, (self.alfajor, 50, '600'))
        self.turno = f.abrir_turno(self.cajera)
        self.cc = MedioPago.objects.get(nombre_medio_pago=MedioPago.NOMBRE_CUENTA_CORRIENTE)
        self.elsa = cliente()
        credito.cambiar_estado(self.elsa, 'HABILITADO', self.admin)

    def fiar(self, cantidad='2'):
        return ventas.registrar(
            self.turno, self.cajera, [ventas.Item(self.alfajor, D(cantidad))],
            [ventas.Pago(self.cc)], cliente=self.elsa)[0]

    def test_sin_deuda_se_puede_dar_de_baja(self):
        self.assertEqual(self.elsa.motivos_que_impiden_la_baja(), [])

    def test_con_deuda_no_se_da_de_baja(self):
        self.fiar('2')
        motivos = self.elsa.motivos_que_impiden_la_baja()
        self.assertEqual(len(motivos), 1)
        self.assertIn('deuda', motivos[0])
        self.assertIn('2.000', motivos[0])

    def test_saldada_la_deuda_vuelve_a_poder_bajarse(self):
        self.fiar('2')
        credito.cobrar(self.elsa, '2000', f.efectivo(), self.cajera, turno=self.turno)
        self.assertEqual(self.elsa.motivos_que_impiden_la_baja(), [])


class ReactivarBeneficioTest(TestCase):

    def setUp(self):
        self.categoria = CategoriaFidelizacion.objects.order_by('puntos_minimos').first()
        self.hoy = datetime.date.today()

    def beneficio(self, descripcion, desde, hasta=None):
        nuevo = Beneficio(
            id_categoria=self.categoria, descripcion_beneficio=descripcion,
            tipo_beneficio=TipoBeneficio.AUTOMATICO, modo_entrega=ModoEntrega.DESCUENTO,
            porcentaje_descuento=D('10'), vigencia_desde=desde, vigencia_hasta=hasta)
        nuevo.full_clean()
        nuevo.save()
        return nuevo

    def dar_de_baja(self, beneficio):
        Beneficio.objects.filter(pk=beneficio.pk).update(borrado_logico=True)
        beneficio.refresh_from_db()

    def test_no_se_reactiva_encima_de_otro_vigente(self):
        viejo = self.beneficio('Descuento de la categoría', self.hoy)
        self.dar_de_baja(viejo)
        self.beneficio('Descuento nuevo de la categoría', self.hoy)
        motivos = viejo.motivos_que_impiden_reactivar()
        self.assertEqual(len(motivos), 1)
        self.assertIn('Se superpone', motivos[0])

    def test_si_las_vigencias_no_se_pisan_se_reactiva(self):
        viejo = self.beneficio('Descuento de la categoría', self.hoy, self.hoy + timedelta(days=5))
        self.dar_de_baja(viejo)
        self.beneficio('Descuento nuevo de la categoría', self.hoy + timedelta(days=10))
        self.assertEqual(viejo.motivos_que_impiden_reactivar(), [])

    def test_sin_otro_vigente_se_reactiva(self):
        viejo = self.beneficio('Descuento de la categoría', self.hoy)
        self.dar_de_baja(viejo)
        self.assertEqual(viejo.motivos_que_impiden_reactivar(), [])
