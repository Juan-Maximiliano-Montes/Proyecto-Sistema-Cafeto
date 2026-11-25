"""Busca registros que hoy el sistema ya no aceptaría, y los da de baja.

Las reglas del sistema crecieron sobre una base que ya tenía datos: DD-54
separó apellido y nombre, DD-55 sacó la marca y la presentación de adentro
del nombre del producto, DD-56 agregó la fecha de nacimiento. Las filas
cargadas antes de cada cambio siguen ahí y nadie las vuelve a mirar: pasan
los CHECK (que se evalúan al grabar) pero no pasarían el formulario de hoy.

Qué se considera inconsistente
------------------------------
1. Un producto cuyo nombre repite la marca o arrastra la presentación.
   Es la violación de 1FN que DD-55 vino a corregir, y es la que hace que
   el sistema no reconozca dos altas del mismo artículo.
2. Dos filas que, según la regla de unicidad VIGENTE, son el mismo
   registro. El índice único no las frenó porque se cargaron cuando la
   regla todavía no existía.
3. Una fila que ya no pasa su propio ``full_clean()``.
4. Un cliente cuyo apellido y nombre son el mismo texto: lo dejó el corte
   automático de la migración ``0005_cliente_apellido_y_nombre``, cuando
   el nombre venía en una sola palabra.

La baja es LÓGICA y por lo tanto reversible: la fila queda en la base y se
recupera con el botón Reactivar. Nada se borra, que es lo que exige RN-8.1.1
y lo que hace cumplir el mixin ``SinBajaFisica``.

Uso::

    manage.py auditar_consistencia                # sólo informa
    manage.py auditar_consistencia --dar-de-baja  # informa y da de baja
"""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from compras.models import Proveedor
from fidelizacion.models import Cliente
from nucleo.crud import REGISTRO
from nucleo.validaciones import clave_normalizada
from stock.models import MARCA_GENERICA, TIPOS_CON_PROVEEDOR, Marca, Producto

# Sufijos que delatan una presentación metida adentro del nombre.
UNIDADES = (' g', ' gr', ' kg', ' ml', ' cc', ' l', ' lt', ' un', ' u')

# Las marcas que no son de nadie: «Sin marca» y la reservada del alta simple
# (DD-86). Que el nombre diga «genérica» no repite ninguna marca.
MARCAS_QUE_NO_SON_MARCA = frozenset({clave_normalizada('Sin marca'), clave_normalizada(MARCA_GENERICA)})


class Hallazgo:
    """Una fila inconsistente, con el motivo en castellano."""

    def __init__(self, fila, tabla, motivo):
        self.fila = fila
        self.tabla = tabla
        self.motivo = motivo

    def __str__(self):
        return '%-22s id=%-5s %-44s %s' % (
            self.tabla, self.fila.pk, str(self.fila)[:44], self.motivo)


def productos_con_el_nombre_sucio():
    """DD-55: el nombre tiene que ser SÓLO la denominación.

    Con el mismo criterio que ``Producto.clean``: la marca de un producto
    comprado (reventa, insumo) es la del fabricante y no va en el nombre;
    lo que hace el local (elaborado, producido, combo) lleva la marca de la
    casa, y un combo que se llama como el local («Desayuno Cafeto») no
    repite nada. Las marcas comparan por palabra entera y normalizadas
    (sin acentos ni mayúsculas)."""
    marcas = {m.pk: clave_normalizada(m.nombre_marca) for m in Marca.objects.all()}
    marcas = {pk: clave for pk, clave in marcas.items()
              if clave and clave not in MARCAS_QUE_NO_SON_MARCA}

    for producto in Producto.objects.filter(borrado_logico=False):
        nombre = (producto.nombre_producto or '').lower().rstrip()
        palabras = f' {clave_normalizada(nombre)} '
        motivos = []
        for pk, marca in marcas.items():
            if producto.tipo_producto not in TIPOS_CON_PROVEEDOR and pk == producto.id_marca_id:
                continue
            if f' {marca} ' in palabras:
                motivos.append('el nombre repite la marca "%s"' % marca)
                break
        if any(c.isdigit() for c in nombre) and nombre.endswith(UNIDADES):
            motivos.append('el nombre arrastra la presentación')
        if motivos:
            yield Hallazgo(producto, 'productos',
                           '%s (DD-55)' % '; '.join(motivos))


