"""Vistas del Hito 2: login, cambio de clave obligatorio y CRUD de usuarios.

El "interceptor de seguridad" NO vive acá: vive en seguridad/middleware.py,
que se ejecuta ANTES de cualquier vista. Acá sólo está la lógica de cada
pantalla.

Convención de borrado lógico en TODO el módulo:
    activo = True   -> el registro está vigente y se muestra
    activo = False  -> el registro está dado de baja y se oculta
"""
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from nucleo.busqueda import filtrar_por_texto
from nucleo.exportar import ColumnaExportable, exportar
from nucleo.motivos import redactar
from nucleo.paginacion import paginar
from seguridad.forms import (
    CambioClaveForm,
    CambioPerfilForm,
    LoginForm,
    PerfilForm,
    PerfilTemporalForm,
    TitularidadForm,
    UsuarioAltaForm,
    UsuarioEdicionForm,
)
from seguridad.models import (
    MODULOS_EXCLUSIVOS_DEL_ADMINISTRADOR,
    ModuloPermiso,
    Perfil,
    PerfilPermiso,
    Permiso,
    Usuario,
)
from seguridad.servicios import (
    abrir_sesion,
    cerrar_sesion,
    describir_espera,
    espera_por_bloqueo,
    instantanea,
    intentos_antes_del_bloqueo,
    ip_del_request,
    registrar_auditoria,
    registrar_intento_fallido,
    vincular_sesion,
)

from .decorators import requiere_permiso


# ===========================================================================
#  PARTE 2 — INICIO DE SESIÓN Y SEGURIDAD
# ===========================================================================
def login_view(request):
    """Pantalla de acceso. Valida ``dni_usuario`` **o** ``nombre_usuario``.

    Regla de usabilidad (Hito 2): el operador escribe en un único campo de
    texto y el sistema busca en las DOS columnas indistintamente.

    Seguridad:
      * bloqueo progresivo por reintentos (``espera_por_bloqueo``): mientras
        dura, la clave ni se compara;
      * el mismo mensaje para "no existe" y "clave mala", y el mismo costo
        de cálculo (se hashea igual), para no revelar qué usuarios existen;
      * al entrar se cambia la llave de la sesión de Django (``cycle_key``):
        una llave que alguien haya sembrado antes del login no sirve.
    """
    if request.session.get('usuario_id'):
        # Ya hay sesión: 'home' decide el destino seguro según el perfil.
        return redirect('home')

    form = LoginForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        identificador = form.cleaned_data['identificador'].strip()
        clave = form.cleaned_data['clave']
        ip = ip_del_request(request)

        usuario = Usuario.objects.select_related('id_perfil').filter(
            Q(dni_usuario=identificador) |
            Q(nombre_usuario__iexact=identificador)
        ).first()

        espera = espera_por_bloqueo(identificador, usuario, ip)
        if espera is not None:
            registrar_intento_fallido(
                request, identificador, usuario, 'CUENTA_BLOQUEADA')
            messages.error(request, (
                'Demasiados intentos fallidos. Por seguridad, el ingreso '
                'queda bloqueado: probá de nuevo en '
                f'{describir_espera(espera)}.'))
            return render(request, 'seguridad/login.html', {'form': form})

        if usuario is None:
            # Mismo trabajo que con un usuario real: el tiempo de respuesta
            # no delata si el DNI existe.
            make_password(clave)
            correcta = False
        else:
            correcta = usuario.verificar_clave(clave)

        if not correcta:
            registrar_intento_fallido(
                request, identificador, usuario, 'CLAVE_INCORRECTA')
            quedan = intentos_antes_del_bloqueo(identificador, usuario, ip)
            aviso = 'DNI/usuario o clave incorrectos.'
            if 0 < quedan <= 2:
                plural = quedan > 1
                aviso += (f' Te queda{"n" if plural else ""} {quedan} '
                          f'intento{"s" if plural else ""} antes de un '
                          'bloqueo temporal.')
            messages.error(request, aviso)
        elif not usuario.activo:
            messages.error(
                request,
                'El usuario está dado de baja. Consultá con el administrador.')
            registrar_intento_fallido(
                request, identificador, usuario, 'USUARIO_INACTIVO')
        else:
            # Llave de sesión nueva ANTES de guardar nada (fijación de
            # sesión, OWASP).
            request.session.cycle_key()

            # Sesión de NEGOCIO (tabla SESIONES del DER): la que firma la
            # auditoría y la que el middleware valida en cada pedido. DD-01:
            # abrirla revoca cualquier otra del mismo usuario.
            sesion = abrir_sesion(usuario, request)
            vincular_sesion(request.session, usuario, sesion)
            registrar_auditoria(
                request, 'INICIO', 'SESION', instancia=sesion,
                modulo='SEGURIDAD', detalle=f'Ingresó desde {ip}.')

            if usuario.debe_cambiar_clave:
                messages.info(
                    request,
                    'Tenés que definir una clave nueva antes de continuar.')
                return redirect('seguridad:cambiar_clave')

            messages.success(request, f'¡Hola, {usuario.nombre}!')
            return redirect('home')

    return render(request, 'seguridad/login.html', {'form': form})


