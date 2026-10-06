"""Vistas del Hito 2: login, cambio de clave obligatorio y CRUD de usuarios.

El "interceptor de seguridad" NO vive acá: vive en usuarios/middleware.py,
que se ejecuta ANTES de cualquier vista. Acá sólo está la lógica de cada
pantalla.

Convención de borrado lógico en TODO el módulo:
    activo = True   -> el registro está vigente y se muestra
    activo = False  -> el registro está dado de baja y se oculta
"""
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from operaciones.servicios import abrir_sesion, cerrar_sesion

from .decorators import requiere_permiso
from .forms import (CambioClaveForm, LoginForm, PerfilForm,
                    UsuarioAltaForm, UsuarioEdicionForm)
from .models import ModuloPermiso, Perfil, PerfilPermiso, Permiso, Usuario


# ===========================================================================
#  PARTE 2 — INICIO DE SESIÓN Y SEGURIDAD
# ===========================================================================
def login_view(request):
    """Pantalla de acceso. Valida ``dni_usuario`` **o** ``nombre_usuario``.

    Regla de usabilidad (Hito 2): el operador escribe en un único campo de
    texto y el sistema busca en las DOS columnas indistintamente. No hace
    falta que adivine con cuál de las dos lo cargaron.
    """
    if request.session.get('usuario_id'):
        # Ya hay sesión: NO mandamos directo a 'usuarios:lista' (esa vista
        # está protegida con 'ver_usuarios' y a un Cajero le dispararía
        # el cartel rojo de "Acceso denegado"). Mandamos a 'home', que decide
        # el destino seguro según el perfil.
        return redirect('home')

    form = LoginForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        identificador = form.cleaned_data['identificador'].strip()
        clave = form.cleaned_data['clave']

        # Una sola consulta contra las dos columnas: DNI o nombre de usuario.
        usuario = Usuario.objects.filter(
            Q(dni_usuario=identificador) |
            Q(nombre_usuario__iexact=identificador)
        ).first()

        if usuario is None or not usuario.verificar_clave(clave):
            messages.error(request, 'DNI/usuario o clave incorrectos.')
            registrar_intento_fallido(request, identificador, usuario)
        elif not usuario.activo:
            messages.error(request, 'El usuario está dado de baja. Consultá con el administrador.')
            registrar_intento_fallido(
                request, identificador, usuario, 'USUARIO_INACTIVO')
        else:
            # Login OK: guardamos la PK en la sesión
            request.session['usuario_id'] = usuario.pk
            request.session['usuario_nombre'] = f'{usuario.nombre} {usuario.apellido}'

            # Sesión de NEGOCIO (tabla SESIONES del DER). Es distinta de la
            # sesión de Django: ésta es la que firma la auditoría, porque
            # LOG_AUDITORIA cuelga de ella. DD-01: abrirla revoca cualquier
            # otra que el mismo usuario tuviera abierta.
            sesion = abrir_sesion(usuario, request)
            request.session['id_sesion_negocio'] = sesion.pk

            # INTERCEPTOR: si arrastra una clave provisoria, lo mandamos
            # obligatoriamente a cambiarla antes de dejarlo entrar.
            if usuario.debe_cambiar_clave:
                messages.info(request, 'Tenés que definir una clave nueva antes de continuar.')
                return redirect('usuarios:cambiar_clave')

            messages.success(request, f'¡Hola, {usuario.nombre}!')
            # 'home' redirige limpiamente: al directorio si el perfil tiene
            # 'ver_usuarios'; a '/perfil/' (su ficha personal) si es operativo.
            # Así el Cajero entra sin toparse con "Acceso denegado".
            return redirect('home')

    return render(request, 'usuarios/login.html', {'form': form})


def logout_view(request):
    # Primero se cierra la sesión de negocio (queda registrada con su
    # motivo), y recién después se tira la de Django.
    cerrar_sesion(request.session.get('id_sesion_negocio'))
    request.session.flush()
    messages.success(request, 'Cerraste la sesión.')
    return redirect('usuarios:login')


def registrar_intento_fallido(request, identificador, usuario,
                              motivo='CLAVE_INCORRECTA'):
    """Deja el intento rechazado en el libro INTENTOS_ACCESO.

    Sirve para la alerta a la Administradora ante intentos repetidos. El
    libro es append-only: nunca se corrige, sólo se agrega.
    """
    from operaciones.models import IntentoAcceso
    from operaciones.servicios import ip_del_request

    if usuario is None:
        motivo = 'USUARIO_INEXISTENTE'
    IntentoAcceso.objects.create(
        id_usuario=usuario,
        nombre_usuario_ingresado=identificador[:35],
        ip_origen=ip_del_request(request),
        motivo_rechazo=motivo,
    )


