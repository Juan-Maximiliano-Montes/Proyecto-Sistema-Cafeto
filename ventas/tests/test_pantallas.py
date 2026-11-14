"""Las pantallas de Ventas, de punta a punta: caja, mostrador y cambios.

Las reglas ya las prueban test_ventas (servicios); acá se prueba que la
pantalla las respete: permisos, doble envío, arqueo ciego, errores en rojo
y que lo que se cobra sea lo que calculó el servidor.
"""
import json
import uuid
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from nucleo.tests import fabrica as f
from ventas import caja
from ventas.models import MovimientoCaja, TurnoCaja, Venta

D = Decimal


class Base(TestCase):

    def setUp(self):
        self.operador = f.usuario('40000001', 'Operador', nombre='Cami')
        self.encargada = f.usuario('40000002', 'Encargado', nombre='Lucrecia')
        self.cajon = f.caja_cafeteria()
        self.prov = f.proveedor()
        self.alfajor = f.producto('Alfajor', prov=self.prov, precio='1200')
        f.comprar(self.encargada, self.prov, (self.alfajor, 10, '700'))

    def entrar(self, usuario, con_caja=True):
        sesion = f.entrar(self.client, usuario)
        if con_caja:
            self.client.post(reverse('ventas:elegir_caja'), {'caja': self.cajon.pk})
        return sesion

    def token(self):
        """Pide la pantalla para que el servidor emita un token válido."""
        respuesta = self.client.get(reverse('ventas:caja'))
        return respuesta.context['form_apertura' if 'form_apertura' in respuesta.context
                                 else 'form_movimiento'].initial['token_operacion']


class PuestoDeCajaTest(Base):

    def test_sin_caja_elegida_ofrece_las_cajas(self):
        self.entrar(self.operador, con_caja=False)
        respuesta = self.client.get(reverse('ventas:caja'))
        self.assertContains(respuesta, '¿En qué caja estás?')
        self.assertContains(respuesta, 'Caja Cafetería')

    def test_la_tablet_se_acuerda_de_su_caja(self):
        self.entrar(self.operador)
        self.assertIn('cafeto_puesto', self.client.cookies)
        # Al día siguiente, otra sesión en el mismo dispositivo.
        f.entrar(self.client, self.operador)
        respuesta = self.client.get(reverse('ventas:caja'))
        self.assertEqual(respuesta.context['caja'], self.cajon)

    def test_abrir_una_sola_vez_aunque_se_envie_dos(self):
        self.entrar(self.operador)
        token = self.token()
        datos = {'token_operacion': token, 'monto_inicial': '10.000'}
        self.client.post(reverse('ventas:abrir_turno'), datos)
        self.client.post(reverse('ventas:abrir_turno'), datos)
        turno = TurnoCaja.objects.get(id_caja_registradora=self.cajon)
        self.assertEqual(turno.monto_inicial, D('10000'))
        self.assertEqual(MovimientoCaja.objects.filter(id_turno_caja=turno).count(), 1)

    def test_el_puesto_no_muestra_lo_esperado_a_nadie(self):
        """Arqueo ciego: la pantalla del puesto la usa quien va a contar, y
        puede ser la encargada. Ni el saldo ni los importes que lo arman."""
        turno = f.abrir_turno(self.operador, self.cajon, fondo='5000')
        caja.refuerzo(turno, self.encargada, D('1234'), 'Cambio')
        for persona in (self.operador, self.encargada):
            with self.subTest(perfil=str(persona.id_perfil)):
                self.client.logout()
                self.entrar(persona)
                respuesta = self.client.get(reverse('ventas:caja'))
                html = respuesta.content.decode()
                self.assertNotIn('saldo', respuesta.context)
                self.assertNotIn('Efectivo en el cajón', html)
                self.assertNotIn('1.234', html)
                self.assertNotIn('6.234', html)

    def test_la_encargada_ve_lo_esperado_de_otro_cajon_no_del_suyo(self):
        ajeno = f.abrir_turno(self.operador, self.cajon, fondo='5000')
        self.entrar(self.encargada)
        html = self.client.get(reverse('ventas:ficha_turno', args=[ajeno.pk])).content.decode()
        self.assertIn('5.000', html)
        ajeno.refresh_from_db()
        caja.cerrar(ajeno, self.operador, D('5000'))
        propio = f.abrir_turno(self.encargada, self.cajon, fondo='7000')
        caja.refuerzo(propio, self.encargada, D('1234'), 'Cambio')
        html = self.client.get(reverse('ventas:ficha_turno', args=[propio.pk])).content.decode()
        self.assertIn('arqueo ciego', html)
        self.assertNotIn('1.234', html)

    def test_el_operador_no_puede_retirar_efectivo(self):
        f.abrir_turno(self.operador, self.cajon, fondo='5000')
        self.entrar(self.operador)
        self.client.post(reverse('ventas:mover_efectivo'),
                         {'tipo': 'RETIRO', 'monto': '1000', 'concepto': 'x',
                          'token_operacion': 'cualquiera'})
        self.assertFalse(MovimientoCaja.objects.filter(tipo_movimiento='RETIRO').exists())


