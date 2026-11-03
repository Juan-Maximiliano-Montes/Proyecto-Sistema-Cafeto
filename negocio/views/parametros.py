"""Los datos del local y las reglas que decide la dueña.

Una sola fila (``ParametrosNegocio``). Cada cambio se audita con la foto de
antes y la de después: cambiar la tolerancia de caja o el redondeo cambia
cómo se cobra y cómo se cierra, y tiene que quedar quién lo hizo.
"""
from django.contrib import messages
from django.shortcuts import redirect, render

from negocio.forms import ParametrosForm
from negocio.models import ParametrosNegocio
from seguridad.decorators import requiere_permiso
from seguridad.servicios import instantanea, registrar_auditoria


@requiere_permiso('editar_parametros', redirigir_a='home')
def parametros(request):
    actual = ParametrosNegocio.vigentes()
    if request.method == 'POST':
        anterior = instantanea(actual)
        form = ParametrosForm(request.POST, instance=actual)
        if form.is_valid():
            guardado = form.save()
            registrar_auditoria(request, 'MODIFICACION', 'PARAMETROS', instancia=guardado,
                                anterior=anterior, nuevo=instantanea(guardado),
                                detalle='Cambió los datos y las reglas del local.')
            messages.success(request, 'Guardado. Los cambios valen desde la próxima operación.')
            return redirect('negocio:parametros')
        messages.error(request, 'No se pudo guardar: revisá los campos marcados.')
    else:
        form = ParametrosForm(instance=actual)
    return render(request, 'negocio/parametros.html', {'form': form, 'parametros': actual})
