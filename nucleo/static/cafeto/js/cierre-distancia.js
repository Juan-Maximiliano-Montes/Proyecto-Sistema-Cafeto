/* Cafeto · cierre de caja: pedir a distancia la aprobación de la diferencia (DD-94),
   esperar la respuesta y validar el PIN que dictan. */
(function () {
    if (!document.getElementById('remotaPedir')) { return; }
    var $ = function (id) { return document.getElementById(id); };
    var csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
    var radios = document.querySelectorAll('#formas input[type=radio]');
    var envio = document.querySelector('form [type=submit][data-requiere-red]');
    var remota = {id: null, reloj: null, aprobada: false};
    function forma() { var r = document.querySelector('#formas input:checked'); return r ? r.value : 'ACA'; }
    function mostrar() {
        document.querySelectorAll('[data-forma]').forEach(function (bloque) {
            bloque.classList.toggle('d-none', bloque.dataset.forma !== forma());
        });
        envio.disabled = forma() === 'DISTANCIA' && !remota.aprobada;
    }
    radios.forEach(function (r) { r.addEventListener('change', mostrar); });
    if (!document.querySelector('#formas input:checked') && radios.length) { radios[0].checked = true; }
    var yaAprobada = $('remotaYaAprobada');
    if (yaAprobada) {
        remota = {id: Number(yaAprobada.dataset.id), reloj: null, aprobada: true};
        document.querySelector('[name=remota]').value = remota.id;
        $('remotaPedir').classList.add('d-none');
    }
    mostrar();
    function pintar(texto, tipo, conPin) {
        var caja = $('remota');
        caja.classList.remove('d-none', 'aviso-info', 'aviso-exito', 'aviso-error', 'aviso-aviso');
        caja.classList.add('aviso-' + (tipo || 'info'));
        $('remotaIcono').className = 'bi ' + ({exito: 'bi-check-circle-fill', error: 'bi-x-circle-fill', aviso: 'bi-exclamation-triangle-fill'}[tipo] || 'bi-hourglass-split');
        $('remotaTexto').textContent = texto;
        $('remotaPinGrupo').classList.toggle('d-none', !conPin);
        $('remotaPedir').classList.toggle('d-none', !!conPin);
    }
    function minutos(seg) { var m = Math.floor(seg / 60), s = seg % 60; return m + ':' + ('0' + s).slice(-2); }
    function url(sufijo) { return $('remotaPedir').dataset.estado.replace('/0/', '/' + remota.id + '/') + (sufijo || ''); }
    function enviar(direccion, datos) {
        return fetch(direccion, {method: 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify(datos || {})}).then(function (r) { return r.json(); });
    }
    function seguir(r) {
        if (r.estado === 'PENDIENTE') {
            pintar('Esperando respuesta. Le avisamos por ' + (remota.avisos || 'la bandeja del sistema') + '. Vence en ' + minutos(r.restan) + ' min. Si te dictan el PIN, escribilo acá.', 'info', true);
        } else if (r.estado === 'APROBADA') {
            clearInterval(remota.reloj);
            remota.aprobada = true;
            document.querySelector('[name=remota]').value = remota.id;
            pintar('Aprobó ' + r.autorizo + ' (' + (r.via || '').toLowerCase() + '). Ya podés cerrar.', 'exito', false);
            $('remotaPedir').classList.add('d-none');
            mostrar();
        } else if (r.estado === 'RECHAZADA') {
            clearInterval(remota.reloj); remota.id = null;
            pintar('Rechazaron el pedido. Volvé a contar con quien supervise o dejalo pendiente de revisión.', 'error', false);
        } else if (r.estado === 'ANULADA') {
            clearInterval(remota.reloj); remota.id = null;
            pintar('El pedido venció sin respuesta. Podés pedirlo de nuevo.', 'aviso', false);
        }
    }
    function preguntar() {
        if (!remota.id) { return; }
        fetch(url(), {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}})
            .then(function (r) { return r.json(); })
            .then(function (r) { if (r.ok && remota.id) { seguir(r); } })
            .catch(function () {});
    }
    $('remotaPedir').addEventListener('click', function () {
        var boton = this;
        boton.disabled = true;
        var modo = document.querySelector('#modos input[name=modo]:checked');
        enviar(boton.dataset.url, {modo: modo ? modo.value : ''}).then(function (r) {
            boton.disabled = false;
            if (!r.ok) { pintar(r.error, 'error', false); return; }
            remota = {id: r.id, reloj: null, aprobada: false, avisos: (r.avisos || []).join(' y ') || 'la bandeja del sistema'};
            seguir(r);
            remota.reloj = setInterval(preguntar, 2000);
            $('remotaPin').value = ''; $('remotaPin').focus();
        }).catch(function () { boton.disabled = false; pintar('No se pudo pedir la autorización. Revisá la conexión.', 'error', false); });
    });
    $('remotaPinOk').addEventListener('click', function () {
        if (!remota.id) { return; }
        enviar(url('pin/'), {pin: $('remotaPin').value}).then(function (r) {
            if (!r.ok) { pintar(r.error, 'error', r.estado === 'PENDIENTE'); return; }
            seguir(r);
        }).catch(function () { pintar('No se pudo validar el PIN. Revisá la conexión.', 'error', true); });
    });
    $('remotaPin').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); $('remotaPinOk').click(); } });
})();
