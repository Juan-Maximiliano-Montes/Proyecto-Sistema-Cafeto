"""Cómo se redacta un "no se puede dar de baja".

Los modelos con baja lógica contestan ``motivos_que_impiden_la_baja()`` con
una lista de textos, y el CRUD los engarza en una sola frase::

    No se puede dar de baja "Cafetería" porque tiene 3 producto(s)
    vigente(s); dalos de baja primero.

Para que eso se lea bien, cada motivo es un FRAGMENTO que continúa el
"porque": empieza en minúscula y no lleva punto final.

Hay restricciones que no entran en ese molde. El medio de pago "Efectivo"
no se bloquea porque algo dependa de él, sino por lo que es: el método por
defecto del sistema. Ahí la explicación es una oración entera, y meterla
detrás de un "porque" la rompe.

``MotivoCompleto`` marca justamente eso: "este texto YA es el mensaje, no
lo engarces". Es un ``str`` común, así que un modelo lo puede devolver en
la misma lista que los fragmentos y los tests lo comparan igual que
cualquier cadena; lo único que cambia es cómo lo arma el CRUD.
"""


class MotivoCompleto(str):
    """Un motivo que se muestra tal cual, sin el prefijo del CRUD."""

    __slots__ = ()


def redactar(encabezado, motivos):
    """Arma el mensaje final a partir de los motivos de un modelo.

    ``encabezado`` es la parte que introduce los fragmentos, ya sin el
    "porque" (lo pone esta función): por ejemplo ``'No se puede dar de baja
    "Efectivo"'``.

    Los ``MotivoCompleto`` salen enteros y primero —son la razón de fondo—;
    los fragmentos se juntan con punto y coma detrás del encabezado. Si sólo
    hay motivos completos, el encabezado no se usa: ya lo dicen ellos.
    """
    completos = [m for m in motivos if isinstance(m, MotivoCompleto)]
    fragmentos = [m for m in motivos if not isinstance(m, MotivoCompleto)]

    partes = list(completos)
    if fragmentos:
        partes.append(f'{encabezado} porque ' + '; '.join(fragmentos) + '.')
    return ' '.join(partes)
