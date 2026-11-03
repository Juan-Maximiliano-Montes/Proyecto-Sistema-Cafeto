"""Registro de Actividad: la auditoría, para que la lea una persona.

LOG_AUDITORIA es un libro **append-only**: lo escribe ``registrar_auditoria()``
y el mixin ``LibroInmutable`` rechaza cualquier UPDATE o DELETE (RN-8.1.1).
Una pantalla que ofreciera editar o borrar estaría prometiendo algo que el
modelo va a rechazar, y encima rompería la única garantía que hace que la
auditoría sirva de algo: que nadie pueda cambiar lo que quedó escrito.

Por eso las vistas de consulta son GET: el listado, la ficha y la
exportación. No existe —ni debe existir— una ruta de edición o baja para
este modelo. La única que escribe es ``revisar_alertas`` (POST), y lo que
escribe es un asiento NUEVO —"revisé las alertas"—: no marca ni toca las
alertas, que quedan como estaban.
"""
import re

from django.contrib import messages
from django.db.models import CharField, F, Func, Q, Value
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.views.generic import ListView

from seguridad.decorators import requiere_permiso
from seguridad.models import LogAuditoria

from nucleo.busqueda import filtrar_por_texto
from nucleo.paginacion import TAMANIOS_TABLA, ventana_de_paginas

from .auditoria import comparar

PERMISO = 'ver_auditoria'


def usuarios_con_actividad():
    """(id, etiqueta) de los usuarios que figuran en el log."""
    vistos = {}
    consulta = (LogAuditoria.objects
                .values_list('id_usuario', 'id_usuario__apellido',
                             'id_usuario__nombre')
                .distinct())
    for pk, apellido, nombre in consulta:
        if pk is not None:
            vistos[pk] = '%s, %s' % (apellido, nombre)
    return sorted(vistos.items(), key=lambda par: par[1])


class _Verbo(Func):
    """El verbo del asiento: lo que va antes de " DE " ("ALTA DE RUBRO" -> ALTA)."""
    function = 'split_part'
    output_field = CharField()


def etiqueta_operacion(verbo):
    """ALTA -> Alta, ANULACION -> Anulación, ACCESO DENEGADO -> Acceso denegado.

    El libro guarda las operaciones en mayúsculas y sin tilde; el combo las
    muestra como se leen.
    """
    texto = verbo.capitalize()
    texto = re.sub(r'cion\b', 'ción', texto)
    return re.sub(r'sion\b', 'sión', texto)


def operaciones_con_actividad():
    """(verbo, etiqueta) de los tipos de operación que figuran en el log."""
    verbos = (LogAuditoria.objects
              .annotate(verbo=_Verbo(F('operacion_realizada'), Value(' DE '),
                                     Value(1)))
              .values_list('verbo', flat=True).distinct().order_by('verbo'))
    return [(v, etiqueta_operacion(v)) for v in verbos if v]


def modulos_con_actividad():
    modulos = (LogAuditoria.objects
               .values_list('modulo_afectado', flat=True)
               .distinct().order_by('modulo_afectado'))
    return [(m, m.replace('_', ' ').capitalize()) for m in modulos if m]


@method_decorator(
    requiere_permiso(PERMISO, redirigir_a='mi_perfil'), name='dispatch')
