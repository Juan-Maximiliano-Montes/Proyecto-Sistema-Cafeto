"""La bandeja de mejora continua: proponer, aplicar y descartar (Etapa 2).

Lo central es que aprobar una sugerencia NO es un atajo: el cambio pasa
por las mismas validaciones del modelo que si alguien lo editara a mano, y
cuando no puede (un rubro que no existe, un producto con stock, una marca
ya cargada por una persona) el producto queda intacto y la sugerencia
sigue esperando.
"""
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from nucleo.tests import fabrica as f
from stock import sugerencias as servicio
from stock.models import (
    MARCA_GENERICA,
    EstadoSugerencia,
    Marca,
    Producto,
    Rubro,
    SugerenciaCatalogo,
    TipoSugerencia,
)


class Base(TestCase):

    def setUp(self):
        self.admin = f.usuario('30333100', 'Administrador')
        self.prov = f.proveedor()
        self.leche = f.producto('Leche entera', prov=self.prov,
                                precio='1500', costo='1000')
        self.lacteos = Rubro.objects.create(nombre_rubro='Lácteos')

    def proponer(self, tipo, propuesta, producto=None, motivo='Porque sí, con razón'):
        return servicio.proponer(producto or self.leche, tipo, motivo, propuesta)


class ProponerTest(Base):

    def test_crea_y_es_idempotente_por_huella(self):
        primera, nueva = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        self.assertTrue(nueva)
        segunda, otra_vez = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        self.assertFalse(otra_vez)
        self.assertEqual(primera.pk, segunda.pk)
        self.assertEqual(SugerenciaCatalogo.objects.count(), 1)

    def test_una_propuesta_distinta_si_es_otra_sugerencia(self):
        self.proponer('RUBRO', {'rubro': 'Lácteos'})
        self.proponer('RUBRO', {'rubro': 'Almacén'})
        self.assertEqual(SugerenciaCatalogo.objects.count(), 2)

    def test_despues_de_resolverla_se_puede_volver_a_proponer(self):
        """Si la realidad volvió a cambiar, vale preguntar de nuevo."""
        primera, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        servicio.descartar(primera.pk, self.admin)
        segunda, nueva = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        self.assertTrue(nueva)
        self.assertNotEqual(primera.pk, segunda.pk)

    def test_las_propuestas_sin_forma_no_entran(self):
        casos = [
            ('RUBRO', {'rubro': '   '}),
            ('RUBRO', {}),
            ('MARCA', {'marca': ''}),
            ('PRECIO', {'precio_venta': '0'}),
            ('PRECIO', {'precio_venta': '-5'}),
            ('PRECIO', {'precio_venta': None}),
            ('PRECIO', {}),
            ('RECETA', {'insumos': 'café'}),
            ('INVENTADO', {}),
        ]
        for tipo, propuesta in casos:
            with self.subTest(tipo=tipo, propuesta=propuesta):
                with self.assertRaises(ValidationError):
                    self.proponer(tipo, propuesta)
        self.assertEqual(SugerenciaCatalogo.objects.count(), 0)

    def test_sin_motivo_no_hay_sugerencia(self):
        with self.assertRaises(ValidationError):
            self.proponer('RUBRO', {'rubro': 'Lácteos'}, motivo='   ')

    def test_un_producto_dado_de_baja_no_recibe_sugerencias(self):
        Producto.objects.filter(pk=self.leche.pk).update(borrado_logico=True)
        self.leche.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.proponer('RUBRO', {'rubro': 'Lácteos'})

    def test_la_base_tambien_frena_la_repetida(self):
        """Tercera capa: el UNIQUE parcial, aunque alguien escriba por fuera."""
        primera, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        with self.assertRaises(IntegrityError):
            SugerenciaCatalogo.objects.create(
                tipo_sugerencia=primera.tipo_sugerencia, id_producto=self.leche,
                motivo=primera.motivo, propuesta=primera.propuesta,
                huella=primera.huella)

    def test_pendientes_sale_ordenado_y_sin_productos_de_baja(self):
        otro = f.producto('Pan lactal', prov=self.prov)
        self.proponer('RUBRO', {'rubro': 'Lácteos'})
        self.proponer('MARCA', {'marca': 'La Serenísima'}, producto=otro)
        self.assertEqual(servicio.pendientes().count(), 2)
        Producto.objects.filter(pk=otro.pk).update(borrado_logico=True)
        self.assertEqual(servicio.pendientes().count(), 1)
        self.assertEqual(servicio.pendientes(tipo='MARCA').count(), 0)


