"""Rechazos en el Registro de Actividad (DD-62).

El libro de auditoría no sólo cuenta lo que se hizo: también lo que el
sistema FRENÓ por un control de seguridad, con ``resultado_operacion =
RECHAZADA``. Y deja afuera, a propósito, los errores comunes de carga.

    ModeloTest          el dato nuevo y su CHECK (un rechazo no trae fotos)
    AccesoDenegadoTest  entrar sin permiso queda asentado
    AutorizacionTest    una autorización con clave que no se acepta
    AnulacionTest       una anulación fuera de regla, y lo que NO se asienta
    FiltrosTest         filtrar por resultado y por tipo de operación
    HistoricoTest       el resultado viaja al histórico
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from nucleo.tests import fabrica as f
from seguridad.models import IntentoAcceso, LogAuditoria
from seguridad.servicios import autorizar_con_clave
from seguridad.views_actividad import etiqueta_operacion
from ventas import caja
from ventas import servicios as ventas

D = Decimal


def rechazos(**filtro):
    return LogAuditoria.objects.filter(resultado_operacion='RECHAZADA', **filtro)


class Base(TestCase):

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.admin = f.usuario('40000003', 'Administrador', nombre='Ana')
        self.prov = f.proveedor()
        self.alfajor = f.producto('Alfajor', prov=self.prov, precio='1000',
                                  costo='500')
        f.comprar(self.encargada, self.prov, (self.alfajor, 50, '500'))
        self.turno = f.abrir_turno(self.operador, fondo='5000')
        self.venta, _ = ventas.registrar(
            self.turno, self.operador, [ventas.Item(self.alfajor, D('2'))],
            [ventas.Pago(f.efectivo())])

    def pedir_anulacion(self, **extra):
        url = reverse('ventas:cambios') + f'?venta={self.venta.pk}'
        token = self.client.get(url).context['token_anulacion']
        datos = {'token_operacion': token, 'motivo': 'ERROR_DE_CARGA',
                 'desde': 'cambios'}
        datos.update(extra)
        return self.client.post(
            reverse('ventas:anular_venta', args=[self.venta.pk]), datos)


# ===========================================================================
class ModeloTest(Base):

    def test_lo_ya_asentado_nace_exitoso(self):
        asiento = LogAuditoria.objects.create(
            operacion_realizada='ALTA DE RUBRO', modulo_afectado='STOCK')
        asiento.refresh_from_db()
        self.assertEqual(asiento.resultado_operacion, 'EXITOSA')

    def test_un_rechazo_no_puede_traer_fotos(self):
        """No cambió ninguna fila: una foto de "después" sería mentira."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            LogAuditoria.objects.create(
                operacion_realizada='ANULACION DE VENTA', modulo_afectado='VENTAS',
                resultado_operacion='RECHAZADA', registro_nuevo={'x': 1})

    def test_el_resultado_tiene_dominio_cerrado(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            LogAuditoria.objects.create(
                operacion_realizada='ALTA DE RUBRO', modulo_afectado='STOCK',
                resultado_operacion='A MEDIAS')


# ===========================================================================
class AccesoDenegadoTest(Base):

    def test_entrar_sin_permiso_queda_asentado(self):
        f.entrar(self.client, self.operador)
        self.client.get(reverse('seguridad:actividad'))
        asiento = rechazos(operacion_realizada='ACCESO DENEGADO').get()
        self.assertEqual(asiento.id_usuario, self.operador)
        self.assertIn('ver_auditoria', asiento.detalle_operacion)
        self.assertIn('/', asiento.detalle_operacion)
        self.assertIsNone(asiento.registro_nuevo)

    def test_quien_tiene_permiso_no_deja_rechazo(self):
        f.entrar(self.client, self.admin)
        self.client.get(reverse('seguridad:actividad'))
        self.assertFalse(rechazos().exists())

    def test_el_ticket_de_otra_cajera_tambien(self):
        otra = f.usuario('40000009', 'Operador', nombre='Fede')
        f.entrar(self.client, otra)
        self.client.get(reverse('ventas:ticket', args=[self.venta.pk]))
        self.assertTrue(rechazos(operacion_realizada='ACCESO DENEGADO').exists())


# ===========================================================================
class AutorizacionTest(Base):

    def test_autorizarse_a_si_mismo_queda_asentado_sin_la_clave(self):
        f.entrar(self.client, self.operador)
        self.pedir_anulacion(autoriza_usuario='40000001', autoriza_clave=f.CLAVE)
        asiento = rechazos(operacion_realizada='AUTORIZACION DE MOSTRADOR').get()
        self.assertIn('a sí mismo', asiento.detalle_operacion)
        self.assertIn(f'anular la venta N° {self.venta.pk}', asiento.detalle_operacion)
        self.assertNotIn(f.CLAVE, asiento.detalle_operacion)
        self.assertEqual(asiento.modulo_afectado, 'VENTAS')

    def test_la_clave_equivocada_va_a_los_dos_libros(self):
        """INTENTOS_ACCESO sigue frenando a quien prueba claves; el log
        cuenta qué se quiso autorizar."""
        f.entrar(self.client, self.operador)
        self.pedir_anulacion(autoriza_usuario='40000002', autoriza_clave='mal')
        self.assertTrue(IntentoAcceso.objects.filter(
            motivo_rechazo='CLAVE_INCORRECTA').exists())
        asiento = rechazos(operacion_realizada='AUTORIZACION DE MOSTRADOR').get()
        self.assertNotIn('mal', asiento.detalle_operacion.split('"')[0])
        self.venta.refresh_from_db()
        self.assertFalse(self.venta.anulada)

    def test_la_vista_previa_no_asienta(self):
        with self.assertRaises(ValidationError):
            autorizar_con_clave(RequestFactory().post('/'), '40000002', 'mal', 'vender',
                                para='autorizar el descuento', auditar=False)
        self.assertFalse(rechazos().exists())

    def test_sin_permiso_tambien(self):
        with self.assertRaises(ValidationError):
            autorizar_con_clave(None, '40000001', f.CLAVE, 'anular_venta',
                                para='anular la venta')
        self.assertIn('no tiene ese permiso',
                      rechazos().get().detalle_operacion)


# ===========================================================================
class AnulacionTest(Base):

    def test_una_anulacion_con_el_turno_cerrado_queda_rechazada(self):
        f.entrar(self.client, self.encargada)
        url = reverse('ventas:ficha_venta', args=[self.venta.pk])
        token = self.client.get(url).context['token_anulacion']
        caja.cerrar(self.turno, self.operador, D('7000'))
        self.client.post(reverse('ventas:anular_venta', args=[self.venta.pk]),
                         {'token_operacion': token, 'motivo': 'ERROR_DE_CARGA'})
        asiento = rechazos(operacion_realizada='ANULACION DE VENTA').get()
        self.assertEqual((asiento.tabla_afectada, asiento.id_registro),
                         ('ventas', self.venta.pk))
        self.assertIn('ya se cerró', asiento.detalle_operacion)
        # y la anulación exitosa sigue siendo otra cosa
        self.assertFalse(LogAuditoria.objects.filter(
            operacion_realizada='ANULACION DE VENTA',
            resultado_operacion='EXITOSA').exists())

    def test_un_error_de_carga_no_se_asienta(self):
        """Sin motivo no es un intento de saltear un control: es un
        formulario incompleto, y no llena el libro de ruido."""
        f.entrar(self.client, self.encargada)
        url = reverse('ventas:ficha_venta', args=[self.venta.pk])
        token = self.client.get(url).context['token_anulacion']
        self.client.post(reverse('ventas:anular_venta', args=[self.venta.pk]),
                         {'token_operacion': token, 'motivo': ''})
        self.assertFalse(rechazos().exists())

    def test_la_anulacion_hecha_sigue_exitosa(self):
        f.entrar(self.client, self.operador)
        self.pedir_anulacion(autoriza_usuario='40000002', autoriza_clave=f.CLAVE)
        asiento = LogAuditoria.objects.get(operacion_realizada='ANULACION DE VENTA')
        self.assertEqual(asiento.resultado_operacion, 'EXITOSA')
        self.assertIsNotNone(asiento.registro_nuevo)


# ===========================================================================
class FiltrosTest(Base):

    def setUp(self):
        super().setUp()
        f.entrar(self.client, self.operador)
        self.client.get(reverse('seguridad:actividad'))      # un rechazo
        LogAuditoria.objects.create(operacion_realizada='ALTA DE RUBRO',
                                    modulo_afectado='STOCK')
        LogAuditoria.objects.create(operacion_realizada='BAJA DE RUBRO',
                                    modulo_afectado='STOCK')
        f.entrar(self.client, self.admin)
        self.url = reverse('seguridad:actividad')

    def filas(self, **filtros):
        return self.client.get(self.url, filtros).context['registros']

    def test_filtra_solo_los_rechazos(self):
        filas = self.filas(resultado='RECHAZADA')
        self.assertTrue(filas)
        self.assertTrue(all(r.resultado_operacion == 'RECHAZADA' for r in filas))

    def test_filtra_por_tipo_de_operacion(self):
        filas = self.filas(operacion='ALTA')
        self.assertEqual([r.operacion_realizada for r in filas], ['ALTA DE RUBRO'])
        self.assertEqual(len(self.filas(operacion='ACCESO DENEGADO')), 1)

    def test_el_combo_ofrece_los_tipos_que_hay_en_el_libro(self):
        formulario = self.client.get(self.url).context['formulario']
        tipos = dict(formulario.fields['operacion'].choices)
        self.assertEqual(tipos['ALTA'], 'Alta')
        self.assertEqual(tipos['ACCESO DENEGADO'], 'Acceso denegado')
        self.assertNotIn('ANULACION', tipos)
        self.assertEqual(etiqueta_operacion('MODIFICACION'), 'Modificación')

    def test_la_pantalla_marca_el_rechazo(self):
        respuesta = self.client.get(self.url, {'resultado': 'RECHAZADA'})
        self.assertContains(respuesta, 'Rechazada')
        asiento = rechazos().first()
        ficha = self.client.get(f'{self.url}{asiento.pk}/')
        self.assertContains(ficha, 'no cambió ningún dato')

    def test_la_exportacion_lleva_el_resultado(self):
        respuesta = self.client.get(reverse('seguridad:exportar_actividad'),
                                    {'formato': 'xlsx', 'resultado': 'RECHAZADA'})
        self.assertEqual(respuesta.status_code, 200)


# ===========================================================================
class HistoricoTest(TestCase):

    def test_el_resultado_viaja_al_historico(self):
        viejo = timezone.now() - timedelta(days=800)
        asiento = LogAuditoria.objects.create(
            operacion_realizada='ACCESO DENEGADO', modulo_afectado='SEGURIDAD',
            resultado_operacion='RECHAZADA', fecha_hora=viejo)
        call_command('pasar_a_historico', '--ejecutar', stdout=StringIO())
        self.assertFalse(LogAuditoria.objects.filter(pk=asiento.pk).exists())
        with connection.cursor() as cursor:
            cursor.execute('SELECT resultado_operacion FROM hist_log_auditoria '
                           'WHERE id_auditoria = %s', [asiento.pk])
            self.assertEqual(cursor.fetchone(), ('RECHAZADA',))
