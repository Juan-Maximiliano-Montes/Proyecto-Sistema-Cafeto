"""Facturas por foto, PDF, WhatsApp o correo, y la compra de salvataje (DD-98)."""
import base64
import json
import zlib
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from compras import facturas, lector_facturas
from compras.models import Compra, EstadoFacturaRecibida, FacturaRecibida, Proveedor
from negocio import ganancia, mensual, panel
from nucleo.tests import fabrica as f
from stock.models import Producto
from ventas import servicios as ventas

TOKEN = 'credencial-de-prueba-larga-0123456789abcdef'


def pdf_con_texto(*lineas, qr=None):
    """Un PDF mínimo con texto en un flujo FlateDecode (como los arman los
    sistemas de facturación) y, si se pide, el enlace del QR de ARCA."""
    contenido = b'BT /F1 9 Tf 40 800 Td ' + b' T* '.join(
        b'(' + l.encode('latin-1').replace(b'(', b'\\(').replace(b')', b'\\)') + b') Tj' for l in lineas) + b' ET'
    flujo = zlib.compress(contenido)
    anotacion = (b'<< /Type /Annot /Subtype /Link /A << /S /URI /URI (' + qr.encode() + b') >> >>') if qr else b''
    return (b'%PDF-1.4\n1 0 obj << /Length ' + str(len(flujo)).encode() + b' /Filter /FlateDecode >>\nstream\n'
            + flujo + b'\nendstream\nendobj\n' + anotacion + b'\n%%EOF')


class Base(TestCase):

    def setUp(self):
        self.admin = f.usuario('40300001', 'Administrador', nombre='Rita', apellido='Ruiz')
        self.encargada = f.usuario('40300002', 'Encargado', nombre='Lucre', apellido='Sosa')
        self.prov = f.proveedor()
        self.yerba = f.producto('Yerba suave', prov=self.prov, precio='3000', costo='2000',
                                codigo_barras='7790070012345')
        self.leche = f.producto('Leche entera', prov=self.prov, precio='1500', costo='900',
                                codigo_barras='7790080055555', controla_vencimiento=True)


# ===========================================================================
#  El lector local
# ===========================================================================
class LectorTest(Base):

    TEXTO = '\n'.join([
        'FACTURA A   Cód. 01', 'Razón Social: Distribuidora Norte', 'CUIT: 30-71234567-1',
        'Punto de Venta: 0003   Comp. Nro: 00012345', 'Fecha de Emisión: 10/11/2026',
        'Fecha de Vto. para el pago: 25/11/2026',
        '7790070012345  Yerba suave 1 kg        10,00     2.150,00     21.500,00',
        '7790080055555  Leche entera 1 l        24        950,00       22.800,00',
        '               Lote: L33 Vto: 30/12/2026',
        'Subtotal 44.300,00', 'IVA 21 % 9.303,00', 'Importe total 53.603,00'])

    def test_interpreta_encabezado_renglones_y_lotes(self):
        datos = lector_facturas.interpretar(self.TEXTO)
        self.assertEqual((datos['cuit_proveedor'], datos['tipo_comprobante'], datos['punto_venta'],
                          datos['numero'], datos['fecha'], datos['vence']),
                         ('30-71234567-1', 'FACTURA_A', '0003', '00012345', '2026-11-10', '2026-11-25'))
        self.assertEqual([(r['codigo_barras'], r['cantidad'], r['costo_unitario']) for r in datos['renglones']],
                         [('7790070012345', '10.00', '2150.00'), ('7790080055555', '24', '950.00')])
        self.assertEqual(datos['renglones'][1]['lotes'], [{'numero': 'L33', 'vence': '2026-12-30'}])

    def test_lee_el_texto_de_un_pdf_y_el_qr_de_arca_manda(self):
        qr = {'ver': 1, 'fecha': '2026-11-12', 'cuit': 30712345671, 'ptoVta': 7, 'tipoCmp': 6, 'nroCmp': 991}
        enlace = 'https://www.afip.gob.ar/fe/qr/?p=' + base64.b64encode(json.dumps(qr).encode()).decode()
        texto, leido = lector_facturas.texto_de(pdf_con_texto(*self.TEXTO.splitlines(), qr=enlace),
                                                'application/pdf', 'factura.pdf')
        self.assertIn('Yerba suave', texto)
        datos = lector_facturas.interpretar(texto, leido)
        self.assertEqual((datos['tipo_comprobante'], datos['punto_venta'], datos['numero'], datos['fecha']),
                         ('FACTURA_B', '7', '991', '2026-11-12'))
        self.assertEqual(len(datos['renglones']), 2)

    def test_una_foto_sin_ocr_lo_dice_y_un_archivo_raro_no_entra(self):
        import shutil
        if shutil.which('tesseract') is None:
            with self.assertRaisesMessage(Exception, 'lector de texto para fotos'):
                lector_facturas.texto_de(b'\xff\xd8\xff\xe0 foto', 'image/jpeg', 'factura.jpg')
        with self.assertRaisesMessage(Exception, 'no es una foto'):
            lector_facturas.texto_de(b'MZ\x90', 'application/x-msdownload', 'virus.exe')
        with self.assertRaisesMessage(Exception, '8 MB'):
            lector_facturas.texto_de(b'x' * (lector_facturas.MAXIMO_BYTES + 1), 'text/plain', 'a.txt')


