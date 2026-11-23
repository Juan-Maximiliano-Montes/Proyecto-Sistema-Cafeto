/* Cafeto · Registro de Actividad: el modal del ojo a pantalla completa.

   El detalle de un asiento no siempre es una fila de quince campos: la foto
   de una venta trae sus renglones y sus pagos, y las dos fotos (antes y
   después) van lado a lado. En un modal de 800 px eso se lee corriendo dos
   barras horizontales —la de la tabla grande y la de cada subtabla—, que es
   justamente lo que hace imposible comparar un antes con un después.

   El botón alterna `.modal-fullscreen`: el ancho de la pantalla, las dos
   columnas enteras y, casi siempre, ninguna barra horizontal. La elección
   se recuerda mientras dure la pestaña, porque quien revisa el registro
   abre veinte asientos seguidos y no quiere tocar el botón veinte veces. */
(function () {
    'use strict';

    var CLAVE = 'cafeto:actividad:pantalla-completa';
    var modal = document.getElementById('modalActividad');
    if (!modal) { return; }
    var dialogo = modal.querySelector('.modal-dialog');
    var boton = modal.querySelector('.js-pantalla-completa');
    if (!dialogo || !boton) { return; }

    function recordado() {
        try { return sessionStorage.getItem(CLAVE) === '1'; } catch (e) { return false; }
    }

    function recordar(completa) {
        try { sessionStorage.setItem(CLAVE, completa ? '1' : '0'); } catch (e) { /* modo privado */ }
    }

    function aplicar(completa) {
        dialogo.classList.toggle('modal-fullscreen', completa);
        dialogo.classList.toggle('modal-lg', !completa);
        boton.setAttribute('aria-pressed', completa ? 'true' : 'false');
        var etiqueta = completa ? 'Volver al tamaño normal' : 'Ver en pantalla completa';
        boton.title = etiqueta;
        boton.setAttribute('aria-label', etiqueta);
        boton.innerHTML = '<i class="bi bi-' +
            (completa ? 'fullscreen-exit' : 'arrows-fullscreen') + '" aria-hidden="true"></i>';
    }

    boton.addEventListener('click', function () {
        var completa = !dialogo.classList.contains('modal-fullscreen');
        aplicar(completa);
        recordar(completa);
    });

    modal.addEventListener('show.bs.modal', function () { aplicar(recordado()); });

    aplicar(recordado());
}());