def cambiar_clave_view(request):
    """Pantalla 'Cambio de Clave Obligatorio'.

    Se llega acá de dos formas:
      1. El middleware te trae a la fuerza porque debe_cambiar_clave == True.
      2. Entrás vos mismo para cambiarla por gusto.
    """
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return redirect('usuarios:login')

    form = CambioClaveForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        usuario.set_clave(form.cleaned_data['clave_nueva'])  # hashea + apaga la bandera
        usuario.save()
        messages.success(request, 'Clave actualizada. Ya podés usar el sistema.')
        # Mismo criterio que el login: 'home' lleva a cada perfil a su
        # pantalla segura. Un operativo recién estrenado no debe caer en
        # 'usuarios:lista' y comerse el cartel rojo apenas cambia la clave.
        return redirect('home')

    return render(request, 'usuarios/cambiar_clave.html', {'form': form})


# ===========================================================================
#  PARTE 1 y 3 — CRUD DE USUARIOS
# ===========================================================================
def _contexto_listado(request):
    """Arma el queryset del listado aplicando búsqueda y filtros."""
    busqueda = request.GET.get('busqueda', '').strip()
    perfil = request.GET.get('perfil', '')
    estado = request.GET.get('estado', 'activos')

    usuarios = Usuario.objects.select_related('id_perfil').all()

    # En la base la baja se marca con 'activo' (False = dado de baja).
    if estado == 'activos':
        usuarios = usuarios.filter(activo=True)
    elif estado == 'inactivos':
        usuarios = usuarios.filter(activo=False)

    # Búsqueda por Nombre, Apellido o DNI
    if busqueda:
        usuarios = usuarios.filter(
            Q(nombre__icontains=busqueda) |
            Q(apellido__icontains=busqueda) |
            Q(dni_usuario__icontains=busqueda) |
            Q(nombre_usuario__icontains=busqueda)
        )

    if perfil:
        usuarios = usuarios.filter(id_perfil_id=perfil)

    return {
        'usuarios': usuarios,
        'total': usuarios.count(),
        'form': UsuarioAltaForm(),
        'busqueda': busqueda,
        'perfil': perfil,
        'estado': estado,
        'perfiles': Perfil.objects.all(),
    }


@requiere_permiso('ver_usuarios', redirigir_a='mi_perfil')
def lista_usuarios(request):
    """Directorio general de usuarios. Expone datos sensibles de TODO el
    personal, así que queda restringido al permiso 'ver_usuarios'
    (en la práctica, sólo el Administrador). Un Cajero que entre
    es rebotado a su propia ficha ('mi_perfil')."""
    return render(request, 'usuarios/lista_usuarios.html', _contexto_listado(request))


@requiere_permiso('crear_usuario', redirigir_a='usuarios:lista')
def crear_usuario(request):
    """Alta (modal +). Sólo DNI, Apellido, Nombre, Correo y Perfil.

    La clave inicial se autogenera con el DNI (hasheada) y el usuario queda
    obligado a cambiarla en el primer inicio de sesión.
    """
    if request.method != 'POST':
        return redirect('usuarios:lista')

    form = UsuarioAltaForm(request.POST)
    if form.is_valid():
        usuario = form.save(commit=False)
        # 1) La clave inicial es el DNI, guardada SIEMPRE hasheada.
        usuario.clave = make_password(form.cleaned_data['dni_usuario'])
        usuario.debe_cambiar_clave = True    # 2) la primera vez la cambia
        usuario.activo = True                # 3) nace vigente
        usuario.save()                       # 4) genera 'nombre_usuario'
        messages.success(
            request,
            f'Usuario "{usuario.nombre_usuario}" creado. '
            f'Ya puede iniciar sesión con su DNI.')
        return redirect('usuarios:lista')

    messages.error(request, 'No se pudo crear el usuario. Revisá los datos.')
    contexto = _contexto_listado(request)
    contexto['form'] = form
    contexto['abrir_modal_crear'] = True
    return render(request, 'usuarios/lista_usuarios.html', contexto)


