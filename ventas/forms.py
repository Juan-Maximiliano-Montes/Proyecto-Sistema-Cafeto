"""Formularios del módulo Ventas: medios de pago y cajas registradoras."""

from django import forms

from nucleo.formularios import CampoImporte, FormularioMaestro
from ventas.models import CajaRegistradora, MedioPago

# La clave de OTRA persona (quien autoriza o recibe la caja). Chrome ignora
# autocomplete="off" en un campo de clave: al verlo, llenaba con el usuario
# guardado del equipo el primer campo de texto que encontraba (en el
# mostrador, el del Club Cafeto). "one-time-code" no lo autocompleta; los
# data-* son para los gestores de claves (LastPass, 1Password).
CLAVE_DE_OTRA_PERSONA = {'class': 'form-control', 'autocomplete': 'one-time-code',
                         'data-lpignore': 'true', 'data-1p-ignore': ''}
USUARIO_DE_OTRA_PERSONA = {'class': 'form-control', 'autocomplete': 'off',
                           'data-lpignore': 'true', 'data-1p-ignore': ''}


class MedioPagoForm(FormularioMaestro):
    class Meta:
        model = MedioPago
        fields = ['nombre_medio_pago', 'afecta_caja', 'es_tarjeta_credito',
                  'cantidad_cuotas', 'recargo_porcentaje']
        widgets = {
            'nombre_medio_pago': forms.TextInput(
                attrs={'placeholder': 'Tarjeta de crédito',
                       'autofocus': 'autofocus'}),
        }
        help_texts = {
            'recargo_porcentaje': 'Sólo para un plan en cuotas: el costo de '
                                  'financiación que se le traslada al '
                                  'cliente (se informa en el ticket). En un '
                                  'solo pago va 0: la Ley 25.065 no permite '
                                  'cobrar distinto con tarjeta.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Cuotas y recargo, sólo para la tarjeta de crédito (DD-65).
        self.fields['cantidad_cuotas'].visible_si = 'es_tarjeta_credito=si'
        self.fields['recargo_porcentaje'].visible_si = 'es_tarjeta_credito=si'
        self.fields['cantidad_cuotas'].widget.attrs.update({'min': 1, 'max': 36})
        self.fields['cantidad_cuotas'].required = False   # vacío = 1 pago
        # El efectivo es el dato inicial protegido: ni el nombre ni el
        # recargo se tocan, porque el vuelto y el arqueo dependen de él.
        if self.instance and self.instance.pk and self.instance.es_efectivo:
            self.fields['nombre_medio_pago'].disabled = True
            self.fields['recargo_porcentaje'].disabled = True
            self.fields['afecta_caja'].disabled = True
            self.fields['es_tarjeta_credito'].disabled = True
            self.fields['cantidad_cuotas'].disabled = True
            self.fields['nombre_medio_pago'].help_text = (
                'El medio de pago en efectivo no se renombra ni lleva '
                'recargo: el vuelto y el arqueo de caja se calculan sobre él.')

    def clean_cantidad_cuotas(self):
        return self.cleaned_data.get('cantidad_cuotas') or 1


# ===========================================================================
#  CAJAS REGISTRADORAS
# ===========================================================================
class CajaRegistradoraForm(FormularioMaestro):
    class Meta:
        model = CajaRegistradora
        fields = ['nombre_caja_registradora']
        widgets = {
            'nombre_caja_registradora': forms.TextInput(
                attrs={'placeholder': 'Caja Mostrador',
                       'autofocus': 'autofocus'}),
        }
        help_texts = {
            'nombre_caja_registradora': 'El mueble de cobro, como lo nombra el equipo. '
                             'Entre 3 y 20 caracteres.',
        }


# ===========================================================================
#  CATEGORÍAS DE FIDELIZACIÓN
# ===========================================================================


# ===========================================================================
#  CAJA: apertura, efectivo, arqueo
# ===========================================================================
class ConToken(forms.Form):
    """Token de un solo uso contra el doble envío (nucleo.idempotencia)."""
    token_operacion = forms.CharField(widget=forms.HiddenInput, required=False)


class AutorizacionForm(forms.Form):
    """Usuario y clave de quien autoriza, en el mismo dispositivo."""
    autoriza_usuario = forms.CharField(
        label='Usuario o DNI de quien autoriza', required=False, max_length=35,
        widget=forms.TextInput(attrs=USUARIO_DE_OTRA_PERSONA))
    autoriza_clave = forms.CharField(
        label='Su clave', required=False,
        widget=forms.PasswordInput(attrs=CLAVE_DE_OTRA_PERSONA))


class AperturaForm(ConToken):
    monto_inicial = CampoImporte(
        label='Fondo de cambio con el que arranca',
        help_text='Lo que hay en el cajón al abrir: billetes chicos y monedas.')


class MovimientoEfectivoForm(ConToken):
    TIPOS = (('REFUERZO', 'Refuerzo: entra cambio al cajón'),
             ('RETIRO', 'Retiro: sale efectivo del cajón'))
    tipo = forms.ChoiceField(label='Movimiento', choices=TIPOS,
                             widget=forms.RadioSelect)
    monto = CampoImporte(label='Monto')
    concepto = forms.CharField(
        label='Concepto', max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control',
                                      'placeholder': 'Depósito en el banco'}))

    def clean_monto(self):
        monto = self.cleaned_data['monto']
        if monto <= 0:
            raise forms.ValidationError('El monto tiene que ser mayor que cero.')
        return monto


class ArqueoForm(ConToken):
    """Paso 1 del cierre: lo que hay en el cajón, contado sin mirar el libro."""
    monto_contado = CampoImporte(label='¿Cuánto efectivo contaste?')
    relevo = forms.BooleanField(
        label='Es un cambio de turno: otra persona sigue con este cajón',
        required=False)


class ConfirmacionCierreForm(ConToken, AutorizacionForm):
    """Paso 2: con la diferencia a la vista, se explica y, si hace falta,
    autoriza alguien más."""
    observacion = forms.CharField(
        label='¿A qué se debe la diferencia?', required=False, max_length=120,
        widget=forms.TextInput(attrs={'class': 'form-control'}))
    entrante_usuario = forms.CharField(
        label='Usuario o DNI de quien sigue', required=False, max_length=35,
        widget=forms.TextInput(attrs=USUARIO_DE_OTRA_PERSONA))
    entrante_clave = forms.CharField(
        label='Su clave', required=False,
        widget=forms.PasswordInput(attrs=CLAVE_DE_OTRA_PERSONA))