def logout_view(request):
    """Cierra la sesión. Sólo por POST: un enlace o una imagen ajena no
    pueden sacar a nadie del sistema."""
    if request.method != 'POST':
        return redirect('home')
    # Primero se cierra la sesión de negocio (queda registrada con su
    # motivo), y recién después se tira la de Django.
    cerrar_sesion(request.session.get('id_sesion_negocio'))
    request.session.flush()
    messages.success(request, 'Cerraste la sesión.')
    return redirect('seguridad:login')


def cambiar_clave_view(request):
    """Pantalla 'Cambio de Clave'.

    Se llega de dos formas:
      1. El middleware te trae a la fuerza porque debe_cambiar_clave == True.
      2. Entrás vos mismo para cambiarla.
    """
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return redirect('seguridad:login')

    form = CambioClaveForm(request.POST or None, usuario=usuario)

    if request.method == 'POST' and form.is_valid():
        anterior = instantanea(usuario)
        usuario.set_clave(form.cleaned_data['clave_nueva'])
        usuario.save()
        registrar_auditoria(
            request, 'CAMBIO DE CLAVE', 'USUARIO', instancia=usuario,
            anterior=anterior, nuevo=instantanea(usuario),
            detalle=f'{usuario.nombre_usuario} cambió su clave.')
        messages.success(request,
                         'Clave actualizada. Ya podés usar el sistema.')
        return redirect('home')

    return render(request, 'seguridad/cambiar_clave.html', {'form': form})


# ===========================================================================
#  PARTE 1 y 3 — CRUD DE USUARIOS
# ===========================================================================
def _usuarios_filtrados(request):
    """La consulta del directorio con la búsqueda y los filtros de la URL.

    La comparten la pantalla (paginada) y la exportación (entera).
    """
    busqueda = request.GET.get('busqueda', '').strip()
    perfil = request.GET.get('perfil', '')
    estado = request.GET.get('estado', 'activos')

    usuarios = Usuario.objects.select_related(
        'id_perfil', 'id_perfil_temporal').order_by('apellido', 'nombre')

    # En la base la baja se marca con 'activo' (False = dado de baja).
    if estado == 'activos':
        usuarios = usuarios.filter(activo=True)
    elif estado == 'inactivos':
        usuarios = usuarios.filter(activo=False)

    # Nombre, apellido, DNI o usuario; sin distinguir acentos ni mayúsculas.
    usuarios = filtrar_por_texto(
        usuarios, ('nombre', 'apellido', 'dni_usuario', 'nombre_usuario'),
        busqueda)

    if perfil.isdigit():
        usuarios = usuarios.filter(id_perfil_id=perfil)
    return usuarios, busqueda, perfil, estado


def _contexto_listado(request):
    """Arma el contexto del directorio: filtros, página y formularios."""
    operador = getattr(request, 'usuario_actual', None)
    usuarios, busqueda, perfil, estado = _usuarios_filtrados(request)
    pagina = paginar(request, usuarios)
    return {
        **pagina,
        'usuarios': pagina['filas'],
        'form': UsuarioAltaForm(operador=operador),
        'form_perfil': CambioPerfilForm(operador=operador),
        'form_temporal': PerfilTemporalForm(),
        'form_titularidad': TitularidadForm(operador=operador),
        'busqueda': busqueda,
        'perfil': perfil,
        'estado': estado,
        'perfiles': Perfil.objects.filter(borrado_logico=False),
    }


