"""El fiado (cuenta corriente), DD-91.

Servicio primero (reglas, saldos, caja, stock y puntos) y después lo que
ve quien está en la caja y quien gestiona las cuentas.
"""
import json
import uuid
from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from fidelizacion import credito
from fidelizacion.forms import ClienteForm
from fidelizacion.models import Cliente, MovimientoCuenta
from negocio import mensual
from nucleo.tests import fabrica as f
from ventas import anulacion, cambios, servicios as ventas
from ventas.models import MedioPago, MovimientoCaja

TOKEN = 'credencial-de-prueba-larga-0123456789abcdef'


def cliente(apellido='Gómez', nombre='Elsa', telefono='3874555111', dni='20111222'):
    formulario = ClienteForm({'apellido_cliente': apellido, 'nombre_cliente': nombre,
                              'telefono_cliente': telefono, 'dni_cliente': dni})
    assert formulario.is_valid(), formulario.errors
    return formulario.save()


class Base(TestCase):

    def setUp(self):
        self.cajera = f.usuario('40100001', 'Operador', nombre='Cami')
        self.admin = f.usuario('40100002', 'Administrador', nombre='Ana')
        self.prov = f.proveedor()
        self.alfajor = f.producto('Alfajor', prov=self.prov, precio='1000', costo='600')
        f.comprar(self.admin, self.prov, (self.alfajor, 50, '600'))
        self.turno = f.abrir_turno(self.cajera)
        self.cc = MedioPago.objects.get(nombre_medio_pago=MedioPago.NOMBRE_CUENTA_CORRIENTE)
        self.elsa = cliente()

    def habilitar(self, quien=None, **extra):
        return credito.cambiar_estado(quien or self.elsa, 'HABILITADO', self.admin, **extra)

    def fiar(self, cantidad='2', usuario=None, autorizante=None, quien='elsa', vence=None, **extra):
        return ventas.registrar(
            self.turno, usuario or self.cajera, [ventas.Item(self.alfajor, D(cantidad))],
            [ventas.Pago(self.cc)], autorizante=autorizante,
            cliente=self.elsa if quien == 'elsa' else quien, vence_fiado=vence, **extra)[0]

    def caja(self):
        return sum((m.monto for m in MovimientoCaja.objects.filter(id_turno_caja=self.turno)), D('0'))


