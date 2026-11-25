"""Fábrica de datos de prueba compartida por los tests de procesos.

Arma lo mínimo para operar: un proveedor, productos de cada tipo, un
usuario por perfil y un turno abierto. Todo pasa por los mismos caminos
que usa el sistema (el stock entra por una compra, no se escribe a mano).
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from compras import servicios as compras
from compras.models import Proveedor
from seguridad.models import Perfil, Usuario
from stock.models import ComposicionProducto, Marca, Producto, Rubro
from ventas import caja
from ventas.models import CajaRegistradora, MedioPago

D = Decimal
CLAVE = 'Clave.Segura.2026'
_numero = {'n': 0}


def usuario(dni, perfil_nombre='Operador', apellido='Prueba', nombre=None):
    perfil = Perfil.objects.get(nombre=perfil_nombre)
    nuevo = Usuario(dni_usuario=dni, apellido=apellido,
                    nombre=nombre or perfil_nombre,
                    correo=f'{dni}@cafeto.test', id_perfil=perfil)
    nuevo.set_clave(CLAVE)
    nuevo.save()
    return nuevo


def titular():
    """La dueña del sistema (la crea la migración inicial), con la clave de
    las pruebas y lista para operar."""
    duenia = Usuario.objects.get(es_titular=True)
    duenia.set_clave(CLAVE)
    duenia.debe_cambiar_clave = False
    duenia.save()
    return duenia


def proveedor(nombre='Distribuidora Norte', cuit='30-71234567-1'):
    return Proveedor.objects.create(nombre_proveedor=nombre,
                                    cuit_proveedor=cuit)


def rubro(nombre='Minisuper'):
    return Rubro.objects.get(nombre_rubro=nombre)


def marca(nombre='Sin marca', es_propia=None):
    """La marca ``nombre``. Las del propio local ('Sin marca', 'Genérica',
    'Cafeto') son ``es_propia``; cualquier otra, de un fabricante."""
    if es_propia is None:
        es_propia = nombre in ('Sin marca', 'Genérica', 'Cafeto')
    nueva = Marca.objects.get_or_create(
        nombre_marca=nombre, defaults={'es_propia': es_propia})[0]
    if es_propia and not nueva.es_propia:
        Marca.objects.filter(pk=nueva.pk).update(es_propia=True)
        nueva.es_propia = True
    return nueva


def producto(nombre, tipo='REVENTA', precio='1000', costo='600', prov=None,
             rubro_nombre='Minisuper', marca_nombre='Sin marca',
             contenido='1', unidad='UNIDAD', **extra):
    # Lo que se prepara en el local lleva una marca propia (es_propia).
    propia = True if tipo in ('ELABORADO', 'PRODUCIDO') else None
    datos = dict(
        tipo_producto=tipo, nombre_producto=nombre,
        id_rubro=rubro(rubro_nombre), id_marca=marca(marca_nombre, propia),
        contenido=D(contenido), unidad_medida=unidad,
        precio_venta=D(precio) if tipo != 'INSUMO' else D('0'),
        precio_costo=D(costo), id_proveedor=prov)
    datos.update(extra)
    nuevo = Producto(**datos)
    nuevo.full_clean()
    nuevo.save()
    return nuevo


def receta(compuesto, *componentes):
    """``receta(cafe, (grano, '0.018'), (leche, '0.15'))``."""
    for componente, cantidad in componentes:
        ComposicionProducto.objects.create(
            id_producto=compuesto, id_componente=componente,
            cantidad=D(cantidad))
    return compuesto


def comprar(usuario_, prov, *renglones, otros='0', numero=None):
    """``comprar(u, p, (producto, cantidad, costo[, [(vence, cant), ...]]))``."""
    _numero['n'] += 1
    lista = []
    for renglon in renglones:
        prod, cantidad, costo = renglon[:3]
        lotes = []
        if len(renglon) > 3:
            lotes = [compras.LoteRecibido(vence, D(cant), f'L{i}')
                     for i, (vence, cant) in enumerate(renglon[3], 1)]
        lista.append(compras.RenglonRecibido(prod, D(cantidad), D(costo),
                                             lotes))
    return compras.recepcionar(
        prov, usuario_, 'FACTURA_B', 1, numero or _numero['n'], lista,
        importe_otros_conceptos=D(otros))


def caja_cafeteria():
    return CajaRegistradora.objects.get(
        nombre_caja_registradora='Caja Cafetería')


def caja_minisuper():
    return CajaRegistradora.objects.get(
        nombre_caja_registradora='Caja Minisuper')


def abrir_turno(usuario_, cajon=None, fondo='5000'):
    return caja.abrir(cajon or caja_minisuper(), usuario_, D(fondo))


def medio(nombre):
    return MedioPago.objects.get(nombre_medio_pago=nombre)


def efectivo():
    return medio('Efectivo')


def en_dias(dias):
    return timezone.localdate() + timedelta(days=dias)


def entrar(cliente, usuario_):
    """Deja al ``cliente`` de pruebas logueado como ``usuario_``.

    Por el mismo camino que el login (``abrir_sesion`` + ``vincular_sesion``):
    el middleware valida el token en cada pedido.
    """
    from seguridad.servicios import abrir_sesion, vincular_sesion

    if usuario_.debe_cambiar_clave:
        usuario_.debe_cambiar_clave = False
        usuario_.save()
    sesion_negocio = abrir_sesion(usuario_, ip='127.0.0.1')
    sesion = cliente.session
    vincular_sesion(sesion, usuario_, sesion_negocio)
    sesion.save()
    return sesion_negocio
