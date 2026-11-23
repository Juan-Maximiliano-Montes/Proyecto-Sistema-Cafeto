"""Los cuatro diagnósticos del catálogo y la bandeja de sugerencias (Etapa 2).

Lo que se controla acá es que la automatización pueda MIRAR el desorden
heredado (elaborados sin receta, márgenes caídos, stock en cero que igual
se vende, productos sin rubro) y PROPONER, pero no cambiar nada: las
cuatro consultas son de sólo lectura y la quinta deja una sugerencia que
espera a una persona.
"""
import json
from decimal import Decimal as D

from django.test import TestCase, override_settings
from django.urls import reverse

from nucleo.tests import fabrica as f
from stock.models import (
    EstadoSugerencia,
    Producto,
    Rubro,
    SugerenciaCatalogo,
    TipoSugerencia,
)
from ventas import servicios as ventas

TOKEN = 'credencial-de-prueba-larga-0123456789abcdef'


@override_settings(CAFETO_N8N_TOKEN=TOKEN, CAFETO_URL_PUBLICA='https://cafeto.test')
class Base(TestCase):

    def setUp(self):
        self.admin = f.usuario('30222100', 'Administrador')
        self.prov = f.proveedor()
        self.turno = f.abrir_turno(self.admin)

    def pedir(self, nombre, token=TOKEN, cuerpo=None, **parametros):
        cabeceras = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
        url = reverse(f'api_servicio:{nombre}')
        if cuerpo is None:
            return self.client.get(url, parametros, **cabeceras)
        return self.client.post(url, json.dumps(cuerpo),
                                content_type='application/json', **cabeceras)

    def vender(self, producto, cantidad='1'):
        return ventas.registrar(self.turno, self.admin,
                                [ventas.Item(producto, D(cantidad))],
                                [ventas.Pago(f.efectivo())])[0]


class SinRecetaTest(Base):

    def setUp(self):
        super().setUp()
        self.cafe = f.producto('Café con leche', tipo='ELABORADO',
                               precio='2000', costo='400',
                               rubro_nombre='Cafetería')
        self.licuado = f.producto('Licuado de banana', tipo='ELABORADO',
                                  precio='3000', costo='700',
                                  rubro_nombre='Cafetería')

    def test_lista_los_elaborados_sin_receta_ordenados_por_lo_que_facturaron(self):
        self.vender(self.cafe, '3')
        datos = self.pedir('productos_sin_receta').json()
        self.assertEqual(datos['total_pendientes'], 2)
        self.assertEqual(datos['dias'], 7)
        primero = datos['productos'][0]
        self.assertEqual(primero['id'], self.cafe.pk)
        self.assertEqual(primero['veces_vendido'], 1)
        self.assertEqual(D(primero['facturado']), D('6000'))
        self.assertEqual(primero['rubro'], 'Cafetería')
        self.assertTrue(primero['url'].startswith('https://cafeto.test/'))

    def test_sin_ventas_igual_devuelve_los_pendientes(self):
        """La lista existe para terminar el trabajo, no para quedar vacía."""
        datos = self.pedir('productos_sin_receta').json()
        self.assertEqual(datos['total_pendientes'], 2)
        # Sin ventas manda el precio: el licuado sale primero.
        self.assertEqual([p['id'] for p in datos['productos']],
                         [self.licuado.pk, self.cafe.pk])

    def test_el_que_ya_tiene_receta_sale_de_la_lista(self):
        insumo = f.producto('Café en grano', tipo='INSUMO', costo='5000',
                            prov=self.prov, unidad='KG', contenido='1')
        f.receta(self.cafe, (insumo, '0.018'))
        datos = self.pedir('productos_sin_receta').json()
        self.assertEqual(datos['total_pendientes'], 1)
        self.assertEqual([p['id'] for p in datos['productos']], [self.licuado.pk])

    def test_un_producto_dado_de_baja_no_cuenta(self):
        Producto.objects.filter(pk=self.licuado.pk).update(borrado_logico=True)
        self.assertEqual(self.pedir('productos_sin_receta').json()['total_pendientes'], 1)

    def test_los_dias_se_acotan_y_un_disparate_no_rompe(self):
        self.assertEqual(self.pedir('productos_sin_receta', dias='500').json()['dias'], 90)
        self.assertEqual(self.pedir('productos_sin_receta', dias='0').json()['dias'], 1)
        self.assertEqual(self.pedir('productos_sin_receta', dias='ayer').json()['dias'], 7)

    def test_el_top_recorta(self):
        datos = self.pedir('productos_sin_receta', top='1').json()
        self.assertEqual(len(datos['productos']), 1)
        self.assertEqual(datos['total_pendientes'], 2)

    def test_sin_credencial_no_entra(self):
        self.assertEqual(self.pedir('productos_sin_receta', token=None).status_code, 401)


