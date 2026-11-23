"""Las columnas del Archivo histórico dicen lo mismo que la pantalla viva.

Dos cosas que no coincidían (auditoría 2026-11):

* en «Pagos y gastos», la columna «A quién» salía VACÍA en todos los pagos
  a proveedores —que son la mayoría del libro—, porque un egreso a un
  proveedor guarda el proveedor y deja ``destinatario`` en NULL (DD-25.1);
* en «Compras», el número salía con cinco dígitos de punto de venta
  ("00001-00001234") y no coincidía con el que figura impreso ni con
  ``Compra.numero_formateado`` ("0001-00001234"), que es el que el contador
  busca.
"""
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from compras import egresos
from compras.models import TipoEgreso
from negocio import archivo
from nucleo.tests import fabrica as f
from nucleo.tests.test_archivo_legal import Base


class LibroDeEgresosTest(Base):

    def pago_a_proveedor(self):
        """Por el mismo camino que la pantalla: el servicio de egresos.

        Por transferencia y no en efectivo: el efectivo sale del cajón y
        $120.000 no caben en el fondo de un turno de prueba.
        """
        return egresos.registrar_egreso(
            TipoEgreso.objects.get(nombre_tipo_egreso='Proveedores'),
            f.medio('Transferencia'), D('120000'), 'Factura de mercadería',
            proveedor=self.prov, usuario=self.admin)

    def filas(self, clave='egresos'):
        hoy = timezone.localdate()
        columnas, filas, _ = archivo.consultar(archivo.libro(clave), hoy, hoy)
        return [c.etiqueta for c in columnas], filas

    def test_un_pago_a_proveedor_muestra_el_nombre_del_proveedor(self):
        self.pago_a_proveedor()
        titulos, filas = self.filas()
        quien = titulos.index('A quién')
        self.assertEqual([fila[quien] for fila in filas],
                         [self.prov.nombre_proveedor])

    def test_un_pago_a_otro_destinatario_sigue_mostrando_el_destinatario(self):
        egresos.registrar_egreso(
            TipoEgreso.objects.get(nombre_tipo_egreso='Servicios'),
            f.medio('Transferencia'), D('50000'), 'Luz',
            destinatario='EDESA', usuario=self.admin)
        titulos, filas = self.filas()
        quien = titulos.index('A quién')
        self.assertEqual([fila[quien] for fila in filas], ['EDESA'])

    def test_en_la_pantalla_tambien_se_ve(self):
        self.pago_a_proveedor()
        f.entrar(self.client, self.admin)
        hoy = timezone.localdate().isoformat()
        respuesta = self.client.get(reverse('negocio:archivo'), {
            'libro': 'egresos', 'desde': hoy, 'hasta': hoy})
        self.assertContains(respuesta, self.prov.nombre_proveedor)

    def test_el_libro_declara_su_join(self):
        self.assertIn('proveedores p', archivo.libro('egresos').joins)


class LibroDeComprasTest(Base):

    def test_el_punto_de_venta_va_con_cuatro_digitos(self):
        compra = f.comprar(self.admin, self.prov, (self.alfajor, 1, '600'))
        hoy = timezone.localdate()
        columnas, filas, _ = archivo.consultar(archivo.libro('compras'), hoy, hoy)
        numero = [c.etiqueta for c in columnas].index('Número')
        self.assertIn(compra.numero_formateado, [fila[numero] for fila in filas])
        for fila in filas:
            self.assertRegex(fila[numero], r'^\d{4}-\d{8}$')
