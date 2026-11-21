/* Cafeto · imprimir sin JavaScript en línea: cualquier elemento con
   data-imprimir abre el diálogo; <body data-autoimprimir> lo abre al cargar. */
(function () {
    'use strict';
    document.addEventListener('click', function (evento) {
        if (evento.target.closest('[data-imprimir]')) { window.print(); }
    });
    if (document.body && document.body.hasAttribute('data-autoimprimir')) {
        window.addEventListener('load', function () {
            try { window.print(); } catch (e) { /* sin diálogo de impresión */ }
        });
    }
})();
