(function () {
    'use strict';
    function abrir(id) { bootstrap.Modal.getOrCreateInstance(document.getElementById(id)).show(); }
    function texto(id, valor) { document.getElementById(id).textContent = valor || '—'; }

    // En la edición: los permisos de Seguridad sólo se muestran (y se
    // mandan) para el perfil del sistema; el tope, sólo para los demás.
    function ajustarAlTipo(esSistema) {
        var tope = document.getElementById('bloque_tope');
        if (tope) {
            tope.classList.toggle('d-none', esSistema);
            document.getElementById('edit_tope').disabled = esSistema;
        }
        document.querySelectorAll('#formEditar .permiso-exclusivo').forEach(function (bloque) {
            bloque.classList.toggle('d-none', !esSistema);
            bloque.querySelectorAll('input').forEach(function (i) { i.disabled = !esSistema; });
        });
    }

    document.addEventListener('click', function (evento) {
        var boton, t;
        if ((boton = evento.target.closest('.btn-detalle'))) {
            t = boton.closest('.tarjeta');
            texto('det_id', t.dataset.id);
            texto('det_nombre', t.dataset.nombre);
            texto('det_descripcion', t.dataset.descripcion);
            var destino = document.getElementById('det_permisos');
            destino.textContent = '';
            var codigos = (t.dataset.permisosTexto || '').split(', ').filter(Boolean);
            if (t.dataset.sistema === '1') { codigos = ['acceso total']; }
            if (!codigos.length) { destino.textContent = 'Sin permisos'; }
            codigos.forEach(function (codigo) {
                var chip = document.createElement('span');
                chip.className = 'insignia ' + (t.dataset.sistema === '1' ? 'insignia-marca' : 'insignia-neutra') + ' me-1 mb-1';
                chip.textContent = codigo;
                destino.appendChild(chip);
            });
            texto('det_usuarios', t.dataset.usuarios);
            texto('det_modificado', t.dataset.modificado);
            texto('det_modificado_por', t.dataset.modificadoPor);
            abrir('modalDetalle');
        } else if ((boton = evento.target.closest('.btn-editar'))) {
            t = boton.closest('.tarjeta');
            var form = document.getElementById('formEditar');
            form.action = boton.dataset.url;
            document.getElementById('edit_nombre').value = t.dataset.nombre;
            document.getElementById('edit_descripcion').value = t.dataset.descripcion;
            var asignados = (t.dataset.permisos || '').split(',');
            form.querySelectorAll('.chk-permiso-editar').forEach(function (casilla) {
                casilla.checked = asignados.indexOf(casilla.value) !== -1;
            });
            document.getElementById('edit_tope').value = t.dataset.tope;
            ajustarAlTipo(t.dataset.sistema === '1');
            abrir('modalEditar');
        }
    });
    // Al cargar la página. Si el modal se reabre porque el guardado falló
    // (data-modal-auto), el servidor ya dejó puesto el nombre, el tope y los
    // tildes, y dice en data-es-sistema de qué perfil se trata: sin eso se
    // llamaba siempre con `false` y el bloque de Seguridad del perfil
    // administrador quedaba escondido y DESHABILITADO, así que al corregir
    // el error se mandaba el formulario sin sus permisos exclusivos.
    var formEditar = document.getElementById('formEditar');
    if (formEditar) { ajustarAlTipo(formEditar.dataset.esSistema === '1'); }
})();