class ArqueoCiegoTest(Base):

    def setUp(self):
        super().setUp()
        self.turno = f.abrir_turno(self.operador, self.cajon, fondo='5000')
        self.entrar(self.operador)

    def declarar(self, monto, relevo=False):
        token = self.client.get(reverse('ventas:caja')).context['form_arqueo'].initial['token_operacion']
        datos = {'token_operacion': token, 'monto_contado': monto}
        if relevo:
            datos['relevo'] = 'on'
        return self.client.post(reverse('ventas:declarar_arqueo'), datos)

    def confirmar(self, **extra):
        token = self.client.get(reverse('ventas:cierre')).context['form'].initial['token_operacion']
        return self.client.post(reverse('ventas:cierre'), {'token_operacion': token, **extra})

    def test_justo_cierra_sin_preguntar(self):
        self.declarar('5000')
        self.confirmar()
        self.turno.refresh_from_db()
        self.assertEqual(self.turno.estado_turno, 'CERRADA')
        self.assertEqual(self.turno.diferencia_caja, 0)

    def test_al_cerrar_sale_el_documento_de_recaudacion(self):
        from ventas import servicios as ventas
        ventas.registrar(self.turno, self.operador,
                         [ventas.Item(self.alfajor, D('1'))],
                         [ventas.Pago(f.efectivo())])
        ventas.registrar(self.turno, self.operador,
                         [ventas.Item(self.alfajor, D('2'))],
                         [ventas.Pago(f.medio('QR'))])
        self.declarar('6200')
        respuesta = self.confirmar()
        self.assertRedirects(respuesta, reverse('ventas:recaudacion',
                                                args=[self.turno.pk]))
        documento = self.client.get(respuesta['Location'])
        self.assertContains(documento, 'Documento de recaudación')
        filas = {r['medio']: r for r in documento.context['filas']}
        self.assertEqual(filas['Efectivo']['total'], D('1200'))
        self.assertEqual(filas['QR']['total'], D('2400'))
        self.assertEqual(documento.context['total'], D('3600'))

    def test_el_documento_de_otro_turno_no_se_ve(self):
        self.declarar('5000')
        self.confirmar()
        otro = f.usuario('40000003', 'Operador', nombre='Dani')
        self.client.logout()
        self.entrar(otro)
        respuesta = self.client.get(reverse('ventas:recaudacion',
                                            args=[self.turno.pk]))
        self.assertRedirects(respuesta, reverse('ventas:caja'),
                             fetch_redirect_response=False)

    def test_lo_declarado_no_se_puede_corregir(self):
        self.declarar('4000')
        # Vuelve a la pantalla e intenta declarar otro número: no cambia.
        self.declarar('5000')
        respuesta = self.client.get(reverse('ventas:cierre'))
        self.assertEqual(respuesta.context['contado'], D('4000'))
        self.assertEqual(respuesta.context['diferencia'], D('-1000'))

    def cerrar_igual(self):
        return self.client.post(reverse('ventas:cierre'), {'cerrar_igual': '1'})

    def test_si_no_coincide_no_dice_por_cuanto(self):
        self.declarar('3000')
        pantalla = self.client.get(reverse('ventas:cierre'))
        self.assertEqual(pantalla.context['etapa'], 'NO_COINCIDE')
        self.assertContains(pantalla, 'no coincide con el libro')
        self.assertNotContains(pantalla, '5.000')
        self.assertNotContains(pantalla, '2.000')

    def test_puede_volver_a_contar_una_vez_sin_ver_la_diferencia(self):
        self.declarar('3000')
        self.client.post(reverse('ventas:descartar_arqueo'))
        self.declarar('5000')
        pantalla = self.client.get(reverse('ventas:cierre'))
        self.assertEqual(pantalla.context['etapa'], 'COINCIDE')
        self.confirmar()
        self.turno.refresh_from_db()
        self.assertEqual(self.turno.diferencia_caja, 0)

    def test_el_segundo_conteo_que_no_coincide_queda_fijo(self):
        self.declarar('3000')
        self.client.post(reverse('ventas:descartar_arqueo'))
        self.declarar('3500')
        pantalla = self.client.get(reverse('ventas:cierre'))
        self.assertEqual(pantalla.context['etapa'], 'REVELADA')
        self.assertNotContains(pantalla, 'Volver sin cerrar')
        # Ya vio la diferencia: ni volviendo atrás ni saliendo del sistema
        # puede declarar lo que el libro esperaba.
        self.client.post(reverse('ventas:descartar_arqueo'))
        self.client.logout()
        self.entrar(self.operador)
        self.declarar('5000')
        pantalla = self.client.get(reverse('ventas:cierre'))
        self.assertEqual(pantalla.context['contado'], D('3500'))
        self.assertEqual(pantalla.context['etapa'], 'REVELADA')

    def test_una_diferencia_chica_no_pide_explicacion(self):
        from negocio.models import ParametrosNegocio
        tolerancia = ParametrosNegocio.vigentes().tolerancia_arqueo
        if tolerancia <= 0:
            self.skipTest('Sin tolerancia configurada')
        self.declarar(str(D('5000') - tolerancia))
        self.assertEqual(self.client.get(reverse('ventas:cierre')).context['etapa'], 'COINCIDE')
        self.confirmar()
        self.turno.refresh_from_db()
        self.assertEqual(self.turno.estado_turno, 'CERRADA')
        self.assertEqual(self.turno.diferencia_caja, -tolerancia)

    def test_una_diferencia_grande_pide_supervisor(self):
        self.declarar('3000')
        self.cerrar_igual()
        respuesta = self.confirmar(observacion='Faltante')
        self.assertContains(respuesta, 'autorice')
        self.turno.refresh_from_db()
        self.assertEqual(self.turno.estado_turno, 'ABIERTA')
        self.confirmar(observacion='Faltante', autoriza_usuario='40000002',
                       autoriza_clave=f.CLAVE)
        self.turno.refresh_from_db()
        self.assertEqual(self.turno.tipo_cierre, 'SUPERVISADO')
        self.assertEqual(self.turno.id_usuario_supervisor, self.encargada)

    def test_el_relevo_abre_a_nombre_de_quien_sigue(self):
        self.declarar('5000', relevo=True)
        self.confirmar(entrante_usuario='40000002', entrante_clave=f.CLAVE)
        nuevo = TurnoCaja.objects.get(estado_turno='ABIERTA')
        self.assertEqual(nuevo.id_usuario_apertura, self.encargada)
        self.assertEqual(nuevo.monto_inicial, D('5000'))


