(function () {
    'use strict';
    var $ = function (id) { return document.getElementById(id); };
    var rafaga = $('rafaga'), escaner = $('escaner'), aviso = $('rafagaAviso');
    var CLAVE = 'cafeto.conteo.sumar';
    var casilla = $('sumarRepetidos');
    try { casilla.checked = window.localStorage.getItem(CLAVE) !== '0'; } catch (e) { /* nada */ }
    function reflejarSuma() {
        var valor = casilla.checked ? '1' : '';
        $('rafagaSumar').value = valor;
        document.querySelectorAll('.js-sumar').forEach(function (campo) { campo.value = valor; });
    }
    casilla.addEventListener('change', function () {
        try { window.localStorage.setItem(CLAVE, casilla.checked ? '1' : '0'); } catch (e) { /* nada */ }
        reflejarSuma();
    });
    reflejarSuma();

    function avisar(texto) { aviso.textContent = texto; aviso.classList.toggle('d-none', !texto); }
    function limpiar() { $('rafagaEncontrado').classList.add('d-none'); $('rafagaProducto').value = ''; }

    function sugerirDesdeInternet(codigo) {
        if (!window.AbortController || !/^[0-9]{8,14}$/.test(codigo)) { return; }
        var corte = new AbortController();
        var reloj = window.setTimeout(function () { corte.abort(); }, 2000);
        fetch('https://world.openfoodfacts.org/api/v2/product/' + codigo + '.json?fields=product_name,brands,quantity',
              {signal: corte.signal, credentials: 'omit', referrerPolicy: 'no-referrer'})
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d || d.status !== 1 || !d.product || $('altaCodigo').value !== codigo) { return; }
                var nombre = [d.product.product_name, d.product.quantity].filter(Boolean).join(' ').trim();
                var marca = (d.product.brands || '').split(',')[0].trim();
                if (nombre && !$('altaNombre').value) { $('altaNombre').value = nombre.slice(0, 100); }
                $('altaMarca').value = marca.slice(0, 50);
                if (nombre || marca) {
                    $('altaSugerido').textContent = 'Encontrado en internet' + (marca ? ' (marca ' + marca + ')' : '') + ': revisá que el nombre sea el del envase.';
                    $('altaSugerido').classList.remove('d-none');
                }
            })
            .catch(function () { /* sin internet o sin datos: se carga a mano */ })
            .then(function () { window.clearTimeout(reloj); });
    }

    function ofrecerAlta(codigo) {
        $('altaCodigo').value = codigo;
        document.querySelectorAll('#altaCodigoTexto').forEach(function (e) { e.textContent = codigo; });
        ['altaNombre', 'altaPrecio', 'altaCosto', 'altaContada', 'altaMarca'].forEach(function (id) { if ($(id)) { $(id).value = ''; } });
        if ($('altaSugerido')) { $('altaSugerido').classList.add('d-none'); }
        bootstrap.Modal.getOrCreateInstance($('modalAltaRapida')).show();
        if ($('altaNombre')) { sugerirDesdeInternet(codigo); }
    }
    $('modalAltaRapida').addEventListener('shown.bs.modal', function () { if ($('altaNombre')) { $('altaNombre').focus(); } });
    $('modalAltaRapida').addEventListener('hidden.bs.modal', function () { escaner.value = ''; escaner.focus(); });

    function buscar() {
        var leido = escaner.value.trim();
        if (!leido) { return; }
        avisar(''); limpiar();
        fetch(rafaga.dataset.buscar + '?codigo=' + encodeURIComponent(leido), {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}})
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d.existe) {
                    if (d.valido) { ofrecerAlta(d.codigo); }
                    else { avisar('Eso no parece un código: van sólo números, de 3 a 30. Para buscar por nombre usá el formulario de abajo.'); escaner.select(); }
                    return;
                }
                if (!d.contable) { avisar(d.nombre + ': ' + d.motivo); escaner.select(); return; }
                $('rafagaProducto').value = d.id;
                $('rafagaNombre').textContent = d.nombre;
                $('rafagaUnidad').textContent = d.unidad;
                $('rafagaContada').inputMode = d.entera ? 'numeric' : 'decimal';
                var lote = $('rafagaLote');
                lote.innerHTML = '';
                d.lotes.forEach(function (l) {
                    var o = document.createElement('option');
                    o.value = l.id; o.textContent = l.texto + (l.anotado ? ' · anotado ' + l.anotado : '');
                    lote.appendChild(o);
                });
                $('rafagaBloqueLote').classList.toggle('d-none', !d.por_lote);
                lote.disabled = !d.por_lote;
                if (d.por_lote && !d.lotes.length) { avisar(d.nombre + ' va por lote y no tiene ninguno: anotalo con el formulario de abajo, eligiendo "lote nuevo".'); return; }
                $('rafagaAnotado').textContent = d.anotado ? 'En este conteo ya lleva anotado ' + d.anotado + ' ' + d.unidad + (casilla.checked ? ': lo que pongas se suma.' : ': lo que pongas lo reemplaza.') : '';
                $('rafagaEncontrado').classList.remove('d-none');
                $('rafagaContada').value = ''; $('rafagaContada').focus();
            })
            .catch(function () { avisar('No se pudo buscar: revisá la conexión y probá de nuevo.'); });
    }
    escaner.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); buscar(); } });
    rafaga.addEventListener('submit', function (e) {
        if (!$('rafagaProducto').value) { e.preventDefault(); buscar(); }
        else if (!$('rafagaContada').value.trim()) { e.preventDefault(); $('rafagaContada').focus(); }
    });
})();