class MargenBajoTest(Base):

    def test_sin_costo_a_perdida_y_margen_flaco(self):
        sin_costo = f.producto('Fideos sin costo', prov=self.prov,
                               precio='1000', costo='0')
        perdida = f.producto('Arroz a pérdida', prov=self.prov,
                             precio='500', costo='900')
        flaco = f.producto('Yerba al ras', prov=self.prov,
                           precio='1050', costo='1000')
        f.producto('Galletitas sanas', prov=self.prov, precio='2000', costo='1000')
        filas = {p['id']: p for p in self.pedir('productos_margen_bajo').json()['productos']}
        self.assertEqual(filas[sin_costo.pk]['motivo'], 'SIN_COSTO')
        self.assertEqual(filas[perdida.pk]['motivo'], 'A_PERDIDA')
        self.assertEqual(filas[flaco.pk]['motivo'], 'MARGEN_BAJO')
        self.assertEqual(len(filas), 3)        # el que gana bien no está

    def test_el_piso_se_puede_mover(self):
        f.producto('Fiambre justo', prov=self.prov, precio='1300', costo='1000')
        self.assertEqual(self.pedir('productos_margen_bajo', piso='15').json()['total'], 0)
        datos = self.pedir('productos_margen_bajo', piso='50').json()
        self.assertEqual(datos['piso'], 50)
        self.assertEqual(datos['total'], 1)
        self.assertEqual(datos['productos'][0]['motivo'], 'MARGEN_BAJO')

    def test_un_elaborado_con_receta_no_se_marca_sin_costo(self):
        """Su costo sale de la receta: el precio_costo en 0 no dice nada."""
        insumo = f.producto('Leche insumo', tipo='INSUMO', costo='1200',
                            prov=self.prov, unidad='L', contenido='1')
        cortado = f.producto('Cortado', tipo='ELABORADO', precio='1800',
                             costo='0', rubro_nombre='Cafetería')
        f.receta(cortado, (insumo, '0.1'))
        motivos = {p['id']: p['motivo']
                   for p in self.pedir('productos_margen_bajo').json()['productos']}
        self.assertNotIn(cortado.pk, motivos)

    def test_un_elaborado_sin_receta_y_sin_costo_si_avisa(self):
        solo = f.producto('Promo sin costo', tipo='ELABORADO', precio='1500',
                          costo='0', rubro_nombre='Cafetería')
        motivos = {p['id']: p['motivo']
                   for p in self.pedir('productos_margen_bajo').json()['productos']}
        self.assertEqual(motivos[solo.pk], 'SIN_COSTO')

    def test_primero_lo_que_se_movio_hace_poco(self):
        quieto = f.producto('Lata vieja', prov=self.prov, precio='900', costo='900')
        movido = f.producto('Lata que sale', prov=self.prov, precio='900', costo='900')
        f.comprar(self.admin, self.prov, (movido, 6, '900'))
        filas = self.pedir('productos_margen_bajo').json()['productos']
        self.assertEqual(filas[0]['id'], movido.pk)
        self.assertTrue(filas[0]['se_movio_hace_poco'])
        self.assertEqual(filas[1]['id'], quieto.pk)

    def test_un_insumo_no_se_mira(self):
        """No se vende: no tiene margen que revisar."""
        f.producto('Servilletas', tipo='INSUMO', prov=self.prov, costo='0')
        self.assertEqual(self.pedir('productos_margen_bajo').json()['total'], 0)

    def test_sin_credencial_no_entra(self):
        self.assertEqual(self.pedir('productos_margen_bajo', token=None).status_code, 401)


