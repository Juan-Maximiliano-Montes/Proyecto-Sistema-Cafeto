"""El catálogo público: qué se publica, cómo se muestra y con qué precio.

    SeccionesTest     cada tipo en su sección y un insumo nunca, en ningún lado
    PaginacionTest    de a 20 mientras haya más, sin repetir ni perder nada
    BusquedaTest      sin tildes ni mayúsculas, por nombre, marca o rubro
    OfertasTest       la misma rebaja que las etiquetas de la góndola
    DestacadosTest    combos primero, ofertas de mayor a menor, nada agotado
    BeneficiosTest    sólo los vigentes hoy, con su categoría y sus puntos
"""
from api_movil.tests.base import D, BaseApi, BaseCatalogo
from fidelizacion.models import Beneficio, CategoriaFidelizacion
from nucleo.tests import fabrica as f
from stock import servicios
from stock.models import Lote, Producto, Rubro


# ===========================================================================
class SeccionesTest(BaseCatalogo):

    def publicados(self):
        """Todo lo que la app puede llegar a ver sin token."""
        ids = set()
        for seccion in ('CAFETERIA', 'MINISUPER'):
            for q in ('', 'grano', 'cafe'):
                ids |= set(self.catalogo(seccion, q=q))
        destacados = self.pedir('get', '/catalogo/destacados/').json()
        return ids | {p['id'] for p in destacados['resultados']}

    def test_un_insumo_nunca_se_publica(self):
        publicados = self.publicados()
        self.assertNotIn(self.grano.pk, publicados)
        self.assertIn(self.cafe.pk, publicados)     # el café que lo usa, sí

    def test_un_insumo_tampoco_suma_su_rubro_a_los_filtros(self):
        deposito = Rubro.objects.create(nombre_rubro='Depósito')
        f.producto('Azúcar', 'INSUMO', prov=self.prov, rubro_nombre='Depósito')
        for seccion in ('CAFETERIA', 'MINISUPER'):
            rubros = self.pedir('get', '/catalogo/rubros/', {'seccion': seccion}).json()
            self.assertNotIn(deposito.pk, [r['id'] for r in rubros['resultados']])

    def test_cada_tipo_en_su_seccion(self):
        cafeteria = self.catalogo('CAFETERIA').values()
        minisuper = self.catalogo('MINISUPER').values()
        self.assertEqual({p['tipo'] for p in cafeteria}, {'ELABORADO', 'COMBO'})
        self.assertEqual({p['tipo'] for p in minisuper}, {'REVENTA'})
        self.assertEqual({p['seccion'] for p in cafeteria}, {'CAFETERIA'})
        self.assertEqual({p['seccion'] for p in minisuper}, {'MINISUPER'})

    def test_lo_dado_de_baja_no_se_publica(self):
        Producto.objects.filter(pk=self.alfajor.pk).update(borrado_logico=True)
        self.assertNotIn(self.alfajor.pk, self.publicados())

    def test_la_seccion_es_obligatoria(self):
        for ruta in ('/catalogo/productos/', '/catalogo/rubros/'):
            for parametros in ({}, {'seccion': 'BAR'}):
                with self.subTest(ruta=ruta, parametros=parametros):
                    respuesta = self.pedir('get', ruta, parametros)
                    self.assertEqual(respuesta.status_code, 400)
                    self.assertEqual(respuesta.json(),
                                     {'detail': 'Elegí Cafetería o Minisuper.'})

    def test_los_rubros_de_cada_seccion(self):
        for seccion, rubro in (('CAFETERIA', self.cafeteria),
                               ('MINISUPER', self.minisuper)):
            respuesta = self.pedir('get', '/catalogo/rubros/', {'seccion': seccion})
            self.assertEqual(respuesta.json(), {
                'resultados': [{'id': rubro.pk, 'nombre': rubro.nombre_rubro}],
                'hay_mas': False})

    def test_como_se_ve_un_producto(self):
        gaseosa = self.catalogo('MINISUPER')[self.gaseosa.pk]
        self.assertEqual(gaseosa, {
            'id': self.gaseosa.pk, 'nombre': 'Gaseosa cola', 'descripcion': '',
            'presentacion': '500 ml', 'precio': 1000, 'precio_oferta': None,
            'porcentaje_oferta': None, 'seccion': 'MINISUPER', 'tipo': 'REVENTA',
            'rubro': {'id': self.minisuper.pk, 'nombre': 'Minisuper'},
            'marca': {'id': self.gaseosa.id_marca_id, 'nombre': 'Coca-Cola'},
            'imagen': None, 'icono': 'basket-outline', 'disponible': True})

    def test_la_marca_generica_va_como_null(self):
        self.assertIsNone(self.catalogo('MINISUPER')[self.yogur.pk]['marca'])

    def test_un_combo_dice_lo_que_trae(self):
        combo = self.catalogo('CAFETERIA')[self.combo.pk]
        self.assertEqual(combo['presentacion'], 'Combo')
        self.assertEqual(combo['descripcion'], 'Café con leche y Alfajor ×2')

    def test_un_combo_sin_componentes_en_stock_esta_agotado(self):
        medialuna = f.producto('Medialuna', prov=self.prov, precio='500')
        merienda = f.receta(
            f.producto('Merienda', 'COMBO', rubro_nombre='Cafetería', precio='3000'),
            (self.cafe, '1'), (medialuna, '1'))
        cafeteria = self.catalogo('CAFETERIA')
        self.assertFalse(cafeteria[merienda.pk]['disponible'])
        self.assertTrue(cafeteria[self.combo.pk]['disponible'])
        self.assertTrue(cafeteria[self.cafe.pk]['disponible'])  # sus insumos no frenan


