"""Formularios base compartidos (capa 1 de la sección 4.4.V).

Qué resuelve esta capa, además de dibujar los campos:

  * **Lista explícita de campos.** Nunca ``fields = '__all__'`` (V-G6).
  * **Combos sólo con vigentes** (V-G7).
  * **Formato argentino** (V-G4/V-G5): coma decimal, fecha dd/mm/aaaa.
  * **Bloqueo optimista** (V-G11) por ``fecha_modificacion``.

Las reglas de negocio viven en ``clean()`` del modelo (capa 2) y en las
restricciones de la base (capa 3).
"""
from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MinValueValidator

# Clases de Bootstrap según el tipo de control.
CLASES = {
    forms.Select: 'form-select',
    forms.SelectMultiple: 'form-select',
    forms.CheckboxInput: 'form-check-input',
}
CLASE_POR_DEFECTO = 'form-control'


def vigentes_mas_el_actual(formulario, nombre, vigentes):
    """El combo ofrece sólo lo vigente (V-G7), pero si el registro ya
    apunta a algo que después se dio de baja o se desactivó, eso también
    aparece, elegido y marcado.

    Sin esto el ``<select>`` no encuentra la opción guardada y el navegador
    muestra la primera: al guardar por otra cosa (el precio, el nombre) el
    dato cambia sin que nadie lo haya elegido, o el formulario falla con
    un «elegí una opción válida» que nadie entiende. Elegir otra cosa
    vigente sigue funcionando igual; volver a elegir lo de baja, no: sólo
    se conserva lo que ya estaba.
    """
    campo = formulario.fields[nombre]
    actual = formulario.initial.get(nombre)
    actual = getattr(actual, 'pk', actual)
    if actual in (None, '') or vigentes.filter(pk=actual).exists():
        campo.queryset = vigentes
        return
    campo.queryset = vigentes | vigentes.model.objects.filter(pk=actual)
    etiquetar = campo.label_from_instance

    def con_aviso(objeto):
        texto = etiquetar(objeto)
        if str(objeto.pk) != str(actual):
            return texto
        return f'{texto} (dado de baja)' if getattr(objeto, 'borrado_logico', False) else f'{texto} (inactivo)'

    campo.label_from_instance = con_aviso
    campo.fuera_de_uso = actual


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

    def _minimo_de(self, nombre, campo):
        """Menor valor aceptado por el campo (si tiene cota >= 0), o None."""
        minimo = getattr(campo, 'min_value', None)
        validadores = list(getattr(campo, 'validators', ()))
        modelo = getattr(getattr(self, '_meta', None), 'model', None)
        if modelo is not None:
            try:
                validadores.extend(modelo._meta.get_field(nombre).validators)
            except Exception:
                pass
        if minimo is None:
            for validador in validadores:
                if isinstance(validador, MinValueValidator):
                    limite = validador.limit_value
                    if callable(limite):
                        limite = limite()
                    minimo = limite if minimo is None else max(minimo, limite)
        if minimo is None:
            crudo = campo.widget.attrs.get('min')
            if crudo not in (None, ''):
                try:
                    minimo = float(crudo)
                except (TypeError, ValueError):
                    minimo = None
        if minimo is not None and minimo >= 0:
            return minimo
        return None

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
        Además se propaga ``data-minimo`` y ``data-decimales`` para que el
        resumen de ``.form-una-vez`` frene negativos o letras antes de pedir
        confirmación (DD-103).
        """
        for nombre, campo in self.fields.items():
            if isinstance(campo, forms.DecimalField):
                campo.localize = True
                campo.widget.is_localized = True
                minimo = self._minimo_de(nombre, campo)
                if isinstance(campo.widget, forms.NumberInput):
                    attrs = dict(campo.widget.attrs, inputmode='decimal')
                    attrs.pop('min', None)
                    attrs.pop('max', None)
                    attrs.pop('step', None)
                    campo.widget = forms.TextInput(attrs=attrs)
                if minimo is not None:
                    campo.widget.attrs.setdefault('data-minimo', str(minimo))
                    decimales = getattr(campo, 'decimal_places', None)
                    campo.widget.attrs.setdefault(
                        'data-decimales', str(2 if decimales is None else decimales))
            elif isinstance(campo, forms.IntegerField):
                minimo = self._minimo_de(nombre, campo)
                if minimo is not None:
                    campo.widget.attrs.setdefault('data-minimo', str(minimo))
                    campo.widget.attrs.setdefault('data-decimales', '0')

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

        Por eso ``clean()`` se corre de nuevo —y por eso hay que filtrar lo
        repetido—. Los errores sobre campos que SÍ están en el formulario
        ya los reportó Django en la pasada anterior; si se agregaran otra
        vez, el usuario vería el mismo cartel dos veces debajo del mismo
        control. Se agregan únicamente los mensajes nuevos.
        """
        super()._post_clean()
        try:
            self.instance.clean()
        except ValidationError as error:
            self._agregar_lo_que_falte(error)

    def _agregar_lo_que_falte(self, error):
        """Suma al formulario sólo los mensajes que no estén ya puestos."""
        if hasattr(error, 'error_dict'):
            por_campo = error.message_dict
        else:
            por_campo = {NON_FIELD_ERRORS: error.messages}

        for campo, mensajes in por_campo.items():
            # Un error sobre algo que no es un control del formulario va
            # al bloque general: no hay dónde dibujarlo si no.
            destino = campo if campo in self.fields else None
            ya_estan = set(self._errors.get(destino or NON_FIELD_ERRORS, []))
            nuevos = [m for m in mensajes if m not in ya_estan]
            if nuevos:
                self.add_error(destino, nuevos)




