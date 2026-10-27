"""CUIT tipeado como salga, y razón social que no se repite.

Dos fallos del reporte de pruebas de usuario:

  * **CUIT rígido.** El input llevaba ``pattern="\\d{2}-\\d{8}-\\d"``, que
    lo hace cumplir el NAVEGADOR antes de enviar: escribir los 11 dígitos
    de corrido se rechazaba en el cliente y nunca llegaba al servidor
    —que siempre lo aceptó, porque ``normalizar_cuit()`` saca los
    separadores—. Y del lado del formulario, el ``max_length=13`` del
    modelo cortaba "30.712.345.671" (14 caracteres) con un "máximo 13
    caracteres" que no explica nada.

  * **Razón social duplicada.** PROVEEDORES era la única maestra sin
    unicidad por clave normalizada: "Arcor SA" y "arcor sa" entraban las
    dos.

Los CUIT de prueba se CALCULAN con el mismo algoritmo que valida el
sistema, en vez de escribirlos a mano: un literal con el dígito
verificador mal puesto hace fallar la prueba por el motivo equivocado.
"""
from django.core.exceptions import ValidationError
from django.test import TestCase

from cafeto.validaciones import PESOS_CUIT, normalizar_cuit
from proveedores.forms import ProveedorForm
from proveedores.models import Proveedor


def cuit_valido(prefijo, cuerpo):
    """Arma un CUIT con su dígito verificador correcto (módulo 11)."""
    digitos = prefijo + cuerpo
    suma = sum(int(d) * p for d, p in zip(digitos, PESOS_CUIT))
    resto = 11 - (suma % 11)
    verificador = 0 if resto == 11 else (9 if resto == 10 else resto)
    return '%s-%s-%d' % (prefijo, cuerpo, verificador)


CUIT_A = cuit_valido('30', '71234567')
CUIT_B = cuit_valido('30', '71659554')


def datos(**extra):
    base = {'nombre_proveedor': 'Proveedor de prueba',
            'cuit_proveedor': CUIT_A, 'condicion_pago': 'CONTADO'}
    base.update(extra)
    return base


# ===========================================================================
#  CUIT: se tipea como salga, se guarda estandarizado
# ===========================================================================
class FormatoDelCuitTest(TestCase):

    def test_las_cinco_formas_de_tipearlo_dan_lo_mismo(self):
        pelado = CUIT_A.replace('-', '')
        formas = [
            pelado,                                              # de corrido
            CUIT_A,                                              # con guiones
            '%s %s %s' % (pelado[:2], pelado[2:10], pelado[10]),  # con espacios
            '%s.%s.%s' % (pelado[:2], pelado[2:10], pelado[10]),  # con puntos
            '  %s  ' % pelado,                                   # con espacios al borde
        ]
        for i, entrada in enumerate(formas):
            with self.subTest(entrada=entrada):
                form = ProveedorForm(datos(
                    nombre_proveedor='Proveedor numero %d' % i,
                    cuit_proveedor=entrada))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['cuit_proveedor'], CUIT_A)

    def test_los_once_digitos_de_corrido_se_guardan_con_guiones(self):
        form = ProveedorForm(datos(cuit_proveedor=CUIT_A.replace('-', '')))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().cuit_proveedor, CUIT_A)

    def test_el_input_no_lleva_pattern(self):
        """El ``pattern`` es lo que bloqueaba los 11 dígitos pelados."""
        html = str(ProveedorForm()['cuit_proveedor'])
        self.assertNotIn('pattern', html)
        self.assertIn('data-formato="cuit"', html)

    # -- lo que tiene que seguir rechazando ------------------------------
    def test_con_menos_de_once_digitos_no_pasa(self):
        form = ProveedorForm(datos(cuit_proveedor='3071234567'))
        self.assertFalse(form.is_valid())
        self.assertIn('11 dígitos', str(form.errors['cuit_proveedor']))

    def test_con_mas_de_once_digitos_no_pasa(self):
        form = ProveedorForm(datos(cuit_proveedor='307123456719'))
        self.assertFalse(form.is_valid())
        self.assertIn('11 dígitos', str(form.errors['cuit_proveedor']))

    def test_el_verificador_tiene_que_verificar(self):
        """Once dígitos con la pinta correcta no son un CUIT."""
        malo = CUIT_A[:-1] + str((int(CUIT_A[-1]) + 1) % 10)
        form = ProveedorForm(datos(cuit_proveedor=malo))
        self.assertFalse(form.is_valid())

    def test_el_prefijo_tiene_que_ser_de_la_lista(self):
        form = ProveedorForm(datos(cuit_proveedor='40-71234567-1'))
        self.assertFalse(form.is_valid())

    def test_las_letras_no_pasan(self):
        form = ProveedorForm(datos(cuit_proveedor='30-7123A567-1'))
        self.assertFalse(form.is_valid())

    def test_normalizar_cuit_es_la_misma_funcion_que_usa_el_modelo(self):
        self.assertEqual(normalizar_cuit(CUIT_A.replace('-', '')), CUIT_A)
        with self.assertRaises(ValidationError):
            normalizar_cuit('123')