class AceptarTest(Base):

    def test_rubro_se_aplica_de_verdad(self):
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'lacteos'})
        sugerencia, resultado = servicio.aceptar(sugerencia.pk, self.admin)
        self.assertEqual(Producto.objects.get(pk=self.leche.pk).id_rubro, self.lacteos)
        self.assertIn('Lácteos', resultado)
        self.assertEqual(sugerencia.estado_sugerencia, EstadoSugerencia.ACEPTADA)
        self.assertEqual(sugerencia.id_usuario_resolucion, self.admin)
        self.assertIsNotNone(sugerencia.fecha_resolucion)

    def test_un_rubro_que_no_existe_no_se_inventa(self):
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Perfumería Fina'})
        with self.assertRaises(ValidationError):
            servicio.aceptar(sugerencia.pk, self.admin)
        self.assertFalse(Rubro.objects.filter(nombre_rubro='Perfumería Fina').exists())
        sugerencia.refresh_from_db()
        self.assertTrue(sugerencia.pendiente)

    def test_un_rubro_dado_de_baja_no_sirve(self):
        Rubro.objects.filter(pk=self.lacteos.pk).update(borrado_logico=True)
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        with self.assertRaises(ValidationError):
            servicio.aceptar(sugerencia.pk, self.admin)

    def test_marca_usa_el_servicio_y_no_pisa_la_cargada_a_mano(self):
        generica = Marca.objects.get_or_create(nombre_marca=MARCA_GENERICA)[0]
        sin_marca = f.producto('Yogur suelto', prov=self.prov)
        Producto.objects.filter(pk=sin_marca.pk).update(id_marca=generica)
        sin_marca.refresh_from_db()
        sugerencia, _ = self.proponer('MARCA', {'marca': 'La Serenísima'},
                                      producto=sin_marca)
        servicio.aceptar(sugerencia.pk, self.admin)
        self.assertEqual(Producto.objects.get(pk=sin_marca.pk).id_marca.nombre_marca,
                         'La Serenísima')

        # La leche ya tiene marca cargada ("Sin marca", puesta por una
        # persona): una automatización no la pisa.
        otra, _ = self.proponer('MARCA', {'marca': 'Sancor'})
        with self.assertRaises(ValidationError):
            servicio.aceptar(otra.pk, self.admin)
        self.assertEqual(Producto.objects.get(pk=self.leche.pk).id_marca.nombre_marca,
                         'Sin marca')

    def test_precio_pasa_por_full_clean(self):
        sugerencia, _ = self.proponer('PRECIO', {'precio_venta': '2100.50'})
        sugerencia, resultado = servicio.aceptar(sugerencia.pk, self.admin)
        self.assertEqual(Producto.objects.get(pk=self.leche.pk).precio_venta,
                         D('2100.50'))
        self.assertIn('2100.50', resultado)

    def test_baja_respeta_los_dependientes(self):
        f.comprar(self.admin, self.prov, (self.leche, 5, '1000'))
        sugerencia, _ = self.proponer('BAJA_INACTIVO', {'dias_sin_movimiento': 400})
        with self.assertRaises(ValidationError) as caso:
            servicio.aceptar(sugerencia.pk, self.admin)
        self.assertIn('stock', ' '.join(caso.exception.messages))
        self.assertFalse(Producto.objects.get(pk=self.leche.pk).borrado_logico)
        sugerencia.refresh_from_db()
        self.assertTrue(sugerencia.pendiente)

    def test_baja_cuando_nada_lo_impide(self):
        sugerencia, _ = self.proponer('BAJA_INACTIVO', {})
        servicio.aceptar(sugerencia.pk, self.admin)
        self.assertTrue(Producto.objects.get(pk=self.leche.pk).borrado_logico)
        # Baja LÓGICA: la fila sigue estando (RN-8.1.1).
        self.assertTrue(Producto.objects.filter(pk=self.leche.pk).exists())

    def test_receta_solo_se_cierra_cuando_la_receta_ya_esta(self):
        cafe = f.producto('Café con leche', tipo='ELABORADO', precio='2000',
                          costo='400', rubro_nombre='Cafetería')
        sugerencia, _ = self.proponer('RECETA', {'insumos': ['Café', 'Leche']},
                                      producto=cafe)
        with self.assertRaises(ValidationError):
            servicio.aceptar(sugerencia.pk, self.admin)
        insumo = f.producto('Café en grano', tipo='INSUMO', costo='5000',
                            prov=self.prov, unidad='KG')
        f.receta(cafe, (insumo, '0.018'))
        sugerencia, resultado = servicio.aceptar(sugerencia.pk, self.admin)
        self.assertEqual(sugerencia.estado_sugerencia, EstadoSugerencia.ACEPTADA)
        self.assertIn('insumo', resultado)

    def test_una_sugerencia_resuelta_no_se_vuelve_a_resolver(self):
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        servicio.aceptar(sugerencia.pk, self.admin)
        with self.assertRaises(ValidationError):
            servicio.aceptar(sugerencia.pk, self.admin)
        with self.assertRaises(ValidationError):
            servicio.descartar(sugerencia.pk, self.admin)

    def test_una_sugerencia_que_no_existe_avisa(self):
        with self.assertRaises(ValidationError):
            servicio.aceptar(999999, self.admin)