@requiere_permiso('editar_usuario', redirigir_a='usuarios:lista')
def editar_usuario(request, id_usuario):
    """Modificación (modal lápiz). REGLA ESTRICTA: sólo el campo 'correo'."""
    usuario = get_object_or_404(Usuario, pk=id_usuario)

    if request.method != 'POST':
        return redirect('usuarios:lista')

    form = UsuarioEdicionForm(request.POST, instance=usuario)
    if form.is_valid():
        form.save()  # auto_now actualiza fecha_ultima_modificacion sola
        messages.success(request, f'Correo de "{usuario.nombre_usuario}" actualizado.')
        return redirect('usuarios:lista')

    messages.error(request, 'No se pudo actualizar el correo.')
    contexto = _contexto_listado(request)
    contexto['form_editar'] = form
    contexto['editar_id'] = id_usuario
    contexto['abrir_modal_editar'] = True
    return render(request, 'usuarios/lista_usuarios.html', contexto)


@requiere_permiso('baja_usuario', redirigir_a='usuarios:lista')
def eliminar_usuario(request, id_usuario):
    """Baja LÓGICA (modal tacho): activo = False + fecha_baja = ahora.

    Candado "último administrador en pie": el modelo levanta
    ``ValidationError`` si se intenta borrar al único Administrador vigente.
    Acá se traduce a un cartel rojo y NO se toca la fila.
    """
    usuario = get_object_or_404(Usuario, pk=id_usuario)
    if request.method == 'POST':
        try:
            usuario.dar_de_baja()
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
        else:
            messages.success(
                request, f'El usuario "{usuario.nombre_usuario}" fue dado de baja.')
    return redirect('usuarios:lista')


@requiere_permiso('reactivar_usuario', redirigir_a='usuarios:lista')
def reactivar_usuario(request, id_usuario):
    usuario = get_object_or_404(Usuario, pk=id_usuario)
    if request.method == 'POST':
        usuario.reactivar()
        messages.success(
            request, f'El usuario "{usuario.nombre_usuario}" fue reactivado.')
    return redirect('usuarios:lista')


@requiere_permiso('restablecer_clave', redirigir_a='usuarios:lista')
def restablecer_clave_usuario(request, id_usuario):
    """Endpoint separado: genera una clave nueva, la hashea y marca
    DebeCambiarClave = True. Muestra la clave generada UNA vez en pantalla.

    IMPORTANTE: la clave provisoria SÓLO se ve en este mensaje. No queda
    guardada en texto plano en ningún lado (en la base va hasheada), así
    que si el operador no la anota ahora, hay que volver a restablecer.
    """
    usuario = get_object_or_404(Usuario, pk=id_usuario)
    if request.method == 'POST':
        clave_generada = usuario.restablecer_clave()
        messages.success(
            request,
            f'Clave restablecida con éxito. La nueva clave provisoria es: '
            f'{clave_generada}')
    return redirect('usuarios:lista')


# ===========================================================================
#  CRUD DE PERFILES  (Hito 2 — ampliación)
# ===========================================================================
#  Módulo sensible de seguridad: sólo el Administrador (o quien tenga el
#  permiso puntual) entra. Un perfil operativo que pruebe la URL es rebotado
#  a su ficha personal por @requiere_permiso(..., redirigir_a='mi_perfil').

# Perfiles que NO se pueden borrar nunca: romperían el acceso al sistema.
PERFILES_PROTEGIDOS = {'Administrador'}

# Ya no existe una lista de "permisos protegidos": el catálogo entero es de
# sólo lectura, así que ninguna pantalla puede tocarlo. Ver el bloque
# "CATÁLOGO DE PERMISOS" más abajo.


def _sincronizar_permisos(perfil, permisos_seleccionados):
    """Deja la tabla PERFILES_PERMISOS igual a lo tildado en el formulario.

    Como la relación N a N pasa por un modelo intermedio con datos extra
    (fecha_asignacion), no se puede usar ``perfil.permisos.set(...)`` sin
    vueltas: acá borramos las filas que sobran y creamos las que faltan.

    DD-14: un perfil que ya tiene permisos no puede quedarse sin ninguno.
    Un perfil asignable sin permisos es una trampa: se le puede dar a un
    usuario y después ese usuario no puede hacer nada ni entender por qué.
    La base lo defiende con el trigger ``trg_perfil_conserva_permiso``;
    acá se controla antes para poder decirlo en castellano.
    """
    seleccion = set(permisos_seleccionados)
    tenia = PerfilPermiso.objects.filter(perfil=perfil).exists()

    if tenia and not seleccion:
        raise ValidationError(
            f'El perfil "{perfil.nombre}" quedaría sin ningún permiso. Un '
            'perfil asignable conserva al menos uno: dejá tildado el que '
            'corresponda, o eliminá el perfil si ya no se usa.')

    PerfilPermiso.objects.filter(perfil=perfil).exclude(
        permiso__in=seleccion).delete()
    for permiso in seleccion:
        PerfilPermiso.objects.get_or_create(perfil=perfil, permiso=permiso)


