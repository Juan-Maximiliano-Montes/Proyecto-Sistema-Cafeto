"""Registro de Actividad: la auditoría, para que la lea una persona.

LOG_AUDITORIA es un libro **append-only**: lo escribe ``registrar_auditoria()``
y el mixin ``LibroInmutable`` rechaza cualquier UPDATE o DELETE (RN-8.1.1).
Una pantalla que ofreciera editar o borrar estaría prometiendo algo que el
modelo va a rechazar, y encima rompería la única garantía que hace que la
auditoría sirva de algo: que nadie pueda cambiar lo que quedó escrito.

Por eso acá hay **dos vistas y las dos son GET**: el listado y la ficha. No
existe —ni debe existir— una ruta de alta, edición o baja para este modelo.
"""
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views.generic import ListView

from usuarios.decorators import requiere_permiso

from .auditoria import comparar
from .models import LogAuditoria


PERMISO = 'ver_auditoria'


def usuarios_con_actividad():
    """(id, etiqueta) de los usuarios que figuran en el log."""
    vistos = {}
    consulta = (LogAuditoria.objects
                .select_related('id_sesion__id_usuario')
                .values_list('id_sesion__id_usuario',
                             'id_sesion__id_usuario__apellido',
                             'id_sesion__id_usuario__nombre')
                .distinct())
    for pk, apellido, nombre in consulta:
        if pk is not None:
            vistos[pk] = '%s, %s' % (apellido, nombre)
    return sorted(vistos.items(), key=lambda par: par[1])


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
    template_name = 'operaciones/registro_actividad.html'
    context_object_name = 'registros'
    paginate_by = 25            # el log crece sin techo; no se trae entero

    def get_queryset(self):
        # select_related en los dos saltos (sesión -> usuario): sin esto, la
        # columna "Usuario" dispara una consulta por fila.
        registros = (LogAuditoria.objects
                     .select_related('id_sesion', 'id_sesion__id_usuario')
                     .order_by('-fecha_hora'))

        formulario = self.formulario()
        if not formulario.is_valid():
            # Con un filtro mal cargado se muestra el log sin filtrar y el
            # error al lado del campo. Vaciar la tabla no explicaría nada.
            return registros

        datos = formulario.cleaned_data
        if datos.get('usuario'):
            registros = registros.filter(
                id_sesion__id_usuario=datos['usuario'])
        if datos.get('modulo'):
            registros = registros.filter(modulo_afectado=datos['modulo'])
        if datos.get('desde'):
            registros = registros.filter(fecha_hora__date__gte=datos['desde'])
        if datos.get('hasta'):
            # __date__lte y no __lte: comparar contra la fecha pelada dejaría
            # afuera todo lo de ese mismo día después de medianoche.
            registros = registros.filter(fecha_hora__date__lte=datos['hasta'])
        if datos.get('busqueda'):
            registros = registros.filter(
                operacion_realizada__icontains=datos['busqueda'])
        return registros

    def formulario(self):
        """El form de filtros, armado una sola vez por pedido."""
        from .forms import FiltroActividadForm

        if not hasattr(self, '_formulario'):
            self._formulario = FiltroActividadForm(
                self.request.GET or None,
                usuarios=usuarios_con_actividad(),
                modulos=modulos_con_actividad())
        return self._formulario

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['formulario'] = self.formulario()
        contexto['total'] = self.get_queryset().count()
        contexto['hay_filtros'] = any(
            self.request.GET.get(c)
            for c in ('usuario', 'modulo', 'desde', 'hasta', 'busqueda'))
        return contexto


@requiere_permiso(PERMISO, redirigir_a='mi_perfil')
def ficha_actividad(request, pk):
    """Fragmento HTML con el detalle de UN registro, para el modal.

    Lo pide el listado por fetch, igual que la ficha de datos maestros. Es
    GET y no toca nada.
    """
    registro = get_object_or_404(
        LogAuditoria.objects.select_related('id_sesion__id_usuario'), pk=pk)
    filas, resumen = comparar(
        registro.registro_anterior, registro.registro_nuevo)
    return render(request, 'operaciones/_ficha_actividad.html', {
        'registro': registro,
        'filas': filas,
        'resumen': resumen,
        'hubo_antes': bool(registro.registro_anterior),
    })
