from django.contrib import admin

from .models import Localidad, Provincia, Proveedor


@admin.register(Provincia)
class ProvinciaAdmin(admin.ModelAdmin):
    list_display = ('id_provincia', 'nombre_provincia')
    search_fields = ('nombre_provincia',)


@admin.register(Localidad)
class LocalidadAdmin(admin.ModelAdmin):
    list_display = ('id_localidad', 'nombre_localidad', 'id_provincia',
                    'codigo_postal')
    list_filter = ('id_provincia',)
    search_fields = ('nombre_localidad', 'codigo_postal')


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    #Configura cómo se muestra el modelo Proveedor en el panel /admin.

    list_display = ('id_proveedor', 'nombre_proveedor', 'cuit_proveedor',
                    'telefono_proveedor', 'domicilio', 'id_localidad',
                    'condicion_pago', 'borrado_logico', 'fecha_modificacion',
                    'usuario_modificacion')
    list_filter = ('borrado_logico', 'condicion_pago', 'id_localidad')
    search_fields = ('nombre_proveedor', 'cuit_proveedor', 'domicilio')
    readonly_fields = ('fecha_alta', 'fecha_modificacion')
