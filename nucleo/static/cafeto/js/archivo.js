/* Cafeto · archivo histórico: el año y las fechas van juntos (DD-101).
   Elegir un año llena «desde» y «hasta» con ese año; tocar una fecha a mano
   deja el año en «Fechas a elección». Así lo que se consulta o se baja es
   siempre lo que se ve en el formulario. */
(function () {
    'use strict';
    var anio = document.getElementById('archivoAnio');
    var desde = document.getElementById('archivoDesde');
    var hasta = document.getElementById('archivoHasta');
    if (!anio || !desde || !hasta) { return; }
    anio.addEventListener('change', function () {
        if (!anio.value) { return; }
        var hoy = hasta.max || new Date().toISOString().slice(0, 10);
        desde.value = anio.value + '-01-01';
        var fin = anio.value + '-12-31';
        hasta.value = fin > hoy ? hoy : fin;
    });
    [desde, hasta].forEach(function (campo) {
        campo.addEventListener('change', function () {
            var mismo = desde.value.slice(0, 4) === hasta.value.slice(0, 4) &&
                        desde.value.slice(5) === '01-01' && anio.value === desde.value.slice(0, 4);
            if (!mismo) { anio.value = ''; }
        });
    });
})();
