"""Nombres duplicados: se rechazan y el aviso sale EN EL CAMPO.

Dos cosas que QA marcó y que acá quedan cubiertas.

1. **La puntuación no distinguía.** ``f_clave_normalizada`` bajaba a
   minúsculas, sacaba los acentos y colapsaba los espacios, pero dejaba
   los signos::

       'Coca Cola'  ->  'coca cola'
       'Coca-cola'  ->  'coca-cola'    <-- otra clave: ENTRABAN LAS DOS

   Ahora todo lo que no es letra ni número es separador, así que las dos
   dan ``'coca cola'`` y la segunda se rechaza.

2. **El aviso no salía en el campo.** El duplicado se interceptaba en la
   vista, que respondía con un redirect más un mensaje suelto: el usuario
   veía el aviso arriba, desenganchado del control, y el formulario volvía
   vacío. Ahora lo levanta el ``clean()`` del modelo atado al campo, así
   que Django lo dibuja al lado de lo que se tipeó y el formulario vuelve
   con los datos puestos.

Hasta dónde llega
-----------------
La clave convierte la puntuación en un espacio; NO la borra. Entonces
``'ARCOR S.A'`` ("arcor s a") sigue siendo distinto de ``'Arcor SA'``
("arcor sa"). Borrarla del todo juntaría esos dos, pero separaría
``'Coca-Cola'`` ("cocacola") de ``'Coca Cola'`` ("coca cola"), que es el
caso que QA pidió resolver. No hay una regla que agarre los dos, y entre
las dos esta es la que cubre lo pedido sin rechazar altas legítimas.
"""
from decimal import Decimal

from django.test import TestCase

from cafeto.validaciones import clave_normalizada
from maestros.forms import (CajaRegistradoraForm, CategoriaFidelizacionForm,
                            MarcaForm, MedioPagoForm, ProductoForm, RubroForm,
                            TipoEgresoForm)
from maestros.models import Marca, Producto, Rubro, UnidadMedida
from proveedores.models import Proveedor

CUIT_D = '30-71234567-1'


class ClaveNormalizadaTest(TestCase):
    """La función de Python, que tiene que dar lo mismo que la de la base."""

    def test_las_variantes_de_un_nombre_caen_en_la_misma_clave(self):
        esperada = 'coca cola'
        for texto in ('Coca Cola', 'coca cola', 'COCA COLA', 'COCA  COLA',
                      'Coca-Cola', 'Coca-cola', 'Coca.Cola', 'Coca/Cola',
                      'Cocá Colá', '  Coca   Cola  ', '(Coca Cola)'):
            with self.subTest(texto=texto):
                self.assertEqual(clave_normalizada(texto), esperada)

    def test_un_nombre_mas_corto_NO_es_duplicado(self):
        """'coca' y 'coca cola' son nombres distintos, y así se quedan."""
        self.assertNotEqual(clave_normalizada('coca'),
                            clave_normalizada('Coca Cola'))

    def test_la_eñe_y_los_acentos_se_planchan_como_en_la_base(self):
        self.assertEqual(clave_normalizada('Ñandú'), 'nandu')
        self.assertEqual(clave_normalizada('Café'), 'cafe')

    def test_sin_texto_no_hay_clave(self):
        self.assertIsNone(clave_normalizada(None))


