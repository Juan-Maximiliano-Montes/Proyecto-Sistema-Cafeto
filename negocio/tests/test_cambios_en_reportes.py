"""REP-01 y REP-02: los cambios cuentan en los reportes y el stock se valoriza
a lo que de verdad se puede vender.

El ticket que se llevó el cliente no se anula: un cambio o una devolución lo
corrige con su NETO (lo cobrado de más suma, lo reintegrado resta) y con el
costo de lo que entró y salió del stock. Todos los reportes tienen que dar
lo mismo entre sí.
"""
from decimal import Decimal as D

from django.test import TestCase
from django.utils import timezone

from fidelizacion.tests.test_credito import cliente as nuevo_cliente
from negocio import ganancia, reportes
from nucleo.tests import fabrica as f
from stock.models import Lote
from ventas import cambios, servicios as ventas
from ventas.models import Venta


class Base(TestCase):

    def setUp(self):
        self.duenia = f.titular()
        self.prov = f.proveedor()
        self.alfajor = f.producto('Alfajor', prov=self.prov, precio='1000', costo='600')
        self.cafe = f.producto('Café en lata', prov=self.prov, precio='2500', costo='1500',
                               rubro_nombre='Cafetería')
        f.comprar(self.duenia, self.prov, (self.alfajor, 20, '600'), (self.cafe, 10, '1500'))
        self.turno = f.abrir_turno(self.duenia)
        self.cliente = nuevo_cliente()
        self.venta, _ = ventas.registrar(
            self.turno, self.duenia, [ventas.Item(self.alfajor, D('3'))],
            [ventas.Pago(f.efectivo())], cliente=self.cliente)       # $ 3.000, costo $ 1.800
        self.hoy = timezone.localdate()

    def cambio(self, devueltos, entregados=()):
        return cambios.registrar(
            self.turno, self.duenia, self.venta, motivo='PRODUCTO_DANADO',
            devueltos=[cambios.Devuelto(self.venta.detalles.get(), D(c), reingresa) for c, reingresa in devueltos],
            entregados=[cambios.Entregado(p, D(c)) for p, c in entregados],
            medio_pago=f.efectivo(), autorizante=self.duenia)

    def todo(self):
        return Venta.objects.all()


class DevolucionTest(Base):
    """Devuelve 1 alfajor en buen estado y se le reintegran $ 1.000."""

    def setUp(self):
        super().setUp()
        self.cambio([('1', True)])

    def test_la_ganancia_descuenta_lo_reintegrado_y_su_costo_vuelve_al_stock(self):
        cuenta = ganancia.de_ventas(self.todo())
        self.assertEqual((cuenta['facturado'], cuenta['costo'], cuenta['ganancia']),
                         (D('2000'), D('1200'), D('800')))
        self.assertEqual(cuenta['margen'], D('66.7'))

    def test_lo_cobrado_por_medio_cierra_con_lo_vendido(self):
        self.assertEqual(ganancia.por_medio_de_pago(self.todo()), [('Efectivo', D('2000'))])

    def test_ventas_por_dia(self):
        _, filas, totales = reportes.ventas_por_dia(self.hoy, self.hoy)
        self.assertEqual(filas[0][1:4], [1, D('2000'), D('2000.00')])      # tickets, vendido, promedio
        self.assertEqual(totales[2], D('2000'))

    def test_productos(self):
        _, filas, totales = reportes.productos(self.hoy, self.hoy)
        (fila,) = filas
        self.assertEqual((fila[1], fila[2], fila[3], fila[4]), (D('2'), D('2000'), D('1200'), D('800')))
        self.assertEqual((totales[2], totales[3], totales[4]), (D('2000'), D('1200'), D('800')))

    def test_medios(self):
        _, filas, totales = reportes.medios(self.hoy, self.hoy)
        self.assertEqual(filas, [['Efectivo', 1, D('2000'), D('0'), D('2000')]])
        self.assertEqual(totales[4], D('2000'))

    def test_consumo_por_cliente(self):
        consumo = reportes._consumo_por_cliente(self.hoy, self.hoy)
        self.assertEqual((consumo[self.cliente.pk]['compras'], consumo[self.cliente.pk]['neto']), (1, D('2000')))
        _, filas, totales = reportes.consumo(self.hoy, self.hoy)
        self.assertEqual(filas[0][3], D('2000'))
        self.assertEqual(totales[3], D('2000'))


class DevolucionNoAptaTest(Base):

    def test_lo_que_no_reingresa_sigue_costando(self):
        self.cambio([('1', False)])
        cuenta = ganancia.de_ventas(self.todo())
        self.assertEqual((cuenta['facturado'], cuenta['costo'], cuenta['ganancia']),
                         (D('2000'), D('1800'), D('200')))
        _, filas, _ = reportes.productos(self.hoy, self.hoy)
        self.assertEqual((filas[0][1], filas[0][3]), (D('2'), D('1800')))