# ===========================================================================
#  La pantalla
# ===========================================================================
class EscanearTest(Base):

    def setUp(self):
        super().setUp()
        f.entrar(self.client, self.admin)

    def test_la_muestra_llena_el_formulario_y_ofrece_el_alta_del_producto_nuevo(self):
        pantalla = self.client.get(reverse('compras:nueva_compra'))
        self.assertContains(pantalla, 'Escanear o subir la factura')
        # La muestra es para la demostración: en el uso diario no aparece (DD-101).
        self.assertNotContains(pantalla, 'Probar con factura de muestra')
        demo = self.client.get(reverse('compras:nueva_compra'), {'demo': '1'})
        self.assertContains(demo, 'Probar con factura de muestra')
        self.assertContains(pantalla, 'accept="image/*,application/pdf,text/*" capture="environment"')
        self.assertContains(pantalla, 'Compra ocasional / de salvataje')
        r = self.client.post(reverse('compras:escanear_factura'), {'muestra': '1'}).json()
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['origen'], 'muestra')
        self.assertEqual(r['proveedor'], {'id': self.prov.pk, 'nombre': str(self.prov), 'existe': True})
        self.assertEqual(r['tipo_comprobante'], 'FACTURA_A')
        self.assertEqual(r['fecha'], timezone.localdate().isoformat())
        self.assertEqual({x['producto'] for x in r['renglones']}, {self.yerba.pk, self.leche.pk})
        leche = next(x for x in r['renglones'] if x['producto'] == self.leche.pk)
        self.assertEqual(len(leche['lotes']), 1)
        self.assertEqual([n['codigo'] for n in r['nuevos']], ['7790000000017'])

        nuevo = r['nuevos'][0]
        alta = self.client.post(reverse('compras:alta_producto_factura'), json.dumps({
            'nombre': nuevo['descripcion'], 'codigo': nuevo['codigo'], 'costo': nuevo['costo'],
            'proveedor': str(self.prov.pk), 'controla': True}), content_type='application/json').json()
        self.assertTrue(alta['ok'], alta)
        producto = Producto.objects.get(pk=alta['producto'])
        self.assertEqual((producto.precio_costo, producto.precio_venta, producto.id_proveedor,
                          producto.controla_vencimiento), (D('980.00'), D('1372.00'), self.prov, True))
        # Una segunda vez choca con el código: no se duplica.
        otra = self.client.post(reverse('compras:alta_producto_factura'), json.dumps({
            'nombre': 'Otra cosa', 'codigo': nuevo['codigo'], 'costo': '10'}), content_type='application/json').json()
        self.assertFalse(otra['ok'])

    def test_el_proveedor_nuevo_se_da_de_alta_en_un_clic(self):
        r = self.client.post(reverse('compras:alta_proveedor_factura'), json.dumps(
            {'nombre': 'Lácteos del Valle', 'cuit': '30-71000000-6'}), content_type='application/json').json()
        self.assertTrue(r['ok'], r)
        self.assertTrue(Proveedor.objects.filter(pk=r['id'], cuit_proveedor='30-71000000-6').exists())

    def test_sube_un_pdf(self):
        archivo = SimpleUploadedFile('factura.pdf', pdf_con_texto(*LectorTest.TEXTO.splitlines()),
                                     content_type='application/pdf')
        r = self.client.post(reverse('compras:escanear_factura'), {'archivo': archivo}).json()
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['origen'], 'local')
        self.assertEqual(len(r['renglones']), 2)

    def test_sin_permiso_de_compras_no_escanea(self):
        cajera = f.usuario('40300003', 'Operador', nombre='Cami')
        f.entrar(self.client, cajera)
        respuesta = self.client.post(reverse('compras:escanear_factura'), {'muestra': '1'})
        self.assertEqual(respuesta.status_code, 302)


