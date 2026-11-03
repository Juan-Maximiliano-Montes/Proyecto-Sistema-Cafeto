"""Formularios del módulo Geografía: provincias y localidades."""


from django import forms
from django.core.exceptions import ValidationError

from geografia.models import Localidad, Provincia
from nucleo.formularios import FormularioMaestro


class ProvinciaForm(FormularioMaestro):
    """Alta y edición de una jurisdicción.

    Las 24 vienen precargadas por migración. Dar de alta una más casi
    siempre es un error de tipeo sobre una que ya existe, así que el alta
    pide una confirmación explícita.
    """

    confirmo_alta = forms.BooleanField(
        required=False,
        label='Confirmo que es una jurisdicción nueva',
        help_text='Las 24 jurisdicciones argentinas ya están cargadas. '
                  'Tildá sólo si de verdad falta una.')

    class Meta:
        model = Provincia
        fields = ['nombre_provincia']
        widgets = {
            'nombre_provincia': forms.TextInput(
                attrs={'placeholder': 'Salta', 'autofocus': 'autofocus'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # En la edición no hace falta confirmar nada: ya existe.
        if self.instance and self.instance.pk:
            del self.fields['confirmo_alta']

    def clean(self):
        datos = super().clean()
        if not (self.instance and self.instance.pk):
            if not datos.get('confirmo_alta'):
                self.add_error('confirmo_alta', ValidationError(
                    'Confirmá que la jurisdicción es nueva: el catálogo ya '
                    'trae las 24 del país.'))
        return datos


class LocalidadForm(FormularioMaestro):
    """Alta y edición de una localidad dentro de su provincia."""

    class Meta:
        model = Localidad
        fields = ['id_provincia', 'nombre_localidad', 'codigo_postal']
        widgets = {
            'nombre_localidad': forms.TextInput(
                attrs={'placeholder': 'Campo Quijano'}),
            'codigo_postal': forms.TextInput(
                attrs={'placeholder': '4401 o A4400XAB'}),
        }
        help_texts = {
            'codigo_postal': 'Opcional. 4 dígitos (4401) o CPA de 8 '
                             'caracteres (A4400XAB).',
            'nombre_localidad': 'Puede repetirse entre provincias, pero no '
                                'dentro de la misma.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['id_provincia'].queryset = Provincia.objects.all()
        self.fields['id_provincia'].empty_label = '— Elegí la provincia —'