@requiere_permiso('ver_usuarios', redirigir_a='mi_perfil')
def exportar_usuarios(request):
    """El directorio filtrado en Excel o PDF. Sin claves ni datos de sesión."""
    usuarios, busqueda, perfil, estado = _usuarios_filtrados(request)
    columnas = [
        ColumnaExportable('DNI'), ColumnaExportable('Usuario'),
        ColumnaExportable('Apellido'), ColumnaExportable('Nombre'),
        ColumnaExportable('Correo'), ColumnaExportable('Perfil'),
        ColumnaExportable('Perfil temporal'), ColumnaExportable('Estado'),
        ColumnaExportable('Última modificación', 'fecha_hora'),
    ]
    filas = ([u.dni_usuario, u.nombre_usuario, u.apellido, u.nombre,
              u.correo, u.id_perfil.nombre,
              u.id_perfil_temporal.nombre if u.tiene_perfil_temporal_vigente else '',
              'Activo' if u.activo else 'Inactivo',
              u.fecha_ultima_modificacion]
             for u in usuarios)
    formato = request.GET.get('formato')
    registrar_auditoria(
        request, 'EXPORTACION', 'USUARIOS',
        detalle=f'Exportó el directorio de usuarios a '
                f'{"PDF" if formato == "pdf" else "Excel"}.',
        modulo='SEGURIDAD')
    return exportar(formato, 'Usuarios', columnas, filas,
                    f'Búsqueda: "{busqueda}"' if busqueda else '')


def _volver_al_listado(request, contexto_extra):
    contexto = _contexto_listado(request)
    contexto.update(contexto_extra)
    return render(request, 'seguridad/lista_usuarios.html', contexto)


@requiere_permiso('ver_usuarios', redirigir_a='mi_perfil')
def lista_usuarios(request):
    """Directorio general de usuarios. Expone datos de TODO el personal, así
    que queda restringido al permiso 'ver_usuarios', que es exclusivo del
    perfil administrador. Un operativo que entre es rebotado a su propia
    ficha ('mi_perfil')."""
    return render(request, 'seguridad/lista_usuarios.html',
                  _contexto_listado(request))


@requiere_permiso('crear_usuario', redirigir_a='seguridad:lista')
def crear_usuario(request):
    """Alta (modal +). Sólo DNI, Apellido, Nombre, Correo y Perfil.

    La clave inicial se autogenera con el DNI (hasheada) y el usuario queda
    obligado a cambiarla en el primer inicio de sesión. Si el perfil
    elegido es el de administrador, se pide la clave de quien lo da.
    """
    if request.method != 'POST':
        return redirect('seguridad:lista')

    form = UsuarioAltaForm(request.POST, operador=request.usuario_actual)
    if form.is_valid():
        usuario = form.save(commit=False)
        # 1) La clave inicial es el DNI, guardada SIEMPRE hasheada.
        usuario.clave = make_password(form.cleaned_data['dni_usuario'])
        usuario.debe_cambiar_clave = True    # 2) la primera vez la cambia
        usuario.activo = True                # 3) nace vigente
        usuario.save()                       # 4) genera 'nombre_usuario'
        registrar_auditoria(
            request, 'ALTA', 'USUARIO', instancia=usuario,
            nuevo=instantanea(usuario),
            detalle=f'Alta de {usuario} con perfil "{usuario.id_perfil}".')
        messages.success(
            request,
            f'Usuario "{usuario.nombre_usuario}" creado. '
            f'Ya puede iniciar sesión con su DNI.')
        return redirect('seguridad:lista')

    messages.error(request, 'No se pudo crear el usuario. Revisá los datos.')
    return _volver_al_listado(request, {'form': form,
                                        'abrir_modal_crear': True})


