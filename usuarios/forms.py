"""Formularios del módulo de usuarios.

Cada formulario refleja EXACTAMENTE una regla de negocio de la consigna:

    UsuarioAltaForm     -> en el alta sólo entran DNI, Apellido, Nombre,
                           Correo y Perfil (la clave se autogenera con el DNI).
    UsuarioEdicionForm  -> en la modificación SÓLO se puede tocar el Correo.
    LoginForm           -> DNI **o** usuario + Clave para iniciar sesión.
    CambioClaveForm     -> clave nueva + repetición (pantalla obligatoria).
    PerfilForm          -> alta/edición de un perfil y sus permisos (CRUD
                           Perfil). Es también el formulario con el que el
                           Administrador asigna y desasigna permisos.

No hay PermisoForm: el catálogo de permisos es de sólo lectura (lo define
el código, con los @requiere_permiso de las vistas), así que no existe
ninguna pantalla que dé de alta o edite un permiso.
"""
from django import forms

from .models import Perfil, Permiso, Usuario


class UsuarioAltaForm(forms.ModelForm):
    """Alta de usuario. La clave NO se pide: la vista la genera a partir del
    DNI y la hashea antes de guardar."""

    class Meta:
        model = Usuario
        # La clave no entra al formulario: se autogenera con el DNI en la
        # vista y se guarda hasheada.
        fields = ['dni_usuario', 'apellido', 'nombre', 'correo', 'id_perfil']
        widgets = {
            'dni_usuario': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': '12345678',
                'id': 'id_dni_usuario', 'maxlength': 8,
                'inputmode': 'numeric', 'pattern': r'\d{7,8}',
                'title': '7 u 8 dígitos, sin puntos',
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

    def clean_dni_usuario(self):
        dni = self.cleaned_data['dni_usuario'].strip()
        if Usuario.objects.filter(dni_usuario=dni).exists():
            raise forms.ValidationError('Ya existe un usuario con ese DNI.')
        return dni


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
            'placeholder': 'Clave',
        }),
    )


class CambioClaveForm(forms.Form):
    """Pantalla de 'Cambio de Clave Obligatorio'."""

    clave_nueva = forms.CharField(
        label='Clave nueva',
        min_length=6,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Mínimo 6 caracteres', 'autofocus': True,
        }),
    )
    clave_repetir = forms.CharField(
        label='Repetir la clave nueva',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Escribila otra vez',
        }),
    )

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
        fields = ['nombre', 'descripcion']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'Cajero'}),
            'descripcion': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Operación diaria del local'}),
        }

    def clean_nombre(self):
        nombre = self.cleaned_data['nombre'].strip()
        existentes = Perfil.objects.filter(nombre__iexact=nombre)
        if self.instance.pk:
            existentes = existentes.exclude(pk=self.instance.pk)
        if existentes.exists():
            raise forms.ValidationError('Ya existe un perfil con ese nombre.')
        return nombre


class PermisoForm(forms.ModelForm):
    """Alta y edición de un permiso suelto."""

    class Meta:
        model = Permiso
        fields = ['codigo', 'nombre', 'descripcion', 'modulo']
        widgets = {
            'codigo': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'ver_reportes'}),
            'nombre': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'Ver reportes'}),
            'descripcion': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Acceder al módulo de reportes'}),
            'modulo': forms.Select(attrs={'class': 'form-select'}),
        }
        help_texts = {
            'codigo': 'Sólo letras, números, guiones y guión bajo. '
                      'Es el texto que usan los decoradores @requiere_permiso.',
            'modulo': 'Agrupa el permiso en la pantalla de Permisos y en el '
                      'tilde de permisos de un perfil.',
        }

    def clean_codigo(self):
        codigo = self.cleaned_data['codigo']
        existentes = Permiso.objects.filter(codigo=codigo)
        if self.instance.pk:
            existentes = existentes.exclude(pk=self.instance.pk)
        if existentes.exists():
            raise forms.ValidationError('Ya existe un permiso con ese código.')
        return codigo