class ConteoRapidoTest(Base):

    def test_lo_que_esta_en_cero_y_se_sigue_vendiendo(self):
        agotado = f.producto('Gaseosa agotada', prov=self.prov, precio='1500', costo='900')
        f.comprar(self.admin, self.prov, (agotado, 2, '900'))
        self.vender(agotado, '2')               # queda en 0
        con_stock = f.producto('Gaseosa con stock', prov=self.prov, precio='1500', costo='900')
        f.comprar(self.admin, self.prov, (con_stock, 10, '900'))
        datos = self.pedir('productos_conteo_rapido').json()
        self.assertEqual(datos['dias'], 30)
        ids = [p['id'] for p in datos['productos']]
        self.assertIn(agotado.pk, ids)
        self.assertNotIn(con_stock.pk, ids)
        fila = next(p for p in datos['productos'] if p['id'] == agotado.pk)
        self.assertTrue(fila['se_vendio'])
        self.assertEqual(D(fila['unidades_vendidas']), D('2'))

    def test_los_que_quedaron_en_cero_al_migrar_tambien_aparecen(self):
        nunca = f.producto('Lavandina sin contar', prov=self.prov,
                           precio='800', costo='500')
        datos = self.pedir('productos_conteo_rapido').json()
        fila = next(p for p in datos['productos'] if p['id'] == nunca.pk)
        self.assertFalse(fila['se_vendio'])

    def test_los_que_rotan_van_primero_y_el_top_recorta(self):
        poco = f.producto('Alfajor flojo', prov=self.prov, precio='900', costo='500')
        mucho = f.producto('Alfajor que vuela', prov=self.prov, precio='900', costo='500')
        f.comprar(self.admin, self.prov, (poco, 1, '500'), (mucho, 5, '500'))
        self.vender(poco, '1')
        self.vender(mucho, '5')
        datos = self.pedir('productos_conteo_rapido', top='1').json()
        self.assertEqual([p['id'] for p in datos['productos']], [mucho.pk])
        self.assertGreaterEqual(datos['total'], 2)

    def test_un_elaborado_no_se_cuenta(self):
        """No tiene stock propio: su stock sale de los componentes."""
        f.producto('Café solo', tipo='ELABORADO', precio='1200', costo='300',
                   rubro_nombre='Cafetería')
        ids = [p['id'] for p in self.pedir('productos_conteo_rapido').json()['productos']]
        self.assertEqual(ids, [])

    def test_sin_credencial_no_entra(self):
        self.assertEqual(self.pedir('productos_conteo_rapido', token=None).status_code, 401)