@requiere_permiso('editar_usuario', redirigir_a='seguridad:lista')
def editar_usuario(request, id_usuario):
    """Modificación (modal lápiz). REGLA ESTRICTA: sólo el campo 'correo'.

    El perfil no se cambia acá: tiene su propia acción, con
    re-autenticación (``cambiar_perfil_usuario``).
    """
    usuario = get_object_or_404(Usuario, pk=id_usuario)

    if request.method != 'POST':
        return redirect('seguridad:lista')

    anterior = instantanea(usuario)
    form = UsuarioEdicionForm(request.POST, instance=usuario)
    if form.is_valid():
        form.save()
        registrar_auditoria(
            request, 'MODIFICACION', 'USUARIO', instancia=usuario,
            anterior=anterior, nuevo=instantanea(usuario),
            detalle=f'Correo de {usuario.nombre_usuario} actualizado.')
        messages.success(
            request, f'Correo de "{usuario.nombre_usuario}" actualizado.')
        return redirect('seguridad:lista')

    messages.error(request, 'No se pudo actualizar el correo.')
    return _volver_al_listado(request, {
        'form_editar': form, 'editar_id': id_usuario,
        'abrir_modal_editar': True})


@requiere_permiso('baja_usuario', redirigir_a='seguridad:lista')
def eliminar_usuario(request, id_usuario):
    """Baja LÓGICA (modal tacho): activo = False + fecha_baja = ahora.

    Candados del modelo: el titular no se da de baja, y el sistema
    conserva al menos un administrador activo. Acá se traducen a un
    cartel rojo y NO se toca la fila.
    """
    usuario = get_object_or_404(Usuario, pk=id_usuario)
    if request.method == 'POST':
        if usuario.pk == request.usuario_actual.pk:
            messages.error(request, 'No podés darte de baja a vos mismo.')
            return redirect('seguridad:lista')
        anterior = instantanea(usuario)
        try:
            usuario.dar_de_baja()
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
        else:
            registrar_auditoria(
                request, 'BAJA', 'USUARIO', instancia=usuario,
                anterior=anterior, nuevo=instantanea(usuario),
                detalle=f'Baja lógica de {usuario}.')
            messages.success(
                request,
                f'El usuario "{usuario.nombre_usuario}" fue dado de baja.')
    return redirect('seguridad:lista')


@requiere_permiso('reactivar_usuario', redirigir_a='seguridad:lista')
def reactivar_usuario(request, id_usuario):
    usuario = get_object_or_404(Usuario, pk=id_usuario)
    if request.method == 'POST':
        anterior = instantanea(usuario)
        usuario.reactivar()
        registrar_auditoria(
            request, 'REACTIVACION', 'USUARIO', instancia=usuario,
            anterior=anterior, nuevo=instantanea(usuario),
            detalle=f'Reactivación de {usuario}.')
        messages.success(
            request, f'El usuario "{usuario.nombre_usuario}" fue reactivado.')
    return redirect('seguridad:lista')


@requiere_permiso('restablecer_clave', redirigir_a='seguridad:lista')
def restablecer_clave_usuario(request, id_usuario):
    """Genera una clave provisoria nueva, la hashea y obliga a cambiarla.

    La clave provisoria SÓLO se ve en este mensaje. No queda guardada en
    texto plano en ningún lado, así que si no se la anota ahora, hay que
    volver a restablecer. La del titular no se restablece desde acá.
    """
    usuario = get_object_or_404(Usuario, pk=id_usuario)
    if request.method == 'POST':
        try:
            clave_generada = usuario.restablecer_clave()
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
            return redirect('seguridad:lista')
        registrar_auditoria(
            request, 'RESTABLECIMIENTO DE CLAVE', 'USUARIO',
            instancia=usuario,
            detalle=f'Clave provisoria generada para {usuario}.')
        messages.success(
            request,
            f'Clave restablecida con éxito. La nueva clave provisoria es: '
            f'{clave_generada}')
    return redirect('seguridad:lista')