def duplicados_de_identidad():
    """Dos filas que para la regla de unicidad de hoy son la misma."""
    for _clave, cfg in sorted(REGISTRO.items()):
        # Provincias y localidades no tienen baja lógica: son catálogo
        # geográfico fijo, y además nadie los carga a mano.
        if not cfg.tiene_baja or not cfg.nombre_es_identidad:
            continue
        vistos = {}
        for fila in cfg.modelo.objects.filter(borrado_logico=False):
            identidad = [clave_normalizada(
                str(getattr(fila, cfg.campo_nombre) or ''))]
            for campo in cfg.ambito_unico:
                identidad.append(str(getattr(fila, campo, None)))
            identidad = tuple(identidad)
            if identidad in vistos:
                yield Hallazgo(
                    fila, cfg.modelo._meta.db_table,
                    'misma identidad que el id %d, que se conserva'
                    % vistos[identidad])
            else:
                vistos[identidad] = fila.pk


def filas_que_no_pasan_su_clean():
    """Lo que el formulario de hoy rechazaría."""
    for modelo in (Cliente, Proveedor):
        for fila in modelo.objects.filter(borrado_logico=False):
            try:
                fila.full_clean(exclude=['usuario_modificacion'])
            except ValidationError as error:
                detalle = '; '.join(
                    '%s: %s' % (campo, ' '.join(mensajes))
                    for campo, mensajes in error.message_dict.items())
                yield Hallazgo(fila, modelo._meta.db_table, detalle[:150])


def clientes_sin_apellido_real():
    """DD-54: el corte automático dejó apellido y nombre iguales."""
    for cliente in Cliente.objects.filter(borrado_logico=False):
        apellido = (cliente.apellido_cliente or '').strip().lower()
        nombre = (cliente.nombre_cliente or '').strip().lower()
        if apellido and apellido == nombre:
            yield Hallazgo(
                cliente, 'clientes',
                'apellido y nombre son el mismo texto: quedó del corte '
                'automático de la migración 0005 (DD-54)')


AUDITORIAS = (
    productos_con_el_nombre_sucio,
    duplicados_de_identidad,
    filas_que_no_pasan_su_clean,
    clientes_sin_apellido_real,
)


# ---------------------------------------------------------------------------
#  DERIVAS DE ESQUEMA (DB-RPH-01)
# ---------------------------------------------------------------------------
# El archivo histórico se crea con ``LIKE <tabla viva>`` y desde entonces
# nadie lo mantiene solo: cada vez que una migración le agrega, le saca o le
# cambia el tipo a una columna de la tabla viva, hay que acordarse de hacer
# lo mismo en su ``hist_*``. Si se olvida, el pase a histórico falla (o, peor,
# copia a la columna equivocada) justo la noche en que hay algo para archivar.
# Esta auditoría lo detecta antes: compara columna por columna.
COLUMNA_SOLO_DEL_HISTORICO = 'fecha_archivo'

COLUMNAS_DEL_ESQUEMA = """
    SELECT column_name, udt_name, character_maximum_length,
           numeric_precision, numeric_scale
      FROM information_schema.columns
     WHERE table_schema = current_schema() AND table_name = %s
"""

TABLAS_CON_HISTORICO_VIGENTES = """
    SELECT h.table_name
      FROM information_schema.tables h
      JOIN information_schema.tables v
        ON v.table_schema = h.table_schema
       AND v.table_name = substr(h.table_name, 6)
     WHERE h.table_schema = current_schema()
       AND h.table_type = 'BASE TABLE' AND v.table_type = 'BASE TABLE'
       AND h.table_name LIKE 'hist\\_%'
     ORDER BY h.table_name
"""


class DerivaDeEsquema:
    """Una diferencia entre una tabla viva y su histórico.

    No es una fila que se pueda dar de baja: se arregla con una migración.
    """

    def __init__(self, tabla, historico, motivo):
        self.tabla = tabla
        self.historico = historico
        self.motivo = motivo

    def __str__(self):
        return '%-22s %-28s %s' % (self.tabla, self.historico, self.motivo)


def _columnas(cursor, tabla):
    """``{columna: (tipo, largo, precisión, escala)}`` de una tabla."""
    cursor.execute(COLUMNAS_DEL_ESQUEMA, [tabla])
    return {nombre: tuple(forma) for nombre, *forma in cursor.fetchall()}


