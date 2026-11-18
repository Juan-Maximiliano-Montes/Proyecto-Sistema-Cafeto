"""Detalles de pantalla del cierre de QA (DD-103): los montos se revisan
antes del resumen, el estado de la red no prende botones que el sistema
apagó, la confirmación queda arriba de la ventana del formulario y los
desplegables muestran una sola flecha.

Lo que necesita un navegador está en ``test_navegador``; acá se controla
lo que llega a la página."""
from pathlib import Path

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from nucleo.formularios import CampoCantidad, CampoImporte, CampoNumero
from nucleo.tests import fabrica as f

ESTATICOS = Path(settings.BASE_DIR) / 'nucleo' / 'static' / 'cafeto'


def _leer(nombre):
    return (ESTATICOS / nombre).read_text(encoding='utf-8')


class MinimoEnElCampoTest(SimpleTestCase):

    def test_el_importe_y_la_cantidad_llevan_su_minimo(self):
        self.assertEqual(CampoImporte().widget_attrs(None), {'data-minimo': '0'})
        self.assertEqual(CampoCantidad().widget_attrs(None), {'data-minimo': '0.001'})
        self.assertEqual(CampoNumero().widget_attrs(None), {})

    def test_sin_min_max_ni_step_del_navegador(self):
        html = CampoImporte().widget.render('monto', None, attrs=CampoImporte().widget_attrs(None))
        for atributo in (' min=', ' max=', ' step=', 'type="number"'):
            self.assertNotIn(atributo, html)


class CajaTest(TestCase):

    def test_el_fondo_inicial_lleva_el_minimo(self):
        cliente = Client()
        f.entrar(cliente, f.titular())
        cliente.post(reverse('ventas:elegir_caja'), {'caja': f.caja_minisuper().pk})
        respuesta = cliente.get(reverse('ventas:caja'))
        self.assertContains(respuesta, 'name="monto_inicial"')
        self.assertContains(respuesta, 'data-minimo="0"')

    def test_un_fondo_negativo_lo_rechaza_el_servidor_igual(self):
        cliente = Client()
        f.entrar(cliente, f.titular())
        cliente.post(reverse('ventas:elegir_caja'), {'caja': f.caja_minisuper().pk})
        respuesta = cliente.get(reverse('ventas:caja'))
        token = respuesta.context['form_apertura']['token_operacion'].value()
        respuesta = cliente.post(reverse('ventas:abrir_turno'),
                                 {'monto_inicial': '-500', 'token_operacion': token}, follow=True)
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(f.caja_minisuper().turnos.filter(estado_turno='ABIERTA').exists())


class JavascriptTest(SimpleTestCase):

    def setUp(self):
        self.js = _leer('js/cafeto.js')

    def test_la_red_sólo_vuelve_a_prender_lo_que_apagó(self):
        bloque = self.js[self.js.index('function estadoDeRed'):self.js.index("window.addEventListener('online'")]
        self.assertIn("if (!control.disabled)", bloque)
        self.assertIn("control.dataset.deshabilitadoPorRed = '1'", bloque)
        self.assertIn("else if (control.dataset.deshabilitadoPorRed === '1')", bloque)

    def test_los_montos_se_revisan_antes_del_resumen(self):
        bloque = self.js[self.js.index('function numerosValidos'):]
        self.assertIn('input[data-minimo]', bloque)
        self.assertIn('No puede ser negativo.', bloque)
        envio = self.js[self.js.index("if (!form.matches('.form-una-vez')) { return; }"):]
        self.assertLess(envio.index('numerosValidos(form)'), envio.index('Cafeto.confirmar'))

    def test_la_pregunta_propia_no_abre_un_segundo_resumen(self):
        bloque = self.js[self.js.index('form[data-confirmar="pregunta"]'):self.js.index('function resumenDelFormulario')]
        self.assertIn('evento.stopPropagation()', bloque)
        self.assertIn('resumenDelFormulario(form)', bloque)

    def test_al_cerrar_la_confirmacion_el_modal_de_abajo_sigue_trabando_el_fondo(self):
        bloque = self.js[self.js.index('function alCerrar'):self.js.index('aceptar.addEventListener')]
        self.assertIn("classList.add('modal-open')", bloque)


class EstilosTest(SimpleTestCase):

    def setUp(self):
        self.css = _leer('css/cafeto.css')

    def test_la_confirmacion_va_arriba_de_cualquier_ventana(self):
        self.assertRegex(self.css, r'#modalConfirmar\s*\{\s*z-index:\s*1080;')
        self.assertIn('.modal-backdrop ~ .modal-backdrop', self.css)

    def test_el_desplegable_tiene_una_sola_flecha(self):
        self.assertRegex(self.css, r'\.form-select\s*\{[^}]*appearance:\s*none;[^}]*-webkit-appearance:\s*none;')


class FichaDelTurnoTest(SimpleTestCase):

    def test_la_recaudacion_se_desplaza_dentro_de_su_caja(self):
        plantilla = (Path(settings.BASE_DIR) / 'ventas' / 'templates' / 'ventas' / '_ficha_turno.html').read_text()
        recaudacion = plantilla[plantilla.index('Recaudación por medio de pago'):]
        self.assertLess(recaudacion.index('table-responsive'), recaudacion.index('<table'))
