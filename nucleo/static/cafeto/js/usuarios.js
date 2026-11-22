(function () {
    'use strict';
    function tarjeta(boton) { return boton.closest('.tarjeta'); }
    function abrir(id) { bootstrap.Modal.getOrCreateInstance(document.getElementById(id)).show(); }
    function texto(id, valor) { var nodo = document.getElementById(id); if (nodo) { nodo.textContent = valor || '—'; } }
    function insignia(clase, contenido) {
        var span = document.createElement('span');
        span.className = 'insignia ' + clase + ' me-1';
        span.textContent = contenido;
        return span;
    }
    // Deja elegir cualquier perfil menos el que ya tiene.
    function marcarPerfilActual(form, actual) {
        form.querySelectorAll('select option').forEach(function (opcion) {
            opcion.disabled = opcion.textContent.trim() === actual;
        });
    }

    // ¿El DNI ya existe? Se pregunta al servidor mientras se tipea.
    var dni = document.getElementById('id_dni_usuario');
    if (dni) {
        var espera = null;
        dni.addEventListener('input', function () {
            var valor = dni.value.replace(/\D/g, '');
            var aviso = document.getElementById('avisoDni');
            var guardar = document.getElementById('btnGuardarCrear');
            clearTimeout(espera);
            if (valor.length < 7) { aviso.classList.add('d-none'); guardar.disabled = false; return; }
            espera = setTimeout(function () {
                fetch(aviso.dataset.verificar + '?dni=' + encodeURIComponent(valor),
                      {headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'})
                    .then(function (r) { return r.json(); })
                    .then(function (r) {
                        aviso.classList.toggle('d-none', !r.existe);
                        guardar.disabled = !!r.existe;
                    })
                    .catch(function () { /* sin red: decide el servidor al guardar */ });
            }, 300);
        });
    }

    document.addEventListener('click', function (evento) {
        var boton;
        if ((boton = evento.target.closest('.btn-detalle'))) {
            fetch(boton.dataset.url, {headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'})
                .then(function (r) { return r.json(); })
                .then(function (u) {
                    texto('det_dni', u.dni_usuario);
                    texto('det_nombre', u.nombre_completo);
                    texto('det_correo', u.correo);
                    texto('det_usuario', u.nombre_usuario);
                    texto('det_perfil', u.perfil);
                    texto('det_fecha_mod', u.fecha_ultima_modificacion);
                    texto('det_fecha_baja', u.fecha_baja);
                    var estado = document.getElementById('det_estado');
                    estado.textContent = '';
                    estado.appendChild(u.borrado_logico ? insignia('insignia-neutra', 'Inactivo') : insignia('insignia-exito', 'Activo'));
                    if (u.debe_cambiar_clave) { estado.appendChild(insignia('insignia-aviso', 'Clave provisoria')); }
                    if (u.es_ultimo_administrador) { estado.appendChild(insignia('insignia-marca', 'Único administrador activo')); }
                    abrir('modalDetalle');
                });
        } else if ((boton = evento.target.closest('.btn-editar'))) {
            var t = tarjeta(boton);
            document.getElementById('formEditar').action = boton.dataset.url;
            texto('edit_dni_txt', t.dataset.dni);
            texto('edit_usuario_txt', t.dataset.usuario);
            texto('edit_nombre_txt', t.dataset.nombre);
            document.getElementById('edit_correo').value = t.dataset.correo;
            abrir('modalEditar');
        } else if ((boton = evento.target.closest('.btn-perfil'))) {
            var tp = tarjeta(boton);
            var formPerfil = document.getElementById('formPerfil');
            formPerfil.action = boton.dataset.url;
            texto('perfil_usuario_txt', tp.dataset.nombre + ' (hoy: ' + tp.dataset.perfil + ')');
            marcarPerfilActual(formPerfil, tp.dataset.perfil);
            formPerfil.querySelector('select').value = '';
            abrir('modalPerfil');
        } else if ((boton = evento.target.closest('.btn-temporal'))) {
            var tt = tarjeta(boton);
            var formTemporal = document.getElementById('formTemporal');
            formTemporal.action = boton.dataset.url;
            texto('temporal_usuario_txt', tt.dataset.nombre);
            marcarPerfilActual(formTemporal, tt.dataset.perfil);
            abrir('modalTemporal');
        }
    });
})();
