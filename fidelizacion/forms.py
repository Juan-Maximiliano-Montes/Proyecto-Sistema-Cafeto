"""Formularios del módulo Fidelización: categorías, beneficios con su
receta y clientes."""
import re

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse_lazy

from fidelizacion.models import (
    Beneficio,
    BeneficioProducto,
    CategoriaFidelizacion,
    Cliente,
    ModoEntrega,
    TipoBeneficio,
)
from geografia.models import Localidad
from nucleo.formularios import FormularioMaestro, SelectorConBusqueda, vigentes_mas_el_actual
from nucleo.validaciones import nombre_de_persona, normalizar_telefono
from stock.models import Producto


class CategoriaFidelizacionForm(FormularioMaestro):
    class Meta:
        model = CategoriaFidelizacion
        fields = ['nombre_categoria', 'puntos_minimos', 'importe_por_punto']
        widgets = {
            'nombre_categoria': forms.TextInput(
                attrs={'placeholder': 'Gold', 'autofocus': 'autofocus'}),
        }
        help_texts = {
            'puntos_minimos': 'Puntos desde los que el cliente entra a esta '
                              'categoría. Tiene que ser distinto al de las '
                              'demás.',
            'importe_por_punto': 'Cuántos pesos hay que gastar para sumar un '
                                 'punto. Cuanto más alta la categoría, menos '
                                 'pesos por punto.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['importe_por_punto'].widget.attrs.update({
            'data-minimo': '1', 'data-decimales': '2'})
        if self.instance and self.instance.pk and self.instance.es_de_ingreso:
            self.fields['puntos_minimos'].disabled = True
            self.fields['puntos_minimos'].help_text = (
                'Es la categoría de ingreso (umbral 0): el umbral no se '
                'cambia, porque los clientes nuevos entran por acá.')


# ===========================================================================
#  BENEFICIOS
# ===========================================================================
class BeneficioForm(FormularioMaestro):
    """Alta y edición de beneficios.

    Si el beneficio YA se usó en una venta, sólo se pueden tocar la
    descripción y la fecha de fin: cambiar el tipo, el modo, el porcentaje
    o los puntos reescribiría comprobantes ya emitidos. Para eso se da de
    baja y se crea otro.
    """

    class Meta:
        model = Beneficio
        fields = [
            'id_categoria', 'descripcion_beneficio', 'tipo_beneficio',
            'modo_entrega', 'porcentaje_descuento', 'puntos_requeridos',
            'vigencia_desde', 'vigencia_hasta',
        ]
        widgets = {
            'descripcion_beneficio': forms.TextInput(attrs={
                'placeholder': '10% de descuento en cafetería',
                'autofocus': 'autofocus'}),
            'vigencia_desde': forms.DateInput(
                format='%d/%m/%Y', attrs={'placeholder': 'dd/mm/aaaa'}),
            'vigencia_hasta': forms.DateInput(
                format='%d/%m/%Y', attrs={'placeholder': 'dd/mm/aaaa'}),
        }
        help_texts = {
            'porcentaje_descuento': 'Sólo para los beneficios de DESCUENTO.',
            'puntos_requeridos': 'Sólo para los beneficios CANJEABLES.',
            'vigencia_hasta': 'Opcional: dejalo vacío si no tiene fin.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        vigentes_mas_el_actual(self, 'id_categoria',
                               CategoriaFidelizacion.objects.filter(borrado_logico=False))
        for nombre in ('vigencia_desde', 'vigencia_hasta'):
            self.fields[nombre].input_formats = ['%d/%m/%Y', '%Y-%m-%d']
        # Porcentaje sólo para DESCUENTO; puntos sólo para CANJEABLE.
        self.fields['porcentaje_descuento'].visible_si = f'modo_entrega={ModoEntrega.DESCUENTO}'
        self.fields['puntos_requeridos'].visible_si = f'tipo_beneficio={TipoBeneficio.CANJEABLE}'
        self.fields['porcentaje_descuento'].widget.attrs.update({
            'data-minimo': '0.01', 'data-decimales': '2'})
        self.fields['puntos_requeridos'].widget.attrs.update({
            'data-minimo': '1', 'data-decimales': '0'})

        if self.instance and self.instance.pk and self.instance.ya_se_uso:
            congelados = ['id_categoria', 'tipo_beneficio', 'modo_entrega',
                          'porcentaje_descuento', 'puntos_requeridos',
                          'vigencia_desde']
            for nombre in congelados:
                self.fields[nombre].disabled = True
            self.fields['tipo_beneficio'].help_text = (
                'Este beneficio ya se usó en una venta: sólo se pueden '
                'cambiar la descripción y la fecha de fin. Para cambiar lo '
                'demás, dalo de baja y creá uno nuevo.')

    def clean(self):
        datos = super().clean()
        if not (self.instance and self.instance.pk and self.instance.ya_se_uso):
            # Si el usuario cambió el modo/tipo en pantalla, el campo oculto
            # por data-visible-si puede traer un valor viejo del DOM: se
            # descarta para no devolver un error invisible en un campo oculto.
            if datos.get('modo_entrega') == ModoEntrega.ESPECIE:
                datos['porcentaje_descuento'] = None
                self._errors.pop('porcentaje_descuento', None)
            if datos.get('tipo_beneficio') == TipoBeneficio.AUTOMATICO:
                datos['puntos_requeridos'] = None
                self._errors.pop('puntos_requeridos', None)
        return datos



