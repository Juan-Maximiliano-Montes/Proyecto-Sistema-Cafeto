"""La caja del puesto y las cajas del local, para la barra superior."""


def caja_del_puesto(request):
    """``caja_de_la_sesion`` es el cajón de ESTE dispositivo: lo ve todo el
    que trabaja en él. ``cajas_del_local`` es el estado de todos los cajones
    y sólo se arma para quien supervisa la caja (``ver_caja``): la cajera no
    necesita saber qué pasa en el otro mostrador."""
    usuario = getattr(request, 'usuario_actual', None)
    if usuario is None:
        return {}
    from ventas.puesto import caja_de, turno_de

    caja = caja_de(request)
    contexto = {'caja_de_la_sesion': caja,
                'turno_de_la_sesion': turno_de(request) if caja else None}
    if usuario.puede('ver_caja'):
        from ventas.models import CajaRegistradora, TurnoCaja

        abiertos = {t.id_caja_registradora_id: t for t in
                    TurnoCaja.objects.filter(estado_turno='ABIERTA')
                    .select_related('id_usuario_apertura')}
        cajas = [(c, abiertos.get(c.pk)) for c in
                 CajaRegistradora.objects.filter(borrado_logico=False)
                 .order_by('nombre_caja_registradora')]
        contexto['cajas_del_local'] = cajas
        contexto['cajas_abiertas'] = sum(1 for _c, t in cajas if t)
    from ventas import a_distancia
    if a_distancia.puede_contestar(usuario):
        # Lo que la caja está esperando que alguien apruebe (DD-94).

        contexto['autorizaciones_pendientes'] = a_distancia.pendientes().count()
    return contexto
