"""La foto de un producto (DD-83)."""
import io
import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from nucleo.tests import fabrica
from stock import imagenes
from stock.models import Producto

MEDIA = tempfile.mkdtemp(prefix='cafeto-media-')


def foto(lado=2400, formato='JPEG'):
    salida = io.BytesIO()
    Image.new('RGB', (lado, lado // 2), (200, 120, 40)).save(salida, formato)
    return SimpleUploadedFile('foto.jpg', salida.getvalue(), content_type='image/jpeg')


@override_settings(MEDIA_ROOT=MEDIA, CAFETO_URL_PUBLICA='https://cafeto.ejemplo.com')
class FotoDeProductoTest(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.admin = fabrica.usuario('30111250', 'Administrador')
        fabrica.entrar(self.client, self.admin)
        self.medialuna = fabrica.producto('Medialuna de prueba', tipo='ELABORADO')
        self.url = reverse('stock:producto_foto', args=[self.medialuna.pk])

    def test_la_foto_subida_se_achica_y_queda_fuera_de_la_base(self):
        self.client.post(self.url, {'foto': foto()})
        self.medialuna.refresh_from_db()
        self.assertTrue(self.medialuna.imagen.startswith('productos/'))
        with Image.open(f'{MEDIA}/{self.medialuna.imagen}') as guardada:
            self.assertEqual(guardada.format, 'WEBP')
            self.assertLessEqual(max(guardada.size), imagenes.LADO_MAXIMO)

    def test_se_sirve_sin_sesion_para_la_app(self):
        self.client.post(self.url, {'foto': foto(400)})
        self.medialuna.refresh_from_db()
        self.client.logout()
        respuesta = self.client.get(imagenes.url_de(self.medialuna))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.client.get('/media/otra-cosa/secreto.txt').status_code, 302)

    def test_cambiar_la_foto_borra_la_anterior(self):
        self.client.post(self.url, {'foto': foto(400)})
        primera = Producto.objects.get(pk=self.medialuna.pk).imagen
        self.client.post(self.url, {'foto': foto(500)})
        import os
        self.assertFalse(os.path.exists(f'{MEDIA}/{primera}'))
        self.client.post(self.url, {'quitar': '1'})
        self.assertIsNone(Producto.objects.get(pk=self.medialuna.pk).imagen)

    def test_un_archivo_que_no_es_foto_se_rechaza(self):
        respuesta = self.client.post(
            self.url, {'foto': SimpleUploadedFile('virus.jpg', b'esto no es una foto')}, follow=True)
        self.assertContains(respuesta, 'no es una foto')
        self.assertIsNone(Producto.objects.get(pk=self.medialuna.pk).imagen)

    def test_la_direccion_externa_tiene_que_ser_https(self):
        for mala in ('http://sitio.com/a.jpg', 'javascript:alert(1)', 'ftp://x/y.png', ''):
            with self.subTest(mala=mala), self.assertRaises(ValidationError):
                imagenes.poner_url(self.medialuna, mala)
        imagenes.poner_url(self.medialuna, 'https://images.openfoodfacts.org/a.jpg')
        self.assertEqual(imagenes.url_de(self.medialuna), 'https://images.openfoodfacts.org/a.jpg')

    def test_la_base_tampoco_acepta_otra_cosa(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Producto.objects.filter(pk=self.medialuna.pk).update(imagen='http://inseguro/a.jpg')

    def test_la_app_recibe_la_direccion_completa(self):
        from api_movil import catalogo
        self.assertIsNone(catalogo.imagen(self.medialuna))
        self.client.post(self.url, {'foto': foto(400)})
        self.medialuna.refresh_from_db()
        self.assertTrue(catalogo.imagen(self.medialuna).startswith(
            'https://cafeto.ejemplo.com/media/productos/'))

    def test_sin_permiso_no_se_cambia(self):
        self.client.logout()
        fabrica.entrar(self.client, fabrica.usuario('40111251'))
        self.client.post(self.url, {'foto': foto(400)})
        self.assertIsNone(Producto.objects.get(pk=self.medialuna.pk).imagen)
