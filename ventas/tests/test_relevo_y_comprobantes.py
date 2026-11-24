"""Relevo de caja, diferencias sin aprobar, corte X y comprobantes (DD-97).

* El relevo pasa el navegador a la cuenta de quien entra, con rastro de
  quién salió y quién entró.
* Quien llega y encuentra el turno de otro abierto lo cierra por relevo y
  abre el suyo: el turno cerrado dice quién abrió y quién cerró.
* La clave de quien recibe el cajón es custodia, no perdón: nunca aprueba
  una diferencia. La diferencia la aprueba quien supervisa (al lado o a
  distancia) o queda pendiente de revisión, con aviso rojo.
* El arqueo parcial (X) no cierra el turno y sigue siendo ciego.
* Cada comprobante imprimible dice lo que tiene que decir.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from fidelizacion import credito
from fidelizacion.models import MovimientoCuenta
from negocio import panel
from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria, Sesion
from ventas import a_distancia
from ventas import caja
from ventas import servicios as ventas
from ventas.models import (
    EstadoSolicitud,
    MedioPago,
    MovimientoCaja,
    SolicitudAutorizacion,
    TurnoCaja,
    ViaAprobacion,
)

D = Decimal


class Base(TestCase):

    def setUp(self):
        self.ana = f.usuario('40200001', 'Operador', nombre='Ana', apellido='Paz')
        self.beto = f.usuario('40200002', 'Operador', nombre='Beto', apellido='Ruiz')
        self.lucre = f.usuario('40200003', 'Encargado', nombre='Lucrecia', apellido='Sosa')
        self.cajon = f.caja_cafeteria()
        self.prov = f.proveedor()
        self.alfajor = f.producto('Alfajor', prov=self.prov, precio='1000', costo='600')
        f.comprar(self.lucre, self.prov, (self.alfajor, 50, '600'))

    def entrar(self, usuario):
        self.client.logout()
        f.entrar(self.client, usuario)
        self.client.post(reverse('ventas:elegir_caja'), {'caja': self.cajon.pk})

    def abrir(self, quien, fondo='5000'):
        return caja.abrir(self.cajon, quien, D(fondo))

    def declarar(self, monto, modo='CIERRE'):
        token = self.client.get(reverse('ventas:caja')).context['form_arqueo'].initial['token_operacion']
        return self.client.post(reverse('ventas:declarar_arqueo'),
                                {'token_operacion': token, 'monto_contado': monto, 'modo': modo})

    def revelar(self):
        """El conteo que no coincide queda fijo y se muestra la diferencia."""
        return self.client.post(reverse('ventas:cierre'), {'cerrar_igual': '1'})

    def confirmar(self, **extra):
        token = self.client.get(reverse('ventas:cierre')).context['form'].initial['token_operacion']
        return self.client.post(reverse('ventas:cierre'), {'token_operacion': token, **extra})


# ===========================================================================
#  1. Relevo: el navegador pasa a quien entra
# ===========================================================================
class RelevoConCambioDeSesionTest(Base):

    def test_el_relevo_pasa_la_sesion_a_quien_entra_y_deja_el_rastro(self):
        turno = self.abrir(self.ana)
        self.entrar(self.ana)
        sesion_ana = Sesion.objects.get(id_usuario=self.ana, activa=True)
        self.declarar('5000', modo='RELEVO')
        respuesta = self.confirmar(entrante_usuario='40200002', entrante_clave=f.CLAVE)

        self.assertRedirects(respuesta, reverse('ventas:recaudacion', args=[turno.pk]),
                             fetch_redirect_response=False)
        # La sesión de Django es de Beto, con su sesión de negocio nueva y en el mismo cajón.
        self.assertEqual(self.client.session['usuario_id'], self.beto.pk)
        nueva = Sesion.objects.get(id_usuario=self.beto, activa=True)
        self.assertEqual(self.client.session['id_sesion_negocio'], nueva.pk)
        self.assertEqual(nueva.id_caja_registradora, self.cajon)
        sesion_ana.refresh_from_db()
        self.assertFalse(sesion_ana.activa)
        self.assertEqual(sesion_ana.motivo_cierre, 'CIERRE_MANUAL')
        # El Registro de Actividad dice quién salió (con su sesión) y quién entró (con la suya).
        salida = LogAuditoria.objects.get(operacion_realizada='CIERRE DE SESION', id_usuario=self.ana)
        self.assertEqual(salida.id_sesion_id, sesion_ana.pk)
        self.assertIn('Beto Ruiz', salida.detalle_operacion)
        entrada = LogAuditoria.objects.filter(operacion_realizada='INICIO DE SESION',
                                              id_usuario=self.beto).latest('pk')
        self.assertEqual(entrada.id_sesion_id, nueva.pk)
        self.assertIn('Ana Paz', entrada.detalle_operacion)
        # El aviso y el comprobante, ya con la cuenta de Beto (que firma haberlo recibido).
        comprobante = self.client.get(respuesta['Location'])
        self.assertEqual(comprobante.status_code, 200)
        avisos = [str(m) for m in comprobante.context['messages']]
        self.assertIn('Turno relevado: ahora estás operando con la cuenta de Beto Ruiz.', avisos)
        self.assertContains(comprobante, 'CIERRE POR RELEVO')
        self.assertContains(comprobante, 'Beto Ruiz @')
        # El turno nuevo es de Beto, con lo contado; el cerrado apunta a él.
        nuevo = TurnoCaja.objects.get(estado_turno='ABIERTA', id_caja_registradora=self.cajon)
        self.assertEqual(nuevo.id_usuario_apertura, self.beto)
        self.assertEqual(nuevo.id_turno_anterior_id, turno.pk)
        self.assertEqual(self.client.get(reverse('ventas:caja')).context['turno'], nuevo)

    def test_con_la_clave_equivocada_no_hay_relevo_ni_cambio_de_sesion(self):
        turno = self.abrir(self.ana)
        self.entrar(self.ana)
        self.declarar('5000', modo='RELEVO')
        respuesta = self.confirmar(entrante_usuario='40200002', entrante_clave='otra')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.client.session['usuario_id'], self.ana.pk)
        turno.refresh_from_db()
        self.assertTrue(turno.abierto)

    def test_quien_entra_tiene_que_ser_otra_persona(self):
        self.abrir(self.ana)
        self.entrar(self.ana)
        self.declarar('5000', modo='RELEVO')
        respuesta = self.confirmar(entrante_usuario='40200001', entrante_clave=f.CLAVE)
        # El turno es suyo: el mensaje dice qué elegir, no culpa a una clave (DD-101).
        self.assertContains(respuesta, 'tiene que ser el compañero que entra')
        self.assertEqual(self.client.session['usuario_id'], self.ana.pk)


# ===========================================================================
#  2. Quien abrió se fue sin cerrar
# ===========================================================================
class RelevoEnAusenciaTest(Base):

    def test_quien_llega_cierra_el_turno_del_otro_y_abre_el_suyo(self):
        turno = self.abrir(self.ana)
        ventas.registrar(turno, self.ana, [ventas.Item(self.alfajor, D('1'))], [ventas.Pago(f.efectivo())])
        self.entrar(self.beto)
        pantalla = self.client.get(reverse('ventas:caja'))
        self.assertTrue(pantalla.context['puede_ausencia'])
        self.assertContains(pantalla, 'Cerrar turno anterior por relevo y abrir mi turno')
        self.declarar('6000', modo='AUSENCIA')
        respuesta = self.confirmar()
        self.assertRedirects(respuesta, reverse('ventas:recaudacion', args=[turno.pk]),
                             fetch_redirect_response=False)

        turno.refresh_from_db()
        self.assertEqual(turno.id_usuario_apertura, self.ana)
        self.assertEqual(turno.id_usuario_cierre, self.beto)
        self.assertTrue(turno.relevo_en_ausencia)
        self.assertEqual(turno.tipo_cierre, 'RELEVO')
        self.assertEqual(turno.diferencia_caja, 0)
        nuevo = turno.siguiente
        self.assertEqual(nuevo.id_usuario_apertura, self.beto)
        self.assertEqual(nuevo.monto_inicial, D('6000'))
        self.assertEqual(self.client.session['usuario_id'], self.beto.pk)

        comprobante = self.client.get(respuesta['Location'])
        self.assertContains(comprobante, 'CIERRE POR RELEVO')
        self.assertContains(comprobante, 'Cerrado en relevo por Beto Ruiz en ausencia de Ana Paz')
        html = comprobante.content.decode()
        self.assertIn('Ana Paz @', html)
        self.assertIn('Beto Ruiz @', html)
        cierre = LogAuditoria.objects.filter(operacion_realizada='CIERRE DE TURNO DE CAJA').latest('pk')
        self.assertIn('en ausencia de Ana Paz', cierre.detalle_operacion)

    def test_quien_abrio_no_puede_cerrar_su_propio_turno_en_ausencia(self):
        turno = self.abrir(self.ana)
        self.entrar(self.ana)
        self.assertFalse(self.client.get(reverse('ventas:caja')).context['puede_ausencia'])
        self.declarar('5000', modo='AUSENCIA')
        turno.refresh_from_db()
        self.assertTrue(turno.abierto)
        self.assertNotIn('cafeto_arqueo', self.client.session)

    def test_el_servicio_tambien_lo_impide(self):
        turno = self.abrir(self.ana)
        with self.assertRaisesMessage(Exception, 'lo abriste vos'):
            caja.relevar(turno, self.ana, D('5000'), self.ana, en_ausencia=True)


# ===========================================================================
#  3. Custodia no es perdón: quién aprueba una diferencia
# ===========================================================================
class DiferenciaSinFrenarLaFilaTest(Base):

    def setUp(self):
        super().setUp()
        self.turno = self.abrir(self.ana)
        self.entrar(self.ana)
        self.declarar('3000', modo='RELEVO')      # faltan 2.000: supera la tolerancia
        self.revelar()

    def test_la_clave_de_quien_recibe_no_aprueba_la_diferencia(self):
        respuesta = self.confirmar(observacion='Faltante sin explicación', forma='ACA',
                                   entrante_usuario='40200002', entrante_clave=f.CLAVE)
        self.assertContains(respuesta, 'autorice')
        # Tampoco poniéndola como "quien autoriza": un cajero no supervisa cierres.
        respuesta = self.confirmar(observacion='Faltante sin explicación', forma='ACA',
                                   autoriza_usuario='40200002', autoriza_clave=f.CLAVE,
                                   entrante_usuario='40200002', entrante_clave=f.CLAVE)
        self.assertContains(respuesta, 'no tiene permiso')
        self.turno.refresh_from_db()
        self.assertTrue(self.turno.abierto)
        self.assertEqual(self.client.session['usuario_id'], self.ana.pk)

    def test_dejarlo_a_revisar_pide_un_motivo_que_diga_algo(self):
        respuesta = self.confirmar(observacion='x', forma='REVISAR',
                                   entrante_usuario='40200002', entrante_clave=f.CLAVE)
        self.assertContains(respuesta, 'al menos 10 letras')
        self.turno.refresh_from_db()
        self.assertTrue(self.turno.abierto)

    def test_cerrar_igual_deja_el_turno_pendiente_y_avisa_en_rojo_hasta_que_se_revise(self):
        pedido = self.client.post(reverse('ventas:pedir_autorizacion_cierre')).json()
        self.confirmar(observacion='Se pagó al sodero y no se anotó', forma='REVISAR',
                       entrante_usuario='40200002', entrante_clave=f.CLAVE)
        # Lo que se había pedido a distancia ya no espera en ninguna bandeja.
        self.assertEqual(SolicitudAutorizacion.objects.get(pk=pedido['id']).estado_solicitud,
                         EstadoSolicitud.ANULADA)
        self.turno.refresh_from_db()
        self.assertFalse(self.turno.abierto)
        self.assertTrue(self.turno.pendiente_de_revision)
        self.assertIsNone(self.turno.id_usuario_supervisor)
        self.assertEqual(self.turno.diferencia_caja, D('-2000'))
        # El relevo se hizo igual: la fila no esperó.
        self.assertEqual(self.client.session['usuario_id'], self.beto.pk)
        # Aviso rojo en el inicio de la titular y de quien supervisa, y en la API de servicio.
        duenia = f.titular()
        for quien in (duenia, self.lucre):
            _, avisos = panel.del_dia(quien)
            self.assertEqual(avisos[0]['clave'], 'CAJA_A_REVISAR')
            self.assertEqual(avisos[0]['tipo'], 'error')
            self.assertIn('faltan $', avisos[0]['texto'])
        _, avisos_cajero = panel.del_dia(self.beto)
        self.assertNotIn('CAJA_A_REVISAR', [a['clave'] for a in avisos_cajero])
        from api_servicio.views import _cajas_a_revisar
        self.assertEqual([c['turno'] for c in _cajas_a_revisar()], [self.turno.pk])
        # El comprobante lo dice.
        comprobante = self.client.get(reverse('ventas:recaudacion', args=[self.turno.pk]))
        self.assertContains(comprobante, 'Pendiente de revisión de titular')
        self.assertContains(comprobante, 'FALTANTE')
        self.assertContains(comprobante, 'Se pagó al sodero y no se anotó')
        # Un cajero no la puede dar por revisada; la encargada sí, y el aviso se va.
        self.client.post(reverse('ventas:revisar_turno', args=[self.turno.pk]))
        self.turno.refresh_from_db()
        self.assertTrue(self.turno.pendiente_de_revision)
        self.entrar(self.lucre)
        self.client.post(reverse('ventas:revisar_turno', args=[self.turno.pk]))
        self.turno.refresh_from_db()
        self.assertFalse(self.turno.pendiente_de_revision)
        self.assertEqual(self.turno.id_usuario_revision, self.lucre)
        _, avisos = panel.del_dia(self.lucre)
        self.assertNotIn('CAJA_A_REVISAR', [a['clave'] for a in avisos])
        self.assertContains(self.client.get(reverse('ventas:recaudacion', args=[self.turno.pk])),
                            'Revisó Lucrecia Sosa @')

    def test_quien_cerro_no_revisa_su_propia_diferencia(self):
        turno = TurnoCaja.objects.create(
            id_caja_registradora=f.caja_minisuper(), id_usuario_apertura=self.lucre,
            monto_inicial=D('0'))
        caja.registrar_movimiento(turno, self.lucre, 'APERTURA', D('0'), 'Fondo')
        caja.cerrar(turno, self.lucre, D('900'), 'Sobró y no sé por qué', a_revisar=True)
        with self.assertRaisesMessage(Exception, 'otra persona'):
            caja.revisar(turno, self.lucre)

    def test_a_distancia_aprueba_quien_supervisa_y_la_aprobacion_se_usa_una_vez(self):
        pedido = self.client.post(reverse('ventas:pedir_autorizacion_cierre')).json()
        self.assertTrue(pedido['ok'], pedido)
        solicitud = SolicitudAutorizacion.objects.get(pk=pedido['id'])
        self.assertEqual(solicitud.pedido['tipo'], a_distancia.CIERRE)
        control = a_distancia.control_de(solicitud)
        # Un cajero no puede aprobarla aunque le llegue el enlace.
        with self.assertRaises(Exception):
            a_distancia.resolver(solicitud, self.beto, True, ViaAprobacion.BANDEJA, control)
        a_distancia.resolver(solicitud, self.lucre, True, ViaAprobacion.ENLACE, control)
        self.confirmar(observacion='Faltante: lo vemos mañana', forma='DISTANCIA', remota=solicitud.pk,
                       entrante_usuario='40200002', entrante_clave=f.CLAVE)
        self.turno.refresh_from_db()
        self.assertEqual(self.turno.tipo_cierre, 'SUPERVISADO')
        self.assertEqual(self.turno.id_usuario_supervisor, self.lucre)
        self.assertFalse(self.turno.requiere_revision)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado_solicitud, EstadoSolicitud.USADA)
        comprobante = self.client.get(reverse('ventas:recaudacion', args=[self.turno.pk]))
        self.assertContains(comprobante, 'Lucrecia Sosa @')
        self.assertContains(comprobante, f'pedido N° {solicitud.pk}')

    def test_la_aprobacion_a_distancia_es_de_ese_conteo(self):
        pedido = self.client.post(reverse('ventas:pedir_autorizacion_cierre')).json()
        solicitud = SolicitudAutorizacion.objects.get(pk=pedido['id'])
        a_distancia.resolver(solicitud, self.lucre, True, ViaAprobacion.BANDEJA,
                             a_distancia.control_de(solicitud))
        self.assertNotEqual(a_distancia.huella_cierre(self.turno, D('3000'), 'RELEVO'),
                            a_distancia.huella_cierre(self.turno, D('3100'), 'RELEVO'))
        with self.assertRaisesMessage(Exception, 'ticket cambió'):
            a_distancia.aprobada_para(solicitud.pk, self.ana,
                                      a_distancia.huella_cierre(self.turno, D('3100'), 'RELEVO'))


# ===========================================================================
#  4. Arqueo parcial (corte X)
# ===========================================================================
class CorteXTest(Base):

    def test_el_corte_x_no_cierra_queda_fijo_y_sigue_siendo_ciego(self):
        turno = self.abrir(self.ana)
        ventas.registrar(turno, self.ana, [ventas.Item(self.alfajor, D('2'))], [ventas.Pago(f.efectivo())])
        self.entrar(self.ana)
        token = self.client.get(reverse('ventas:caja')).context['form_corte'].initial['token_operacion']
        respuesta = self.client.post(reverse('ventas:corte_x'),
                                     {'token_operacion': token, 'monto_contado': '6900',
                                      'observacion': 'Control de la tarde'})
        asiento = LogAuditoria.objects.get(operacion_realizada=caja.OPERACION_CORTE_X)
        self.assertRedirects(respuesta, reverse('ventas:comprobante_x', args=[asiento.pk]),
                             fetch_redirect_response=False)
        turno.refresh_from_db()
        self.assertTrue(turno.abierto)
        # Se vende después: el comprobante X sigue diciendo lo de ese momento.
        ventas.registrar(turno, self.ana, [ventas.Item(self.alfajor, D('1'))], [ventas.Pago(f.efectivo())])
        cajera = self.client.get(respuesta['Location'])
        self.assertContains(cajera, 'ARQUEO PARCIAL')
        self.assertContains(cajera, '6.900,00')
        self.assertFalse(cajera.context['ve_esperado'])
        self.assertNotContains(cajera, '7.000,00')      # lo esperado no se le muestra
        self.assertContains(cajera, 'Arqueo ciego')
        self.entrar(self.lucre)
        supervisora = self.client.get(respuesta['Location'])
        self.assertTrue(supervisora.context['ve_esperado'])
        self.assertEqual(supervisora.context['esperado'], D('7000'))
        self.assertEqual(supervisora.context['diferencia'], D('-100'))
        self.assertContains(supervisora, 'FALTANTE')
        self.assertContains(supervisora, asiento.fecha_hora.astimezone().strftime('%d/%m/%Y'))


# ===========================================================================
#  5. Comprobantes imprimibles
# ===========================================================================
class ComprobanteZTest(Base):

    def test_el_desglose_del_efectivo_suma_lo_esperado(self):
        turno = self.abrir(self.ana, fondo='5000')
        ventas.registrar(turno, self.ana, [ventas.Item(self.alfajor, D('3'))], [ventas.Pago(f.efectivo())])
        ventas.registrar(turno, self.ana, [ventas.Item(self.alfajor, D('2'))],
                         [ventas.Pago(f.efectivo(), D('500')), ventas.Pago(f.medio('QR'), D('1500'))])
        caja.refuerzo(turno, self.lucre, D('800'), 'Monedas')
        caja.retiro(turno, self.lucre, D('2000'), 'A la caja fuerte')
        foto = caja.foto_del_cajon(turno)
        filas = caja.desglose_del_cajon(foto)
        con_signo = sum((r['monto'] if r['signo'] == '+' else -r['monto'] for r in filas), D('0'))
        self.assertEqual(con_signo, foto['esperado'])
        self.assertEqual(foto['esperado'], D('5000') + D('3500') + D('800') - D('2000'))
        etiquetas = {r['etiqueta']: r for r in filas}
        self.assertEqual(etiquetas['Ventas en efectivo']['cantidad'], 2)
        self.assertEqual(etiquetas['Retiros a tesorería']['monto'], D('2000'))

    def test_el_z_tiene_encabezado_responsables_desglose_otros_medios_y_firmas(self):
        turno = self.abrir(self.ana, fondo='5000')
        ventas.registrar(turno, self.ana, [ventas.Item(self.alfajor, D('1'))], [ventas.Pago(f.efectivo())])
        ventas.registrar(turno, self.ana, [ventas.Item(self.alfajor, D('2'))], [ventas.Pago(f.medio('QR'))])
        self.entrar(self.ana)
        self.declarar('6000')
        respuesta = self.confirmar()
        html = self.client.get(respuesta['Location']).content.decode()
        for texto in ('CIERRE DE TURNO', 'Comercio', 'Caja Cafetería', 'N° de turno',
                      'Abrió el turno:', 'Ana Paz @', 'Cerró / arqueó la caja:',
                      'Autorizó / auditó la diferencia:', 'No hubo diferencia',
                      '(+) Fondo inicial', '(+) Ventas en efectivo (1)',
                      '(+) Cobros de cuentas corrientes (0)', '(+) Ingresos manuales (refuerzos) (0)',
                      '(−) Gastos pagados por caja (0)', '(−) Retiros a tesorería (0)',
                      '(=) Efectivo esperado', 'Efectivo contado declarado', 'Diferencia: EXACTA',
                      'Otros medios (informativo)', 'QR (1)', 'Ventas anuladas (0)',
                      'Firma cajero saliente', 'Firma cajero entrante / supervisor'):
            with self.subTest(texto=texto):
                self.assertIn(texto, html)
        self.assertNotIn('Cerrado por relevo', html)


class TicketDeVentaTest(Base):

    def setUp(self):
        super().setUp()
        from fidelizacion.tests.test_credito import cliente
        self.admin = f.usuario('40200009', 'Administrador', nombre='Dora', apellido='Vera')
        self.elsa = cliente()
        credito.cambiar_estado(self.elsa, 'HABILITADO', self.admin, limite=D('100000'))
        self.turno = self.abrir(self.ana)
        self.cc = MedioPago.objects.get(nombre_medio_pago=MedioPago.NOMBRE_CUENTA_CORRIENTE)

    def fiar(self, cantidad):
        return ventas.registrar(self.turno, self.ana, [ventas.Item(self.alfajor, D(cantidad))],
                                [ventas.Pago(self.cc)], cliente=self.elsa,
                                vence_fiado=f.en_dias(15))[0]

    def test_el_ticket_fiado_tiene_saldo_anterior_cargo_nuevo_saldo_vencimiento_y_firma(self):
        self.fiar('2')
        venta = self.fiar('3')
        self.entrar(self.ana)
        respuesta = self.client.get(reverse('ventas:ticket', args=[venta.pk]))
        cuenta = respuesta.context['cuenta']
        self.assertEqual(cuenta['anterior'], D('2000'))
        self.assertEqual(cuenta['cargo'], D('3000'))
        self.assertEqual(cuenta['nuevo'], D('5000'))
        html = respuesta.content.decode()
        for texto in ('TICKET DE VENTA', 'Venta N°', 'Caja Cafetería', 'Ana Paz', 'Elsa Gómez',
                      'DNI 20111222', 'Cuenta corriente', 'Saldo anterior', '(+) Cargo de esta compra',
                      '(=) Nuevo saldo total', 'Vence el', f.en_dias(15).strftime('%d/%m/%Y'),
                      'Firma de conformidad del cliente'):
            with self.subTest(texto=texto):
                self.assertIn(texto, html)
        # Reimpreso más tarde, con la deuda ya cobrada, dice lo mismo.
        credito.cobrar(self.elsa, D('5000'), f.medio('Transferencia'), self.admin)
        self.assertEqual(self.client.get(reverse('ventas:ticket', args=[venta.pk])).context['cuenta'], cuenta)

    def test_el_ticket_de_pago_mixto_discrimina_cada_medio(self):
        venta = ventas.registrar(self.turno, self.ana, [ventas.Item(self.alfajor, D('2'))],
                                 [ventas.Pago(f.efectivo(), D('500')), ventas.Pago(f.medio('QR'), D('1500'))])[0]
        self.entrar(self.ana)
        html = self.client.get(reverse('ventas:ticket', args=[venta.pk])).content.decode()
        self.assertIn('Pago mixto', html)
        self.assertIn('Efectivo', html)
        self.assertIn('QR', html)
        self.assertNotIn('Cuenta corriente', html)

    def test_el_recibo_de_cobro_dice_lo_que_se_pago_y_a_que_se_imputo(self):
        primera = self.fiar('2')
        segunda = self.fiar('3')
        resultado = credito.cobrar(self.elsa, D('2500'), f.medio('Transferencia'), self.admin,
                                   interes_porcentaje=D('10'))
        self.entrar(self.admin)
        respuesta = self.client.get(reverse('fidelizacion:recibo', args=[resultado['recibo']]))
        r = respuesta.context['r']
        self.assertEqual(r['anterior'], D('5000'))
        self.assertEqual(r['pagado'], D('2500'))
        self.assertEqual(r['recargo'], D('250'))
        self.assertEqual(r['total'], D('2750'))
        self.assertEqual(r['nuevo'], D('2500'))
        self.assertEqual([m.id_fiado.id_venta_id for m in r['renglones']], [primera.pk, segunda.pk])
        html = respuesta.content.decode()
        for texto in ('RECIBO DE PAGO', 'N° de recibo', 'Dora Vera @', 'Elsa Gómez', 'DNI 20111222',
                      'Saldo anterior', '(−) Monto pagado', '(+) Interés por mora', 'Transferencia',
                      'Nuevo saldo pendiente', 'Comprobantes imputados', f'Venta N° {primera.pk}',
                      f'Venta N° {segunda.pk}'):
            with self.subTest(texto=texto):
                self.assertIn(texto, html)
        # El segundo COBRO del mismo pago lleva al mismo recibo.
        segundo = MovimientoCuenta.objects.filter(tipo_movimiento='COBRO').order_by('pk').last()
        self.assertRedirects(self.client.get(reverse('fidelizacion:recibo', args=[segundo.pk])),
                             reverse('fidelizacion:recibo', args=[resultado['recibo']]))
        # Y la cuenta lo ofrece para imprimir.
        cuenta = self.client.get(reverse('fidelizacion:cuenta', args=[self.elsa.pk]))
        self.assertContains(cuenta, f'Recibo N° {resultado["recibo"]}')


class ValeYCambioTest(Base):

    def test_el_vale_de_retiro_tiene_fecha_hora_responsable_motivo_y_firmas(self):
        turno = self.abrir(self.lucre)
        self.entrar(self.lucre)
        token = self.client.get(reverse('ventas:caja')).context['form_movimiento'].initial['token_operacion']
        respuesta = self.client.post(reverse('ventas:mover_efectivo'), {
            'token_operacion': token, 'tipo': 'RETIRO', 'monto': '1500', 'concepto': 'Depósito en el banco'})
        movimiento = MovimientoCaja.objects.get(id_turno_caja=turno, tipo_movimiento='RETIRO')
        self.assertIn(f'vale={movimiento.pk}', respuesta['Location'])
        self.assertContains(self.client.get(respuesta['Location']), 'Imprimir el vale')
        html = self.client.get(reverse('ventas:vale_movimiento', args=[movimiento.pk])).content.decode()
        for texto in ('VALE DE RETIRO DE EFECTIVO', 'Fecha', 'Hora', 'Responsable:', 'Lucrecia Sosa @',
                      'Autorizó:', 'Depósito en el banco', '1.500,00', 'Firma del responsable',
                      'Firma de quien recibe el efectivo'):
            with self.subTest(texto=texto):
                self.assertIn(texto, html)
        # Otra cajera no ve el vale ajeno (sumando vales se reconstruye el saldo).
        self.entrar(self.ana)
        self.assertRedirects(self.client.get(reverse('ventas:vale_movimiento', args=[movimiento.pk])),
                             reverse('ventas:caja'))

    def test_el_comprobante_de_devolucion(self):
        from ventas import cambios
        turno = self.abrir(self.ana)
        venta = ventas.registrar(turno, self.ana, [ventas.Item(self.alfajor, D('2'))], [ventas.Pago(f.efectivo())])[0]
        renglon = venta.detalles.get()
        cambio = cambios.registrar(
            turno, self.ana, venta, 'CLIENTE_ARREPENTIDO',
            [cambios.Devuelto(renglon, D('1'), True)], medio_pago=f.efectivo(), autorizante=self.lucre)
        self.entrar(self.lucre)
        html = self.client.get(reverse('ventas:comprobante_cambio', args=[cambio.pk])).content.decode()
        for texto in ('COMPROBANTE DE DEVOLUCIÓN', f'venta N° {venta.pk}', 'Fecha', 'Hora',
                      'El cliente se arrepintió', 'Devuelve', 'Se le devuelve', 'Responsable:',
                      'Autorizó:', 'Firma del cliente (recibí conforme)'):
            with self.subTest(texto=texto):
                self.assertIn(texto, html)

    def test_el_ticket_tiene_papel_y_boton_volver_segun_permisos(self):
        turno = self.abrir(self.ana)
        venta = ventas.registrar(turno, self.ana, [ventas.Item(self.alfajor, D('1'))], [ventas.Pago(f.efectivo())])[0]

        # Operador (vender): ve el papel y el botón Volver al mostrador (sin link a Ventas si no tiene ver_ventas).
        self.entrar(self.ana)
        html_op = self.client.get(reverse('ventas:ticket', args=[venta.pk])).content.decode()
        self.assertIn('<article class="comprobante-papel">', html_op)
        self.assertIn(f'href="{reverse("ventas:mostrador")}"', html_op)
        self.assertIn('Volver al mostrador', html_op)

        # Encargado (vender + ver_ventas): ve ambos enlaces de vuelta y la ficha también ofrece el ticket.
        self.entrar(self.lucre)
        html_enc = self.client.get(reverse('ventas:ticket', args=[venta.pk])).content.decode()
        self.assertIn('<article class="comprobante-papel">', html_enc)
        self.assertIn('Volver al mostrador', html_enc)
        self.assertIn(f'href="{reverse("ventas:ventas")}"', html_enc)
        self.assertIn('Ver ventas', html_enc)

        ficha = self.client.get(reverse('ventas:ficha_venta', args=[venta.pk])).content.decode()
        self.assertIn(reverse('ventas:ticket', args=[venta.pk]), ficha)
        self.assertIn('Ver / imprimir ticket', ficha)