# ===========================================================================
#  Venta fiada
# ===========================================================================
class FiarTest(Base):

    def test_no_se_le_fia_a_un_anonimo(self):
        with self.assertRaisesMessage(ValidationError, 'hay que saber a quién'):
            self.fiar(quien=None)

    def test_sin_cuenta_corriente_hace_falta_quien_gestione(self):
        with self.assertRaisesMessage(ValidationError, 'todavía no tiene cuenta corriente'):
            self.fiar()
        self.assertFalse(MovimientoCuenta.objects.exists())

    def test_con_la_autorizacion_se_habilita_y_se_fia_en_un_paso(self):
        self.alfajor.refresh_from_db()
        caja_antes, stock_antes = self.caja(), self.alfajor.stock_actual
        venta = self.fiar(autorizante=self.admin)
        self.elsa.refresh_from_db()
        self.alfajor.refresh_from_db()
        self.assertEqual(self.elsa.estado_credito, 'HABILITADO')
        self.assertEqual(credito.saldo(self.elsa), D('2000'))
        self.assertEqual(self.alfajor.stock_actual, stock_antes - 2)       # el stock baja en el acto
        self.assertEqual(self.caja(), caja_antes)                          # el cajón no se mueve
        fiado = MovimientoCuenta.objects.get(id_venta=venta)
        self.assertEqual(fiado.fecha_vencimiento, timezone.localdate() + timedelta(days=30))

    def test_la_titular_y_quien_gestiona_no_necesitan_firma_ajena(self):
        self.fiar(usuario=self.admin)
        self.assertEqual(Cliente.objects.get(pk=self.elsa.pk).estado_credito, 'HABILITADO')

    def test_habilitado_y_al_dia_fia_la_cajera_sola_con_la_fecha_acordada(self):
        self.habilitar()
        vence = timezone.localdate() + timedelta(days=10)
        venta = self.fiar(vence=vence)
        self.assertEqual(MovimientoCuenta.objects.get(id_venta=venta).fecha_vencimiento, vence)

    def test_la_fecha_de_pago_no_puede_ser_pasada(self):
        self.habilitar()
        with self.assertRaisesMessage(ValidationError, 'anterior a hoy'):
            self.fiar(vence=timezone.localdate() - timedelta(days=1))

    def test_el_tope_frena_salvo_autorizacion(self):
        self.habilitar(limite='2500')
        self.fiar('2')
        with self.assertRaisesMessage(ValidationError, 'su tope es'):
            self.fiar('1')
        self.fiar('1', autorizante=self.admin)
        self.assertEqual(credito.saldo(self.elsa), D('3000'))

    def test_en_mora_no_se_fia_ni_se_canjea_sin_autorizacion(self):
        self.habilitar()
        venta = self.fiar()
        f.atrasar_fiados(MovimientoCuenta.objects.filter(id_venta=venta), 5)
        aviso = credito.aviso_de_mora(self.elsa)
        self.assertIn('Elsa Gómez debe', aviso)
        self.assertIn('vencidos hace 5 días', aviso)
        with self.assertRaisesMessage(ValidationError, 'No se le fía hasta que se ponga al día'):
            self.fiar('1')
        with self.assertRaisesMessage(ValidationError, 'No puede usar puntos ni beneficios'):
            credito.exigir_que_pueda_canjear(self.elsa, self.cajera, None)
        credito.exigir_que_pueda_canjear(self.elsa, self.cajera, self.admin)       # con la firma, sí

    def test_incobrable_no_se_fia_ni_con_firma(self):
        self.habilitar()
        self.fiar()
        credito.declarar_incobrable(self.elsa, 'Se mudó sin avisar', self.admin)
        with self.assertRaisesMessage(ValidationError, 'incobrable'):
            self.fiar('1', autorizante=self.admin)

    def test_pago_mixto_parte_en_efectivo_parte_fiado(self):
        self.habilitar()
        venta = ventas.registrar(
            self.turno, self.cajera, [ventas.Item(self.alfajor, D('3'))],
            [ventas.Pago(f.efectivo(), monto='1000', recibido='1000'), ventas.Pago(self.cc)],
            cliente=self.elsa)[0]
        self.assertEqual(credito.lo_que_falta_de(venta), D('2000'))
        self.assertEqual(MovimientoCaja.objects.get(id_venta=venta).monto, D('1000'))

    def test_los_puntos_de_lo_fiado_esperan_al_pago(self):
        self.habilitar()
        self.fiar('5')
        self.elsa.refresh_from_db()
        self.assertEqual(self.elsa.puntos_disponibles, 0)


