from django import forms
from django.core.exceptions import ValidationError

from .models import Localidad, Proveedor, Provincia, normalizar_telefono


class ProveedorForm(forms.ModelForm):
    """Formulario de alta y edición. Se usa dentro de los modales."""

    class Meta:
        model = Proveedor
        fields = [
            'nombre_proveedor',
            'cuit_proveedor',
            'telefono_proveedor',
            'email_proveedor',
            'domicilio',
            'id_localidad',
            'condicion_pago',
        ]
        widgets = {
            'nombre_proveedor': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': 'Razón social'}),
            'cuit_proveedor': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': '30-12345678-9', 'pattern': r'\d{2}-\d{8}-\d', 'title': 'Formato: 30-12345678-9'}),
            'telefono_proveedor': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': '387-4123456',
                       'title': 'Se guarda estandarizado como 387-4123456'}),
            'email_proveedor': forms.EmailInput(
                attrs={'class': 'form-control', 'placeholder': 'ventas@proveedor.com'}),
            'domicilio': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': 'Av. Belgrano 1234'}),
            'id_localidad': forms.Select(attrs={'class': 'form-select'}),
            'condicion_pago': forms.Select(
                attrs={'class': 'form-select'}),
        }
        labels = {'id_localidad': 'Localidad'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # El combo muestra "Localidad (Provincia)" y trae la provincia en la
        # misma consulta para no disparar una query por fila.
        self.fields['id_localidad'].queryset = (
            Localidad.objects.select_related('id_provincia'))
        self.fields['id_localidad'].empty_label = '— Sin especificar —'

        # El ModelForm hereda el RegexValidator de formato del modelo, y ese
        # validador corre ANTES de clean_telefono_proveedor: si lo dejamos,
        # "(0387) 412-3456" se rechaza en vez de estandarizarse. Lo sacamos
        # del formulario y lo dejamos actuar recién sobre el valor ya
        # normalizado (Django lo vuelve a correr en _post_clean, cuando
        # valida la instancia del modelo).
        self.fields['telefono_proveedor'].validators = []

    def clean_telefono_proveedor(self):
        """Estandariza el teléfono ANTES del RegexValidator del modelo.

        El operador puede escribir "(0387) 412-3456" o "+54 387 4123456";
        en la base entra siempre como "387-4123456".
        """
        return normalizar_telefono(self.cleaned_data.get('telefono_proveedor'))


# ===========================================================================
#  UBICACIONES — parte de "Mantener datos maestros" (DFD 10)
# ===========================================================================
# Provincias y localidades son un catálogo geográfico: no tienen baja
# lógica. Una provincia no deja de existir, y esconderla sólo dejaría sin
# leer las direcciones históricas que la usan. Por eso su CRUD es alta y
# modificación nada más.
from maestros.forms import FormularioMaestro  # noqa: E402


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
