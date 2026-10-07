"""Formularios de "Mantener datos maestros" (capa 1 de la sección 4.4.V).

Qué resuelve esta capa, además de dibujar los campos:

  * **Lista explícita de campos.** Nunca ``fields = '__all__'`` (V-G6): lo
    que el usuario no edita no viaja en el POST, y si viaja igual —porque
    alguien armó el formulario a mano— el servidor lo ignora.
  * **Combos sólo con vigentes** (V-G7), y el id recibido se vuelve a
    comprobar contra la base.
  * **Formato argentino** (V-G4/V-G5): coma decimal en los importes,
    fecha dd/mm/aaaa.
  * **Bloqueo optimista** (V-G11): el formulario de edición lleva oculta la
    ``fecha_modificacion`` que leyó; si al guardar la de la base es otra,
    rechaza el cambio en vez de pisar lo que hizo el otro usuario.

Las reglas de negocio propiamente dichas viven en ``clean()`` del modelo
(capa 2) y en los CHECK de la base (capa 3). Acá sólo se las invoca y se
traduce el error al campo correcto, para que el mensaje salga al lado de
lo que el usuario escribió.
"""
from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse_lazy

from proveedores.models import Proveedor

from .models import (
    Beneficio,
    BeneficioProducto,
    CategoriaFidelizacion,
    Marca,
    MedioPago,
    ModoEntrega,
    Producto,
    CajaRegistradora,
    Rubro,
    TipoBeneficio,
    TipoEgreso,
    UnidadMedida,
)

# Clases de Bootstrap según el tipo de control.
CLASES = {
    forms.Select: 'form-select',
    forms.SelectMultiple: 'form-select',
    forms.CheckboxInput: 'form-check-input',
}
CLASE_POR_DEFECTO = 'form-control'


