"""Las tareas del equipo desde el celular, con las reglas de la web.

    IdempotenciaTest    un reintento no repite la operación; si falló, la
                        clave queda libre
    MermaTraspasoTest   quedan PENDIENTES, sin mover stock; OTRO pide detalle
    AutorizarTest       DD-67: quien carga no aprueba lo propio (salvo la
                        Administradora); el stock se mueve recién al aprobar
    ConteoTest          a ciegas: quien cuenta nunca ve el teórico
    RecepcionTest       contra la orden, con diferencias, y sin repetirse
    ProduccionTest      la tanda que vence pide su fecha; la que no, no
    PanelTest           lo mismo que el inicio de la web, según permisos
    LotesOfertasTest    lotes con stock (vencidos incluidos) y el sector de
                        ofertas de las etiquetas
"""
import json
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from api_movil.idempotencia import YA_SE_HABIA_CARGADO
from api_movil.models import OperacionDelCelular
from api_movil.tests.base import D, BaseCatalogo
from compras import servicios as compras
from compras.models import Compra
from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria
from stock import servicios
from stock.models import AjusteStock, Lote, Produccion, Producto


class Base(BaseCatalogo):

    def enviar(self, ruta, datos, como, clave='clave-de-prueba-0001'):
        """Un POST que mueve mercadería, con su ``Idempotency-Key``."""
        extra = {'HTTP_IDEMPOTENCY_KEY': clave} if clave else {}
        return self.pedir('post', ruta, datos, como=como, **extra)

    def merma(self, como, clave='clave-de-prueba-0001', **cambios):
        datos = {'motivo': 'ROTURA', 'detalle': None,
                 'renglones': [{'producto': self.gaseosa.pk, 'cantidad': 1}]}
        datos.update(cambios)
        return self.enviar('/stock/mermas/', datos, como, clave)

    def stock(self, producto):
        return Producto.objects.get(pk=producto.pk).stock_actual


# ===========================================================================
class IdempotenciaTest(Base):

    def test_la_misma_clave_no_repite_la_merma(self):
        primera = self.merma(self.operador)
        self.assertEqual(primera.status_code, 201)
        segunda = self.merma(self.operador)
        self.assertEqual(segunda.status_code, 409)
        self.assertEqual(segunda.json(), {'detail': YA_SE_HABIA_CARGADO})
        self.assertEqual(AjusteStock.objects.count(), 1)
        anotada = OperacionDelCelular.objects.get()
        self.assertEqual((anotada.operacion, anotada.id_registro),
                         ('MERMA', primera.json()['id']))

    def test_sin_clave_no_se_hace(self):
        respuesta = self.merma(self.operador, clave=None)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('Idempotency-Key', respuesta.json()['detail'])
        self.assertFalse(AjusteStock.objects.exists())

    def test_si_falla_la_clave_queda_libre_para_corregir_y_reenviar(self):
        fallida = self.merma(self.operador, motivo='OTRO')
        self.assertEqual(fallida.status_code, 400)
        self.assertFalse(OperacionDelCelular.objects.exists())
        corregida = self.merma(self.operador, motivo='OTRO', detalle='Se mojó la caja')
        self.assertEqual(corregida.status_code, 201)

    def test_la_clave_es_de_cada_usuario(self):
        self.assertEqual(self.merma(self.operador).status_code, 201)
        self.assertEqual(self.merma(self.encargada).status_code, 201)
        self.assertEqual(AjusteStock.objects.count(), 2)

    def test_una_clave_de_hace_mas_de_un_dia_ya_no_cuenta(self):
        OperacionDelCelular.objects.create(
            id_usuario=self.operador, clave='clave-de-prueba-0001', operacion='MERMA',
            fecha=timezone.now() - timedelta(hours=25))
        self.assertEqual(self.merma(self.operador).status_code, 201)