class DuplicadoEnElCampoTest(TestCase):
    """El error tiene que llegar al formulario atado al campo."""

    def setUp(self):
        form = MarcaForm({'nombre_marca': 'Coca Cola'})
        self.assertTrue(form.is_valid(), form.errors)
        self.existente = form.save()

    def test_todas_las_variantes_se_rechazan_en_el_campo(self):
        """Dos capas lo pueden cortar, y las dos dejan el aviso en el campo.

        El ``clean_nombre_marca()`` del formulario agarra las variantes de
        mayúsculas con ``__iexact``; el ``clean()`` del modelo agarra ésas
        y además las de puntuación y acentos, por clave normalizada. Cuál
        de las dos conteste primero no importa: lo que esta prueba exige
        es que el alta se rechace y que el usuario lo vea EN el campo.
        """
        for nombre in ('coca cola', 'COCA COLA', 'Coca-cola', 'Coca.Cola',
                       'Cocá Cola', '  Coca   Cola  ', 'Coca-Cola'):
            with self.subTest(nombre=nombre):
                form = MarcaForm({'nombre_marca': nombre})

                self.assertFalse(form.is_valid())
                # Lo que pidió QA: visible, y en el campo.
                self.assertIn('nombre_marca', form.errors)
                self.assertRegex(
                    form.errors['nombre_marca'][0],
                    'ya se encuentra registrado|Ya existe una marca')

    def test_la_variante_de_mayusculas_la_corta_el_formulario(self):
        """El ``__iexact`` del formulario, con su mensaje propio."""
        form = MarcaForm({'nombre_marca': 'COCA COLA'})
        self.assertFalse(form.is_valid())
        self.assertIn('Este nombre ya se encuentra registrado',
                      form.errors['nombre_marca'][0])

    def test_la_variante_de_puntuacion_la_corta_el_modelo(self):
        """Lo que ``__iexact`` no ve, lo ve la clave normalizada."""
        form = MarcaForm({'nombre_marca': 'Coca-cola'})
        self.assertFalse(form.is_valid())
        self.assertIn('Ya existe una marca con este nombre',
                      form.errors['nombre_marca'][0])

    def test_el_formulario_vuelve_con_lo_que_se_tipeo(self):
        """Antes el redirect lo borraba y había que cargar todo de nuevo."""
        form = MarcaForm({'nombre_marca': 'coca cola',
                          'descripcion_marca': 'Gaseosas'})
        self.assertFalse(form.is_valid())
        self.assertIn('value="coca cola"', str(form['nombre_marca']))
        self.assertIn('value="Gaseosas"', str(form['descripcion_marca']))

    def test_un_nombre_distinto_entra(self):
        for nombre in ('Coca', 'Pepsi Cola', 'Coca Cola Zero'):
            with self.subTest(nombre=nombre):
                self.assertTrue(MarcaForm({'nombre_marca': nombre}).is_valid())

    def test_editar_la_propia_no_choca_consigo_misma(self):
        form = MarcaForm({'nombre_marca': 'Coca Cola'},
                         instance=self.existente)
        self.assertTrue(form.is_valid(), form.errors)

    def test_renombrarla_a_una_variante_de_si_misma_tampoco(self):
        form = MarcaForm({'nombre_marca': 'coca-cola'},
                         instance=self.existente)
        self.assertTrue(form.is_valid(), form.errors)

    def test_si_la_que_choca_esta_de_baja_manda_a_reactivar(self):
        self.existente.borrado_logico = True
        self.existente.save(update_fields=['borrado_logico'])

        form = MarcaForm({'nombre_marca': 'Coca-cola'})
        self.assertFalse(form.is_valid())
        self.assertIn('reactivalo', form.errors['nombre_marca'][0])


class TodasLasMaestrasTest(TestCase):
    """La regla vale para todas, no sólo para las tres del reporte."""

    CASOS = [
        (RubroForm, 'nombre_rubro', 'Bebidas Frías', 'bebidas-frias'),
        # Ojo al elegir nombres: no pueden chocar con los de la semilla
        # (Cafetería, Minisuper, Gastos varios, Efectivo, ...), porque el
        # choque sería contra ésos y no contra el que crea la prueba.
        (MarcaForm, 'nombre_marca', 'La Morenita', 'la-morenita'),
        (TipoEgresoForm, 'nombre_tipo_egreso', 'Fletes Especiales',
         'fletes-especiales'),
        (CajaRegistradoraForm, 'nombre_caja_registradora', 'Caja Norte',
         'caja-norte'),
    ]

    def test_ninguna_admite_la_variante_con_puntuacion(self):
        for Formulario, campo, original, variante in self.CASOS:
            with self.subTest(modelo=Formulario.__name__):
                primero = Formulario({campo: original})
                self.assertTrue(primero.is_valid(), primero.errors)
                primero.save()

                segundo = Formulario({campo: variante})
                self.assertFalse(segundo.is_valid())
                self.assertIn(campo, segundo.errors)

    def test_el_medio_de_pago_tambien(self):
        primero = MedioPagoForm({'nombre_medio_pago': 'Tarjeta Débito',
                                 'recargo_porcentaje': '0'})
        self.assertTrue(primero.is_valid(), primero.errors)
        primero.save()

        segundo = MedioPagoForm({'nombre_medio_pago': 'tarjeta-debito',
                                 'recargo_porcentaje': '0'})
        self.assertFalse(segundo.is_valid())
        self.assertIn('nombre_medio_pago', segundo.errors)

    def test_la_categoria_tambien(self):
        primero = CategoriaFidelizacionForm({
            'nombre_categoria': 'Oro Premium', 'puntos_minimos': '9100',
            'importe_por_punto': '70'})
        self.assertTrue(primero.is_valid(), primero.errors)
        primero.save()

        segundo = CategoriaFidelizacionForm({
            'nombre_categoria': 'oro-premium', 'puntos_minimos': '9200',
            'importe_por_punto': '70'})
        self.assertFalse(segundo.is_valid())
        self.assertIn('nombre_categoria', segundo.errors)


