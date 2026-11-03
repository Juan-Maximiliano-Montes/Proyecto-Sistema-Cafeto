"""Pantallas de catálogo del módulo Compras: proveedores y tipos de egreso.

Las dos pasan por la máquina genérica de ``nucleo.crud``, que les da lo
mismo que a todas las maestras: búsqueda sin acentos, tarjetas paginadas,
exportación, bloqueo optimista, auditoría de cada cambio y la regla de no
dar de baja lo que todavía se usa (``Proveedor.motivos_que_impiden_la_baja``:
órdenes abiertas, compras impagas, productos vigentes).

Antes Proveedores tenía vistas propias, escritas a mano, que se habían
quedado atrás: daban de baja sin mirar los dependientes y no auditaban.
"""
from compras.models import CondicionPago, Proveedor, TipoEgreso, TipoProveedor
from nucleo.crud import Columna, ConfiguracionCRUD, Filtro, registrar

from compras import forms


def _condiciones():
    return CondicionPago.choices


def _tipos_de_proveedor():
    return TipoProveedor.choices


PROVEEDORES = registrar(ConfiguracionCRUD(
    clave='proveedores',
    modelo=Proveedor,
    formulario=forms.ProveedorForm,
    singular='proveedor', plural='proveedores', articulo='el',
    titulo='Proveedores', icono='bi-truck',
    campo_nombre='nombre_proveedor',
    permiso_singular='proveedor',
    columnas=(
        Columna('Proveedor', 'nombre_proveedor'),
        Columna('CUIT', 'cuit_proveedor'),
        Columna('Tipo', 'get_tipo_proveedor_display'),
        Columna('Condición de pago', 'get_condicion_pago_display'),
        Columna('Teléfono', 'telefono_proveedor'),
        Columna('Localidad', 'id_localidad.nombre_localidad'),
    ),
    campos_busqueda=('nombre_proveedor', 'cuit_proveedor', 'email_proveedor',
                     'domicilio', 'id_localidad__nombre_localidad'),
    filtros=(
        Filtro('tipo', 'Tipo', 'tipo_proveedor', _tipos_de_proveedor),
        Filtro('condicion', 'Condición de pago', 'condicion_pago',
               _condiciones),
    ),
    relaciones=('id_localidad', 'id_localidad__id_provincia',
                'usuario_modificacion'),
    orden=('nombre_proveedor',),
    acciones_extra=(
        ('Importar', 'bi-file-earmark-arrow-up', 'importar', ('proveedores',),
         'importar_proveedores'),
    ),
))


TIPOS_EGRESO = registrar(ConfiguracionCRUD(
    clave='tipos_egreso',
    modelo=TipoEgreso,
    formulario=forms.TipoEgresoForm,
    singular='tipo de egreso', plural='tipos de egreso', articulo='el',
    titulo='Tipos de egreso', icono='bi-arrow-down-circle',
    campo_nombre='nombre_tipo_egreso',
    permiso_singular='tipo_egreso',
    columnas=(
        Columna('Tipo de egreso', 'nombre_tipo_egreso'),
        Columna('Descripción', 'descripcion_tipo_egreso'),
    ),
    campos_busqueda=('nombre_tipo_egreso', 'descripcion_tipo_egreso'),
    orden=('nombre_tipo_egreso',),
))