# ===========================================================================
class MermaTraspasoTest(Base):

    def test_la_merma_queda_pendiente_y_no_mueve_stock(self):
        respuesta = self.merma(self.operador)
        self.assertEqual(respuesta.status_code, 201)
        ajuste = AjusteStock.objects.get()
        self.assertEqual(respuesta.json(), {'id': ajuste.pk, 'tipo': 'MERMA',
                                            'estado': 'PENDIENTE'})
        self.assertEqual(self.stock(self.gaseosa), 10)
        asiento = LogAuditoria.objects.get(operacion_realizada='ALTA DE MERMA')
        self.assertEqual(asiento.id_usuario, self.operador)
        self.assertEqual(len(asiento.registro_nuevo['renglones']), 1)
        self.assertIn('app móvil', asiento.detalle_operacion)

    def test_motivo_otro_sin_detalle(self):
        respuesta = self.merma(self.operador, motivo='OTRO', detalle=None)
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json(), {'detail': 'El detalle es obligatorio.'})
        self.assertFalse(AjusteStock.objects.exists())

    def test_un_motivo_que_no_es_de_merma(self):
        respuesta = self.merma(self.operador, motivo='USO_EN_CAFETERIA')
        self.assertEqual(respuesta.json(), {'detail': 'Elegí el motivo de la merma.'})

    def test_sin_lote_sale_del_que_vence_primero(self):
        self.merma(self.operador, renglones=[{'producto': str(self.yogur.pk), 'cantidad': 1}])
        renglon = AjusteStock.objects.get().detalles.get()
        self.assertEqual(renglon.id_lote.numero_lote, 'L1')       # vence en 3 días

    def test_no_sale_mas_de_lo_que_hay(self):
        respuesta = self.merma(self.operador, renglones=[{'producto': self.gaseosa.pk,
                                                          'cantidad': 50}])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('no pueden salir', respuesta.json()['detail'])

    def test_media_lata_no(self):
        respuesta = self.merma(self.operador, renglones=[{'producto': self.gaseosa.pk,
                                                          'cantidad': 0.5}])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('entera', respuesta.json()['detail'])

    def test_traspaso_de_la_gondola_a_la_cafeteria(self):
        sachet = f.producto('Leche entera', prov=self.prov, contenido='1', unidad='L',
                            precio='1200')
        leche = f.producto('Leche', 'INSUMO', prov=self.prov, rubro_nombre='Cafetería',
                           costo='1100', contenido='1', unidad='L')
        f.comprar(self.encargada, self.prov, (sachet, 5, '1000'))
        respuesta = self.enviar('/stock/traspasos/', {
            'motivo': 'USO_EN_CAFETERIA', 'detalle': None,
            'renglones': [{'producto': sachet.pk, 'cantidad': 2, 'destino': leche.pk,
                           'cantidad_destino': 2}]}, self.encargada)
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual((respuesta.json()['tipo'], respuesta.json()['estado']),
                         ('TRASPASO', 'PENDIENTE'))
        self.assertEqual(self.stock(sachet), 5)
        # El operador no tiene el permiso de traspasar.
        self.assertEqual(self.enviar('/stock/traspasos/', {}, self.operador).status_code, 403)


