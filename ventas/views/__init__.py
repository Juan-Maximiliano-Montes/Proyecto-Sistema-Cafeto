"""Pantallas del módulo Ventas, una por proceso.

    catalogos   medios de pago y cajas registradoras (CRUD genérico)
    caja        el puesto: elegir el cajón, abrir, mover efectivo, cerrar
    mostrador   cobrar
    cambios     cambios y devoluciones de una venta
    consultas   tickets y turnos, para quien supervisa

Se importa ``catalogos`` acá porque registra sus CRUD al cargar la app.
"""
from . import catalogos

__all__ = ['catalogos']
