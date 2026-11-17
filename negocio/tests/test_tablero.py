"""Tablero interactivo de Reportes y Resumen del mes (DD-100).

Lo que importa no es el dibujo sino que los números cierren: la suma de
las barras tiene que dar el resultado del mes, los medios de pago el 100 %
y los rankings estar bien ordenados.
"""
import json
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from negocio import mensual, tablero
from nucleo.tests import fabrica as f
from ventas import servicios as ventas
from ventas.models import MedioPago


class TableroTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.duenia = f.titular()
        cls.cajera = f.usuario('40800001', 'Operador')
        prov = f.proveedor()
        cls.alfajor = f.producto('Alfajor', prov=prov, precio='1000', costo='600')
        cls.cafe = f.producto('Café', prov=prov, precio='2500', costo='800', rubro_nombre='Cafetería')
        f.comprar(cls.duenia, prov, (cls.alfajor, 50, '600'), (cls.cafe, 50, '800'))
        turno = f.abrir_turno(cls.cajera)
        transferencia = MedioPago.objects.exclude(pk=f.efectivo().pk).exclude(
            nombre_medio_pago=MedioPago.NOMBRE_CUENTA_CORRIENTE).first()
        ventas.registrar(turno, cls.cajera, [ventas.Item(cls.alfajor, D('5'))], [ventas.Pago(f.efectivo())])
        ventas.registrar(turno, cls.cajera, [ventas.Item(cls.cafe, D('2'))], [ventas.Pago(transferencia)])
        cls.hoy = timezone.localdate()

    def test_la_suma_de_las_barras_da_el_resultado_del_mes(self):
        desde = self.hoy.replace(day=1)
        datos = tablero.evolucion(desde, self.hoy)
        neta = sum(D(p['neta']) for p in datos['puntos'])
        resumen = mensual.del_mes(self.hoy.year, self.hoy.month)
        self.assertEqual(neta, resumen['resultado'])
        self.assertEqual(D(datos['total']['neta']), resumen['resultado'])
        self.assertEqual(D(datos['total']['desglose']['vendido']), D('10000'))
        self.assertEqual(D(datos['total']['desglose']['costo']), D('4600'))

    def test_un_periodo_largo_se_agrupa(self):
        self.assertEqual(tablero.evolucion(self.hoy, self.hoy)['unidad'], 'día')
        largo = tablero.evolucion(self.hoy.replace(year=self.hoy.year - 1), self.hoy)
        self.assertEqual(largo['unidad'], 'semana')
        self.assertEqual(sum(D(p['ingresos']) for p in largo['puntos']), D('10000'))

    def test_medios_de_pago_suman_el_cien_por_ciento(self):
        medios = tablero.medios_de_pago(self.hoy, self.hoy)
        self.assertEqual(D(medios['total']), D('10000'))
        self.assertEqual(sum(D(p['porcentaje']) for p in medios['partes']), D('100.0'))
        self.assertEqual(medios['partes'][0]['porcentaje'], '50.0')

    def test_el_ranking_cambia_con_el_criterio(self):
        top = tablero.top(self.hoy, self.hoy)['productos']
        self.assertIn('Alfajor', top['unidades'][0]['nombre'])
        self.assertIn('Café', top['ganancia'][0]['nombre'])   # $ 3.400 contra $ 2.000
        self.assertEqual(top['ganancia'][0]['margen'], '68.0')
        rubros = {r['nombre'] for r in tablero.top(self.hoy, self.hoy)['rubros']['monto']}
        self.assertEqual(rubros, {'Minisuper', 'Cafetería'})

    def test_franja_horaria(self):
        horas = tablero.franjas(self.hoy, self.hoy)['horas']
        self.assertEqual(sum(h['tickets'] for h in horas), 2)

    def test_las_pantallas_traen_el_tablero(self):
        f.entrar(self.client, self.duenia)
        for url in (reverse('negocio:reportes'), reverse('negocio:resumen_del_mes')):
            respuesta = self.client.get(url)
            self.assertContains(respuesta, 'id="datosTablero"')
            self.assertContains(respuesta, 'tablero.js')
            self.assertContains(respuesta, 'Por margen de ganancia')
            datos = json.loads(respuesta.content.decode().split('id="datosTablero" type="application/json">')[1]
                               .split('</script>')[0])
            self.assertIn('evolucion', datos)
        self.assertContains(self.client.get(reverse('negocio:reportes')), '90 días')