# ===========================================================================
class AutorizarTest(Base):

    def pendientes(self, como):
        respuesta = self.pedir('get', '/stock/ajustes/', {'estado': 'PENDIENTE'}, como=como)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.json()['resultados']

    def aprobar(self, ajuste_id, como, observacion=''):
        return self.pedir('post', f'/stock/ajustes/{ajuste_id}/aprobar/',
                          {'observacion': observacion}, como=como)

    def test_quien_carga_no_aprueba_lo_propio(self):
        """DD-67: la encargada que cargó la merma no se la aprueba sola."""
        id_merma = self.merma(self.encargada).json()['id']
        ajuste = self.pendientes(self.encargada)[0]
        self.assertEqual((ajuste['propio'], ajuste['puede_aprobar']), (True, False))
        respuesta = self.aprobar(id_merma, self.encargada)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('lo cargaste vos', respuesta.json()['detail'])
        self.assertEqual(AjusteStock.objects.get().estado_ajuste, 'PENDIENTE')
        self.assertEqual(self.stock(self.gaseosa), 10)

    def test_otra_persona_aprueba_y_recien_ahi_se_mueve_el_stock(self):
        id_merma = self.merma(self.operador).json()['id']
        ajuste = self.pendientes(self.encargada)[0]
        self.assertEqual((ajuste['propio'], ajuste['puede_aprobar']), (False, True))
        self.assertEqual(ajuste['cargado_por'], 'Cami Prueba')
        self.assertEqual(ajuste['renglones'][0]['cantidad'], 1)
        respuesta = self.aprobar(id_merma, self.encargada, 'Visto')
        self.assertEqual(respuesta.json(), {'id': id_merma, 'tipo': 'MERMA',
                                            'estado': 'APROBADO'})
        self.assertEqual(self.stock(self.gaseosa), 9)
        asiento = LogAuditoria.objects.get(
            operacion_realizada='APROBACION DE AJUSTE DE STOCK')
        self.assertEqual(asiento.registro_anterior['estado_ajuste'], 'PENDIENTE')
        self.assertEqual(asiento.registro_nuevo['estado_ajuste'], 'APROBADO')

    def test_la_titular_aprueba_lo_propio(self):
        from nucleo.tests import fabrica
        duenia = fabrica.titular()
        id_merma = self.merma(duenia).json()['id']
        self.assertTrue(self.pendientes(duenia)[0]['puede_aprobar'])
        self.assertEqual(self.aprobar(id_merma, duenia).status_code, 200)

    def test_otro_administrador_no_aprueba_lo_propio(self):
        self.merma(self.admin)
        self.assertFalse(self.pendientes(self.admin)[0]['puede_aprobar'])

    def test_rechazar_pide_por_que(self):
        id_merma = self.merma(self.operador).json()['id']
        ruta = f'/stock/ajustes/{id_merma}/rechazar/'
        sin_motivo = self.pedir('post', ruta, {'observacion': ' '}, como=self.encargada)
        self.assertEqual(sin_motivo.json(), {'detail': 'Contá por qué se rechaza.'})
        rechazada = self.pedir('post', ruta, {'observacion': 'Está sana'}, como=self.encargada)
        self.assertEqual(rechazada.json()['estado'], 'RECHAZADO')
        self.assertEqual(self.stock(self.gaseosa), 10)

    def test_lo_ya_resuelto_es_409(self):
        id_merma = self.merma(self.operador).json()['id']
        self.aprobar(id_merma, self.encargada)
        self.assertEqual(self.aprobar(id_merma, self.encargada).status_code, 409)
        self.assertEqual(self.aprobar(999999, self.encargada).status_code, 404)

    def test_el_operador_no_autoriza(self):
        self.assertEqual(self.pedir('get', '/stock/ajustes/', como=self.operador).status_code, 403)
        self.assertEqual(self.pedir('get', '/stock/ajustes/', {'estado': 'APROBADO'},
                                    como=self.encargada).status_code, 400)