class SinClasificarTest(Base):

    def setUp(self):
        super().setUp()
        self.otros = Rubro.objects.create(nombre_rubro='Otros Rubros')
        self.sin = Rubro.objects.create(nombre_rubro='sin clasificar')

    def test_los_dos_rubros_sin_criterio_con_clave_normalizada(self):
        a = f.producto('Producto perdido', prov=self.prov)
        Producto.objects.filter(pk=a.pk).update(id_rubro=self.otros)
        b = f.producto('Producto suelto', prov=self.prov)
        Producto.objects.filter(pk=b.pk).update(id_rubro=self.sin)
        f.producto('Producto ordenado', prov=self.prov)
        datos = self.pedir('productos_sin_clasificar').json()
        self.assertEqual(datos['total'], 2)
        self.assertEqual({p['id'] for p in datos['productos']}, {a.pk, b.pk})
        fila = next(p for p in datos['productos'] if p['id'] == a.pk)
        self.assertEqual(fila['rubro_actual'], 'Otros Rubros')
        self.assertEqual(fila['marca'], 'Sin marca')
        self.assertFalse(fila['tiene_foto'])

    def test_primero_lo_que_se_movio_hace_poco(self):
        quieto = f.producto('Quieto', prov=self.prov)
        movido = f.producto('Movido', prov=self.prov)
        Producto.objects.filter(pk__in=[quieto.pk, movido.pk]).update(id_rubro=self.otros)
        f.comprar(self.admin, self.prov, (movido, 3, '500'))
        filas = self.pedir('productos_sin_clasificar').json()['productos']
        self.assertEqual(filas[0]['id'], movido.pk)
        self.assertTrue(filas[0]['se_movio_hace_poco'])

    def test_el_top_recorta_sin_mentir_el_total(self):
        for nombre in ('Uno', 'Dos', 'Tres'):
            producto = f.producto(nombre, prov=self.prov)
            Producto.objects.filter(pk=producto.pk).update(id_rubro=self.otros)
        datos = self.pedir('productos_sin_clasificar', top='2').json()
        self.assertEqual(len(datos['productos']), 2)
        self.assertEqual(datos['total'], 3)

    def test_sin_credencial_no_entra(self):
        self.assertEqual(self.pedir('productos_sin_clasificar', token=None).status_code, 401)


