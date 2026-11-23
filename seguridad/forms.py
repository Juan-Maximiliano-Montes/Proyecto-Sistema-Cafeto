"""Formularios del módulo de Seguridad: login, clave, usuarios y perfiles.

Cada formulario refleja EXACTAMENTE una regla de negocio de la consigna:

    UsuarioAltaForm     -> en el alta sólo entran DNI, Apellido, Nombre,
                           Correo y Perfil (la clave es una provisoria al azar).
    UsuarioEdicionForm  -> en la modificación SÓLO se puede tocar el Correo.
    LoginForm           -> DNI **o** usuario + Clave para iniciar sesión.
    CambioClaveForm     -> clave actual + nueva + repetición.
    PerfilForm          -> alta/edición de un perfil y sus permisos (CRUD
                           Perfil). Es también el formulario con el que el
                           Administrador asigna y desasigna permisos.
    CambioPerfilForm    -> cambio PERMANENTE de perfil, con la clave de
                           quien lo hace (re-autenticación).
    PerfilTemporalForm  -> prestar un perfil por un rango de fechas.
    TitularidadForm     -> el titular le pasa la titularidad a otro
                           administrador, con su clave.

No hay PermisoForm: el catálogo de permisos es de sólo lectura (lo define
el código, con los @requiere_permiso de las vistas), así que no existe
ninguna pantalla que dé de alta o edite un permiso.
"""
import re

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from nucleo.validaciones import nombre_de_persona
from seguridad.models import (
    MODULOS_EXCLUSIVOS_DEL_ADMINISTRADOR,
    Perfil,
    Permiso,
    Usuario,
)


class ConClaveDelOperador(forms.Form):
    """Pide la clave de quien está operando antes de una acción sensible.

    Que la sesión esté abierta no prueba que quien está frente a la
    pantalla sea su dueño: alguien pudo dejar la compu sin bloquear. Para
    lo que no tiene vuelta atrás (dar acceso de administrador, cambiar un
    perfil, ceder la titularidad) se vuelve a pedir la clave.
    """

    clave_operador = forms.CharField(
        label='Tu clave, para confirmar',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 'autocomplete': 'current-password'}),
    )

    def __init__(self, *args, operador=None, **kwargs):
        self.operador = operador
        super().__init__(*args, **kwargs)

    def clean_clave_operador(self):
        clave = self.cleaned_data['clave_operador']
        if self.operador is None or not self.operador.verificar_clave(clave):
            raise forms.ValidationError(
                'La clave no es correcta. La acción no se realizó.')
        return clave


class UsuarioAltaForm(forms.ModelForm):
    """Alta de usuario. La clave NO se pide: la vista la genera a partir del
    DNI y la hashea antes de guardar."""

    class Meta:
        model = Usuario
        # La clave no entra al formulario: la vista genera una provisoria al
        # azar y la guarda hasheada.
        fields = ['dni_usuario', 'apellido', 'nombre', 'correo', 'id_perfil']
        widgets = {
            'dni_usuario': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': '12345678',
                'id': 'id_dni_usuario', 'maxlength': 8,
                'inputmode': 'numeric', 'pattern': r'\d{7,8}',
                # El formateador de base.html le saca los puntos mientras
                # se tipea, así "12.345.678" queda "12345678" y el
                # ``pattern`` del navegador pasa igual.
                'data-formato': 'dni',
                'title': '7 u 8 dígitos; los puntos se quitan solos',
            }),
            'apellido': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'Pérez'}),
            'nombre': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'Juan'}),
            'correo': forms.EmailInput(attrs={
                'class': 'form-control', 'placeholder': 'jperez@cafeto.com'}),
            'id_perfil': forms.Select(attrs={'class': 'form-select'}),
        }
        labels = {'id_perfil': 'Perfil', 'dni_usuario': 'DNI'}

    clave_operador = forms.CharField(
        label='Tu clave, para confirmar', required=False,
        help_text='Sólo se pide si el perfil elegido es el de administrador.',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 'autocomplete': 'current-password'}),
    )

    def __init__(self, *args, operador=None, **kwargs):
        self.operador = operador
        super().__init__(*args, **kwargs)
        self.fields['id_perfil'].queryset = Perfil.objects.filter(
            borrado_logico=False)

        # El DNI se tipea o se PEGA desde un documento, y ahí viene con
        # puntos: "40.125.874" son 10 caracteres.
        #
        # Con el maxlength de 8 (el del VARCHAR), el navegador cortaba lo
        # pegado en el caracter 8 —"40.125.8"— y recién después el
        # formateador le sacaba los puntos: quedaba 401258, un DNI
        # DISTINTO, sin ningún aviso. Y el MaxLengthValidator heredado del
        # modelo corre en ``_clean_fields()``, antes que
        # ``clean_dni_usuario()``, así que ni con el JS apagado llegaba a
        # limpiarse: devolvía un "asegúrese de que tenga menos de 8
        # caracteres" que no explica nada.
        #
        # El largo no queda sin controlar: ``clean_dni_usuario`` exige 7 u 8
        # dígitos, y Django vuelve a correr los validadores del modelo en
        # ``_post_clean()`` sobre el valor ya limpio. El ``pattern`` también
        # se va, por lo mismo: no puede describir "dígitos con o sin puntos".
        dni = self.fields['dni_usuario']
        dni.validators = []
        dni.max_length = None
        for atributo in ('maxlength', 'pattern'):
            dni.widget.attrs.pop(atributo, None)
        dni.widget.attrs['title'] = ('7 u 8 dígitos, con o sin puntos. '
                                     'Se guarda como 40125874.')

    def clean_dni_usuario(self):
        """«40.125.874» y «40125874» son el mismo DNI (paridad con ClienteForm).

        El DNI se copia de un documento o de una planilla y viene con
        puntos. Sin limpiarlos, el formulario devolvía un "el DNI son 7 u 8
        dígitos" por la restricción de la base, y el aviso de DNI repetido
        no salía nunca: "40.125.874" no es igual a "40125874", así que un
        alta con puntos creaba un usuario duplicado hasta que la base la
        frenaba con un error 500 del índice único.
        """
        texto = (self.cleaned_data.get('dni_usuario') or '').strip()
        if re.search(r'[^\d.\s]', texto):
            raise forms.ValidationError('El DNI son 7 u 8 dígitos, sin letras.')
        dni = re.sub(r'\D', '', texto)
        if not 7 <= len(dni) <= 8:
            raise forms.ValidationError('El DNI son 7 u 8 dígitos.')
        if Usuario.objects.filter(dni_usuario=dni).exists():
            raise forms.ValidationError('Ya existe un usuario con ese DNI.')
        return dni

    def clean(self):
        datos = super().clean()
        perfil = datos.get('id_perfil')
        if perfil is not None and perfil.es_sistema:
            clave = datos.get('clave_operador') or ''
            if self.operador is None or not self.operador.verificar_clave(
                    clave):
                self.add_error('clave_operador', (
                    'Dar un usuario administrador pide tu clave: escribila '
                    'para confirmar.'))
        return datos

    def clean_apellido(self):
        return nombre_de_persona(self.cleaned_data.get('apellido'), 'El apellido')

    def clean_nombre(self):
        return nombre_de_persona(self.cleaned_data.get('nombre'), 'El nombre')


