"""Pantallas del módulo Compras, una por proceso.

    catalogos     proveedores y tipos de egreso (CRUD genérico)
    recepciones   la mercadería que llega con su factura, y las compras
    ordenes       los pedidos al proveedor
    egresos       lo que hay que pagar, lo pagado y la conciliación

Se importa ``catalogos`` acá porque registra sus CRUD al cargar la app.
"""
from . import catalogos

__all__ = ['catalogos']
