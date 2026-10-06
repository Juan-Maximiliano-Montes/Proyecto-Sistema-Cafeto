"""URLs de "Mantener datos maestros".

Todas las entidades comparten las mismas seis rutas; lo único que cambia
es la clave de la entidad, que viaja como primer segmento:

    /maestros/<entidad>/                    listar
    /maestros/<entidad>/crear/              alta
    /maestros/<entidad>/<pk>/ver/           ficha de sólo lectura
    /maestros/<entidad>/<pk>/editar/        modificación
    /maestros/<entidad>/<pk>/baja/          baja lógica
    /maestros/<entidad>/<pk>/reactivar/     deshacer la baja

Los nombres de ruta se arman como ``<entidad>_<accion>`` para que
``ConfiguracionCRUD.url()`` pueda resolverlos sin tabla de equivalencias.
``views`` se importa aunque no se use de forma directa: es el módulo que
registra las configuraciones.
"""
from django.urls import path

from . import crud, views  # noqa: F401  (views registra las entidades)

app_name = 'maestros'


def rutas_de(clave):
    """Las seis rutas de una entidad."""
    return [
        path(f'{clave}/', crud.listar, {'clave': clave},
             name=f'{clave}_lista'),
        path(f'{clave}/crear/', crud.crear, {'clave': clave},
             name=f'{clave}_crear'),
        path(f'{clave}/<int:pk>/ver/', crud.ver, {'clave': clave},
             name=f'{clave}_ver'),
        path(f'{clave}/<int:pk>/editar/', crud.editar, {'clave': clave},
             name=f'{clave}_editar'),
        path(f'{clave}/<int:pk>/baja/', crud.dar_de_baja, {'clave': clave},
             name=f'{clave}_eliminar'),
        path(f'{clave}/<int:pk>/reactivar/', crud.reactivar, {'clave': clave},
             name=f'{clave}_reactivar'),
    ]


ENTIDADES = ['rubros', 'marcas', 'productos', 'tipos_egreso', 'medios_pago',
             'cajas_registradoras', 'categorias', 'beneficios']

urlpatterns = [ruta for clave in ENTIDADES for ruta in rutas_de(clave)]
