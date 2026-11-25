"""El Archivo histórico se recorre de a páginas, sin perder los filtros.

Antes la pantalla cortaba en las primeras 200 filas. Ahora se ve de a 25
(o 10, 50, 100, con el mismo selector que la Actividad), los enlaces de
página llevan el libro y el período elegidos, y las descargas siguen
bajando el período completo: la paginación es sólo de la pantalla.
"""
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from compras.models import Egreso, TipoEgreso
from nucleo.tests import fabrica as f
from nucleo.tests.test_archivo_legal import Base

CANTIDAD = 30


class PaginacionDelArchivoTest(Base):

    def setUp(self):
        super().setUp()
        self.hoy = timezone.localdate()
        servicios = TipoEgreso.objects.get(nombre_tipo_egreso='Servicios')
        for numero in range(CANTIDAD):
            Egreso.objects.create(id_tipo_egreso=servicios, destinatario=f'Proveedor {numero:02d}',
                                  monto=D('1000'), concepto=f'Factura {numero:02d}',
                                  fecha_vencimiento=self.hoy, estado_pago='PENDIENTE_PAGO')
        f.entrar(self.client, self.admin)
        self.filtros = {'libro': 'egresos', 'anio': self.hoy.year}

    def pantalla(self, **extra):
        return self.client.get(reverse('negocio:archivo'), {**self.filtros, **extra})

    def test_muestra_25_por_pagina_por_defecto(self):
        respuesta = self.pantalla()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['total'], CANTIDAD)
        self.assertEqual(respuesta.context['por_pagina'], 25)
        self.assertEqual(len(respuesta.context['filas']), 25)
        self.assertContains(respuesta, 'class="paginador"')

    def test_la_segunda_pagina_trae_el_resto(self):
        respuesta = self.pantalla(pagina=2)
        self.assertEqual(len(respuesta.context['filas']), CANTIDAD - 25)
        self.assertEqual(respuesta.context['pagina'].number, 2)

    def test_los_enlaces_de_pagina_conservan_los_filtros(self):
        respuesta = self.pantalla(por_pagina=10)
        parametros = respuesta.context['parametros']
        self.assertIn('libro=egresos', parametros)
        self.assertIn(f'anio={self.hoy.year}', parametros)
        self.assertIn('por_pagina=10', parametros)
        self.assertNotIn('pagina=', parametros.replace('por_pagina=', ''))
        self.assertContains(respuesta, f'?{parametros.replace("&", "&amp;")}&amp;pagina=2')
        self.assertEqual(len(respuesta.context['filas']), 10)

    def test_el_selector_de_tamanio_envia_el_formulario_de_la_consulta(self):
        html = self.pantalla().content.decode()
        self.assertIn('id="herramientas"', html)
        self.assertIn('form="herramientas"', html)

    def test_un_tamanio_fuera_de_la_lista_vuelve_a_25(self):
        respuesta = self.pantalla(por_pagina=1000000)
        self.assertEqual(respuesta.context['por_pagina'], 25)

    def test_las_descargas_bajan_el_periodo_completo(self):
        csv = self.client.get(reverse('negocio:archivo_exportar'),
                              {**self.filtros, 'pagina': 2, 'por_pagina': 10, 'formato': 'csv'})
        self.assertEqual(csv.status_code, 200)
        contenido = b''.join(csv.streaming_content if csv.streaming else [csv.content]).decode('utf-8-sig')
        for numero in range(CANTIDAD):
            self.assertIn(f'Proveedor {numero:02d}', contenido)
