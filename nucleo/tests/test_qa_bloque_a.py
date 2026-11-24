"""Pruebas de regresión del Bloque A de la auditoría QA y fuzzing.

Cubre:
  1. Eliminación de los 4 crashes HTTP 500 ante bytes NUL (``%00``) en
     buscadores GET y defensa global en ``nucleo.errores.mensaje_de``.
  2. Rechazo de letras/símbolos inválidos en ``ProveedorForm.telefono_proveedor``
     y ``validar_telefono`` (sin borrado silencioso a ``None``).
  3. Propagación de ``data-minimo`` / ``data-decimales`` en ``FormularioMaestro``,
     ``ProductoForm``, ``BeneficioForm``, ``CategoriaFidelizacionForm``,
     merma, traspaso y recepción de mercadería.
  4. Rechazo de nombres sólo numéricos (``"123456"``) y etiquetas HTML en
     ``PerfilForm`` y catálogos, conservando nombres alfanuméricos reales
     (``"7Up"``, ``"Caja 2"``, ``"25 de Mayo"``).
  5. Visibilidad condicional ``visible_si`` en ``BeneficioForm``.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.forms import ProveedorForm, TipoEgresoForm
from fidelizacion.forms import BeneficioForm, CategoriaFidelizacionForm
from fidelizacion.models import (
    Beneficio,
    CategoriaFidelizacion,
    Cliente,
    ModoEntrega,
    TipoBeneficio,
)
from geografia.forms import LocalidadForm, ProvinciaForm
from geografia.models import Localidad, Provincia
from negocio.models import ParametrosNegocio
from nucleo.errores import mensaje_de
from nucleo.tests import fabrica
from nucleo.validaciones import texto_de_catalogo, validar_telefono
from seguridad.forms import PerfilForm
from stock.forms import MarcaForm, ProductoForm, RubroForm
from stock.models import Rubro
from ventas.forms import CajaRegistradoraForm, MedioPagoForm


class ProteccionByteNulTest(TestCase):
    """Ningún buscador GET ni helper de identificación puede devolver 500 por %00."""

    def setUp(self):
        self.admin = fabrica.usuario('31000111', 'Administrador')
        fabrica.entrar(self.client, self.admin)

    def test_los_cuatro_buscadores_no_crashean_con_byte_nul(self):
        rutas = [
            f"{reverse('stock:buscar_productos')}?q=a%00b",
            f"{reverse('stock:buscar_productos')}?q=cafe&excluir=a%00b&tipos=a%00b",
            f"{reverse('ventas:identificar_cliente')}?q=a%00b",
            f"{reverse('fidelizacion:cuentas')}?q=a%00b",
            f"{reverse('seguridad:verificar_dni')}?dni=a%00b",
        ]
        for url in rutas:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertLess(resp.status_code, 500)

    def test_buscar_productos_con_nul_devuelve_vacio(self):
        resp = self.client.get(f"{reverse('stock:buscar_productos')}?q=a%00b")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['results'], [])

    def test_verificar_dni_con_nul_devuelve_false(self):
        resp = self.client.get(f"{reverse('seguridad:verificar_dni')}?dni=a%00b")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'existe': False})

    def test_cuentas_corrientes_con_nul_devuelve_200_sin_excepcion(self):
        resp = self.client.get(f"{reverse('fidelizacion:cuentas')}?q=a%00b")
        self.assertEqual(resp.status_code, 200)

    def test_cliente_identificar_con_nul_devuelve_none(self):
        self.assertIsNone(Cliente.identificar('a\x00b'))
        self.assertIsNone(Cliente.identificar('\x00'))

    def test_mensaje_de_traduce_valueerror_de_psycopg_por_nul_a_400(self):
        err = ValueError('A string literal cannot contain NUL (0x00) characters.')
        resultado = mensaje_de(err)
        self.assertIsNotNone(resultado)
        texto, estado = resultado
        self.assertEqual(estado, 400)
        self.assertIn('caracteres', texto.lower())


class TelefonoProveedorSinPerdidaSilenciosaTest(TestCase):
    """Escribir letras en teléfono debe rechazar el formulario, no borrarlo."""

    def _datos_base(self, **extra):
        datos = {
            'nombre_proveedor': 'Distribuidora del Norte',
            'cuit_proveedor': '30-12345678-1',
            'telefono_proveedor': '',
            'email_proveedor': 'ventas@norte.com',
            'domicilio': 'Av. Belgrano 123',
            'id_localidad': Localidad.objects.first().pk,
            'condicion_pago': 'CONTADO',
            'tipo_proveedor': 'MERCADERIA',
        }
        datos.update(extra)
        return datos

    def test_validar_telefono_rechaza_letras_y_simbolos(self):
        for malo in ('solo-letras', '387-4123456abc', '---', '12345'):
            with self.subTest(malo=malo), self.assertRaises(ValidationError):
                validar_telefono(malo)
        self.assertIsNone(validar_telefono(''))
        self.assertIsNone(validar_telefono('   '))
        self.assertEqual(validar_telefono('(0387) 412-3456'), '3874123456')

    def test_proveedor_form_rechaza_letras_en_telefono(self):
        form = ProveedorForm(self._datos_base(telefono_proveedor='solo-letras'))
        self.assertFalse(form.is_valid())
        self.assertIn('telefono_proveedor', form.errors)

    def test_proveedor_form_rechaza_telefono_mezclado_con_letras(self):
        form = ProveedorForm(self._datos_base(telefono_proveedor='387-4123456abc'))
        self.assertFalse(form.is_valid())
        self.assertIn('telefono_proveedor', form.errors)

    def test_proveedor_form_acepta_vacio_y_formatos_validos(self):
        for ok in ('', '(0387) 412-3456', '+54 387 4123456'):
            with self.subTest(telefono=ok):
                form = ProveedorForm(self._datos_base(telefono_proveedor=ok))
                self.assertTrue(form.is_valid(), form.errors)


class DataMinimoEnFormulariosYTemplatesTest(TestCase):
    """Los campos numéricos no negativos emiten data-minimo para pre-validación."""

    def setUp(self):
        self.admin = fabrica.usuario('31000222', 'Administrador')
        fabrica.entrar(self.client, self.admin)

    def test_producto_form_emite_data_minimo_y_no_en_utilidad(self):
        form = ProductoForm()
        self.assertEqual(form.fields['precio_costo'].widget.attrs.get('data-minimo'), '0')
        self.assertEqual(form.fields['precio_venta'].widget.attrs.get('data-minimo'), '0')
        self.assertEqual(form.fields['stock_minimo'].widget.attrs.get('data-minimo'), '0')
        self.assertEqual(form.fields['contenido'].widget.attrs.get('data-minimo'), '0.001')
        self.assertEqual(form.fields['dias_rebaja_vencimiento'].widget.attrs.get('data-minimo'), '1')
        self.assertEqual(form.fields['porcentaje_rebaja_vencimiento'].widget.attrs.get('data-minimo'), '1')
        # Utilidad admite valores negativos (venta bajo costo): no lleva data-minimo.
        self.assertNotIn('data-minimo', form.fields['utilidad'].widget.attrs)

    def test_beneficio_y_categoria_emiten_data_minimo(self):
        bf = BeneficioForm()
        self.assertEqual(bf.fields['porcentaje_descuento'].widget.attrs.get('data-minimo'), '0.01')
        self.assertEqual(bf.fields['puntos_requeridos'].widget.attrs.get('data-minimo'), '1')
        cf = CategoriaFidelizacionForm()
        self.assertEqual(cf.fields['importe_por_punto'].widget.attrs.get('data-minimo'), '1')

    def test_templates_merma_traspaso_recepcion_incluyen_data_minimo(self):
        for nombre in ('stock:merma', 'stock:traspaso', 'compras:recepcion'):
            with self.subTest(vista=nombre):
                resp = self.client.get(reverse(nombre))
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, 'data-minimo=')


class NombresCatalogosYPerfilesTest(TestCase):
    """Bloquea nombres sólo numéricos o con etiquetas HTML y permite los reales."""

    def test_texto_de_catalogo_exige_letra(self):
        for malo in ('123456', '99-99', '---'):
            with self.subTest(malo=malo), self.assertRaises(ValidationError):
                texto_de_catalogo(malo, 2, 30, 'El nombre', exige_letra=True)
        for ok in ('7Up', 'Caja 2', '25 de Mayo', 'Café 1890', '10% descuento'):
            with self.subTest(ok=ok):
                self.assertEqual(
                    texto_de_catalogo(ok, 2, 30, 'El nombre', exige_letra=True), ok)

    def test_perfil_form_rechaza_solo_numeros_y_script(self):
        for malo in ('123456', '<script>x</script>'):
            with self.subTest(nombre=malo):
                form = PerfilForm({
                    'nombre': malo,
                    'descripcion': 'Prueba',
                    'tope_descuento_porcentaje': '10',
                })
                self.assertFalse(form.is_valid())
                self.assertIn('nombre', form.errors)

        form_desc_html = PerfilForm({
            'nombre': 'Cajero Turno 2',
            'descripcion': '<script>alert(1)</script>',
            'tope_descuento_porcentaje': '10',
        })
        self.assertFalse(form_desc_html.is_valid())
        self.assertIn('descripcion', form_desc_html.errors)

        form_ok = PerfilForm({
            'nombre': 'Cajero Turno 2',
            'descripcion': 'Atiende caja tarde',
            'tope_descuento_porcentaje': '10',
        })
        self.assertTrue(form_ok.is_valid(), form_ok.errors)

    def test_provincia_rechaza_digitos_y_solo_numeros(self):
        for malo in ('123456', 'Salta123'):
            with self.subTest(nombre=malo):
                form = ProvinciaForm({'nombre_provincia': malo, 'confirmo_alta': True})
                self.assertFalse(form.is_valid())

        form_ok = ProvinciaForm({'nombre_provincia': 'Antártida', 'confirmo_alta': True})
        self.assertTrue(form_ok.is_valid(), form_ok.errors)

    def test_localidad_rechaza_solo_numeros_y_permite_25_de_mayo(self):
        salta = Provincia.objects.get(nombre_provincia='Salta')
        form_mal = LocalidadForm({
            'id_provincia': salta.pk,
            'nombre_localidad': '123456',
            'codigo_postal': '4400',
        })
        self.assertFalse(form_mal.is_valid())

        form_ok = LocalidadForm({
            'id_provincia': salta.pk,
            'nombre_localidad': '25 de Mayo',
            'codigo_postal': '4400',
        })
        self.assertTrue(form_ok.is_valid(), form_ok.errors)

    def test_catalogos_rechazan_solo_digitos_y_aceptan_alfanumericos(self):
        self.assertFalse(RubroForm({'nombre_rubro': '123456', 'activo': True}).is_valid())
        self.assertTrue(RubroForm({'nombre_rubro': 'Bebidas 500', 'activo': True}).is_valid())

        self.assertFalse(MarcaForm({'nombre_marca': '123456'}).is_valid())
        self.assertTrue(MarcaForm({'nombre_marca': '7Up'}).is_valid())

        self.assertFalse(TipoEgresoForm({'nombre_tipo_egreso': '123456'}).is_valid())
        self.assertTrue(TipoEgresoForm({'nombre_tipo_egreso': 'Impuestos 2026'}).is_valid())

        self.assertFalse(CajaRegistradoraForm({'nombre_caja_registradora': '123456'}).is_valid())
        self.assertTrue(CajaRegistradoraForm({'nombre_caja_registradora': 'Caja 2'}).is_valid())

        self.assertFalse(MedioPagoForm({
            'nombre_medio_pago': '123456', 'recargo_porcentaje': '0',
        }).is_valid())
        self.assertTrue(MedioPagoForm({
            'nombre_medio_pago': 'Cuotas 3 pagos', 'recargo_porcentaje': '0',
        }).is_valid())

        self.assertFalse(CategoriaFidelizacionForm({
            'nombre_categoria': '123456', 'puntos_minimos': '500',
            'importe_por_punto': '100',
        }).is_valid())

        rubro = Rubro.objects.first() or Rubro.objects.create(nombre_rubro='General', activo=True)
        self.assertFalse(ProductoForm({
            'nombre_producto': '123456', 'id_rubro': rubro.pk,
            'precio_costo': '100', 'precio_venta': '200',
        }).is_valid())

        param = ParametrosNegocio.vigentes()
        param.razon_social = '123456'
        with self.assertRaises(ValidationError):
            param.clean()


class BeneficioFormVisibilidadCondicionalTest(TestCase):
    """BeneficioForm configura visible_si y limpia campos ocultos al cambiar modo/tipo."""

    def test_beneficio_form_declara_visible_si(self):
        form = BeneficioForm()
        self.assertEqual(
            form.fields['porcentaje_descuento'].visible_si,
            f'modo_entrega={ModoEntrega.DESCUENTO}')
        self.assertEqual(
            form.fields['puntos_requeridos'].visible_si,
            f'tipo_beneficio={TipoBeneficio.CANJEABLE}')

    def test_beneficio_form_descarta_campos_ocultos_al_cambiar_tipo_o_modo(self):
        cat = CategoriaFidelizacion.objects.first()
        hoy = timezone.localdate().strftime('%d/%m/%Y')

        # Usuario pasó a ESPECIE pero el input oculto seguía mandando porcentaje=10
        form_especie = BeneficioForm({
            'id_categoria': cat.pk,
            'descripcion_beneficio': 'Café gratis de bienvenida',
            'tipo_beneficio': TipoBeneficio.CANJEABLE,
            'modo_entrega': ModoEntrega.ESPECIE,
            'porcentaje_descuento': '10',
            'puntos_requeridos': '100',
            'vigencia_desde': hoy,
        })
        self.assertTrue(form_especie.is_valid(), form_especie.errors)
        self.assertIsNone(form_especie.cleaned_data['porcentaje_descuento'])

        # Usuario pasó a AUTOMATICO pero el input oculto seguía mandando puntos=50
        form_auto = BeneficioForm({
            'id_categoria': cat.pk,
            'descripcion_beneficio': '15% descuento automático',
            'tipo_beneficio': TipoBeneficio.AUTOMATICO,
            'modo_entrega': ModoEntrega.DESCUENTO,
            'porcentaje_descuento': '15',
            'puntos_requeridos': '50',
            'vigencia_desde': hoy,
        })
        self.assertTrue(form_auto.is_valid(), form_auto.errors)
        self.assertIsNone(form_auto.cleaned_data['puntos_requeridos'])

    def test_modelo_beneficio_sigue_siendo_estricto_en_capa_2(self):
        cat = CategoriaFidelizacion.objects.first()
        b = Beneficio(
            id_categoria=cat,
            descripcion_beneficio='Incoherente en capa 2',
            tipo_beneficio=TipoBeneficio.AUTOMATICO,
            modo_entrega=ModoEntrega.DESCUENTO,
            porcentaje_descuento=Decimal('10'),
            puntos_requeridos=50,
            vigencia_desde=timezone.localdate(),
        )
        with self.assertRaises(ValidationError):
            b.clean()
