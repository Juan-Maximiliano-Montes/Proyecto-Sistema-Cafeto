"""Importar productos desde Excel: altas nuevas y actualización de precios.

La identidad de un producto es la terna marca + nombre + presentación
(DD-55), comparada sin mayúsculas, acentos ni puntuación. Si la fila ya
existe, se actualiza lo que trae (precio, costo, mínimo, proveedor,
código); si no, es un alta.

El stock NO se importa: entra por una compra o por el conteo de
inventario inicial, que dejan su movimiento y su lote. Una planilla que
"pone" stock sería una forma de saltearse el libro de movimientos.

Una marca que no existe se crea (es sólo un nombre); un rubro que no
existe es un error, porque el rubro trae reglas (la rebaja por
vencimiento) que alguien tiene que decidir.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError

from compras.models import Proveedor
from nucleo.formularios import leer_numero
from nucleo.importar import Columna, Importador, opcion, registrar, si_no
from nucleo.validaciones import clave_normalizada, normalizar_cuit
from stock.models import Marca, Producto, Rubro, TipoProducto, UnidadMedida
from nucleo.templatetags.cafeto import pesos

CAMPOS_QUE_SE_ACTUALIZAN = ('precio_venta', 'precio_costo', 'stock_minimo',
                            'id_proveedor_id', 'codigo_barras')


@registrar
class ImportadorProductos(Importador):
    clave = 'productos'
    titulo = 'Productos'
    permiso = 'importar_productos'
    modulo = 'STOCK'
    volver = 'stock:productos_lista'
    columnas = (
        Columna('nombre', 'Nombre', True, 'Sin la marca ni el contenido.', 'Alfajor de maicena',
                ('producto', 'nombre del producto')),
        Columna('marca', 'Marca', True, 'Si no existe, se crea.', 'Havanna'),
        Columna('rubro', 'Rubro', True, 'Tiene que existir en el sistema.', 'Minisuper'),
        Columna('tipo', 'Tipo', False, 'Reventa, Insumo, Elaborado, Producido o Combo. Vacío = Reventa.',
                'Reventa', ('tipo de producto',)),
        Columna('contenido', 'Contenido', False, 'Número. Vacío = 1.', '1'),
        Columna('unidad', 'Unidad', False, 'UNIDAD, KG, G, L o ML. Vacío = UNIDAD.', 'UNIDAD',
                ('unidad de medida',)),
        Columna('precio', 'Precio de venta', False, 'Obligatorio salvo para los insumos.', '1200',
                ('precio', 'precio venta')),
        Columna('costo', 'Costo', False, 'Precio de costo. Vacío = no se toca (o 0 en un alta).', '700',
                ('precio de costo', 'costo unitario')),
        Columna('minimo', 'Stock mínimo', False, 'Para el aviso de faltantes.', '5', ('minimo',)),
        Columna('proveedor', 'Proveedor', False, 'CUIT o nombre. Obligatorio para reventa e insumos.',
                '30-71234567-1'),
        Columna('codigo', 'Código de barras', False, 'El EAN del envase.', '7790000000000',
                ('codigo', 'ean')),
        Columna('fraccionable', 'Fraccionable', False, 'Sí si se vende por peso (KG o L, contenido 1).', 'No'),
        Columna('vence', 'Controla vencimiento', False, 'Sí si va por lote con fecha de vencimiento.', 'No',
                ('vencimiento',)),
    )

    def preparar(self):
        self.rubros = {clave_normalizada(r.nombre_rubro): r for r in Rubro.objects.filter(borrado_logico=False)}
        self.marcas = {clave_normalizada(m.nombre_marca): m for m in Marca.objects.all()}
        proveedores = list(Proveedor.objects.filter(borrado_logico=False))
        self.proveedores_por_cuit = {p.cuit_proveedor: p for p in proveedores}
        self.proveedores_por_nombre = {clave_normalizada(p.nombre_proveedor): p for p in proveedores}

    def _proveedor(self, texto):
        if not (texto or '').strip():
            return None
        try:
            proveedor = self.proveedores_por_cuit.get(normalizar_cuit(texto))
        except (ValidationError, ValueError, TypeError):
            proveedor = None
        proveedor = proveedor or self.proveedores_por_nombre.get(clave_normalizada(texto))
        if proveedor is None:
            raise ValidationError(f'No hay ningún proveedor vigente «{texto}».')
        return proveedor

    def validar(self, resultado, vistos):
        d = resultado.datos
        errores = resultado.errores
        nombre = (d.get('nombre') or '').strip()
        rubro = self.rubros.get(clave_normalizada(d.get('rubro') or ''))
        if not rubro:
            errores.append(f'El rubro «{d.get("rubro")}» no existe: crealo primero en Rubros.')
        marca_texto = (d.get('marca') or '').strip()
        marca = self.marcas.get(clave_normalizada(marca_texto))
        try:
            tipo = opcion(d.get('tipo'), TipoProducto.choices, 'El tipo', TipoProducto.REVENTA)
            unidad = opcion(d.get('unidad'), UnidadMedida.choices, 'La unidad', UnidadMedida.UNIDAD)
            contenido = leer_numero(d.get('contenido') or '1', 3)
            if contenido is None:
                raise ValidationError(f'El contenido «{d.get("contenido")}» no es un número.')
            proveedor = self._proveedor(d.get('proveedor'))
            fraccionable = si_no(d.get('fraccionable'), 'Fraccionable')
            controla = si_no(d.get('vence'), 'Controla vencimiento')
        except ValidationError as error:
            errores.extend(error.messages)
            return
        numeros = {}
        for clave, etiqueta, decimales in (('precio', 'El precio', 2), ('costo', 'El costo', 2),
                                           ('minimo', 'El stock mínimo', 3)):
            texto = (d.get(clave) or '').strip()
            if texto:
                numeros[clave] = leer_numero(texto, decimales)
                if numeros[clave] is None:
                    errores.append(f'{etiqueta} «{texto}» no es un número.')
        if errores:
            return

        identidad = (clave_normalizada(marca_texto), clave_normalizada(nombre), contenido, unidad)
        if identidad in vistos:
            errores.append(f'Está repetido: es el mismo producto que la fila {vistos[identidad]}.')
            return
        vistos[identidad] = resultado.numero

        existente = None
        if marca is not None:
            candidatos = Producto.objects.filter(id_marca=marca, contenido=contenido, unidad_medida=unidad)
            existente = next((p for p in candidatos
                              if clave_normalizada(p.nombre_producto) == clave_normalizada(nombre)), None)

        if existente is not None:
            producto = Producto.objects.get(pk=existente.pk)
            antes = {c: getattr(producto, c) for c in CAMPOS_QUE_SE_ACTUALIZAN}
            if 'precio' in numeros:
                producto.precio_venta = numeros['precio']
            if 'costo' in numeros:
                producto.precio_costo = numeros['costo']
            if 'minimo' in numeros:
                producto.stock_minimo = numeros['minimo']
            if proveedor is not None:
                producto.id_proveedor = proveedor
            if (d.get('codigo') or '').strip():
                producto.codigo_barras = d['codigo'].strip()
            if producto.tipo_producto != tipo and (d.get('tipo') or '').strip():
                errores.append('El tipo de un producto existente no se cambia desde una planilla.')
                return
            try:
                producto.full_clean(exclude=['id_marca', 'id_rubro'])
            except ValidationError as error:
                errores.extend(error.messages)
                return
            cambios = [c for c in CAMPOS_QUE_SE_ACTUALIZAN if getattr(producto, c) != antes[c]]
            resultado.accion = 'CAMBIO' if cambios else 'SIN CAMBIOS'
            resultado.descripcion = str(producto) + (
                ': ' + ', '.join(self._describir(c, antes[c], getattr(producto, c)) for c in cambios)
                if cambios else '')
            resultado.preparado = (producto, None)
            return

        producto = Producto(
            nombre_producto=nombre, id_marca=marca, id_rubro=rubro, tipo_producto=tipo,
            contenido=contenido, unidad_medida=unidad, precio_venta=numeros.get('precio', Decimal('0')),
            precio_costo=numeros.get('costo', Decimal('0')), stock_minimo=numeros.get('minimo', Decimal('0')),
            id_proveedor=proveedor, codigo_barras=(d.get('codigo') or '').strip() or None,
            fraccionable=fraccionable, controla_vencimiento=controla)
        try:
            producto.full_clean(exclude=['id_marca'] if marca is None else None)
        except ValidationError as error:
            errores.extend(error.messages)
            return
        resultado.accion = 'ALTA'
        resultado.descripcion = (f'{marca_texto} · {nombre} · {producto.presentacion}'
                                 + ('' if marca else ' (marca nueva)'))
        resultado.preparado = (producto, None if marca else marca_texto)

    @staticmethod
    def _describir(campo, antes, despues):
        nombres = {'precio_venta': 'precio', 'precio_costo': 'costo', 'stock_minimo': 'mínimo',
                   'id_proveedor_id': 'proveedor', 'codigo_barras': 'código'}
        if campo in ('precio_venta', 'precio_costo'):
            return f'{nombres[campo]} {pesos(antes)} → {pesos(despues)}'
        if campo == 'id_proveedor_id':
            return 'cambia el proveedor'
        return f'{nombres[campo]} {antes or "—"} → {despues or "—"}'

    def instancia_de(self, resultado):
        return resultado.preparado[0]

    def aplicar(self, resultado, usuario):
        producto, marca_nueva = resultado.preparado
        if marca_nueva:
            clave = clave_normalizada(marca_nueva)
            marca = self.marcas.get(clave)
            if marca is None:
                marca = Marca(nombre_marca=marca_nueva)
                marca.full_clean()
                marca.save()
                self.marcas[clave] = marca
            producto.id_marca = marca
            producto.full_clean()
        producto.save()
