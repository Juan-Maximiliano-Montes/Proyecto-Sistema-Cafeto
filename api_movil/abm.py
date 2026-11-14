"""El ABM liviano de marcas y rubros desde el celular.

Las reglas son las del modelo, no unas propias de la API:

* **Alta y modificación** pasan por ``full_clean()``: los textos se limpian
  y se miden como en la web (``texto_de_catalogo``) y un nombre repetido
  —sin distinguir mayúsculas, acentos ni puntuación— se rechaza en el campo
  (``exigir_nombre_unico``).
* **Baja** es lógica (RN-8.1.1) y se pregunta antes
  ``motivos_que_impiden_la_baja()``: con productos vigentes, 409 con el
  mismo texto que la web. Reactivar queda en la web.
* **Auditoría**: alta, modificación y baja quedan en el Registro de
  Actividad con las fotos de antes y después, como en el CRUD de la web.
* **Concurrencia**: la fila se toma con ``select_for_update()`` antes de
  modificarla o darla de baja, y si dos altas iguales llegan a la vez la
  base frena la segunda y se responde con el mismo aviso de duplicado.
"""
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse

from api_movil import respuestas
from api_movil.autenticacion import desde_el_celular
from nucleo.busqueda import filtrar_por_texto
from nucleo.motivos import redactar
from seguridad.servicios import instantanea, registrar_auditoria
from stock.models import Marca, Rubro

# Los avisos genéricos de los campos de Django ("Este campo no puede estar
# en blanco."). El ``clean()`` del modelo dice lo mismo, mejor y en
# castellano del sistema ("El nombre de la marca es obligatorio."): si un
# campo tiene los dos, se manda el del modelo.
GENERICOS = frozenset({'null', 'blank', 'max_length', 'invalid'})


@dataclass(frozen=True)
class Catalogo:
    """Lo que cambia entre el ABM de marcas y el de rubros."""

    modelo: type
    entidad: str                        # para la auditoría: "ALTA DE MARCA"
    no_existe: str                      # el 404
    campos: dict                        # campo de la API -> campo del modelo
    booleanos: dict = field(default_factory=dict)  # campo -> aviso si no es bool

    @property
    def nombre(self):
        return self.campos['nombre']

    def a_json(self, fila):
        datos = {'id': fila.pk}
        for campo, del_modelo in self.campos.items():
            valor = getattr(fila, del_modelo)
            datos[campo] = valor if campo in self.booleanos else (valor or '')
        return datos


MARCAS = Catalogo(
    modelo=Marca, entidad='MARCA', no_existe='Esa marca ya no existe.',
    campos={'nombre': 'nombre_marca', 'descripcion': 'descripcion_marca'})

RUBROS = Catalogo(
    modelo=Rubro, entidad='RUBRO', no_existe='Ese rubro ya no existe.',
    campos={'nombre': 'nombre_rubro', 'descripcion': 'descripcion_rubro',
            'activo': 'activo'},
    # "activo" es "se ofrece en el alta de productos", no la baja. La
    # rebaja por vencimiento del rubro se edita sólo en la web.
    booleanos={'activo': 'Indicá si se ofrece o no.'})


# ---------------------------------------------------------------------------
#  Errores, con los nombres de campo de la API
# ---------------------------------------------------------------------------
def _campo_del_texto(mensaje):
    """A qué campo va un error que el modelo levantó sin campo.

    ``texto_de_catalogo`` levanta sus errores sueltos, y ``full_clean()``
    los junta bajo ``__all__``. Cada uno empieza con la etiqueta del campo
    («La descripción no puede…»): de ahí se sabe de cuál es.
    """
    if mensaje.startswith('La descripción'):
        return 'descripcion'
    return 'nombre'


def errores_por_campo(cfg, error):
    """El ``ValidationError`` de ``full_clean()`` como ``{campo: [textos]}``."""
    del_modelo = {modelo: api for api, modelo in cfg.campos.items()}
    propios, genericos = {}, {}
    for campo, lista in error.error_dict.items():
        for item in lista:
            grupo = genericos if item.code in GENERICOS else propios
            for mensaje in item.messages:
                destino = del_modelo.get(campo) or _campo_del_texto(mensaje)
                textos = grupo.setdefault(destino, [])
                if mensaje not in textos:
                    textos.append(mensaje)
    resultado = dict(propios)
    for campo, textos in genericos.items():
        resultado.setdefault(campo, textos)
    return resultado


def _tipos_invalidos(cfg, datos):
    """Lo que llegó con un tipo que no corresponde, antes de tocar el modelo.

    Un texto tiene que ser texto (o null), y un sí/no, ``true`` o
    ``false``: un ``"1"`` o un ``0`` no se adivinan.
    """
    problemas = {}
    for campo in cfg.campos:
        if campo not in datos:
            continue
        valor = datos[campo]
        if campo in cfg.booleanos:
            if not isinstance(valor, bool):
                problemas[campo] = [cfg.booleanos[campo]]
        elif valor is not None and not isinstance(valor, str):
            problemas[campo] = ['Tiene que ser un texto.']
    return problemas