class MostradorTest(Base):

    def setUp(self):
        super().setUp()
        self.turno = f.abrir_turno(self.operador, self.cajon, fondo='5000')
        self.entrar(self.operador)

    def post(self, nombre, cuerpo):
        respuesta = self.client.post(reverse(f'ventas:{nombre}'), json.dumps(cuerpo),
                                     content_type='application/json')
        return respuesta.json()

    def test_el_catalogo_busca_sin_acentos(self):
        f.producto('Té verde', prov=self.prov)
        datos = self.client.get(reverse('ventas:catalogo'), {'q': 'te verde'}).json()
        self.assertEqual([p['nombre'] for p in datos['productos']], ['Té verde'])

    def test_el_precio_lo_pone_el_servidor(self):
        datos = self.post('cotizar', {'items': [
            {'producto': self.alfajor.pk, 'cantidad': '2', 'precio': '1'}]})
        self.assertTrue(datos['ok'])
        self.assertEqual(D(datos['total']), D('2400'))

    def test_sin_stock_vuelve_en_rojo(self):
        datos = self.post('cotizar', {'items': [
            {'producto': self.alfajor.pk, 'cantidad': '50'}]})
        self.assertFalse(datos['ok'])
        self.assertIn('stock', datos['error'].lower())

    def test_cobrar_dos_veces_el_mismo_ticket_es_una_venta(self):
        cuerpo = {'token': uuid.uuid4().hex,
                  'items': [{'producto': self.alfajor.pk, 'cantidad': '1'}],
                  'pagos': [{'medio': f.efectivo().pk, 'recibido': '2.000'}]}
        uno = self.post('cobrar', cuerpo)
        dos = self.post('cobrar', cuerpo)
        self.assertTrue(uno['ok'])
        self.assertEqual(uno['venta'], dos['venta'])
        self.assertTrue(dos['repetida'])
        self.assertEqual(D(uno['vuelto']), D('800'))
        self.assertEqual(Venta.objects.count(), 1)
        # La venta queda en la auditoría una sola vez, con su sesión.
        from seguridad.models import LogAuditoria
        asientos = LogAuditoria.objects.filter(
            operacion_realizada='ALTA DE VENTA', id_registro=uno['venta'])
        self.assertEqual(asientos.count(), 1)
        self.assertIsNotNone(asientos.get().id_sesion_id)

    def test_sin_turno_no_se_vende(self):
        caja.cerrar(self.turno, self.operador, caja.saldo(self.turno))
        datos = self.post('cobrar', {'token': uuid.uuid4().hex,
                                     'items': [{'producto': self.alfajor.pk, 'cantidad': '1'}],
                                     'pagos': [{'medio': f.efectivo().pk}]})
        self.assertFalse(datos['ok'])
        self.assertIn('turno', datos['error'])

    def test_el_descuento_sobre_el_tope_pide_autorizacion(self):
        item = {'producto': self.alfajor.pk, 'cantidad': '1', 'descuento': '50',
                'motivo': 'CORTESIA'}
        datos = self.post('cotizar', {'items': [item]})
        self.assertFalse(datos['ok'])
        self.assertIn('tope', datos['error'])
        datos = self.post('cotizar', {'items': [item], 'autoriza': {
            'usuario': '40000002', 'clave': 'mal'}})
        self.assertIn('no son correctos', datos['error'])

    def test_el_ticket_imprimible(self):
        venta = self.post('cobrar', {'token': uuid.uuid4().hex,
                                     'items': [{'producto': self.alfajor.pk, 'cantidad': '1'}],
                                     'pagos': [{'medio': f.efectivo().pk}]})
        respuesta = self.client.get(venta['ticket'])
        self.assertContains(respuesta, 'Alfajor')
        self.assertContains(respuesta, '$ 1.200,00')