# ===========================================================================
class ConteoTest(Base):

    def contar(self, datos, como=None):
        return self.pedir('post', '/stock/conteo/renglones/', datos, como=como or self.operador)

    def test_el_conteo_es_a_ciegas(self):
        empezado = self.pedir('post', '/stock/conteo/', {'motivo': 'DIFERENCIA_CONTEO'},
                              como=self.operador)
        self.assertEqual(empezado.status_code, 201)
        self.assertEqual(empezado.json()['conteo']['estado'], 'EN_CURSO')
        self.assertEqual(empezado.json()['conteo']['iniciado_por'], 'Cami Prueba')
        self.contar({'producto': self.gaseosa.pk, 'contada': 7})
        respuesta = self.contar({'producto': self.yogur.pk, 'contada': 4})
        self.assertEqual(respuesta.status_code, 200)

        for vista in (respuesta, self.pedir('get', '/stock/conteo/', como=self.operador)):
            conteo = vista.json()['conteo']
            self.assertNotIn('teorica', json.dumps(conteo))       # ni el teórico...
            for renglon in conteo['renglones']:
                self.assertEqual(set(renglon), {'producto', 'lote', 'contada'})
                self.assertIsNone(renglon['producto']['stock_actual'])   # ...ni el stock
                self.assertNotIn('cantidad', renglon['lote'] or {})      # ...ni el del lote
            yogur = next(r for r in conteo['renglones'] if r['producto']['id'] == self.yogur.pk)
            self.assertEqual(yogur['lote']['numero'], 'L1')   # sin lote: el que vence primero

        enviado = self.pedir('post', '/stock/conteo/enviar/', {}, como=self.operador)
        self.assertEqual(enviado.json()['conteo']['estado'], 'PENDIENTE')
        # Quien autoriza sí ve el teórico y la diferencia.
        ajuste = self.pedir('get', '/stock/ajustes/', como=self.encargada).json()['resultados'][0]
        gaseosa = next(r for r in ajuste['renglones'] if r['producto']['id'] == self.gaseosa.pk)
        self.assertEqual((gaseosa['teorica'], gaseosa['contada'], gaseosa['diferencia']),
                         (10, 7, -3))
        self.assertTrue(LogAuditoria.objects.filter(operacion_realizada='INICIO DE CONTEO').exists())
        self.assertTrue(LogAuditoria.objects.filter(operacion_realizada='ENVIO DE CONTEO').exists())

    def test_volver_a_contar_reemplaza_lo_contado(self):
        self.pedir('post', '/stock/conteo/', {}, como=self.operador)
        self.contar({'producto': self.gaseosa.pk, 'contada': 7})
        conteo = self.contar({'producto': self.gaseosa.pk, 'contada': 8}).json()['conteo']
        self.assertEqual([r['contada'] for r in conteo['renglones']], [8])

    def test_un_lote_que_el_sistema_no_conocia(self):
        self.pedir('post', '/stock/conteo/', {}, como=self.operador)
        respuesta = self.contar({'producto': self.yogur.pk, 'contada': 3, 'nuevo_lote': {
            'fecha_vencimiento': f.en_dias(15).isoformat(), 'numero_lote': 'Y-9'}})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(Lote.objects.filter(id_producto=self.yogur, numero_lote='Y-9').exists())

    def test_un_solo_conteo_abierto_y_nada_sin_conteo(self):
        self.assertEqual(self.pedir('get', '/stock/conteo/', como=self.operador).json(),
                         {'conteo': None})
        sin_conteo = self.contar({'producto': self.gaseosa.pk, 'contada': 1})
        self.assertEqual(sin_conteo.json(), {'detail': 'No hay ningún conteo en curso.'})
        self.pedir('post', '/stock/conteo/', {}, como=self.operador)
        otro = self.pedir('post', '/stock/conteo/', {}, como=self.encargada)
        self.assertEqual(otro.status_code, 400)
        self.assertIn('Ya hay un conteo abierto', otro.json()['detail'])

    def test_pide_contar_stock(self):
        self.assertEqual(self.pedir('get', '/stock/conteo/',
                                    como=self.usuario_sin_permisos()).status_code, 403)


