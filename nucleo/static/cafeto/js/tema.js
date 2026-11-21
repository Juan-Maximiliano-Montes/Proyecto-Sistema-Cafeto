/* Cafeto · tema guardado, antes de pintar la página. */
(function () {
    'use strict';
    var raiz = document.documentElement;
    raiz.classList.add('js');
    try {
        var tema = window.localStorage.getItem('cafeto.tema');
        if (tema === 'claro' || tema === 'oscuro') {
            raiz.setAttribute('data-tema', tema);
            raiz.setAttribute('data-bs-theme', tema === 'claro' ? 'light' : 'dark');
            var meta = document.querySelector('meta[name="theme-color"]');
            if (meta) { meta.setAttribute('content', tema === 'claro' ? '#F4F1EA' : '#121128'); }
        }
    } catch (e) { /* sin almacenamiento: queda el oscuro */ }
})();
