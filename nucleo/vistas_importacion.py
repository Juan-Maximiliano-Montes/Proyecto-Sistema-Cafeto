"""Las pantallas de cualquier importación: subir, revisar, confirmar y
bajar la cuarentena.

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


#  Cuántas filas listas se dibujan: con miles, la tabla entera tarda más en
#  pintarse que en leerse. Las de la cuarentena se muestran siempre todas.
MUESTRA_DE_LISTAS = 200
#  Hasta cuántas filas se guarda la foto de cada una en el Registro de
#  Actividad. Más allá, queda el resumen: las altas están en la tabla misma.
FOTOS_EN_AUDITORIA = 300


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
        cuarentena = [r for r in resultados if not r.ok]
        listas = [r for r in resultados if r.ok]
        contexto.update({
            'cuarentena': cuarentena,
            'listas': listas[:MUESTRA_DE_LISTAS],
            'listas_total': len(listas),
            'listas_con_aviso': [r for r in listas[MUESTRA_DE_LISTAS:] if r.avisos],
            'por_aplicar': sum(1 for r in listas if r.accion != 'SIN CAMBIOS'),
            'resumen': importador.resumen(resultados),
            'errores': len(cuarentena),
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
        resultados = importador.confirmar(
            pendiente['filas'], request.usuario_actual,
            solo_validas=request.POST.get('solo_validas') == '1',
            opciones={o.clave: request.POST.get(o.clave) == '1' for o in importador.opciones})
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect('importar', clave=clave)
    altas = sum(1 for r in resultados if r.ok and r.accion == 'ALTA')
    cambios = sum(1 for r in resultados if r.ok and r.accion == 'CAMBIO')
    afuera = [r for r in resultados if not r.ok]
    aplicadas = [r for r in resultados if r.ok and r.foto_nueva]
    con_foto = aplicadas if len(aplicadas) <= FOTOS_EN_AUDITORIA else [
        r for r in aplicadas if r.foto_anterior][:FOTOS_EN_AUDITORIA]
    detalle = f'Importó «{pendiente["archivo"]}»: {altas} alta(s) y {cambios} cambio(s).'
    if afuera:
        detalle += f' {len(afuera)} fila(s) quedaron en cuarentena y no se importaron.'
    if len(con_foto) < len(aplicadas):
        detalle += ' Por el tamaño, el registro guarda el detalle de los cambios y no el de cada alta.'
    for nota in getattr(importador, 'notas', None) or []:
        messages.info(request, nota)
        detalle += ' ' + nota
    registrar_auditoria(
        request, 'IMPORTACION', importador.titulo.upper(), modulo=importador.modulo,
        anterior=[r.foto_anterior for r in con_foto if r.foto_anterior],
        nuevo=[r.foto_nueva for r in con_foto],
        detalle=detalle)
    if afuera:
        # La cuarentena queda a la vista para bajarla, corregirla y resubirla.
        numeros = {r.numero for r in afuera}
        request.session[clave_sesion] = {
            'archivo': pendiente['archivo'],
            'filas': [f for f in pendiente['filas'] if f.get('_fila') in numeros],
            'importadas': altas + cambios}
        messages.success(
            request, f'Listo: {altas} alta(s) y {cambios} cambio(s). '
                     f'Quedaron {len(afuera)} fila(s) en cuarentena: bajalas, corregilas y volvé a subirlas.')
    else:
        request.session.pop(clave_sesion, None)
        messages.success(request, f'Listo: {altas} alta(s) y {cambios} cambio(s).')
    return redirect('importar', clave=clave)


def errores(request, clave):
    """El Excel de la cuarentena: las filas que no entran y por qué."""
    importador = _importador(request, clave)
    if importador is None:
        return _denegado(request)
    pendiente = request.session.get(CLAVE_SESION.format(clave))
    if not pendiente:
        messages.info(request, 'No hay ninguna planilla pendiente.')
        return redirect('importar', clave=clave)
    resultados = [r for r in importador.revisar(pendiente['filas']) if not r.ok]
    if not resultados:
        messages.info(request, 'No quedó ninguna fila con error.')
        return redirect('importar', clave=clave)
    respuesta = HttpResponse(
        importador.planilla_de_errores(resultados),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    respuesta['Content-Disposition'] = f'attachment; filename="cafeto-{clave}-para-corregir.xlsx"'
    return respuesta


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