@requiere_permiso('cambiar_perfil_usuario', redirigir_a='seguridad:lista')
def cambiar_perfil_usuario(request, id_usuario):
    """Cambio PERMANENTE de perfil, con la clave de quien lo hace.

    Es la acción que da o quita permisos a una persona, así que no basta
    con tener la sesión abierta: se vuelve a pedir la clave.
    """
    usuario = get_object_or_404(Usuario, pk=id_usuario)
    if request.method != 'POST':
        return redirect('seguridad:lista')

    form = CambioPerfilForm(request.POST, operador=request.usuario_actual,
                            usuario=usuario)
    if form.is_valid():
        anterior = instantanea(usuario)
        try:
            perfil_anterior = usuario.cambiar_perfil(
                form.cleaned_data['id_perfil'])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            registrar_auditoria(
                request, 'CAMBIO DE PERFIL', 'USUARIO', instancia=usuario,
                anterior=anterior, nuevo=instantanea(usuario),
                detalle=(f'{usuario}: de "{perfil_anterior}" a '
                         f'"{usuario.id_perfil}".'))
            messages.success(
                request,
                f'{usuario.nombre} ahora tiene el perfil '
                f'"{usuario.id_perfil}".')
            return redirect('seguridad:lista')

    messages.error(request, 'No se cambió el perfil. Revisá los datos.')
    return _volver_al_listado(request, {
        'form_perfil': form, 'perfil_id': id_usuario,
        'abrir_modal_perfil': True})


@requiere_permiso('asignar_perfil_temporal', redirigir_a='seguridad:lista')
def asignar_perfil_temporal(request, id_usuario):
    """Le presta a un usuario otro perfil por un rango de fechas."""
    usuario = get_object_or_404(Usuario, pk=id_usuario)
    if request.method != 'POST':
        return redirect('seguridad:lista')

    form = PerfilTemporalForm(request.POST, usuario=usuario)
    if form.is_valid():
        anterior = instantanea(usuario)
        datos = form.cleaned_data
        try:
            usuario.asignar_perfil_temporal(
                datos['id_perfil_temporal'], datos['temporal_desde'],
                datos['temporal_hasta'], request.usuario_actual,
                datos['temporal_motivo'])
        except ValidationError as error:
            if hasattr(error, 'error_dict'):
                for campo, errores in error.message_dict.items():
                    form.add_error(
                        campo if campo in form.fields else None, errores)
            else:
                form.add_error(None, error)
        else:
            hasta = timezone.localtime(usuario.temporal_hasta)
            registrar_auditoria(
                request, 'PERFIL TEMPORAL', 'USUARIO', instancia=usuario,
                anterior=anterior, nuevo=instantanea(usuario),
                detalle=(f'{usuario} opera como "{usuario.id_perfil_temporal}"'
                         f' hasta el {hasta:%d/%m/%Y %H:%M}. Motivo: '
                         f'{usuario.temporal_motivo}'))
            messages.success(
                request,
                f'{usuario.nombre} opera como "{usuario.id_perfil_temporal}" '
                f'hasta el {hasta:%d/%m/%Y %H:%M}. Después vuelve solo a '
                f'"{usuario.id_perfil}".')
            return redirect('seguridad:lista')

    messages.error(request, 'No se asignó el perfil temporal. Revisá los datos.')
    return _volver_al_listado(request, {
        'form_temporal': form, 'temporal_id': id_usuario,
        'abrir_modal_temporal': True})


@requiere_permiso('asignar_perfil_temporal', redirigir_a='seguridad:lista')
def revocar_perfil_temporal(request, id_usuario):
    usuario = get_object_or_404(Usuario, pk=id_usuario)
    if request.method == 'POST':
        anterior = instantanea(usuario)
        try:
            usuario.revocar_perfil_temporal()
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
        else:
            registrar_auditoria(
                request, 'REVOCACION DE PERFIL TEMPORAL', 'USUARIO',
                instancia=usuario, anterior=anterior,
                nuevo=instantanea(usuario),
                detalle=f'{usuario} vuelve a "{usuario.id_perfil}".')
            messages.success(
                request,
                f'{usuario.nombre} volvió a su perfil "{usuario.id_perfil}".')
    return redirect('seguridad:lista')


