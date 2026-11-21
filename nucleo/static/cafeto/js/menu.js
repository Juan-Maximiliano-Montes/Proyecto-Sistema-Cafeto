/* Cafeto · panel lateral: las áreas abiertas y el desplazamiento, antes de pintar el contenido. */
(function () {
    'use strict';
    var lateral = document.querySelector('.lateral');
    if (!lateral) { return; }
    var CLAVE_AREAS = 'cafeto.menu.abiertos';
    var CLAVE_SCROLL = 'cafeto.menu.scroll';

    function leer(almacen, clave) { try { return window[almacen].getItem(clave); } catch (e) { return null; } }
    function guardar(almacen, clave, valor) { try { window[almacen].setItem(clave, valor); } catch (e) { /* nada */ } }
    function abiertas() {
        try { return JSON.parse(leer('localStorage', CLAVE_AREAS) || '[]'); } catch (e) { return []; }
    }

    var lista = abiertas();
    lateral.querySelectorAll('details.lateral-desplegable').forEach(function (area) {
        if (lista.indexOf(area.dataset.grupo) >= 0) { area.open = true; }
        area.addEventListener('toggle', function () {
            var actuales = abiertas().filter(function (g) { return g !== area.dataset.grupo; });
            if (area.open) { actuales.push(area.dataset.grupo); }
            guardar('localStorage', CLAVE_AREAS, JSON.stringify(actuales));
        });
    });

    var desplazado = parseInt(leer('sessionStorage', CLAVE_SCROLL), 10);
    if (desplazado > 0) { lateral.scrollTop = desplazado; }
    function recordar() { guardar('sessionStorage', CLAVE_SCROLL, String(Math.round(lateral.scrollTop))); }
    var espera = null;
    lateral.addEventListener('scroll', function () {
        clearTimeout(espera);
        espera = setTimeout(recordar, 120);
    }, {passive: true});
    window.addEventListener('pagehide', recordar);
})();