class CambioConEntregaTest(Base):
    """Devuelve 1 alfajor y se lleva 1 café de $ 2.500: paga $ 1.500 de más."""

    def setUp(self):
        super().setUp()
        self.cambio([('1', True)], [(self.cafe, '1')])

    def test_se_suma_lo_cobrado_y_el_costo_de_lo_que_salio(self):
        cuenta = ganancia.de_ventas(self.todo())
        self.assertEqual((cuenta['facturado'], cuenta['costo'], cuenta['ganancia']),
                         (D('4500'), D('2700'), D('1800')))

    def test_los_reportes_dan_lo_mismo_entre_si(self):
        cuenta = ganancia.de_ventas(self.todo())
        _, por_dia, total_dia = reportes.ventas_por_dia(self.hoy, self.hoy)
        _, por_producto, total_productos = reportes.productos(self.hoy, self.hoy)
        _, por_medio, total_medios = reportes.medios(self.hoy, self.hoy)
        self.assertEqual(total_dia[2], cuenta['facturado'])
        self.assertEqual(total_productos[2], cuenta['facturado'])
        self.assertEqual(total_productos[3], cuenta['costo'])
        self.assertEqual(total_productos[4], cuenta['ganancia'])
        self.assertEqual(total_medios[4], cuenta['facturado'])
        self.assertEqual(sum(t for _, t in ganancia.por_medio_de_pago(self.todo())), cuenta['facturado'])
        # El producto que se llevó a cambio aparece con lo suyo.
        cafe = next(fila for fila in por_producto if 'Café' in fila[0])
        self.assertEqual((cafe[1], cafe[2], cafe[3]), (D('1'), D('2500'), D('1500')))
        alfajor = next(fila for fila in por_producto if 'Alfajor' in fila[0])
        self.assertEqual((alfajor[1], alfajor[2], alfajor[3]), (D('2'), D('2000'), D('1200')))

    def test_los_filtros_siguen_a_la_venta_original_y_al_producto(self):
        # El cambio se hizo sobre una venta de la caja Minisuper.
        _, filas, _ = reportes.productos(self.hoy, self.hoy, caja=f.caja_cafeteria().pk)
        self.assertEqual(filas, [])
        _, filas, _ = reportes.productos(self.hoy, self.hoy, caja=f.caja_minisuper().pk)
        self.assertEqual(len(filas), 2)
        # Por rubro: sólo lo que se llevó de Cafetería (el alfajor, de Minisuper, no).
        _, filas, _ = reportes.productos(self.hoy, self.hoy, rubro=f.rubro('Cafetería').pk)
        self.assertEqual([(fila[1], fila[2]) for fila in filas], [(D('1'), D('2500'))])


class SinCambiosNoSeMueveNadaTest(Base):

    def test_una_venta_sin_cambios_da_lo_de_siempre(self):
        cuenta = ganancia.de_ventas(self.todo())
        self.assertEqual((cuenta['facturado'], cuenta['costo'], cuenta['ganancia']),
                         (D('3000'), D('1800'), D('1200')))
        _, filas, totales = reportes.ventas_por_dia(self.hoy, self.hoy)
        self.assertEqual(totales[2], D('3000'))


class StockValorizadoTest(TestCase):
    """REP-02: «a precio de venta» es lo APTO de lo VENDIBLE."""

    def setUp(self):
        duenia = f.titular()
        prov = f.proveedor()
        self.yogur = f.producto('Yogur', prov=prov, precio='2000', costo='1200', controla_vencimiento=True)
        azucar = f.producto('Azúcar', tipo='INSUMO', prov=prov, costo='100', rubro_nombre='Cafetería')
        f.comprar(duenia, prov, (self.yogur, 10, '1200', [(f.en_dias(10), 6), (f.en_dias(20), 4)]),
                  (azucar, 5, '100'))
        # 6 de los 10 yogures se vencieron.
        Lote.objects.filter(id_producto=self.yogur, fecha_vencimiento=f.en_dias(10)).update(
            fecha_vencimiento=f.en_dias(-1))

    def filas(self, **filtros):
        _, filas, totales = reportes.stock_valorizado(**filtros)
        return {fila[0]: fila[1:] for fila in filas}, totales

    def test_lo_vencido_no_se_valoriza_a_precio_de_venta_pero_si_al_costo(self):
        filas, _ = self.filas()
        # 10 al costo (lo vencido sigue siendo plata puesta); 4 aptos a $ 2.000.
        self.assertEqual(filas['Minisuper'], [1, D('12000'), D('8000')])

    def test_un_insumo_no_se_vende(self):
        filas, _ = self.filas()
        self.assertEqual(filas['Cafetería'], [1, D('500'), D('0')])

    def test_los_totales_y_el_filtro_por_rubro(self):
        _, totales = self.filas()
        self.assertEqual(totales, ['Total', 2, D('12500'), D('8000')])
        filas, totales = self.filas(rubro=f.rubro('Cafetería').pk)
        self.assertEqual(list(filas), ['Cafetería'])
        self.assertEqual(totales[3], D('0'))

    def test_todo_vencido_vale_cero_a_precio_de_venta(self):
        Lote.objects.filter(id_producto=self.yogur).update(fecha_vencimiento=f.en_dias(-1))
        filas, _ = self.filas()
        self.assertEqual(filas['Minisuper'], [1, D('12000'), D('0')])