class CambiosTest(Base):

    def setUp(self):
        super().setUp()
        from ventas import servicios
        self.turno = f.abrir_turno(self.operador, self.cajon, fondo='5000')
        self.venta, _ = servicios.registrar(
            self.turno, self.operador, [servicios.Item(self.alfajor, D('2'))],
            [servicios.Pago(f.efectivo())])
        self.renglon = self.venta.detalles.get()
        self.entrar(self.operador)

    def test_calcular_no_escribe_nada(self):
        respuesta = self.client.post(reverse('ventas:cambios'), {
            'venta': self.venta.pk, 'accion': 'calcular', 'motivo': 'PRODUCTO_DANADO',
            f'devuelve_{self.renglon.pk}': '1', f'reingresa_{self.renglon.pk}': '0'})
        self.assertEqual(respuesta.context['a_devolver'], D('1200'))
        self.assertFalse(self.venta.cambios.exists())

    def test_devolver_plata_lo_autoriza_otra_persona(self):
        datos = {'venta': self.venta.pk, 'accion': 'confirmar', 'motivo': 'PRODUCTO_DANADO',
                 'token': uuid.uuid4().hex, 'medio': f.efectivo().pk,
                 f'devuelve_{self.renglon.pk}': '1', f'reingresa_{self.renglon.pk}': '0'}
        respuesta = self.client.post(reverse('ventas:cambios'), datos)
        self.assertIn('autorice', respuesta.context['error'])
        datos.update(autoriza_usuario='40000002', autoriza_clave=f.CLAVE)
        self.client.post(reverse('ventas:cambios'), datos)
        cambio = self.venta.cambios.get()
        self.assertEqual(cambio.importe_devuelto, D('1200'))
        self.assertEqual(cambio.id_usuario_autorizante, self.encargada)

    def test_no_se_devuelve_mas_de_lo_comprado(self):
        respuesta = self.client.post(reverse('ventas:cambios'), {
            'venta': self.venta.pk, 'accion': 'calcular', 'motivo': 'OTRO',
            f'devuelve_{self.renglon.pk}': '3'})
        self.assertFalse(self.venta.cambios.exists())
        self.assertIn('como máximo', respuesta.context['error'])


