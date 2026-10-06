"""Máquina genérica de los CRUD de datos maestros (DFD 10).

Las nueve pantallas de "Mantener datos maestros" hacen lo mismo: listar
con búsqueda y filtros, dar de alta, editar, dar de baja lógica y
reactivar, auditando cada paso. Escribirlas nueve veces sería nueve veces
la misma oportunidad de olvidarse una validación.

Así que la lógica está UNA vez, acá, y cada entidad aporta una
``ConfiguracionCRUD``: su modelo, su formulario, cómo se llama en
castellano, qué columnas muestra y por qué campos se busca y se filtra.

Lo que esta máquina garantiza para todas, sin excepción:

  V-G7   los combos y los ids recibidos se controlan contra lo vigente
  V-G8   el duplicado se busca por clave normalizada; si estaba dado de
         baja, se ofrece reactivarlo en vez de crear una fila nueva
  V-G10  no se da de baja algo con dependientes vigentes, ni se reactiva
         algo cuyos padres están de baja; el mensaje dice cuántos y cuáles
  V-G11  bloqueo optimista por ``fecha_modificacion``
  V-G12  POST-Redirect-GET
  V-G13  permiso validado en el servidor, 404 por id inexistente, y no se
         edita un registro dado de baja sin reactivarlo antes
  4.4 d  auditoría de cada alta, modificación, baja y reactivación
"""
from dataclasses import dataclass, field
from functools import wraps

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from cafeto.restricciones import ClaveNormalizada
from cafeto.validaciones import clave_normalizada
from operaciones.servicios import instantanea, registrar_auditoria


# ===========================================================================
#  CONFIGURACIÓN
# ===========================================================================
@dataclass(frozen=True)
class Columna:
    """Una columna del listado."""
    etiqueta: str
    atributo: str
    # 'texto' | 'numero' | 'si_no' | 'dinero'
    formato: str = 'texto'


@dataclass(frozen=True)
class Filtro:
    """El filtro natural de la entidad (rubro, proveedor, provincia...)."""
    clave: str            # nombre del parámetro en la URL
    etiqueta: str
    campo: str            # campo del modelo por el que filtra
    # Función sin argumentos que devuelve las opciones (valor, etiqueta).
    opciones: callable


@dataclass(frozen=True)
class ConfiguracionCRUD:
    clave: str                     # 'rubros' — va en la URL y en los permisos
    modelo: type
    formulario: type
    singular: str                  # 'rubro'
    plural: str                    # 'rubros'
    articulo: str                  # 'el' / 'la'
    titulo: str                    # 'Rubros'
    icono: str                     # clase de Bootstrap Icons
    campo_nombre: str              # campo por el que se busca el duplicado
    permiso_singular: str          # 'rubro' -> crear_rubro, editar_rubro...
    columnas: tuple
    campos_busqueda: tuple
    espacio_urls: str = 'maestros'
    filtros: tuple = ()
    tiene_baja: bool = True
    relaciones: tuple = ()
    orden: tuple = ()
    # Campos que ACOMPAÑAN al nombre en la regla de unicidad. Una localidad
    # es única dentro de su provincia; un producto, dentro de su marca y su
    # presentación. Vacío = el nombre es único por sí solo.
    ambito_unico: tuple = ()

    # -- concordancia de género --------------------------------------------
    @property
    def terminacion(self):
        """'a' o 'o', según el artículo de la entidad.

        Para que los avisos concuerden: "el rubro creado", "la categoría
        creada". Es un detalle, pero un mensaje mal concordado le avisa al
        usuario que el sistema no lo está mirando a él.
        """
        return 'a' if self.articulo == 'la' else 'o'

    def participio(self, raiz):
        """``participio('cread')`` -> 'creado' o 'creada'."""
        return raiz + self.terminacion

    # -- nombres de permiso ------------------------------------------------
    def permiso(self, accion):
        if accion == 'ver':
            return f'ver_{self.clave}'
        return f'{accion}_{self.permiso_singular}'

    def url(self, accion, pk=None):
        nombre = f'{self.espacio_urls}:{self.clave}_{accion}'
        return reverse(nombre, args=[pk] if pk is not None else None)


# Registro global: lo llenan maestros/views.py y proveedores/views.py.
REGISTRO = {}


def registrar(configuracion):
    REGISTRO[configuracion.clave] = configuracion
    return configuracion