class RegistroActividadListView(ListView):
    """Listado de sólo lectura de LOG_AUDITORIA.

    ``ListView`` y no una vista de edición: la clase que se elige ya dice
    qué se puede hacer con el modelo. No se declaran ``form_class`` ni
    ``success_url`` porque no hay nada que guardar.

    El permiso se controla en ``dispatch``, así que cubre el GET y
    cualquier otro método: aunque alguien arme un POST a mano, rebota antes
    de llegar a la vista. ``redirigir_a='mi_perfil'`` evita el bucle de
    mandarlo al listado del que justamente se lo está echando.
    """

    model = LogAuditoria
    page_kwarg = 'pagina'
    template_name = 'seguridad/registro_actividad.html'
    context_object_name = 'registros'
    # El log crece sin techo: se ve de a 10, 25, 50 o 100 (lo elige quien
    # mira, con ``?por_pagina=``), nunca entero.
    paginate_by = 25

    def get_paginate_by(self, queryset):
        try:
            elegido = int(self.request.GET.get('por_pagina', self.paginate_by))
        except ValueError:
            return self.paginate_by
        return elegido if elegido in TAMANIOS_TABLA else self.paginate_by

    def get_queryset(self):
        # select_related en los dos saltos (sesión -> usuario): sin esto, la
        # columna "Usuario" dispara una consulta por fila.
        registros = (LogAuditoria.objects
                     .select_related('id_usuario', 'id_perfil_efectivo')
                     .order_by('-fecha_hora', '-pk'))

        formulario = self.formulario()
        if not formulario.is_valid():
            # Con un filtro mal cargado se muestra el log sin filtrar y el
            # error al lado del campo. Vaciar la tabla no explicaría nada.
            return registros

        datos = formulario.cleaned_data
        if datos.get('usuario'):
            registros = registros.filter(id_usuario=datos['usuario'])
        if datos.get('modulo'):
            registros = registros.filter(modulo_afectado=datos['modulo'])
        if datos.get('operacion'):
            verbo = datos['operacion']
            registros = registros.filter(
                Q(operacion_realizada=verbo)
                | Q(operacion_realizada__startswith=f'{verbo} DE '))
        if datos.get('resultado'):
            registros = registros.filter(resultado_operacion=datos['resultado'])
        if datos.get('desde'):
            registros = registros.filter(fecha_hora__date__gte=datos['desde'])
        if datos.get('hasta'):
            # __date__lte y no __lte: comparar contra la fecha pelada dejaría
            # afuera todo lo de ese mismo día después de medianoche.
            registros = registros.filter(fecha_hora__date__lte=datos['hasta'])
        if datos.get('busqueda'):
            registros = filtrar_por_texto(
                registros, ('operacion_realizada', 'detalle_operacion'),
                datos['busqueda'])
        return registros

    def formulario(self):
        """El form de filtros, armado una sola vez por pedido."""
        from seguridad.forms_actividad import FiltroActividadForm

        if not hasattr(self, '_formulario'):
            self._formulario = FiltroActividadForm(
                self.request.GET or None,
                usuarios=usuarios_con_actividad(),
                modulos=modulos_con_actividad(),
                operaciones=operaciones_con_actividad())
        return self._formulario

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['formulario'] = self.formulario()
        pagina = contexto['page_obj']
        parametros = self.request.GET.copy()
        parametros.pop('pagina', None)
        parametros.pop('page', None)
        contexto.update({
            'pagina': pagina,
            'total': pagina.paginator.count,
            'por_pagina': pagina.paginator.per_page,
            'tamanios': TAMANIOS_TABLA,
            'parametros': parametros.urlencode(),
            'paginas': ventana_de_paginas(pagina.number, pagina.paginator.num_pages),
        })
        contexto['hay_filtros'] = any(
            self.request.GET.get(c)
            for c in ('usuario', 'modulo', 'operacion', 'resultado', 'desde',
                      'hasta', 'busqueda'))
        from seguridad.servicios import alertas_sin_revisar
        alertas = alertas_sin_revisar(self.request.usuario_actual)
        contexto['alertas'] = list(alertas[:20])
        contexto['alertas_total'] = alertas.count()
        return contexto


@requiere_permiso(PERMISO, redirigir_a='mi_perfil')
def ficha_actividad(request, pk):
    """Fragmento HTML con el detalle de UN registro, para el modal.

    Lo pide el listado por fetch, igual que la ficha de datos maestros. Es
    GET y no toca nada.
    """
    registro = get_object_or_404(
        LogAuditoria.objects.select_related('id_usuario', 'id_perfil_efectivo'),
        pk=pk)
    filas, resumen = comparar(
        registro.registro_anterior, registro.registro_nuevo)
    return render(request, 'seguridad/_ficha_actividad.html', {
        'registro': registro,
        'filas': filas,
        'resumen': resumen,
        'hubo_antes': bool(registro.registro_anterior),
    })


@requiere_permiso(PERMISO, redirigir_a='mi_perfil')
def exportar_actividad(request):
    """El registro filtrado, en Excel o PDF, para entregarlo a quien audite.

    Exportar también queda asentado: quién se llevó una copia del libro es
    parte de lo que el libro tiene que contar.
    """
    from nucleo.exportar import ColumnaExportable, exportar
    from seguridad.servicios import registrar_auditoria

    vista = RegistroActividadListView()
    vista.setup(request)
    registros = vista.get_queryset()
    columnas = [
        ColumnaExportable('Fecha y hora', 'fecha_hora'),
        ColumnaExportable('Usuario'), ColumnaExportable('Perfil'),
        ColumnaExportable('Operación'), ColumnaExportable('Resultado'),
        ColumnaExportable('Módulo'), ColumnaExportable('Detalle'),
    ]
    filas = ([r.fecha_hora,
              f'{r.id_usuario.apellido}, {r.id_usuario.nombre}' if r.id_usuario else 'Sistema',
              r.id_perfil_efectivo.nombre if r.id_perfil_efectivo else '',
              r.operacion_realizada, r.get_resultado_operacion_display(),
              r.modulo_afectado, r.detalle_operacion or '']
             for r in registros.iterator(chunk_size=500))
    formato = request.GET.get('formato')
    respuesta = exportar(formato, 'Registro de Actividad', columnas, filas)
    registrar_auditoria(
        request, 'EXPORTACION', 'AUDITORIA',
        detalle='Exportó el Registro de Actividad a '
                f'{"PDF" if formato == "pdf" else "Excel"}.',
        modulo='SEGURIDAD')
    return respuesta


@require_POST
@requiere_permiso(PERMISO, redirigir_a='mi_perfil')
def revisar_alertas(request):
    """La Administradora da por leídas las alertas de intentos fallidos.

    Queda asentado quién las revisó y cuándo; desde ese momento dejan de
    aparecer como pendientes PARA ELLA (otra persona con el mismo permiso
    las sigue viendo hasta revisarlas).
    """
    from seguridad.servicios import marcar_alertas_revisadas

    if marcar_alertas_revisadas(request) is not None:
        messages.success(request, 'Alertas de seguridad marcadas como revisadas.')
    return redirect('seguridad:actividad')
