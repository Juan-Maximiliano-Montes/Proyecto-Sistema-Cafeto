"""El comando de la demo carga todo sin que ninguna operación falle.

Si una regla nueva rompe un recorrido real (una venta, un cierre, una
recepción con faltante), esta prueba lo dice: el comando informa cada
operación que el sistema rechazó.
"""
from datetime import timedelta
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from ventas.models import TurnoCaja, Venta


@override_settings(DEBUG=True)
class DatosDemoTest(TestCase):

    def test_carga_dos_semanas_sin_errores(self):
        salida = StringIO()
        call_command('cargar_datos_demo', stdout=salida)
        texto = salida.getvalue()
        self.assertIn('todas las operaciones salieron bien', texto, texto)
        self.assertEqual(TurnoCaja.objects.filter(fecha_cierre__isnull=True).count(), 1)
        self.hay_historia_para_comparar_y_horas_repartidas()
        with self.assertRaisesMessage(CommandError, 'ya está cargada'):
            call_command('cargar_datos_demo', stdout=StringIO())

    def hay_historia_para_comparar_y_horas_repartidas(self):
        """DD-103: los gráficos del Parcial comparan los últimos 30 días con
        los 30 anteriores, y el mapa de calor necesita ventas a lo largo del
        día, no todas a la apertura."""
        hoy = timezone.localdate()
        completadas = Venta.objects.filter(estado_venta='COMPLETADA')
        ultimos = completadas.filter(fecha_venta__range=(hoy - timedelta(days=29), hoy))
        anteriores = completadas.filter(fecha_venta__range=(hoy - timedelta(days=59), hoy - timedelta(days=30)))
        self.assertGreaterEqual(ultimos.count(), 20)
        self.assertGreaterEqual(anteriores.count(), 10)
        self.assertGreaterEqual(anteriores.values('fecha_venta').distinct().count(), 5)
        horas = {h.hour for h in completadas.values_list('hora_venta', flat=True)}
        self.assertTrue(horas <= set(range(8, 22)), sorted(horas))
        self.assertGreaterEqual(len(horas), 9, sorted(horas))
        self.assertGreaterEqual(max(horas), 18)

    def test_reutiliza_lo_que_ya_estaba_cargado_a_mano(self):
        """Pasó en la base de desarrollo del equipo: "Lácteos del Valle" ya
        existía (con otro CUIT y escrito distinto) y la demo se cortaba al
        querer darlo de alta otra vez. Lo propio no se toca."""
        from compras.models import Proveedor
        from nucleo.tests import fabrica as f
        f.proveedor()   # crea la geografía y un proveedor cualquiera
        Proveedor.objects.create(
            nombre_proveedor='LACTEOS DEL VALLE', cuit_proveedor='20-12345678-6',
            tipo_proveedor='MERCADERIA', condicion_pago='CONTADO')
        otro = Proveedor.objects.create(
            nombre_proveedor='Limpieza total', cuit_proveedor='27-12345678-0',
            tipo_proveedor='MIXTO', condicion_pago='CONTADO')
        salida = StringIO()
        call_command('cargar_datos_demo', stdout=salida)
        texto = salida.getvalue()
        self.assertIn('todas las operaciones salieron bien', texto, texto)
        self.assertEqual(Proveedor.objects.filter(nombre_proveedor__iexact='lacteos del valle').count()
                         + Proveedor.objects.filter(nombre_proveedor='Lácteos del Valle').count(), 1)
        otro.refresh_from_db()
        self.assertFalse(otro.borrado_logico)      # la demo no da de baja lo ajeno

    @override_settings(DEBUG=False)
    def test_no_corre_en_la_base_del_local(self):
        with self.assertRaisesMessage(CommandError, 'DEBUG está apagado'):
            call_command('cargar_datos_demo', stdout=StringIO())