class ProponerSugerenciaTest(Base):

    def setUp(self):
        super().setUp()
        Rubro.objects.create(nombre_rubro='Lácteos')
        self.leche = f.producto('Leche entera', prov=self.prov, precio='1500', costo='1000')

    def cuerpo(self, **cambios):
        datos = {'producto': self.leche.pk, 'tipo': 'RUBRO',
                 'motivo': 'Encontrado en Open Food Facts bajo lácteos',
                 'propuesta': {'rubro': 'Lácteos'}}
        datos.update(cambios)
        return datos

    def test_deja_la_sugerencia_y_no_toca_el_producto(self):
        respuesta = self.pedir('proponer_sugerencia', cuerpo=self.cuerpo())
        self.assertEqual(respuesta.status_code, 201)
        datos = respuesta.json()
        self.assertFalse(datos['ya_estaba'])
        self.assertEqual(datos['pendientes'], 1)
        self.assertTrue(datos['url'].startswith('https://cafeto.test/'))
        sugerencia = SugerenciaCatalogo.objects.get()
        self.assertEqual(sugerencia.estado_sugerencia, EstadoSugerencia.PENDIENTE)
        self.assertEqual(sugerencia.origen, 'AUTOMATIZACION')
        self.assertIsNone(sugerencia.id_usuario_resolucion)
        # El catálogo quedó como estaba: la automatización no cambia nada.
        self.assertEqual(Producto.objects.get(pk=self.leche.pk).id_rubro.nombre_rubro,
                         'Minisuper')

    def test_el_reintento_no_duplica(self):
        self.assertEqual(self.pedir('proponer_sugerencia', cuerpo=self.cuerpo()).status_code, 201)
        segunda = self.pedir('proponer_sugerencia', cuerpo=self.cuerpo())
        self.assertEqual(segunda.status_code, 200)
        self.assertTrue(segunda.json()['ya_estaba'])
        self.assertEqual(SugerenciaCatalogo.objects.count(), 1)

    def test_el_orden_de_las_claves_no_cambia_la_huella(self):
        self.pedir('proponer_sugerencia', cuerpo=self.cuerpo(
            propuesta={'rubro': 'Lácteos', 'extra': None}))
        self.pedir('proponer_sugerencia', cuerpo=self.cuerpo(
            propuesta={'rubro': 'Lácteos'}))
        self.assertEqual(SugerenciaCatalogo.objects.count(), 1)

    def test_deja_asiento_en_el_registro_de_actividad(self):
        from seguridad.models import LogAuditoria

        self.pedir('proponer_sugerencia', cuerpo=self.cuerpo())
        asiento = LogAuditoria.objects.filter(
            tabla_afectada='sugerencias_catalogo').first()
        self.assertIsNotNone(asiento)
        self.assertIn('SUGERENCIA', asiento.operacion_realizada)
        # El reintento no vuelve a anotar: no pasó nada nuevo.
        self.pedir('proponer_sugerencia', cuerpo=self.cuerpo())
        self.assertEqual(LogAuditoria.objects.filter(
            tabla_afectada='sugerencias_catalogo').count(), 1)

    def test_lo_que_no_tiene_forma_se_rechaza(self):
        casos = [
            (self.cuerpo(tipo='INVENTADO'), 400),
            (self.cuerpo(motivo='   '), 400),
            (self.cuerpo(propuesta={'rubro': ''}), 400),
            (self.cuerpo(tipo='PRECIO', propuesta={'precio_venta': '0'}), 400),
            (self.cuerpo(tipo='PRECIO', propuesta={'precio_venta': '-100'}), 400),
            (self.cuerpo(tipo='PRECIO', propuesta={'precio_venta': 'barato'}), 400),
            (self.cuerpo(producto=999999), 404),
        ]
        for cuerpo, esperado in casos:
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.pedir('proponer_sugerencia', cuerpo=cuerpo).status_code,
                                 esperado)
        self.assertEqual(SugerenciaCatalogo.objects.count(), 0)

    def test_un_producto_dado_de_baja_no_recibe_sugerencias(self):
        Producto.objects.filter(pk=self.leche.pk).update(borrado_logico=True)
        self.assertEqual(self.pedir('proponer_sugerencia', cuerpo=self.cuerpo()).status_code, 404)

    def test_sin_credencial_no_entra_y_el_get_no_existe(self):
        self.assertEqual(self.pedir('proponer_sugerencia', token=None,
                                    cuerpo=self.cuerpo()).status_code, 401)
        self.assertEqual(self.pedir('proponer_sugerencia').status_code, 405)

    def test_una_sesion_de_usuario_no_alcanza(self):
        f.entrar(self.client, self.admin)
        self.assertEqual(self.pedir('proponer_sugerencia', token=None,
                                    cuerpo=self.cuerpo()).status_code, 401)

    def test_las_sugerencias_aceptan_los_cinco_tipos(self):
        esperados = {'RUBRO', 'MARCA', 'RECETA', 'PRECIO', 'BAJA_INACTIVO'}
        self.assertEqual(set(TipoSugerencia.values), esperados)


