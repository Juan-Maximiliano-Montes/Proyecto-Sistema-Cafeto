"""Las reglas que protegen cada fila, escritas en Python.

Son las invariantes que valen para CUALQUIER escritura, venga de donde
venga: el sello de modificación, la prohibición del borrado físico, los
libros de sólo agregado y los saldos derivados. Se implementan como
**mixins de modelo** y no como señales (``pre_save``, ``pre_delete``) por
dos motivos:

* una señal está lejos del modelo que gobierna, y al leer el modelo no se
  ve qué le pasa al guardarlo. Un mixin se lee en la línea del ``class``;
* el orden de ejecución de varias señales sobre el mismo modelo depende
  del orden de importación, que es un detalle frágil. La herencia tiene un
  orden explícito (el MRO) que se lee en el código.

Lo que hay que tener presente
-----------------------------
Estos mixins protegen el MODELO: corren cuando la escritura pasa por el
ORM. Una escritura hecha a mano desde ``psql`` o Adminer los saltea, así
que la base conserva sus CHECK y sus UNIQUE como última red (ver
``nucleo/restricciones.py``).

Por eso cada regla se cubre en los dos caminos que el ORM ofrece: el de la
instancia (``save()`` / ``delete()``) y el del conjunto
(``QuerySet.update()`` / ``QuerySet.delete()``), que NO llama a los
métodos de la instancia. Olvidarse del segundo es el error clásico al
mover lógica de la base al ORM, y deja un agujero por el que pasa
``Modelo.objects.filter(...).delete()``.
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from nucleo import contexto


# ===========================================================================
#  T01 · SELLO DE MODIFICACIÓN (DD-17)
# ===========================================================================
class ConSelloDeModificacion(models.Model):
    """Anota cuándo y quién tocó la fila por última vez.

    Corre en cada UPDATE leyendo el usuario del contexto del pedido; acá se
    hace en ``save()`` leyendo el contexto del pedido.

    En el ALTA el sello queda NULO, igual que antes: una fila recién creada
    no fue "modificada" por nadie. Lo que registra el alta es la auditoría.
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        # Mover un saldo (el stock que baja con una venta) no es modificar
        # la fila: el sello queda para los cambios que hace una persona.
        if getattr(self, '_saldo_autorizado', False):
            return super().save(*args, **kwargs)
        if self.pk is not None and not self._state.adding:
            self.fecha_modificacion = timezone.now()
            usuario = contexto.usuario_actual()
            if usuario is not None and usuario.pk:
                self.usuario_modificacion_id = usuario.pk

            campos = kwargs.get('update_fields')
            if campos is not None:
                # save(update_fields=[...]) escribe SÓLO esas columnas. Sin
                # agregar las del sello, el UPDATE las dejaría sin tocar y
                # la fila mentiría sobre su última modificación.
                campos = set(campos) | {'fecha_modificacion'}
                if usuario is not None and usuario.pk:
                    campos.add('usuario_modificacion')
                kwargs['update_fields'] = tuple(campos)

        return super().save(*args, **kwargs)


# ===========================================================================
#  T15 · LA BAJA ES LÓGICA (RN-8.1.1)
# ===========================================================================
class SinBajaFisica(models.Model):
    """Prohíbe el DELETE: en estas tablas la baja es ``borrado_logico``.

    Reemplaza a ``fn_sin_baja_fisica``. Una venta borrada se lleva puesto
    el arqueo del día y la trazabilidad del stock; por eso nada se borra.

    La excepción es el pasaje a histórico, que copia la fila y recién
    entonces la saca del libro vivo.
    """

    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        if not contexto.en_pasaje_historico():
            raise ValidationError(
                'En %s la baja es lógica: marcá "borrado_logico" en vez de '
                'borrar la fila (RN-8.1.1).' % self._meta.verbose_name_plural)
        return super().delete(*args, **kwargs)


class QuerySetConSello(models.QuerySet):
    """``update()`` también sella (DD-17).

    ``QuerySet.update()`` emite un UPDATE directo y NO pasa por ``save()``,
    así que sin esto una modificación masiva dejaría la fila mintiendo
    sobre cuándo y quién la tocó. Es el mismo agujero que el del DELETE,
    por el otro camino.
    """

    def update(self, **kwargs):
        campos = {f.name for f in self.model._meta.fields}
        if 'fecha_modificacion' in campos:
            kwargs.setdefault('fecha_modificacion', timezone.now())
        usuario = contexto.usuario_actual()
        if usuario is not None and usuario.pk and 'usuario_modificacion' in campos:
            kwargs.setdefault('usuario_modificacion_id', usuario.pk)
        return super().update(**kwargs)


class ManagerSinBajaFisica(models.Manager):
    """El mismo corte, para ``Modelo.objects.filter(...).delete()``.

    Hace falta aparte porque ``QuerySet.delete()`` borra con un solo DELETE
    masivo y NO llama al ``delete()`` de cada instancia.
    """

    def get_queryset(self):
        return QuerySetSinBajaFisica(self.model, using=self._db)


