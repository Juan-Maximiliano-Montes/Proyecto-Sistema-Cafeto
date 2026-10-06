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
y lo que hace cumplir el trigger ``trg_sin_baja_fisica``.

Uso::

    manage.py auditar_consistencia                # sólo informa
    manage.py auditar_consistencia --dar-de-baja  # informa y da de baja
"""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction

from cafeto.validaciones import clave_normalizada
from maestros.crud import REGISTRO
from maestros.models import Marca, Producto
from operaciones.models import Cliente
from proveedores.models import Proveedor

# Sufijos que delatan una presentación metida adentro del nombre.
UNIDADES = (' g', ' gr', ' kg', ' ml', ' cc', ' l', ' lt', ' un', ' u')


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
    """DD-55: el nombre tiene que ser SÓLO la denominación."""
    marcas = [m.nombre_marca.lower()
              for m in Marca.objects.all()
              if m.nombre_marca.lower() != 'sin marca']

    for producto in Producto.objects.filter(borrado_logico=False):
        nombre = (producto.nombre_producto or '').lower().rstrip()
        motivos = []
        for marca in marcas:
            if marca in nombre:
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
        if not cfg.tiene_baja:
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