class UsuarioEdicionForm(forms.ModelForm):
    """Modificación. REGLA ESTRICTA: el único campo editable es 'correo'."""

    class Meta:
        model = Usuario
        fields = ['correo']
        widgets = {
            'correo': forms.EmailInput(attrs={'class': 'form-control'}),
        }


class LoginForm(forms.Form):
    # Campo único de identificación: el operador puede tipear su DNI
    # (columna ``dni_usuario``) o su nombre de usuario autogenerado
    # (columna ``nombre_usuario``). La vista valida contra las dos
    # indistintamente (ver login_view).
    identificador = forms.CharField(
        label='DNI o Usuario',
        max_length=70,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'DNI o Usuario', 'autofocus': True,
            'autocomplete': 'username',
        }),
    )
    clave = forms.CharField(
        label='Clave',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Clave', 'autocomplete': 'current-password',
        }),
    )


class OlvideClaveForm(forms.Form):
    identificador = forms.CharField(
        label='Tu DNI o tu usuario', max_length=70,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg', 'autofocus': True,
            'placeholder': 'DNI o Usuario', 'autocomplete': 'username',
        }),
    )

    def clean_identificador(self):
        return self.cleaned_data['identificador'].strip()


class CambioClaveForm(forms.Form):
    """Cambio de clave: el obligatorio del primer ingreso y el voluntario.

    La clave nueva pasa por los validadores de ``AUTH_PASSWORD_VALIDATORS``
    (largo mínimo, claves comunes, sólo números, parecida a los datos de la
    persona). La actual se pide siempre, salvo en el cambio obligatorio:
    ahí acaba de tipearla para entrar.
    """

    clave_actual = forms.CharField(
        label='Clave actual', required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'autocomplete': 'current-password'}),
    )
    clave_nueva = forms.CharField(
        label='Clave nueva',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Mínimo 8 caracteres', 'autofocus': True,
            'autocomplete': 'new-password',
        }),
    )
    clave_repetir = forms.CharField(
        label='Repetir la clave nueva',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Escribila otra vez',
            'autocomplete': 'new-password',
        }),
    )

    def __init__(self, *args, usuario=None, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)
        self.pide_actual = usuario is not None and not usuario.debe_cambiar_clave
        if not self.pide_actual:
            del self.fields['clave_actual']

    def clean_clave_actual(self):
        clave = self.cleaned_data.get('clave_actual') or ''
        if not self.usuario.verificar_clave(clave):
            raise forms.ValidationError('La clave actual no es correcta.')
        return clave

    def clean_clave_nueva(self):
        clave = self.cleaned_data['clave_nueva']
        if self.usuario is not None:
            if clave == self.usuario.dni_usuario:
                raise forms.ValidationError(
                    'La clave no puede ser tu DNI: es lo primero que se prueba.')
            if self.usuario.verificar_clave(clave):
                raise forms.ValidationError(
                    'La clave nueva tiene que ser distinta de la actual.')
        validate_password(clave, user=self.usuario)
        return clave

    def clean(self):
        datos = super().clean()
        n, r = datos.get('clave_nueva'), datos.get('clave_repetir')
        if n and r and n != r:
            self.add_error('clave_repetir', 'Las dos claves no coinciden.')
        return datos


