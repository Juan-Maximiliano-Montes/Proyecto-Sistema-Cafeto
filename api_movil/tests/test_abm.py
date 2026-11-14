"""El ABM liviano de marcas y rubros: las reglas del modelo, desde el celular.

    MarcasTest     listar de a 20, alta, duplicados disfrazados, errores en
                   su campo, modificación sellada y auditada, baja lógica
    RubrosTest     lo mismo con "activo", y la baja frenada por productos
"""
from api_movil.tests.base import BaseApi
from nucleo.tests import fabrica as f
from seguridad.models import LogAuditoria
from stock.models import Marca, Rubro


# ===========================================================================
class MarcasTest(BaseApi):

    def crear(self, datos, como=None):
        return self.pedir('post', '/marcas/', datos, como=como or self.admin)

    def test_lista_de_a_20_mientras_hay_mas(self):
        for n in range(25):
            Marca.objects.create(nombre_marca=f'Marca {n:02d}')
        vigentes = Marca.objects.filter(borrado_logico=False).count()
        primera = self.pedir('get', '/marcas/', {'pagina': 1}, como=self.encargada).json()
        segunda = self.pedir('get', '/marcas/', {'pagina': 2}, como=self.encargada).json()
        self.assertEqual(len(primera['resultados']), 20)
        self.assertTrue(primera['hay_mas'])
        self.assertEqual(len(segunda['resultados']), vigentes - 20)
        self.assertFalse(segunda['hay_mas'])
        ids = [m['id'] for m in primera['resultados'] + segunda['resultados']]
        self.assertEqual(len(set(ids)), vigentes)          # nada repetido
        self.assertEqual([m['nombre'] for m in primera['resultados'][:2]],
                         ['Marca 00', 'Marca 01'])         # por nombre

    def test_una_pagina_rara_no_rompe(self):
        self.assertEqual(self.pedir('get', '/marcas/', {'pagina': 'x'},
                                    como=self.encargada).status_code, 200)
        lejos = self.pedir('get', '/marcas/', {'pagina': 99999999},
                           como=self.encargada).json()
        self.assertEqual(lejos, {'resultados': [], 'hay_mas': False})

    def test_busca_sin_tildes_ni_mayusculas_y_solo_vigentes(self):
        Marca.objects.create(nombre_marca='Café Tío Pancho')
        Marca.objects.create(nombre_marca='Tiorita', borrado_logico=True)
        resultados = self.pedir('get', '/marcas/', {'q': 'TIO'},
                                como=self.encargada).json()['resultados']
        self.assertEqual([m['nombre'] for m in resultados], ['Café Tío Pancho'])

    def test_alta_limpia_el_texto_y_queda_en_el_registro(self):
        respuesta = self.crear({'nombre': '  La   Morenita ',
                                'descripcion': 'Café y yerba'})
        self.assertEqual(respuesta.status_code, 201)
        datos = respuesta.json()
        self.assertEqual(datos['nombre'], 'La Morenita')
        self.assertEqual(datos['descripcion'], 'Café y yerba')
        marca = Marca.objects.get(pk=datos['id'])
        asiento = LogAuditoria.objects.get(operacion_realizada='ALTA DE MARCA')
        self.assertEqual(asiento.id_usuario, self.admin)
        self.assertEqual((asiento.tabla_afectada, asiento.id_registro),
                         ('marcas', marca.pk))
        self.assertEqual(asiento.registro_nuevo['nombre_marca'], 'La Morenita')
        self.assertIn('app móvil', asiento.detalle_operacion)

    def test_un_duplicado_disfrazado_se_rechaza_en_el_nombre(self):
        self.crear({'nombre': 'Coca-Cola'})
        respuesta = self.crear({'nombre': 'coca cola'})
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(list(respuesta.json()['errores']), ['nombre'])
        self.assertIn('Ya existe una marca', respuesta.json()['errores']['nombre'][0])
        self.assertEqual(Marca.objects.filter(nombre_marca__icontains='coca').count(), 1)

    def test_sin_nombre_un_solo_aviso_y_claro(self):
        for datos in ({}, {'nombre': None}, {'nombre': '   '}):
            with self.subTest(datos=datos):
                respuesta = self.crear(datos)
                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json(), {'errores': {
                    'nombre': ['El nombre de la marca es obligatorio.']}})

    def test_el_error_de_la_descripcion_va_en_la_descripcion(self):
        respuesta = self.crear({'nombre': 'Arcor', 'descripcion': 'Golosinas <b>'})
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(list(respuesta.json()['errores']), ['descripcion'])

    def test_un_nombre_que_no_es_texto(self):
        respuesta = self.crear({'nombre': 123})
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json(), {'errores': {'nombre': ['Tiene que ser un texto.']}})

    def test_un_cuerpo_que_no_es_json(self):
        respuesta = self.crear('nombre=Arcor')
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json(), {'detail': 'El pedido no trae datos válidos.'})

    def test_modificar_sella_quien_fue_y_queda_en_el_registro(self):
        marca = Marca.objects.create(nombre_marca='Arcor')
        respuesta = self.pedir('patch', f'/marcas/{marca.pk}/',
                               {'descripcion': 'Golosinas'}, como=self.encargada)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json(), {'id': marca.pk, 'nombre': 'Arcor',
                                            'descripcion': 'Golosinas'})
        marca.refresh_from_db()
        self.assertEqual(marca.usuario_modificacion, self.encargada)
        asiento = LogAuditoria.objects.get(operacion_realizada='MODIFICACION DE MARCA')
        self.assertIsNone(asiento.registro_anterior['descripcion_marca'])
        self.assertEqual(asiento.registro_nuevo['descripcion_marca'], 'Golosinas')

    def test_modificar_sin_cambios_no_guarda_ni_ensucia_el_registro(self):
        marca = Marca.objects.create(nombre_marca='Arcor')
        antes = marca.fecha_modificacion
        respuesta = self.pedir('patch', f'/marcas/{marca.pk}/', {'nombre': ' Arcor '},
                               como=self.encargada)
        self.assertEqual(respuesta.status_code, 200)
        marca.refresh_from_db()
        self.assertEqual(marca.fecha_modificacion, antes)
        self.assertFalse(LogAuditoria.objects.filter(
            operacion_realizada='MODIFICACION DE MARCA').exists())

    def test_modificar_a_un_nombre_que_ya_existe(self):
        Marca.objects.create(nombre_marca='Arcor')
        otra = Marca.objects.create(nombre_marca='Bagley')
        respuesta = self.pedir('patch', f'/marcas/{otra.pk}/', {'nombre': 'ARCOR'},
                               como=self.encargada)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('Ya existe una marca', respuesta.json()['errores']['nombre'][0])
        otra.refresh_from_db()
        self.assertEqual(otra.nombre_marca, 'Bagley')

    def test_lo_que_no_existe_o_esta_dado_de_baja_es_404(self):
        baja = Marca.objects.create(nombre_marca='Vieja', borrado_logico=True)
        for pk in (baja.pk, 999999):
            for metodo in ('patch', 'delete'):
                with self.subTest(pk=pk, metodo=metodo):
                    respuesta = self.pedir(metodo, f'/marcas/{pk}/', {'nombre': 'Nueva'},
                                           como=self.admin)
                    self.assertEqual(respuesta.status_code, 404)
                    self.assertEqual(respuesta.json(), {'detail': 'Esa marca ya no existe.'})

    def test_la_baja_es_logica_y_queda_en_el_registro(self):
        marca = Marca.objects.create(nombre_marca='Arcor')
        respuesta = self.pedir('delete', f'/marcas/{marca.pk}/', como=self.admin)
        self.assertEqual(respuesta.status_code, 204)
        marca.refresh_from_db()
        self.assertTrue(marca.borrado_logico)              # la fila sigue
        asiento = LogAuditoria.objects.get(operacion_realizada='BAJA DE MARCA')
        self.assertFalse(asiento.registro_anterior['borrado_logico'])
        self.assertTrue(asiento.registro_nuevo['borrado_logico'])
        listado = self.pedir('get', '/marcas/', como=self.admin).json()['resultados']
        self.assertNotIn(marca.pk, [m['id'] for m in listado])

    def test_con_productos_vigentes_no_se_da_de_baja(self):
        marca = Marca.objects.create(nombre_marca='Arcor')
        f.producto('Alfajor', prov=f.proveedor(), marca_nombre='Arcor')
        respuesta = self.pedir('delete', f'/marcas/{marca.pk}/', como=self.admin)
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(respuesta.json(), {'detail': (
            'No se puede dar de baja "Arcor" porque tiene 1 producto(s) '
            'vigente(s); dalos de baja o cambialos de marca primero.')})
        marca.refresh_from_db()
        self.assertFalse(marca.borrado_logico)


