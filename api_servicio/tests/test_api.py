"""La API de servicio para automatizaciones (DD-84)."""
import json
from decimal import Decimal as D

from django.test import TestCase, override_settings
from django.urls import reverse

from compras import servicios as compras
from nucleo.tests import fabrica
from stock.models import Producto

TOKEN = 'credencial-de-prueba-larga-0123456789abcdef'


@override_settings(CAFETO_N8N_TOKEN=TOKEN)
class ApiDeServicioTest(TestCase):

    def pedir(self, nombre, *args, token=TOKEN, cuerpo=None):
        cabeceras = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
        url = reverse(f'api_servicio:{nombre}', args=args)
        if cuerpo is None:
            return self.client.get(url, **cabeceras)
        return self.client.post(url, json.dumps(cuerpo), content_type='application/json', **cabeceras)

    def test_sin_credencial_o_con_otra_no_entra(self):
        self.assertEqual(self.pedir('resumen', token=None).status_code, 401)
        self.assertEqual(self.pedir('resumen', token='otra-cosa').status_code, 401)
        self.assertEqual(self.pedir('resumen', token=TOKEN + 'x').status_code, 401)

    @override_settings(CAFETO_N8N_TOKEN='')
    def test_sin_configurar_no_existe(self):
        self.assertEqual(self.pedir('resumen').status_code, 503)

    @override_settings(CAFETO_N8N_TOKEN='corta')
    def test_una_credencial_corta_no_la_habilita(self):
        self.assertEqual(self.pedir('resumen', token='corta').status_code, 503)

    def test_una_sesion_de_usuario_no_alcanza(self):
        fabrica.entrar(self.client, fabrica.usuario('30111260', 'Administrador'))
        self.assertEqual(self.pedir('resumen', token=None).status_code, 401)

    def test_el_resumen_del_dia(self):
        datos = self.pedir('resumen').json()
        self.assertIn('numeros', datos)
        self.assertIn('avisos', datos)
        self.assertTrue(any(n['titulo'] == 'Vendido hoy' for n in datos['numeros']))

    def test_las_ordenes_emitidas_y_su_pdf(self):
        usuario = fabrica.usuario('30111261', 'Administrador')
        proveedor = fabrica.proveedor()
        agua = fabrica.producto('Agua de prueba', prov=proveedor)
        orden = compras.emitir_orden(proveedor, usuario, [
            {'producto': agua, 'cantidad': D('6'), 'precio_unitario': D('500')}])
        self.assertEqual(self.pedir('ordenes_emitidas').json()['ordenes'], [])   # todavía PENDIENTE
        self.assertEqual(self.pedir('orden_pdf', orden.pk).status_code, 404)
        compras.enviar_orden(orden)
        lista = self.pedir('ordenes_emitidas').json()['ordenes']
        self.assertEqual([o['id'] for o in lista], [orden.pk])
        self.assertEqual(lista[0]['proveedor']['nombre'], proveedor.nombre_proveedor)
        pdf = self.pedir('orden_pdf', orden.pk)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))

    def test_poner_la_foto_por_codigo_de_barras(self):
        proveedor = fabrica.proveedor()
        galleta = fabrica.producto('Galleta de prueba', prov=proveedor)
        Producto.objects.filter(pk=galleta.pk).update(codigo_barras='7790895000997')
        sin_foto = self.pedir('productos_sin_foto').json()['productos']
        self.assertEqual([p['codigo_barras'] for p in sin_foto], ['7790895000997'])
        mala = self.pedir('foto_de_producto', galleta.pk, cuerpo={'url': 'http://inseguro/a.jpg'})
        self.assertEqual(mala.status_code, 400)
        buena = self.pedir('foto_de_producto', galleta.pk,
                           cuerpo={'url': 'https://images.openfoodfacts.org/a.jpg'})
        self.assertEqual(buena.status_code, 200)
        self.assertEqual(Producto.objects.get(pk=galleta.pk).imagen,
                         'https://images.openfoodfacts.org/a.jpg')
        self.assertEqual(self.pedir('productos_sin_foto').json()['productos'], [])

    def test_subir_foto_binaria_desde_n8n_genera_url_nueva_al_reemplazar(self):
        import base64
        import io
        import shutil
        import tempfile

        from PIL import Image

        media_tmp = tempfile.mkdtemp(prefix='cafeto-n8n-img-')
        self.addCleanup(shutil.rmtree, media_tmp, True)
        proveedor = fabrica.proveedor()
        galleta = fabrica.producto('Galleta artesanal', prov=proveedor)

        def imagen_b64(color):
            buf = io.BytesIO()
            Image.new('RGB', (120, 120), color).save(buf, 'JPEG')
            return base64.b64encode(buf.getvalue()).decode('ascii')

        with override_settings(MEDIA_ROOT=media_tmp):
            r1 = self.pedir('foto_de_producto', galleta.pk,
                            cuerpo={'archivo': imagen_b64((200, 50, 50)), 'nombre': 'f1.jpg'})
            self.assertEqual(r1.status_code, 200)
            primera = r1.json()['imagen']
            self.assertTrue(primera.startswith('productos/'))

            r2 = self.pedir('foto_de_producto', galleta.pk,
                            cuerpo={'archivo': imagen_b64((50, 200, 50)), 'nombre': 'f1.jpg'})
            self.assertEqual(r2.status_code, 200)
            segunda = r2.json()['imagen']
            self.assertNotEqual(primera, segunda)

    def test_no_hay_nada_mas_que_eso(self):
        """La credencial de servicio no abre el resto del sistema."""
        for ruta in ('/stock/productos/', '/ventas/caja/', '/usuarios/'):
            respuesta = self.client.get(ruta, HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
            self.assertEqual(respuesta.status_code, 302, ruta)