# ===========================================================================
class PaginacionTest(BaseCatalogo):

    def test_de_a_20_mientras_hay_mas(self):
        for n in range(25):
            f.producto(f'Galletita {n:02d}', prov=self.prov, precio='500')
        total = Producto.objects.filter(tipo_producto='REVENTA',
                                        borrado_logico=False).count()
        vistos, pagina = [], 0
        while True:
            pagina += 1
            datos = self.pedir('get', '/catalogo/productos/',
                               {'seccion': 'MINISUPER', 'pagina': pagina}).json()
            self.assertLessEqual(len(datos['resultados']), 20)
            vistos += [p['id'] for p in datos['resultados']]
            if not datos['hay_mas']:
                break
        self.assertEqual(pagina, 2)
        self.assertEqual(len(vistos), total)
        self.assertEqual(len(set(vistos)), total)
        nombres = [Producto.objects.get(pk=pk).nombre_producto for pk in vistos[:3]]
        self.assertEqual(nombres, ['Alfajor', 'Galletita 00', 'Galletita 01'])


# ===========================================================================
class BusquedaTest(BaseCatalogo):

    def test_sin_tildes_ni_mayusculas_por_nombre_marca_o_rubro(self):
        self.assertEqual(set(self.catalogo('CAFETERIA', q='CAFE CON')), {self.cafe.pk})
        self.assertEqual(set(self.catalogo('MINISUPER', q='coca')), {self.gaseosa.pk})
        self.assertEqual(set(self.catalogo('MINISUPER', q='arcor')), {self.alfajor.pk})
        self.assertEqual(set(self.catalogo('MINISUPER', q='minisúper')),
                         set(self.catalogo('MINISUPER')))

    def test_filtra_por_rubro(self):
        self.assertEqual(set(self.catalogo('CAFETERIA', rubro=self.cafeteria.pk)),
                         {self.cafe.pk, self.combo.pk})
        self.assertEqual(self.catalogo('CAFETERIA', rubro=self.minisuper.pk), {})


# ===========================================================================
class OfertasTest(BaseCatalogo):

    def publicado(self):
        """El yogur tal como lo ve la app."""
        return self.catalogo('MINISUPER')[self.yogur.pk]

    def test_la_oferta_es_la_de_las_etiquetas_de_la_gondola(self):
        yogur = self.publicado()
        etiqueta = [o for o in servicios.en_oferta() if o[0].id_producto_id == self.yogur.pk]
        lote, porcentaje, precio = etiqueta[0]
        self.assertEqual((yogur['precio_oferta'], yogur['porcentaje_oferta']), (800, 20))
        self.assertEqual((D(yogur['precio_oferta']), D(yogur['porcentaje_oferta'])),
                         (precio, porcentaje))

    def test_sin_oferta_si_el_lote_que_sale_primero_esta_lejos(self):
        Lote.objects.filter(id_producto=self.yogur, fecha_vencimiento=f.en_dias(3)).update(
            fecha_vencimiento=f.en_dias(20))
        self.assertIsNone(self.publicado()['precio_oferta'])
        self.assertEqual(servicios.en_oferta(), [])

    def test_la_rebaja_del_producto_manda_sobre_la_del_rubro(self):
        Producto.objects.filter(pk=self.yogur.pk).update(
            dias_rebaja_vencimiento=10, porcentaje_rebaja_vencimiento=D('10'))
        yogur = self.publicado()
        self.assertEqual((yogur['precio_oferta'], yogur['porcentaje_oferta']), (900, 10))

    def test_un_lote_vencido_no_se_vende_ni_cuenta_para_la_oferta(self):
        Lote.objects.filter(id_producto=self.yogur, fecha_vencimiento=f.en_dias(3)).update(
            fecha_vencimiento=f.en_dias(-1))
        yogur = self.publicado()
        self.assertIsNone(yogur['precio_oferta'])       # sale el de 30 días
        self.assertTrue(yogur['disponible'])            # quedan 6 sin vencer
        Lote.objects.filter(id_producto=self.yogur).update(fecha_vencimiento=f.en_dias(-1))
        self.assertFalse(self.publicado()['disponible'])    # todo vencido: agotado