# ===========================================================================
#  Cobrar
# ===========================================================================
class CobrarTest(Base):

    def setUp(self):
        super().setUp()
        self.habilitar()
        self.venta = self.fiar('5')                     # $ 5.000; 1 punto cada $ 100 = 50 puntos

    def test_cobro_parcial_en_efectivo_entra_al_cajon(self):
        antes = self.caja()
        r = credito.cobrar(self.elsa, '2000', f.efectivo(), self.cajera, turno=self.turno)
        self.assertEqual((r['cobrado'], r['queda'], r['puntos']), (D('2000'), D('3000'), 0))
        self.assertEqual(self.caja(), antes + 2000)
        self.assertEqual(MovimientoCaja.objects.filter(tipo_movimiento='COBRO_CUENTA').get().monto, D('2000'))

    def test_por_transferencia_no_toca_el_cajon_y_cierra_la_deuda_con_puntos(self):
        antes = self.caja()
        transferencia = MedioPago.objects.create(nombre_medio_pago='Transferencia prueba', afecta_caja=False)
        r = credito.cobrar(self.elsa, '5000', transferencia, self.cajera)
        self.assertEqual(self.caja(), antes)
        self.assertEqual((r['queda'], r['puntos'], r['compras_saldadas']), (D('0'), 50, 1))
        self.elsa.refresh_from_db()
        self.assertEqual(self.elsa.puntos_disponibles, 50)

    def test_efectivo_sin_caja_abierta_no_se_cobra(self):
        with self.assertRaisesMessage(ValidationError, 'caja abierta'):
            credito.cobrar(self.elsa, '1000', f.efectivo(), self.cajera, turno=None)

    def test_no_se_cobra_mas_de_lo_que_debe_ni_con_cuenta_corriente(self):
        with self.assertRaisesMessage(ValidationError, 'no se le puede cobrar'):
            credito.cobrar(self.elsa, '5001', f.efectivo(), self.cajera, turno=self.turno)
        with self.assertRaisesMessage(ValidationError, 'Elegí con qué paga'):
            credito.cobrar(self.elsa, '100', self.cc, self.cajera)

    def test_recargo_opcional_sobre_lo_que_paga(self):
        sin = credito.cobrar(self.elsa, '1000', f.efectivo(), self.cajera, turno=self.turno)
        con = credito.cobrar(self.elsa, '1000', f.efectivo(), self.cajera, turno=self.turno, interes_porcentaje='10')
        self.assertEqual((sin['interes'], con['interes'], con['total']), (D('0'), D('100'), D('1100')))
        self.assertEqual(credito.saldo(self.elsa), D('3000'))              # el recargo no agranda la deuda
        self.assertEqual(MovimientoCuenta.objects.filter(tipo_movimiento='INTERES').get().monto, D('100'))

    def test_pagando_tarde_los_puntos_vienen_destildados_pero_se_pueden_dar(self):
        f.atrasar_fiados(MovimientoCuenta.objects.filter(id_venta=self.venta), 3)
        r = credito.cobrar(self.elsa, '5000', f.efectivo(), self.cajera, turno=self.turno)
        self.assertEqual(r['puntos'], 0)                                    # por defecto, no
        otra = self.fiar('2')
        f.atrasar_fiados(MovimientoCuenta.objects.filter(id_venta=otra), 3)
        r = credito.cobrar(self.elsa, '2000', f.efectivo(), self.cajera, turno=self.turno, acreditar_puntos=True)
        self.assertEqual(r['puntos'], 20)                                   # lo decidió la dueña

    def test_en_termino_se_puede_no_dar_los_puntos(self):
        r = credito.cobrar(self.elsa, '5000', f.efectivo(), self.cajera, turno=self.turno, acreditar_puntos=False)
        self.assertEqual(r['puntos'], 0)

    def test_se_aplica_del_fiado_mas_viejo_al_mas_nuevo(self):
        nueva = self.fiar('1')
        credito.cobrar(self.elsa, '5500', f.efectivo(), self.cajera, turno=self.turno)
        self.assertEqual(credito.lo_que_falta_de(self.venta), D('0'))
        self.assertEqual(credito.lo_que_falta_de(nueva), D('500'))