class BombaDeDescompresionTest(TestCase):
    """DD-103: un PDF de pocos KB puede llevar un flujo que, descomprimido,
    ocupa gigas. Se corta a los 16 MB con un aviso, no con la memoria del
    servidor."""

    @staticmethod
    def pdf_con_flujos(*tamanios):
        cuerpo = b'%PDF-1.4\n'
        for tamanio in tamanios:
            flujo = zlib.compress(b'0' * tamanio, 9)
            cuerpo += (b'1 0 obj << /Filter /FlateDecode >>\nstream\n' + flujo
                       + b'\nendstream\nendobj\n')
        return cuerpo + b'%%EOF'

    def test_un_flujo_que_se_infla_de_mas_se_rechaza(self):
        pdf = self.pdf_con_flujos(64 * 1024 * 1024)
        self.assertLess(len(pdf), lector_facturas.MAXIMO_BYTES)
        with self.assertRaisesMessage(ValidationError, 'demasiado grande'):
            lector_facturas.texto_de(pdf, 'application/pdf')

    def test_muchos_flujos_que_juntos_pasan_el_tope_tambien(self):
        mega = 1024 * 1024
        with self.assertRaisesMessage(ValidationError, 'demasiado grande'):
            lector_facturas.texto_de(self.pdf_con_flujos(6 * mega, 6 * mega, 6 * mega), 'application/pdf')
        # Bajo el tope se lee normal (este no trae texto: es como un escaneo).
        with self.assertRaisesMessage(ValidationError, 'no trae texto'):
            lector_facturas.texto_de(self.pdf_con_flujos(mega, mega), 'application/pdf')

    def test_un_pdf_comun_se_sigue_leyendo(self):
        texto, _qr = lector_facturas.texto_de(pdf_con_texto('FACTURA B', 'Total 1.000,00'), 'application/pdf')
        self.assertIn('FACTURA B', texto)


