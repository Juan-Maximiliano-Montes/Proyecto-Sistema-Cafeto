"""Anulación de venta (Hito 4): estado, reversos, permisos y consulta.

    AnulacionTest       el servicio: stock, caja y puntos vuelven atrás
    ReglasTest          cuándo NO se anula (turno cerrado, cambios, perfil)
                        y quién la pide y quién la autoriza (DD-61)
    ReportesTest        las anuladas no suman en reportes ni recaudación
    PantallaTest        la ficha, el formulario, el log y el filtro de estado
    HistoricoTest       una venta anulada también se archiva
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from fidelizacion.models import MovimientoPuntos
from fidelizacion.tests.test_club import beneficio, cliente
from negocio import reportes
from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria
from stock.models import MovimientoStock
from ventas import anulacion, cambios, caja
from ventas import servicios as ventas
from ventas.models import MovimientoCaja, TurnoCaja, Venta

D = Decimal


class Base(TestCase):

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.prov = f.proveedor()
        self.alfajor = f.producto('Alfajor', prov=self.prov, precio='1000',
                                  costo='500')
        self.yogur = f.producto('Yogur', prov=self.prov, precio='800',
                                costo='400', controla_vencimiento=True)
        f.comprar(self.encargada, self.prov, (self.alfajor, 50, '500'),
                  (self.yogur, 10, '400', [(f.en_dias(10), 10)]))
        self.turno = f.abrir_turno(self.operador, fondo='5000')

    def vender(self, *items, pagos=None, cliente_=None, beneficios=()):
        lista = [ventas.Item(p, D(str(c)), *resto) for p, c, *resto in items]
        venta, _ = ventas.registrar(
            self.turno, self.operador, lista,
            pagos if pagos is not None else [ventas.Pago(f.efectivo())],
            cliente=cliente_, beneficios=beneficios)
        return venta

    def stock(self, producto):
        producto.refresh_from_db()
        return producto.stock_actual


# ===========================================================================
class AnulacionTest(Base):

    def test_la_venta_queda_anulada_con_fecha_usuario_y_motivo(self):
        venta = self.vender((self.alfajor, 2))
        self.assertEqual(venta.estado_venta, 'COMPLETADA')   # estado automático
        anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA')
        venta.refresh_from_db()
        self.assertTrue(venta.anulada)
        self.assertEqual(venta.id_usuario_anulacion, self.encargada)
        self.assertEqual(venta.motivo_anulacion, 'ERROR_DE_CARGA')
        self.assertIsNotNone(venta.fecha_anulacion)
        # sin baja física: sigue con sus renglones
        self.assertEqual(venta.detalles.count(), 1)

    def test_el_stock_vuelve_al_mismo_lote(self):
        lote = self.yogur.lotes.get()
        venta = self.vender((self.alfajor, 3), (self.yogur, 2))
        self.assertEqual(self.stock(self.alfajor), 47)
        anulacion.anular(venta, self.encargada, 'CLIENTE_DESISTIO')
        self.assertEqual(self.stock(self.alfajor), 50)
        self.assertEqual(self.stock(self.yogur), 10)
        lote.refresh_from_db()
        self.assertEqual(lote.stock_lote, 10)
        reversos = MovimientoStock.objects.filter(
            id_venta=venta, tipo_movimiento='ANULACION')
        self.assertEqual(reversos.count(), 2)
        self.assertEqual(reversos.get(id_producto=self.yogur).id_lote, lote)

    def test_el_efectivo_sale_del_cajon(self):
        venta = self.vender((self.alfajor, 2),
                            pagos=[ventas.Pago(f.efectivo(), recibido=D('5000'))])
        self.assertEqual(caja.saldo(self.turno), D('7000'))
        resultado = anulacion.anular(venta, self.encargada, 'COBRO_DUPLICADO')
        self.assertEqual(resultado.efectivo, D('2000'))
        self.assertEqual(caja.saldo(self.turno), D('5000'))
        asiento = MovimientoCaja.objects.get(id_venta=venta,
                                             tipo_movimiento='ANULACION')
        self.assertEqual(asiento.monto, D('-2000'))

    def test_lo_cobrado_fuera_del_cajon_no_mueve_la_caja(self):
        venta = self.vender((self.alfajor, 1),
                            pagos=[ventas.Pago(f.medio('Transferencia'))])
        resultado = anulacion.anular(venta, self.encargada, 'PAGO_RECHAZADO')
        self.assertIsNone(resultado.efectivo)
        self.assertEqual(resultado.medios_externos, ['Transferencia'])
        self.assertFalse(MovimientoCaja.objects.filter(id_venta=venta).exists())

    def test_los_puntos_vuelven_y_el_canje_se_devuelve(self):
        sofia = cliente()
        canje = beneficio('Bronze', '20% canjeando', 'CANJEABLE', 'DESCUENTO',
                          porcentaje=D('20'), puntos=50)
        self.vender((self.alfajor, 10), cliente_=sofia)           # +100
        venta = self.vender((self.alfajor, 5), cliente_=sofia,
                            beneficios=[canje.pk])                 # -50 +40
        sofia.refresh_from_db()
        self.assertEqual(sofia.puntos_disponibles, 90)
        resultado = anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA')
        self.assertEqual((resultado.puntos.acumulacion, resultado.puntos.canjes),
                         (40, 50))
        sofia.refresh_from_db()
        self.assertEqual(sofia.puntos_disponibles, 100)   # como si no hubiera existido
        self.assertEqual(sofia.puntos_acumulados, 100)

    def test_otro_exige_detalle(self):
        venta = self.vender((self.alfajor, 1))
        with self.assertRaisesMessage(ValidationError, 'Contá por qué'):
            anulacion.anular(venta, self.encargada, 'OTRO', '  ')
        with self.assertRaisesMessage(ValidationError, 'Elegí el motivo'):
            anulacion.anular(venta, self.encargada, 'CUALQUIERA')
        anulacion.anular(venta, self.encargada, 'OTRO', 'Se probó la impresora')
        venta.refresh_from_db()
        self.assertEqual(anulacion.motivo_de(venta),
                         'Otro (explicar): Se probó la impresora')

    def test_no_se_anula_dos_veces(self):
        venta = self.vender((self.alfajor, 1))
        anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA')
        with self.assertRaisesMessage(ValidationError, 'ya está anulada'):
            anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA')
        self.assertEqual(MovimientoStock.objects.filter(
            id_venta=venta, tipo_movimiento='ANULACION').count(), 1)

    def test_una_anulada_no_se_modifica(self):
        venta = self.vender((self.alfajor, 1))
        anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA')
        venta.refresh_from_db()
        venta.motivo_anulacion = 'OTRO'
        with self.assertRaisesMessage(ValidationError, 'no se modifica'):
            venta.save(update_fields=['motivo_anulacion'])

    def test_la_base_exige_los_datos_de_la_anulacion(self):
        venta = self.vender((self.alfajor, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Venta.objects.filter(pk=venta.pk).update(estado_venta='ANULADA')

    def test_si_falta_efectivo_no_se_anula_nada(self):
        venta = self.vender((self.alfajor, 2))
        caja.retiro(self.turno, self.encargada, D('6500'), 'Depósito')
        with self.assertRaisesMessage(ValidationError, 'No hay suficiente efectivo'):
            anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA')
        venta.refresh_from_db()
        self.assertFalse(venta.anulada)
        self.assertEqual(self.stock(self.alfajor), 48)


# ===========================================================================
class ReglasTest(Base):

    def test_el_operador_vende_pero_no_anula(self):
        venta = self.vender((self.alfajor, 1))
        with self.assertRaisesMessage(ValidationError, 'lo autoriza el Encargado'):
            anulacion.anular(venta, self.operador, 'ERROR_DE_CARGA')

    def test_el_operador_la_pide_y_la_encargada_la_autoriza(self):
        venta = self.vender((self.alfajor, 1))
        anulacion.anular(venta, self.operador, 'COBRO_DUPLICADO',
                         autorizante=self.encargada)
        venta.refresh_from_db()
        self.assertEqual(venta.id_usuario_solicitante, self.operador)
        self.assertEqual(venta.id_usuario_anulacion, self.encargada)

    def test_un_autorizante_sin_permiso_no_sirve(self):
        otro = f.usuario('40000005', 'Operador', nombre='Rodo')
        venta = self.vender((self.alfajor, 1))
        with self.assertRaisesMessage(ValidationError, 'no tiene permiso'):
            anulacion.anular(venta, self.operador, 'ERROR_DE_CARGA',
                             autorizante=otro)

    def test_quien_vendio_no_autoriza_su_propia_anulacion(self):
        venta, _ = ventas.registrar(
            self.turno, self.encargada, [ventas.Item(self.alfajor, D('1'))],
            [ventas.Pago(f.efectivo())])
        with self.assertRaisesMessage(ValidationError, 'no quien hizo la venta'):
            anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA')
        duenia = f.usuario('40000009', 'Administrador', nombre='Lucía')
        anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA',
                         autorizante=duenia)
        venta.refresh_from_db()
        self.assertEqual((venta.id_usuario_solicitante, venta.id_usuario_anulacion),
                         (self.encargada, duenia))

    def test_el_modelo_tampoco_deja_que_el_vendedor_anule(self):
        venta = self.vender((self.alfajor, 1))
        venta.estado_venta = 'ANULADA'
        venta.fecha_anulacion = timezone.now()
        venta.id_usuario_anulacion = self.operador
        venta.id_usuario_solicitante = self.operador
        venta.motivo_anulacion = 'ERROR_DE_CARGA'
        with self.assertRaisesMessage(ValidationError, 'otra persona'):
            venta.save(update_fields=['estado_venta', 'fecha_anulacion',
                                      'id_usuario_anulacion',
                                      'id_usuario_solicitante',
                                      'motivo_anulacion'])

    def test_con_el_turno_cerrado_es_una_devolucion(self):
        venta = self.vender((self.alfajor, 1))
        caja.cerrar(self.turno, self.operador, D('6000'))
        with self.assertRaisesMessage(ValidationError, 'ya se cerró'):
            anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA')

    def test_con_cambios_no_se_anula(self):
        venta = self.vender((self.alfajor, 2))
        cambios.registrar(
            self.turno, self.operador, venta, 'PRODUCTO_DANADO',
            [cambios.Devuelto(venta.detalles.get(), D('1'), False)],
            [cambios.Entregado(self.alfajor, D('1'))])
        with self.assertRaisesMessage(ValidationError, 'tuvo cambios'):
            anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA')

    def test_una_anulada_no_admite_cambios(self):
        venta = self.vender((self.alfajor, 2))
        anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA')
        with self.assertRaisesMessage(ValidationError, 'está anulada'):
            cambios.registrar(
                self.turno, self.operador, venta, 'PRODUCTO_DANADO',
                [cambios.Devuelto(venta.detalles.get(), D('1'), True)],
                [cambios.Entregado(self.alfajor, D('1'))])


# ===========================================================================
class ReportesTest(Base):

    def test_las_anuladas_no_suman(self):
        self.vender((self.alfajor, 1))
        anulada = self.vender((self.alfajor, 3))
        anulacion.anular(anulada, self.encargada, 'ERROR_DE_CARGA')
        hoy = timezone.localdate()
        _, filas, total = reportes.ventas_por_dia(hoy, hoy)
        self.assertEqual(total[1:3], [1, D('1000')])
        _, filas, total = reportes.productos(hoy, hoy)
        self.assertEqual(filas[0][1], 1)
        _, filas, _ = reportes.medios(hoy, hoy)
        self.assertEqual(filas[0][2], D('1000'))
        efectivo = [r for r in caja.recaudacion(self.turno)
                    if r['medio'] == 'Efectivo'][0]
        self.assertEqual(efectivo['total'], D('1000'))
        # el libro de caja dice lo mismo: 5.000 + 1.000 + 3.000 - 3.000
        self.assertEqual(caja.saldo(self.turno), D('6000'))


# ===========================================================================
class PantallaTest(Base):

    def setUp(self):
        super().setUp()
        self.venta = self.vender((self.alfajor, 2))

    def ficha(self):
        return self.client.get(reverse('ventas:ficha_venta', args=[self.venta.pk]))

    def test_anular_desde_la_ficha_deja_el_log_con_antes_y_despues(self):
        f.entrar(self.client, self.encargada)
        token = self.ficha().context['token_anulacion']
        respuesta = self.client.post(
            reverse('ventas:anular_venta', args=[self.venta.pk]),
            {'token_operacion': token, 'motivo': 'CLIENTE_DESISTIO'})
        self.assertRedirects(respuesta, reverse('ventas:ventas')
                             + f'?numero={self.venta.pk}',
                             fetch_redirect_response=False)
        self.venta.refresh_from_db()
        self.assertTrue(self.venta.anulada)
        log = LogAuditoria.objects.get(operacion_realizada='ANULACION DE VENTA')
        self.assertEqual(log.registro_anterior['estado_venta'], 'COMPLETADA')
        self.assertEqual(log.registro_nuevo['estado_venta'], 'ANULADA')
        self.assertEqual(log.registro_nuevo['motivo_anulacion'], 'CLIENTE_DESISTIO')
        # el mismo formulario enviado dos veces no hace nada
        self.client.post(reverse('ventas:anular_venta', args=[self.venta.pk]),
                         {'token_operacion': token, 'motivo': 'CLIENTE_DESISTIO'})
        self.assertEqual(LogAuditoria.objects.filter(
            operacion_realizada='ANULACION DE VENTA').count(), 1)
        # la ficha y el ticket la muestran anulada
        self.assertContains(self.ficha(), 'Venta anulada')
        ticket = self.client.get(reverse('ventas:ticket', args=[self.venta.pk]))
        self.assertContains(ticket, 'VENTA ANULADA')

    def test_el_cajero_la_pide_desde_cambios_y_autoriza_la_encargada(self):
        f.entrar(self.client, self.operador)
        url = reverse('ventas:cambios') + f'?venta={self.venta.pk}'
        pantalla = self.client.get(url)
        self.assertTrue(pantalla.context['pide_clave'])
        datos = {'token_operacion': pantalla.context['token_anulacion'],
                 'motivo': 'COBRO_DUPLICADO', 'desde': 'cambios'}
        # sin la clave de alguien con permiso, no pasa nada
        respuesta = self.client.post(
            reverse('ventas:anular_venta', args=[self.venta.pk]), datos)
        self.assertRedirects(respuesta, url, fetch_redirect_response=False)
        self.venta.refresh_from_db()
        self.assertFalse(self.venta.anulada)
        # con la clave de la encargada, sí; y quedan los dos nombres
        datos.update(autoriza_usuario='40000002', autoriza_clave=f.CLAVE)
        self.client.post(reverse('ventas:anular_venta', args=[self.venta.pk]), datos)
        self.venta.refresh_from_db()
        self.assertTrue(self.venta.anulada)
        self.assertEqual(self.venta.id_usuario_solicitante, self.operador)
        self.assertEqual(self.venta.id_usuario_anulacion, self.encargada)
        log = LogAuditoria.objects.get(operacion_realizada='ANULACION DE VENTA')
        self.assertIn('autorizó', log.detalle_operacion)
        self.assertContains(self.client.get(url), 'está anulada')

    def test_el_cajero_no_se_autoriza_a_si_mismo(self):
        f.entrar(self.client, self.operador)
        url = reverse('ventas:cambios') + f'?venta={self.venta.pk}'
        token = self.client.get(url).context['token_anulacion']
        self.client.post(reverse('ventas:anular_venta', args=[self.venta.pk]), {
            'token_operacion': token, 'motivo': 'ERROR_DE_CARGA', 'desde': 'cambios',
            'autoriza_usuario': '40000001', 'autoriza_clave': f.CLAVE})
        self.venta.refresh_from_db()
        self.assertFalse(self.venta.anulada)

    def test_en_una_venta_propia_la_ficha_pide_otra_clave(self):
        propia, _ = ventas.registrar(
            self.turno, self.encargada, [ventas.Item(self.alfajor, D('1'))],
            [ventas.Pago(f.efectivo())])
        f.entrar(self.client, self.encargada)
        ficha = self.client.get(reverse('ventas:ficha_venta', args=[propia.pk]))
        self.assertTrue(ficha.context['pide_clave'])
        self.assertContains(ficha, 'Es una venta tuya')
        self.assertFalse(self.ficha().context['pide_clave'])

    def test_la_consulta_filtra_por_estado_y_excluye_del_total(self):
        otra = self.vender((self.alfajor, 1))
        anulacion.anular(otra, self.encargada, 'ERROR_DE_CARGA')
        f.entrar(self.client, self.encargada)
        url = reverse('ventas:ventas')
        todas = self.client.get(url)
        self.assertEqual(todas.context['cantidad'], 1)
        self.assertEqual(todas.context['facturado'], D('2000'))
        self.assertEqual(todas.context['anuladas'], 1)
        solo = self.client.get(url, {'estado': 'ANULADA'})
        self.assertEqual([v.pk for v in solo.context['filas']], [otra.pk])
        hoy = timezone.localdate().isoformat()
        excel = self.client.get(reverse('ventas:exportar_ventas'),
                                {'desde': hoy, 'hasta': hoy, 'formato': 'xlsx'})
        self.assertEqual(excel.status_code, 200)

    def test_con_el_turno_cerrado_la_ficha_explica_por_que(self):
        caja.cerrar(self.turno, self.operador, D('7000'))
        f.entrar(self.client, self.encargada)
        respuesta = self.ficha()
        self.assertNotIn('token_anulacion', respuesta.context)
        self.assertIn('ya se cerró', respuesta.context['no_se_anula'])


# ===========================================================================
class HistoricoTest(Base):

    def test_una_venta_anulada_se_archiva_con_su_estado(self):
        venta = self.vender((self.alfajor, 1))
        anulacion.anular(venta, self.encargada, 'ERROR_DE_CARGA')
        caja.cerrar(self.turno, self.operador, D('5000'))
        hace = timezone.now() - timedelta(days=800)
        TurnoCaja.objects.filter(pk=self.turno.pk).update(
            fecha_apertura=hace, fecha_cierre=hace)
        call_command('pasar_a_historico', '--ejecutar', stdout=StringIO())
        self.assertFalse(Venta.objects.filter(pk=venta.pk).exists())
        with connection.cursor() as cursor:
            cursor.execute('SELECT estado_venta, motivo_anulacion FROM hist_ventas '
                           'WHERE id_venta = %s', [venta.pk])
            self.assertEqual(cursor.fetchone(), ('ANULADA', 'ERROR_DE_CARGA'))
            cursor.execute('SELECT estado_venta FROM v_ventas_completo '
                           'WHERE id_venta = %s', [venta.pk])
            self.assertEqual(cursor.fetchone(), ('ANULADA',))
        self.assertEqual(MovimientoPuntos.objects.count(), 0)