# ===========================================================================
#  Lo que decide la dueña
# ===========================================================================
class GestionTest(Base):

    def setUp(self):
        super().setUp()
        self.habilitar()
        self.fiar('3')

    def test_la_cajera_no_puede_decidir(self):
        for accion in (lambda: credito.cambiar_estado(self.elsa, 'SANCIONADO', self.cajera),
                       lambda: credito.quitar_puntos(self.elsa, 1, self.cajera),
                       lambda: credito.declarar_incobrable(self.elsa, 'Motivo largo', self.cajera)):
            with self.assertRaisesMessage(ValidationError, 'quien gestiona el fiado'):
                accion()

    def test_suspender_y_quitar_puntos(self):
        from fidelizacion.models import MovimientoPuntos
        MovimientoPuntos.objects.create(id_cliente=self.elsa, tipo_movimiento='AJUSTE', puntos=40)
        credito.cambiar_estado(self.elsa, 'SANCIONADO', self.admin, nota='Paga a fin de mes')
        credito.quitar_puntos(self.elsa, 15, self.admin)
        self.elsa.refresh_from_db()
        self.assertEqual((self.elsa.estado_credito, self.elsa.puntos_disponibles, self.elsa.nota_credito),
                         ('SANCIONADO', 25, 'Paga a fin de mes'))
        self.assertTrue(credito.situacion(self.elsa)['en_mora'])
        with self.assertRaisesMessage(ValidationError, 'no se le pueden quitar'):
            credito.quitar_puntos(self.elsa, 26, self.admin)

    def test_no_se_le_saca_la_cuenta_a_quien_debe(self):
        with self.assertRaisesMessage(ValidationError, 'todavía debe'):
            credito.cambiar_estado(self.elsa, 'SIN_CREDITO', self.admin)

    def test_incobrable_cierra_la_deuda_y_va_al_resumen_como_perdida(self):
        hoy = timezone.localdate()
        antes = mensual.del_mes(hoy.year, hoy.month)
        perdido = credito.declarar_incobrable(self.elsa, 'Falleció', self.admin)
        self.assertEqual(perdido, D('3000'))
        self.assertEqual(credito.saldo(self.elsa), D('0'))
        self.assertEqual(Cliente.objects.get(pk=self.elsa.pk).estado_credito, 'INCOBRABLE')
        despues = mensual.del_mes(hoy.year, hoy.month)
        self.assertEqual(despues['incobrable'], D('3000'))
        self.assertEqual(antes['resultado'] - despues['resultado'], D('3000'))


# ===========================================================================
#  Anulación y devolución de una venta fiada
# ===========================================================================
class AnulacionYDevolucionTest(Base):

    def setUp(self):
        super().setUp()
        self.habilitar()
        self.venta = self.fiar('4')

    def test_anular_cancela_la_deuda_sin_tocar_el_cajon(self):
        antes = self.caja()
        resultado = anulacion.anular(self.venta, self.admin, 'ERROR_DE_CARGA', autorizante=self.admin)
        self.assertEqual(resultado.deuda_cancelada, D('4000'))
        self.assertEqual(credito.saldo(self.elsa), D('0'))
        self.assertEqual(self.caja(), antes)

    def test_si_ya_pago_algo_no_se_anula_se_devuelve(self):
        credito.cobrar(self.elsa, '1000', f.efectivo(), self.cajera, turno=self.turno)
        with self.assertRaisesMessage(ValidationError, 'ya pagó parte'):
            anulacion.anular(self.venta, self.admin, 'ERROR_DE_CARGA', autorizante=self.admin)

    def devolver(self, cantidad, medio, autorizante=None):
        return cambios.registrar(
            self.turno, self.cajera, self.venta, 'PRODUCTO_DANADO',
            [cambios.Devuelto(self.venta.detalles.get(), D(cantidad), True)],
            medio_pago=medio, autorizante=autorizante or self.admin)

    def test_la_devolucion_achica_la_deuda_y_no_saca_plata_del_cajon(self):
        antes = self.caja()
        self.assertEqual(list(cambios.medios_de_reintegro(self.venta)), [self.cc])
        with self.assertRaisesMessage(ValidationError, 'se descuenta de esa deuda'):
            self.devolver('1', f.efectivo())
        cambio = self.devolver('1', self.cc)
        self.assertEqual(cambio.deuda_cancelada, D('1000'))
        self.assertEqual(credito.saldo(self.elsa), D('3000'))
        self.assertEqual(self.caja(), antes)

    def test_lo_que_ya_pago_se_le_devuelve_por_el_medio_con_que_pago(self):
        credito.cobrar(self.elsa, '4000', f.efectivo(), self.cajera, turno=self.turno)
        self.assertEqual(list(cambios.medios_de_reintegro(self.venta)), [f.efectivo()])
        antes = self.caja()
        self.devolver('1', f.efectivo())
        self.assertEqual(self.caja(), antes - 1000)

    def test_los_puntos_de_lo_devuelto_no_se_acreditan_despues(self):
        self.devolver('2', self.cc)
        r = credito.cobrar(self.elsa, '2000', f.efectivo(), self.cajera, turno=self.turno)
        self.assertEqual(r['puntos'], 20)                   # 40 de la compra entera, menos lo devuelto