def obtener(clave):
    from django.http import Http404
    try:
        return REGISTRO[clave]
    except KeyError:
        raise Http404(f'No existe la pantalla de datos maestros "{clave}".')


# ===========================================================================
#  PERMISOS
# ===========================================================================
def exige(accion):
    """Decorador: comprueba en el SERVIDOR el permiso de esa acción.

    No alcanza con esconder el botón: alguien puede escribir la URL. El
    decorador corta antes de ejecutar la vista. Si falta el permiso de
    lectura no hay a dónde rebotar dentro del módulo, así que lo manda a
    su ficha personal.
    """
    def decorador(vista):
        @wraps(vista)
        def envoltura(request, clave, *args, **kwargs):
            configuracion = obtener(clave)
            usuario = getattr(request, 'usuario_actual', None)
            if usuario is None:
                return redirect('usuarios:login')

            codigo = configuracion.permiso(accion)
            if not usuario.tiene_permiso(codigo):
                messages.error(
                    request,
                    'Acceso denegado: tu perfil no tiene habilitado el '
                    f'permiso necesario para esta acción ("{codigo}").')
                if accion == 'ver':
                    return redirect('mi_perfil')
                return redirect(configuracion.url('lista'))

            return vista(request, configuracion, *args, **kwargs)
        return envoltura
    return decorador


# ===========================================================================
#  AYUDAS
# ===========================================================================
def _queryset(configuracion):
    consulta = configuracion.modelo.objects.all()
    if configuracion.relaciones:
        consulta = consulta.select_related(*configuracion.relaciones)
    if configuracion.orden:
        consulta = consulta.order_by(*configuracion.orden)
    return consulta


def buscar_duplicado(configuracion, instancia):
    """Devuelve la fila que ya ocupa esa identidad, o None (V-G8).

    Compara por clave normalizada con la MISMA función que la base
    (``f_clave_normalizada``), así que lo que encuentra acá es exactamente
    lo que rechazaría el índice único. La diferencia es el mensaje: acá se
    puede decir "ya existe" y, si está dado de baja, ofrecer reactivarlo en
    vez de crear otra fila (D-10).

    Trabaja sobre la INSTANCIA ya validada, no sobre el POST crudo: así los
    valores vienen con su tipo (un contenido es un Decimal, no el texto
    "1,5") y la comparación es la misma que hará PostgreSQL.
    """
    valor = getattr(instancia, configuracion.campo_nombre, None)
    if not valor:
        return None

    consulta = configuracion.modelo.objects.annotate(
        _clave=ClaveNormalizada(configuracion.campo_nombre)
    ).filter(_clave=clave_normalizada(valor))

    if instancia.pk:
        consulta = consulta.exclude(pk=instancia.pk)

    # El resto de la identidad: la provincia de la localidad, la marca y la
    # presentación del producto.
    for campo in configuracion.ambito_unico:
        valor_campo = getattr(instancia, campo, None)
        if valor_campo is None:
            return None
        consulta = consulta.filter(**{campo: valor_campo})

    return consulta.first()


def _avisar_duplicado(request, configuracion, duplicado):
    """Arma el aviso de duplicado, con botón a reactivar si corresponde."""
    nombre = getattr(duplicado, configuracion.campo_nombre)
    if configuracion.tiene_baja and getattr(duplicado, 'borrado_logico', False):
        messages.warning(request, (
            f'Ya existe {configuracion.articulo} {configuracion.singular} '
            f'"{nombre}", pero está {configuracion.participio("dad")} de baja. '
            f'No se crea {configuracion.articulo} nuev{configuracion.terminacion}: '
            'buscalo con el filtro "Dados de baja" y reactivalo.'))
    else:
        messages.error(
            request,
            f'Ya existe {configuracion.articulo} {configuracion.singular} '
            f'"{nombre}".')


