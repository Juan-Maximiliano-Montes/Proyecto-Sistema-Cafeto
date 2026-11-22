"""Los dos registros reservados del catálogo (DD-86).

Un producto necesita marca y, si se compra, proveedor. El alta simple y el
importador no los piden: usan «Genérica» y «Proveedor General (Por
asignar)», que después se reemplazan por los de verdad (al recibir la
primera compra, o editando el producto).

Los crea una migración; estas funciones los vuelven a crear si faltan (una
base restaurada de un respaldo viejo, una prueba) y los reactivan si
alguien los dio de baja por fuera del sistema.
"""
from compras.models import (
    CUIT_PROVEEDOR_GENERAL,
    NOMBRE_PROVEEDOR_GENERAL,
    Proveedor,
    TipoProveedor,
)
from stock.models import MARCA_GENERICA, Marca


def marca_generica():
    marca = Marca.objects.filter(nombre_marca=MARCA_GENERICA).first()
    if marca is None:
        marca = Marca.objects.create(
            nombre_marca=MARCA_GENERICA,
            descripcion_marca='Productos que todavía no tienen la marca cargada')
    if marca.borrado_logico:
        Marca.objects.filter(pk=marca.pk).update(borrado_logico=False)
        marca.borrado_logico = False
    return marca


def proveedor_general():
    proveedor, _ = Proveedor.objects.get_or_create(
        cuit_proveedor=CUIT_PROVEEDOR_GENERAL,
        defaults={'nombre_proveedor': NOMBRE_PROVEEDOR_GENERAL,
                  'tipo_proveedor': TipoProveedor.MIXTO})
    if proveedor.borrado_logico:
        Proveedor.objects.filter(pk=proveedor.pk).update(borrado_logico=False)
        proveedor.borrado_logico = False
    return proveedor


def asignar_marca(producto, texto):
    """Le pone marca a un producto que todavía tiene «Genérica».

    Devuelve (True, marca) o (False, motivo). No pisa una marca que cargó
    una persona, y no la pone si el producto quedaría igual a otro (misma
    marca, nombre y presentación: ``uq_productos_identidad``).
    """
    from django.core.exceptions import ValidationError

    from nucleo.validaciones import clave_normalizada
    from stock.models import Producto

    texto = ' '.join(str(texto or '').split())[:50]
    if not texto:
        return False, 'No vino ninguna marca.'
    if producto.id_marca.nombre_marca != MARCA_GENERICA:
        return False, 'El producto ya tiene una marca cargada.'
    clave = clave_normalizada(texto)
    if clave == clave_normalizada(MARCA_GENERICA):
        return False, 'Es la misma marca reservada.'
    marca = next((m for m in Marca.objects.all() if clave_normalizada(m.nombre_marca) == clave), None)
    if marca is None:
        marca = Marca(nombre_marca=texto)
        try:
            marca.full_clean()
        except ValidationError as error:
            return False, ' '.join(error.messages)
        marca.save()
    elif marca.borrado_logico:
        return False, f'La marca "{marca}" está dada de baja.'
    nombre = clave_normalizada(producto.nombre_producto)
    gemelos = Producto.objects.filter(
        id_marca=marca, contenido=producto.contenido,
        unidad_medida=producto.unidad_medida).exclude(pk=producto.pk)
    if any(clave_normalizada(g.nombre_producto) == nombre for g in gemelos):
        return False, 'Ya hay otro producto con esa marca, nombre y presentación.'
    Producto.objects.filter(pk=producto.pk).update(id_marca=marca)
    producto.id_marca = marca
    return True, marca
