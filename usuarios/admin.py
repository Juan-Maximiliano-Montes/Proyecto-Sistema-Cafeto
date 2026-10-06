from django.contrib import admin

from .models import Perfil, PerfilPermiso, Permiso, Usuario


class PerfilPermisoInline(admin.TabularInline):
    model = PerfilPermiso
    extra = 1


@admin.register(Perfil)
class PerfilAdmin(admin.ModelAdmin):
    list_display = ('id_perfil', 'nombre', 'descripcion',
                    'fecha_modificacion', 'usuario_modificacion')
    search_fields = ('nombre',)
    readonly_fields = ('fecha_modificacion',)
    inlines = [PerfilPermisoInline]


@admin.register(Permiso)
class PermisoAdmin(admin.ModelAdmin):
    list_display = ('id_permiso', 'codigo', 'nombre', 'modulo', 'descripcion',
                    'fecha_modificacion', 'usuario_modificacion')
    list_filter = ('modulo',)
    search_fields = ('codigo', 'nombre')
    readonly_fields = ('fecha_modificacion',)


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('id_usuario', 'dni_usuario', 'nombre_usuario', 'apellido',
                    'nombre', 'correo', 'id_perfil', 'activo',
                    'debe_cambiar_clave', 'fecha_ultima_modificacion')
    list_filter = ('activo', 'debe_cambiar_clave', 'id_perfil')
    search_fields = ('dni_usuario', 'apellido', 'nombre', 'nombre_usuario',
                     'correo')
    readonly_fields = ('nombre_usuario', 'clave', 'fecha_ultima_modificacion',
                       'fecha_baja')