# ===========================================================================
#  Resumen del mes: la cuenta cierra al centavo
# ===========================================================================
class ResumenTest(Base):

    def test_vendido_es_cobrado_al_vender_mas_fiado_y_entra_lo_cobrado_de_cuentas(self):
        self.habilitar()
        self.fiar('3')
        ventas.registrar(self.turno, self.cajera, [ventas.Item(self.alfajor, D('2'))],
                         [ventas.Pago(f.efectivo(), recibido='2000')])
        credito.cobrar(self.elsa, '1000', f.efectivo(), self.cajera, turno=self.turno, interes_porcentaje='5')
        hoy = timezone.localdate()
        r = mensual.del_mes(hoy.year, hoy.month)
        self.assertEqual((r['bruta']['facturado'], r['en_el_momento'], r['fiado']), (D('5000'), D('2000'), D('3000')))
        self.assertTrue(r['cuadra'])
        self.assertEqual((r['cobrado_de_cuentas'], r['interes'], r['total_entro']), (D('1050'), D('50'), D('3050')))
        self.assertEqual(r['te_deben'], D('2000'))
        self.assertNotIn(MedioPago.NOMBRE_CUENTA_CORRIENTE, [m for m, _ in r['entro']])


# ===========================================================================
#  Pantallas y endpoints
# ===========================================================================
class PantallasTest(Base):

    def entrar(self, usuario):
        f.entrar(self.client, usuario)
        self.client.post(reverse('ventas:elegir_caja'), {'caja': self.turno.id_caja_registradora_id})

    def pedir(self, nombre, cuerpo):
        return self.client.post(reverse(f'ventas:{nombre}'), json.dumps(cuerpo),
                                content_type='application/json').json()

    def cuerpo(self, **extra):
        base = {'token': uuid.uuid4().hex, 'cliente': self.elsa.pk, 'beneficios': [],
                'items': [{'producto': self.alfajor.pk, 'cantidad': '1'}],
                'pagos': [{'medio': self.cc.pk, 'monto': '', 'recibido': ''}]}
        base.update(extra)
        return base

    def test_el_mostrador_avisa_antes_de_cobrar_y_cobra_con_la_firma(self):
        self.entrar(self.cajera)
        sin = self.pedir('cotizar', self.cuerpo())
        self.assertFalse(sin['ok'])
        self.assertIn('autorice', sin['error'])
        anonimo = self.pedir('cotizar', self.cuerpo(cliente=None))
        self.assertIn('hay que saber a quién', anonimo['error'])
        firma = {'usuario': self.admin.nombre_usuario, 'clave': f.CLAVE}
        con = self.pedir('cotizar', self.cuerpo(autoriza=firma))
        self.assertTrue(con['ok'], con)
        self.assertTrue(con['pide_autorizacion'])
        cobro = self.pedir('cobrar', self.cuerpo(autoriza=firma))
        self.assertTrue(cobro['ok'], cobro)
        self.assertEqual(cobro['fiado']['monto'], '1000.00')
        self.assertEqual(Cliente.objects.get(pk=self.elsa.pk).estado_credito, 'HABILITADO')

    def test_cuentas_corrientes_y_cobro_desde_la_pantalla(self):
        self.habilitar()
        self.fiar('2')
        self.entrar(self.cajera)
        lista = self.client.get(reverse('fidelizacion:cuentas'))
        self.assertContains(lista, 'Gómez, Elsa')
        url = reverse('fidelizacion:cuenta', args=[self.elsa.pk])
        pantalla = self.client.get(url)
        self.assertContains(pantalla, 'Registrar el pago')
        self.assertNotContains(pantalla, 'Declarar incobrable')        # la cajera no gestiona
        respuesta = self.client.post(url, {
            'token_operacion': pantalla.context['token'], 'accion': 'cobrar', 'monto': '500',
            'medio': f.efectivo().pk, 'interes': '0', 'puntos_decidido': '1', 'puntos': '1'}, follow=True)
        self.assertContains(respuesta, 'Queda debiendo')
        self.assertEqual(credito.saldo(self.elsa), D('1500'))
        repetido = self.client.post(url, {
            'token_operacion': pantalla.context['token'], 'accion': 'cobrar', 'monto': '500',
            'medio': f.efectivo().pk}, follow=True)
        self.assertContains(repetido, 'ya se había registrado')
        self.assertEqual(credito.saldo(self.elsa), D('1500'))

    def test_radar_y_parecidos(self):
        self.habilitar()
        venta = self.fiar('2')
        f.atrasar_fiados(MovimientoCuenta.objects.filter(id_venta=venta), 4)
        credito.cambiar_estado(self.elsa, 'HABILITADO', self.admin, nota='Cobra la jubilación el 10')
        self.entrar(self.cajera)
        mostrador = self.client.get(reverse('ventas:mostrador'))
        self.assertContains(mostrador, 'Deudores en mora (1)')
        radar = self.client.get(reverse('fidelizacion:morosos')).json()['morosos']
        self.assertEqual((radar[0]['dias'], radar[0]['nota']), (4, 'Cobra la jubilación el 10'))
        parecidos = self.client.get(reverse('fidelizacion:parecidos'),
                                    {'dni': '20.111.222', 'apellido': 'Otro', 'nombre': 'X'}).json()['parecidos']
        self.assertEqual(parecidos[0]['por'], 'el mismo DNI')
        self.assertIn('vencidos', parecidos[0]['aviso'])
        por_nombre = self.client.get(reverse('fidelizacion:parecidos'),
                                     {'apellido': 'gomez', 'nombre': 'Elsa María'}).json()['parecidos']
        self.assertEqual(por_nombre[0]['por'], 'el mismo nombre')