def _permisos_por_modulo():
    """Devuelve [(etiqueta del módulo, [permisos...]), ...].

    Los modales de alta/edición de perfil muestran el catálogo agrupado por
    ``modulo`` en vez de una lista plana: con 14 permisos ya cuesta encontrar
    el que buscás si están todos mezclados.
    """
    etiquetas = dict(ModuloPermiso.choices)
    agrupados = {}
    for permiso in Permiso.objects.all():          # ordering: modulo, nombre
        agrupados.setdefault(permiso.modulo, []).append(permiso)
    return [(etiquetas.get(modulo, modulo), permisos)
            for modulo, permisos in agrupados.items()]


def _contexto_perfiles(request):
    busqueda = request.GET.get('busqueda', '').strip()
    # Desde la Fase 4 la baja de un perfil es LÓGICA, así que la pantalla
    # necesita el mismo filtro de estado que el resto del sistema: sin él
    # los perfiles dados de baja quedaban invisibles y sin forma de volver.
    estado = request.GET.get('estado', 'vigentes')

    perfiles = Perfil.objects.prefetch_related('permisos').select_related(
        'usuario_modificacion').all()
    if estado == 'vigentes':
        perfiles = perfiles.filter(borrado_logico=False)
    elif estado == 'baja':
        perfiles = perfiles.filter(borrado_logico=True)
    if busqueda:
        perfiles = perfiles.filter(
            Q(nombre__icontains=busqueda) | Q(descripcion__icontains=busqueda))
    return {
        'perfiles': perfiles,
        'total': perfiles.count(),
        'busqueda': busqueda,
        'estado': estado,
        'form': PerfilForm(),
        'permisos_todos': Permiso.objects.all(),
        'permisos_por_modulo': _permisos_por_modulo(),
    }


@requiere_permiso('ver_perfiles', redirigir_a='mi_perfil')
def lista_perfiles(request):
    """Listado de perfiles + sus permisos asignados."""
    return render(request, 'usuarios/lista_perfiles.html',
                  _contexto_perfiles(request))


@requiere_permiso('crear_perfil', redirigir_a='usuarios:perfiles')
def crear_perfil(request):
    """Alta de perfil (modal +). Incluye el tilde de permisos."""
    if request.method != 'POST':
        return redirect('usuarios:perfiles')

    form = PerfilForm(request.POST)
    if form.is_valid():
        perfil = form.save(commit=False)
        perfil.usuario_modificacion = getattr(request, 'usuario_actual', None)
        perfil.save()
        try:
            _sincronizar_permisos(perfil, form.cleaned_data['permisos'])
        except ValidationError as error:
            messages.error(request, error.messages[0])
            return redirect('usuarios:perfiles')
        messages.success(request, f'Perfil "{perfil.nombre}" creado.')
        return redirect('usuarios:perfiles')

    messages.error(request, 'No se pudo crear el perfil. Revisá los datos.')
    contexto = _contexto_perfiles(request)
    contexto['form'] = form
    contexto['abrir_modal_crear'] = True
    return render(request, 'usuarios/lista_perfiles.html', contexto)


@requiere_permiso('editar_perfil', redirigir_a='usuarios:perfiles')
def editar_perfil(request, id_perfil):
    """Edición de perfil (modal lápiz): nombre, descripción y permisos."""
    perfil = get_object_or_404(Perfil, pk=id_perfil)

    if request.method != 'POST':
        return redirect('usuarios:perfiles')

    form = PerfilForm(request.POST, instance=perfil)
    if form.is_valid():
        perfil = form.save(commit=False)
        perfil.usuario_modificacion = getattr(request, 'usuario_actual', None)
        perfil.save()
        try:
            _sincronizar_permisos(perfil, form.cleaned_data['permisos'])
        except ValidationError as error:
            messages.error(request, error.messages[0])
            return redirect('usuarios:perfiles')
        messages.success(request, f'Perfil "{perfil.nombre}" actualizado.')
        return redirect('usuarios:perfiles')

    messages.error(request, 'No se pudo actualizar el perfil.')
    contexto = _contexto_perfiles(request)
    contexto['form_editar'] = form
    contexto['editar_id'] = id_perfil
    contexto['abrir_modal_editar'] = True
    return render(request, 'usuarios/lista_perfiles.html', contexto)


