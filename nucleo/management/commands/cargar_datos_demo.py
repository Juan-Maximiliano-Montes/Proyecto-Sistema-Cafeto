"""Carga un local de demostración: dos meses de ventas y dos semanas de
operación diaria completa.

    docker compose exec web python manage.py cargar_datos_demo

Para qué: tener datos con los que recorrer el sistema y buscar errores, con
las situaciones que pasan de verdad en Cafeto —una factura con faltante, un
cierre de caja con diferencia, una devolución, una venta anulada, un lote
por vencer, un pago atrasado—, entre 7 y 20 casos por tabla. Antes de esas
dos semanas hay una historia corta de ventas (de 60 a 15 días atrás) para
que los reportes y los gráficos del Parcial comparen un período con el
anterior. Las ventas se reparten entre las 8 y las 21 h.

Cómo: las OPERACIONES pasan por las mismas pantallas del sistema (las
vistas de Django, con un cliente HTTP interno): las mismas validaciones,
los mismos permisos, el mismo Registro de Actividad con el antes y el
después. Si una operación falla, es un error del sistema o de una regla,
y el comando lo informa al final en lugar de esconderlo.

Los CATÁLOGOS (productos, proveedores, clientes...) se dan de alta directo
en el modelo, con su asiento de auditoría igual al de la pantalla de alta:
es el camino que ya tienen probado y no aporta nada pasarlos por un
formulario.

El tiempo: para que haya historia (cierres de días anteriores, lotes que
vencen, pagos atrasados) el comando "viaja" a cada día con un reloj propio
que reemplaza ``timezone.now`` mientras corre. No toca el reloj del
servidor ni ninguna otra cosa.

Es para un entorno de desarrollo o de prueba: no corre con DEBUG apagado
salvo que se le pida, y no corre dos veces sobre la misma base.
"""
import json
import random
import uuid
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

D = Decimal
CLAVE_DEMO = 'Demo.Cafeto.2026'
# Si este proveedor existe, la demo ya se cargó.
CUIT_MARCADOR = '30-71584930-1'


# ===========================================================================
#  EL RELOJ
# ===========================================================================
class Reloj:
    """La hora "de mentira" del comando. ``timezone.now`` la devuelve."""

    def __init__(self):
        self.ahora = timezone.now()

    def ir_a(self, dia, hora, minuto=0):
        local = datetime.combine(dia, time(hora, minuto))
        self.ahora = timezone.make_aware(local)

    def pasar(self, minutos):
        self.ahora += timedelta(minutes=minutos)


@contextmanager
def con_reloj(reloj):
    """Reemplaza ``timezone.now`` por el reloj del comando.

    Los campos con ``default=timezone.now`` guardaron una referencia a la
    función original, así que se les pone por el rato una que la busca de
    nuevo cada vez (y encuentra la reemplazada).
    """
    original = timezone.now
    campos = [campo for modelo in apps.get_models()
              for campo in modelo._meta.concrete_fields
              if campo.default is original]

    def ahora():
        return reloj.ahora

    with mock.patch.object(timezone, 'now', ahora):
        for campo in campos:
            campo.default = lambda: timezone.now()
            # Django guarda la función del default la primera vez que la usa
            # (``Field._get_default`` es un cached_property): hay que olvidarla.
            campo.__dict__.pop('_get_default', None)
        try:
            yield
        finally:
            for campo in campos:
                campo.default = original
                campo.__dict__.pop('_get_default', None)


# ===========================================================================
#  UNA PERSONA QUE USA EL SISTEMA
# ===========================================================================
class Persona:
    """Alguien del local operando desde su navegador (un cliente HTTP)."""

    def __init__(self, usuario, comando):
        self.usuario = usuario
        self.comando = comando
        self.cliente = None

    def __str__(self):
        return f'{self.usuario.nombre} {self.usuario.apellido}'

    def entrar(self, clave=CLAVE_DEMO):
        self.cliente = Client(HTTP_HOST='localhost')
        self.desde = self.ultimo = self.comando.reloj.ahora
        respuesta = self.cliente.post(reverse('seguridad:login'), {
            'identificador': self.usuario.nombre_usuario, 'clave': clave})
        return respuesta.status_code == 302

    def salir(self):
        if self.cliente is not None:
            self.cliente.post(reverse('seguridad:logout'))
            self.cliente = None

    def token(self):
        """Un token de un solo uso, como el que emite cada formulario."""
        from nucleo.idempotencia import CLAVE
        sesion = self.cliente.session
        nuevo = uuid.uuid4().hex
        sesion[CLAVE] = (sesion.get(CLAVE, []) + [nuevo])[-200:]
        sesion.save()
        return nuevo

    def post(self, nombre, datos=None, args=None, consulta=''):
        self.ultimo = self.comando.reloj.ahora
        respuesta = self.cliente.post(reverse(nombre, args=args) + consulta, datos or {})
        # Lo que la pantalla le habría avisado a la persona.
        guardados = getattr(respuesta.wsgi_request, '_messages', None)
        self.ultimos_avisos = [str(m) for m in getattr(guardados, '_queued_messages', [])
                               if m.level >= 40]
        if not self.ultimos_avisos and respuesta.status_code == 200:
            # Algunas pantallas no redirigen: vuelven a dibujarse con el error.
            import re
            html = respuesta.content.decode(errors='ignore')
            for bloque in re.findall(r'(?:aviso-error|error-campo)[^>]*>(.*?)</(?:div|p)>', html, re.S)[:3]:
                texto = ' '.join(re.sub(r'<[^>]+>', ' ', bloque).split())
                if texto:
                    self.ultimos_avisos.append(texto)
        return respuesta

    @property
    def aviso(self):
        return ' / '.join(getattr(self, 'ultimos_avisos', [])) or 'sin aviso en pantalla'

    def post_json(self, nombre, cuerpo):
        self.ultimo = self.comando.reloj.ahora
        return self.cliente.post(reverse(nombre), json.dumps(cuerpo),
                                 content_type='application/json')

    def elegir_caja(self, caja):
        self.post('ventas:elegir_caja', {'caja': caja.pk})