class DescartarTest(Base):

    def test_descartar_no_toca_el_producto(self):
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        sugerencia = servicio.descartar(sugerencia.pk, self.admin)
        self.assertEqual(sugerencia.estado_sugerencia, EstadoSugerencia.DESCARTADA)
        self.assertEqual(sugerencia.id_usuario_resolucion, self.admin)
        self.assertEqual(Producto.objects.get(pk=self.leche.pk).id_rubro.nombre_rubro,
                         'Minisuper')
        self.assertEqual(servicio.pendientes().count(), 0)


class ConsistenciaEnBaseTest(Base):
    """Tercera capa: lo que la base rechaza aunque el ORM se saltee."""

    def test_pendiente_no_puede_tener_quien_la_resolvio(self):
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        with self.assertRaises(IntegrityError):
            SugerenciaCatalogo.objects.filter(pk=sugerencia.pk).update(
                id_usuario_resolucion=self.admin, fecha_resolucion=timezone.now())

    def test_resuelta_sin_quien_ni_cuando_no_entra(self):
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        with self.assertRaises(IntegrityError):
            SugerenciaCatalogo.objects.filter(pk=sugerencia.pk).update(
                estado_sugerencia=EstadoSugerencia.ACEPTADA)

    def test_el_motivo_vacio_lo_frena_la_base(self):
        with self.assertRaises(IntegrityError):
            SugerenciaCatalogo.objects.create(
                tipo_sugerencia='RUBRO', id_producto=self.leche, motivo='  ',
                propuesta={'rubro': 'Lácteos'}, huella='x' * 64)

    def test_un_estado_inventado_no_entra(self):
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        with self.assertRaises(IntegrityError):
            SugerenciaCatalogo.objects.filter(pk=sugerencia.pk).update(
                estado_sugerencia='INVENTADO')


