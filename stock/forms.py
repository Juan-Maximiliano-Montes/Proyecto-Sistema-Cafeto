"""Formularios del módulo Stock: rubros, marcas y productos."""

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse_lazy
from django.utils.text import format_lazy

from compras.models import Proveedor, TipoProveedor
from nucleo.formularios import FormularioMaestro, SelectorConBusqueda
from stock.models import (
    ComposicionProducto,
    Marca,
    Producto,
    Rubro,
    TipoProducto,
)


# ===========================================================================
#  RUBROS
# ===========================================================================
class RubroForm(FormularioMaestro):
    class Meta:
        model = Rubro
        fields = ['nombre_rubro', 'descripcion_rubro', 'activo',
                  'dias_rebaja_vencimiento', 'porcentaje_rebaja_vencimiento']
        widgets = {
            'nombre_rubro': forms.TextInput(
                attrs={'placeholder': 'Cafetería', 'autofocus': 'autofocus'}),
            'descripcion_rubro': forms.TextInput(
                attrs={'placeholder': 'Qué agrupa este rubro'}),
            'dias_rebaja_vencimiento': forms.NumberInput(
                attrs={'min': 1, 'max': 365, 'placeholder': '5'}),
            'porcentaje_rebaja_vencimiento': forms.NumberInput(
                attrs={'min': 1, 'max': 99, 'step': '0.5',
                       'placeholder': '20'}),
        }
        help_texts = {
            'activo': 'Si lo destildás, el rubro deja de ofrecerse al dar de '
                      'alta productos, pero los que ya tiene siguen vigentes.',
            'dias_rebaja_vencimiento': 'Opcional. Desde cuántos días antes '
                                       'de vencer se rebaja solo el precio.',
            'porcentaje_rebaja_vencimiento': 'Cuánto se rebaja (vale para '
                                             'los productos del rubro que '
                                             'no tengan la suya).',
        }


# ===========================================================================
#  MARCAS
# ===========================================================================
class MarcaForm(FormularioMaestro):
    class Meta:
        model = Marca
        fields = ['nombre_marca', 'descripcion_marca']
        widgets = {
            'nombre_marca': forms.TextInput(
                attrs={'placeholder': 'La Morenita', 'autofocus': 'autofocus'}),
            'descripcion_marca': forms.TextInput(
                attrs={'placeholder': 'Fabricante, origen o lo que convenga recordar'}),
        }
        help_texts = {
            'nombre_marca': 'Sólo la marca, sin el producto: "La Morenita", '
                            'no "Café La Morenita".',
        }

    def clean_nombre_marca(self):
        """Nombre repetido sin distinguir mayúsculas, acá y en pantalla.

        Corre en el FORMULARIO, antes de tocar la base, y el error queda
        atado al campo: Django lo dibuja al lado del control y el
        formulario vuelve con lo que el usuario escribió.

        Los dados de baja se excluyen a propósito: para ésos el aviso
        útil no es "ya está registrado" sino "reactivalo" (D-10), y lo da
        el ``clean()`` del modelo, que sí mira ``borrado_logico``. Si acá
        se los tomara, el usuario quedaría trabado sin saber que la fila
        existe y se puede recuperar.
        """
        nombre_limpio = (self.cleaned_data.get('nombre_marca') or '').strip()
        if not nombre_limpio:
            return nombre_limpio

        existe = Marca.objects.filter(
            nombre_marca__iexact=nombre_limpio, borrado_logico=False
        ).exclude(pk=self.instance.pk).exists()
        if existe:
            raise forms.ValidationError(
                'Este nombre ya se encuentra registrado (ignorando mayúsculas/minúsculas).')
        return nombre_limpio