@requiere_permiso('eliminar_perfil', redirigir_a='usuarios:perfiles')
def eliminar_perfil(request, id_perfil):
    """Baja LÓGICA de un perfil, con dos candados:

      - el perfil Administrador no se toca;
      - un perfil con usuarios asignados no se da de baja.

    Desde la Fase 4 la baja es lógica, no física (RN-8.1.1): PERFILES
    tiene ``borrado_logico`` y la base rechaza cualquier DELETE sobre la
    tabla. El motivo es que los usuarios históricos siguen apuntando a su
    perfil: borrarlo dejaría registros sin poder explicar con qué permisos
    operaba esa persona.
    """
    perfil = get_object_or_404(Perfil, pk=id_perfil)
    if request.method == 'POST':
        if perfil.nombre in PERFILES_PROTEGIDOS:
            messages.error(
                request, f'El perfil "{perfil.nombre}" no se puede eliminar.')
        elif perfil.usuarios.filter(activo=True).exists():
            messages.error(
                request,
                f'No se puede dar de baja "{perfil.nombre}": tiene '
                f'{perfil.usuarios.filter(activo=True).count()} usuario(s) '
                'activo(s) asignado(s).')
        else:
            perfil.borrado_logico = True
            perfil.save(update_fields=['borrado_logico'])
            messages.success(
                request,
                f'Perfil "{perfil.nombre}" dado de baja. La fila queda en la '
                'base para no romper el historial de los usuarios.')
    return redirect('usuarios:perfiles')


@requiere_permiso('reactivar_perfil', redirigir_a='usuarios:perfiles')
def reactivar_perfil(request, id_perfil):
    """Deshace la baja lógica de un perfil (D-10)."""
    perfil = get_object_or_404(Perfil, pk=id_perfil)
    if request.method == 'POST':
        if not perfil.borrado_logico:
            messages.info(request, f'El perfil "{perfil.nombre}" ya estaba vigente.')
        else:
            perfil.borrado_logico = False
            perfil.save(update_fields=['borrado_logico'])
            messages.success(request, f'Perfil "{perfil.nombre}" reactivado.')
    return redirect('usuarios:perfiles')


# ===========================================================================
#  CRUD DE PERMISOS  (Hito 2 — ampliación)
# ===========================================================================
def _contexto_permisos(request):
    busqueda = request.GET.get('busqueda', '').strip()
    modulo = request.GET.get('modulo', '')
    permisos = Permiso.objects.select_related('usuario_modificacion').all()
    if busqueda:
        permisos = permisos.filter(
            Q(codigo__icontains=busqueda) | Q(nombre__icontains=busqueda) |
            Q(descripcion__icontains=busqueda))
    if modulo:
        permisos = permisos.filter(modulo=modulo)
    return {
        'permisos': permisos,
        'total': permisos.count(),
        'busqueda': busqueda,
        'modulo': modulo,
        'modulos': ModuloPermiso.choices,
    }


@requiere_permiso('ver_permisos', redirigir_a='mi_perfil')
def lista_permisos(request):
    """Catálogo de permisos del sistema, en modo SÓLO LECTURA.

    No hay alta, edición ni baja de permisos, y es a propósito: cada
    código existe porque una vista lo exige con ``@requiere_permiso``.
    Un permiso creado desde la pantalla no tendría ninguna vista detrás
    que lo consulte, y renombrar un código dejaría al decorador pidiendo
    algo que ya no está en la tabla — nadie salvo el Administrador (que
    pasa por diseño) podría volver a entrar a esa vista.

    Para dar de alta un permiso nuevo, el camino es el que corresponde:
    agregar el ``@requiere_permiso`` en la vista y cargarlo por migración
    de datos, igual que 0002_datos_iniciales_v2 y 0003_permisos_granulares.

    Lo que el Administrador SÍ administra es a qué perfiles está asignado
    cada permiso, y eso vive en ``editar_perfil`` (pantalla de Perfiles).
    """
    return render(request, 'usuarios/lista_permisos.html',
                  _contexto_permisos(request))