class PantallaTest(Base):

    def test_la_bandeja_muestra_lo_de_hoy_y_lo_propuesto(self):
        self.proponer('RUBRO', {'rubro': 'Lácteos'},
                      motivo='Open Food Facts lo tiene en lácteos')
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(reverse('stock:sugerencias'))
        self.assertEqual(pantalla.status_code, 200)
        self.assertContains(pantalla, 'Leche entera')
        self.assertContains(pantalla, 'Minisuper')       # lo de hoy
        self.assertContains(pantalla, 'Lácteos')         # lo propuesto
        self.assertContains(pantalla, 'Open Food Facts lo tiene en lácteos')

    def test_sin_sugerencias_lo_dice_sin_romperse(self):
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(reverse('stock:sugerencias'))
        self.assertContains(pantalla, 'No hay sugerencias esperando')

    def test_el_filtro_por_tipo_y_sus_contadores(self):
        otro = f.producto('Pan lactal', prov=self.prov)
        self.proponer('RUBRO', {'rubro': 'Lácteos'})
        self.proponer('PRECIO', {'precio_venta': '2500'}, producto=otro)
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(reverse('stock:sugerencias'), {'tipo': 'PRECIO'})
        self.assertEqual([s.tipo_sugerencia for s in pantalla.context['filas']],
                         ['PRECIO'])
        self.assertEqual(pantalla.context['total'], 2)
        vistas = {v['clave']: v['cantidad'] for v in pantalla.context['vistas']}
        self.assertEqual(vistas, {'': 2, 'RUBRO': 1, 'PRECIO': 1})

    def test_un_tipo_inventado_no_rompe(self):
        self.proponer('RUBRO', {'rubro': 'Lácteos'})
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(reverse('stock:sugerencias'), {'tipo': 'HACKEO'})
        self.assertEqual(pantalla.status_code, 200)
        self.assertEqual(len(pantalla.context['filas']), 1)

    def test_aplicar_desde_la_pantalla_cambia_y_audita(self):
        from seguridad.models import LogAuditoria

        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        f.entrar(self.client, self.admin)
        respuesta = self.client.post(
            reverse('stock:aplicar_sugerencia', args=[sugerencia.pk]), follow=True)
        self.assertEqual(Producto.objects.get(pk=self.leche.pk).id_rubro, self.lacteos)
        self.assertContains(respuesta, 'Lácteos')
        asiento = LogAuditoria.objects.filter(tabla_afectada='sugerencias_catalogo').first()
        self.assertIsNotNone(asiento)
        self.assertIn('producto', asiento.registro_anterior)
        self.assertIn('sugerencia', asiento.registro_nuevo)
        self.assertEqual(asiento.id_usuario, self.admin)

    def test_lo_que_no_se_puede_aplicar_avisa_y_no_cambia_nada(self):
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Nada De Nada'})
        f.entrar(self.client, self.admin)
        respuesta = self.client.post(
            reverse('stock:aplicar_sugerencia', args=[sugerencia.pk]), follow=True)
        self.assertContains(respuesta, 'No hay un rubro vigente')
        sugerencia.refresh_from_db()
        self.assertTrue(sugerencia.pendiente)

    def test_descartar_desde_la_pantalla(self):
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        f.entrar(self.client, self.admin)
        self.client.post(reverse('stock:descartar_sugerencia', args=[sugerencia.pk]))
        sugerencia.refresh_from_db()
        self.assertEqual(sugerencia.estado_sugerencia, EstadoSugerencia.DESCARTADA)
        self.assertEqual(Producto.objects.get(pk=self.leche.pk).id_rubro.nombre_rubro,
                         'Minisuper')

    def test_el_get_no_resuelve_nada(self):
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        f.entrar(self.client, self.admin)
        for nombre in ('stock:aplicar_sugerencia', 'stock:descartar_sugerencia'):
            respuesta = self.client.get(reverse(nombre, args=[sugerencia.pk]))
            self.assertEqual(respuesta.status_code, 405, nombre)
        sugerencia.refresh_from_db()
        self.assertTrue(sugerencia.pendiente)

    def test_sin_el_permiso_no_entra_ni_resuelve(self):
        sugerencia, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        cajera = f.usuario('30333101', 'Operador')
        f.entrar(self.client, cajera)
        self.assertEqual(self.client.get(reverse('stock:sugerencias')).status_code, 302)
        self.client.post(reverse('stock:aplicar_sugerencia', args=[sugerencia.pk]))
        sugerencia.refresh_from_db()
        self.assertTrue(sugerencia.pendiente)
        self.assertEqual(Producto.objects.get(pk=self.leche.pk).id_rubro.nombre_rubro,
                         'Minisuper')

    def test_la_receta_ofrece_el_camino_a_productos(self):
        cafe = f.producto('Café con leche', tipo='ELABORADO', precio='2000',
                          costo='400', rubro_nombre='Cafetería')
        self.proponer('RECETA', {'insumos': ['Café', 'Leche']}, producto=cafe)
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(reverse('stock:sugerencias'))
        self.assertContains(pantalla, 'Ir a cargar la receta')
        self.assertContains(pantalla, 'busqueda=Caf')

    def test_la_pantalla_ofrece_marcar_varias_y_aplicarlas_juntas(self):
        otro = f.producto('Pan lactal', prov=self.prov)
        self.proponer('RUBRO', {'rubro': 'Lácteos'})
        self.proponer('PRECIO', {'precio_venta': '2500'}, producto=otro)
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(reverse('stock:sugerencias'))
        self.assertContains(pantalla, 'Aplicar seleccionadas')
        self.assertContains(pantalla, 'name="sugerencias"', count=2)
        self.assertContains(pantalla, reverse('stock:aplicar_sugerencias'))

    def test_la_receta_no_se_puede_marcar(self):
        """La receta la carga una persona: no hay nada que aplicar en masa."""
        cafe = f.producto('Café con leche', tipo='ELABORADO', precio='2000',
                          costo='400', rubro_nombre='Cafetería')
        self.proponer('RECETA', {'insumos': ['Café']}, producto=cafe)
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(reverse('stock:sugerencias'))
        self.assertNotContains(pantalla, 'name="sugerencias"')


