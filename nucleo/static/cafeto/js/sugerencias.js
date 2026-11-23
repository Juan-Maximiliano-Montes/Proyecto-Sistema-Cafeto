/* Cafeto · Sugerencias de mejora: aplicar varias de una vez.

   La casilla por tarjeta y el botón «Aplicar seleccionadas» andan sin este
   archivo (son HTML del formulario). Acá se suman las tres cosas que sólo
   el navegador puede dar: tildar o destildar todas las que se ven de un
   clic, el conteo en vivo y —lo importante— que la confirmación diga
   CUÁNTAS se van a aplicar, porque «aplicar las que marqué» sin saber
   cuántas son es exactamente el clic a ciegas que esta pantalla evita. */
(function () {
    'use strict';

    var form = document.querySelector('form.js-masiva');
    if (!form) { return; }

    var todas = form.querySelector('.js-todas');
    var boton = form.querySelector('.js-aplicar-marcadas');
    var cuenta = form.querySelector('.js-cuenta');
    var cola = form.dataset.cola || '';

    function marcas() {
        return Array.prototype.slice.call(form.querySelectorAll('.js-marca'));
    }

    function marcadas() {
        return marcas().filter(function (casilla) { return casilla.checked; });
    }

    function alCambiar() {
        var elegidas = marcadas().length;
        var disponibles = marcas().length;
        boton.disabled = elegidas === 0;
        cuenta.textContent = elegidas === 0
            ? 'Ninguna marcada'
            : (elegidas === 1 ? '1 marcada' : elegidas + ' marcadas');
        if (todas) {
            todas.checked = disponibles > 0 && elegidas === disponibles;
            todas.indeterminate = elegidas > 0 && elegidas < disponibles;
        }
        //  La pregunta se arma acá y no en el template: el modal de
        //  confirmación lee data-confirmar en el momento de enviar.
        form.dataset.confirmar = (elegidas === 1
            ? 'Se aplica 1 sugerencia. '
            : 'Se aplican ' + elegidas + ' sugerencias, una por una. ') + cola;
    }

    form.addEventListener('change', function (evento) {
        if (evento.target === todas) {
            marcas().forEach(function (casilla) { casilla.checked = todas.checked; });
        }
        if (evento.target === todas || evento.target.classList.contains('js-marca')) {
            alCambiar();
        }
    });

    alCambiar();
}());