class PantallasDeConsultaTest(Base):
    """Las pantallas cargan y respetan el permiso."""

    def setUp(self):
        super().setUp()
        from ventas import servicios
        turno = f.abrir_turno(self.operador, self.cajon, fondo='5000')
        self.venta, _ = servicios.registrar(
            turno, self.operador, [servicios.Item(self.alfajor, D('1'))],
            [servicios.Pago(f.efectivo())])

    def test_la_encargada_ve_ventas_y_turnos(self):
        self.entrar(self.encargada)
        for nombre in ('ventas', 'turnos', 'mostrador', 'cambios'):
            with self.subTest(pantalla=nombre):
                self.assertEqual(self.client.get(reverse(f'ventas:{nombre}')).status_code, 200)
        ficha = self.client.get(reverse('ventas:ficha_venta', args=[self.venta.pk]))
        self.assertContains(ficha, 'Alfajor')
        for formato in ('xlsx', 'pdf'):
            with self.subTest(formato=formato):
                archivo = self.client.get(reverse('ventas:exportar_ventas'), {'formato': formato})
                self.assertEqual(archivo.status_code, 200)
                self.assertIn('attachment', archivo['Content-Disposition'])

    def test_el_operador_no_consulta_ventas_ajenas(self):
        self.entrar(self.operador)
        self.assertRedirects(self.client.get(reverse('ventas:ventas')), reverse('home'),
                             fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse('ventas:turnos')), reverse('ventas:caja'),
                             fetch_redirect_response=False)


class TitularCierraSolaTest(Base):
    """DD-80: a la dueña no la supervisa nadie; confirma con su clave."""

    def setUp(self):
        super().setUp()
        self.duenia = f.titular()
        self.turno = f.abrir_turno(self.duenia, self.cajon, fondo='5000')
        self.entrar(self.duenia)

    declarar = ArqueoCiegoTest.declarar
    confirmar = ArqueoCiegoTest.confirmar

    def test_con_su_clave_cierra_aunque_falte_plata(self):
        self.declarar('3000')
        pantalla = self.client.get(reverse('ventas:cierre'))
        self.assertTrue(pantalla.context['titular_sola'])
        self.assertContains(pantalla, 'alcanza con tu clave')
        self.confirmar(observacion='Saqué para el flete', autoriza_clave=f.CLAVE)
        self.turno.refresh_from_db()
        self.assertEqual(self.turno.estado_turno, 'CERRADA')
        self.assertEqual(self.turno.tipo_cierre, 'NORMAL')
        self.assertIsNone(self.turno.id_usuario_supervisor)
        self.assertEqual(self.turno.diferencia_caja, D('-2000'))

    def test_sin_la_clave_no_cierra(self):
        self.declarar('3000')
        self.confirmar(observacion='Saqué para el flete')
        self.confirmar(observacion='Saqué para el flete', autoriza_clave='otra-cosa')
        self.turno.refresh_from_db()
        self.assertEqual(self.turno.estado_turno, 'ABIERTA')

    def test_otro_administrador_sigue_necesitando_a_alguien(self):
        self.declarar('3000')
        self.confirmar(observacion='x', autoriza_clave=f.CLAVE)   # cierra la dueña
        admin = f.usuario('40000009', 'Administrador', nombre='Ana')
        turno = f.abrir_turno(admin, self.cajon, fondo='5000')
        self.client.logout()
        self.entrar(admin)
        self.declarar('3000')
        self.client.post(reverse('ventas:cierre'), {'cerrar_igual': '1'})
        respuesta = self.confirmar(observacion='Faltante',
                                   autoriza_usuario='40000009', autoriza_clave=f.CLAVE)
        self.assertContains(respuesta, 'otra persona')
        turno.refresh_from_db()
        self.assertEqual(turno.estado_turno, 'ABIERTA')