class AplicarVariasTest(Base):
    """"Aplicar seleccionadas": cada una con su validación y su asiento."""

    def setUp(self):
        super().setUp()
        self.pan = f.producto('Pan lactal', prov=self.prov, precio='1000', costo='600')
        f.entrar(self.client, self.admin)
        self.url = reverse('stock:aplicar_sugerencias')

    def test_aplica_todas_las_marcadas_y_audita_cada_una(self):
        from seguridad.models import LogAuditoria

        rubro, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        precio, _ = self.proponer('PRECIO', {'precio_venta': '2500'}, producto=self.pan)
        respuesta = self.client.post(
            self.url, {'sugerencias': [rubro.pk, precio.pk]}, follow=True)
        self.assertEqual(Producto.objects.get(pk=self.leche.pk).id_rubro, self.lacteos)
        self.assertEqual(Producto.objects.get(pk=self.pan.pk).precio_venta, D('2500'))
        self.assertContains(respuesta, '2 sugerencias aplicadas')
        for una in (rubro, precio):
            una.refresh_from_db()
            self.assertEqual(una.estado_sugerencia, EstadoSugerencia.ACEPTADA)
            self.assertEqual(una.id_usuario_resolucion, self.admin)
        asientos = LogAuditoria.objects.filter(
            tabla_afectada='sugerencias_catalogo',
            operacion_realizada='APLICACION DE SUGERENCIA DE CATALOGO')
        self.assertEqual(asientos.count(), 2)
        for asiento in asientos:
            self.assertIn('producto', asiento.registro_nuevo)
            self.assertEqual(asiento.id_usuario, self.admin)

    def test_la_que_falla_no_se_lleva_a_las_demas(self):
        """Lo que hace segura la aplicación en masa: una sugerencia imposible
        se informa y sigue pendiente; las otras entran igual."""
        imposible, _ = self.proponer('RUBRO', {'rubro': 'Nada De Nada'})
        buena, _ = self.proponer('PRECIO', {'precio_venta': '2500'}, producto=self.pan)
        respuesta = self.client.post(
            self.url, {'sugerencias': [imposible.pk, buena.pk]}, follow=True)
        self.assertContains(respuesta, 'No hay un rubro vigente')
        self.assertContains(respuesta, '1 sugerencia aplicada')
        imposible.refresh_from_db()
        self.assertTrue(imposible.pendiente)
        buena.refresh_from_db()
        self.assertEqual(buena.estado_sugerencia, EstadoSugerencia.ACEPTADA)
        self.assertEqual(Producto.objects.get(pk=self.pan.pk).precio_venta, D('2500'))
        self.assertEqual(Producto.objects.get(pk=self.leche.pk).id_rubro.nombre_rubro,
                         'Minisuper')

    def test_sin_marcar_nada_no_hace_nada(self):
        rubro, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        respuesta = self.client.post(self.url, {}, follow=True)
        self.assertContains(respuesta, 'No marcaste ninguna')
        rubro.refresh_from_db()
        self.assertTrue(rubro.pendiente)

    def test_una_basura_en_el_formulario_no_rompe(self):
        rubro, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        respuesta = self.client.post(
            self.url, {'sugerencias': ['x', '', '99999', str(rubro.pk), str(rubro.pk)]},
            follow=True)
        self.assertEqual(respuesta.status_code, 200)
        rubro.refresh_from_db()
        self.assertEqual(rubro.estado_sugerencia, EstadoSugerencia.ACEPTADA)
        # El id repetido se aplica una sola vez; el que no existe se informa.
        self.assertContains(respuesta, '1 sugerencia aplicada')
        self.assertContains(respuesta, 'Esa sugerencia no existe')

    def test_mas_de_cien_no_entran_de_una(self):
        from stock.views.sugerencias import LIMITE_MASIVO

        rubro, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        respuesta = self.client.post(
            self.url, {'sugerencias': [str(n) for n in range(1, LIMITE_MASIVO + 2)]},
            follow=True)
        self.assertContains(respuesta, f'De a {LIMITE_MASIVO} como máximo')
        rubro.refresh_from_db()
        self.assertTrue(rubro.pendiente)

    def test_el_get_no_aplica_nada(self):
        rubro, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        self.assertEqual(self.client.get(self.url).status_code, 405)
        rubro.refresh_from_db()
        self.assertTrue(rubro.pendiente)

    def test_sin_el_permiso_no_aplica_nada(self):
        rubro, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        f.entrar(self.client, f.usuario('30333103', 'Operador'))
        self.client.post(self.url, {'sugerencias': [rubro.pk]})
        rubro.refresh_from_db()
        self.assertTrue(rubro.pendiente)
        self.assertEqual(Producto.objects.get(pk=self.leche.pk).id_rubro.nombre_rubro,
                         'Minisuper')

    def test_vuelve_a_la_pestana_desde_la_que_se_aplico(self):
        rubro, _ = self.proponer('RUBRO', {'rubro': 'Lácteos'})
        respuesta = self.client.post(self.url, {'sugerencias': [rubro.pk], 'tipo': 'PRECIO'})
        self.assertEqual(respuesta['Location'],
                         reverse('stock:sugerencias') + '?tipo=PRECIO')


class MenuTest(Base):

    def test_el_menu_la_ofrece_a_quien_edita_productos(self):
        from seguridad import menu

        items = [i['titulo'] for grupo in menu.armar(self.admin) for i in grupo['items']]
        self.assertIn('Sugerencias de mejora', items)
        cajera = f.usuario('30333102', 'Operador')
        items = [i['titulo'] for grupo in menu.armar(cajera) for i in grupo['items']]
        self.assertNotIn('Sugerencias de mejora', items)