class FormularioMaestro(forms.ModelForm):
    """Base de todos los formularios de datos maestros.

    Aporta tres cosas iguales para todos: las clases de Bootstrap, el
    ``maxlength`` de cada texto (V-G2, para que el navegador no deje
    tipear de más) y el bloqueo optimista de V-G11.
    """

    # Testigo del bloqueo optimista. Viaja oculto y sólo se usa para
    # comparar: nunca se guarda.
    version_leida = forms.CharField(
        required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._localizar_importes()
        self._decorar_campos()
        if self.instance and self.instance.pk and \
                hasattr(self.instance, 'fecha_modificacion'):
            self.fields['version_leida'].initial = \
                self.instance.fecha_modificacion.isoformat()

    def _localizar_importes(self):
        """Los importes se tipean en formato argentino: 1500,50 (V-G4).

        Dos cosas hacen falta, y van juntas:

        * ``localize = True``, para que el servidor acepte la coma decimal
          y el punto de miles al interpretar lo que llegó.
        * cambiar el control de ``<input type="number">`` a texto. El
          control numérico del navegador se rige por el locale del sistema:
          si no coincide, rechaza "1500,50" y deja el campo VACÍO sin
          avisar. El servidor recibe un campo obligatorio sin cargar y el
          usuario no entiende por qué. Django hace este cambio solo, pero
          únicamente cuando ``localize`` se pasa al CONSTRUIR el campo, y
          un ModelForm no da esa oportunidad: hay que hacerlo acá.

        ``inputmode="decimal"`` conserva el teclado numérico en el celular.
        """
        for campo in self.fields.values():
            if not isinstance(campo, forms.DecimalField):
                continue
            campo.localize = True
            campo.widget.is_localized = True
            if isinstance(campo.widget, forms.NumberInput):
                campo.widget = forms.TextInput(attrs=dict(
                    campo.widget.attrs, inputmode='decimal'))

    def _decorar_campos(self):
        for nombre, campo in self.fields.items():
            if nombre == 'version_leida':
                continue
            widget = campo.widget
            clase = CLASES.get(type(widget), CLASE_POR_DEFECTO)
            widget.attrs.setdefault('class', clase)
            # maxlength del VARCHAR, para avisar antes de enviar (V-G2).
            largo = getattr(campo, 'max_length', None)
            if largo and isinstance(widget, (forms.TextInput, forms.Textarea)):
                widget.attrs.setdefault('maxlength', largo)

    # ------------------------------------------------------------------
    #  V-G11 · edición concurrente
    # ------------------------------------------------------------------
    def clean(self):
        datos = super().clean()
        self._comprobar_version()
        return datos

    def _comprobar_version(self):
        if not (self.instance and self.instance.pk):
            return
        if not hasattr(self.instance, 'fecha_modificacion'):
            return
        leida = (self.cleaned_data.get('version_leida') or '').strip()
        if not leida:
            return
        actual = type(self.instance).objects.filter(
            pk=self.instance.pk).values_list(
                'fecha_modificacion', flat=True).first()
        if actual is not None and actual.isoformat() != leida:
            raise ValidationError(
                'Otro usuario modificó este registro mientras lo editabas; '
                'recargá la página para ver los datos actuales y volvé a '
                'aplicar tu cambio.')

    # ------------------------------------------------------------------
    #  Capa 2 · se delega en el clean() del modelo
    # ------------------------------------------------------------------
    def _post_clean(self):
        """Corre el ``clean()`` del modelo y ubica cada error en su campo.

        Django ya llama a ``full_clean()`` de la instancia, pero excluye
        los campos que no están en el formulario. Acá no se excluye nada:
        las reglas del modelo valen para todos los caminos de entrada, y
        si una falla tiene que verse en pantalla.
        """
        super()._post_clean()
        try:
            self.instance.clean()
        except ValidationError as error:
            self.add_error(None, error)


# ===========================================================================
#  RUBROS
# ===========================================================================
class RubroForm(FormularioMaestro):
    class Meta:
        model = Rubro
        fields = ['nombre_rubro', 'descripcion_rubro', 'activo']
        widgets = {
            'nombre_rubro': forms.TextInput(
                attrs={'placeholder': 'Cafetería', 'autofocus': 'autofocus'}),
            'descripcion_rubro': forms.TextInput(
                attrs={'placeholder': 'Qué agrupa este rubro'}),
        }
        help_texts = {
            'activo': 'Si lo destildás, el rubro deja de ofrecerse al dar de '
                      'alta productos, pero los que ya tiene siguen vigentes.',
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
            'id_marca', 'nombre_producto', 'contenido', 'unidad_medida',
            'id_rubro', 'id_proveedor',
            'precio_venta', 'precio_costo', 'stock_minimo', 'codigo_barras',
            'controla_vencimiento',
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
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # V-G7: los combos ofrecen sólo lo vigente. Un rubro inactivo
        # tampoco se ofrece, aunque no esté dado de baja.
        self.fields['id_rubro'].queryset = Rubro.objects.filter(
            borrado_logico=False, activo=True)
        self.fields['id_proveedor'].queryset = Proveedor.objects.filter(
            borrado_logico=False)
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

    def clean(self):
        datos = super().clean()
        venta = datos.get('precio_venta')
        costo = datos.get('precio_costo')
        if costo is None and self.instance and self.instance.pk:
            costo = self.instance.precio_costo

        # No se bloquea vender bajo el costo —a veces es una liquidación—
        # pero tiene que ser una decisión consciente y queda auditada.
        if venta is not None and costo is not None and venta < costo:
            if not datos.get('confirmo_bajo_costo'):
                self.add_error('precio_venta', ValidationError(
                    'El precio de venta ($%(venta)s) es menor que el de costo '
                    '($%(costo)s). Si es a propósito, tildá la confirmación.',
                    params={'venta': venta, 'costo': costo}))
        return datos


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
class MedioPagoForm(FormularioMaestro):
    class Meta:
        model = MedioPago
        fields = ['nombre_medio_pago', 'recargo_porcentaje']
        widgets = {
            'nombre_medio_pago': forms.TextInput(
                attrs={'placeholder': 'Tarjeta de crédito',
                       'autofocus': 'autofocus'}),
        }
        help_texts = {
            'recargo_porcentaje': 'Porcentaje que se suma al total cuando se '
                                  'cobra con este medio. 0 si no tiene.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # El efectivo es el dato inicial protegido: ni el nombre ni el
        # recargo se tocan, porque el vuelto y el arqueo dependen de él.
        if self.instance and self.instance.pk and self.instance.es_efectivo:
            self.fields['nombre_medio_pago'].disabled = True
            self.fields['recargo_porcentaje'].disabled = True
            self.fields['nombre_medio_pago'].help_text = (
                'El medio de pago en efectivo no se renombra ni lleva '
                'recargo: el vuelto y el arqueo de caja se calculan sobre él.')


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
        self.fields['id_categoria'].queryset = \
            CategoriaFidelizacion.objects.filter(borrado_logico=False)
        for nombre in ('vigencia_desde', 'vigencia_hasta'):
            self.fields[nombre].input_formats = ['%d/%m/%Y', '%Y-%m-%d']

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


class SelectorBuscado(forms.Select):
    """``<select>`` que dibuja SÓLO la opción elegida; el resto se busca.

    Por qué existe
    --------------
    Un ``ModelChoiceField`` normal escribe un ``<option>`` por cada fila
    del queryset. Para elegir un producto entre 5.000 eso son 5.000
    opciones **por renglón**, y el formset dibuja además un molde oculto
    para los renglones nuevos: la pantalla de Beneficios llegó a 705 KB y
    8,4 segundos de armado, con el navegador clavado mientras parsea el
    HTML. Medido, no estimado.

    Acá el control nace vacío (o con la única opción que ya está elegida,
    para que la edición muestre lo que tiene cargado) y las opciones las
    trae el JS de ``lista.html`` desde ``maestros:buscar_productos``, de a
    20 y filtrando por lo que se tipea.

    Lo importante: esto cambia sólo lo que se DIBUJA. La validación sigue
    siendo la del ``ModelChoiceField``, que comprueba el id recibido
    contra su ``queryset`` completo (V-G7). Mandar un id que no está en la
    lista visible no sirve de nada: el campo lo rechaza igual.
    """

    def __init__(self, *args, url_busqueda='', **kwargs):
        super().__init__(*args, **kwargs)
        self.url_busqueda = url_busqueda

    def optgroups(self, name, value, attrs=None):
        """Las opciones se recortan a la vacía más la que esté elegida.

        ``self.choices`` es el iterador perezoso del ``ModelChoiceField``:
        recorrerlo entero traería las 5.000 filas, que es justo lo que se
        quiere evitar. Se lo reemplaza por la lista corta ANTES de que
        ``Select.optgroups()`` lo recorra.

        La opción vacía se conserva siempre: es la que muestra
        "— Buscá un producto —". Sin ella el control queda en blanco y
        parece roto.
        """
        iterador = self.choices
        queryset = getattr(iterador, 'queryset', None)
        if queryset is None:          # no es un ModelChoiceField: sin cambios
            return super().optgroups(name, value, attrs)

        campo = getattr(iterador, 'field', None)
        etiqueta_vacia = getattr(campo, 'empty_label', None)
        cortas = [('', etiqueta_vacia)] if etiqueta_vacia is not None else []

        seleccionados = {str(v) for v in value if v not in (None, '')}
        if seleccionados:
            cortas += [(str(o.pk), str(o))
                       for o in queryset.filter(pk__in=seleccionados)]

        self.choices = cortas
        try:
            return super().optgroups(name, value, attrs)
        finally:
            # Se devuelve el iterador original: el widget se reutiliza
            # entre renglones del formset, y dejarlo recortado haría que
            # el siguiente no encontrara su propia opción elegida.
            self.choices = iterador

    def build_attrs(self, base_attrs, extra_attrs=None):
        attrs = super().build_attrs(base_attrs, extra_attrs)
        attrs['data-busca-en'] = self.url_busqueda
        return attrs


class BeneficioProductoForm(FormularioMaestro):
    """Un renglón de la receta de un beneficio en especie."""

    class Meta:
        model = BeneficioProducto
        fields = ['id_producto', 'cantidad']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # El queryset completo SÍ se declara: es contra esto que se valida
        # el id que llegue. Lo que no se dibuja son sus 5.000 opciones
        # (ver SelectorBuscado).
        campo = self.fields['id_producto']
        campo.queryset = Producto.objects.filter(
            borrado_logico=False).select_related('id_marca')
        campo.widget = SelectorBuscado(
            url_busqueda=reverse_lazy('maestros:buscar_productos'),
            attrs={'class': 'form-select js-producto-buscado'})
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
