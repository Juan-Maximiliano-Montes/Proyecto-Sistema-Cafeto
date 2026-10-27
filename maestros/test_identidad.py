"""Pruebas de la identidad del producto y de los nombres de personas.

Dos cambios de normalización que se hicieron después de usar el sistema y
encontrar que dejaba entrar datos que no debía:

* **DD-55** — la marca y la presentación salen del nombre del producto y
  pasan a ser columnas. Lo que identifica al artículo es la terna
  marca + nombre + presentación.
* **DD-54** — CLIENTES separa apellido y nombre, como ya hacía USUARIOS.

Viven en su propio archivo y no en ``tests.py`` porque prueban el cambio de
modelo, no una regla de la sección 4.4.V. El runner los encuentra igual:
busca cualquier ``test*.py``.
"""
from decimal import Decimal

from django.db import IntegrityError, transaction

from operaciones.models import Cliente

from .models import CategoriaFidelizacion, Marca, Producto
from .tests import BaseMaestros


class IdentidadDelProductoTest(BaseMaestros):
    """La terna marca + nombre + presentación identifica al producto.

    Antes el nombre cargaba los tres datos en un solo campo, y por eso el
    sistema dejaba entrar el mismo artículo dos veces con el nombre escrito
    apenas distinto: "Café molido La morenita" y "Café molido La Morenita
    500 g" son cadenas diferentes, así que el control de duplicados —que
    compara nombres— no tenía nada que objetar. Esta clase prueba que con
    la marca y el contenido como columnas eso ya no pasa.
    """

    def datos(self, **extra):
        base = {
            'id_marca': self.marca.pk,
            'nombre_producto': 'Café molido',
            'contenido': '500',
            'unidad_medida': 'G',
            'id_rubro': self.rubro.pk,
            'id_proveedor': self.proveedor.pk,
            'precio_venta': '8500',
            'precio_costo': '5200',
            'stock_minimo': '6',
        }
        base.update(extra)
        return base

    # -- el caso que apareció usando el sistema ------------------------
    def test_el_mismo_producto_cargado_dos_veces_se_rechaza(self):
        self.crear('productos', self.datos())
        self.assertEqual(Producto.objects.count(), 1)

        respuesta = self.crear('productos', self.datos())
        self.assertEqual(Producto.objects.count(), 1)
        # El aviso va EN EL CAMPO, no como cartel suelto: el modelo lo
        # levanta atado a 'nombre_producto' y el formulario vuelve con los
        # datos cargados.
        errores = ' '.join(self.errores_de_campo(respuesta, 'nombre_producto'))
        self.assertRegex(errores, 'ya se encuentra registrado|Ya existe')

    def test_tambien_se_rechaza_con_otra_mayuscula_o_espacios(self):
        self.crear('productos', self.datos())
        self.crear('productos', self.datos(nombre_producto='CAFE  MOLIDO'))
        self.assertEqual(Producto.objects.count(), 1)

    # -- lo que SÍ son productos distintos -----------------------------
    def test_otra_presentacion_de_la_misma_marca_es_otro_producto(self):
        self.crear('productos', self.datos())
        self.crear('productos', self.datos(contenido='250'))
        self.assertEqual(Producto.objects.count(), 2)

    def test_la_misma_denominacion_de_otra_marca_es_otro_producto(self):
        otra = Marca.objects.create(nombre_marca='Cabrales')
        self.crear('productos', self.datos())
        self.crear('productos', self.datos(id_marca=otra.pk))
        self.assertEqual(Producto.objects.count(), 2)

    # -- la presentación ------------------------------------------------
    def test_el_contenido_tiene_que_ser_mayor_que_cero(self):
        self.crear('productos', self.datos(contenido='0'))
        self.assertFalse(Producto.objects.exists())

    def test_la_base_tambien_rechaza_el_contenido_cero(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Producto.objects.create(
                nombre_producto='Café', id_rubro=self.rubro,
                id_proveedor=self.proveedor, id_marca=self.marca,
                contenido=Decimal('0'), precio_venta=Decimal('10'),
                precio_costo=Decimal('1'))

    def test_como_se_muestra_el_producto(self):
        self.crear('productos', self.datos())
        producto = Producto.objects.get()
        self.assertEqual(producto.presentacion, '500 G')
        self.assertEqual(str(producto), 'La Morenita · Café molido · 500 G')

    def test_la_presentacion_no_arrastra_ceros(self):
        self.crear('productos', self.datos(contenido='1,5', unidad_medida='L'))
        self.assertEqual(Producto.objects.get().presentacion, '1,5 L')

    # -- la marca como maestra ------------------------------------------
    def test_alta_de_marca_y_duplicado_normalizado(self):
        self.crear('marcas', {'nombre_marca': 'Cabrales'})
        self.assertTrue(Marca.objects.filter(nombre_marca='Cabrales').exists())

        respuesta = self.crear('marcas', {'nombre_marca': 'CABRALES'})
        self.assertEqual(Marca.objects.filter(
            nombre_marca__iexact='cabrales').count(), 1)
        errores = ' '.join(self.errores_de_campo(respuesta, 'nombre_marca'))
        self.assertRegex(errores, 'ya se encuentra registrado|Ya existe')

    def test_no_se_da_de_baja_una_marca_con_productos_vigentes(self):
        self.crear('productos', self.datos())
        respuesta = self.client.post(
            self.url('marcas', 'eliminar', self.marca.pk), follow=True)
        self.marca.refresh_from_db()
        self.assertFalse(self.marca.borrado_logico)
        self.assertIn('producto(s) vigente(s)',
                      ' '.join(self.mensajes(respuesta)))

    def test_el_combo_de_marcas_no_ofrece_las_dadas_de_baja(self):
        baja = Marca.objects.create(nombre_marca='Discontinuada',
                                    borrado_logico=True)
        self.crear('productos', self.datos(id_marca=baja.pk))
        self.assertFalse(Producto.objects.exists())

    def test_no_se_reactiva_un_producto_con_la_marca_de_baja(self):
        producto = self.producto_de_prueba(borrado_logico=True)
        Marca.objects.filter(pk=self.marca.pk).update(borrado_logico=True)
        respuesta = self.client.post(
            self.url('productos', 'reactivar', producto.pk), follow=True)
        producto.refresh_from_db()
        self.assertTrue(producto.borrado_logico)
        self.assertIn('marca', ' '.join(self.mensajes(respuesta)))


class NombreYApellidoTest(BaseMaestros):
    """CLIENTES guarda apellido y nombre aparte, igual que USUARIOS (DD-54)."""

    def categoria(self):
        return CategoriaFidelizacion.objects.get(nombre_categoria='Gold')

    def test_el_cliente_se_muestra_por_apellido(self):
        cliente = Cliente.objects.create(
            id_categoria=self.categoria(), apellido_cliente='Fernández',
            nombre_cliente='Lucía', qr_cliente='QR-0100')
        self.assertEqual(str(cliente), 'Fernández, Lucía')

    def test_el_listado_de_clientes_se_ordena_por_apellido(self):
        gold = self.categoria()
        for apellido, nombre, qr in [('Zalazar', 'Ana', 'QR-1'),
                                     ('Acosta', 'Bruno', 'QR-2'),
                                     ('Medina', 'Carla', 'QR-3')]:
            Cliente.objects.create(id_categoria=gold, apellido_cliente=apellido,
                                   nombre_cliente=nombre, qr_cliente=qr)
        self.assertEqual(
            list(Cliente.objects.values_list('apellido_cliente', flat=True)),
            ['Acosta', 'Medina', 'Zalazar'])

    def test_la_base_exige_los_dos_datos(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cliente.objects.create(
                id_categoria=self.categoria(), apellido_cliente='   ',
                nombre_cliente='Lucía', qr_cliente='QR-9')

    def test_el_usuario_admite_un_apellido_compuesto(self):
        """50 caracteres: con 30 un apellido compuesto no entraba."""
        usuario = self._crear_usuario(
            '40000003', 'Rodríguez de la Fuente Villalba', 'María Soledad',
            self.perfil_admin)
        usuario.refresh_from_db()
        self.assertEqual(usuario.apellido, 'Rodríguez De La Fuente Villalba')
