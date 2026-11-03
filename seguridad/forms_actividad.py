"""Formularios del Registro de Actividad: el filtro del listado."""
from django import forms
from django.utils import timezone


class FiltroActividadForm(forms.Form):
    """Filtros del Registro de Actividad: quién, qué, cuándo, dónde y cómo
    terminó.

    Ningún campo es obligatorio: sin filtros, la pantalla muestra la
    actividad completa, que es lo que se quiere al entrar.
    """

    usuario = forms.ChoiceField(
        label='Usuario', required=False,
        widget=forms.Select(attrs={'class': 'form-select'}))

    modulo = forms.ChoiceField(
        label='Módulo afectado', required=False,
        widget=forms.Select(attrs={'class': 'form-select'}))

    # El verbo del asiento: Alta, Modificación, Baja, Inicio de sesión,
    # Anulación, Acceso denegado... Sólo los que hay en el libro.
    operacion = forms.ChoiceField(
        label='Tipo de operación', required=False,
        widget=forms.Select(attrs={'class': 'form-select'}))

    # DD-62: lo que se hizo y lo que el sistema rechazó por un control.
    resultado = forms.ChoiceField(
        label='Resultado', required=False,
        widget=forms.Select(attrs={'class': 'form-select'}))

    desde = forms.DateField(
        label='Desde', required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))

    hasta = forms.DateField(
        label='Hasta', required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))

    busqueda = forms.CharField(
        label='Buscar en la operación o el detalle', required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej.: rubro, venta N° 25, producto…'}))

    def __init__(self, *args, usuarios=(), modulos=(), operaciones=(), **kwargs):
        """Las opciones se arman en cada pedido.

        Se listan sólo los usuarios y los módulos que REALMENTE aparecen en
        el log. Un combo con cincuenta opciones de las que cuarenta no
        devuelven nada no es un filtro: es una trampa.
        """
        super().__init__(*args, **kwargs)
        self.fields['usuario'].choices = [('', 'Todos')] + list(usuarios)
        self.fields['modulo'].choices = [('', 'Todos')] + list(modulos)
        self.fields['operacion'].choices = [('', 'Todas')] + list(operaciones)
        from seguridad.models import ResultadoOperacion
        self.fields['resultado'].choices = (
            [('', 'Todos')] + [(v, f'{e}s') for v, e in ResultadoOperacion.choices])

    def clean(self):
        """Las dos fechas tienen que tener sentido entre sí y con hoy."""
        datos = super().clean()
        desde, hasta = datos.get('desde'), datos.get('hasta')
        hoy = timezone.localdate()

        for campo, fecha in (('desde', desde), ('hasta', hasta)):
            if fecha and fecha > hoy:
                self.add_error(
                    campo, 'No se puede buscar actividad en el futuro.')

        if desde and hasta and desde > hasta:
            self.add_error(
                'hasta', 'La fecha "hasta" no puede ser anterior a "desde".')
        return datos