@override_settings(CAFETO_N8N_TOKEN=TOKEN)
class AutomatizacionTest(Base):

    def test_el_resumen_avisa_los_fiados_atrasados_y_los_que_vencen(self):
        self.habilitar()
        atrasado = self.fiar('2')
        f.atrasar_fiados(MovimientoCuenta.objects.filter(id_venta=atrasado), 2)
        self.fiar('1', vence=timezone.localdate() + timedelta(days=1), autorizante=self.admin)
        datos = self.client.get(reverse('api_servicio:resumen'), HTTP_AUTHORIZATION=f'Bearer {TOKEN}').json()
        cc = datos['cuentas_corrientes']
        self.assertEqual(cc['te_deben'], '3000.00')
        self.assertEqual(cc['atrasados'][0]['dias_de_atraso'], 2)
        self.assertEqual(cc['vencen_pronto'][0]['falta'], '1000.00')


class RecaudacionTest(Base):

    def test_lo_fiado_no_suma_y_lo_cobrado_de_cuentas_si(self):
        from ventas import caja
        self.habilitar()
        self.fiar('3')
        credito.cobrar(self.elsa, '1000', f.efectivo(), self.cajera, turno=self.turno)
        transferencia = MedioPago.objects.create(nombre_medio_pago='Transferencia prueba', afecta_caja=False)
        credito.cobrar(self.elsa, '500', transferencia, self.cajera, turno=self.turno)
        filas = {fila['medio']: fila for fila in caja.recaudacion(self.turno)}
        self.assertTrue(filas[MedioPago.NOMBRE_CUENTA_CORRIENTE]['fiado'])
        self.assertEqual(filas[MedioPago.NOMBRE_CUENTA_CORRIENTE]['total'], D('3000'))
        self.assertEqual(filas['Efectivo']['cuentas'], D('1000'))
        self.assertEqual(filas['Transferencia prueba']['total'], D('500'))
        self.assertEqual(caja.saldo(self.turno) - D('5000'), D('1000'))      # sólo el efectivo, al cajón


