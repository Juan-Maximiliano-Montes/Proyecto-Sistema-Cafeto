/* Cafeto · botón «Imprimir» sin JavaScript en línea: cualquier elemento
   con data-imprimir abre el diálogo de impresión. */
(function () {
    'use strict';
    document.addEventListener('click', function (evento) {
        if (evento.target.closest('[data-imprimir]')) { window.print(); }
    });
})();