def _forma(firma):
    tipo, largo, precision, escala = firma
    if largo:
        return '%s(%s)' % (tipo, largo)
    if tipo == 'numeric' and precision:
        return 'numeric(%s,%s)' % (precision, escala or 0)
    return tipo


def derivas_de_esquema():
    """Las columnas que no coinciden entre cada tabla con histórico y su
    ``hist_*``.

    Se compara el nombre y la forma del tipo (no el NOT NULL ni el orden:
    el histórico es una fotografía y el archivado copia por nombre). La única
    columna que el histórico tiene de más es ``fecha_archivo``.
    """
    with connection.cursor() as cursor:
        cursor.execute(TABLAS_CON_HISTORICO_VIGENTES)
        historicos = [fila[0] for fila in cursor.fetchall()]
        for historico in historicos:
            tabla = historico[len('hist_'):]
            vivas = _columnas(cursor, tabla)
            archivadas = _columnas(cursor, historico)
            for columna in vivas.keys() - archivadas.keys():
                yield DerivaDeEsquema(
                    tabla, historico,
                    'a %s le falta la columna "%s": el pase a histórico '
                    'fallaría' % (historico, columna))
            for columna in archivadas.keys() - vivas.keys() - {COLUMNA_SOLO_DEL_HISTORICO}:
                yield DerivaDeEsquema(
                    tabla, historico,
                    '%s tiene la columna "%s" que ya no existe en %s'
                    % (historico, columna, tabla))
            if COLUMNA_SOLO_DEL_HISTORICO not in archivadas:
                yield DerivaDeEsquema(
                    tabla, historico,
                    '%s no tiene la columna "%s"' % (historico, COLUMNA_SOLO_DEL_HISTORICO))
            for columna in sorted(vivas.keys() & archivadas.keys()):
                if vivas[columna] != archivadas[columna]:
                    yield DerivaDeEsquema(
                        tabla, historico,
                        'la columna "%s" es %s en %s y %s en %s'
                        % (columna, _forma(vivas[columna]), tabla,
                           _forma(archivadas[columna]), historico))


class Command(BaseCommand):
    help = 'Informa (y opcionalmente da de baja) los registros inconsistentes.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dar-de-baja', action='store_true',
            help='Además de informar, marca borrado_logico en lo encontrado.')

    def handle(self, *args, **opciones):
        hallazgos = []
        vistos = set()
        for auditoria in AUDITORIAS:
            for hallazgo in auditoria():
                marca = (hallazgo.tabla, hallazgo.fila.pk)
                if marca in vistos:
                    continue
                vistos.add(marca)
                hallazgos.append(hallazgo)

        # Las derivas de esquema se informan siempre y aparte: no son filas,
        # no se dan de baja, se arreglan con una migración.
        derivas = list(derivas_de_esquema())
        if derivas:
            self.stdout.write(self.style.ERROR(
                'DERIVAS DE ESQUEMA ENTRE TABLAS VIVAS E HISTÓRICO: %d' % len(derivas)))
            self.stdout.write('')
            for deriva in derivas:
                self.stdout.write('  ' + str(deriva))
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'Esto no se corrige con --dar-de-baja: hace falta una '
                'migración que iguale la tabla hist_* a su tabla viva.'))
            self.stdout.write('')
        else:
            self.stdout.write(self.style.SUCCESS(
                'Esquema del histórico: cada hist_* coincide con su tabla viva.'))

        if not hallazgos:
            self.stdout.write(self.style.SUCCESS(
                'No hay registros inconsistentes: todo lo vigente pasa las '
                'reglas de hoy.'))
            return

        self.stdout.write('REGISTROS INCONSISTENTES: %d' % len(hallazgos))
        self.stdout.write('')
        for hallazgo in hallazgos:
            self.stdout.write('  ' + str(hallazgo))
        self.stdout.write('')

        if not opciones['dar_de_baja']:
            self.stdout.write(self.style.WARNING(
                'Nada se modificó. Volvé a correrlo con --dar-de-baja para '
                'darlos de baja (la baja es lógica y se puede reactivar).'))
            return

        with transaction.atomic():
            for hallazgo in hallazgos:
                hallazgo.fila.borrado_logico = True
                hallazgo.fila.save(update_fields=['borrado_logico'])

        self.stdout.write(self.style.SUCCESS(
            '%d registro(s) dados de baja LÓGICA. Siguen en la base y se '
            'recuperan con el botón Reactivar.' % len(hallazgos)))