def _contexto(request, configuracion, extra=None):
    """Arma el contexto del listado: búsqueda, filtros y tabla."""
    busqueda = request.GET.get('busqueda', '').strip()
    estado = request.GET.get('estado', 'vigentes')

    filas = _queryset(configuracion)

    if configuracion.tiene_baja:
        if estado == 'vigentes':
            filas = filas.filter(borrado_logico=False)
        elif estado == 'baja':
            filas = filas.filter(borrado_logico=True)

    if busqueda:
        condicion = Q()
        for campo in configuracion.campos_busqueda:
            condicion |= Q(**{f'{campo}__icontains': busqueda})
        filas = filas.filter(condicion)

    filtros_aplicados = {}
    for filtro in configuracion.filtros:
        valor = request.GET.get(filtro.clave, '')
        filtros_aplicados[filtro.clave] = valor
        if valor:
            filas = filas.filter(**{filtro.campo: valor})

    contexto = {
        'cfg': configuracion,
        'filas': filas,
        'total': filas.count(),
        'busqueda': busqueda,
        'estado': estado,
        'formulario': configuracion.formulario(),
        'filtros': [
            (filtro, filtro.opciones(), filtros_aplicados[filtro.clave])
            for filtro in configuracion.filtros
        ],
        'url_lista': configuracion.url('lista'),
        'url_crear': configuracion.url('crear'),
        'permiso_crear': configuracion.permiso('crear'),
        'permiso_editar': configuracion.permiso('editar'),
        'permiso_eliminar': configuracion.permiso('eliminar'),
        'permiso_reactivar': configuracion.permiso('reactivar'),
    }
    contexto.update(extra or {})
    return contexto


def _mensaje_de_base(error):
    """Traduce un error de PostgreSQL a castellano (V-G14).

    Nunca se muestra el texto del SQL ni el nombre de la restricción: eso
    le dice al usuario cómo se llama una tabla, no qué hizo mal.
    """
    texto = str(error)
    if 'uq_' in texto and 'norm' in texto:
        return ('Ya existe un registro con ese nombre (no se distinguen '
                'mayúsculas ni acentos).')
    if 'ck_' in texto or 'check constraint' in texto.lower():
        return ('Los datos no cumplen una regla del sistema. Revisá los '
                'valores cargados.')
    if 'unique' in texto.lower() or 'uq_' in texto:
        return 'Ya existe un registro con ese valor.'
    if 'foreign key' in texto.lower() or 'fk_' in texto:
        return ('El registro está relacionado con otros datos y no se puede '
                'modificar así.')
    return ('No se pudo guardar: los datos no cumplen una regla del sistema. '
            'Revisá lo cargado y volvé a intentar.')


# ===========================================================================
#  VISTAS
# ===========================================================================
@exige('ver')
def listar(request, configuracion):
    """Listado con búsqueda, filtro por estado y el filtro propio."""
    return render(request, 'maestros/lista.html',
                  _contexto(request, configuracion))


@exige('ver')
def ver(request, configuracion, pk):
    """Ficha de sólo lectura de una fila, para el modal del ojo.

    Devuelve un fragmento de HTML que el listado inserta por fetch, igual
    que hace con el formulario de edición. Es GET y no toca nada: el
    permiso que pide es el mismo ``ver_*`` con el que se entró al listado.

    Los valores salen del FORMULARIO y no de las columnas de la tabla. El
    listado muestra un resumen; la ficha tiene que mostrar todo lo que se
    puede cargar, incluidos los campos que no entran en la grilla.
    """
    instancia = get_object_or_404(configuracion.modelo, pk=pk)
    return render(request, 'maestros/_ficha.html', {
        'cfg': configuracion,
        'fila': instancia,
        'datos': datos_de_la_ficha(configuracion, instancia),
    })


def datos_de_la_ficha(configuracion, instancia):
    """[(etiqueta, valor legible), ...] de todos los campos cargables.

    Para cada campo del formulario se busca el valor que una persona
    entiende, en este orden:

      * ``get_<campo>_display()`` si el campo tiene opciones (``AUTOMATICO``
        se lee "Automático");
      * el ``__str__`` del objeto relacionado si es una clave foránea (el
        nombre de la marca, no su id);
      * el valor crudo en cualquier otro caso.
    """
    formulario = configuracion.formulario(instance=instancia)
    guardados = {f.name for f in configuracion.modelo._meta.get_fields()}
    datos = []
    for campo in formulario.visible_fields():
        nombre = campo.name
        # Un control que no es columna del modelo no se guarda en ningún
        # lado: "confirmo vender por debajo del costo" es una decisión que
        # se toma al cargar, y queda en la auditoría, no en la fila. En una
        # ficha de lo ALMACENADO sólo podría mostrar un guión.
        if nombre not in guardados:
            continue
        mostrar = getattr(instancia, 'get_%s_display' % nombre, None)
        if callable(mostrar):
            valor = mostrar()
        else:
            valor = getattr(instancia, nombre, None)
        datos.append((campo.label, valor))
    return datos


