"""Las tres pantallas de cualquier importación: subir, revisar y confirmar.

El importador concreto (productos, proveedores...) se elige por la URL y
trae su propio permiso, que se controla acá en el servidor.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from nucleo.importar import CLAVE_SESION, IMPORTADORES
from seguridad.servicios import registrar_acceso_denegado, registrar_auditoria


def _importador(request, clave):
    clase = IMPORTADORES.get(clave)
    if clase is None:
        raise Http404('No existe esa importación.')
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None or not usuario.puede(clase.permiso):
        if usuario is not None:
            registrar_acceso_denegado(request, clase.permiso)
        return None
    return clase()


def _denegado(request):
    messages.error(request, 'Acceso denegado: tu perfil no puede hacer esta importación.')
    return redirect('home')


def importar(request, clave):
    importador = _importador(request, clave)
    if importador is None:
        return _denegado(request)
    clave_sesion = CLAVE_SESION.format(clave)

    if request.method == 'POST':
        archivo = request.FILES.get('archivo')
        if archivo is None:
            messages.error(request, 'Elegí la planilla que querés importar.')
            return redirect('importar', clave=clave)
        try:
            filas = importador.leer(archivo)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
            return redirect('importar', clave=clave)
        request.session[clave_sesion] = {'archivo': archivo.name[:80], 'filas': filas}
        return redirect('importar', clave=clave)

    pendiente = request.session.get(clave_sesion)
    contexto = {'importador': importador, 'pendiente': pendiente}
    if pendiente:
        resultados = importador.revisar(pendiente['filas'])
        contexto.update({
            'resultados': resultados,
            'errores': sum(1 for r in resultados if not r.ok),
            'altas': sum(1 for r in resultados if r.ok and r.accion == 'ALTA'),
            'cambios': sum(1 for r in resultados if r.ok and r.accion == 'CAMBIO'),
            'iguales': sum(1 for r in resultados if r.ok and r.accion == 'SIN CAMBIOS'),
        })
    return render(request, 'importar/importar.html', contexto)


@require_POST
def confirmar(request, clave):
    importador = _importador(request, clave)
    if importador is None:
        return _denegado(request)
    clave_sesion = CLAVE_SESION.format(clave)
    pendiente = request.session.get(clave_sesion)
    if not pendiente:
        messages.info(request, 'No hay ninguna planilla pendiente: ya se importó o se descartó.')
        return redirect('importar', clave=clave)
    try:
        resultados = importador.confirmar(pendiente['filas'], request.usuario_actual)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect('importar', clave=clave)
    request.session.pop(clave_sesion, None)
    altas = sum(1 for r in resultados if r.accion == 'ALTA')
    cambios = sum(1 for r in resultados if r.accion == 'CAMBIO')
    registrar_auditoria(
        request, 'IMPORTACION', importador.titulo.upper(), modulo=importador.modulo,
        detalle=f'Importó «{pendiente["archivo"]}»: {altas} alta(s) y {cambios} cambio(s).')
    messages.success(request, f'Listo: {altas} alta(s) y {cambios} cambio(s).')
    return redirect('importar', clave=clave)


@require_POST
def descartar(request, clave):
    if _importador(request, clave) is None:
        return _denegado(request)
    request.session.pop(CLAVE_SESION.format(clave), None)
    return redirect('importar', clave=clave)


def modelo(request, clave):
    importador = _importador(request, clave)
    if importador is None:
        return _denegado(request)
    respuesta = HttpResponse(
        importador.planilla_modelo(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    respuesta['Content-Disposition'] = f'attachment; filename="cafeto-modelo-{clave}.xlsx"'
    return respuesta