class SelectorConBusqueda(forms.Select):
    """Combo con búsqueda que trae su catálogo por AJAX (Select2).

    Por qué existe
    --------------
    Un ``ModelChoiceField`` normal escribe un ``<option>`` por cada fila
    del queryset. Para elegir un producto entre 5.000 eso son 5.000
    opciones **por renglón**, y el formset dibuja además un molde oculto
    para los renglones nuevos: la pantalla de Beneficios llegó a 705 KB y
    8,4 segundos de armado, con el navegador clavado mientras parsea el
    HTML. Medido, no estimado.

    Qué ve el usuario
    -----------------
    Un desplegable con buscador, que muestra el catálogo COMPLETO: se
    escribe para filtrar y se scrollea para seguir viendo: al llegar al
    final, Select2 pide la página siguiente a
    ``stock:buscar_productos``. No hay producto al que no se pueda
    llegar; lo único que cambia es que las filas viajan a medida que se
    piden, en vez de todas dentro del HTML inicial.

    En el HTML sólo se dibuja la opción YA ELEGIDA —para que la edición
    muestre lo que tiene cargado— más la vacía del placeholder. Las demás
    las agrega Select2 al abrirse.

    Lo importante: esto cambia sólo lo que se DIBUJA. La validación sigue
    siendo la del ``ModelChoiceField``, que comprueba el id recibido
    contra su ``queryset`` completo (V-G7). Mandar un id que no está en la
    lista visible no sirve de nada: el campo lo rechaza igual.

    Si el JS no corre, el control queda como un ``<select>`` con la opción
    elegida: se puede ver y guardar lo que ya estaba cargado, aunque no
    elegir uno nuevo. Es el modo degradado aceptado para una pantalla de
    administración.
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
            etiquetar = getattr(campo, 'label_from_instance', str)
            cortas += [(str(o.pk), etiquetar(o))
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
        # El JS de lista.html engancha Select2 por esta marca y usa la URL
        # como origen de datos.
        attrs['data-catalogo'] = self.url_busqueda
        return attrs


# ===========================================================================
#  IMPORTES Y CANTIDADES COMO LOS ESCRIBE UNA PERSONA
# ===========================================================================
DIGITOS_EN_BASE = 12


def leer_numero(texto, decimales):
    """'1.234,50', '1234,5', '1234.50' y '1,234.50' -> Decimal.

    En una tablet el teclado numérico pone coma o punto según el idioma
    del equipo, y en el mostrador se escribe como se escribe en Argentina.
    Reglas, en orden:

    * con coma y punto, el que va ÚLTIMO es el separador decimal;
    * sólo con coma, la coma es decimal ('1500,50');
    * sólo con punto: si el valor admite tres decimales (una cantidad en
      kilos) el punto es decimal ('1.250' kg = 1,25); en un importe, un
      punto seguido de exactamente tres dígitos es de miles ('1.500' =
      mil quinientos) y si no, decimal ('12.5').

    Devuelve None si el texto no es un número.
    """
    from decimal import Decimal, InvalidOperation

    texto = (str(texto) if texto is not None else '').strip().replace(' ', '')
    texto = texto.replace('$', '').replace('−', '-')
    if not texto:
        return None
    if ',' in texto and '.' in texto:
        decimal = ',' if texto.rfind(',') > texto.rfind('.') else '.'
        miles = '.' if decimal == ',' else ','
        texto = texto.replace(miles, '').replace(decimal, '.')
    elif ',' in texto:
        if texto.count(',') > 1:
            return None
        texto = texto.replace(',', '.')
    elif '.' in texto:
        partes = texto.split('.')
        if decimales < 3 and all(len(p) == 3 for p in partes[1:]):
            texto = texto.replace('.', '')
        elif len(partes) > 2:
            return None
    try:
        numero = Decimal(texto)
    except InvalidOperation:
        return None
    # Ni infinito ni NaN, y no más decimales que los que guarda la base:
    # 0,0005 kg se guardaba como 0,001 con el subtotal de 0,0005, la base
    # lo rechazaba por inconsistente y la pantalla se caía con un error 500.
    if not numero.is_finite() or numero.as_tuple().exponent < -decimales:
        return None
    # Ni notación científica ("1e3") ni un número que no entra en la base
    # (12 dígitos en total): un "recibido" de 25 cifras daba error 500.
    if 'e' in texto.lower() or abs(numero) >= Decimal(10) ** (DIGITOS_EN_BASE - decimales):
        return None
    return numero


def sobran_decimales(texto, decimales):
    """True si ``texto`` es un número válido pero con más decimales de los
    que se aceptan: para decir eso en vez de un genérico "no es válido"."""
    return (leer_numero(texto, decimales) is None
            and leer_numero(texto, 12) is not None)


class CampoNumero(forms.DecimalField):
    """DecimalField que entiende la coma y el punto como una persona."""

    def __init__(self, *args, decimales=2, **kwargs):
        kwargs.setdefault('decimal_places', decimales)
        kwargs.setdefault('max_digits', 12)
        kwargs.setdefault('widget', forms.TextInput(attrs={
            'class': 'form-control', 'inputmode': 'decimal',
            'autocomplete': 'off'}))
        self.decimales = decimales
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        if value in self.empty_values:
            return None
        numero = leer_numero(value, self.decimales)
        if numero is None:
            raise forms.ValidationError(
                'Escribí un número, por ejemplo 1500 o 1500,50.',
                code='invalid')
        return numero

    def widget_attrs(self, widget):
        # Sin min/max/step del navegador (rechazan la coma). El mínimo viaja
        # como dato para que el resumen de .form-una-vez avise antes de
        # mostrar «—» o un monto negativo (DD-103).
        #
        # Con el mínimo viajan los decimales que acepta el campo: el JS lee
        # lo tipeado con las MISMAS reglas que ``leer_numero``, y ahí los
        # decimales deciden si "1.500" son mil quinientos (un importe) o uno
        # con medio (una cantidad en kilos).
        if self.min_value is None:
            return {}
        return {'data-minimo': str(self.min_value),
                'data-decimales': str(self.decimales)}

    def prepare_value(self, value):
        """Se muestra como se escribe acá: 10.000 o 1.234,50."""
        from decimal import Decimal, InvalidOperation
        if value in (None, ''):
            return value
        try:
            numero = Decimal(str(value))
        except InvalidOperation:
            return value            # lo que tipeó la persona, tal cual
        texto = f'{numero:,.{self.decimales}f}'.replace(',', '§').replace('.', ',').replace('§', '.')
        if ',' in texto:
            texto = texto.rstrip('0').rstrip(',')
        return texto


class CampoImporte(CampoNumero):
    """Plata: dos decimales, nunca negativa."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('min_value', 0)
        super().__init__(*args, decimales=2, **kwargs)


class CampoCantidad(CampoNumero):
    """Unidades o kilos: hasta tres decimales, mayor que cero."""

    def __init__(self, *args, **kwargs):
        from decimal import Decimal
        kwargs.setdefault('min_value', Decimal('0.001'))
        super().__init__(*args, decimales=3, **kwargs)