def _asignar(cfg, fila, datos):
    for campo, del_modelo in cfg.campos.items():
        if campo in datos:
            setattr(fila, del_modelo, datos[campo])


def _validar(cfg, fila):
    """``full_clean()``: los errores por campo, o ``None`` si está bien."""
    try:
        fila.full_clean()
    except ValidationError as error:
        return errores_por_campo(cfg, error)
    return None


def _grabar(cfg, fila):
    """Guarda lo ya validado: los errores por campo, o ``None`` si guardó."""
    try:
        with transaction.atomic():
            fila.save()
    except IntegrityError:
        # Otra alta igual se confirmó entre la validación y el guardado: el
        # índice único la frenó. Validar de nuevo da el aviso de duplicado.
        problemas = _validar(cfg, fila)
        if problemas:
            return problemas
        raise
    return None


def _datos(request, cfg):
    """El cuerpo del pedido ya controlado: ``(datos, None)`` o ``(None, respuesta)``."""
    datos = respuestas.cuerpo(request)
    if datos is None:
        return None, respuestas.error(respuestas.DATOS_INVALIDOS, 400)
    problemas = _tipos_invalidos(cfg, datos)
    if problemas:
        return None, respuestas.errores(problemas)
    return datos, None


def _vigente(cfg, pk, bloquear=False):
    filas = cfg.modelo.objects.filter(pk=pk, borrado_logico=False)
    if bloquear:
        filas = filas.select_for_update()
    return filas.first()


# ---------------------------------------------------------------------------
#  Las operaciones
# ---------------------------------------------------------------------------
def listar(request, cfg):
    """Los vigentes, por nombre, de a 20. ``q`` busca sin tildes ni mayúsculas."""
    filas = cfg.modelo.objects.filter(borrado_logico=False)
    filas = filtrar_por_texto(
        filas, (cfg.nombre, cfg.campos['descripcion']),
        request.GET.get('q', '').strip())
    return respuestas.paginar(request, filas.order_by(cfg.nombre, 'pk'),
                              cfg.a_json)


def crear(request, cfg):
    datos, rechazo = _datos(request, cfg)
    if rechazo:
        return rechazo
    fila = cfg.modelo()
    _asignar(cfg, fila, datos)
    problemas = _validar(cfg, fila) or _grabar(cfg, fila)
    if problemas:
        return respuestas.errores(problemas)
    registrar_auditoria(request, 'ALTA', cfg.entidad, nuevo=instantanea(fila),
                        instancia=fila, detalle=desde_el_celular(request))
    return JsonResponse(cfg.a_json(fila), status=201)


def editar(request, cfg, pk):
    """Cambia sólo los campos que llegan (PATCH). Sin cambios, no guarda."""
    fila = _vigente(cfg, pk, bloquear=True)
    if fila is None:
        return respuestas.error(cfg.no_existe, 404)
    datos, rechazo = _datos(request, cfg)
    if rechazo:
        return rechazo

    anterior = instantanea(fila)
    _asignar(cfg, fila, datos)
    # La validación limpia los textos como al guardar: se compara lo que de
    # verdad quedaría ("Arcor " no es un cambio de "Arcor"). Lo que no
    # cambia nada no se guarda ni ensucia el Registro de Actividad.
    problemas = _validar(cfg, fila)
    if problemas:
        return respuestas.errores(problemas)
    despues = instantanea(fila, list(cfg.campos.values()))
    if all(anterior[columna] == valor for columna, valor in despues.items()):
        return JsonResponse(cfg.a_json(fila))

    problemas = _grabar(cfg, fila)
    if problemas:
        return respuestas.errores(problemas)
    registrar_auditoria(request, 'MODIFICACION', cfg.entidad,
                        anterior=anterior,
                        nuevo=instantanea(cfg.modelo.objects.get(pk=fila.pk)),
                        instancia=fila, detalle=desde_el_celular(request))
    return JsonResponse(cfg.a_json(fila))


def dar_de_baja(request, cfg, pk):
    """Baja lógica, si nada vigente depende del registro."""
    fila = _vigente(cfg, pk, bloquear=True)
    if fila is None:
        return respuestas.error(cfg.no_existe, 404)
    motivos = fila.motivos_que_impiden_la_baja()
    if motivos:
        return respuestas.error(
            redactar(f'No se puede dar de baja "{fila}"', motivos), 409)

    anterior = instantanea(fila)
    fila.borrado_logico = True
    fila.save(update_fields=['borrado_logico'])
    registrar_auditoria(request, 'BAJA', cfg.entidad, anterior=anterior,
                        nuevo=instantanea(cfg.modelo.objects.get(pk=fila.pk)),
                        instancia=fila,
                        detalle=f'Baja lógica de "{fila}". '
                                + desde_el_celular(request))
    return HttpResponse(status=204)