class BeneficioProductoForm(FormularioMaestro):
    """Un renglón de la receta de un beneficio en especie."""

    class Meta:
        model = BeneficioProducto
        fields = ['id_producto', 'cantidad']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # El queryset completo SÍ se declara: es contra esto que se valida
        # el id que llegue. Lo que no se dibuja son sus 5.000 opciones
        # (ver SelectorConBusqueda).
        campo = self.fields['id_producto']
        vigentes_mas_el_actual(self, 'id_producto', Producto.objects.filter(
            borrado_logico=False).select_related('id_marca'))
        campo.widget = SelectorConBusqueda(
            url_busqueda=reverse_lazy('stock:buscar_productos'),
            attrs={'class': 'form-select js-catalogo'})
        campo.widget.choices = campo.choices
        campo.empty_label = '— Buscá un producto —'
        self.fields['cantidad'].widget.attrs.update({'min': 1, 'max': 99})


# Formset de la receta: se muestra cuando el beneficio es en ESPECIE.
RecetaFormSet = forms.inlineformset_factory(
    Beneficio, BeneficioProducto,
    form=BeneficioProductoForm,
    fk_name='id_beneficio',
    extra=1, can_delete=True,
)


# ===========================================================================
#  CLIENTES (DFD 9 bloque A)
# ===========================================================================
class ClienteForm(FormularioMaestro):
    """Alta y modificación de un cliente del club.

    La categoría y el código QR NO se cargan: el cliente nuevo entra por la
    categoría de ingreso y el QR lo genera el sistema (``Cliente.save``).
    Los puntos tampoco: los mueve el libro de puntos (DD-04).

    Para identificarlo en el mostrador hace falta al menos el teléfono o el
    DNI (RN-7.1.2); el QR llega después, con la tarjeta.
    """

    class Meta:
        model = Cliente
        fields = ['apellido_cliente', 'nombre_cliente', 'dni_cliente',
                  'telefono_cliente', 'email_cliente', 'fecha_nacimiento',
                  'domicilio', 'id_localidad']
        widgets = {
            'apellido_cliente': forms.TextInput(
                attrs={'placeholder': 'López', 'autofocus': 'autofocus'}),
            'nombre_cliente': forms.TextInput(attrs={'placeholder': 'Sofía'}),
            'dni_cliente': forms.TextInput(attrs={
                'placeholder': '40.125.874', 'inputmode': 'numeric'}),
            'telefono_cliente': forms.TextInput(attrs={
                'placeholder': '387 412-3456', 'inputmode': 'tel'}),
            'fecha_nacimiento': forms.DateInput(
                format='%d/%m/%Y', attrs={'placeholder': 'dd/mm/aaaa'}),
        }
        help_texts = {
            'dni_cliente': 'Opcional. Con o sin puntos.',
            'telefono_cliente': 'Con el teléfono o el DNI se lo identifica '
                                'en el mostrador.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fecha_nacimiento'].input_formats = [
            '%d/%m/%Y', '%Y-%m-%d']
        self.fields['id_localidad'].queryset = (
            Localidad.objects.select_related('id_provincia')
            .order_by('nombre_localidad'))
        # El modelo valida el formato ya normalizado (ver los clean_).
        for campo in ('dni_cliente', 'telefono_cliente'):
            self.fields[campo].validators = []
            self.fields[campo].max_length = None
            self.fields[campo].widget.attrs.pop('maxlength', None)

    def clean_apellido_cliente(self):
        return nombre_de_persona(self.cleaned_data.get('apellido_cliente'), 'El apellido')

    def clean_nombre_cliente(self):
        return nombre_de_persona(self.cleaned_data.get('nombre_cliente'), 'El nombre')

    def clean_dni_cliente(self):
        """DD-48: "40.125.874" o "40125874" se guardan como 40125874."""
        texto = (self.cleaned_data.get('dni_cliente') or '').strip()
        if not texto:
            return None
        if re.search(r'[^\d.\s]', texto):
            raise ValidationError('El DNI son 7 u 8 dígitos, sin letras.')
        digitos = re.sub(r'\D', '', texto)
        if not 7 <= len(digitos) <= 8:
            raise ValidationError('El DNI son 7 u 8 dígitos.')
        return digitos

    def clean_telefono_cliente(self):
        texto = (self.cleaned_data.get('telefono_cliente') or '').strip()
        if not texto:
            return None
        if re.search(r'[^\d\s().+-]', texto):
            raise ValidationError('El teléfono no puede llevar letras.')
        digitos = normalizar_telefono(texto)
        if not digitos or not 7 <= len(digitos) <= 15:
            raise ValidationError(
                'El teléfono debe tener entre 7 y 15 dígitos.')
        return digitos

    def clean(self):
        datos = super().clean()
        if not datos.get('dni_cliente') and not datos.get('telefono_cliente') \
                and not self.errors.get('dni_cliente') \
                and not self.errors.get('telefono_cliente'):
            raise ValidationError(
                'Cargá el teléfono o el DNI: es con lo que se identifica al '
                'cliente en el mostrador.')
        return datos