# ===========================================================================
#  Razón social: única sin distinguir mayúsculas, acentos ni espacios
# ===========================================================================
class RazonSocialUnicaTest(TestCase):

    def setUp(self):
        form = ProveedorForm(datos(nombre_proveedor='Arcor SA',
                                   cuit_proveedor=CUIT_A))
        self.assertTrue(form.is_valid(), form.errors)
        self.existente = form.save()

    def test_las_variantes_del_mismo_nombre_se_rechazan(self):
        for nombre in ('arcor sa', 'ARCOR SA', 'Arcor  SA', '  Arcor SA  ',
                       'Arcór SA', 'Arcor-SA', 'Arcor/SA'):
            with self.subTest(nombre=nombre):
                form = ProveedorForm(datos(nombre_proveedor=nombre,
                                           cuit_proveedor=CUIT_B))
                self.assertFalse(form.is_valid())
                self.assertIn('nombre_proveedor', form.errors)
                # Lo corta el __iexact del formulario o la clave
                # normalizada del modelo, según la variante.
                self.assertRegex(
                    form.errors['nombre_proveedor'][0],
                    'ya se encuentra registrado|Ya existe un proveedor')

    def test_un_nombre_distinto_entra(self):
        """El candado no puede trabar un alta legítima."""
        form = ProveedorForm(datos(nombre_proveedor='Molinos Río de la Plata',
                                   cuit_proveedor=CUIT_B))
        self.assertTrue(form.is_valid(), form.errors)

    def test_el_nombre_se_guarda_sin_espacios_de_mas(self):
        form = ProveedorForm(datos(nombre_proveedor='   Molinos   Río   ',
                                   cuit_proveedor=CUIT_B))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().nombre_proveedor, 'Molinos Río')

    def test_editar_el_propio_no_choca_consigo_mismo(self):
        form = ProveedorForm(datos(nombre_proveedor='Arcor SA',
                                   cuit_proveedor=CUIT_A),
                             instance=self.existente)
        self.assertTrue(form.is_valid(), form.errors)

    def test_el_duplicado_dado_de_baja_manda_a_reactivar(self):
        self.existente.borrado_logico = True
        self.existente.save(update_fields=['borrado_logico'])

        form = ProveedorForm(datos(nombre_proveedor='arcor sa',
                                   cuit_proveedor=CUIT_B))
        self.assertFalse(form.is_valid())
        # El __iexact del formulario deja pasar los dados de baja justo
        # para que llegue este mensaje, que es el único que dice qué hacer.
        self.assertIn('reactivalo', form.errors['nombre_proveedor'][0])
        self.assertIn('Ya existe un registro con este nombre',
                      form.errors['nombre_proveedor'][0])

    def test_la_base_tambien_lo_impide(self):
        """El índice existe en PostgreSQL, no sólo la regla en Python.

        Se consulta ``pg_indexes`` y no ``Model._meta.constraints``:
        lo segundo sólo diría que el modelo lo declara, que es justo lo
        que no hace falta probar. Lo que importa es que la migración lo
        haya creado de verdad, porque es la red que queda si alguien
        escribe por fuera del ORM.

        Un ``UniqueConstraint`` sobre una EXPRESIÓN Django lo crea como
        índice único, no como fila de ``pg_constraint``: por eso se busca
        acá y no allá.
        """
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes "
                " WHERE schemaname = 'public'"
                "   AND indexname = 'uq_proveedores_nombre_norm'")
            fila = cursor.fetchone()

        self.assertIsNotNone(
            fila, 'la migración no creó el índice único del nombre')
        self.assertIn('f_clave_normalizada', fila[0])
        self.assertIn('UNIQUE', fila[0])

    def test_la_base_lo_impide_aunque_se_esquive_el_ORM(self):
        """Un INSERT crudo tampoco puede meter el duplicado."""
        from django.db import IntegrityError, connection, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO proveedores (nombre_proveedor, "
                    "  cuit_proveedor, condicion_pago, fecha_alta, "
                    "  borrado_logico, fecha_modificacion) "
                    "VALUES ('arcor  sa', %s, 'CONTADO', now(), false, now())",
                    [CUIT_B])