# ===========================================================================
#  PRODUCTOS
# ===========================================================================
class ProductoForm(FormularioMaestro):
    """Alta y edición de productos.

    Dos campos se comportan distinto según el momento:

      * ``precio_costo`` sólo se carga en el ALTA (es el costo inicial).
        Después lo actualiza la compra (RN-4.2.4), así que en la edición
        va deshabilitado y el servidor ignora lo que venga en el POST.
      * ``stock_actual`` no aparece nunca: lo mueven los movimientos de
        stock (RN-5.1.1).
    """

    confirmo_bajo_costo = forms.BooleanField(
        required=False,
        label='Confirmo vender por debajo del costo',
        help_text='El precio de venta quedó por debajo del de costo. '
                  'Tildalo si es a propósito: queda registrado en la '
                  'auditoría.')

    class Meta:
        model = Producto
        fields = [
            'tipo_producto',
            'id_marca', 'nombre_producto', 'contenido', 'unidad_medida',
            'fraccionable',
            'id_rubro', 'id_proveedor',
            'precio_venta', 'precio_costo', 'stock_minimo', 'codigo_barras',
            'controla_vencimiento',
            'dias_rebaja_vencimiento', 'porcentaje_rebaja_vencimiento',
        ]
        widgets = {
            'nombre_producto': forms.TextInput(attrs={
                'placeholder': 'Café molido',
                'autofocus': 'autofocus'}),
            'codigo_barras': forms.TextInput(attrs={
                'placeholder': '7790001001234', 'inputmode': 'numeric'}),
        }
        help_texts = {
            'nombre_producto': 'Sólo la denominación: "Café molido". La marca '
                               'y el contenido van en sus propios campos.',
            'contenido': 'Cuánto trae el envase, en la unidad de al lado: '
                         '500 + G es "500 g". Poné 1 si se vende por unidad.',
            'unidad_medida': 'En qué se mide el contenido y se cuenta el stock.',
            'codigo_barras': 'Opcional. EAN-8, UPC-A, EAN-13 o GTIN-14, con '
                             'dígito verificador válido.',
            'stock_minimo': 'Cuando el stock baja de acá, el sistema avisa.',
            'tipo_producto': 'Decide qué datos lleva: lo que se compra tiene '
                             'proveedor; lo que se prepara, receta; el '
                             'insumo no se vende.',
            'dias_rebaja_vencimiento': 'Opcional: si la dejás vacía, rige la '
                                       'rebaja del rubro.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # V-G7: los combos ofrecen sólo lo vigente. Un rubro inactivo
        # tampoco se ofrece, aunque no esté dado de baja.
        self.fields['id_rubro'].queryset = Rubro.objects.filter(
            borrado_logico=False, activo=True)
        self.fields['id_proveedor'].queryset = Proveedor.objects.filter(
            borrado_logico=False).exclude(
            tipo_proveedor=TipoProveedor.SERVICIOS)
        # La matriz de tipos la controla el modelo: acá se deja vacío lo
        # que no corresponde y el clean() del modelo dice qué falta.
        self.fields['id_proveedor'].required = False
        self.fields['precio_venta'].required = False
        self.fields['tipo_producto'].required = False
        self.fields['id_marca'].queryset = Marca.objects.filter(
            borrado_logico=False)
        self.fields['id_marca'].empty_label = '— Elegí la marca —'
        if self.instance and self.instance.pk:
            # RN-4.2.4: en la edición el costo es de sólo lectura.
            self.fields['precio_costo'].disabled = True
            self.fields['precio_costo'].help_text = (
                'Lo actualiza la recepción de la compra; no se edita a mano.')
        else:
            self.fields['precio_costo'].required = False
            self.fields['precio_costo'].help_text = (
                'Costo inicial. Si no lo sabés todavía, dejalo en 0.')

    def _exigir_nombre_no_registrado(self, datos):
        """Nombre repetido sin distinguir mayúsculas, con el aviso en el campo.

        Se compara DENTRO de la misma marca y presentación, que es como el
        sistema identifica un producto (DD-55): "Café" de La Morenita y
        "Café" de otra marca son dos artículos distintos, y 500 g y 1 kg
        también. Comparar sólo por el nombre rechazaría altas legítimas.
        """
        nombre_limpio = (datos.get('nombre_producto') or '').strip()
        marca = datos.get('id_marca')
        contenido = datos.get('contenido')
        unidad = datos.get('unidad_medida')
        if not (nombre_limpio and marca and contenido and unidad):
            return

        existe = Producto.objects.filter(
            nombre_producto__iexact=nombre_limpio, borrado_logico=False,
            id_marca=marca, contenido=contenido, unidad_medida=unidad,
        ).exclude(pk=self.instance.pk).exists()
        if existe:
            self.add_error('nombre_producto', forms.ValidationError(
                'Este nombre ya se encuentra registrado '
                '(ignorando mayúsculas/minúsculas) para esa marca y '
                'presentación.'))

    def clean_tipo_producto(self):
        return (self.cleaned_data.get('tipo_producto')
                or self.instance.tipo_producto or TipoProducto.REVENTA)

    def clean(self):
        datos = super().clean()
        self._exigir_nombre_no_registrado(datos)
        venta = datos.get('precio_venta')
        costo = datos.get('precio_costo')
        if costo is None and self.instance and self.instance.pk:
            costo = self.instance.precio_costo

        # No se bloquea vender bajo el costo —a veces es una liquidación—
        # pero tiene que ser una decisión consciente y queda auditada.
        if (datos.get('tipo_producto') != TipoProducto.INSUMO
                and venta is not None and costo is not None and venta < costo):
            if not datos.get('confirmo_bajo_costo'):
                self.add_error('precio_venta', ValidationError(
                    'El precio de venta ($%(venta)s) es menor que el de costo '
                    '($%(costo)s). Si es a propósito, tildá la confirmación.',
                    params={'venta': venta, 'costo': costo}))
        return datos


# ===========================================================================
#  COMPOSICIÓN (receta de un elaborado/producido, contenido de un combo)
# ===========================================================================
class ComposicionForm(forms.ModelForm):
    """Un renglón de la receta o del combo, con el buscador de productos."""

    class Meta:
        model = ComposicionProducto
        fields = ['id_componente', 'cantidad']
        labels = {'id_componente': 'Lleva', 'cantidad': 'Cantidad'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        campo = self.fields['id_componente']
        campo.queryset = Producto.objects.filter(
            borrado_logico=False).exclude(
            tipo_producto=TipoProducto.COMBO).select_related('id_marca')
        campo.widget = SelectorConBusqueda(
            url_busqueda=format_lazy('{}?excluir={}',
                                     reverse_lazy('stock:buscar_productos'),
                                     TipoProducto.COMBO),
            attrs={'class': 'form-select js-catalogo'})
        campo.widget.choices = campo.choices
        campo.empty_label = '— Buscá un producto —'
        self.fields['cantidad'].widget.attrs.update(
            {'class': 'form-control', 'min': '0.001', 'step': '0.001'})


ComposicionFormSet = forms.inlineformset_factory(
    Producto, ComposicionProducto,
    form=ComposicionForm,
    fk_name='id_producto',
    extra=1, can_delete=True,
)