@override_settings(CAFETO_N8N_TOKEN=TOKEN)
class DireccionPublicaTest(TestCase):
    """El enlace que sale por WhatsApp tiene que ABRIR en un celular.

    Los flujos nocturnos los dispara n8n desde Docker, contra
    ``http://web:8000``: ése es el ``Host`` del pedido. Armando el enlace
    con el host del pedido, el aviso de «Recetas y márgenes» llegaba como
    ``http://web:8000/stock/productos/412/editar/`` —un nombre que sólo
    existe dentro de la red de Docker— y no abría en ninguna parte.
    """

    def pedir(self, nombre, host, seguro=False, **parametros):
        url = reverse(f'api_servicio:{nombre}')
        return self.client.get(
            url, parametros, HTTP_AUTHORIZATION=f'Bearer {TOKEN}',
            HTTP_HOST=host, secure=seguro)

    def setUp(self):
        self.admin = f.usuario('30222190', 'Administrador')
        self.prov = f.proveedor()
        f.producto('Café con leche', tipo='ELABORADO', precio='2000',
                   costo='400', rubro_nombre='Cafetería')

    def una_url(self, respuesta):
        return respuesta.json()['productos'][0]['url']

    @override_settings(CAFETO_URL_PUBLICA='https://cafeto.ejemplo.com',
                       ALLOWED_HOSTS=['*'])
    def test_manda_la_url_publica_configurada(self):
        """Es la misma variable que usan el enlace de autorización a
        distancia y el de recuperación de clave."""
        url = self.una_url(self.pedir('productos_sin_receta', 'web:8000'))
        self.assertTrue(url.startswith('https://cafeto.ejemplo.com/stock/'), url)

    @override_settings(CAFETO_URL_PUBLICA='https://cafeto.ejemplo.com/',
                       ALLOWED_HOSTS=['*'])
    def test_la_barra_final_no_se_duplica(self):
        url = self.una_url(self.pedir('productos_sin_receta', 'web:8000'))
        self.assertNotIn('.com//', url)

    @override_settings(CAFETO_URL_PUBLICA='', ALLOWED_HOSTS=['*'])
    def test_sin_configurar_nada_no_devuelve_el_host_interno_de_docker(self):
        url = self.una_url(self.pedir('productos_sin_receta', 'web:8000'))
        self.assertNotIn('web:8000', url)
        self.assertTrue(url.startswith('http://localhost:8000/stock/'), url)

    @override_settings(CAFETO_URL_PUBLICA='',
                       ALLOWED_HOSTS=['cafeto.ejemplo.com', 'web'])
    def test_sin_configurar_nada_sirve_el_nombre_que_declara_el_sistema(self):
        """``DJANGO_ALLOWED_HOSTS`` ya dice con qué nombres se llega al
        sistema desde afuera: se usa el primero que no sea de Docker."""
        url = self.una_url(self.pedir('productos_sin_receta', 'web:8000'))
        self.assertTrue(url.startswith('http://cafeto.ejemplo.com/stock/'), url)

    @override_settings(CAFETO_URL_PUBLICA='', ALLOWED_HOSTS=['*'])
    def test_un_pedido_de_una_persona_sigue_usando_su_propio_host(self):
        """Lo que entra por el navegador o por el túnel no se toca.

        Sólo se descartan los nombres de servicio de Docker: un nombre de
        una sola palabra puede ser el NAS del local (``cafeto``), y ése es
        un enlace que sí abre.
        """
        for host, esperado in (('cafeto.ejemplo.com', 'https://cafeto.ejemplo.com/stock/'),
                               ('cafeto', 'https://cafeto/stock/'),
                               ('192.168.0.12:8000', 'https://192.168.0.12:8000/stock/')):
            url = self.una_url(self.pedir('productos_sin_receta', host, seguro=True))
            self.assertTrue(url.startswith(esperado), f'{host}: {url}')

    @override_settings(CAFETO_URL_PUBLICA='https://cafeto.ejemplo.com',
                       ALLOWED_HOSTS=['*'])
    def test_todas_las_direcciones_de_salida_salen_de_la_misma_funcion(self):
        """Los cuatro diagnósticos, la bandeja y la factura pre-cargada."""
        for nombre in ('productos_sin_receta', 'productos_margen_bajo',
                       'productos_conteo_rapido', 'productos_sin_clasificar'):
            datos = self.pedir(nombre, 'web:8000').json()
            for producto in datos.get('productos', []):
                self.assertTrue(
                    producto['url'].startswith('https://cafeto.ejemplo.com/'),
                    f'{nombre}: {producto["url"]}')

    def test_ningun_enlace_se_arma_con_el_host_del_pedido_a_mano(self):
        """``_base_publica`` es el único camino: un ``build_absolute_uri``
        suelto vuelve a traer el ``http://web:8000`` de Docker."""
        from pathlib import Path

        from api_servicio import views

        fuente = Path(views.__file__).read_text(encoding='utf-8')
        self.assertNotIn('build_absolute_uri', fuente)