# ===========================================================================
#  La bandeja: WhatsApp / correo vía n8n
# ===========================================================================
@override_settings(CAFETO_N8N_TOKEN=TOKEN)
class BandejaTest(Base):

    CUERPO = {'origen': 'WHATSAPP', 'remitente': '+54 387 555-1234',
              'cuit_proveedor': '30712345671', 'tipo_comprobante': 'FACTURA_B', 'punto_venta': '3',
              'numero': '4521', 'fecha': '2026-11-15',
              'renglones': [{'codigo_barras': '7790070012345', 'cantidad': 10, 'costo_unitario': 2100}]}

    def pre_cargar(self, cuerpo=None):
        return self.client.post(reverse('api_servicio:precargar_compra'), json.dumps(cuerpo or self.CUERPO),
                                content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {TOKEN}')

    def test_queda_en_la_bandeja_avisa_y_se_confirma_una_sola_vez(self):
        primera = self.pre_cargar()
        self.assertEqual(primera.status_code, 201)
        datos = primera.json()
        self.assertTrue(datos['en_bandeja'])
        self.assertEqual(Compra.objects.count(), 0)
        # Un reintento de n8n no la deja dos veces.
        repetida = self.pre_cargar()
        self.assertEqual((repetida.status_code, repetida.json()['id']), (200, datos['id']))
        self.assertEqual(FacturaRecibida.objects.count(), 1)
        # Avisa en Inicio y en Compras.
        _, avisos = panel.del_dia(self.encargada)
        self.assertIn('Tenés 1 factura recibida por WhatsApp/Correo lista para revisar y confirmar.',
                      [a['texto'] for a in avisos])
        f.entrar(self.client, self.encargada)
        compras = self.client.get(reverse('compras:compras'))
        self.assertContains(compras, 'Revisar y confirmar')
        self.assertContains(compras, 'WhatsApp')
        # Se abre ya escrita y se confirma.
        pantalla = self.client.get(datos['url'])
        self.assertContains(pantalla, 'llegó por WhatsApp')
        self.assertEqual(pantalla.context['datos']['fecha'], '2026-11-15')
        guardar = self.client.post(reverse('compras:recepcion'), {
            'factura': datos['id'], 'proveedor': str(self.prov.pk), 'tipo_comprobante': 'FACTURA_B',
            'punto_venta': '3', 'numero': '4521', 'fecha': '2026-11-15', 'vincular': '1', 'otros': '0',
            'token_operacion': pantalla.context['token'], 'renglones': pantalla.context['renglones_json']})
        self.assertRedirects(guardar, reverse('compras:compras'), fetch_redirect_response=False,
                             msg_prefix=str(getattr(guardar, 'context', None) and guardar.context.get('error')))
        factura = FacturaRecibida.objects.get(pk=datos['id'])
        self.assertEqual(factura.estado_factura, EstadoFacturaRecibida.CONFIRMADA)
        self.assertEqual(factura.id_compra.numero_comprobante, 4521)
        self.assertEqual(factura.id_usuario_resolucion, self.encargada)
        _, avisos = panel.del_dia(self.encargada)
        self.assertNotIn('FACTURAS_RECIBIDAS', [a['clave'] for a in avisos])
        # Ya confirmada, el enlace no la vuelve a ofrecer.
        self.assertContains(self.client.get(datos['url'], follow=True), 'ya se confirmó o se descartó')

    def test_n8n_puede_usar_el_lector_local_y_dejarla_en_la_bandeja(self):
        url = reverse('api_servicio:leer_factura')
        archivo = base64.b64encode(LectorTest.TEXTO.encode()).decode()
        self.assertEqual(self.client.post(url, '{}', content_type='application/json').status_code, 401)
        lectura = self.client.post(url, json.dumps({'nombre': 'factura.txt', 'tipo': 'text/plain', 'archivo': archivo,
                                                    'origen': 'CORREO', 'remitente': 'ventas@norte.com.ar'}),
                                   content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        self.assertEqual(lectura.status_code, 200)
        datos = lectura.json()
        self.assertEqual((len(datos['renglones']), datos['origen']), (2, 'CORREO'))
        bandeja = self.pre_cargar(datos).json()
        factura = FacturaRecibida.objects.get(pk=bandeja['id'])
        self.assertEqual((factura.origen_factura, factura.remitente), ('CORREO', 'ventas@norte.com.ar'))
        vacia = self.client.post(url, json.dumps({'nombre': 'x.txt', 'tipo': 'text/plain',
                                                  'archivo': base64.b64encode(b'hola').decode()}),
                                 content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        self.assertEqual(vacia.status_code, 422)

    def test_se_descarta_con_nombre(self):
        datos = self.pre_cargar().json()
        f.entrar(self.client, self.encargada)
        self.client.post(reverse('compras:descartar_factura', args=[datos['id']]))
        factura = FacturaRecibida.objects.get(pk=datos['id'])
        self.assertEqual((factura.estado_factura, factura.id_usuario_resolucion),
                         (EstadoFacturaRecibida.DESCARTADA, self.encargada))
        self.assertEqual(facturas.pendientes().count(), 0)


# ===========================================================================
#  Curación progresiva, salvataje y su impacto en la ganancia
# ===========================================================================
class CuracionYSalvatajeTest(Base):

    def test_la_compra_completa_el_costo_y_el_proveedor_de_lo_migrado_y_la_ganancia_lo_usa(self):
        from stock.reservados import proveedor_general
        general = proveedor_general()
        migrado = f.producto('Galletitas del Excel', prov=general, precio='1000', costo='0')
        f.comprar(self.admin, self.prov, (migrado, 20, '600'))
        migrado.refresh_from_db()
        self.assertEqual((migrado.precio_costo, migrado.id_proveedor), (D('600.00'), self.prov))
        turno = f.abrir_turno(self.admin)
        ventas.registrar(turno, self.admin, [ventas.Item(migrado, D('2'))], [ventas.Pago(f.efectivo())])
        hoy = timezone.localdate()
        del_dia = ganancia.de_ventas(ganancia.completadas(hoy, hoy))
        self.assertEqual((del_dia['facturado'], del_dia['costo'], del_dia['ganancia'], del_dia['sin_costo']),
                         (D('2000.00'), D('1200.00'), D('800.00'), 0))
        resumen = mensual.del_mes(hoy.year, hoy.month)
        self.assertEqual(resumen['bruta']['ganancia'], D('800.00'))

    def test_la_compra_de_salvataje_suma_stock_sin_tocar_costo_ni_proveedor(self):
        from compras import servicios
        otro = f.proveedor('Supermercado de la esquina', '30-71000000-6')
        antes = self.yerba.stock_actual
        compra = servicios.recepcionar(
            otro, self.admin, 'TICKET', 1, 77, [servicios.RenglonRecibido(self.yerba, D('3'), D('3500'), [])],
            ocasional=True)
        self.yerba.refresh_from_db()
        self.assertTrue(compra.compra_ocasional)
        self.assertEqual(self.yerba.stock_actual, antes + 3)
        self.assertEqual((self.yerba.precio_costo, self.yerba.id_proveedor), (D('2000.00'), self.prov))
        # Y no cuenta para «tres compras seguidas al mismo proveedor».
        for numero in (78, 79):
            servicios.recepcionar(otro, self.admin, 'TICKET', 1, numero,
                                  [servicios.RenglonRecibido(self.yerba, D('1'), D('3500'), [])], ocasional=True)
        servicios.recepcionar(otro, self.admin, 'TICKET', 1, 80,
                              [servicios.RenglonRecibido(self.yerba, D('1'), D('2100'), [])])
        self.yerba.refresh_from_db()
        self.assertEqual(self.yerba.id_proveedor, self.prov)
        self.assertEqual(self.yerba.precio_costo, D('2100.00'))

    def test_la_pantalla_manda_la_marca_de_salvataje(self):
        f.entrar(self.client, self.admin)
        pantalla = self.client.get(reverse('compras:recepcion'))
        renglones = json.dumps([{'producto': self.yerba.pk, 'cantidad': '2', 'costo': '3600', 'lotes': []}])
        self.client.post(reverse('compras:recepcion'), {
            'proveedor': str(self.prov.pk), 'tipo_comprobante': 'TICKET', 'punto_venta': '1', 'numero': '55',
            'ocasional': '1', 'otros': '0', 'confirmo_costos': '1', 'token_operacion': pantalla.context['token'],
            'renglones': renglones})
        compra = Compra.objects.get(numero_comprobante=55)
        self.assertTrue(compra.compra_ocasional)
        self.yerba.refresh_from_db()
        self.assertEqual(self.yerba.precio_costo, D('2000.00'))


class CantidadesRedondasTest(Base):

    def test_diez_unidades_no_se_escriben_como_1e1(self):
        from compras import servicios
        from compras.precarga import reconocer
        borrador, _ = reconocer({'cuit_proveedor': '30-71234567-1', 'renglones': [
            {'codigo_barras': '7790070012345', 'cantidad': '10', 'costo_unitario': '2000'}]})
        self.assertEqual(borrador['r'][0][1], '10')
        orden = servicios.emitir_orden(self.prov, self.admin, [
            {'producto': self.yerba, 'cantidad': D('100'), 'precio_unitario': D('2000')}])
        servicios.enviar_orden(orden)
        f.entrar(self.client, self.admin)
        planilla = self.client.get(reverse('compras:recepcion'), {'orden': orden.pk})
        self.assertEqual(json.loads(planilla.context['renglones_json'])[0]['cantidad'], '100')


class FotoConOCRTest(TestCase):
    """La foto se lee con Tesseract, sin internet (DD-98). Si el equipo no lo
    tiene, la prueba se saltea: el contenedor lo trae desde el Dockerfile."""

    def test_una_foto_de_la_factura_se_lee(self):
        import io
        import shutil
        import unittest

        from PIL import Image, ImageDraw, ImageFont

        from compras import facturas, lector_facturas

        if not shutil.which('tesseract'):
            raise unittest.SkipTest('Este equipo no tiene Tesseract.')
        try:
            letra = ImageFont.truetype('DejaVuSansMono.ttf', 22)
        except OSError:
            letra = ImageFont.load_default()
        lineas = facturas.factura_de_muestra().splitlines()
        imagen = Image.new('RGB', (1500, 40 + 32 * len(lineas)), 'white')
        lapiz = ImageDraw.Draw(imagen)
        for i, linea in enumerate(lineas):
            lapiz.text((20, 20 + 32 * i), linea, font=letra, fill='black')
        foto = io.BytesIO()
        imagen.save(foto, 'PNG')
        datos, origen = lector_facturas.leer(foto.getvalue(), 'image/png', 'factura.png')
        self.assertEqual(origen, 'local')
        self.assertEqual(datos['tipo_comprobante'], 'FACTURA_A')
        self.assertEqual(datos['punto_venta'].lstrip('0'), '3')
        self.assertIn('7790000000017', [r['codigo_barras'] for r in datos['renglones']])
