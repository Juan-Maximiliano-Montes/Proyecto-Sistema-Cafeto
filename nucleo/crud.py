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
  V-G8   el duplicado lo rechaza el ``clean()`` de cada modelo, con
         ``exigir_nombre_unico()``, y el aviso sale EN EL CAMPO; si el
         que estaba se había dado de baja, el mensaje manda a reactivarlo
  V-G10  no se da de baja algo con dependientes vigentes, ni se reactiva
         algo cuyos padres están de baja; el mensaje dice cuántos y cuáles
  V-G11  bloqueo optimista por ``fecha_modificacion``
  V-G12  POST-Redirect-GET
  V-G13  permiso validado en el servidor, 404 por id inexistente, y no se
         edita un registro dado de baja sin reactivarlo antes
  4.4 d  auditoría de cada alta, modificación, baja y reactivación
"""
from dataclasses import dataclass
from functools import wraps

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from nucleo.busqueda import filtrar_por_texto
from nucleo.exportar import ColumnaExportable, exportar as armar_exportacion
from nucleo.motivos import redactar
from nucleo.paginacion import paginar
from seguridad.servicios import instantanea, registrar_auditoria
from nucleo.validaciones import id_valido


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
    # False = va en la ficha y en lo exportado, pero no en la tarjeta: una
    # tarjeta de producto con diez renglones ya no se lee de un vistazo.
    en_tarjeta: bool = True
    # Atributo con una aclaración que va debajo del valor, en rojo (lo que
    # el número no cuenta: "5 vencidas, no se venden").
    aclaracion: str = ''
    # La cantidad es de un producto de la fila: entera si se vende por
    # unidad, con tres decimales si se vende por peso o volumen.
    de_producto: bool = False


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
    # Namespace de URLs. Vacío = el de la app dueña del modelo
    # (``stock``, ``ventas``...), que es donde viven sus rutas.
    espacio_urls: str = ''
    filtros: tuple = ()
    tiene_baja: bool = True
    relaciones: tuple = ()
    # Función queryset -> queryset que agrega lo que la lista necesita
    # calcular de una vez para todas las filas (sin una consulta por fila).
    anotar: object = None
    orden: tuple = ()
    # Campos que ACOMPAÑAN al nombre en la regla de unicidad. Una localidad
    # es única dentro de su provincia; un producto, dentro de su marca y su
    # presentación. Vacío = el nombre es único por sí solo.
    ambito_unico: tuple = ()
    # False = el nombre NO identifica a la fila: dos clientes pueden
    # llamarse igual (los identifica su DNI o su teléfono). Lo usa la
    # auditoría de consistencia para no tomarlos por duplicados.
    nombre_es_identidad: bool = True
    # Lista a pedido: al entrar no se consulta la tabla; se muestra el
    # buscador y recién al buscar, filtrar o tocar "Ver todos" se traen las
    # filas. Para los maestros que crecen (productos, clientes): nadie los
    # recorre enteros, se busca uno. Los catálogos cortos se muestran de una.
    a_pedido: bool = False
    # Permiso para bajar el listado a Excel/PDF. Vacío = el mismo de ver.
    # Los clientes lo piden aparte: verlos de a uno en el mostrador no es
    # lo mismo que llevarse el padrón entero con DNI y teléfono.
    permiso_exportar: str = ''

    # -- renglones hijos (la receta de un beneficio en especie) ------------
    # Formset en línea que se edita DENTRO del mismo modal que la fila
    # padre. Vacío = la entidad no tiene hijos y nada cambia.
    formset: type = None
    formset_titulo: str = ''
    formset_vacio: str = 'Todavía no hay renglones cargados.'
    formset_agregar: str = 'Agregar renglón'
    # Nombre del campo del formulario padre que decide si los renglones
    # hacen falta, y el valor con el que hacen falta: el bloque se muestra
    # y se esconde solo mientras se carga. Vacío = siempre visible.
    formset_depende_de: str = ''
    formset_visible_si: str = ''

    # -- acciones de la pantalla además del alta ---------------------------
    # ((etiqueta, ícono, nombre de ruta, argumentos, permiso), ...): la
    # importación desde Excel, el aumento masivo de precios.
    acciones_extra: tuple = ()

    # -- ficha ---------------------------------------------------------------
    # Plantilla que se agrega al pie de la ficha de sólo lectura, con la
    # fila en ``fila``: la tarjeta con el QR y los puntos de un cliente.
    ficha_extra: str = ''

    # -- concordancia de género --------------------------------------------
    @property
    def terminacion(self):
        """'a' o 'o', según el artículo de la entidad.

        Para que los avisos concuerden: "el rubro creado", "la categoría
        creada". Es un detalle, pero un mensaje mal concordado le avisa al
        usuario que el sistema no lo está mirando a él.
        """
        return 'a' if self.articulo == 'la' else 'o'

    @property
    def nuevo(self):
        """'Nuevo proveedor', 'Nueva categoría': el texto del botón de alta."""
        return f'{self.participio("Nuev")} {self.singular}'

    def participio(self, raiz):
        """``participio('cread')`` -> 'creado' o 'creada'."""
        return raiz + self.terminacion

    # -- nombres de permiso ------------------------------------------------
    def permiso(self, accion):
        if accion == 'exportar':
            return self.permiso_exportar or f'ver_{self.clave}'
        if accion == 'ver':
            return f'ver_{self.clave}'
        return f'{accion}_{self.permiso_singular}'

    @property
    def espacio(self):
        return self.espacio_urls or self.modelo._meta.app_label

    def acciones_para(self, usuario):
        """Las acciones extra que ``usuario`` puede usar, con su URL."""
        return [{'etiqueta': etiqueta, 'icono': icono,
                 'url': reverse(ruta, args=argumentos)}
                for etiqueta, icono, ruta, argumentos, permiso in self.acciones_extra
                if usuario is not None and usuario.puede(permiso)]

    def url(self, accion, pk=None):
        nombre = f'{self.espacio}:{self.clave}_{accion}'
        return reverse(nombre, args=[pk] if pk is not None else None)


# Registro global: lo llena el views.py de cada módulo.
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
                return redirect('seguridad:login')

            codigo = configuracion.permiso(accion)
            if not usuario.tiene_permiso(codigo):
                from seguridad.servicios import registrar_acceso_denegado
                registrar_acceso_denegado(request, codigo)
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
    if configuracion.anotar:
        consulta = configuracion.anotar(consulta)
    if configuracion.orden:
        consulta = consulta.order_by(*configuracion.orden)
    return consulta


class _Rechazo(Exception):
    """Una regla del modelo dijo que no, con la transacción ya abierta.

    No es un error técnico: es el modelo contestando "así no". Se levanta
    para que ``transaction.atomic()`` deshaga lo escrito —el padre ya se
    guardó cuando los renglones se validan— y se atrapa arriba para
    mostrar el texto tal cual lo redactó el modelo.
    """


def _guardar_renglones(configuracion, renglones, instancia):
    """Escribe los renglones hijos y revalida el padre YA con su receta.

    Devuelve el texto del error si alguna regla no se cumple, o None si
    salió todo bien. Corre DENTRO de la transacción del padre: la receta
    de un beneficio en especie no existe sin su beneficio, y un beneficio
    en especie sin receta no se puede entregar, así que las dos cosas se
    guardan juntas o no se guarda ninguna.

    ``validar_receta()`` se llama acá y no en ``clean()`` porque necesita
    los renglones ya escritos: en el alta el padre todavía no tiene PK y
    no se le pueden colgar hijos.

    El orden de escritura NO es el de Django. ``formset.save()`` borra
    primero y recién después da de alta, y con eso una receta que se
    reemplaza entera (se quitan los tres productos viejos y se cargan tres
    nuevos) se cae: al borrar el último renglón viejo, el beneficio queda
    un instante sin receta y ``BeneficioProducto.delete()`` lo corta por
    DD-18. Así que acá se escribe al revés —primero las altas y los
    cambios, los borrados al final—, que llega al mismo estado final sin
    pasar por un estado intermedio que la regla prohíbe.
    """
    if renglones is None:
        return None

    renglones.instance = instancia
    renglones.save(commit=False)

    try:
        for renglon in renglones.new_objects:
            renglon.save()
        for renglon, _campos in renglones.changed_objects:
            renglon.save()
        for renglon in renglones.deleted_objects:
            renglon.delete()
    except ValidationError as error:
        return ' '.join(error.messages)

    validar = getattr(instancia, 'validar_receta', None)
    if validar is None:
        return None
    try:
        validar()
    except ValidationError as error:
        return ' '.join(error.messages)
    return None


def armar_formset(configuracion, datos=None, instancia=None):
    """El formset de renglones de esa entidad, o None si no corresponde.

    Un único punto de construcción para las cuatro situaciones —alta en
    blanco, alta con errores, edición por GET y edición con errores—, así
    no hay forma de que una de ellas se olvide de pasar la instancia y los
    renglones aparezcan vacíos.

    Devuelve None en dos casos distintos:

    * la entidad no tiene renglones (``formset`` sin configurar);
    * el POST no trae el formset. Un formset de Django viaja con su
      *management form* (``<prefijo>-TOTAL_FORMS`` y compañía); sin eso,
      ``is_valid()`` no da "no hay renglones", da un error de formulario
      mal armado, y el alta entera se caería sin explicar por qué.

    El segundo caso es normal, no una anomalía: un beneficio de DESCUENTO
    no lleva receta, así que la pantalla no tiene por qué mandarla. "Sin
    formset en el POST" significa "no toques los renglones", y quien
    decide si eso está bien es el modelo: ``validar_receta()`` corre
    igual y rechaza un beneficio en especie que quedó sin receta.
    """
    if configuracion.formset is None:
        return None
    if datos is not None:
        prefijo = configuracion.formset.get_default_prefix()
        if '%s-TOTAL_FORMS' % prefijo not in datos:
            return None
    return configuracion.formset(datos, instance=instancia)


def filas_filtradas(request, configuracion):
    """La consulta del listado con la búsqueda, el estado y los filtros.

    La usan la pantalla (que la pagina) y la exportación (que la baja
    entera): lo que se exporta es exactamente lo que se está mirando.
    """
    busqueda = request.GET.get('busqueda', '').strip()
    estado = request.GET.get('estado', 'vigentes')

    filas = _queryset(configuracion)
    if configuracion.tiene_baja:
        if estado == 'vigentes':
            filas = filas.filter(borrado_logico=False)
        elif estado == 'baja':
            filas = filas.filter(borrado_logico=True)

    filas = filtrar_por_texto(filas, configuracion.campos_busqueda, busqueda)

    aplicados = {}
    for filtro in configuracion.filtros:
        valor = request.GET.get(filtro.clave, '')
        if valor and valor not in {str(clave) for clave, _ in filtro.opciones()}:
            valor = ''   # ?marca=abc o una marca que no se ofrece: se ignora
        aplicados[filtro.clave] = valor
        if valor:
            filas = filas.filter(**{filtro.campo: valor})
    if not filas.query.order_by and not configuracion.modelo._meta.ordering:
        filas = filas.order_by('pk')
    return filas, busqueda, estado, aplicados


# Con cualquiera de estos en la dirección, la persona ya pidió ver algo.
CRITERIOS_DE_LISTA = ('busqueda', 'estado', 'todos', 'pagina', 'por_pagina')


def pidio_algo(request):
    return any(clave in request.GET for clave in CRITERIOS_DE_LISTA) or any(
        request.GET.get(clave) for clave in request.GET)


def _contexto(request, configuracion, extra=None):
    """Arma el contexto del listado: búsqueda, filtros y tarjetas."""
    filas, busqueda, estado, filtros_aplicados = filas_filtradas(
        request, configuracion)
    esperando = configuracion.a_pedido and not pidio_algo(request)
    if esperando:
        filas = filas.none()                 # no llega a la base
    pagina = paginar(request, filas)

    contexto = {
        'cfg': configuracion,
        **pagina,
        'busqueda': busqueda,
        'estado': estado,
        'esperando': esperando,
        'formulario': _preparado(request, configuracion,
                                 configuracion.formulario()),
        'formset': armar_formset(configuracion),
        'filtros': [
            (filtro, filtro.opciones(), filtros_aplicados[filtro.clave])
            for filtro in configuracion.filtros
        ],
        'url_lista': configuracion.url('lista'),
        'url_crear': configuracion.url('crear'),
        'url_exportar': configuracion.url('exportar'),
        'acciones_extra': configuracion.acciones_para(
            getattr(request, 'usuario_actual', None)),
        'permiso_crear': configuracion.permiso('crear'),
        'permiso_exportar': configuracion.permiso('exportar'),
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
    from nucleo.mensajes_bd import mensaje
    concreto = mensaje(error)
    if concreto:
        return concreto
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


def _preparado(request, configuracion, formulario):
    """El formulario con las ayudas de carga marcadas (DD-70)."""
    from nucleo.asistencia import preparar
    return preparar(formulario, configuracion,
                    getattr(request, 'usuario_actual', None))


# ===========================================================================
#  VISTAS
# ===========================================================================
@exige('ver')
def parecidos(request, configuracion):
    """Lo que ya existe igual o parecido a lo que se está tipeando (DD-70).

    La pide el navegador al salir de un campo del alta o la edición. Es GET,
    no escribe nada y responde JSON. ``excluir`` es la fila que se está
    editando: no tiene sentido avisar que se parece a sí misma.
    """
    from django.http import JsonResponse

    from nucleo.asistencia import buscar
    campo = request.GET.get('campo', '')
    valor = (request.GET.get('valor') or '').strip()[:200]
    excluir = request.GET.get('excluir', '')
    resultados = buscar(configuracion, configuracion.formulario(), campo, valor,
                        excluir=id_valido(excluir))
    return JsonResponse({'campo': campo, 'resultados': resultados,
                         'singular': configuracion.singular})


def _alta_rapida(request, configuracion):
    """El alta de esta tabla, desplegada DENTRO de otro formulario (DD-70).

    GET devuelve el fragmento con los campos (prefijo ``rapido``, para que
    no choquen con los del formulario de afuera). POST guarda igual que el
    alta común —mismas validaciones, mismo asiento de auditoría— y
    responde JSON con la fila nueva para que quede elegida en el campo.
    """
    from django.http import JsonResponse

    from nucleo.asistencia import preparar
    usuario = getattr(request, 'usuario_actual', None)
    formulario = preparar(
        configuracion.formulario(request.POST or None, prefix='rapido'),
        configuracion, usuario, rapido=True)
    contexto = {'cfg': configuracion, 'form': formulario, 'formset': None}
    if request.method != 'POST':
        return render(request, 'crud/_alta_rapida.html', contexto)
    if not formulario.is_valid():
        return render(request, 'crud/_alta_rapida.html', contexto, status=400)
    instancia = formulario.save(commit=False)
    try:
        with transaction.atomic():
            instancia.save()
            formulario.save_m2m()
            registrar_auditoria(
                request, 'ALTA', configuracion.singular.upper(),
                anterior=None, nuevo=instantanea(instancia),
                instancia=instancia,
                detalle=_detalle(configuracion, instancia, formulario)
                + ' (alta rápida desde otro formulario)')
    except (IntegrityError, ValidationError) as error:
        mensaje = (_mensaje_de_base(error) if isinstance(error, IntegrityError)
                   else ' '.join(error.messages))
        formulario.add_error(None, mensaje)
        return render(request, 'crud/_alta_rapida.html', contexto, status=400)
    return JsonResponse({'ok': True, 'id': instancia.pk, 'texto': str(instancia),
                         'mensaje': f'{configuracion.singular.capitalize()} '
                                    f'"{instancia}" {configuracion.participio("cread")}.'})


@exige('ver')
def listar(request, configuracion):
    """Listado con búsqueda, filtro por estado y el filtro propio."""
    return render(request, 'crud/lista.html',
                  _contexto(request, configuracion))


@exige('exportar')
def exportar(request, configuracion):
    """El listado filtrado, entero, en Excel o PDF."""
    from nucleo.templatetags.crud_extras import valor_de

    filas, busqueda, estado, aplicados = filas_filtradas(request, configuracion)
    columnas = [ColumnaExportable('N°', 'numero')] + [
        ColumnaExportable(c.etiqueta, c.formato) for c in configuracion.columnas]
    if configuracion.tiene_baja:
        columnas.append(ColumnaExportable('Estado'))

    def renglones():
        for fila in filas.iterator(chunk_size=500):
            datos = [fila.pk] + [valor_de(fila, c.atributo)
                                 for c in configuracion.columnas]
            if configuracion.tiene_baja:
                datos.append('Dado de baja' if fila.borrado_logico else 'Vigente')
            yield datos

    partes = []
    if busqueda:
        partes.append(f'Búsqueda: "{busqueda}"')
    if configuracion.tiene_baja and estado != 'vigentes':
        partes.append({'baja': 'Sólo dados de baja', 'todos': 'Vigentes y dados de baja'}.get(estado, ''))
    for filtro in configuracion.filtros:
        valor = aplicados.get(filtro.clave)
        if valor:
            etiqueta = dict((str(k), v) for k, v in filtro.opciones()).get(valor, valor)
            partes.append(f'{filtro.etiqueta}: {etiqueta}')

    registrar_auditoria(
        request, 'EXPORTACION', configuracion.plural, None, None,
        f'Exportó {configuracion.titulo} a '
        f'{"PDF" if request.GET.get("formato") == "pdf" else "Excel"}'
        + (f' ({"; ".join(p for p in partes if p)})' if partes else ''),
        modulo=configuracion.modelo._meta.app_label.upper())
    return armar_exportacion(request.GET.get('formato'), configuracion.titulo,
                             columnas, renglones(), ' · '.join(p for p in partes if p))


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
    return render(request, 'crud/_ficha.html', {
        'cfg': configuracion,
        'fila': instancia,
        'datos': datos_de_la_ficha(configuracion, instancia),
    })


def _visible_en(condicion, instancia):
    """Evalúa ``visible_si`` ("campo=VALOR,VALOR") sobre una fila guardada,
    con la misma lectura que el navegador: una casilla vale "si" o "no"."""
    if not condicion or '=' not in condicion:
        return True
    campo, valores = condicion.split('=', 1)
    if not hasattr(instancia, campo):          # depende de algo que no se guarda
        return True
    actual = getattr(instancia, campo, None)
    if isinstance(actual, bool):
        actual = 'si' if actual else 'no'
    elif hasattr(actual, 'pk'):
        actual = actual.pk
    return str(actual if actual is not None else '') in valores.split(',')


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
        # Un campo que el formulario sólo muestra en cierto caso (las
        # cuotas, sólo en una tarjeta de crédito) tampoco va en la ficha
        # cuando no es ese caso: "Cuotas: 1" al lado del efectivo se leía
        # como una cuota.
        if not _visible_en(getattr(campo.field, 'visible_si', ''), instancia):
            continue
        mostrar = getattr(instancia, 'get_%s_display' % nombre, None)
        if callable(mostrar):
            valor = mostrar()
        else:
            valor = getattr(instancia, nombre, None)
        legible = getattr(instancia, 'valor_legible', None)
        if callable(legible):
            valor = legible(nombre, valor)
        datos.append((campo.label, valor))
    return datos


@exige('crear')
def crear(request, configuracion):
    """Alta. POST-Redirect-GET y auditoría del registro nuevo."""
    if request.GET.get('rapido') or request.POST.get('rapido'):
        return _alta_rapida(request, configuracion)
    if request.method != 'POST':
        return redirect(configuracion.url('lista'))

    formulario = _preparado(request, configuracion,
                            configuracion.formulario(request.POST))
    valido = formulario.is_valid()

    # Los renglones hijos viajan en el MISMO POST que el padre y se validan
    # acá, antes de escribir nada: si la receta está mal, el beneficio
    # tampoco se crea.
    renglones = armar_formset(configuracion, request.POST)
    if renglones is not None:
        valido = renglones.is_valid() and valido

    # El duplicado NO se intercepta acá. Lo detecta el ``clean()`` del
    # modelo con ``exigir_nombre_unico()``, que levanta el error atado al
    # CAMPO: Django lo pasa al formulario y el aviso sale al lado del
    # control, con lo que el usuario escribió todavía cargado.
    #
    # Antes se miraba acá y se respondía con un redirect más un aviso
    # suelto: el mensaje aparecía arriba, desenganchado del campo, y el
    # formulario volvía en blanco. Había que tipear todo de nuevo.
    if valido:
        instancia = formulario.save(commit=False)

        try:
            with transaction.atomic():
                instancia.save()
                formulario.save_m2m()
                error_de_renglones = _guardar_renglones(
                    configuracion, renglones, instancia)
                if error_de_renglones:
                    raise _Rechazo(error_de_renglones)
                registrar_auditoria(
                    request, 'ALTA', configuracion.singular.upper(),
                    anterior=None, nuevo=instantanea(instancia),
                    instancia=instancia,
                    detalle=_detalle(configuracion, instancia, formulario))
        except _Rechazo as rechazo:
            messages.error(request, str(rechazo))
            contexto = _contexto(request, configuracion, {
                'formulario': formulario, 'formset': renglones,
                'abrir_modal_crear': True})
            return render(request, 'crud/lista.html', contexto)
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
        'formulario': formulario, 'formset': renglones,
        'abrir_modal_crear': True})
    return render(request, 'crud/lista.html', contexto)


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
    # ACTUALES de la fila, incluido el testigo de versión de V-G11. Los
    # renglones hijos van en el mismo fragmento: el modal de edición se
    # arma por fetch y es el único lugar donde la receta ya cargada se
    # puede ver y tocar.
    if request.method == 'GET':
        if request.GET.get('formulario'):
            return render(request, 'crud/_campos.html', {
                'cfg': configuracion,
                'form': _preparado(request, configuracion,
                                   configuracion.formulario(instance=instancia)),
                'formset': armar_formset(
                    configuracion, instancia=instancia)})
        return redirect(configuracion.url('lista'))

    if request.method != 'POST':
        return redirect(configuracion.url('lista'))

    anterior = instantanea(configuracion.modelo.objects.get(pk=pk))
    formulario = _preparado(request, configuracion, configuracion.formulario(
        request.POST, instance=instancia))
    valido = formulario.is_valid()

    renglones = armar_formset(configuracion, request.POST, instancia)
    if renglones is not None:
        valido = renglones.is_valid() and valido

    # El duplicado lo marca el ``clean()`` del modelo, en el campo
    # (ver el comentario en ``crear``).
    if valido:
        modificada = formulario.save(commit=False)

        try:
            with transaction.atomic():
                # V-G11: la fila se toma para escribir hasta el commit.
                configuracion.modelo.objects.select_for_update().get(pk=pk)
                modificada.save()
                formulario.save_m2m()
                error_de_renglones = _guardar_renglones(
                    configuracion, renglones, modificada)
                if error_de_renglones:
                    raise _Rechazo(error_de_renglones)
                registrar_auditoria(
                    request, 'MODIFICACION', configuracion.singular.upper(),
                    anterior=anterior, instancia=modificada,
                    nuevo=instantanea(
                        configuracion.modelo.objects.get(pk=pk)),
                    detalle=_detalle(configuracion, modificada, formulario))
        except _Rechazo as rechazo:
            messages.error(request, str(rechazo))
            contexto = _contexto(request, configuracion, {
                'formulario_editar': formulario,
                'formset_editar': renglones,
                'editar_pk': pk,
                'abrir_modal_editar': True})
            return render(request, 'crud/lista.html', contexto)
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
        'formset_editar': renglones,
        'editar_pk': pk,
        'abrir_modal_editar': True})
    return render(request, 'crud/lista.html', contexto)


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
        messages.error(request, redactar(
            f'No se puede dar de baja "{instancia}"', motivos))
        return redirect(configuracion.url('lista'))

    anterior = instantanea(instancia)
    with transaction.atomic():
        configuracion.modelo.objects.select_for_update().get(pk=pk)
        # Con la fila bloqueada se vuelve a preguntar: entre la primera
        # consulta y el bloqueo otra caja pudo fiarle al cliente (FID-03).
        motivos = instancia.motivos_que_impiden_la_baja()
        if motivos:
            messages.error(request, redactar(
                f'No se puede dar de baja "{instancia}"', motivos))
            return redirect(configuracion.url('lista'))
        instancia.borrado_logico = True
        instancia.save(update_fields=['borrado_logico'])
        registrar_auditoria(
            request, 'BAJA', configuracion.singular.upper(),
            anterior=anterior, instancia=instancia,
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
        messages.error(request, redactar(
            f'No se puede reactivar "{instancia}"', motivos))
        return redirect(configuracion.url('lista'))

    anterior = instantanea(instancia)
    with transaction.atomic():
        configuracion.modelo.objects.select_for_update().get(pk=pk)
        # Misma razón que en la baja: con el bloqueo puesto, se revisa de
        # nuevo (otro beneficio pudo quedar vigente en el medio, FID-04).
        motivos = instancia.motivos_que_impiden_reactivar()
        if motivos:
            messages.error(request, redactar(
                f'No se puede reactivar "{instancia}"', motivos))
            return redirect(configuracion.url('lista'))
        instancia.borrado_logico = False
        instancia.save(update_fields=['borrado_logico'])
        registrar_auditoria(
            request, 'REACTIVACION', configuracion.singular.upper(),
            anterior=anterior, instancia=instancia,
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


# --- Rutas -----------------------------------------------------------------

ACCIONES_COMPLETAS = ('lista', 'exportar', 'crear', 'ver', 'editar',
                      'eliminar', 'reactivar', 'parecidos')
ACCIONES_SIN_BAJA = ('lista', 'exportar', 'crear', 'ver', 'editar', 'parecidos')


def rutas_crud(clave, acciones=ACCIONES_COMPLETAS):
    """Las rutas de una pantalla de CRUD genérico.

        <clave>/                    listar
        <clave>/exportar/           el listado filtrado en Excel o PDF
        <clave>/crear/              alta
        <clave>/<pk>/ver/           ficha de sólo lectura
        <clave>/<pk>/editar/        modificación
        <clave>/<pk>/baja/          baja lógica
        <clave>/<pk>/reactivar/     deshacer la baja

    Los nombres se arman como ``<clave>_<accion>`` para que
    ``ConfiguracionCRUD.url()`` los resuelva sin tabla de equivalencias.
    Un catálogo sin baja (provincias, localidades) pide sólo
    ``ACCIONES_SIN_BAJA``: lo que no tiene ruta no se puede pedir.
    """
    from django.urls import path

    molde = {
        'lista': ('', listar),
        'exportar': ('exportar/', exportar),
        'crear': ('crear/', crear),
        'ver': ('<int:pk>/ver/', ver),
        'editar': ('<int:pk>/editar/', editar),
        'eliminar': ('<int:pk>/baja/', dar_de_baja),
        'reactivar': ('<int:pk>/reactivar/', reactivar),
        'parecidos': ('parecidos/', parecidos),
    }
    return [
        path(f'{clave}/{sufijo}', vista, {'clave': clave},
             name=f'{clave}_{accion}')
        for accion in acciones
        for sufijo, vista in [molde[accion]]
    ]