def transferir_titularidad(request):
    """El titular le pasa la titularidad a otro administrador.

    No lleva ``@requiere_permiso``: no es un permiso de perfil sino una
    condición de la persona. Sólo el titular puede, y con su clave.
    """
    operador = request.usuario_actual
    if not operador.es_titular:
        messages.error(request,
                       'Sólo el titular del sistema puede transferirla.')
        return redirect('home')
    if request.method != 'POST':
        return redirect('seguridad:lista')

    form = TitularidadForm(request.POST, operador=operador)
    if form.is_valid():
        nuevo = form.cleaned_data['nuevo_titular']
        try:
            operador.transferir_titularidad(nuevo)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            registrar_auditoria(
                request, 'TRANSFERENCIA DE TITULARIDAD', 'USUARIO',
                instancia=nuevo,
                detalle=f'La titularidad pasa de {operador} a {nuevo}.')
            messages.success(
                request,
                f'{nuevo.nombre} {nuevo.apellido} es el nuevo titular del '
                'sistema.')
            return redirect('seguridad:lista')

    messages.error(request, 'No se transfirió la titularidad.')
    return _volver_al_listado(request, {
        'form_titularidad': form, 'abrir_modal_titularidad': True})


# ===========================================================================
#  CRUD DE PERFILES  (Hito 2 — ampliación)
# ===========================================================================
#  Módulo sensible de seguridad: sólo el Administrador (o quien tenga el
#  permiso puntual) entra. Un perfil operativo que pruebe la URL es rebotado
#  a su ficha personal por @requiere_permiso(..., redirigir_a='mi_perfil').

