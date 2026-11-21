/* Cafeto · permisos: la ficha de un permiso y los perfiles que lo tienen. */
(function () {
    'use strict';
    function texto(id, valor) { document.getElementById(id).textContent = valor || '—'; }
    document.addEventListener('click', function (evento) {
        var boton = evento.target.closest('.btn-detalle');
        if (!boton) { return; }
        var t = boton.closest('.tarjeta');
        ['id', 'codigo', 'nombre', 'descripcion'].forEach(function (c) { texto('det_' + c, t.dataset[c]); });
        texto('det_modulo', t.dataset.moduloTexto);
        texto('det_modificado', t.dataset.modificado);
        texto('det_modificado_por', t.dataset.modificadoPor);
        var destino = document.getElementById('det_perfiles');
        destino.textContent = '';
        var perfiles = (t.dataset.perfiles || '').split(', ').filter(Boolean);
        if (!perfiles.length) { destino.textContent = 'Ningún perfil'; }
        perfiles.forEach(function (nombre) {
            var chip = document.createElement('span');
            chip.className = 'insignia insignia-neutra me-1 mb-1';
            chip.textContent = nombre;
            destino.appendChild(chip);
        });
        bootstrap.Modal.getOrCreateInstance(document.getElementById('modalDetalle')).show();
    });
})();