# ===========================================================================
#  PARTE 3 — VALIDACIÓN AJAX
# ===========================================================================
def _sin_permiso_json(request, codigo_permiso):
    """Chequeo de permiso para los endpoints que devuelven JSON.

    No se puede usar ``@requiere_permiso`` acá: ese decorador responde con
    un ``redirect``, y un redirect contestado a un ``$.getJSON`` le llega al
    navegador como HTML del login o del listado, no como el JSON que el
    script espera. Devolvemos un 403 con cuerpo JSON, que es lo que
    corresponde para un endpoint de datos.

    Devuelve la respuesta de error, o ``None`` si el usuario puede pasar.
    """
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None or not usuario.tiene_permiso(codigo_permiso):
        return JsonResponse(
            {'error': 'Acceso denegado: tu perfil no tiene habilitado el '
                      f'permiso "{codigo_permiso}".'},
            status=403)
    return None


def verificar_dni(request):
    """Responde en JSON si un DNI ya está usado. Lo llama el modal de Alta
    mientras el operador tipea.

    Pide 'crear_usuario': saber si un DNI está registrado es información del
    padrón de personal, y el único que necesita consultarla es quien está
    dando de alta a alguien.
    """
    denegado = _sin_permiso_json(request, 'crear_usuario')
    if denegado:
        return denegado

    dni = request.GET.get('dni', '').strip()
    existe = bool(dni and Usuario.objects.filter(dni_usuario=dni).exists())
    return JsonResponse({'existe': existe})


def detalle_usuario(request, id_usuario):
    """Devuelve TODOS los datos de un usuario en JSON, para el modal
    "Ver detalle" (ícono de ojo). Es SÓLO LECTURA: no modifica nada.

    Hace falta porque el modal de edición sólo muestra/permite el correo,
    y a veces necesitás ver la ficha completa sin entrar a editar.

    Pide 'ver_usuarios', el mismo permiso que el listado del que se abre:
    sin esto, cualquier sesión válida podía leer el DNI, el correo y el
    perfil de todo el personal pegándole directo a la URL.
    """
    denegado = _sin_permiso_json(request, 'ver_usuarios')
    if denegado:
        return denegado

    usuario = get_object_or_404(Usuario, pk=id_usuario)

    def fecha(valor):
        # Con USE_TZ=True las fechas se guardan en UTC; localtime las pasa
        # a America/Argentina/Buenos_Aires antes de formatearlas.
        return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M') if valor else '—'

    return JsonResponse({
        'id_usuario': usuario.id_usuario,
        'dni_usuario': usuario.dni_usuario,
        'nombre_completo': f'{usuario.nombre} {usuario.apellido}',
        'correo': usuario.correo,
        'nombre_usuario': usuario.nombre_usuario,
        'perfil': usuario.id_perfil.nombre,
        'borrado_logico': usuario.borrado_logico,
        'debe_cambiar_clave': usuario.debe_cambiar_clave,
        'es_ultimo_administrador': usuario.es_ultimo_administrador,
        'fecha_ultima_modificacion': fecha(usuario.fecha_ultima_modificacion),
        'fecha_baja': fecha(usuario.fecha_baja),
    })


# ===========================================================================
#  PARTE 4 — "MI PERFIL" (para los perfiles operativos: Cajero)
# ===========================================================================
def mi_perfil(request):
    """Ficha personal del usuario logueado, en modo LECTURA.

    Un Cajero no puede entrar al directorio general (`/usuarios/`),
    pero sí necesita ver sus propios datos (usuario autogenerado, perfil,
    fechas...). Esta vista se los muestra sin dejar editar nada.
    """
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return redirect('usuarios:login')
    return render(request, 'usuarios/perfil.html', {'usuario': usuario})


def home(request):
    """Raíz del sitio. Manda a cada uno a donde corresponde:
      - si puede ver el directorio  -> listado de usuarios
      - si no (Cajero)              -> su propia ficha
    Así el logo "CAFETO" y la URL '/' nunca rebotan con 'acceso denegado'.
    """
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is not None and usuario.tiene_permiso('ver_usuarios'):
        return redirect('usuarios:lista')
    return redirect('mi_perfil')