# Qué perfil no se puede borrar nunca ya NO es una lista de nombres: lo
# dice la marca ``Perfil.es_sistema``. Una lista de nombres se rompía sola,
# porque el nombre del perfil se edita desde esta misma pantalla: bastaba
# renombrar "Administrador" para que dejara de estar protegido.

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
    La regla se controla acá, antes de escribir, para poder decirla en
    castellano.

    El perfil de sistema va más allá: conserva el catálogo ENTERO. Es el
    perfil del que depende el acceso a la administración, y dejarlo con
    menos permisos de los que hay deja la pantalla de Perfiles mintiendo
    sobre lo que puede hacer (ver ``Perfil.asegurar_catalogo_completo``).
    """
    seleccion = set(permisos_seleccionados)
    tenia = PerfilPermiso.objects.filter(perfil=perfil).exists()

    if perfil.es_sistema:
        if set(Permiso.objects.all()) - seleccion:
            raise ValidationError(
                f'El perfil "{perfil.nombre}" es el perfil administrador '
                'del sistema: conserva todos los permisos del catálogo y no '
                'se le puede desmarcar ninguno.')
    elif tenia and not seleccion:
        raise ValidationError(
            f'El perfil "{perfil.nombre}" quedaría sin ningún permiso. Un '
            'perfil asignable conserva al menos uno: dejá tildado el que '
            'corresponda, o eliminá el perfil si ya no se usa.')

    PerfilPermiso.objects.filter(perfil=perfil).exclude(
        permiso__in=seleccion).delete()
    for permiso in seleccion:
        PerfilPermiso.objects.get_or_create(perfil=perfil, permiso=permiso)


def _foto_perfil(perfil):
    """La fila del perfil MÁS la lista de sus permisos: lo que de verdad
    cambia cuando se edita un perfil son sus tildes."""
    foto = instantanea(perfil)
    foto['permisos'] = sorted(
        PerfilPermiso.objects.filter(perfil=perfil)
        .values_list('permiso__codigo', flat=True))
    return foto


def _permisos_por_modulo(incluir_exclusivos=True):
    """Devuelve [(etiqueta del módulo, [permisos...]), ...].

    Los modales de alta/edición de perfil muestran el catálogo agrupado por
    ``modulo`` en vez de una lista plana: con 14 permisos ya cuesta encontrar
    el que buscás si están todos mezclados.
    """
    etiquetas = dict(ModuloPermiso.choices)
    agrupados = {}
    permisos = Permiso.objects.all()                 # ordering: modulo, nombre
    if not incluir_exclusivos:
        permisos = permisos.exclude(
            modulo__in=MODULOS_EXCLUSIVOS_DEL_ADMINISTRADOR)
    for permiso in permisos:
        agrupados.setdefault(permiso.modulo, []).append(permiso)
    return [(etiquetas.get(modulo, modulo), permisos)
            for modulo, permisos in agrupados.items()]


def _contexto_perfiles(request):
    busqueda = request.GET.get('busqueda', '').strip()
    # Desde la Fase 4 la baja de un perfil es LÓGICA, así que la pantalla
    # necesita el mismo filtro de estado que el resto del sistema: sin él
    # los perfiles dados de baja quedaban invisibles y sin forma de volver.
    estado = request.GET.get('estado', 'vigentes')

    perfiles = (Perfil.objects.prefetch_related('permisos')
                .select_related('usuario_modificacion')
                .annotate(cantidad_usuarios=Count('usuarios', distinct=True))
                .order_by('-es_sistema', 'nombre'))
    if estado == 'vigentes':
        perfiles = perfiles.filter(borrado_logico=False)
    elif estado == 'baja':
        perfiles = perfiles.filter(borrado_logico=True)
    perfiles = filtrar_por_texto(perfiles, ('nombre', 'descripcion'), busqueda)
    pagina = paginar(request, perfiles)
    return {
        **pagina,
        'perfiles': pagina['filas'],
        'busqueda': busqueda,
        'estado': estado,
        'form': PerfilForm(),
        'permisos_todos': Permiso.objects.all(),
        'permisos_por_modulo': _permisos_por_modulo(incluir_exclusivos=False),
        'permisos_por_modulo_sistema': _permisos_por_modulo(),
    }


@requiere_permiso('ver_perfiles', redirigir_a='mi_perfil')
def lista_perfiles(request):
    """Listado de perfiles + sus permisos asignados."""
    return render(request, 'seguridad/lista_perfiles.html',
                  _contexto_perfiles(request))


@requiere_permiso('crear_perfil', redirigir_a='seguridad:perfiles')
def crear_perfil(request):
    """Alta de perfil (modal +). Incluye el tilde de permisos."""
    if request.method != 'POST':
        return redirect('seguridad:perfiles')

    form = PerfilForm(request.POST)
    if form.is_valid():
        perfil = form.save(commit=False)
        perfil.usuario_modificacion = getattr(request, 'usuario_actual', None)
        perfil.save()
        try:
            _sincronizar_permisos(perfil, form.cleaned_data['permisos'])
        except ValidationError as error:
            messages.error(request, error.messages[0])
            return redirect('seguridad:perfiles')
        registrar_auditoria(
            request, 'ALTA', 'PERFIL', instancia=perfil,
            nuevo=_foto_perfil(perfil),
            detalle=f'Alta del perfil "{perfil.nombre}".')
        messages.success(request, f'Perfil "{perfil.nombre}" creado.')
        return redirect('seguridad:perfiles')

    messages.error(request, 'No se pudo crear el perfil. Revisá los datos.')
    contexto = _contexto_perfiles(request)
    contexto['form'] = form
    contexto['abrir_modal_crear'] = True
    return render(request, 'seguridad/lista_perfiles.html', contexto)


@requiere_permiso('editar_perfil', redirigir_a='seguridad:perfiles')
def editar_perfil(request, id_perfil):
    """Edición de perfil (modal lápiz): nombre, descripción y permisos."""
    perfil = get_object_or_404(Perfil, pk=id_perfil)

    if request.method != 'POST':
        return redirect('seguridad:perfiles')

    anterior = _foto_perfil(perfil)
    form = PerfilForm(request.POST, instance=perfil)
    if form.is_valid():
        perfil = form.save(commit=False)
        perfil.usuario_modificacion = getattr(request, 'usuario_actual', None)
        perfil.save()
        try:
            _sincronizar_permisos(perfil, form.cleaned_data['permisos'])
        except ValidationError as error:
            # ATOMIC_REQUESTS: el guardado del perfil de arriba se deshace
            # junto con el resto del pedido.
            transaction.set_rollback(True)
            messages.error(request, error.messages[0])
            return redirect('seguridad:perfiles')
        registrar_auditoria(
            request, 'MODIFICACION', 'PERFIL', instancia=perfil,
            anterior=anterior, nuevo=_foto_perfil(perfil),
            detalle=f'Modificación del perfil "{perfil.nombre}".')
        messages.success(request, f'Perfil "{perfil.nombre}" actualizado.')
        return redirect('seguridad:perfiles')


    messages.error(request, 'No se pudo actualizar el perfil.')
    contexto = _contexto_perfiles(request)
    contexto['form_editar'] = form
    contexto['editar_id'] = id_perfil
    contexto['abrir_modal_editar'] = True
    return render(request, 'seguridad/lista_perfiles.html', contexto)


@requiere_permiso('eliminar_perfil', redirigir_a='seguridad:perfiles')
def eliminar_perfil(request, id_perfil):
    """Baja LÓGICA de un perfil, con dos candados:

      - el perfil marcado ``es_sistema`` no se toca;
      - un perfil con usuarios activos asignados no se da de baja.

    Los dos los contesta el propio modelo en
    ``Perfil.motivos_que_impiden_la_baja()``, que es donde vive la regla:
    así vale igual si la baja se pide desde esta pantalla, desde un
    comando o desde un test.

    La baja es lógica, no física (RN-8.1.1): PERFILES tiene
    ``borrado_logico`` y el modelo rechaza cualquier DELETE. El motivo es
    que los usuarios históricos siguen apuntando a su perfil: borrarlo
    dejaría registros sin poder explicar con qué permisos operaba esa
    persona.
    """
    perfil = get_object_or_404(Perfil, pk=id_perfil)
    if request.method == 'POST':
        motivos = perfil.motivos_que_impiden_la_baja()
        if motivos:
            messages.error(request, redactar(
                f'No se puede dar de baja "{perfil.nombre}"', motivos))
        else:
            anterior = _foto_perfil(perfil)
            perfil.borrado_logico = True
            perfil.save(update_fields=['borrado_logico'])
            registrar_auditoria(
                request, 'BAJA', 'PERFIL', instancia=perfil,
                anterior=anterior, nuevo=_foto_perfil(perfil),
                detalle=f'Baja lógica del perfil "{perfil.nombre}".')
            messages.success(
                request,
                f'Perfil "{perfil.nombre}" dado de baja. La fila queda en la '
                'base para no romper el historial de los usuarios.')
    return redirect('seguridad:perfiles')


@requiere_permiso('reactivar_perfil', redirigir_a='seguridad:perfiles')
def reactivar_perfil(request, id_perfil):
    """Deshace la baja lógica de un perfil (D-10)."""
    perfil = get_object_or_404(Perfil, pk=id_perfil)
    if request.method == 'POST':
        if not perfil.borrado_logico:
            messages.info(request, f'El perfil "{perfil.nombre}" ya estaba vigente.')
        else:
            anterior = _foto_perfil(perfil)
            perfil.borrado_logico = False
            perfil.save(update_fields=['borrado_logico'])
            registrar_auditoria(
                request, 'REACTIVACION', 'PERFIL', instancia=perfil,
                anterior=anterior, nuevo=_foto_perfil(perfil),
                detalle=f'Reactivación del perfil "{perfil.nombre}".')
            messages.success(request, f'Perfil "{perfil.nombre}" reactivado.')
    return redirect('seguridad:perfiles')


# ===========================================================================
#  CRUD DE PERMISOS  (Hito 2 — ampliación)
# ===========================================================================
def _contexto_permisos(request):
    busqueda = request.GET.get('busqueda', '').strip()
    modulo = request.GET.get('modulo', '')
    permisos = (Permiso.objects.select_related('usuario_modificacion')
                .prefetch_related('perfiles').order_by('modulo', 'codigo'))
    permisos = filtrar_por_texto(
        permisos, ('codigo', 'nombre', 'descripcion'), busqueda)
    if modulo:
        permisos = permisos.filter(modulo=modulo)
    pagina = paginar(request, permisos, por_defecto=24)
    return {
        **pagina,
        'permisos': pagina['filas'],
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
    return render(request, 'seguridad/lista_permisos.html',
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
        if usuario is not None:
            from seguridad.servicios import registrar_acceso_denegado
            registrar_acceso_denegado(request, codigo_permiso)
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
        return redirect('seguridad:login')
    return render(request, 'seguridad/perfil.html', {'usuario': usuario})