# ===========================================================================
#  CRUD DE PERFILES Y PERMISOS (Hito 2 — ampliación)
# ===========================================================================
class PerfilForm(forms.ModelForm):
    """Alta y edición de un perfil.

    El campo ``permisos`` se declara aparte (no como M2M automático de
    ModelForm) porque la relación pasa por la tabla intermedia
    ``PerfilPermiso``, que además guarda la fecha de asignación. La vista
    sincroniza esas filas a mano (ver ``_sincronizar_permisos``).
    """

    permisos = forms.ModelMultipleChoiceField(
        queryset=Permiso.objects.all(),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        required=False,
        label='Permisos habilitados para este perfil',
    )

    class Meta:
        model = Perfil
        fields = ['nombre', 'descripcion', 'tope_descuento_porcentaje']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'Cajero'}),
            'descripcion': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Operación diaria del local'}),
            'tope_descuento_porcentaje': forms.NumberInput(attrs={
                'class': 'form-control', 'min': 0, 'max': 100,
                'step': '0.5'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Los permisos de Seguridad son del perfil de sistema y de nadie
        # más: a los otros perfiles ni se les ofrecen.
        if not self.instance.es_sistema:
            self.fields['permisos'].queryset = Permiso.objects.exclude(
                modulo__in=MODULOS_EXCLUSIVOS_DEL_ADMINISTRADOR)
            self.fields['tope_descuento_porcentaje'].required = True
        else:
            # El administrador opera sin tope: el campo ni se muestra.
            del self.fields['tope_descuento_porcentaje']

    def clean_nombre(self):
        nombre = self.cleaned_data['nombre'].strip()
        existentes = Perfil.objects.filter(nombre__iexact=nombre)
        if self.instance.pk:
            existentes = existentes.exclude(pk=self.instance.pk)
        if existentes.exists():
            raise forms.ValidationError('Ya existe un perfil con ese nombre.')
        return nombre


# ===========================================================================
#  CAMBIOS DE PERFIL Y TITULARIDAD
# ===========================================================================
class CambioPerfilForm(ConClaveDelOperador):
    """Cambio PERMANENTE del perfil de un usuario."""

    id_perfil = forms.ModelChoiceField(
        label='Perfil nuevo', queryset=Perfil.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}))

    field_order = ['id_perfil', 'clave_operador']

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        perfiles = Perfil.objects.filter(borrado_logico=False)
        if usuario is not None:
            perfiles = perfiles.exclude(pk=usuario.id_perfil_id)
        self.fields['id_perfil'].queryset = perfiles


class PerfilTemporalForm(forms.Form):
    """Prestar otro perfil por un rango de fechas (reemplazos)."""

    id_perfil_temporal = forms.ModelChoiceField(
        label='Perfil temporal', queryset=Perfil.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}))
    temporal_desde = forms.DateTimeField(
        label='Desde', input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(
            attrs={'class': 'form-control', 'type': 'datetime-local'},
            format='%Y-%m-%dT%H:%M'))
    temporal_hasta = forms.DateTimeField(
        label='Hasta', input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(
            attrs={'class': 'form-control', 'type': 'datetime-local'},
            format='%Y-%m-%dT%H:%M'))
    temporal_motivo = forms.CharField(
        label='Motivo', max_length=120,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Cubre las vacaciones de Lucía'}))

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        perfiles = Perfil.objects.filter(borrado_logico=False,
                                         es_sistema=False)
        if usuario is not None:
            perfiles = perfiles.exclude(pk=usuario.id_perfil_id)
        self.fields['id_perfil_temporal'].queryset = perfiles
        if not self.is_bound:
            self.initial.setdefault(
                'temporal_desde',
                timezone.localtime().replace(second=0, microsecond=0))


class TitularidadForm(ConClaveDelOperador):
    """El titular cede la titularidad a otro administrador activo."""

    nuevo_titular = forms.ModelChoiceField(
        label='Nuevo titular', queryset=Usuario.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}))

    field_order = ['nuevo_titular', 'clave_operador']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        candidatos = Usuario.objects.filter(
            activo=True, id_perfil__es_sistema=True)
        if self.operador is not None:
            candidatos = candidatos.exclude(pk=self.operador.pk)
        self.fields['nuevo_titular'].queryset = candidatos