# ===========================================================================
class DestacadosTest(BaseCatalogo):

    def destacados(self):
        datos = self.pedir('get', '/catalogo/destacados/').json()
        self.assertFalse(datos['hay_mas'])
        return datos['resultados']

    def test_combos_primero_y_despues_ofertas_de_mayor_a_menor(self):
        flan = f.producto('Flan', prov=self.prov, precio='1500',
                          controla_vencimiento=True, dias_rebaja_vencimiento=7,
                          porcentaje_rebaja_vencimiento=D('10'))
        f.comprar(self.encargada, self.prov, (flan, 3, '700', [(f.en_dias(2), 3)]))
        self.assertEqual([(p['id'], p['motivo']) for p in self.destacados()], [
            (self.combo.pk, 'COMBO'), (self.yogur.pk, 'OFERTA'), (flan.pk, 'OFERTA')])

    def test_nada_agotado(self):
        Lote.objects.filter(id_producto=self.yogur).update(fecha_vencimiento=f.en_dias(-1))
        medialuna = f.producto('Medialuna', prov=self.prov, precio='500')
        f.receta(f.producto('Merienda', 'COMBO', rubro_nombre='Cafetería', precio='3000'),
                 (self.cafe, '1'), (medialuna, '1'))
        self.assertEqual([p['id'] for p in self.destacados()], [self.combo.pk])

    def test_no_mas_de_8(self):
        for n in range(9):
            f.receta(f.producto(f'Combo {n}', 'COMBO', rubro_nombre='Cafetería',
                                precio='3000'),
                     (self.cafe, '1'), (self.alfajor, '1'))
        destacados = self.destacados()
        self.assertEqual(len(destacados), 8)
        self.assertEqual({p['motivo'] for p in destacados}, {'COMBO'})


# ===========================================================================
class BeneficiosTest(BaseApi):

    def beneficio(self, categoria, descripcion, tipo, modo, porcentaje=None,
                  puntos=None, **extra):
        nuevo = Beneficio(
            id_categoria=CategoriaFidelizacion.objects.get(nombre_categoria=categoria),
            descripcion_beneficio=descripcion, tipo_beneficio=tipo,
            modo_entrega=modo, porcentaje_descuento=porcentaje,
            puntos_requeridos=puntos, **extra)
        nuevo.full_clean()
        nuevo.save()
        return nuevo

    def beneficios(self):
        datos = self.pedir('get', '/catalogo/beneficios/').json()
        self.assertFalse(datos['hay_mas'])
        return {b['id']: b for b in datos['resultados']}

    def test_solo_los_vigentes_hoy(self):
        vigente = self.beneficio('Bronze', 'Café con leche gratis', 'CANJEABLE',
                                 'ESPECIE', puntos=400)
        futuro = self.beneficio('Bronze', 'Medialunas x3', 'CANJEABLE', 'ESPECIE',
                                puntos=300, vigencia_desde=f.en_dias(5))
        vencido = self.beneficio('Silver', 'Torta del mes', 'CANJEABLE', 'ESPECIE',
                                 puntos=500)
        Beneficio.objects.filter(pk=vencido.pk).update(
            vigencia_desde=f.en_dias(-30), vigencia_hasta=f.en_dias(-1))
        baja = self.beneficio('Gold', 'Desayuno completo', 'CANJEABLE', 'ESPECIE',
                              puntos=900)
        Beneficio.objects.filter(pk=baja.pk).update(borrado_logico=True)

        beneficios = self.beneficios()
        self.assertEqual(set(beneficios), {vigente.pk})
        self.assertEqual(beneficios[vigente.pk], {
            'id': vigente.pk, 'titulo': 'Café con leche gratis',
            'detalle': 'Retirás en el mostrador', 'tipo': 'CANJEABLE',
            'puntos': 400, 'categoria': 'Bronze', 'icono': 'gift-outline'})

    def test_arriba_van_las_categorias_que_existan(self):
        """Hoy son cuatro, con Platinum: la app no depende de una lista fija."""
        datos = self.pedir('get', '/catalogo/beneficios/').json()
        self.assertEqual(datos['categorias'], [
            {'nombre': c.nombre_categoria, 'puntos_minimos': c.puntos_minimos}
            for c in CategoriaFidelizacion.objects.filter(borrado_logico=False)
            .order_by('puntos_minimos')])
        self.assertEqual([c['nombre'] for c in datos['categorias']][-1], 'Platinum')

    def test_un_descuento_automatico_no_pide_puntos(self):
        automatico = self.beneficio('Silver', '5% en cada compra', 'AUTOMATICO',
                                    'DESCUENTO', porcentaje=D('5'))
        datos = self.beneficios()[automatico.pk]
        self.assertEqual(datos['puntos'], 0)
        self.assertEqual(datos['categoria'], 'Silver')
        self.assertEqual(datos['detalle'], '5% de descuento: se aplica solo al pagar')
