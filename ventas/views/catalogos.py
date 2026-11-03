"""Pantallas de los catálogos de Ventas: medios de pago y cajas."""

from nucleo.crud import Columna, ConfiguracionCRUD, registrar
from ventas.models import CajaRegistradora, MedioPago

from ventas import forms

MEDIOS_PAGO = registrar(ConfiguracionCRUD(
    clave='medios_pago',
    modelo=MedioPago,
    formulario=forms.MedioPagoForm,
    singular='medio de pago', plural='medios de pago', articulo='el',
    titulo='Medios de pago', icono='bi-credit-card',
    campo_nombre='nombre_medio_pago',
    permiso_singular='medio_pago',
    columnas=(
        Columna('Medio de pago', 'nombre_medio_pago'),
        Columna('Recargo', 'recargo_porcentaje', 'numero'),
        Columna('Entra al cajón', 'afecta_caja', 'si_no'),
    ),
    campos_busqueda=('nombre_medio_pago',),
    orden=('nombre_medio_pago',),
))


CAJAS_REGISTRADORAS = registrar(ConfiguracionCRUD(
    clave='cajas_registradoras',
    modelo=CajaRegistradora,
    formulario=forms.CajaRegistradoraForm,
    singular='caja registradora', plural='cajas registradoras', articulo='la',
    titulo='Cajas registradoras', icono='bi-shop',
    campo_nombre='nombre_caja_registradora',
    permiso_singular='caja_registradora',
    columnas=(
        Columna('Caja registradora', 'nombre_caja_registradora'),
    ),
    campos_busqueda=('nombre_caja_registradora',),
    orden=('nombre_caja_registradora',),
))
