"""Formularios de la app de operaciones.

Por ahora, uno solo: el filtro del Registro de Actividad. Va por GET y no
por POST a propósito —filtrar no cambia nada— y así la búsqueda queda en la
URL: se puede guardar en favoritos, compartir por mail o volver con el botón
Atrás sin perder lo que se había filtrado.
"""
from django import forms
from django.utils import timezone


class FiltroActividadForm(forms.Form):
    """Filtros del Registro de Actividad: quién, cuándo y dónde.

    Ningún campo es obligatorio: sin filtros, la pantalla muestra la
    actividad completa, que es lo que se quiere al entrar.
    """

    usuario = forms.ChoiceField(
        label='Usuario', required=False,
        widget=forms.Select(attrs={'class': 'form-select'}))

    modulo = forms.ChoiceField(
        label='Módulo afectado', required=False,
        widget=forms.Select(attrs={'class': 'form-select'}))

    desde = forms.DateField(
        label='Desde', required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))

    hasta = forms.DateField(
        label='Hasta', required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))

    busqueda = forms.CharField(
        label='Buscar en la operación', required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej.: alta, rubro, producto…'}))

    def __init__(self, *args, usuarios=(), modulos=(), **kwargs):
        """Las opciones se arman en cada pedido.

        Se listan sólo los usuarios y los módulos que REALMENTE aparecen en
        el log. Un combo con cincuenta opciones de las que cuarenta no
        devuelven nada no es un filtro: es una trampa.
        """
        super().__init__(*args, **kwargs)
        self.fields['usuario'].choices = [('', 'Todos')] + list(usuarios)
        self.fields['modulo'].choices = [('', 'Todos')] + list(modulos)

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