# ===========================================================================
class RubrosTest(BaseApi):

    def test_lista_con_activo(self):
        resultados = self.pedir('get', '/rubros/', como=self.encargada).json()['resultados']
        nombres = {r['nombre'] for r in resultados}
        self.assertTrue({'Cafetería', 'Minisuper'} <= nombres)
        self.assertEqual(set(resultados[0]), {'id', 'nombre', 'descripcion', 'activo'})
        self.assertTrue(all(r['activo'] is True for r in resultados))

    def test_alta_que_no_se_ofrece_en_productos(self):
        respuesta = self.pedir('post', '/rubros/', {
            'nombre': 'Lácteos', 'descripcion': 'Leches, yogures y quesos',
            'activo': False}, como=self.admin)
        self.assertEqual(respuesta.status_code, 201)
        self.assertIs(respuesta.json()['activo'], False)
        self.assertFalse(Rubro.objects.get(pk=respuesta.json()['id']).activo)

    def test_activo_tiene_que_ser_si_o_no(self):
        rubro = f.rubro('Minisuper')
        for valor in ('si', 1, None):
            with self.subTest(valor=valor):
                respuesta = self.pedir('patch', f'/rubros/{rubro.pk}/', {'activo': valor},
                                       como=self.admin)
                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json(), {'errores': {
                    'activo': ['Indicá si se ofrece o no.']}})

    def test_activo_se_cambia_y_la_rebaja_no_se_toca_desde_el_celular(self):
        rubro = f.rubro('Minisuper')
        respuesta = self.pedir('patch', f'/rubros/{rubro.pk}/', {
            'activo': False, 'dias_rebaja_vencimiento': 3,
            'porcentaje_rebaja_vencimiento': 50}, como=self.admin)
        self.assertEqual(respuesta.status_code, 200)
        rubro.refresh_from_db()
        self.assertFalse(rubro.activo)
        self.assertIsNone(rubro.dias_rebaja_vencimiento)

    def test_un_nombre_repetido(self):
        respuesta = self.pedir('post', '/rubros/', {'nombre': 'CAFETERIA'}, como=self.admin)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('Ya existe un rubro', respuesta.json()['errores']['nombre'][0])

    def test_con_productos_vigentes_no_se_da_de_baja(self):
        f.producto('Gaseosa', prov=f.proveedor())           # rubro Minisuper
        rubro = f.rubro('Minisuper')
        respuesta = self.pedir('delete', f'/rubros/{rubro.pk}/', como=self.admin)
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn('1 producto(s) vigente(s)', respuesta.json()['detail'])

    def test_un_rubro_sin_productos_se_da_de_baja(self):
        alta = self.pedir('post', '/rubros/', {'nombre': 'Regalería'}, como=self.admin)
        respuesta = self.pedir('delete', f'/rubros/{alta.json()["id"]}/', como=self.admin)
        self.assertEqual(respuesta.status_code, 204)
        self.assertTrue(Rubro.objects.get(pk=alta.json()['id']).borrado_logico)

    def test_permisos_de_rubros(self):
        self.assertEqual(self.pedir('get', '/rubros/', como=self.operador).status_code, 403)
        rubro = f.rubro('Minisuper')
        self.assertEqual(self.pedir('delete', f'/rubros/{rubro.pk}/',
                                    como=self.encargada).status_code, 403)
