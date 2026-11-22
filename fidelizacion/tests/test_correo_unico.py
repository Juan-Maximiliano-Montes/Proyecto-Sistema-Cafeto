"""El correo del cliente es único entre los vigentes.

Con el correo el cliente entra a la app del club y la app encuentra su
ficha: con dos fichas vigentes que lo comparten, no sabría cuál mostrar.

    CorreoUnicoTest       el alta y la edición lo frenan, sin distinguir
                          mayúsculas; un dado de baja no cuenta, pero no se
                          lo reactiva encima de otro
    CorreosRepetidosTest  los que ya estaban repetidos: el comando que los
                          lista y el aviso del inicio para quien ve clientes
"""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from fidelizacion.forms import ClienteForm
from fidelizacion.models import Cliente
from negocio import panel
from nucleo.tests import fabrica as f
from seguridad.models import Perfil

CORREO = 'marta@correo.test'


def alta(telefono, correo=CORREO, **extra):
    return ClienteForm({'apellido_cliente': 'Gutiérrez', 'nombre_cliente': 'Marta',
                        'telefono_cliente': telefono, 'email_cliente': correo, **extra})


class CorreoUnicoTest(TestCase):

    def test_dos_fichas_vigentes_no_comparten_el_correo(self):
        primera = alta('3874556001')
        self.assertTrue(primera.is_valid(), primera.errors)
        primera.save()
        segunda = alta('3874556002', correo=CORREO.upper())
        self.assertFalse(segunda.is_valid())
        self.assertIn('ya existe', segunda.errors['email_cliente'][0])

    def test_al_editar_tambien(self):
        alta('3874556001').save()
        otra = alta('3874556002', correo='otra@correo.test')
        otra.is_valid()
        otra = otra.save()
        edicion = ClienteForm({'apellido_cliente': 'Gutiérrez', 'nombre_cliente': 'Marta',
                               'telefono_cliente': '3874556002', 'email_cliente': CORREO},
                              instance=otra)
        self.assertFalse(edicion.is_valid())
        self.assertIn('email_cliente', edicion.errors)

    def test_un_dado_de_baja_no_cuenta_pero_no_se_reactiva_encima(self):
        vieja = alta('3874556001')
        vieja.is_valid()
        vieja = vieja.save()
        Cliente.objects.filter(pk=vieja.pk).update(borrado_logico=True)
        nueva = alta('3874556002')
        self.assertTrue(nueva.is_valid(), nueva.errors)
        nueva.save()
        vieja.refresh_from_db()
        self.assertIn('ya es de', vieja.motivos_que_impiden_reactivar()[0])


class CorreosRepetidosTest(TestCase):

    def setUp(self):
        # Fichas de antes de la regla: entraron sin pasar por clean().
        for telefono, correo in (('3871111111', CORREO), ('3872222222', CORREO.upper()),
                                 ('3873333333', 'unica@correo.test')):
            Cliente.objects.create(apellido_cliente='Gutiérrez', nombre_cliente='Marta',
                                   telefono_cliente=telefono, email_cliente=correo)

    def test_el_comando_los_lista(self):
        salida = StringIO()
        call_command('clientes_correo_repetido', stdout=salida)
        texto = salida.getvalue()
        self.assertIn('2 cliente(s) comparten 1 correo(s)', texto)
        self.assertIn(f'{CORREO} (2 fichas)', texto)
        self.assertNotIn('unica@correo.test', texto)

    def test_sin_repetidos_lo_dice(self):
        Cliente.objects.filter(email_cliente__iexact=CORREO).update(borrado_logico=True)
        salida = StringIO()
        call_command('clientes_correo_repetido', stdout=salida)
        self.assertIn('No hay clientes vigentes que compartan el correo', salida.getvalue())

    def test_el_inicio_avisa_a_quien_ve_clientes(self):
        # El Operador ve clientes (los carga en el mostrador): también se entera.
        operador = f.usuario('40000001', 'Operador', nombre='Cami')
        _, avisos = panel.del_dia(operador)
        self.assertIn({'clave': 'CORREOS_REPETIDOS', 'tipo': 'aviso',
                       'texto': '2 clientes comparten el correo: unificalos.'}, avisos)
        Perfil.objects.create(nombre='Cocina', descripcion='Sin permisos',
                              tope_descuento_porcentaje=Decimal('0'))
        sin_clientes = f.usuario('40000009', 'Cocina', nombre='Rita')
        _, avisos = panel.del_dia(sin_clientes)
        self.assertNotIn('CORREOS_REPETIDOS', [a['clave'] for a in avisos])

    def test_la_web_lo_muestra_con_el_enlace_a_clientes(self):
        f.entrar(self.client, f.usuario('40000003', 'Administrador', nombre='Ana'))
        avisos = self.client.get(reverse('home'), follow=True).context['avisos']
        aviso = next(a for a in avisos if 'comparten el correo' in a[2])
        self.assertEqual(aviso[3], reverse('fidelizacion:clientes_lista'))