class QuerySetSinBajaFisica(QuerySetConSello):
    """Las maestras: no se borran, y al modificarse quedan selladas."""

    def delete(self):
        if not contexto.en_pasaje_historico():
            raise ValidationError(
                'En %s la baja es lógica: marcá "borrado_logico" en vez de '
                'borrar las filas (RN-8.1.1).'
                % self.model._meta.verbose_name_plural)
        return super().delete()


# ===========================================================================
#  T03 · LIBROS APPEND-ONLY (DD-04)
# ===========================================================================
class LibroInmutable(models.Model):
    """Un asiento no se corrige: se contra-asienta.

    Reemplaza a ``fn_libro_inmutable``. Vale para
    ``movimientos_caja``, ``movimientos_stock``, ``movimientos_puntos``,
    ``log_auditoria``, ``intentos_acceso`` y ``detalles_venta``.

    Es la regla que hace que la auditoría sirva de algo: si una fila se
    puede editar, no prueba nada. Acá se corta tanto el UPDATE como el
    DELETE, en la instancia y en el conjunto.
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ValidationError(
                '%s es un libro de sólo agregado: un asiento no se corrige, '
                'se contra-asienta (DD-04).'
                % self._meta.verbose_name_plural)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not contexto.en_pasaje_historico():
            raise ValidationError(
                '%s es un libro de sólo agregado: un asiento no se borra '
                '(DD-04).' % self._meta.verbose_name_plural)
        return super().delete(*args, **kwargs)


class QuerySetDeLibro(models.QuerySet):

    def update(self, **kwargs):
        raise ValidationError(
            '%s es un libro de sólo agregado: no admite UPDATE (DD-04).'
            % self.model._meta.verbose_name_plural)

    def delete(self):
        if not contexto.en_pasaje_historico():
            raise ValidationError(
                '%s es un libro de sólo agregado: no admite DELETE (DD-04).'
                % self.model._meta.verbose_name_plural)
        return super().delete()


class ManagerDeLibro(models.Manager):

    def get_queryset(self):
        return QuerySetDeLibro(self.model, using=self._db)


# ===========================================================================
#  T04 · SALDOS DERIVADOS (DD-04)
# ===========================================================================
class ConSaldosDerivados(models.Model):
    """Columnas que NO se escriben a mano: las mueve el libro.

    Reemplaza a ``fn_saldos_nacen_en_cero`` y ``fn_protege_derivados``.
    ``stock_actual``, ``puntos_acumulados`` y compañía son la suma de un
    libro de movimientos. Si una pantalla pudiera escribirlos, el saldo y
    el libro contarían historias distintas y no habría forma de saber cuál
    miente.

    Las subclases declaran ``SALDOS_DERIVADOS`` como ``{campo: valor con el
    que nace}``. No siempre es cero: ``compras.estado_compra`` también es
    derivado --sale de sus egresos (DD-22)-- y nace en ``'PENDIENTE'``.
    Forzarlo a 0 rompía ``ck_compras_estado``, que es justamente lo que
    pasó al escribir esto de la forma fácil.

    En la modificación los derivados ni se escriben: se excluyen del
    UPDATE y la instancia vuelve con el valor de la base. El servicio que
    SÍ puede moverlos lo hace con ``actualizar_saldo_derivado()``, que es
    explícito y se puede buscar.
    """

    SALDOS_DERIVADOS = {}

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self._state.adding:
            for campo, inicial in self.SALDOS_DERIVADOS.items():
                setattr(self, campo, inicial)
        elif not getattr(self, '_saldo_autorizado', False):
            # Una edición común NUNCA escribe los saldos: se los saca del
            # UPDATE. Así una pantalla abierta desde antes de la última
            # venta no pisa el stock con el valor viejo que tenía cargado.
            derivados = set(self.SALDOS_DERIVADOS)
            campos = kwargs.get('update_fields')
            if campos is None:
                campos = [c.name for c in self._meta.concrete_fields
                          if not c.primary_key and c.name not in derivados
                          and not c.generated]
            else:
                campos = [c for c in campos if c not in derivados]
            kwargs['update_fields'] = campos
            actuales = type(self).objects.filter(pk=self.pk).values(
                *derivados).first()
            if actuales:
                for campo, valor in actuales.items():
                    setattr(self, campo, valor)
        return super().save(*args, **kwargs)

    def actualizar_saldo_derivado(self, **saldos):
        """La única puerta por la que un saldo derivado cambia.

        La usan los servicios que registran un movimiento, dentro de la
        misma transacción que escribe el asiento.
        """
        for campo, valor in saldos.items():
            if campo not in self.SALDOS_DERIVADOS:
                raise ValueError('%s no es un saldo derivado de %s'
                                 % (campo, type(self).__name__))
            setattr(self, campo, valor)
        self._saldo_autorizado = True
        try:
            self.save(update_fields=list(saldos))
        finally:
            self._saldo_autorizado = False