# ===========================================================================
class RecepcionTest(Base):

    def setUp(self):
        super().setUp()
        self.orden = compras.emitir_orden(self.prov, self.encargada, [
            {'producto': self.yogur, 'cantidad': D('12'), 'precio_unitario': D('500')},
            {'producto': self.gaseosa, 'cantidad': D('6'), 'precio_unitario': D('600')}])
        compras.enviar_orden(self.orden)

    def recibir(self, renglones, clave='recepcion-0001', numero=4567, **extra):
        return self.enviar('/compras/recepciones/', {
            'orden': self.orden.pk, 'tipo_comprobante': 'REMITO', 'punto_venta': 1,
            'numero_comprobante': numero, 'importe_otros_conceptos': 0,
            'renglones': renglones, **extra}, self.encargada, clave)

    def yogures(self, cantidad, numero='Y-1'):
        return {'producto': self.yogur.pk, 'cantidad': cantidad, 'costo_unitario': 500,
                'lotes': [{'fecha_vencimiento': f.en_dias(20).isoformat(),
                           'numero_lote': numero, 'cantidad': cantidad}]}

    def test_las_ordenes_por_recibir_y_su_detalle(self):
        ordenes = self.pedir('get', '/compras/ordenes/', {'estado': 'POR_RECIBIR'},
                             como=self.encargada).json()['resultados']
        self.assertEqual([(o['id'], o['estado'], o['renglones'], o['total']) for o in ordenes],
                         [(self.orden.pk, 'EMITIDA', 2, 9600)])
        detalle = self.pedir('get', f'/compras/ordenes/{self.orden.pk}/',
                             como=self.encargada).json()
        yogur = next(r for r in detalle['renglones'] if r['producto']['id'] == self.yogur.pk)
        self.assertEqual((yogur['pedido'], yogur['recibido'], yogur['precio_unitario']),
                         (12, 0, 500))
        self.assertTrue(yogur['producto']['controla_vencimiento'])
        self.assertEqual(yogur['producto']['unidad'], 'UNIDAD')

    def test_recepcion_con_diferencias(self):
        respuesta = self.recibir([self.yogures(10),
                                  {'producto': self.gaseosa.pk, 'cantidad': 6,
                                   'costo_unitario': 600}])
        self.assertEqual(respuesta.status_code, 201)
        datos = respuesta.json()
        self.assertEqual(datos['orden'], {'id': self.orden.pk, 'estado': 'CON_DIFERENCIAS'})
        self.assertEqual(datos['diferencias'], [{
            'producto': {'id': self.yogur.pk, 'nombre': 'Yogur frutilla'},
            'pedido': 12, 'recibido': 10, 'diferencia': -2}])
        self.assertEqual(self.stock(self.yogur), 20)
        self.assertTrue(LogAuditoria.objects.filter(operacion_realizada='ALTA DE COMPRA',
                                                    id_registro=datos['id']).exists())
        # Llega lo que faltaba: la orden queda recibida.
        resto = self.recibir([self.yogures(2, 'Y-2')], clave='recepcion-0002', numero=4568)
        self.assertEqual(resto.json()['orden']['estado'], 'RECEPCIONADA')
        self.assertEqual(resto.json()['diferencias'], [])

    def test_un_reintento_no_carga_dos_veces_la_factura(self):
        renglones = [self.yogures(12), {'producto': self.gaseosa.pk, 'cantidad': 6,
                                        'costo_unitario': 600}]
        self.assertEqual(self.recibir(renglones).status_code, 201)
        repetida = self.recibir(renglones)
        self.assertEqual(repetida.status_code, 409)
        self.assertEqual(repetida.json(), {'detail': YA_SE_HABIA_CARGADO})
        self.assertEqual(Compra.objects.filter(id_orden_compra=self.orden).count(), 1)

    def test_una_orden_que_ya_no_espera_mercaderia(self):
        compras.cancelar_orden(self.orden)
        respuesta = self.recibir([self.yogures(12)])
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn('ya no espera mercadería', respuesta.json()['detail'])
        self.assertFalse(OperacionDelCelular.objects.exists())

    def test_las_reglas_de_la_recepcion(self):
        mal_sumados = self.yogures(12)
        mal_sumados['lotes'][0]['cantidad'] = 10
        respuesta = self.recibir([mal_sumados])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('suman', respuesta.json()['detail'])
        sin_lotes = self.yogures(12)
        sin_lotes['lotes'] = []
        self.assertIn('cargá el o los lotes', self.recibir([sin_lotes]).json()['detail'])

    def test_un_costo_que_llama_la_atencion_pide_confirmarlo(self):
        """Como en la pantalla: un cero de más no entra sin que alguien lo mire."""
        caro = self.yogures(12)
        caro['costo_unitario'] = 5000                       # venía a 500
        renglones = [caro, {'producto': self.gaseosa.pk, 'cantidad': 6, 'costo_unitario': 600}]
        respuesta = self.recibir(renglones)
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(respuesta.json()['codigo'], 'COSTOS_RAROS')
        self.assertEqual(respuesta.json()['costos'], [{
            'producto': {'id': self.yogur.pk, 'nombre': 'Yogur frutilla'},
            'anterior': 500, 'nuevo': 5000}])
        self.assertFalse(Compra.objects.filter(id_orden_compra=self.orden).exists())
        self.assertFalse(OperacionDelCelular.objects.exists())     # la clave quedó libre
        confirmada = self.recibir(renglones, confirmo_costos=True)  # la misma clave
        self.assertEqual(confirmada.status_code, 201)
        self.assertEqual(Producto.objects.get(pk=self.yogur.pk).precio_costo, 5000)

    def test_pide_registrar_compra(self):
        self.assertEqual(self.pedir('get', '/compras/ordenes/', como=self.operador).status_code, 403)


