"""Textos con la cantidad bien dicha: «1 pago venció», «3 pagos vencieron».

    pluralizar(1, 'pago')                    -> '1 pago'
    pluralizar(3, 'renglón', 'renglones')    -> '3 renglones'
    segun(3, 'venció', 'vencieron')          -> 'vencieron'

En las plantillas, los filtros del mismo nombre de ``cafeto``:
``{{ n|pluralizar:"renglón,renglones" }}`` y ``{{ n|segun:"vendió,vendieron" }}``
(si alguna forma lleva coma, se separan con ``|``).
"""


def segun(cantidad, singular, plural):
    """La forma que corresponde a ``cantidad`` (singular sólo para 1)."""
    try:
        uno = float(cantidad) == 1
    except (TypeError, ValueError):
        uno = False
    return singular if uno else plural


def pluralizar(cantidad, singular, plural=None):
    """La cantidad seguida del sustantivo en singular o plural."""
    return f'{cantidad} {segun(cantidad, singular, plural or singular + "s")}'