# ===========================================================================
#  EL COMANDO
# ===========================================================================
class Command(BaseCommand):
    help = ('Carga dos meses de ventas y dos semanas de operación de un local de demostración '
            '(7 a 20 casos por tabla) pasando por las pantallas del sistema.')

    def add_arguments(self, parser):
        parser.add_argument('--semilla', type=int, default=2026,
                            help='Misma semilla, mismos datos.')
        parser.add_argument('--aun-sin-debug', action='store_true',
                            help='Permite correrlo con DEBUG apagado (NO en el local real).')

    # ------------------------------------------------------------------
    def handle(self, *args, **opciones):
        from compras.models import Proveedor
        if not settings.DEBUG and not opciones['aun_sin_debug']:
            raise CommandError(
                'DEBUG está apagado: esto parece la base del local. La demo se '
                'carga en una base de desarrollo o de prueba.')
        if Proveedor.objects.filter(cuit_proveedor=CUIT_MARCADOR).exists():
            raise CommandError('La demo ya está cargada en esta base.')
        from ventas.models import TurnoCaja
        abiertos = TurnoCaja.objects.filter(fecha_cierre__isnull=True).select_related(
            'id_caja_registradora', 'id_usuario_apertura')
        if abiertos.exists():
            raise CommandError(
                'Hay cajas con el turno abierto: '
                + '; '.join(f'{t.id_caja_registradora} (abrió {t.id_usuario_apertura})'
                            for t in abiertos)
                + '. Cerralas desde Caja antes de cargar la demo: la demo abre y '
                  'cierra turnos de días anteriores.')

        random.seed(opciones['semilla'])
        # Las horas de las ventas salen de otro generador: así la secuencia
        # de siempre (motivos, demoras) no cambia por repartirlas en el día.
        self.azar_horas = random.Random(opciones['semilla'])
        self.problemas = []
        self.creados = set()
        self.hoy = timezone.localdate()
        self.reloj = Reloj()
        # Todo o nada: si algo se corta a mitad de camino, la base queda
        # como estaba y se puede volver a correr.
        with transaction.atomic(), con_reloj(self.reloj):
            self.preparar_personas()
            self.cargar_catalogos()
            self.operar()
        self.informar()

    # El personal y los catálogos se dan de alta antes de la primera venta
    # de la historia (HISTORIA, más abajo).
    DIA_DE_ALTA = 61

    # ------------------------------------------------------------------
    def dia(self, atras):
        return self.hoy - timedelta(days=atras)

    def problema(self, texto):
        self.problemas.append(texto)
        self.stdout.write(self.style.WARNING(f'  ! {texto}'))

    def paso(self, texto):
        self.stdout.write(f'  · {texto}')

    # ==================================================================
    #  PERSONAS
    # ==================================================================
    def preparar_personas(self):
        from nucleo import contexto
        from seguridad.models import Perfil, Usuario
        from seguridad.servicios import instantanea, registrar_auditoria

        self.reloj.ir_a(self.dia(self.DIA_DE_ALTA), 8, 0)
        self.stdout.write('Personal del local')
        titular = Usuario.objects.filter(es_titular=True).first()
        equipo = [
            # (dni, nombre, apellido, perfil, activo)
            ('31555777', 'Carla', 'Molina', 'Administrador', True),
            ('33444888', 'Lucía', 'Fernández', 'Encargado', True),
            ('38222111', 'Tomás', 'Herrera', 'Operador', True),
            ('40111222', 'Valentina', 'Ríos', 'Operador', True),
            ('41999333', 'Diego', 'Acosta', 'Operador', True),
            ('36777444', 'Pablo', 'Sosa', 'Operador', False),
        ]
        personas = {}
        with contexto.usar(titular):
            for dni, nombre, apellido, perfil, activo in equipo:
                usuario = Usuario.objects.filter(dni_usuario=dni).first()
                if usuario is None:
                    usuario = Usuario(dni_usuario=dni, nombre=nombre, apellido=apellido,
                                      correo=f'{nombre.lower()}.{apellido.lower()}@cafeto.demo'
                                      .replace('í', 'i').replace('á', 'a'),
                                      id_perfil=Perfil.objects.get(nombre=perfil))
                    usuario.set_clave(CLAVE_DEMO)
                    usuario.debe_cambiar_clave = False
                    usuario.save()
                    registrar_auditoria(None, 'ALTA', 'USUARIO', instancia=usuario,
                                        nuevo=instantanea(usuario),
                                        detalle=f'Alta de {usuario} (carga de demostración).')
                    self.reloj.pasar(4)
                if not activo and usuario.activo:
                    antes = instantanea(usuario)
                    usuario.dar_de_baja()
                    usuario.refresh_from_db()
                    registrar_auditoria(None, 'BAJA', 'USUARIO', instancia=usuario,
                                        anterior=antes, nuevo=instantanea(usuario),
                                        detalle=f'{usuario} dejó de trabajar en el local.')
                personas[nombre] = Persona(usuario, self)
        self.carla = personas['Carla']
        self.lucia = personas['Lucía']
        self.tomas = personas['Tomás']
        self.valen = personas['Valentina']
        self.diego = personas['Diego']
        self.pablo = personas['Pablo']
        self.paso(f'{len(equipo)} personas (Pablo Sosa, dado de baja). Clave: {CLAVE_DEMO}')

    # ==================================================================
    #  CATÁLOGOS
    # ==================================================================
    def alta(self, clave_crud, objeto, quien=None, opcional=False):
        """Da de alta como lo hace la pantalla: valida, guarda y audita.

        ``opcional``: si el sistema lo rechaza (porque la base ya tiene algo
        que choca, como otro beneficio automático vigente en la misma
        categoría), se anota en el informe y la demo sigue sin él.
        """
        from django.core.exceptions import ValidationError
        from nucleo import contexto
        from nucleo.crud import REGISTRO
        from seguridad.servicios import instantanea, registrar_auditoria
        configuracion = REGISTRO[clave_crud]
        with contexto.usar((quien or self.carla).usuario):
            try:
                objeto.full_clean()
            except ValidationError as error:
                if not opcional:
                    raise
                self.problemas.append(
                    f'{configuracion.titulo}: "{objeto}" no se cargó porque ya hay '
                    f'datos que chocan ({"; ".join(error.messages)})')
                return None
            objeto.save()
            registrar_auditoria(None, 'ALTA', configuracion.singular.upper(),
                                nuevo=instantanea(objeto), instancia=objeto,
                                detalle=f'{configuracion.titulo}: {objeto}')
        self.creados.add((type(objeto), objeto.pk))
        self.reloj.pasar(2)
        return objeto

    def buscar_o_alta(self, clave_crud, modelo, filtro, **datos):
        """Lo que ya está en la base se reutiliza, no se duplica.

        Se busca por el filtro (el CUIT, el nombre exacto) y, si no aparece,
        por cada dato que la pantalla no dejaría repetir, comparado como lo
        compara el sistema: "Lacteos del valle" es "Lácteos del Valle"
        (DD-50). Pasaba en una base donde alguien ya había cargado ese
        proveedor a mano: la demo lo quería dar de alta otra vez y el
        sistema, con razón, lo rechazaba.
        """
        from nucleo.asistencia import campos_vigilados, clave
        from nucleo.crud import REGISTRO
        existente = modelo.objects.filter(**filtro).first()
        if existente is not None:
            return existente
        valores = {**filtro, **datos}
        for campo, modo in campos_vigilados(REGISTRO[clave_crud]).items():
            valor = valores.get(campo)
            if valor in (None, ''):
                continue
            if modo == 'parecido':
                buscado = clave(valor)
                existente = next((fila for fila in modelo.objects.all()
                                  if clave(getattr(fila, campo)) == buscado), None)
            else:
                existente = modelo.objects.filter(**{campo: valor}).first()
            if existente is not None:
                return existente
        return self.alta(clave_crud, modelo(**valores))

    @staticmethod
    def cuit(prefijo, cuerpo):
        """Un CUIT con su dígito verificador correcto."""
        numeros = f'{prefijo}{cuerpo}'
        pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
        resto = sum(int(n) * p for n, p in zip(numeros, pesos)) % 11
        verificador = 11 - resto
        if verificador == 11:
            verificador = 0
        if verificador == 10:
            return None
        return f'{prefijo}-{cuerpo}-{verificador}'

    def cargar_catalogos(self):
        from compras.models import Proveedor, TipoEgreso
        from fidelizacion.models import Beneficio, BeneficioProducto, CategoriaFidelizacion, Cliente
        from geografia.models import Localidad, Provincia
        from nucleo.restricciones import clave_normalizada
        from stock.models import ComposicionProducto, Marca, Producto, Rubro
        from ventas.models import MedioPago

        self.stdout.write('Catálogos')
        self.reloj.ir_a(self.dia(self.DIA_DE_ALTA), 9, 0)

        # -- Localidades ---------------------------------------------------
        salta = Provincia.objects.get(nombre_provincia='Salta')
        self.localidades = {}
        for nombre, cp in (('Campo Quijano', '4407'), ('Salta Capital', '4400'),
                           ('Rosario de Lerma', '4405'), ('Cerrillos', '4403'),
                           ('La Merced', '4401'), ('Vaqueros', '4401'),
                           ('San Lorenzo', '4401'), ('El Carril', '4421')):
            self.localidades[nombre] = self.buscar_o_alta(
                'localidades', Localidad,
                {'nombre_localidad': nombre, 'id_provincia': salta}, codigo_postal=cp)

        # -- Rubros (con la rebaja por vencimiento donde corresponde) -----
        self.rubros = {}
        for nombre, desc, dias, pct in (
                ('Cafetería', None, None, None), ('Minisuper', None, None, None),
                ('Lácteos', 'Leches, yogures y quesos', 4, D('30')),
                ('Bebidas', 'Gaseosas, aguas y jugos', None, None),
                ('Golosinas', 'Alfajores, chocolates y caramelos', None, None),
                ('Almacén', 'Yerba, azúcar, galletitas', None, None),
                ('Panificados', 'Medialunas y facturas del día', 1, D('50')),
                ('Fiambrería', 'Fiambres y quesos por peso', 3, D('20'))):
            self.rubros[nombre] = self.buscar_o_alta(
                'rubros', Rubro, {'nombre_rubro': nombre}, descripcion_rubro=desc,
                dias_rebaja_vencimiento=dias, porcentaje_rebaja_vencimiento=pct)

        # -- Marcas ----------------------------------------------------------
        self.marcas = {}
        for nombre in ('Sin marca', 'La Serenísima', 'Cabrales', 'Coca-Cola', 'Arcor',
                       'Havanna', 'Terrabusi', 'Taragüí', 'Ledesma', 'Villavicencio',
                       'Tregar', 'Paladini', 'Pureza', 'Cafeto'):
            self.marcas[nombre] = self.buscar_o_alta(
                'marcas', Marca, {'nombre_marca': nombre},
                es_propia=(nombre == 'Cafeto'))
        # «Cafeto» es la marca de la casa: lo que se prepara en el local
        # (café, medialunas, el combo) tiene que llevar una marca propia
        # (STK-01). Si ya existía cargada a mano sin la marca, se la pone.
        cafeto = self.marcas['Cafeto']
        if not cafeto.es_propia:
            Marca.objects.filter(pk=cafeto.pk).update(es_propia=True)
            cafeto.es_propia = True

        # -- Proveedores -----------------------------------------------------
        quijano = self.localidades['Campo Quijano']
        capital = self.localidades['Salta Capital']
        self.proveedores = {}
        for nombre, prefijo, cuerpo, tipo, pago, tel, loc in (
                ('Distribuidora Andina', '30', '71584930', 'MERCADERIA', 'CTA_CTE', '3874211000', capital),
                ('Lácteos del Valle', '30', '70998812', 'MERCADERIA', '15_DIAS', '3874225511', capital),
                ('Café Cabrales Norte', '30', '71203344', 'MERCADERIA', '30_DIAS', '3874310020', capital),
                ('Molinos Lerma', '30', '70455566', 'MERCADERIA', 'CONTADO', '3874920033', self.localidades['Rosario de Lerma']),
                ('Golosinas del Norte', '30', '71877001', 'MERCADERIA', 'CONTADO', '3874440088', capital),
                ('Fiambrería Quijano', '20', '28456123', 'MERCADERIA', 'CONTADO', '3874901122', quijano),
                ('EDESA', '30', '69062215', 'SERVICIOS', 'CONTADO', None, capital),
                ('Aguas del Norte', '30', '70889941', 'SERVICIOS', 'CONTADO', None, capital),
                ('Limpieza Total', '30', '71300456', 'MIXTO', 'CONTADO', '3874556677', capital)):
            cuit = self.cuit(prefijo, cuerpo)
            if cuit is None:
                cuit = self.cuit(prefijo, str(int(cuerpo) + 1))
            if nombre == 'Distribuidora Andina':
                cuit = CUIT_MARCADOR
            self.proveedores[nombre] = self.buscar_o_alta(
                'proveedores', Proveedor, {'cuit_proveedor': cuit},
                nombre_proveedor=nombre, tipo_proveedor=tipo, condicion_pago=pago,
                telefono_proveedor=tel, id_localidad=loc,
                email_proveedor=None if tel is None else
                f"ventas@{nombre.split()[0].lower().replace('á', 'a').replace('é', 'e')}.demo")
        self.dar_de_baja('proveedores', self.proveedores['Limpieza Total'])

        # -- Tipos de egreso y medios de pago -------------------------------
        for nombre, desc in (('Alquiler', 'Alquiler del local'),
                             ('Mantenimiento', 'Arreglos y service de equipos')):
            self.buscar_o_alta('tipos_egreso', TipoEgreso, {'nombre_tipo_egreso': nombre},
                               descripcion_tipo_egreso=desc)
        for nombre, cuotas, recargo in (('Crédito 3 cuotas', 3, D('9')),
                                        ('Crédito 6 cuotas', 6, D('18'))):
            self.buscar_o_alta('medios_pago', MedioPago, {'nombre_medio_pago': nombre},
                               es_tarjeta_credito=True, cantidad_cuotas=cuotas,
                               recargo_porcentaje=recargo, afecta_caja=False)

        # -- Productos -------------------------------------------------------
        p = self.proveedores
        m = self.marcas
        r = self.rubros
        catalogo = [
            # nombre, tipo, rubro, marca, contenido, unidad, precio, costo, prov, extra
            ('Café en grano', 'INSUMO', 'Cafetería', 'Cabrales', '1', 'KG', '0', '28000', p['Café Cabrales Norte'], {'fraccionable': True, 'stock_minimo': D('2')}),
            ('Leche para cafetería', 'INSUMO', 'Cafetería', 'La Serenísima', '1', 'L', '0', '1300', p['Lácteos del Valle'], {'fraccionable': True, 'controla_vencimiento': True, 'stock_minimo': D('6')}),
            ('Harina 000', 'INSUMO', 'Cafetería', 'Pureza', '1', 'KG', '0', '1100', p['Molinos Lerma'], {'fraccionable': True}),
            ('Manteca', 'INSUMO', 'Cafetería', 'La Serenísima', '1', 'KG', '0', '9500', p['Lácteos del Valle'], {'fraccionable': True, 'stock_minimo': D('1')}),
            ('Gaseosa cola', 'REVENTA', 'Bebidas', 'Coca-Cola', '500', 'ML', '2500', '1500', p['Distribuidora Andina'], {'stock_minimo': D('12'), 'codigo_barras': '7790895000782'}),
            ('Gaseosa cola', 'REVENTA', 'Bebidas', 'Coca-Cola', '1.5', 'L', '4200', '2600', p['Distribuidora Andina'], {'stock_minimo': D('6')}),
            ('Agua sin gas', 'REVENTA', 'Bebidas', 'Villavicencio', '500', 'ML', '1500', '800', p['Distribuidora Andina'], {'stock_minimo': D('12')}),
            ('Chocolate con leche', 'REVENTA', 'Golosinas', 'Arcor', '100', 'G', '1800', '1000', p['Golosinas del Norte'], {}),
            ('Alfajor de dulce de leche', 'REVENTA', 'Golosinas', 'Havanna', '1', 'UNIDAD', '2200', '1300', p['Golosinas del Norte'], {'stock_minimo': D('10')}),
            ('Galletitas surtidas', 'REVENTA', 'Almacén', 'Terrabusi', '400', 'G', '2900', '1800', p['Distribuidora Andina'], {}),
            ('Yerba mate', 'REVENTA', 'Almacén', 'Taragüí', '1', 'KG', '5200', '3600', p['Distribuidora Andina'], {}),
            ('Azúcar', 'REVENTA', 'Almacén', 'Ledesma', '1', 'KG', '1500', '950', p['Distribuidora Andina'], {}),
            ('Yogur bebible frutilla', 'REVENTA', 'Lácteos', 'La Serenísima', '1', 'L', '2200', '1300', p['Lácteos del Valle'], {'controla_vencimiento': True, 'stock_minimo': D('4')}),
            ('Leche entera larga vida', 'REVENTA', 'Lácteos', 'La Serenísima', '1', 'L', '1700', '1300', p['Lácteos del Valle'], {'controla_vencimiento': True, 'stock_minimo': D('6')}),
            ('Queso cremoso', 'REVENTA', 'Fiambrería', 'Tregar', '1', 'KG', '9000', '6000', p['Fiambrería Quijano'], {'fraccionable': True, 'controla_vencimiento': True}),
            ('Jamón cocido', 'REVENTA', 'Fiambrería', 'Paladini', '1', 'KG', '14000', '9800', p['Fiambrería Quijano'], {'fraccionable': True, 'controla_vencimiento': True}),
            ('Café chico', 'ELABORADO', 'Cafetería', 'Cafeto', '1', 'UNIDAD', '1800', '0', None, {}),
            ('Café con leche', 'ELABORADO', 'Cafetería', 'Cafeto', '1', 'UNIDAD', '2600', '0', None, {}),
            ('Medialuna de manteca', 'PRODUCIDO', 'Panificados', 'Cafeto', '1', 'UNIDAD', '900', '0', None, {'controla_vencimiento': True}),
            ('Desayuno Cafeto', 'COMBO', 'Cafetería', 'Cafeto', '1', 'UNIDAD', '4000', '0', None, {}),
        ]
        self.productos = {}
        for nombre, tipo, rubro, marca, contenido, unidad, precio, costo, prov, extra in catalogo:
            clave = f'{nombre} {contenido}{unidad}'
            existente = next((x for x in Producto.objects.filter(
                id_marca=m[marca], contenido=D(contenido), unidad_medida=unidad)
                if clave_normalizada(x.nombre_producto) == clave_normalizada(nombre)), None)
            compatible = existente is not None and not existente.borrado_logico and (
                existente.tipo_producto == tipo
                and existente.fraccionable == extra.get('fraccionable', False)
                and existente.controla_vencimiento == extra.get('controla_vencimiento', False))
            if existente is not None and not compatible:
                # Ya hay uno igual pero se usa distinto (otro tipo, otra forma
                # de vender): la demo no toca el tuyo y carga el suyo aparte.
                nombre = f'{nombre} (demo)'
                existente = None
            if existente is None:
                existente = self.alta('productos', Producto(
                    nombre_producto=nombre, tipo_producto=tipo, id_rubro=r[rubro],
                    id_marca=m[marca], contenido=D(contenido), unidad_medida=unidad,
                    precio_venta=D(precio), precio_costo=D(costo), id_proveedor=prov, **extra))
            self.productos[clave] = existente
        P = self.productos
        self.p = {
            'grano': P['Café en grano 1KG'], 'leche_insumo': P['Leche para cafetería 1L'],
            'harina': P['Harina 000 1KG'], 'manteca': P['Manteca 1KG'],
            'cola': P['Gaseosa cola 500ML'], 'cola15': P['Gaseosa cola 1.5L'],
            'agua': P['Agua sin gas 500ML'], 'choco': P['Chocolate con leche 100G'],
            'alfajor': P['Alfajor de dulce de leche 1UNIDAD'],
            'galletitas': P['Galletitas surtidas 400G'], 'yerba': P['Yerba mate 1KG'],
            'azucar': P['Azúcar 1KG'], 'yogur': P['Yogur bebible frutilla 1L'],
            'leche': P['Leche entera larga vida 1L'],
            'queso': P['Queso cremoso 1KG'], 'jamon': P['Jamón cocido 1KG'],
            'cafe': P['Café chico 1UNIDAD'], 'cafe_leche': P['Café con leche 1UNIDAD'],
            'medialuna': P['Medialuna de manteca 1UNIDAD'], 'desayuno': P['Desayuno Cafeto 1UNIDAD'],
        }
        # Recetas y combo
        for compuesto, componentes in (
                (self.p['cafe'], ((self.p['grano'], '0.008'),)),
                (self.p['cafe_leche'], ((self.p['grano'], '0.008'), (self.p['leche_insumo'], '0.15'))),
                (self.p['medialuna'], ((self.p['harina'], '0.04'), (self.p['manteca'], '0.012'))),
                (self.p['desayuno'], ((self.p['cafe_leche'], '1'), (self.p['medialuna'], '2')))):
            if not ComposicionProducto.objects.filter(id_producto=compuesto).exists():
                for componente, cantidad in componentes:
                    ComposicionProducto.objects.create(id_producto=compuesto,
                                                       id_componente=componente,
                                                       cantidad=D(cantidad))
        self.paso(f'{len(catalogo)} productos de los cinco tipos, con recetas y un combo')

        # -- Fidelización: beneficios y clientes ----------------------------
        bronze = CategoriaFidelizacion.objects.order_by('puntos_minimos').first()
        silver = CategoriaFidelizacion.objects.order_by('puntos_minimos')[1]
        self.beneficios = []
        for categoria, desc, tipo, modo, pct, puntos, especie in (
                (bronze, 'Café chico de regalo', 'CANJEABLE', 'ESPECIE', None, 20, self.p['cafe']),
                (bronze, '5 % en el total', 'CANJEABLE', 'DESCUENTO', D('5'), 30, None),
                (bronze, 'Medialuna de cortesía', 'CANJEABLE', 'ESPECIE', None, 10, self.p['medialuna']),
                (silver, '10 % siempre', 'AUTOMATICO', 'DESCUENTO', D('10'), None, None),
                (silver, 'Desayuno de cumpleaños', 'CANJEABLE', 'ESPECIE', None, 60, self.p['desayuno']),
                (bronze, 'Alfajor por 15 puntos', 'CANJEABLE', 'ESPECIE', None, 15, self.p['alfajor']),
                (silver, '15 % en el total', 'CANJEABLE', 'DESCUENTO', D('15'), 80, None)):
            beneficio = Beneficio.objects.filter(descripcion_beneficio=desc).first()
            if beneficio is None:
                beneficio = Beneficio(id_categoria=categoria, descripcion_beneficio=desc,
                                      tipo_beneficio=tipo, modo_entrega=modo,
                                      porcentaje_descuento=pct, puntos_requeridos=puntos,
                                      vigencia_desde=self.dia(self.DIA_DE_ALTA))
                beneficio = self.alta('beneficios', beneficio, opcional=True)
                if beneficio is None:
                    continue
                if especie is not None:
                    BeneficioProducto.objects.create(id_beneficio=beneficio,
                                                     id_producto=especie, cantidad=1)
            self.beneficios.append(beneficio)

        self.clientes = []
        for apellido, nombre, dni, tel, mail, loc in (
                ('Gutiérrez', 'Marta', '22345678', '3874556001', 'marta.gutierrez@gmail.com', 'Campo Quijano'),
                ('Cruz', 'Ramón', '25111999', '3874556002', None, 'Campo Quijano'),
                ('Burgos', 'Florencia', '35678123', None, 'flor.burgos@hotmail.com', 'La Merced'),
                ('Aramayo', 'Joaquín', '40222333', '3874556004', None, 'Rosario de Lerma'),
                ('Vargas', 'Elena', '18765432', '3874556005', None, 'Campo Quijano'),
                ('Paz', 'Nicolás', '38999111', None, 'nico.paz@gmail.com', 'Salta Capital'),
                ('Guzmán', 'Rocío', '42555666', '3874556007', None, 'Cerrillos'),
                ('Quispe', 'Hugo', '27333444', '3874556008', None, 'El Carril'),
                ('Romero', 'Ana Laura', '33222111', None, None, 'Campo Quijano'),
                ('Saravia', 'Federico', '30111444', '3874556010', 'fede.saravia@yahoo.com', 'Vaqueros'),
                ('Mamaní', 'Lidia', '24888777', '3874556011', None, 'Campo Quijano'),
                ('Ortiz', 'Gonzalo', '39444555', '3874556012', None, 'San Lorenzo')):
            cliente = Cliente.objects.filter(dni_cliente=dni).first()
            if cliente is None:
                cliente = self.alta('clientes', Cliente(
                    apellido_cliente=apellido, nombre_cliente=nombre, dni_cliente=dni,
                    telefono_cliente=tel, email_cliente=mail, id_categoria=bronze,
                    id_localidad=self.localidades[loc], qr_cliente=Cliente.generar_qr()),
                    quien=self.lucia)
            self.clientes.append(cliente)
        self.dar_de_baja('clientes', self.clientes[-1], quien=self.lucia)
        self.paso(f'{len(self.beneficios)} beneficios y {len(self.clientes)} clientes (uno dado de baja)')

    def dar_de_baja(self, clave_crud, objeto, quien=None):
        from nucleo import contexto
        from nucleo.crud import REGISTRO
        from seguridad.servicios import instantanea, registrar_auditoria
        if objeto.borrado_logico:
            return
        if (type(objeto), objeto.pk) not in self.creados:
            # Lo cargó otra persona antes de la demo: no se toca.
            return
        configuracion = REGISTRO[clave_crud]
        with contexto.usar((quien or self.carla).usuario):
            antes = instantanea(objeto)
            objeto.borrado_logico = True
            objeto.save()
            registrar_auditoria(None, 'BAJA', configuracion.singular.upper(),
                                anterior=antes, nuevo=instantanea(objeto), instancia=objeto,
                                detalle=f'{configuracion.titulo}: {objeto}')

    # ==================================================================
    #  OPERACIÓN DIARIA (por las pantallas), siempre hacia adelante
    # ==================================================================
    # (días atrás) -> (quién abre, caja, ventas del día)
    PLAN = {
        13: ('tomas', 'Caja Cafetería', 2), 12: ('valen', 'Caja Minisuper', 2),
        11: ('tomas', 'Caja Cafetería', 2), 10: ('diego', 'Caja Minisuper', 1),
        9: ('tomas', 'Caja Cafetería', 2), 8: ('valen', 'Caja Minisuper', 2),
        7: ('tomas', 'Caja Cafetería', 2), 6: ('diego', 'Caja Minisuper', 1),
        5: ('tomas', 'Caja Cafetería', 2), 4: ('valen', 'Caja Minisuper', 1),
        3: ('tomas', 'Caja Cafetería', 1), 2: ('valen', 'Caja Minisuper', 1),
        1: ('tomas', 'Caja Cafetería', 1),
    }

    # La historia: días sueltos entre 60 y 15 días atrás, con productos de
    # reventa que se compran justo para esas ventas (al llegar a las dos
    # semanas el stock es el mismo que sin historia).
    # (días atrás) -> (quién abre, caja, ventas del día)
    HISTORIA = {
        59: ('tomas', 'Caja Cafetería', 3), 55: ('valen', 'Caja Minisuper', 2),
        51: ('diego', 'Caja Minisuper', 3), 47: ('tomas', 'Caja Cafetería', 2),
        43: ('valen', 'Caja Minisuper', 3), 39: ('tomas', 'Caja Cafetería', 2),
        35: ('diego', 'Caja Minisuper', 3), 32: ('valen', 'Caja Cafetería', 2),
        29: ('tomas', 'Caja Cafetería', 3), 26: ('valen', 'Caja Minisuper', 2),
        23: ('diego', 'Caja Minisuper', 3), 20: ('tomas', 'Caja Cafetería', 2),
        17: ('valen', 'Caja Minisuper', 3), 15: ('tomas', 'Caja Cafetería', 2),
    }
    PEDIDOS_HISTORIA = [
        ([('cola', 2), ('alfajor', 1)], [('Efectivo', None)], False),
        ([('yerba', 1), ('azucar', 1)], [('QR', None)], False),
        ([('agua', 2), ('galletitas', 1)], [('Tarjeta de débito', None)], False),
        ([('choco', 2)], [('Efectivo', None)], False),
        ([('cola15', 1), ('alfajor', 2)], [('Transferencia', None)], False),
        ([('galletitas', 2), ('yerba', 1)], [('Tarjeta de crédito', None)], False),
        ([('agua', 1), ('choco', 1)], [('QR', None)], False),
    ]
    # Las ventas del día, entre la apertura (8 h) y el cierre (20:45), en
    # minutos desde las 0 h: de 8:20 a 20:20.
    PRIMERA_VENTA, ULTIMA_VENTA, CIERRE = 8 * 60 + 20, 20 * 60 + 20, 20 * 60 + 45

    def operar(self):
        """Día por día y hora por hora: el reloj nunca vuelve atrás (las
        sesiones, el kárdex y la caja dependen de ese orden)."""
        self.ventas = []
        self.ordenes = {}
        self.egresos = {}
        self.mermas = {}
        self.stdout.write('Dos meses de ventas (de 60 a 15 días atrás)')
        self.historia()
        self.stdout.write('Dos semanas de operación')
        for atras in range(14, 0, -1):
            self.manana(atras)
            if atras in self.PLAN:
                quien, caja, cuantas = self.PLAN[atras]
                self.un_dia(atras, getattr(self, quien), caja, cuantas)
            self.noche(atras)
        self.hoy_con_caja_abierta()

    def manana(self, atras):
        dia = self.dia(atras)
        if atras == 14:
            self.reloj.ir_a(dia, 7, 0)
            self.compras_iniciales()
        if atras in (10, 9, 4):
            self.reloj.ir_a(dia, 7, 10)
            self.recepciones_del_dia(atras)
        if atras == 1:
            self.reloj.ir_a(dia, 7, 20)
            self.intentos_fallidos()
        if atras % 2 == 1 and atras <= 13:
            self.reloj.ir_a(dia, 7, 40)
            self.producir(dia)

    def noche(self, atras):
        self.adelantar_a(self.dia(atras), 21 * 60)
        tareas = {14: self.egresos_del_mes, 13: self.pedir_reposicion,
                  6: self.pagar_cuentas, 5: self.pedir_mas, 4: self.conciliar_banco,
                  3: self.cargar_mermas, 2: self.resolver_mermas}
        if atras in tareas:
            tareas[atras]()

    # -- Compras ----------------------------------------------------------
    def recibir(self, quien, proveedor, numero, renglones, orden=None, tipo='FACTURA_B',
                vence_pago=None):
        """Recepción por la pantalla. ``renglones``: (producto, cant, costo, [(vence, cant)])."""
        from compras.models import Compra
        antes = Compra.objects.count()
        datos = {
            'token_operacion': quien.token(), 'proveedor': proveedor.pk,
            'tipo_comprobante': tipo, 'punto_venta': '3', 'numero': str(numero),
            'renglones': json.dumps([
                {'producto': prod.pk, 'cantidad': str(cant), 'costo': str(costo),
                 **({'lotes': [{'vence': v.isoformat(), 'cantidad': str(c)} for v, c in lotes]}
                    if lotes else {})}
                for prod, cant, costo, lotes in renglones])}
        if orden is not None:
            datos['orden'] = orden.pk
        if vence_pago is not None:
            datos['vence_pago'] = vence_pago.isoformat()
        quien.post('compras:recepcion', datos,
                   consulta=f'?orden={orden.pk}' if orden is not None else '')
        if Compra.objects.count() == antes:
            self.problema(f'No se registró la factura {numero} de {proveedor}: {quien.aviso}')
            return None
        self.reloj.pasar(10)
        return Compra.objects.latest('pk')

    def compras_iniciales(self):
        carla, p, pr, hoy = self.carla, self.p, self.proveedores, self.dia(14)
        carla.entrar()
        recibidas = [
            self.recibir(carla, pr['Distribuidora Andina'], 10231, [
                (p['cola'], 48, 1500, None), (p['cola15'], 18, 2600, None),
                (p['agua'], 36, 800, None), (p['galletitas'], 12, 1800, None),
                (p['yerba'], 10, 3600, None), (p['azucar'], 15, 950, None)],
                vence_pago=hoy + timedelta(days=30)),
            self.recibir(carla, pr['Lácteos del Valle'], 5512, [
                (p['leche_insumo'], 24, 1300, [(hoy + timedelta(days=9), 24)]),
                (p['leche'], 12, 1300, [(hoy + timedelta(days=9), 12)]),
                (p['yogur'], 12, 1300, [(hoy + timedelta(days=8), 6),
                                        (self.hoy + timedelta(days=3), 6)]),
                (p['manteca'], 4, 9500, None)], tipo='FACTURA_A',
                vence_pago=hoy + timedelta(days=15)),
            self.recibir(carla, pr['Café Cabrales Norte'], 889, [(p['grano'], 6, 28000, None)],
                         tipo='FACTURA_A', vence_pago=hoy + timedelta(days=30)),
            self.recibir(carla, pr['Molinos Lerma'], 3301, [(p['harina'], 20, 1100, None)],
                         tipo='TICKET'),
            self.recibir(carla, pr['Golosinas del Norte'], 7710, [
                (p['choco'], 24, 1000, None), (p['alfajor'], 30, 1300, None)]),
            self.recibir(carla, pr['Fiambrería Quijano'], 412, [
                (p['queso'], '3.5', 6000, [(self.hoy + timedelta(days=2), '3.5')]),
                (p['jamon'], '2.2', 9800, [(self.hoy + timedelta(days=12), '2.2')])]),
        ]
        carla.salir()
        self.paso(f'{sum(1 for c in recibidas if c)} facturas de la compra inicial, con lotes')

    def orden(self, quien, proveedor, renglones):
        from compras.models import OrdenCompra
        antes = OrdenCompra.objects.count()
        quien.post('compras:nueva_orden', {
            'token_operacion': quien.token(), 'proveedor': proveedor.pk,
            'renglones': json.dumps([{'producto': x.pk, 'cantidad': str(c), 'costo': str(k)}
                                     for x, c, k in renglones])})
        if OrdenCompra.objects.count() == antes:
            self.problema(f'No se emitió la orden a {proveedor}: {quien.aviso}')
            return None
        self.reloj.pasar(5)
        return OrdenCompra.objects.latest('pk')

    def estado_orden(self, quien, orden, accion):
        if orden is not None:
            quien.post('compras:estado_orden', {'accion': accion}, args=[orden.pk])
            self.reloj.pasar(2)

    def pedir_reposicion(self):
        carla, p, pr = self.carla, self.p, self.proveedores
        carla.entrar()
        o = self.ordenes
        o['lacteos'] = self.orden(carla, pr['Lácteos del Valle'],
                                  [(p['leche_insumo'], 24, 1300), (p['yogur'], 12, 1300)])
        o['andina'] = self.orden(carla, pr['Distribuidora Andina'],
                                 [(p['cola'], 48, 1500), (p['agua'], 24, 800)])
        o['golosinas'] = self.orden(carla, pr['Golosinas del Norte'], [(p['alfajor'], 30, 1300)])
        o['cancelada'] = self.orden(carla, pr['Fiambrería Quijano'], [(p['jamon'], 2, 9800)])
        for clave in ('lacteos', 'andina', 'golosinas', 'cancelada'):
            self.estado_orden(carla, o[clave], 'enviar')
        self.estado_orden(carla, o['cancelada'], 'cancelar')
        carla.salir()

    def pedir_mas(self):
        carla, p, pr = self.carla, self.p, self.proveedores
        carla.entrar()
        self.orden(carla, pr['Café Cabrales Norte'], [(p['grano'], 4, 28500)])   # borrador
        self.ordenes['fiambre'] = self.orden(carla, pr['Fiambrería Quijano'], [(p['queso'], 3, 6100)])
        self.estado_orden(carla, self.ordenes['fiambre'], 'enviar')
        carla.salir()
        self.paso('6 órdenes: recibidas, con faltante, enviada sin recibir, borrador y cancelada')

    def recepciones_del_dia(self, atras):
        lucia, p, pr, o = self.lucia, self.p, self.proveedores, self.ordenes
        lucia.entrar()
        if atras == 10 and o.get('lacteos'):
            self.recibir(lucia, pr['Lácteos del Valle'], 5590, [
                (p['leche_insumo'], 24, 1300, [(self.dia(10) + timedelta(days=9), 24)]),
                (p['yogur'], 12, 1300, [(self.hoy + timedelta(days=4), 12)])],
                orden=o['lacteos'], tipo='FACTURA_A')
        if atras == 9 and o.get('andina'):
            self.recibir(lucia, pr['Distribuidora Andina'], 10377, [
                (p['cola'], 40, 1500, None), (p['agua'], 24, 800, None)], orden=o['andina'])
            o['andina'].refresh_from_db()
            if o['andina'].estado_orden == 'RECEPCIONADA':
                self.problema(f'La orden N° {o["andina"].pk} llegó con faltante y quedó como completa.')
        if atras == 4 and o.get('fiambre'):
            self.recibir(lucia, pr['Fiambrería Quijano'], 455, [
                (p['queso'], 3, 6100, [(self.hoy + timedelta(days=6), 3)])], orden=o['fiambre'])
        if atras == 4:
            # Entrega sin orden: el repartidor de lácteos pasa dos veces por semana.
            self.recibir(lucia, pr['Lácteos del Valle'], 5644, [
                (p['leche'], 12, 1350, [(self.hoy + timedelta(days=6), 12)]),
                (p['leche_insumo'], 24, 1350, [(self.hoy + timedelta(days=7), 24)])],
                tipo='FACTURA_A', vence_pago=self.hoy + timedelta(days=11))
        lucia.salir()

    # -- Egresos ------------------------------------------------------------
    def egreso(self, quien, modo, tipo, monto, concepto, **extra):
        from compras.models import Egreso, TipoEgreso
        antes = Egreso.objects.count()
        quien.post('compras:registrar_egreso', {
            'token_operacion': quien.token(), 'modo': modo,
            'tipo': TipoEgreso.objects.get(nombre_tipo_egreso=tipo).pk,
            'monto': str(monto), 'concepto': concepto, **extra})
        self.reloj.pasar(3)
        if modo == 'caja':
            return None          # un gasto de caja es un movimiento del cajón
        if Egreso.objects.count() == antes:
            self.problema(f'No se registró el egreso "{concepto}": {quien.aviso}')
            return None
        return Egreso.objects.latest('pk')

    def medio(self, nombre):
        from ventas.models import MedioPago
        return MedioPago.objects.get(nombre_medio_pago=nombre)

    def egresos_del_mes(self):
        carla, pr, e = self.carla, self.proveedores, self.egresos
        carla.entrar()
        e['alquiler'] = self.egreso(carla, 'anotar', 'Alquiler', 450000, 'Alquiler del local',
                                    destinatario='Inmobiliaria Lerma', vence=self.dia(5).isoformat())
        e['luz'] = self.egreso(carla, 'anotar', 'Servicios', 68500, 'Luz del bimestre',
                               proveedor=pr['EDESA'].pk,
                               vence=(self.hoy + timedelta(days=3)).isoformat())
        e['agua'] = self.egreso(carla, 'anotar', 'Servicios', 21400, 'Agua',
                                proveedor=pr['Aguas del Norte'].pk, vence=self.dia(2).isoformat())
        self.egreso(carla, 'anotar', 'Impuestos', 38900, 'Ingresos Brutos (DGR)',
                    destinatario='DGR Salta', vence=(self.hoy + timedelta(days=9)).isoformat())
        self.egreso(carla, 'ahora', 'Servicios', 25000, 'Internet del local',
                    destinatario='Telecom', medio=self.medio('Tarjeta de débito').pk)
        carla.salir()

    def pagar_cuentas(self):
        carla, pr, e = self.carla, self.proveedores, self.egresos
        carla.entrar()
        if e.get('alquiler'):
            carla.post('compras:pagar_egreso', {'token_operacion': carla.token(),
                                                'medio': self.medio('Transferencia').pk},
                       args=[e['alquiler'].pk])
        self.egreso(carla, 'ahora', 'Sueldos', 380000, 'Adelanto de quincena',
                    destinatario='Lucía Fernández', medio=self.medio('Transferencia').pk)
        self.egreso(carla, 'ahora', 'Mantenimiento', 42000, 'Service de la cafetera',
                    destinatario='Técnico Ruiz', medio=self.medio('Tarjeta de débito').pk)
        e['andina'] = self.egreso(carla, 'ahora', 'Proveedores', 60000,
                                  'Pago a cuenta de la factura 10231',
                                  proveedor=pr['Distribuidora Andina'].pk,
                                  medio=self.medio('Transferencia').pk)
        carla.salir()

    def conciliar_banco(self):
        from compras.models import Egreso
        carla, e = self.carla, self.egresos
        carla.entrar()
        for clave, referencia in (('alquiler', 'TRF-88213'), ('andina', 'TRF-88290')):
            if e.get(clave):
                carla.post('compras:conciliar_egreso', {'resultado': 'acreditado',
                                                        'referencia': referencia},
                           args=[e[clave].pk])
        carla.salir()
        self.paso(f'{Egreso.objects.count()} egresos: pagados, conciliados, por vencer y vencidos')

    # -- Un día de caja ------------------------------------------------------
    PEDIDOS = [
        # (items [(clave, cantidad)], pagos [(medio, monto|None)], con cliente)
        ([('cafe_leche', 1), ('medialuna', 2)], [('Efectivo', None)], True),
        ([('cola', 2), ('alfajor', 1)], [('Efectivo', None)], False),
        ([('desayuno', 2)], [('Tarjeta de débito', None)], True),
        ([('yerba', 1), ('azucar', 2), ('galletitas', 1)], [('QR', None)], False),
        ([('queso', '0.350'), ('jamon', '0.200')], [('Efectivo', None)], True),
        ([('cafe', 2), ('choco', 1)], [('Efectivo', '2000'), ('QR', None)], False),
        ([('cola15', 2), ('agua', 4), ('alfajor', 3)], [('Crédito 3 cuotas', None)], True),
        ([('leche', 2), ('yogur', 1)], [('Efectivo', None)], False),
        ([('cafe_leche', 2), ('medialuna', 4)], [('Transferencia', None)], True),
        ([('galletitas', 2), ('yerba', 1)], [('Tarjeta de crédito', None)], False),
    ]

    def vender(self, cajero, pedido, cliente=None):
        from ventas.models import Venta
        items, pagos, _ = pedido
        cuerpo = {
            'token': uuid.uuid4().hex,
            'items': [{'producto': self.p[c].pk, 'cantidad': str(q)} for c, q in items],
            'pagos': [{'medio': self.medio(n).pk, **({'monto': m} if m else {})} for n, m in pagos],
        }
        if cliente is not None:
            cuerpo['cliente'] = cliente.pk
        respuesta = cajero.post_json('ventas:cobrar', cuerpo)
        try:
            datos = respuesta.json()
        except ValueError:
            datos = {}
        self.reloj.pasar(random.randint(8, 25))
        if not datos.get('ok'):
            destino = f' → {respuesta["Location"]}' if respuesta.has_header('Location') else ''
            self.problema(f'Venta rechazada ({cajero}) a las {timezone.localtime(self.reloj.ahora):%d/%m %H:%M}: '
                          f'{datos.get("error") or respuesta.status_code}{destino}')
            return None
        return Venta.objects.get(pk=datos['venta'])

    # -- El reloj del día -----------------------------------------------------
    def adelantar_a(self, dia, minutos):
        """Lleva el reloj a esa hora del día, nunca para atrás. Devuelve
        cuánto tiempo pasó."""
        destino = timezone.make_aware(datetime.combine(dia, time()) + timedelta(minutes=minutos))
        salto = max(destino - self.reloj.ahora, timedelta())
        self.reloj.ahora += salto
        return salto

    def horarios(self, cuantas):
        """Las horas de las ventas del día, repartidas de la mañana a la
        noche (no todas a la apertura): el mapa de calor por hora tiene
        algo que mostrar."""
        franja = (self.ULTIMA_VENTA - self.PRIMERA_VENTA) // max(cuantas, 1)
        return [self.PRIMERA_VENTA + franja * i + self.azar_horas.randint(0, max(franja - 30, 0))
                for i in range(cuantas)]

    def a_la_hora(self, cajero, caja, dia, minutos):
        """La persona vuelve a la caja a esa hora. Si desde su último clic
        pasó más que la inactividad permitida, o la sesión está por cumplir
        su duración máxima, entra de nuevo."""
        self.adelantar_a(dia, minutos)
        quieta = self.reloj.ahora - getattr(cajero, 'ultimo', self.reloj.ahora)
        abierta = self.reloj.ahora - getattr(cajero, 'desde', self.reloj.ahora)
        if (quieta > timedelta(minutes=settings.SESION_INACTIVIDAD_MINUTOS - 10)
                or abierta > timedelta(hours=settings.SESION_DURACION_HORAS) - timedelta(hours=1)):
            cajero.salir()
            cajero.entrar()
            cajero.elegir_caja(caja)

    def abrir_caja(self, cajero, caja, dia, monto='10000'):
        from ventas.models import TurnoCaja
        self.adelantar_a(dia, 8 * 60)
        cajero.entrar()
        cajero.elegir_caja(caja)
        cajero.post('ventas:abrir_turno', {'token_operacion': cajero.token(),
                                           'monto_inicial': monto})
        turno = TurnoCaja.objects.filter(id_caja_registradora=caja).order_by('-pk').first()
        if turno is None or not turno.abierto:
            self.problema(f'No se abrió {caja} el {dia:%d/%m}: {cajero.aviso}')
            cajero.salir()
            return None
        return turno

    def vender_en_el_dia(self, cajero, caja, dia, cuantas, pedidos, lista):
        """Las ventas del día a sus horas; cada una se suma a ``lista`` (el
        orden de los pedidos y los clientes sale de cuántas ya hay en ella)."""
        hechas = []
        for minutos in self.horarios(cuantas):
            self.a_la_hora(cajero, caja, dia, minutos)
            cuenta = len(lista)
            pedido = pedidos[cuenta % len(pedidos)]
            cliente = self.clientes[cuenta % (len(self.clientes) - 1)] if pedido[2] else None
            venta = self.vender(cajero, pedido, cliente)
            if venta is not None:
                lista.append(venta)
                hechas.append(venta)
        return hechas

    def historia(self):
        """Ventas de días anteriores a las dos semanas: la mercadería se
        compra (y se paga) el primer día, justo lo que se va a vender."""
        from collections import Counter
        from ventas.models import CajaRegistradora
        azar = random.getstate()        # la historia no cambia el azar de las dos semanas
        dias = sorted(self.HISTORIA, reverse=True)
        primero = self.dia(dias[0] + 1)
        necesarias, cuenta = Counter(), 0
        for atras in dias:
            for _ in range(self.HISTORIA[atras][2]):
                for clave, cantidad in self.PEDIDOS_HISTORIA[cuenta % len(self.PEDIDOS_HISTORIA)][0]:
                    necesarias[clave] += cantidad
                cuenta += 1
        costos = {'cola': 1500, 'cola15': 2600, 'agua': 800, 'galletitas': 1800, 'yerba': 3600,
                  'azucar': 950, 'choco': 1000, 'alfajor': 1300}
        golosinas = ('choco', 'alfajor')
        carla, pr = self.carla, self.proveedores
        self.adelantar_a(primero, 7 * 60)
        carla.entrar()
        for proveedor, numero, claves in (
                (pr['Distribuidora Andina'], 9870, [c for c in necesarias if c not in golosinas]),
                (pr['Golosinas del Norte'], 7302, [c for c in necesarias if c in golosinas])):
            compra = self.recibir(carla, proveedor, numero, [
                (self.p[clave], necesarias[clave], costos[clave], None) for clave in claves])
            if compra is not None:
                self.egreso(carla, 'ahora', 'Proveedores', compra.total_compra,
                            f'Pago de la factura {numero}', proveedor=proveedor.pk,
                            compra=compra.pk, medio=self.medio('Transferencia').pk)
        carla.salir()
        self.ventas_historia = []
        for atras in dias:
            quien, nombre_caja, cuantas = self.HISTORIA[atras]
            cajero, dia = getattr(self, quien), self.dia(atras)
            caja = CajaRegistradora.objects.get(nombre_caja_registradora=nombre_caja)
            turno = self.abrir_caja(cajero, caja, dia)
            if turno is None:
                continue
            self.vender_en_el_dia(cajero, caja, dia, cuantas, self.PEDIDOS_HISTORIA, self.ventas_historia)
            cajero.salir()
            self.cerrar_caja(cajero, caja, turno, dia)
        random.setstate(azar)
        self.paso(f'{len(self.ventas_historia)} ventas en {len(dias)} días, '
                  f'del {self.dia(dias[0]):%d/%m} al {self.dia(dias[-1]):%d/%m}')

    def cerrar_caja(self, cajero, caja, turno, dia, faltante=D('0'), observacion='', supervisor=None):
        """A la noche vuelve a entrar para el cierre (la sesión de la
        mañana ya venció por inactividad, como corresponde)."""
        from ventas import caja as servicio_caja
        self.adelantar_a(dia, self.CIERRE)
        cajero.entrar()
        cajero.elegir_caja(caja)
        turno.refresh_from_db()
        esperado = servicio_caja.saldo(turno)
        contado = esperado - faltante
        cajero.post('ventas:declarar_arqueo', {'token_operacion': cajero.token(),
                                               'monto_contado': str(contado)})
        if supervisor is not None:
            # Con una diferencia grande el sistema no la muestra hasta que la
            # cajera dice que su conteo es definitivo (DD-81).
            cajero.post('ventas:cierre', {'cerrar_igual': '1'})
        datos = {'token_operacion': cajero.token(), 'observacion': observacion}
        if supervisor is not None:
            datos.update({'autoriza_usuario': supervisor.usuario.nombre_usuario,
                          'autoriza_clave': CLAVE_DEMO})
        cajero.post('ventas:cierre', datos)
        turno.refresh_from_db()
        if turno.abierto:
            self.problema(f'No se cerró {caja} el {dia:%d/%m} (contado {contado}, esperado '
                          f'{esperado}): {cajero.aviso}')
        cajero.salir()

    def un_dia(self, atras, cajero, nombre_caja, cuantas):
        from ventas.models import CajaRegistradora

        dia = self.dia(atras)
        caja = CajaRegistradora.objects.get(nombre_caja_registradora=nombre_caja)
        turno = self.abrir_caja(cajero, caja, dia)
        if turno is None:
            return
        hechas = self.vender_en_el_dia(cajero, caja, dia, cuantas, self.PEDIDOS, self.ventas)

        # Lo que pasa algunos días.
        if atras == 11 and hechas:
            self.anular(cajero, hechas[-1], autoriza=self.lucia, motivo='COBRO_DUPLICADO')
        if atras == 9:
            cajero.post('ventas:mover_efectivo', {'token_operacion': cajero.token(),
                                                  'tipo': 'RETIRO', 'monto': '8000',
                                                  'concepto': 'Retiro a la caja fuerte'})
        if atras == 8 and hechas:
            # Primero quiere autorizarse sola (se rechaza y queda en el registro).
            self.anular(cajero, hechas[0], autoriza=cajero, motivo='ERROR_DE_CARGA')
            self.anular(cajero, hechas[0], autoriza=self.lucia, motivo='ERROR_DE_CARGA')
        if atras == 7:
            self.egreso(cajero, 'caja', 'Gastos varios', 1500, 'Hielo para bebidas',
                        destinatario='Almacén Don Pepe')
        if atras in (12, 10, 7, 5, 3, 2, 1):
            self.cambio_o_devolucion(cajero, atras)
        if atras == 5:
            cajero.post('ventas:mover_efectivo', {'token_operacion': cajero.token(),
                                                  'tipo': 'REFUERZO', 'monto': '5000',
                                                  'concepto': 'Cambio chico del banco'})
        cajero.salir()
        if atras == 10:
            self.cerrar_caja(cajero, caja, turno, dia, D('200'),
                             'Faltó cambio chico; se redondeó a favor del cliente.')
        elif atras == 6:
            self.cerrar_caja(cajero, caja, turno, dia, D('3500'),
                             'Se pagó un flete con plata del cajón y no se anotó.', self.lucia)
        else:
            self.cerrar_caja(cajero, caja, turno, dia)

    def anular(self, cajero, venta, autoriza, motivo):
        datos = {'token_operacion': cajero.token(), 'motivo': motivo, 'desde': 'cambios',
                 'autoriza_usuario': autoriza.usuario.nombre_usuario, 'autoriza_clave': CLAVE_DEMO}
        cajero.post('ventas:anular_venta', datos, args=[venta.pk])
        self.reloj.pasar(3)
        venta.refresh_from_db()
        if autoriza is not cajero and not venta.anulada:
            self.problema(f'No se anuló la venta N° {venta.pk} (autorizaba {autoriza}): {cajero.aviso}')

    def cambio_o_devolucion(self, cajero, atras):
        """Un cambio o una devolución sobre una venta de días anteriores."""
        from ventas.models import Cambio
        candidatas = [v for v in self.ventas if not v.anulada and not v.cambios.exists()
                      and v.fecha_venta < self.dia(atras)]
        renglon = None
        for venta in candidatas:
            renglon = next((r for r in venta.detalles.order_by('pk')
                            if r.id_producto.tipo_producto == 'REVENTA'), None)
            if renglon is not None:
                break
        if renglon is None:
            return
        cantidad = min(D('1'), renglon.cantidad)
        motivo = random.choice(['PRODUCTO_DANADO', 'CLIENTE_ARREPENTIDO', 'ERROR_DE_VENTA'])
        pago = venta.pagos.order_by('pk').first()
        datos = {'venta': venta.pk, 'token': uuid.uuid4().hex, 'accion': 'confirmar',
                 'motivo': motivo, f'devuelve_{renglon.pk}': str(cantidad),
                 f'reingresa_{renglon.pk}': '0' if motivo == 'PRODUCTO_DANADO' else '1',
                 'medio': pago.id_medio_pago_id if pago else self.medio('Efectivo').pk,
                 'autoriza_usuario': self.lucia.usuario.nombre_usuario,
                 'autoriza_clave': CLAVE_DEMO}
        antes = Cambio.objects.count()
        cajero.post('ventas:cambios', datos)
        self.reloj.pasar(5)
        if Cambio.objects.count() == antes:
            self.problema(f'No se registró la devolución de la venta N° {venta.pk}: {cajero.aviso}')

    def producir(self, dia):
        from stock.models import Produccion
        lucia = self.lucia
        lucia.entrar()
        antes = Produccion.objects.count()
        lucia.post('stock:producciones', {
            'token_operacion': lucia.token(), 'producto': self.p['medialuna'].pk,
            'cantidad': '24', 'vence': (dia + timedelta(days=2)).isoformat(),
            'observacion': 'Tanda de la mañana'})
        if Produccion.objects.count() == antes:
            self.problema(f'No se registró la producción de medialunas del {dia:%d/%m}: {lucia.aviso}')
        lucia.salir()

    # -- Mermas --------------------------------------------------------------
    def merma(self, quien, motivo, renglones, detalle, aprobar_ya=False):
        from stock.models import AjusteStock
        antes = AjusteStock.objects.count()
        datos = {'token_operacion': quien.token(), 'motivo': motivo, 'detalle': detalle}
        for i, renglon in enumerate(renglones):
            prod, cant = renglon[:2]
            datos[f'producto_{i}'] = prod.pk
            datos[f'cantidad_{i}'] = str(cant)
            if len(renglon) > 2 and renglon[2] is not None:
                datos[f'lote_{i}'] = renglon[2].pk
        if aprobar_ya:
            datos['aprobar_ya'] = '1'
        quien.post('stock:merma', datos)
        self.reloj.pasar(4)
        if AjusteStock.objects.count() == antes:
            self.problema(f'No se cargó la merma "{detalle}": {quien.aviso}')
            return None
        return AjusteStock.objects.latest('pk')

    def resolver(self, quien, ajuste, decision, observacion=''):
        if ajuste is not None:
            quien.post('stock:resolver', {'token_operacion': quien.token(),
                                          'decision': decision, 'observacion': observacion},
                       args=[ajuste.pk])
            self.reloj.pasar(2)

    def cargar_mermas(self):
        m, p = self.mermas, self.p
        self.lucia.entrar()
        m['rotura'] = self.merma(self.lucia, 'ROTURA', [(p['cola'], 2)], 'Se cayó un pack al reponer')
        from stock.models import Lote
        vencida = (Lote.objects.filter(id_producto=p['leche'], stock_lote__gt=0,
                                       fecha_vencimiento__lt=self.dia(3))
                   .order_by('fecha_vencimiento').first())
        m['vencido'] = self.merma(
            self.lucia, 'VENCIMIENTO',
            [(p['leche'], vencida.stock_lote if vencida else 1, vencida)],
            'Leche vencida retirada de la heladera')
        self.lucia.salir()
        self.tomas.entrar()
        m['secas'] = self.merma(self.tomas, 'DETERIORO', [(p['medialuna'], 4)], 'Quedaron secas del día anterior')
        m['alfajor'] = self.merma(self.tomas, 'ROTURA', [(p['alfajor'], 1)], 'Envoltorio roto')
        m['agua'] = self.merma(self.tomas, 'CONSUMO_INTERNO', [(p['agua'], 2)], 'Agua para el personal')
        self.tomas.salir()

    def resolver_mermas(self):
        from stock.models import AjusteStock
        m = self.mermas
        self.lucia.entrar()
        self.resolver(self.lucia, m.get('secas'), 'aprobar', 'Corresponde, se descartan')
        self.resolver(self.lucia, m.get('alfajor'), 'rechazar', 'Se vende con descuento, no es merma')
        self.lucia.salir()
        self.carla.entrar()       # las de Lucía las aprueba otra persona (DD-67)
        self.resolver(self.carla, m.get('rotura'), 'aprobar', 'Visto')
        self.resolver(self.carla, m.get('vencido'), 'aprobar', 'Visto')
        molienda = self.merma(self.carla, 'CONSUMO_INTERNO', [(self.p['grano'], '0.25')],
                              'Prueba de molienda de la cafetera nueva')
        self.carla.salir()
        # La de Carla también la aprueba otra persona: lo propio sólo lo
        # aprueba la titular (DD-80).
        self.lucia.entrar()
        self.resolver(self.lucia, molienda, 'aprobar', 'Visto')
        self.lucia.salir()
        self.paso(f'{AjusteStock.objects.count()} ajustes de stock: aprobados, rechazado y '
                  'uno pendiente')

    # -- Hoy -------------------------------------------------------------------
    def hoy_con_caja_abierta(self):
        """La Caja Cafetería queda abierta, con ventas para probar la anulación."""
        from ventas.models import CajaRegistradora
        self.reloj.ir_a(self.hoy, 8, 0)
        valen = self.valen
        valen.entrar()
        valen.elegir_caja(CajaRegistradora.objects.get(nombre_caja_registradora='Caja Cafetería'))
        valen.post('ventas:abrir_turno', {'token_operacion': valen.token(), 'monto_inicial': '8000'})
        self.reloj.pasar(15)
        for pedido in (self.PEDIDOS[0], self.PEDIDOS[1], self.PEDIDOS[5]):
            venta = self.vender(valen, pedido, self.clientes[0] if pedido[2] else None)
            if venta is not None:
                self.ventas.append(venta)
        valen.salir()
        self.paso('Hoy: Caja Cafetería abierta por Valentina, con 3 ventas')

    def intentos_fallidos(self):
        from seguridad.models import IntentoAcceso
        antes = IntentoAcceso.objects.count()
        for _ in range(3):
            self.diego.entrar(clave='clave-equivocada')
            self.reloj.pasar(1)
        self.pablo.entrar()       # dado de baja: no entra
        self.paso(f'{IntentoAcceso.objects.count() - antes} intentos de acceso fallidos')

    # ==================================================================
    def informar(self):
        from django.db import connection
        self.stdout.write('')
        self.stdout.write('Filas por tabla:')
        with connection.cursor() as cursor:
            for modelo in apps.get_models():
                if modelo._meta.app_label not in ('seguridad', 'geografia', 'stock', 'ventas',
                                                  'compras', 'fidelizacion', 'negocio'):
                    continue
                tabla = modelo._meta.db_table
                cursor.execute(f'SELECT count(*) FROM "{tabla}"')
                self.stdout.write(f'  {tabla:<28} {cursor.fetchone()[0]:>5}')
        self.stdout.write('')
        if self.problemas:
            self.stdout.write(self.style.ERROR(
                f'{len(self.problemas)} operación(es) no salieron como se esperaba:'))
            for texto in self.problemas:
                self.stdout.write(f'  - {texto}')
        else:
            self.stdout.write(self.style.SUCCESS('Demo cargada: todas las operaciones salieron bien.'))
        nombres = ', '.join(f'{p.usuario.nombre_usuario} ({p.usuario.id_perfil})'
                            for p in (self.carla, self.lucia, self.tomas, self.valen, self.diego))
        self.stdout.write(f'Usuarios de la demo: {nombres}. Clave de todos: {CLAVE_DEMO}')
