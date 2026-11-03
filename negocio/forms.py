"""Formulario de los parámetros del negocio."""
from django import forms

from geografia.models import Localidad
from negocio.models import ParametrosNegocio
from nucleo.formularios import CampoImporte

REDONDEOS = (('1.00', 'Al peso ($1): sin centavos'),
             ('0.10', 'A los diez centavos ($0,10)'),
             ('0.01', 'Exacto, con centavos'))


class ParametrosForm(forms.ModelForm):
    redondeo_ventas = forms.ChoiceField(label='Redondeo del total', choices=REDONDEOS,
                                        widget=forms.Select(attrs={'class': 'form-select'}))
    tolerancia_arqueo = CampoImporte(label='Diferencia de caja tolerada')
    fondo_caja_sugerido = CampoImporte(label='Fondo de caja sugerido')

    class Meta:
        model = ParametrosNegocio
        fields = ['nombre_fantasia', 'razon_social', 'cuit', 'condicion_iva',
                  'domicilio', 'id_localidad', 'telefono', 'correo',
                  'redondeo_ventas', 'tolerancia_arqueo', 'fondo_caja_sugerido',
                  'dias_para_cambios', 'dias_aviso_vencimiento']
        widgets = {
            'cuit': forms.TextInput(attrs={'data-formato': 'cuit', 'inputmode': 'numeric'}),
            'telefono': forms.TextInput(attrs={'data-formato': 'telefono', 'inputmode': 'tel'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['id_localidad'].queryset = Localidad.objects.select_related(
            'id_provincia').order_by('nombre_localidad')
        if self.instance and self.instance.pk:
            self.initial['redondeo_ventas'] = f'{self.instance.redondeo_ventas:.2f}'
        for nombre, campo in self.fields.items():
            clase = 'form-select' if isinstance(campo.widget, forms.Select) else 'form-control'
            campo.widget.attrs.setdefault('class', clase)

    def clean_redondeo_ventas(self):
        from decimal import Decimal
        return Decimal(self.cleaned_data['redondeo_ventas'])