# ===========================================================================
class ProduccionTest(Base):

    def setUp(self):
        super().setUp()
        harina = f.producto('Harina 000', 'INSUMO', prov=self.prov, rubro_nombre='Cafetería',
                            costo='1500', contenido='1', unidad='KG')
        f.comprar(self.encargada, self.prov, (harina, 10, '1500'))
        self.harina = harina
        self.medialuna = f.receta(
            f.producto('Medialuna de manteca', 'PRODUCIDO', rubro_nombre='Cafetería',
                       precio='600', controla_vencimiento=True), (harina, '0.04'))
        self.empanada = f.receta(
            f.producto('Empanada de carne', 'PRODUCIDO', rubro_nombre='Cafetería',
                       precio='1200'), (harina, '0.05'))

    def producir(self, datos, clave='produccion-0001'):
        return self.enviar('/stock/producciones/', datos, self.operador, clave)

    def test_lo_que_se_puede_producir(self):
        producibles = self.pedir('get', '/stock/producibles/', como=self.operador).json()
        medialuna = next(p for p in producibles['resultados']
                         if p['producto']['id'] == self.medialuna.pk)
        self.assertTrue(medialuna['producto']['controla_vencimiento'])
        self.assertEqual(medialuna['receta'], [{
            'insumo': {'id': self.harina.pk, 'nombre': 'Harina 000', 'presentacion': '1 kg',
                       'tipo': 'INSUMO', 'unidad': 'KG', 'cantidad_entera': False,
                       'controla_vencimiento': False, 'codigo_barras': None,
                       'stock_actual': 10},
            'cantidad': 0.04}])

    def test_produccion_sin_fecha_de_algo_que_vence(self):
        respuesta = self.producir({'producto': self.medialuna.pk, 'cantidad': 24})
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('indicá hasta cuándo vale la tanda', respuesta.json()['detail'])
        self.assertFalse(Produccion.objects.exists())

    def test_produccion_con_fecha(self):
        respuesta = self.producir({'producto': self.medialuna.pk, 'cantidad': 24,
                                   'fecha_vencimiento': f.en_dias(1).isoformat(),
                                   'observacion': 'Tanda de la mañana'})
        self.assertEqual(respuesta.status_code, 201)
        produccion = Produccion.objects.get()
        self.assertEqual(respuesta.json(), {
            'producto': {'id': self.medialuna.pk, 'nombre': 'Medialuna de manteca'},
            'cantidad': 24,
            'lote': {'id': produccion.lotes.get().pk, 'numero': f'P{produccion.pk}',
                     'vence': f.en_dias(1).isoformat(), 'dias': 1, 'cantidad': 24}})
        self.assertEqual(self.stock(self.medialuna), 24)
        self.assertEqual(self.stock(self.harina), D('9.04'))
        self.assertTrue(LogAuditoria.objects.filter(
            operacion_realizada='ALTA DE PRODUCCION').exists())

    def test_lo_que_no_vence_va_sin_fecha_y_sin_lote(self):
        respuesta = self.producir({'producto': self.empanada.pk, 'cantidad': 12})
        self.assertEqual(respuesta.status_code, 201)
        self.assertIsNone(respuesta.json()['lote'])

    def test_un_reintento_no_produce_dos_veces(self):
        datos = {'producto': self.empanada.pk, 'cantidad': 12}
        self.producir(datos)
        self.assertEqual(self.producir(datos).status_code, 409)
        self.assertEqual(Produccion.objects.count(), 1)