@exige('crear')
def crear(request, configuracion):
    """Alta. POST-Redirect-GET y auditoría del registro nuevo."""
    if request.method != 'POST':
        return redirect(configuracion.url('lista'))

    formulario = configuracion.formulario(request.POST)
    valido = formulario.is_valid()

    # El duplicado se mira ANTES de decidir qué mostrar, esté el formulario
    # válido o no: cuando lo que falla es justamente el índice único, Django
    # ya marcó el formulario como inválido con su mensaje genérico, y el que
    # sirve es el nuestro. ``formulario.instance`` tiene los valores
    # cargados —Django la arma antes de validar—, así que se puede consultar
    # igual (ver buscar_duplicado).
    duplicado = buscar_duplicado(configuracion, formulario.instance)
    if duplicado is not None:
        _avisar_duplicado(request, configuracion, duplicado)
        return redirect(configuracion.url('lista'))

    if valido:
        instancia = formulario.save(commit=False)

        try:
            with transaction.atomic():
                instancia.save()
                formulario.save_m2m()
                registrar_auditoria(
                    request, 'ALTA', configuracion.singular.upper(),
                    anterior=None, nuevo=instantanea(instancia),
                    detalle=_detalle(configuracion, instancia, formulario))
        except IntegrityError as error:
            messages.error(request, _mensaje_de_base(error))
            return redirect(configuracion.url('lista'))

        messages.success(
            request,
            f'{configuracion.singular.capitalize()} "{instancia}" '
            f'{configuracion.participio("cread")} correctamente.')
        return redirect(configuracion.url('lista'))

    messages.error(
        request,
        f'No se pudo crear {configuracion.articulo} {configuracion.singular}. '
        'Revisá los datos marcados.')
    contexto = _contexto(request, configuracion, {
        'formulario': formulario, 'abrir_modal_crear': True})
    return render(request, 'maestros/lista.html', contexto)


@exige('editar')
def editar(request, configuracion, pk):
    """Modificación. Guarda la foto de antes para la auditoría."""
    instancia = get_object_or_404(configuracion.modelo, pk=pk)

    # V-G13: un registro dado de baja se reactiva antes de editarse.
    if configuracion.tiene_baja and instancia.borrado_logico:
        messages.error(request, (
            f'{configuracion.singular.capitalize()} "{instancia}" está '
            f'{configuracion.participio("dad")} de baja: reactivalo antes de '
            'modificarlo.'))
        return redirect(configuracion.url('lista'))

    # El modal pide el formulario por GET para dibujarlo con los datos
    # ACTUALES de la fila, incluido el testigo de versión de V-G11.
    if request.method == 'GET':
        if request.GET.get('formulario'):
            return render(request, 'maestros/_campos.html', {
                'form': configuracion.formulario(instance=instancia)})
        return redirect(configuracion.url('lista'))

    if request.method != 'POST':
        return redirect(configuracion.url('lista'))

    anterior = instantanea(configuracion.modelo.objects.get(pk=pk))
    formulario = configuracion.formulario(request.POST, instance=instancia)
    valido = formulario.is_valid()

    duplicado = buscar_duplicado(configuracion, formulario.instance)
    if duplicado is not None:
        _avisar_duplicado(request, configuracion, duplicado)
        return redirect(configuracion.url('lista'))

    if valido:
        modificada = formulario.save(commit=False)

        try:
            with transaction.atomic():
                # V-G11: la fila se toma para escribir hasta el commit.
                configuracion.modelo.objects.select_for_update().get(pk=pk)
                modificada.save()
                formulario.save_m2m()
                registrar_auditoria(
                    request, 'MODIFICACION', configuracion.singular.upper(),
                    anterior=anterior,
                    nuevo=instantanea(
                        configuracion.modelo.objects.get(pk=pk)),
                    detalle=_detalle(configuracion, modificada, formulario))
        except IntegrityError as error:
            messages.error(request, _mensaje_de_base(error))
            return redirect(configuracion.url('lista'))

        messages.success(
            request,
            f'{configuracion.singular.capitalize()} "{modificada}" '
            f'{configuracion.participio("actualizad")} correctamente.')
        return redirect(configuracion.url('lista'))

    messages.error(
        request,
        f'No se pudo actualizar {configuracion.articulo} '
        f'{configuracion.singular}. Revisá los datos marcados.')
    contexto = _contexto(request, configuracion, {
        'formulario_editar': formulario,
        'editar_pk': pk,
        'abrir_modal_editar': True})
    return render(request, 'maestros/lista.html', contexto)


