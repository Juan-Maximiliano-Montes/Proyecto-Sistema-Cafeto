"""Formularios del módulo Compras: proveedores y tipos de egreso."""


from django import forms
from django.core.exceptions import ValidationError

from compras.models import Proveedor, TipoEgreso
from geografia.models import Localidad
from nucleo.formularios import FormularioMaestro
from nucleo.validaciones import normalizar_cuit, normalizar_telefono


class ProveedorForm(forms.ModelForm):
    """Formulario de alta y edición. Se usa dentro de los modales."""

    def clean_tipo_proveedor(self):
        return (self.cleaned_data.get('tipo_proveedor')
                or self.instance.tipo_proveedor
                or Proveedor.TipoProveedor.MERCADERIA)

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
            'tipo_proveedor',
        ]
        widgets = {
            'nombre_proveedor': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': 'Razón social'}),
            # SIN ``pattern``, y es a propósito. El atributo que había
            # (``\d{2}-\d{8}-\d``) lo hacía cumplir el NAVEGADOR antes de
            # enviar, así que escribir los 11 dígitos de corrido
            # —30712345671— se rechazaba en el cliente y nunca llegaba al
            # servidor, que sí lo acepta: ``normalizar_cuit()`` saca los
            # separadores y lo deja como 30-71234567-1.
            #
            # ``data-formato="cuit"`` es el enganche del formateador de
            # ``base.html``: pone los guiones mientras se tipea. Es una
            # ayuda visual; quien valida y normaliza sigue siendo el
            # modelo, que es lo que vale si el JS no corre.
            #
            # Y SIN ``maxlength``, por lo mismo. Eran 13 (los de
            # "30-71234567-1"), pero un CUIT copiado de una factura viene
            # con PUNTOS: "30.712.345.671" son 14 caracteres y el navegador
            # cortaba el último dígito al pegarlo, en silencio. Se guardaba
            # otro CUIT, o fallaba el dígito verificador y el error no decía
            # por qué. El largo real lo controla el modelo después de
            # normalizar (``normalizar_cuit`` deja 11 dígitos).
            'cuit_proveedor': forms.TextInput(
                attrs={'class': 'form-control',
                       'placeholder': '30712345671 o 30-71234567-1',
                       'data-formato': 'cuit',
                       'inputmode': 'numeric',
                       'title': 'Los 11 dígitos, con o sin guiones. '
                                'Se guarda como 30-71234567-1.'}),
            'telefono_proveedor': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': '387-4123456',
                       'inputmode': 'tel',
                       'title': 'Se guarda estandarizado como 387-4123456'}),
            'email_proveedor': forms.EmailInput(
                attrs={'class': 'form-control', 'placeholder': 'ventas@proveedor.com'}),
            'domicilio': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': 'Av. Belgrano 1234'}),
            'id_localidad': forms.Select(attrs={'class': 'form-select'}),
            'condicion_pago': forms.Select(
                attrs={'class': 'form-select'}),
            'tipo_proveedor': forms.Select(
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
        # Con valor por defecto: un POST que no lo trae (un modal viejo,
        # una importación) conserva el tipo que ya tenía o el de siempre.
        self.fields['tipo_proveedor'].required = False

        # El ModelForm hereda el RegexValidator de formato del modelo, y ese
        # validador corre ANTES de clean_telefono_proveedor: si lo dejamos,
        # "(0387) 412-3456" se rechaza en vez de estandarizarse. Lo sacamos
        # del formulario y lo dejamos actuar recién sobre el valor ya
        # normalizado (Django lo vuelve a correr en _post_clean, cuando
        # valida la instancia del modelo).
        self.fields['telefono_proveedor'].validators = []

        # Lo mismo con el CUIT, y además su ``max_length``. El usuario
        # tiene que poder tipear los 11 dígitos de corrido —30712345671—
        # o con los separadores que le salgan: "30.712.345.671" son 14
        # caracteres y el MaxLengthValidator(13) heredado del modelo lo
        # rechazaba con un "máximo 13 caracteres" que no explica nada.
        #
        # Los validadores de campo corren en ``_clean_fields()``, ANTES
        # que ``clean_cuit_proveedor()``, así que se los saca del
        # formulario para que la normalización pueda actuar primero. El
        # largo no queda sin controlar: ``normalizar_cuit()`` devuelve
        # siempre 13 caracteres, y Django vuelve a correr los validadores
        # del modelo en ``_post_clean()`` sobre el valor ya normalizado.
        self.fields['cuit_proveedor'].validators = []
        self.fields['cuit_proveedor'].max_length = None
        # Y el ``maxlength`` del control, que no lo pone sólo el widget de
        # Meta: Django lo agrega SIEMPRE a partir del max_length del modelo
        # (``CharField.widget_attrs``). Mientras estuvo, el navegador
        # cortaba en el caracter 13 lo que se PEGABA, así que
        # "30.712.345.671" entraba como "30.712.345.67": un CUIT distinto,
        # sin ningún aviso, y el dígito verificador fallaba sin decir por
        # qué. Se saca acá, después de que Django lo haya puesto.
        self.fields['cuit_proveedor'].widget.attrs.pop('maxlength', None)

    def clean_nombre_proveedor(self):
        """Razón social repetida sin distinguir mayúsculas, en el campo.

        Los dados de baja se excluyen: para ésos el aviso útil es
        "reactivalo" (D-10), y lo da el ``clean()`` del modelo.
        """
        nombre_limpio = (
            self.cleaned_data.get('nombre_proveedor') or '').strip()
        if not nombre_limpio:
            return nombre_limpio

        existe = Proveedor.objects.filter(
            nombre_proveedor__iexact=nombre_limpio, borrado_logico=False
        ).exclude(pk=self.instance.pk).exists()
        if existe:
            raise ValidationError(
                'Ya existe un proveedor cargado con ese nombre.')
        return nombre_limpio

    def clean_cuit_proveedor(self):
        """Acepta el CUIT como lo tipeen y lo deja en ``NN-NNNNNNNN-N``.

        Saca cualquier carácter que no sea un dígito, exige que queden 11
        y arma la forma canónica. Lo mismo que hace el modelo en su
        ``clean()``; se repite acá para que ``cleaned_data`` ya tenga el
        valor normalizado y el mensaje salga al lado del campo y no en el
        bloque de errores generales del formulario.

        El dígito verificador NO se controla acá: eso lo hace
        ``validar_cuit()`` en el modelo, que es la capa que vale para
        cualquier camino de entrada (shell, importación, /admin).
        """
        return normalizar_cuit(self.cleaned_data.get('cuit_proveedor'))

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


# ===========================================================================
#  TIPOS DE EGRESO
# ===========================================================================




class TipoEgresoForm(FormularioMaestro):
    class Meta:
        model = TipoEgreso
        fields = ['nombre_tipo_egreso', 'descripcion_tipo_egreso']
        widgets = {
            'nombre_tipo_egreso': forms.TextInput(
                attrs={'placeholder': 'Servicios', 'autofocus': 'autofocus'}),
            'descripcion_tipo_egreso': forms.TextInput(
                attrs={'placeholder': 'Energía, agua, gas e internet'}),
        }


# ===========================================================================
#  MEDIOS DE PAGO
# ===========================================================================
