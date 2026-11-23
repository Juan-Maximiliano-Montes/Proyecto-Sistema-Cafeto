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

from nucleo.formularios import (
    DIGITOS_EN_BASE,
    CampoCantidad,
    CampoImporte,
    CampoNumero,
    leer_numero,
)
from nucleo.tests import fabrica as f

ESTATICOS = Path(settings.BASE_DIR) / 'nucleo' / 'static' / 'cafeto'


def _leer(nombre):
    return (ESTATICOS / nombre).read_text(encoding='utf-8')


class MinimoEnElCampoTest(SimpleTestCase):

    def test_el_importe_y_la_cantidad_llevan_su_minimo(self):
        # Con el mínimo viajan los decimales: el JS lee lo tipeado con las
        # mismas reglas que ``leer_numero`` y ahí los decimales deciden si
        # "1.500" son mil quinientos pesos o uno y medio kilo.
        self.assertEqual(CampoImporte().widget_attrs(None),
                         {'data-minimo': '0', 'data-decimales': '2'})
        self.assertEqual(CampoCantidad().widget_attrs(None),
                         {'data-minimo': '0.001', 'data-decimales': '3'})
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


# ===========================================================================
#  Los precios, de punta a punta (auditoría 2026-11)
# ===========================================================================
class CalculadoraDePreciosTest(SimpleTestCase):
    """La calculadora del alta de productos multiplicaba los precios x100.

    Dos mitades del mismo problema, y las dos están acá porque lo que las
    rompía era mezclar el formato del navegador con el del servidor:

    * al LEER: ``parseFloat('1.500')`` da 1,5, así que el precio que dibuja
      el servidor para un producto de $1.500 entraba a la cuenta como uno
      con cincuenta;
    * al ESCRIBIR: ``toFixed(2)`` da '1400.00', y con
      LANGUAGE_CODE='es-ar' y USE_THOUSAND_SEPARATOR=True Django lee ese
      punto como separador de MILES: se guardaba $140.000,00.
    """

    def setUp(self):
        self.js = _leer('js/cafeto.js')

    def test_leer_un_numero_sigue_las_reglas_del_servidor(self):
        bloque = self.js[self.js.index('function leerNumero'):
                         self.js.index('function textoDeNumero')]
        # Las tres reglas de leer_numero, en el mismo orden.
        self.assertIn("texto.lastIndexOf(',') > texto.lastIndexOf('.')", bloque)
        self.assertIn('decimales < 3 && todosDeTres', bloque)
        self.assertIn('p.length === 3', bloque)
        # Y los dos topes de la base: decimales y cantidad de dígitos.
        self.assertIn('texto.length - coma - 1 > decimales', bloque)
        self.assertIn('DIGITOS_EN_BASE - decimales', bloque)
        self.assertEqual(DIGITOS_EN_BASE, 12)
        self.assertIn('var DIGITOS_EN_BASE = 12;', self.js)

    def test_escribir_un_numero_usa_coma_decimal(self):
        bloque = self.js[self.js.index('function textoDeNumero'):
                         self.js.index('Cafeto.leerNumero')]
        self.assertIn("toFixed(decimales).replace('.', ',')", bloque)

    def test_la_calculadora_no_escribe_con_punto(self):
        bloque = self.js[self.js.index('function recalcular'):
                         self.js.index("['input', 'change'].forEach")]
        self.assertIn('escribirNumero(utilidad', bloque)
        self.assertIn('escribirNumero(precio', bloque)
        self.assertNotIn('toFixed', bloque)

    def test_el_redondeo_rapido_tampoco(self):
        bloque = self.js[self.js.index(".js-redondeo [data-paso]"):
                         self.js.index('js-codigo-interno')]
        self.assertIn('escribirNumero(precio', bloque)
        self.assertNotIn('toFixed', bloque)

    def test_la_calculadora_lee_los_campos_con_las_mismas_reglas(self):
        bloque = self.js[self.js.index('function numeroDe'):
                         self.js.index('function recalcular')]
        self.assertIn('leerNumero(control.value, decimalesDe(control))', bloque)
        self.assertNotIn('parseFloat', bloque)

    def test_el_campo_le_dice_al_navegador_cuantos_decimales_acepta(self):
        """Sin esto, el JS no puede saber si "1.500" son mil quinientos
        pesos (dos decimales) o uno y medio kilo (tres)."""
        self.assertEqual(CampoImporte().decimales, 2)
        self.assertEqual(CampoCantidad().decimales, 3)
        self.assertEqual(CampoImporte().widget_attrs(None)['data-decimales'], '2')
        self.assertEqual(CampoCantidad().widget_attrs(None)['data-decimales'], '3')
        self.assertIn('dataset.decimales', self.js)


class LeerNumeroDelServidorTest(SimpleTestCase):
    """La tabla de casos que el JS replica. Si alguna cambia acá, hay que
    cambiarla también en ``leerNumero`` de cafeto.js."""

    CASOS = (
        # (texto, decimales, resultado)
        ('1.500', 2, '1500'),            # un punto y tres dígitos: miles
        ('1.500,00', 2, '1500.00'),
        ('1400,00', 2, '1400.00'),
        ('1.234.567', 2, '1234567'),
        ('12.5', 2, '12.5'),             # no son tres dígitos: decimal
        ('1,234.50', 2, '1234.50'),      # el último manda
        ('$ 2.300', 2, '2300'),
        ('1.250', 3, '1.250'),           # tres decimales: el punto es decimal
        ('0,001', 3, '0.001'),
        ('9999999999,99', 2, '9999999999.99'),
        ('abc', 2, None),
        ('1.5000', 2, None),             # más decimales que la columna
        ('0,0005', 3, None),
        ('1,2,3', 2, None),
        ('1.23.4', 2, None),
        ('1e3', 2, None),                # nada de notación científica
        ('99999999999,99', 2, None),     # no entra en la columna
        ('', 2, None),
    )

    def test_cada_caso(self):
        from decimal import Decimal

        for texto, decimales, esperado in self.CASOS:
            with self.subTest(texto=texto, decimales=decimales):
                dio = leer_numero(texto, decimales)
                self.assertEqual(dio, None if esperado is None else Decimal(esperado))
