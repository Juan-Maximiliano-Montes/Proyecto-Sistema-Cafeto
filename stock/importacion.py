"""Importar productos desde Excel: altas nuevas y actualización de precios.

Lee la planilla modelo del sistema y también la exportación de artículos de
Fácil Virtual tal cual sale (Código de barras | Descripción | Rubro |
Precio Costo | % IVA | % Utilidad | Precio Venta | Proveedor | Stock...).

Cómo se reconoce un producto que ya está: primero por el CÓDIGO; si la fila
no trae código, por la terna marca + nombre + presentación (DD-55).

Lo que la planilla no trae se completa con valores que dejan la fila válida
(DD-86): sin marca → «Genérica»; sin proveedor → «Proveedor General (Por
asignar)»; un rubro que no existe se crea.

El stock NO se importa: entra por una compra o por el conteo de inventario
inicial, que dejan su movimiento y su lote. Una planilla que "pone" stock
sería una forma de saltearse el libro de movimientos. Si la planilla trae
una columna de stock, se avisa una vez y se ignora.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError

from compras.models import Proveedor
from nucleo.formularios import leer_numero
from nucleo.importar import Columna, Importador, Opcion, opcion, registrar, si_no
from nucleo.validaciones import clave_normalizada, es_gtin, normalizar_cuit, sanear_codigo
from stock.models import (
    ALICUOTAS_IVA, TIPOS_CON_PROVEEDOR, Marca, Producto, Rubro, TipoProducto, UnidadMedida)
from nucleo.templatetags.cafeto import pesos

CAMPOS_QUE_SE_ACTUALIZAN = ('precio_venta', 'precio_costo', 'alicuota_iva',
                            'stock_minimo', 'id_proveedor_id', 'codigo_barras')


@registrar
class ImportadorProductos(Importador):
    clave = 'productos'
    titulo = 'Productos'
    permiso = 'importar_productos'
    modulo = 'STOCK'
    volver = 'stock:productos_lista'
    columnas = (
        Columna('nombre', 'Nombre', True, 'Como se lo busca en el mostrador.', 'Alfajor de maicena',
                ('producto', 'nombre del producto', 'descripcion', 'articulo', 'detalle')),
        Columna('rubro', 'Rubro', True, 'Si no existe, se crea.', 'Minisuper',
                ('categoria', 'familia')),
        Columna('marca', 'Marca', False, 'Vacía = «Genérica». Si no existe, se crea.', 'Havanna'),
        Columna('tipo', 'Tipo', False, 'Reventa, Insumo, Elaborado, Producido o Combo. Vacío = Reventa.',
                'Reventa', ('tipo de producto',)),
        Columna('contenido', 'Contenido', False, 'Número. Vacío = 1.', '1'),
        Columna('unidad', 'Unidad', False, 'UNIDAD, KG, G, L o ML. Vacío = UNIDAD.', 'UNIDAD',
                ('unidad de medida',)),
        Columna('precio', 'Precio de venta', False, 'Obligatorio salvo para los insumos.', '1200',
                ('precio', 'precio venta', 'pvp', 'precio final')),
        Columna('costo', 'Costo', False, 'Precio de costo. Vacío = no se toca (o 0 en un alta).', '700',
                ('precio de costo', 'precio costo', 'costo unitario')),
        Columna('iva', '% IVA', False, '0, 10,5 o 21. Vacío = 0.', '21', ('iva', 'alicuota', 'alicuota iva')),
        Columna('minimo', 'Stock mínimo', False, 'Para el aviso de faltantes.', '5', ('minimo',)),
        Columna('proveedor', 'Proveedor', False,
                'CUIT o nombre. Vacío = «Proveedor General (Por asignar)».', '30-71234567-1'),
        Columna('codigo', 'Código de barras', False,
                'El del envase o uno propio: de 3 a 30 dígitos.', '7790000000003',
                ('codigo', 'ean', 'codigo de barra', 'cod barras', 'codigo interno')),
        Columna('fraccionable', 'Fraccionable', False, 'Sí si se vende por peso (KG o L, contenido 1).', 'No'),
        Columna('vence', 'Controla vencimiento', False, 'Sí si va por lote con fecha de vencimiento.', 'No',
                ('vencimiento',)),
        Columna('stock', 'Stock', False,
                'NO se importa: el stock entra por una compra o por el conteo de inventario.', '',
                ('stock actual', 'existencia', 'existencias')),
    )

    def ejemplo_de(self, columna):
        # El ejemplo usa un proveedor y un rubro que existen de verdad: una
        # planilla modelo cuyo propio ejemplo da error no enseña nada.
        if columna.clave == 'proveedor':
            from compras.models import Proveedor
            real = (Proveedor.objects.filter(borrado_logico=False)
                    .order_by('nombre_proveedor').values_list('cuit_proveedor', flat=True).first())
            return real or columna.ejemplo
        if columna.clave == 'rubro':
            from stock.models import Rubro
            real = (Rubro.objects.filter(borrado_logico=False, nombre_rubro=columna.ejemplo)
                    .values_list('nombre_rubro', flat=True).first()
                    or Rubro.objects.filter(borrado_logico=False)
                    .order_by('nombre_rubro').values_list('nombre_rubro', flat=True).first())
            return real or columna.ejemplo
        return columna.ejemplo

    def preparar(self):
        from stock.reservados import marca_generica, proveedor_general
        self.rubros = {clave_normalizada(r.nombre_rubro): r for r in Rubro.objects.filter(borrado_logico=False)}
        self.rubros_de_baja = {clave_normalizada(r.nombre_rubro) for r in Rubro.objects.filter(borrado_logico=True)}
        self.marcas = {clave_normalizada(m.nombre_marca): m for m in Marca.objects.all()}
        self.generica = marca_generica()
        self.general = proveedor_general()
        proveedores = list(Proveedor.objects.filter(borrado_logico=False))
        self.proveedores_por_cuit = {p.cuit_proveedor: p for p in proveedores}
        self.proveedores_por_nombre = {clave_normalizada(p.nombre_proveedor): p for p in proveedores}
        # Dos índices del catálogo, armados UNA vez: con miles de filas,
        # buscar cada una en la base hacía la vista previa interminable.
        self.por_codigo, self.por_identidad = {}, {}
        for pk, codigo, marca, nombre, contenido, unidad in Producto.objects.values_list(
                'pk', 'codigo_barras', 'id_marca_id', 'nombre_producto', 'contenido', 'unidad_medida'):
            if codigo:
                self.por_codigo[codigo] = pk
            self.por_identidad[(marca, clave_normalizada(nombre), contenido, unidad)] = (pk, codigo)
        self.productos = Producto.objects.select_related('id_marca').in_bulk()
        self.rubros_nuevos = {}

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

    def _rubro(self, texto, resultado):
        """El rubro de la fila: el que existe o uno nuevo por crear."""
        texto = ' '.join((texto or '').split())
        clave = clave_normalizada(texto)
        if not clave:
            raise ValidationError('Falta el rubro.')
        if clave in self.rubros:
            return self.rubros[clave], None
        if clave in self.rubros_de_baja:
            raise ValidationError(
                f'El rubro «{texto}» está dado de baja: reactivalo en Rubros o cambialo en la planilla.')
        if clave not in self.rubros_nuevos:
            nuevo = Rubro(nombre_rubro=texto)
            nuevo.full_clean(validate_unique=False, validate_constraints=False)
            self.rubros_nuevos[clave] = nuevo.nombre_rubro
            resultado.avisos.append(f'El rubro «{nuevo.nombre_rubro}» no existía: se crea.')
        return None, self.rubros_nuevos[clave]

    opciones = (Opcion(
        'stock_inicial', 'Cargar el stock positivo de la planilla como inventario inicial',
        'Sólo en los productos nuevos. Queda como un conteo de inventario inicial: si no sos la '
        'titular, el stock aparece cuando ella lo aprueba. Los stocks negativos o con decimales en '
        'productos que van por unidad no se cargan: esos quedan en 0 hasta que los cuentes.'),)

    @staticmethod
    def _stock(texto, producto):
        """El stock de la planilla si se puede asentar: Decimal > 0 y, en lo
        que va por unidad, entero. Si no: 'NEGATIVO', 'DECIMALES' o None."""
        texto = (texto or '').strip()
        if not texto:
            return None
        numero = leer_numero(texto, 3)
        if numero is None or numero == 0 or producto.tipo_producto not in TIPOS_CON_PROVEEDOR:
            return None
        if numero < 0:
            return 'NEGATIVO'
        if (producto.exige_cantidad_entera and numero != numero.to_integral_value()) or numero > 99999:
            return 'DECIMALES'
        return numero

    def resumen(self, resultados):
        nuevos = [r.preparado[3] for r in resultados if r.ok and r.accion == 'ALTA']
        cargables = sum(1 for s in nuevos if isinstance(s, Decimal))
        negativos, raros = nuevos.count('NEGATIVO'), nuevos.count('DECIMALES')
        elaborados = sum(1 for r in resultados if r.ok and r.accion == 'ALTA'
                         and r.preparado[0].tipo_producto == TipoProducto.ELABORADO)
        frases = ([f'{elaborados} producto(s) entran como elaborados (por el rubro o porque el nombre empieza con '
                   '«CAFETERIA» o «PROMO»): se venden sin llevar stock. Si alguno en realidad se compra hecho, '
                   'cambiale el tipo antes de importarlo.'] if elaborados else [])
        if not (cargables or negativos or raros):
            return frases
        frases.append(f'{cargables} producto(s) nuevos traen un stock que se puede cargar como inventario inicial.')
        if negativos or raros:
            frases.append(
                f'{negativos + raros} quedan en 0 pase lo que pase: {negativos} con stock negativo y {raros} '
                'con decimales o cantidades imposibles en algo que va por unidad. Contalos cuando puedas.')
        return frases

    def despues_de_aplicar(self, resultados, usuario, opciones):
        if not opciones.get('stock_inicial'):
            return []
        from stock import servicios
        from stock.models import MotivoAjuste
        pares = [(r.preparado[0], r.preparado[3]) for r in resultados
                 if r.ok and r.accion == 'ALTA' and r.foto_nueva and isinstance(r.preparado[3], Decimal)]
        if not pares:
            return []
        try:
            conteo = servicios.iniciar_conteo(usuario, MotivoAjuste.INVENTARIO_INICIAL)
        except ValidationError:
            return ['El stock NO se cargó: hay un conteo de inventario abierto. Cerralo y cargá el stock '
                    'con un conteo de inventario inicial.']
        for producto, cantidad in pares:
            servicios.cargar_conteo(conteo, producto, cantidad)
        servicios.enviar_conteo(conteo)
        if servicios.puede_aprobar_lo_propio(usuario):
            servicios.aprobar(conteo, usuario, 'Stock inicial de la importación')
            return [f'Se cargó el stock inicial de {len(pares)} producto(s).']
        return [f'El stock inicial de {len(pares)} producto(s) quedó como un conteo pendiente de aprobación '
                f'({conteo}): aparece en el mostrador cuando la titular lo apruebe.']

    #  Lo que se prepara en el local no se compra ni se cuenta: si la planilla
    #  no dice el tipo (la de Fácil Virtual no lo trae), se reconoce por el
    #  rubro o por cómo empieza el nombre y entra como ELABORADO, que se
    #  vende sin stock propio (DD-90).
    RUBROS_ELABORADOS = ('comidas elaboradas',)
    PREFIJOS_ELABORADOS = ('cafeteria ', 'promo ')

    @classmethod
    def _tipo_por_defecto(cls, nombre, rubro):
        nombre, rubro = clave_normalizada(nombre or ''), clave_normalizada(rubro or '')
        if rubro in cls.RUBROS_ELABORADOS or (nombre + ' ').startswith(cls.PREFIJOS_ELABORADOS):
            return TipoProducto.ELABORADO
        return TipoProducto.REVENTA

    @staticmethod
    def _iva(texto):
        texto = (texto or '').strip().replace('%', '').strip()
        if not texto:
            return None
        numero = leer_numero(texto, 2)
        for valor, _ in ALICUOTAS_IVA:
            if numero is not None and numero == valor:
                return valor
        raise ValidationError(f'El IVA «{texto}» no es una alícuota válida: va 0, 10,5 o 21.')

    def validar(self, resultado, vistos):
        d = resultado.datos
        errores = resultado.errores
        nombre = ' '.join((d.get('nombre') or '').split())
        marca_texto = ' '.join((d.get('marca') or '').split())
        marca = self.marcas.get(clave_normalizada(marca_texto)) if marca_texto else self.generica
        try:
            rubro, rubro_nuevo = self._rubro(d.get('rubro'), resultado)
            tipo = opcion(d.get('tipo'), TipoProducto.choices, 'El tipo',
                          self._tipo_por_defecto(nombre, d.get('rubro')))
            unidad = opcion(d.get('unidad'), UnidadMedida.choices, 'La unidad', UnidadMedida.UNIDAD)
            contenido = leer_numero(d.get('contenido') or '1', 3)
            if contenido is None:
                raise ValidationError(f'El contenido «{d.get("contenido")}» no es un número.')
            proveedor = self._proveedor(d.get('proveedor'))
            iva = self._iva(d.get('iva'))
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

        codigo_crudo = (d.get('codigo') or '').strip()
        codigo = sanear_codigo(codigo_crudo)
        if codigo and codigo != codigo_crudo:
            resultado.avisos.append(f'El código «{codigo_crudo}» se limpió: queda «{codigo}».')
        if codigo:
            if ('codigo', codigo) in vistos:
                errores.append(
                    f'El código {codigo} está repetido: es el mismo que el de la fila {vistos[("codigo", codigo)]}.')
                return
            vistos[('codigo', codigo)] = resultado.numero

        id_existente = self.por_codigo.get(codigo) if codigo else None
        identidad = (clave_normalizada(marca_texto) or '·generica·', clave_normalizada(nombre), contenido, unidad)
        if id_existente is None and marca is not None:
            id_por_nombre, su_codigo = self.por_identidad.get(
                (marca.pk, clave_normalizada(nombre), contenido, unidad), (None, None))
            if id_por_nombre is not None and codigo and su_codigo and su_codigo != codigo:
                # Mismo nombre y presentación, otro código: son dos artículos
                # distintos para el lector, pero el sistema los vería iguales.
                errores.append(
                    'Ya hay un producto con ese nombre, marca y presentación, pero con otro código. '
                    'Diferenciá el nombre (el sabor, el tamaño) o corregí el código.')
                return
            id_existente = id_por_nombre
        if identidad in vistos and id_existente is None:
            errores.append(
                f'Tiene el mismo nombre, marca y presentación que la fila {vistos[identidad]}: '
                'diferenciá el nombre (el sabor, el tamaño) para que no se confundan.')
            return
        vistos.setdefault(identidad, resultado.numero)
        if id_existente is not None:
            if ('pk', id_existente) in vistos:
                errores.append(
                    f'Es el mismo producto que el de la fila {vistos[("pk", id_existente)]}: está dos veces.')
                return
            vistos[('pk', id_existente)] = resultado.numero

        if id_existente is not None:
            producto = self.productos[id_existente]
            antes = {c: getattr(producto, c) for c in CAMPOS_QUE_SE_ACTUALIZAN}
            if 'precio' in numeros:
                producto.precio_venta = numeros['precio']
            if 'costo' in numeros:
                producto.precio_costo = numeros['costo']
            if 'minimo' in numeros:
                producto.stock_minimo = numeros['minimo']
            if iva is not None:
                producto.alicuota_iva = iva
            if proveedor is not None:
                producto.id_proveedor = proveedor
            if codigo:
                producto.codigo_barras = codigo
            if producto.tipo_producto != tipo and (d.get('tipo') or '').strip():
                errores.append('El tipo de un producto existente no se cambia desde una planilla.')
                return
            cambios = [c for c in CAMPOS_QUE_SE_ACTUALIZAN if getattr(producto, c) != antes[c]]
            if cambios:
                try:
                    producto.full_clean(exclude=['id_marca', 'id_rubro', 'id_proveedor'])
                except ValidationError as error:
                    errores.extend(error.messages)
                    return
                self._avisar_bajo_costo(resultado, producto)
            resultado.accion = 'CAMBIO' if cambios else 'SIN CAMBIOS'
            resultado.descripcion = str(producto) + (
                ': ' + ', '.join(self._describir(c, antes[c], getattr(producto, c)) for c in cambios)
                if cambios else '')
            resultado.preparado = (producto, None, None, None)
            return

        if proveedor is None and tipo in TIPOS_CON_PROVEEDOR:
            proveedor = self.general
        producto = Producto(
            nombre_producto=nombre, id_marca=marca, id_rubro=rubro, tipo_producto=tipo,
            contenido=contenido, unidad_medida=unidad, precio_venta=numeros.get('precio', Decimal('0')),
            precio_costo=numeros.get('costo', Decimal('0')), stock_minimo=numeros.get('minimo', Decimal('0')),
            alicuota_iva=iva if iva is not None else Decimal('0.0'),
            id_proveedor=proveedor, codigo_barras=codigo,
            fraccionable=fraccionable, controla_vencimiento=controla)
        excluir = ['id_proveedor', 'id_marca', 'id_rubro']    # ya se sabe que existen
        try:
            # Las unicidades ya se resolvieron arriba con los índices: no
            # hace falta que cada fila vuelva a preguntarle a la base.
            producto.clean_fields(exclude=excluir)
            self._reglas_del_producto(producto)
        except ValidationError as error:
            errores.extend(error.messages)
            return
        self._avisar_bajo_costo(resultado, producto)
        if codigo and len(codigo) in (8, 12, 13, 14) and not es_gtin(codigo):
            resultado.avisos.append(
                f'El código {codigo} tiene el largo de uno de fábrica pero el dígito de control no cierra: '
                'entra como código propio. Si era el del envase, revisalo.')
        resultado.accion = 'ALTA'
        resultado.descripcion = (' · '.join(p for p in (marca_texto, nombre, producto.presentacion) if p)
                                 + ('' if marca else ' (marca nueva)'))
        resultado.preparado = (producto, None if marca else marca_texto, rubro_nuevo,
                               self._stock(d.get('stock'), producto))

    @staticmethod
    def _reglas_del_producto(producto):
        """Las reglas de ``Producto.clean`` que no consultan la base."""
        from nucleo.validaciones import texto_de_catalogo, validar_codigo_barras, validar_decimal
        producto.nombre_producto = texto_de_catalogo(producto.nombre_producto, 2, 100, 'El nombre del producto')
        producto.codigo_barras = validar_codigo_barras(producto.codigo_barras)
        producto.precio_costo = validar_decimal(
            producto.precio_costo, minimo=Decimal('0'), maximo=Decimal('9999999999.99'), decimales=2,
            etiqueta='El precio de costo')
        producto.stock_minimo = validar_decimal(
            producto.stock_minimo, minimo=Decimal('0'), maximo=Decimal('99999'), decimales=3,
            etiqueta='El stock mínimo')
        producto.contenido = validar_decimal(
            producto.contenido, minimo=Decimal('0'), maximo=Decimal('9999999'), decimales=3,
            etiqueta='El contenido', incluye_minimo=False)
        producto._validar_matriz_del_tipo()

    @staticmethod
    def _avisar_bajo_costo(resultado, producto):
        """No frena la importación (puede ser una liquidación), pero una
        planilla con el precio y el costo cruzados se ve antes de aplicar."""
        precio, costo = producto.precio_venta, producto.precio_costo
        if precio and costo and precio < costo:
            resultado.avisos.append(
                f'El precio de venta ({pesos(precio)}) queda por debajo del costo '
                f'({pesos(costo)}): se vendería a pérdida. Fijate que no estén cruzados.')

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
        producto, marca_nueva, rubro_nuevo, _ = resultado.preparado
        if marca_nueva:
            clave = clave_normalizada(marca_nueva)
            marca = self.marcas.get(clave)
            if marca is None:
                marca = Marca(nombre_marca=marca_nueva)
                marca.full_clean()
                marca.save()
                self.marcas[clave] = marca
            producto.id_marca = marca
        if rubro_nuevo:
            clave = clave_normalizada(rubro_nuevo)
            rubro = self.rubros.get(clave)
            if rubro is None:
                rubro = Rubro(nombre_rubro=rubro_nuevo)
                rubro.full_clean()
                rubro.save()
                self.rubros[clave] = rubro
            producto.id_rubro = rubro
        if marca_nueva or (producto.pk is not None):
            producto.full_clean()
        producto.save()