# ===========================================================================
#  Estado coherente en pantalla: lo que se ve no contradice lo que es
# ===========================================================================
class EstadoEnPantallaTest(Base):

    def setUp(self):
        super().setUp()
        self.habilitar()
        self.fiar('3')                                  # debe $ 3.000
        self.pedro = cliente('Pérez', 'Pedro', '3874555222', '20333444')
        self.habilitar(self.pedro)
        self.fiar('8', quien=self.pedro)                # $ 8.000, que se dan por perdidos
        credito.declarar_incobrable(self.pedro, 'Se mudó sin avisar', self.admin)
        self.rita = cliente('Ríos', 'Rita', '3874555333', '20555666')
        credito.cambiar_estado(self.rita, 'SANCIONADO', self.admin)
        f.entrar(self.client, self.admin)

    def test_el_listado_no_esconde_a_nadie_y_cuenta_cada_vista(self):
        listado = self.client.get(reverse('fidelizacion:cuentas'))
        cuentas = {v['clave']: v['cantidad'] for v in listado.context['vistas']}
        self.assertEqual(cuentas, {'deuda': 1, 'mora': 1, 'incobrables': 1, 'todos': 3})
        self.assertEqual(listado.context['perdido'], D('8000'))
        self.assertEqual([c.pk for c in listado.context['filas']], [self.elsa.pk])
        incobrables = self.client.get(reverse('fidelizacion:cuentas'), {'ver': 'incobrables'})
        self.assertEqual([c.pk for c in incobrables.context['filas']], [self.pedro.pk])
        self.assertContains(incobrables, 'Dado por perdido')
        self.assertContains(incobrables, '8.000,00')
        self.assertContains(incobrables, 'aria-current="page"')
        raro = self.client.get(reverse('fidelizacion:cuentas'), {'ver': '<script>'})
        self.assertEqual(raro.context['ver'], 'deuda')

    def test_un_incobrable_no_dice_que_no_debe_nada(self):
        ficha = self.client.get(reverse('fidelizacion:cuenta', args=[self.pedro.pk]))
        self.assertNotContains(ficha, 'No debe nada')
        self.assertContains(ficha, 'Se dieron por perdidos')
        self.assertContains(ficha, 'como incobrables')
        suspendida = self.client.get(reverse('fidelizacion:cuenta', args=[self.rita.pk]))
        self.assertNotContains(suspendida, 'No debe nada')
        self.assertContains(suspendida, 'sigue suspendida')

    def test_el_combo_marca_incobrable_y_guardar_no_lo_rehabilita(self):
        url = reverse('fidelizacion:cuenta', args=[self.pedro.pk])
        ficha = self.client.get(url)
        self.assertContains(ficha, '<option value="INCOBRABLE" selected>Incobrable — no se le fía</option>', html=True)
        self.assertNotContains(ficha, '<option value="HABILITADO" selected>', html=False)
        self.client.post(url, {'accion': 'estado', 'estado': 'INCOBRABLE', 'limite': '0', 'nota': 'No fiar más',
                               'plazo': '30', 'token_operacion': ficha.context['token']})
        self.pedro.refresh_from_db()
        self.assertEqual((self.pedro.estado_credito, self.pedro.nota_credito), ('INCOBRABLE', 'No fiar más'))
        with self.assertRaisesMessage(ValidationError, 'no se elige a mano'):
            credito.cambiar_estado(self.elsa, 'INCOBRABLE', self.admin)

    def test_el_inicio_avisa_el_fiado_vencido(self):
        from negocio import panel
        f.atrasar_fiados(MovimientoCuenta.objects.filter(id_cliente=self.elsa, tipo_movimiento='FIADO'), 4)
        _numeros, avisos = panel.del_dia(self.admin)
        aviso = next(a for a in avisos if a['clave'] == 'FIADO_VENCIDO')
        self.assertEqual(aviso['tipo'], 'error')
        self.assertIn('1 cliente tiene deuda en cuenta corriente vencida (por $\u00a03.000,00)', aviso['texto'])
        self.assertEqual(self.client.get(reverse('fidelizacion:cuentas'), {'estado': 'vencidos'}).context['ver'],
                         'mora')