class IdentidadDelProductoTest(TestCase):
    """Un producto es único por la TERNA, no por el nombre solo (DD-55)."""

    def setUp(self):
        self.rubro = Rubro.objects.create(nombre_rubro='Cafetería D')
        self.marca = Marca.objects.create(nombre_marca='La Morenita D')
        self.otra_marca = Marca.objects.create(nombre_marca='Otra Marca D')
        self.proveedor = Proveedor.objects.create(
            nombre_proveedor='La Salteña D', cuit_proveedor=CUIT_D)

    def _datos(self, **extra):
        base = {'nombre_producto': 'Café Molido',
                'id_rubro': str(self.rubro.pk),
                'id_marca': str(self.marca.pk),
                'id_proveedor': str(self.proveedor.pk),
                'contenido': '500',
                'unidad_medida': UnidadMedida.G,
                'precio_venta': '1000',
                'precio_costo': '500',
                'stock_minimo': '1'}
        base.update(extra)
        return base

    def test_la_terna_repetida_se_rechaza_en_el_campo(self):
        primero = ProductoForm(self._datos())
        self.assertTrue(primero.is_valid(), primero.errors)
        primero.save()

        for nombre in ('cafe molido', 'CAFÉ MOLIDO', 'Café-Molido',
                       'Cafe.Molido'):
            with self.subTest(nombre=nombre):
                segundo = ProductoForm(self._datos(nombre_producto=nombre))
                self.assertFalse(segundo.is_valid())
                self.assertIn('nombre_producto', segundo.errors)

    def test_el_mismo_nombre_en_OTRA_marca_entra(self):
        """Lo que no se puede romper: dos marcas venden "Café Molido"."""
        primero = ProductoForm(self._datos())
        self.assertTrue(primero.is_valid(), primero.errors)
        primero.save()

        segundo = ProductoForm(self._datos(id_marca=str(self.otra_marca.pk)))
        self.assertTrue(segundo.is_valid(), segundo.errors)

    def test_el_mismo_nombre_en_OTRA_presentacion_entra(self):
        primero = ProductoForm(self._datos())
        self.assertTrue(primero.is_valid(), primero.errors)
        primero.save()

        segundo = ProductoForm(self._datos(contenido='1000'))
        self.assertTrue(segundo.is_valid(), segundo.errors)


# ===========================================================================
#  EL HEADER NO SE ROMPE: los comentarios de template no salen al HTML
# ===========================================================================
class TemplatesSinFugasTest(TestCase):
    """``{# ... #}`` es de UNA línea; multilínea se imprime tal cual.

    Es el fallo que trabó el header: un ``{# ... #}`` de nueve líneas
    dentro de ``base.html`` se renderizaba literal, y el texto suelto
    dentro de ``<head>`` le hace cerrar la cabeza al navegador antes de
    tiempo. Los comentarios largos van en ``{% comment %}``.
    """

    def test_ningun_template_tiene_un_comentario_multilinea(self):
        import pathlib
        import re

        patron = re.compile(r'\{#(.*?)#\}', re.DOTALL)
        culpables = []
        for ruta in pathlib.Path('.').rglob('*.html'):
            if '.git' in ruta.parts:
                continue
            texto = ruta.read_text(encoding='utf-8')
            for encontrado in patron.finditer(texto):
                if '\n' in encontrado.group(1):
                    linea = texto[:encontrado.start()].count('\n') + 1
                    culpables.append('%s:%d' % (ruta, linea))

        self.assertEqual(
            culpables, [],
            'comentarios {# #} multilínea: se imprimen en el HTML. '
            'Usá {% comment %}. Están en: ' + ', '.join(culpables))