class ConteosEnElDocumentoTest(Base):
    """DD-80: cada conteo declarado sale en el documento de recaudación."""

    def setUp(self):
        super().setUp()
        self.turno = f.abrir_turno(self.operador, self.cajon, fondo='5000')
        self.entrar(self.operador)

    declarar = ArqueoCiegoTest.declarar
    confirmar = ArqueoCiegoTest.confirmar

    def documento(self):
        return self.client.get(reverse('ventas:recaudacion', args=[self.turno.pk]))

    def test_un_solo_conteo_no_agrega_nada(self):
        self.declarar('5000')
        self.confirmar()
        documento = self.documento()
        self.assertEqual([c['destino'] for c in documento.context['conteos']], ['CIERRE'])
        self.assertNotContains(documento, 'SE CONTÓ')

    def test_volver_atras_y_recontar_queda_en_el_papel(self):
        self.declarar('4000')
        self.client.post(reverse('ventas:descartar_arqueo'))
        self.declarar('5000')
        self.confirmar()
        documento = self.documento()
        conteos = documento.context['conteos']
        self.assertEqual([(c['monto'], c['destino']) for c in conteos],
                         [(D('4000'), 'DESCARTADO'), (D('5000'), 'CIERRE')])
        self.assertContains(documento, 'SE CONTÓ 2 VECES')
        self.assertContains(documento, 'volvió atrás sin cerrar')

    def test_salir_y_volver_a_entrar_tambien(self):
        self.declarar('4000')
        self.client.logout()
        self.entrar(self.operador)
        self.declarar('5000')
        self.confirmar()
        conteos = self.documento().context['conteos']
        self.assertEqual([c['destino'] for c in conteos], ['ABANDONADO', 'CIERRE'])
        self.assertTrue(conteos[1]['otra_sesion'])
        self.assertContains(self.documento(), 'cerró la sesión y volvió a entrar')


class CajasEnLaBarraTest(Base):
    """Quien supervisa ve todas las cajas en la barra; la cajera, la suya."""

    def test_la_encargada_ve_las_dos_cajas(self):
        f.abrir_turno(self.operador, self.cajon, fondo='5000')
        f.abrir_turno(self.encargada, f.caja_minisuper(), fondo='5000')
        self.entrar(self.encargada)
        inicio = self.client.get(reverse('home'), follow=True)
        self.assertEqual(inicio.context['cajas_abiertas'], 2)
        self.assertContains(inicio, '2 cajas abiertas')
        self.assertContains(inicio, 'este equipo')

    def test_la_cajera_solo_ve_la_de_su_equipo(self):
        f.abrir_turno(self.operador, self.cajon, fondo='5000')
        self.entrar(self.operador)
        pantalla = self.client.get(reverse('ventas:caja'))
        self.assertNotIn('cajas_del_local', pantalla.context)
        self.assertNotContains(pantalla, 'cajas abiertas')


class AvisoDeReconteoTest(Base):

    def test_la_duenia_se_entera_en_el_inicio(self):
        from negocio import panel
        turno = f.abrir_turno(self.operador, self.cajon, fondo='5000')
        self.entrar(self.operador)
        declarar = lambda monto: ArqueoCiegoTest.declarar(self, monto)
        declarar('4000')
        self.client.post(reverse('ventas:descartar_arqueo'))
        declarar('5000')
        ArqueoCiegoTest.confirmar(self)
        turno.refresh_from_db()
        self.assertEqual(turno.estado_turno, 'CERRADA')
        _numeros, avisos = panel.del_dia(f.titular())
        self.assertIn('RECONTEOS', [a['clave'] for a in avisos])
        _numeros, avisos = panel.del_dia(self.operador)
        self.assertNotIn('RECONTEOS', [a['clave'] for a in avisos])
