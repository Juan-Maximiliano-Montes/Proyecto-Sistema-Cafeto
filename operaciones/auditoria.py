"""Traduce las fotos JSONB de LOG_AUDITORIA a algo que se pueda leer.

``registro_anterior`` y ``registro_nuevo`` guardan la fila tal como la dejó
``instantanea()``: las claves son **nombres de columna** y los valores, lo
que entra en un JSON. Crudo, una modificación de producto se ve así::

    {"id_marca": 2, "precio_venta": "8500.00", "borrado_logico": false}

Eso le sirve a quien programa. A la dueña del local, no. Este módulo lo
convierte en::

    Marca            La Morenita
    Precio de venta  8500.00
    Borrado lógico   No

Tres traducciones, en este orden:

1. **De columna a etiqueta.** Se averigua de qué modelo es la foto mirando
   su clave primaria —toda instantánea la trae— y se piden los
   ``verbose_name`` de ese modelo.
2. **De id a nombre.** Una clave foránea guarda un número. Con el modelo ya
   identificado se sabe a qué tabla apunta y se trae el ``__str__`` de la
   fila: "La Morenita" en vez de 2.
3. **De valor crudo a valor legible.** ``true`` pasa a "Sí", ``null`` a un
   guión, y una fecha ISO al formato de acá.

Y sobre todo: se marca **qué cambió**. Una modificación de quince campos en
la que sólo se tocó el precio tiene que mostrar ese precio resaltado, no
quince filas iguales entre las que haya que buscar a ojo.
"""
import datetime

from django.apps import apps


# Columnas que no aportan nada al leer un cambio: son el sello técnico de la
# fila, no el dato que alguien modificó.
RUIDO = {'fecha_modificacion', 'usuario_modificacion'}


def modelo_de(datos):
    """El modelo al que pertenece la foto, o None si no se reconoce.

    No alcanza con buscar el modelo cuya clave primaria esté entre las
    claves: la foto de un producto trae ``id_marca``, ``id_rubro`` y
    ``id_proveedor`` —sus claves foráneas—, que son las PK de otras tres
    tablas. Buscando sólo por PK, el producto se confundía con la marca y
    todas las etiquetas salían mal.

    Así que se puntúa: gana el modelo que explique MÁS columnas de la foto,
    con el desempate en el que tenga menos columnas de sobra. El producto
    explica sus catorce claves; la marca, tres. No hay empate posible.

    Se exige además que su PK esté presente: ``instantanea()`` recorre
    ``_meta.fields``, así que la PK de la fila fotografiada siempre está, y
    pedirla descarta de entrada a los modelos que sólo coinciden de refilón.
    """
    if not datos:
        return None

    claves = set(datos)
    mejor, mejor_puntaje = None, (0, 0)
    for modelo in apps.get_models():
        pk = getattr(modelo._meta, 'pk', None)
        if pk is None or pk.column not in claves:
            continue
        columnas = {c.column for c in modelo._meta.fields}
        explicadas = len(claves & columnas)
        puntaje = (explicadas, -len(columnas - claves))
        if puntaje > mejor_puntaje:
            mejor, mejor_puntaje = modelo, puntaje
    return mejor


def _campo_de(modelo, columna):
    if modelo is None:
        return None
    for campo in modelo._meta.fields:
        if campo.column == columna:
            return campo
    return None


def etiqueta_de(modelo, columna):
    """El ``verbose_name`` del campo, o la columna si no se la reconoce."""
    campo = _campo_de(modelo, columna)
    if campo is None:
        return columna.replace('_', ' ').capitalize()
    etiqueta = str(campo.verbose_name)
    return etiqueta[0].upper() + etiqueta[1:] if etiqueta else columna


def valor_legible(modelo, columna, valor):
    """El valor como lo entendería una persona.

    Una FK se resuelve contra su tabla. Si la fila apuntada ya no existe
    —puede haberse purgado— se devuelve el id, que es la verdad disponible,
    y no se rompe: la auditoría tiene que poder leerse siempre.
    """
    if valor is None:
        return '—'
    if isinstance(valor, bool):
        return 'Sí' if valor else 'No'

    campo = _campo_de(modelo, columna)
    if campo is not None and campo.is_relation and campo.related_model:
        try:
            return str(campo.related_model.objects.get(pk=valor))
        except Exception:
            return '#%s (ya no existe)' % valor

    if isinstance(valor, str):
        legible = _fecha_legible(valor)
        if legible is not None:
            return legible
        return valor or '—'
    return valor


def _fecha_legible(texto):
    """"2026-10-05T17:45:00" -> "05/10/2026 17:45". None si no es fecha."""
    for formato, salida in (('%Y-%m-%dT%H:%M:%S', '%d/%m/%Y %H:%M'),
                            ('%Y-%m-%d', '%d/%m/%Y')):
        try:
            return datetime.datetime.strptime(texto[:19], formato).strftime(salida)
        except ValueError:
            continue
    return None


class Fila:
    """Una línea de la comparación, ya lista para el template."""

    def __init__(self, etiqueta, antes, despues, cambio):
        self.etiqueta = etiqueta
        self.antes = antes
        self.despues = despues
        self.cambio = cambio        # 'alta' | 'baja' | 'cambio' | 'igual'

    @property
    def destacada(self):
        return self.cambio != 'igual'


def comparar(anterior, nuevo):
    """Las dos fotos, lado a lado y en castellano.

    Devuelve ``(filas, resumen)``. ``resumen`` dice de una qué clase de
    operación fue, porque es lo primero que se quiere saber:

      * sin ``anterior``  -> se creó el registro;
      * sin ``nuevo``     -> se eliminó;
      * con los dos       -> se modificó, y cuántos campos.
    """
    modelo = modelo_de(nuevo) or modelo_de(anterior)
    anterior = anterior or {}
    nuevo = nuevo or {}

    columnas = [c for c in list(nuevo) + [c for c in anterior if c not in nuevo]
                if c not in RUIDO]

    filas = []
    for columna in columnas:
        habia = columna in anterior
        hay = columna in nuevo
        if not habia:
            cambio = 'alta'
        elif not hay:
            cambio = 'baja'
        elif anterior[columna] != nuevo[columna]:
            cambio = 'cambio'
        else:
            cambio = 'igual'
        filas.append(Fila(
            etiqueta_de(modelo, columna),
            valor_legible(modelo, columna, anterior.get(columna)),
            valor_legible(modelo, columna, nuevo.get(columna)),
            cambio))

    # Lo que cambió, arriba. Si hay que desplazarse para encontrarlo, la
    # pantalla no sirve.
    filas.sort(key=lambda f: (f.cambio == 'igual', f.etiqueta))

    tocados = sum(1 for f in filas if f.cambio == 'cambio')
    if not anterior:
        resumen = 'Se dio de alta el registro.'
    elif not nuevo:
        resumen = 'Se eliminó el registro.'
    elif tocados == 0:
        resumen = 'Se guardó sin cambios en los datos.'
    elif tocados == 1:
        resumen = 'Cambió 1 campo.'
    else:
        resumen = 'Cambiaron %d campos.' % tocados

    return filas, resumen