@exige('eliminar')
def dar_de_baja(request, configuracion, pk):
    """BAJA LÓGICA (RN-8.1.1). Nunca un DELETE.

    Antes de bajar se le pregunta al registro si puede: cada modelo sabe
    qué dependientes vigentes lo necesitan y devuelve los motivos ya
    redactados, con las cantidades, para que el usuario sepa qué resolver.
    """
    if not configuracion.tiene_baja:
        messages.error(request, (
            f'{configuracion.titulo} es un catálogo de referencia: no tiene '
            'baja. Si el dato está mal, corregilo.'))
        return redirect(configuracion.url('lista'))

    instancia = get_object_or_404(configuracion.modelo, pk=pk)
    if request.method != 'POST':
        return redirect(configuracion.url('lista'))

    if instancia.borrado_logico:
        messages.info(
            request,
            f'"{instancia}" ya estaba {configuracion.participio("dad")} de baja.')
        return redirect(configuracion.url('lista'))

    motivos = instancia.motivos_que_impiden_la_baja()
    if motivos:
        messages.error(request, (
            f'No se puede dar de baja "{instancia}" porque '
            + '; '.join(motivos) + '.'))
        return redirect(configuracion.url('lista'))

    anterior = instantanea(instancia)
    with transaction.atomic():
        configuracion.modelo.objects.select_for_update().get(pk=pk)
        instancia.borrado_logico = True
        instancia.save(update_fields=['borrado_logico'])
        registrar_auditoria(
            request, 'BAJA', configuracion.singular.upper(),
            anterior=anterior,
            nuevo=instantanea(configuracion.modelo.objects.get(pk=pk)),
            detalle=f'Baja lógica de "{instancia}".')

    messages.success(request, (
        f'{configuracion.singular.capitalize()} "{instancia}" '
        f'{configuracion.participio("dad")} de baja. La fila queda en la base '
        'y se puede reactivar.'))
    return redirect(configuracion.url('lista'))


@exige('reactivar')
def reactivar(request, configuracion, pk):
    """Deshace la baja lógica (D-10), si los padres están vigentes."""
    if not configuracion.tiene_baja:
        return redirect(configuracion.url('lista'))

    instancia = get_object_or_404(configuracion.modelo, pk=pk)
    if request.method != 'POST':
        return redirect(configuracion.url('lista'))

    if not instancia.borrado_logico:
        messages.info(request, f'"{instancia}" ya estaba vigente.')
        return redirect(configuracion.url('lista'))

    motivos = instancia.motivos_que_impiden_reactivar()
    if motivos:
        messages.error(request, (
            f'No se puede reactivar "{instancia}" porque '
            + '; '.join(motivos) + '.'))
        return redirect(configuracion.url('lista'))

    anterior = instantanea(instancia)
    with transaction.atomic():
        configuracion.modelo.objects.select_for_update().get(pk=pk)
        instancia.borrado_logico = False
        instancia.save(update_fields=['borrado_logico'])
        registrar_auditoria(
            request, 'REACTIVACION', configuracion.singular.upper(),
            anterior=anterior,
            nuevo=instantanea(configuracion.modelo.objects.get(pk=pk)),
            detalle=f'Reactivación de "{instancia}".')

    messages.success(
        request,
        f'{configuracion.singular.capitalize()} "{instancia}" '
        f'{configuracion.participio("reactivad")}.')
    return redirect(configuracion.url('lista'))


def _detalle(configuracion, instancia, formulario):
    """Texto corto para la auditoría, con lo que haya que dejar dicho.

    Hoy sólo se usa para dejar constancia de la confirmación de vender por
    debajo del costo, que es una decisión del usuario y no se deduce de
    los datos guardados.
    """
    partes = [f'{configuracion.titulo}: {instancia}']
    if formulario.cleaned_data.get('confirmo_bajo_costo'):
        partes.append('El usuario confirmó vender por debajo del costo.')
    return ' '.join(partes)