# ===========================================================================
class PanelTest(Base):

    def panel(self, como):
        respuesta = self.pedir('get', '/panel/', como=como)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.json()

    def test_cada_perfil_ve_lo_suyo(self):
        operador = self.panel(self.operador)
        self.assertEqual(operador['indicadores'], [])
        self.assertEqual(operador['avisos'], [{
            'tipo': 'aviso', 'texto': '1 lote(s) vencen en los próximos 7 días.',
            'destino': 'ALERTAS'}])
        administradora = self.panel(self.admin)
        self.assertEqual([(i['clave'], i['formato'], i['valor'])
                          for i in administradora['indicadores']],
                         [('VENDIDO_HOY', 'PESOS', 0), ('CAJAS_ABIERTAS', 'NUMERO', 0)])

    def test_dice_lo_mismo_que_el_inicio_de_la_web(self):
        self.merma(self.operador)                    # un ajuste que espera autorización
        celular = self.panel(self.encargada)
        self.assertIn({'tipo': 'aviso', 'destino': 'AJUSTES',
                       'texto': '1 ajuste(s) de stock esperan tu autorización.'},
                      celular['avisos'])
        f.entrar(self.client, self.encargada)
        web = self.client.get(reverse('home'), follow=True).context
        self.assertEqual([texto for _, _, texto, _ in web['avisos']],
                         [a['texto'] for a in celular['avisos']])
        self.assertEqual([i['titulo'] for i in web['indicadores'] if i['formato'] != 'enlace'],
                         [i['titulo'] for i in celular['indicadores']])

    def test_pide_estar_adentro(self):
        self.assertEqual(self.pedir('get', '/panel/').status_code, 401)


# ===========================================================================
class LotesOfertasTest(Base):

    def test_los_lotes_con_stock_incluidos_los_vencidos(self):
        Lote.objects.filter(id_producto=self.yogur, fecha_vencimiento=f.en_dias(3)).update(
            fecha_vencimiento=f.en_dias(-1))
        lotes = self.pedir('get', f'/stock/productos/{self.yogur.pk}/lotes/',
                           como=self.operador).json()['resultados']
        self.assertEqual([(l['numero'], l['dias'], l['cantidad']) for l in lotes],
                         [('L1', -1, 4), ('L2', 30, 6)])
        self.assertEqual(self.pedir('get', '/stock/productos/999999/lotes/',
                                    como=self.operador).status_code, 404)
        self.assertEqual(self.pedir('get', f'/stock/productos/{self.yogur.pk}/lotes/',
                                    como=self.usuario_sin_permisos()).status_code, 403)

    def test_el_sector_de_ofertas_es_el_de_las_etiquetas(self):
        ofertas = self.pedir('get', '/stock/ofertas/', como=self.operador).json()['resultados']
        lote, porcentaje, precio = servicios.en_oferta()[0]
        self.assertEqual(ofertas, [{
            'id': f'{self.yogur.pk}-{lote.pk}',
            'producto': {'id': self.yogur.pk, 'nombre': 'Yogur frutilla',
                         'presentacion': 'Unidad'},
            'lote': {'id': lote.pk, 'numero': 'L1', 'vence': f.en_dias(3).isoformat(),
                     'dias': 3, 'cantidad': 4},
            'precio': 1000, 'precio_oferta': int(precio), 'porcentaje': int(porcentaje)}])
        self.assertEqual(int(precio), 800)
