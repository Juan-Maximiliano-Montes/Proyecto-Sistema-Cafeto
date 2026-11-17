/* Cafeto · cierre de caja: elegir qué se hace con el turno (DD-101).
   Muestra «Quién sigue con el cajón» sólo en el cambio de turno y cambia el
   texto del botón y de la confirmación según la opción. */
(function () {
    'use strict';
    var opciones = document.querySelectorAll('#modos input[name=modo]');
    if (!opciones.length) { return; }
    var form = opciones[0].form;
    var boton = form.querySelector('button[type=submit] .js-texto-boton');
    function aplicar() {
        var elegida = form.querySelector('#modos input[name=modo]:checked') || opciones[0];
        elegida.checked = true;
        form.querySelectorAll('[data-modo]').forEach(function (bloque) {
            bloque.hidden = bloque.dataset.modo !== elegida.value;
        });
        if (boton) { boton.textContent = elegida.dataset.boton; }
        form.dataset.titulo = elegida.dataset.titulo;
        form.dataset.aceptar = elegida.dataset.aceptar;
        form.dataset.confirmar = elegida.dataset.confirmar;
    }
    opciones.forEach(function (opcion) { opcion.addEventListener('change', aplicar); });
    aplicar();
})();
