"""La caja del puesto, para el chip de la barra superior."""


def caja_del_puesto(request):
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return {}
    from ventas.puesto import caja_de, turno_de

    caja = caja_de(request)
    if caja is None:
        return {'caja_de_la_sesion': None, 'turno_de_la_sesion': None}
    return {'caja_de_la_sesion': caja, 'turno_de_la_sesion': turno_de(request)}
