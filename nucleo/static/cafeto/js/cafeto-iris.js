/* ==========================================================================
   Cafeto × iRiS · Comportamiento del Shell global
   --------------------------------------------------------------------------
   100% compatible con CSP estricta (script-src 'self', 0 inline, 0 on*=),
   HTMX (hx-boost), cafeto.js, menu.js, tablero.js y vender.js.

   Responsabilidades:
     1. Puente de mensajes Django (success/warning/info) hacia el Notch
        central con timer de altura completa, pausa al hover, expansión
        vertical en textos largos + botón Entendido, y sonido Web Audio
        offline silenciable. Los errores críticos (.aviso-error) NUNCA van
        al Notch: quedan fijos inline en la pantalla.
     2. Detección automática de desborde en sub-pestañas soldadas
        (ResizeObserver) -> Selector táctil + Bottom Sheet (<dialog>).
     3. Modal F1 de atajos y paleta Ctrl+K de búsqueda rápida de pantallas
        (leyendo las pantallas reales de .lateral filtradas por permisos).
     4. Sincronización de tema entre pestañas, botón de tema en móvil/tablet,
        accesibilidad del drawer lateral (inert + Esc + aria-expanded) y
        fallback de light-dismiss para <dialog closedby="any">.
   ========================================================================== */
(function () {
    'use strict';

    var CLAVE_SONIDO = 'cafeto.iris.sonido';
    var CLAVE_RAIL_MODO = 'cafeto.iris.railModo';
    var CLAVE_RAIL_COLAPSADO = 'cafeto.iris.railColapsado';
    var CLAVE_AUTH_VISTO = 'cafeto.iris.authUltimoId';
    var CLAVE_AUTH_SYNC = 'cafeto.iris.authSync';
    var CLAVE_AVISO_ACTIVO = 'cafeto.iris.aviso.v1';
    var INTERVALO_AUTH_MS = 8000;

    function leer(clave) {
        try { return window.localStorage.getItem(clave); } catch (e) { return null; }
    }

    function guardar(clave, valor) {
        try { window.localStorage.setItem(clave, valor); } catch (e) { /* modo privado */ }
    }

    function leerSesion(clave) {
        try { return window.sessionStorage.getItem(clave); } catch (e) { return null; }
    }

    function guardarSesion(clave, valor) {
        try { window.sessionStorage.setItem(clave, valor); } catch (e) { /* modo privado */ }
    }

    function modoRailActual() {
        return leer(CLAVE_RAIL_MODO) === 'areas' ? 'areas' : 'completo';
    }

    function railColapsadoActual() {
        return leer(CLAVE_RAIL_COLAPSADO) === '1';
    }

    function aplicarModoRail(modo) {
        var valor = modo === 'areas' ? 'areas' : 'completo';
        document.documentElement.setAttribute('data-iris-rail', valor);
        document.querySelectorAll('[data-iris-rail-modo]').forEach(function (btn) {
            btn.setAttribute('aria-pressed', valor === 'areas' ? 'true' : 'false');
        });
    }

    function aplicarColapsoRail(colapsado) {
        var activo = Boolean(colapsado);
        document.documentElement.setAttribute('data-iris-rail-colapsado', activo ? '1' : '0');
        document.querySelectorAll('[data-iris-rail-colapsar]').forEach(function (btn) {
            btn.setAttribute('aria-pressed', activo ? 'true' : 'false');
            var ic = btn.querySelector('i');
            if (ic) {
                ic.className = activo ? 'bi bi-layout-sidebar' : 'bi bi-layout-sidebar-inset';
            }
        });
    }

    // Aplicar de inmediato para evitar parpadeo al cargar (desplegado por defecto para adultos)
    aplicarModoRail(modoRailActual());
    aplicarColapsoRail(railColapsadoActual());

    function normalizar(texto) {
        return String(texto || '')
            .toLowerCase()
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .trim();
    }

    function sonidoActivo() {
        return leer(CLAVE_SONIDO) !== '0';
    }

    function actualizarBotonesSonido() {
        var activo = sonidoActivo();
        document.querySelectorAll('[data-iris-sonido-alternar]').forEach(function (btn) {
            btn.setAttribute('aria-pressed', activo ? 'true' : 'false');
            var etiqueta = btn.querySelector('.js-sonido-estado');
            if (etiqueta) {
                etiqueta.textContent = activo ? 'Activado' : 'Silenciado';
            }
            var icono = btn.querySelector('i');
            if (icono) {
                icono.className = activo ? 'bi bi-volume-up' : 'bi bi-volume-mute';
            }
        });
    }

    /* ----------------------------------------------------------------------
       1. Sonido suave offline (Web Audio API · 0 archivos externos)
       ---------------------------------------------------------------------- */
    var audioCtx = null;

    function tocarChime(tono) {
        if (!sonidoActivo()) { return; }
        try {
            var AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) { return; }
            if (!audioCtx) { audioCtx = new AC(); }
            if (audioCtx.state === 'suspended') {
                audioCtx.resume().catch(function () {});
            }
            var ahora = audioCtx.currentTime;
            var notas = tono === 'aviso'
                ? [[659.25, 0, 0.14, 0.06], [880, 0.11, 0.20, 0.065]]
                : [[523.25, 0, 0.11, 0.045], [659.25, 0.09, 0.16, 0.045]];
            notas.forEach(function (n) {
                var osc = audioCtx.createOscillator();
                var gain = audioCtx.createGain();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(n[0], ahora + n[1]);
                gain.gain.setValueAtTime(0.0001, ahora + n[1]);
                gain.gain.exponentialRampToValueAtTime(n[3], ahora + n[1] + 0.018);
                gain.gain.exponentialRampToValueAtTime(0.0001, ahora + n[1] + n[2]);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start(ahora + n[1]);
                osc.stop(ahora + n[1] + n[2] + 0.02);
            });
        } catch (e) { /* el navegador bloqueó audio sin interacción previa */ }
    }

    /* ----------------------------------------------------------------------
       2. Notificaciones unificadas en el Notch central (solo no-errores)
       ---------------------------------------------------------------------- */
    var colaAvisos = [];
    var avisoActual = null;
    var temporizadorAviso = null;
    var anchoMinimoNotch = 0;
    var buscadorNotchAbierto = false;

    function actualizarIndicadorColaBuscador() {
        var dot = document.getElementById('irisNotchColaDot');
        if (!dot) { return; }
        dot.hidden = !(buscadorNotchAbierto && (avisoActual || colaAvisos.length > 0));
    }

    function limpiarTemporizador() {
        if (temporizadorAviso) {
            window.clearTimeout(temporizadorAviso);
            temporizadorAviso = null;
        }
    }

    function obtenerNotch() {
        return {
            notch: document.getElementById('irisNotch'),
            host: document.getElementById('irisNotchAviso')
        };
    }

    function sincronizarClasesNotch(notch, estado) {
        if (!notch) { return; }
        if (!estado) {
            notch.classList.remove('tiene-aviso', 'tiene-aviso-largo');
            notch.style.minWidth = '';
            return;
        }
        var esLargo = Boolean(estado.esLargo);
        notch.classList.add('tiene-aviso');
        notch.classList.toggle('tiene-aviso-largo', esLargo);
        if (!esLargo && window.innerWidth >= 640 && anchoMinimoNotch > 0) {
            notch.style.minWidth = anchoMinimoNotch + 'px';
        } else {
            notch.style.minWidth = '';
        }
    }

    function persistirEstadoAvisos() {
        try {
            if (!avisoActual && colaAvisos.length === 0) {
                window.sessionStorage.removeItem(CLAVE_AVISO_ACTIVO);
                return;
            }
            var paquete = {
                anchoMinimo: anchoMinimoNotch || 0,
                viewportW: window.innerWidth || 0,
                actual: avisoActual ? {
                    texto: avisoActual.texto,
                    tono: avisoActual.tono,
                    opciones: avisoActual.opciones || {},
                    esLargo: avisoActual.esLargo,
                    fijo: avisoActual.fijo,
                    duracionMs: avisoActual.duracionMs,
                    restanteMs: avisoActual.restanteMs,
                    expiraEn: avisoActual.expiraEn || 0,
                    pausado: Boolean(avisoActual.pausado)
                } : null,
                cola: colaAvisos.slice(0, 8)
            };
            window.sessionStorage.setItem(CLAVE_AVISO_ACTIVO, JSON.stringify(paquete));
        } catch (e) { /* storage bloqueado */ }
    }

    function limpiarEstadoAvisosStorage() {
        try { window.sessionStorage.removeItem(CLAVE_AVISO_ACTIVO); } catch (e) { /* nada */ }
    }

    function cerrarAvisoActual() {
        limpiarTemporizador();
        avisoActual = null;
        var partes = obtenerNotch();
        if (partes.notch && partes.host) {
            partes.host.hidden = true;
            partes.host.textContent = '';
            partes.notch.classList.remove('tiene-aviso', 'tiene-aviso-largo');
            partes.notch.style.minWidth = '';
        }
        persistirEstadoAvisos();
        actualizarIndicadorColaBuscador();
        if (colaAvisos.length > 0) {
            mostrarSiguienteAviso();
        }
    }

    function pausarAviso() {
        if (!avisoActual || avisoActual.fijo || avisoActual.pausado) { return; }
        limpiarTemporizador();
        var restante = avisoActual.expiraEn
            ? (avisoActual.expiraEn - Date.now())
            : (avisoActual.restanteMs - (Date.now() - avisoActual.iniciadoEn));
        avisoActual.restanteMs = Math.max(800, restante);
        avisoActual.pausado = true;
        var caja = document.querySelector('#irisNotchAviso .iris-aviso-caja');
        if (caja) { caja.classList.add('pausado'); }
        persistirEstadoAvisos();
    }

    function reanudarAviso() {
        if (!avisoActual || avisoActual.fijo || !avisoActual.pausado) { return; }
        avisoActual.pausado = false;
        avisoActual.iniciadoEn = Date.now();
        avisoActual.expiraEn = avisoActual.iniciadoEn + avisoActual.restanteMs;
        var caja = document.querySelector('#irisNotchAviso .iris-aviso-caja');
        if (caja) { caja.classList.remove('pausado'); }
        persistirEstadoAvisos();
        temporizadorAviso = window.setTimeout(cerrarAvisoActual, avisoActual.restanteMs);
    }

    function pintarAvisoEnNotch(estado, silencioso) {
        var partes = obtenerNotch();
        if (!partes.notch || !partes.host || !estado) { return; }

        var texto = estado.texto;
        var tono = estado.tono || 'exito';
        var opciones = estado.opciones || {};
        var esLargo = Boolean(estado.esLargo);
        var fijo = Boolean(estado.fijo);
        var restante = 0;

        if (!fijo) {
            restante = estado.pausado
                ? Number(estado.restanteMs || 0)
                : Math.max(0, Number(estado.expiraEn || 0) - Date.now());
            if (restante <= 150) {
                cerrarAvisoActual();
                return;
            }
            estado.restanteMs = restante;
            estado.iniciadoEn = Date.now();
            estado.expiraEn = estado.iniciadoEn + restante;
        }

        if (!anchoMinimoNotch && window.innerWidth >= 640 && partes.notch.offsetWidth > 0) {
            anchoMinimoNotch = Math.max(window.innerWidth >= 1100 ? 360 : 260, partes.notch.offsetWidth);
        }

        partes.host.textContent = '';

        var caja = document.createElement('div');
        caja.className = 'iris-aviso-caja' +
            (tono === 'aviso' ? ' tono-aviso' : (tono === 'info' ? ' tono-info' : '')) +
            (esLargo ? ' es-largo' : '') +
            (silencioso ? ' sin-anim-entrada' : '') +
            (estado.pausado ? ' pausado' : '');

        if (!fijo && restante > 0) {
            var barra = document.createElement('span');
            barra.className = 'iris-aviso-timer';
            barra.setAttribute('aria-hidden', 'true');
            var duracionTotal = Math.max(restante, Number(estado.duracionMs || restante));
            var transcurrido = Math.max(0, duracionTotal - restante);
            barra.style.setProperty('--iris-notice-ms', duracionTotal + 'ms');
            if (transcurrido > 0) {
                barra.style.animationDelay = (-transcurrido) + 'ms';
            }
            caja.appendChild(barra);
        }

        var cuerpo = document.createElement('div');
        cuerpo.className = 'iris-aviso-cuerpo';

        var icono = document.createElement('i');
        icono.setAttribute('aria-hidden', 'true');
        icono.className = 'bi iris-aviso-ic ' + (
            tono === 'aviso' ? 'bi-exclamation-triangle-fill' :
            tono === 'info' ? 'bi-info-circle-fill' :
            'bi-check-circle-fill'
        );

        var spanTexto = document.createElement('span');
        spanTexto.className = 'iris-aviso-texto';
        spanTexto.textContent = texto;

        cuerpo.appendChild(icono);
        cuerpo.appendChild(spanTexto);
        caja.appendChild(cuerpo);

        if (esLargo) {
            var pie = document.createElement('div');
            pie.className = 'iris-aviso-pie';

            var pista = document.createElement('span');
            pista.className = 'iris-aviso-pista';
            pista.textContent = fijo ? 'Queda fijo hasta que confirmes' : 'Pasá el mouse para pausar';

            var accionesPie = document.createElement('div');
            accionesPie.className = 'iris-aviso-acciones';

            if (opciones.url) {
                var enlaceAccion = document.createElement('a');
                enlaceAccion.className = 'iris-aviso-entendido';
                enlaceAccion.href = opciones.url;
                enlaceAccion.textContent = opciones.etiquetaAccion || 'Revisar ahora';
                enlaceAccion.addEventListener('click', cerrarAvisoActual);
                accionesPie.appendChild(enlaceAccion);
            }

            var btnEntendido = document.createElement('button');
            btnEntendido.type = 'button';
            btnEntendido.className = opciones.url ? 'iris-aviso-secundario' : 'iris-aviso-entendido';
            btnEntendido.textContent = 'Entendido';
            btnEntendido.addEventListener('click', cerrarAvisoActual);
            accionesPie.appendChild(btnEntendido);

            pie.appendChild(pista);
            pie.appendChild(accionesPie);
            caja.appendChild(pie);
        } else {
            var btnCerrar = document.createElement('button');
            btnCerrar.type = 'button';
            btnCerrar.className = 'iris-aviso-cerrar';
            btnCerrar.setAttribute('aria-label', 'Cerrar aviso');
            btnCerrar.innerHTML = '<i class="bi bi-x" aria-hidden="true"></i>';
            btnCerrar.addEventListener('click', cerrarAvisoActual);
            caja.appendChild(btnCerrar);
        }

        caja.addEventListener('mouseenter', pausarAviso);
        caja.addEventListener('mouseleave', reanudarAviso);

        partes.host.appendChild(caja);
        partes.host.hidden = false;
        sincronizarClasesNotch(partes.notch, estado);

        if (!silencioso) {
            tocarChime(tono);
        }

        limpiarTemporizador();
        if (!fijo && !estado.pausado && restante > 0) {
            temporizadorAviso = window.setTimeout(cerrarAvisoActual, restante);
        }
    }

    function mostrarSiguienteAviso() {
        var partes = obtenerNotch();
        if (!partes.notch || !partes.host || colaAvisos.length === 0) { return; }
        if (buscadorNotchAbierto) {
            persistirEstadoAvisos();
            actualizarIndicadorColaBuscador();
            return;
        }

        var item = colaAvisos.shift();
        var texto = item.texto;
        var tono = item.tono; // 'exito' | 'aviso' | 'info'
        var opciones = item.opciones || {};
        var esLargo = Boolean(opciones.url) || texto.length > 56 || texto.indexOf('\n') >= 0;
        var fijo = esLargo && tono !== 'exito';
        var duracionMs = fijo ? 0 : (esLargo ? 8000 : 5500);
        var ahora = Date.now();

        // Conservar el ancho en reposo del Notch para que un aviso corto nunca lo achique
        if (!avisoActual && window.innerWidth >= 640) {
            anchoMinimoNotch = Math.max(window.innerWidth >= 1100 ? 360 : 260, partes.notch.offsetWidth);
        }

        avisoActual = {
            texto: texto,
            tono: tono,
            opciones: opciones,
            esLargo: esLargo,
            fijo: fijo,
            duracionMs: duracionMs,
            restanteMs: duracionMs,
            iniciadoEn: ahora,
            expiraEn: fijo ? 0 : (ahora + duracionMs),
            pausado: false
        };

        persistirEstadoAvisos();
        pintarAvisoEnNotch(avisoActual, false);
    }

    function restaurarAvisoPersistente() {
        var partes = obtenerNotch();
        if (!partes.notch || !partes.host) {
            if (window.location.pathname === '/login/') {
                limpiarEstadoAvisosStorage();
            }
            return;
        }

        // Caso 1: venimos de un swap HTMX (hx-select-oob="#barra" + hx-preserve en #irisNotchAviso)
        if (avisoActual) {
            if (!avisoActual.fijo && !avisoActual.pausado && Number(avisoActual.expiraEn || 0) - Date.now() <= 150) {
                cerrarAvisoActual();
                return;
            }
            if (avisoActual.pausado && !avisoActual.fijo) {
                var cajaExistente = partes.host.querySelector('.iris-aviso-caja');
                if (!cajaExistente || !cajaExistente.matches(':hover')) {
                    reanudarAviso();
                }
            }
            if (partes.host.hidden || !partes.host.firstElementChild) {
                pintarAvisoEnNotch(avisoActual, true);
            } else {
                sincronizarClasesNotch(partes.notch, avisoActual);
            }
            return;
        }

        // Caso 2: navegación completa o recarga con un aviso todavía vigente en sessionStorage
        var crudo = leerSesion(CLAVE_AVISO_ACTIVO);
        if (!crudo) {
            sincronizarClasesNotch(partes.notch, null);
            partes.host.hidden = true;
            return;
        }
        try {
            var guardado = JSON.parse(crudo);
            if (!guardado) {
                limpiarEstadoAvisosStorage();
                sincronizarClasesNotch(partes.notch, null);
                partes.host.hidden = true;
                return;
            }
            if (!anchoMinimoNotch && guardado.anchoMinimo > 0 && guardado.viewportW &&
                Math.abs((window.innerWidth || 0) - guardado.viewportW) < 40) {
                anchoMinimoNotch = guardado.anchoMinimo;
            }
            if (Array.isArray(guardado.cola) && guardado.cola.length && colaAvisos.length === 0) {
                colaAvisos = guardado.cola;
            }
            var act = guardado.actual;
            if (act && act.texto) {
                if (act.pausado && !act.fijo) {
                    act.pausado = false;
                    act.expiraEn = Date.now() + Math.max(800, Number(act.restanteMs || 2500));
                }
                var vigente = Boolean(act.fijo) || (Number(act.expiraEn || 0) - Date.now() > 150);
                if (vigente) {
                    avisoActual = act;
                    persistirEstadoAvisos();
                    pintarAvisoEnNotch(avisoActual, true);
                    return;
                }
            }
            limpiarEstadoAvisosStorage();
            sincronizarClasesNotch(partes.notch, null);
            partes.host.hidden = true;
            if (colaAvisos.length > 0) {
                mostrarSiguienteAviso();
            }
        } catch (e) {
            limpiarEstadoAvisosStorage();
            sincronizarClasesNotch(partes.notch, null);
            partes.host.hidden = true;
        }
    }

    function encolarAviso(texto, tono, opciones) {
        if (!texto) { return; }
        colaAvisos.push({ texto: texto, tono: tono || 'exito', opciones: opciones || {} });
        if (buscadorNotchAbierto) {
            persistirEstadoAvisos();
            actualizarIndicadorColaBuscador();
            return;
        }
        if (!avisoActual) {
            mostrarSiguienteAviso();
        } else {
            persistirEstadoAvisos();
        }
    }

    /* ----------------------------------------------------------------------
       2b. Notificaciones en vivo de autorizaciones pendientes (polling 8 s)
       ---------------------------------------------------------------------- */
    var timerAuth = null;
    var authEnCurso = false;
    var authPrimeraVez = true;

    function detenerPollingAuth() {
        if (timerAuth) {
            window.clearInterval(timerAuth);
            timerAuth = null;
        }
    }

    function pintarPildoraAuth(cantidad, url) {
        var notch = document.getElementById('irisNotch');
        if (!notch) { return; }
        var reposo = notch.querySelector('.iris-notch-reposo');
        if (!reposo) { return; }
        var n = Math.max(0, Number(cantidad || 0));
        notch.dataset.irisAuthCount = String(n);
        var pill = reposo.querySelector('.iris-auth-pill');

        if (n <= 0) {
            if (pill) { pill.remove(); }
            return;
        }

        var destino = url || notch.dataset.irisAuthUrl || '/ventas/autorizaciones/';
        var etiquetaAria = n + ' pedido' + (n === 1 ? '' : 's') + ' de autorización esperando';
        if (!pill) {
            pill = document.createElement('a');
            pill.className = 'iris-auth-pill';
            pill.title = 'La caja espera una autorización';
            var ic = document.createElement('i');
            ic.className = 'bi bi-phone-vibrate';
            ic.setAttribute('aria-hidden', 'true');
            var spanWrap = document.createElement('span');
            pill.appendChild(ic);
            pill.appendChild(spanWrap);
            reposo.appendChild(pill);
        }
        pill.href = destino;
        pill.setAttribute('aria-label', etiquetaAria);
        var contenedorTxt = pill.querySelector('span');
        if (contenedorTxt) {
            contenedorTxt.textContent = '';
            contenedorTxt.appendChild(document.createTextNode(String(n)));
            var sufijo = document.createElement('span');
            sufijo.className = 'd-none d-md-inline';
            sufijo.textContent = ' por autorizar';
            contenedorTxt.appendChild(sufijo);
        }
    }

    function consultarAutorizaciones() {
        var notch = document.getElementById('irisNotch');
        if (!notch || !notch.dataset.irisAuthPoll) { return; }
        if (authEnCurso || document.visibilityState !== 'visible' || !navigator.onLine) { return; }

        authEnCurso = true;
        var pollUrl = notch.dataset.irisAuthPoll;
        var authUrl = notch.dataset.irisAuthUrl || '/ventas/autorizaciones/';
        var prevCantidad = Number(notch.dataset.irisAuthCount || 0);

        fetch(pollUrl, {
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
            .then(function (r) {
                if (r.status === 401 || r.redirected) {
                    detenerPollingAuth();
                    return null;
                }
                if (!r.ok) { return null; }
                return r.json();
            })
            .then(function (datos) {
                if (!datos || !datos.ok) { return; }
                if (!datos.puede) {
                    detenerPollingAuth();
                    pintarPildoraAuth(0, authUrl);
                    return;
                }

                var cantidad = Number(datos.cantidad || 0);
                var ultimo = datos.ultimo || null;
                var ultimoId = ultimo && ultimo.id ? String(ultimo.id) : '';
                var vistoId = leerSesion(CLAVE_AUTH_VISTO) || '';

                pintarPildoraAuth(cantidad, authUrl);

                if (cantidad <= 0) {
                    if (vistoId) { guardarSesion(CLAVE_AUTH_VISTO, ''); }
                    if (avisoActual && avisoActual.opciones && avisoActual.opciones.url === authUrl) {
                        cerrarAvisoActual();
                    }
                    if (prevCantidad > 0) {
                        guardar(CLAVE_AUTH_SYNC, JSON.stringify({ cantidad: 0, ultimoId: '', ts: Date.now() }));
                    }
                    authPrimeraVez = false;
                    return;
                }

                // En el primer chequeo silencioso, si ya venía con ese mismo conteo en el HTML
                // inicial, anotamos el ID sin volver a sonar (evita ruido al recargar).
                if (authPrimeraVez && cantidad === prevCantidad && !vistoId && ultimoId) {
                    guardarSesion(CLAVE_AUTH_VISTO, ultimoId);
                    authPrimeraVez = false;
                    return;
                }
                authPrimeraVez = false;

                var esNuevo = (ultimoId && ultimoId !== vistoId) || cantidad > prevCantidad;
                if (esNuevo) {
                    if (ultimoId) { guardarSesion(CLAVE_AUTH_VISTO, ultimoId); }
                    var textoAviso = ultimo
                        ? (ultimo.caja + ' (' + ultimo.pidio + ') pidió autorización: ' + ultimo.motivo)
                        : (cantidad === 1
                            ? 'Hay 1 pedido de autorización esperando respuesta.'
                            : 'Hay ' + cantidad + ' pedidos de autorización esperando respuesta.');
                    encolarAviso(textoAviso, 'aviso', { url: authUrl, etiquetaAccion: 'Revisar ahora' });
                    guardar(CLAVE_AUTH_SYNC, JSON.stringify({
                        cantidad: cantidad,
                        ultimoId: ultimoId,
                        url: authUrl,
                        ts: Date.now()
                    }));
                }
            })
            .catch(function () { /* sin red momentánea: reintenta en el próximo ciclo */ })
            .then(function () { authEnCurso = false; });
    }

    function iniciarPollingAuth() {
        var notch = document.getElementById('irisNotch');
        if (!notch || !notch.dataset.irisAuthPoll) {
            detenerPollingAuth();
            return;
        }
        if (!timerAuth) {
            timerAuth = window.setInterval(consultarAutorizaciones, INTERVALO_AUTH_MS);
        }
    }

    function promoverMensajesAlNotch(raiz) {
        var partes = obtenerNotch();
        if (!partes.notch || !partes.host) { return; }

        var contenedor = raiz || document;
        contenedor.querySelectorAll('.mensajes').forEach(function (bloque) {
            var quedanVisibles = false;
            bloque.querySelectorAll('.aviso').forEach(function (aviso) {
                if (aviso.dataset.irisProcesado === '1') {
                    if (aviso.dataset.irisPromovido !== '1') { quedanVisibles = true; }
                    return;
                }
                aviso.dataset.irisProcesado = '1';

                var nivel = (aviso.dataset.irisMensaje || '').toLowerCase();
                var esError = aviso.classList.contains('aviso-error') || nivel === 'error';
                // Regla crítica: los errores JAMÁS van al Notch; quedan fijos inline.
                if (esError) {
                    quedanVisibles = true;
                    return;
                }

                var textoNodo = aviso.querySelector('span');
                var texto = (textoNodo ? textoNodo.textContent : aviso.textContent || '').trim();
                if (!texto) {
                    quedanVisibles = true;
                    return;
                }

                var tono = (nivel === 'success' || aviso.classList.contains('aviso-exito'))
                    ? 'exito'
                    : (nivel === 'info' ? 'info' : 'aviso');

                aviso.dataset.irisPromovido = '1';
                encolarAviso(texto, tono);
            });

            if (!quedanVisibles) {
                bloque.classList.add('iris-mensajes-promovidos');
            }
        });
    }

    /* ----------------------------------------------------------------------
       3. Sub-pestañas soldadas adaptativas (ResizeObserver -> Selector + Sheet)
       ---------------------------------------------------------------------- */
    var observadorSubnav = null;

    function evaluarDesbordeSubnav(wrap) {
        if (!wrap || !wrap.isConnected) { return; }
        if (window.innerWidth < 840) {
            wrap.classList.remove('is-compacto');
            return;
        }
        var tira = wrap.querySelector('.iris-subtabs');
        if (!tira) { return; }

        var eraCompacto = wrap.classList.contains('is-compacto');
        if (eraCompacto) {
            wrap.classList.remove('is-compacto');
        }
        var desborda = tira.scrollWidth > tira.clientWidth + 2;
        wrap.classList.toggle('is-compacto', desborda);
    }

    function observarSubnavs(raiz) {
        var lista = (raiz || document).querySelectorAll('[data-iris-subnav]');
        if (!lista.length) { return; }

        if (window.ResizeObserver && !observadorSubnav) {
            observadorSubnav = new ResizeObserver(function (entradas) {
                entradas.forEach(function (entrada) {
                    evaluarDesbordeSubnav(entrada.target);
                });
            });
        }

        lista.forEach(function (wrap) {
            if (observadorSubnav && wrap.dataset.irisObservado !== '1') {
                wrap.dataset.irisObservado = '1';
                observadorSubnav.observe(wrap);
            }
            evaluarDesbordeSubnav(wrap);
        });
    }

    function abrirSubnavSheet(boton) {
        var dialogo = document.getElementById('irisSubnavSheet');
        var lista = document.getElementById('irisSubnavLista');
        var titulo = document.getElementById('irisSubnavTitulo');
        var eyebrow = document.getElementById('irisSubnavEyebrow');
        var wrap = boton.closest('[data-iris-subnav]');
        if (!dialogo || !lista || !wrap) { return; }

        var area = wrap.dataset.area || 'Secciones';
        var pestanas = wrap.querySelectorAll('.iris-subtabs a.iris-subtab');

        if (eyebrow) { eyebrow.textContent = 'Secciones de ' + area; }
        if (titulo) { titulo.textContent = 'Elegí una pantalla (' + pestanas.length + ')'; }

        lista.textContent = '';
        pestanas.forEach(function (enlace) {
            var activo = enlace.classList.contains('is-active');
            var icono = enlace.dataset.icono || 'bi-chevron-right';
            var texto = (enlace.querySelector('span') ? enlace.querySelector('span').textContent : enlace.textContent).trim();
            var href = enlace.getAttribute('href');

            var opcion = document.createElement('a');
            opcion.className = 'iris-sheet-item' + (activo ? ' is-active' : '');
            opcion.href = href;
            opcion.dataset.irisSheetHref = href;
            if (activo) { opcion.setAttribute('aria-current', 'page'); }

            var cajaIc = document.createElement('span');
            cajaIc.className = 'iris-sheet-item-ic';
            var ic = document.createElement('i');
            ic.className = 'bi ' + icono;
            ic.setAttribute('aria-hidden', 'true');
            cajaIc.appendChild(ic);

            var spanNombre = document.createElement('span');
            spanNombre.textContent = texto;

            opcion.appendChild(cajaIc);
            opcion.appendChild(spanNombre);

            if (activo) {
                var tag = document.createElement('span');
                tag.className = 'iris-sheet-actual-tag';
                tag.textContent = 'Actual';
                opcion.appendChild(tag);
            }

            opcion.addEventListener('click', function (ev) {
                dialogo.close();
                var destinoReal = wrap.querySelector('.iris-subtabs a.iris-subtab[href="' + CSS.escape(href) + '"]');
                if (destinoReal) {
                    ev.preventDefault();
                    destinoReal.click();
                }
            });

            lista.appendChild(opcion);
        });

        boton.setAttribute('aria-expanded', 'true');
        dialogo.addEventListener('close', function () {
            boton.setAttribute('aria-expanded', 'false');
        }, { once: true });
        dialogo.showModal();
    }

    /* ----------------------------------------------------------------------
       4. Paleta de búsqueda rápida de pantallas (Ctrl+K)
       ---------------------------------------------------------------------- */
    var indicePaleta = 0;
    var itemsPaletaActuales = [];

    function recogerPantallasDelMenu() {
        var lateral = document.querySelector('.lateral');
        if (!lateral) { return []; }
        var resultado = [];
        lateral.querySelectorAll('a.lateral-link').forEach(function (enlace) {
            var href = enlace.getAttribute('href');
            var span = enlace.querySelector('span');
            var titulo = (span ? span.textContent : enlace.textContent || '').trim();
            if (!href || !titulo) { return; }

            var iconoEl = enlace.querySelector('i');
            var icono = 'bi-arrow-right-circle';
            if (iconoEl) {
                iconoEl.classList.forEach(function (cls) {
                    if (cls.indexOf('bi-') === 0) { icono = cls; }
                });
            }

            var detalles = enlace.closest('details.lateral-desplegable');
            var area = 'General';
            if (detalles) {
                var resumenSpan = detalles.querySelector('summary > span');
                if (resumenSpan) { area = resumenSpan.textContent.trim(); }
            } else if (href === '/') {
                area = 'Inicio';
            }

            resultado.push({
                titulo: titulo,
                area: area,
                icono: icono,
                href: href,
                enlace: enlace,
                busqueda: normalizar(titulo + ' ' + area)
            });
        });
        return resultado;
    }

    function pintarResultadosPaleta(consulta) {
        var lista = document.getElementById('irisPaletaLista');
        var cuenta = document.getElementById('irisPaletaCuenta');
        if (!lista) { return; }

        var todas = recogerPantallasDelMenu();
        var q = normalizar(consulta);
        var filtradas = q
            ? todas.filter(function (p) { return p.busqueda.indexOf(q) !== -1; })
            : todas;

        itemsPaletaActuales = filtradas.slice(0, 12);
        if (indicePaleta >= itemsPaletaActuales.length) {
            indicePaleta = 0;
        }

        if (cuenta) {
            cuenta.textContent = q
                ? 'Resultados (' + filtradas.length + ' de ' + todas.length + ')'
                : 'Pantallas disponibles (' + todas.length + ')';
        }

        lista.textContent = '';
        if (!itemsPaletaActuales.length) {
            var vacio = document.createElement('div');
            vacio.className = 'iris-paleta-vacio';
            vacio.textContent = 'No encontramos ninguna pantalla con "' + consulta.trim() + '".';
            lista.appendChild(vacio);
            return;
        }

        var grilla = document.createElement('div');
        grilla.className = 'iris-notch-lista';

        itemsPaletaActuales.forEach(function (item, idx) {
            var a = document.createElement('a');
            a.className = 'iris-paleta-item' + (idx === indicePaleta ? ' is-seleccionado' : '');
            a.href = item.href;
            a.setAttribute('role', 'option');
            a.setAttribute('aria-selected', idx === indicePaleta ? 'true' : 'false');
            a.dataset.irisPaletaIdx = String(idx);

            var icWrap = document.createElement('span');
            icWrap.className = 'iris-paleta-item-ic';
            var ic = document.createElement('i');
            ic.className = 'bi ' + item.icono;
            ic.setAttribute('aria-hidden', 'true');
            icWrap.appendChild(ic);

            var txt = document.createElement('span');
            txt.className = 'iris-paleta-item-txt';
            var t = document.createElement('span');
            t.className = 'iris-paleta-item-titulo';
            t.textContent = item.titulo;
            var ar = document.createElement('span');
            ar.className = 'iris-paleta-item-area';
            ar.textContent = item.area;
            txt.appendChild(t);
            txt.appendChild(ar);

            a.appendChild(icWrap);
            a.appendChild(txt);

            a.addEventListener('click', function (ev) {
                cerrarBuscadorNotch();
                if (item.enlace && item.enlace.isConnected) {
                    ev.preventDefault();
                    item.enlace.click();
                }
            });

            grilla.appendChild(a);
        });

        lista.appendChild(grilla);
    }

    function actualizarSeleccionPaleta() {
        var nodos = document.querySelectorAll('#irisPaletaLista .iris-paleta-item');
        nodos.forEach(function (nodo, idx) {
            var sel = idx === indicePaleta;
            nodo.classList.toggle('is-seleccionado', sel);
            nodo.setAttribute('aria-selected', sel ? 'true' : 'false');
            if (sel) { nodo.scrollIntoView({ block: 'nearest' }); }
        });
    }

    function cerrarBuscadorNotch() {
        if (!buscadorNotchAbierto) { return; }
        buscadorNotchAbierto = false;
        var notch = document.getElementById('irisNotch');
        var panel = document.getElementById('irisNotchBuscador');
        var lupa = document.getElementById('irisNotchLupa');
        var hostAviso = document.getElementById('irisNotchAviso');
        if (panel) { panel.hidden = true; }
        if (lupa) { lupa.setAttribute('aria-expanded', 'false'); }
        if (notch) {
            notch.classList.remove('es-buscando');
            notch.style.width = '';
        }
        actualizarIndicadorColaBuscador();
        if (avisoActual) {
            if (hostAviso && (hostAviso.hidden || !hostAviso.firstElementChild)) {
                pintarAvisoEnNotch(avisoActual, true);
            } else if (notch) {
                if (hostAviso) { hostAviso.hidden = false; }
                sincronizarClasesNotch(notch, avisoActual);
            }
            if (!avisoActual.fijo) {
                reanudarAviso();
            }
        } else if (colaAvisos.length > 0) {
            mostrarSiguienteAviso();
        }
    }

    function abrirPaleta() {
        var notch = document.getElementById('irisNotch');
        var panel = document.getElementById('irisNotchBuscador');
        var input = document.getElementById('irisPaletaBuscar');
        var lupa = document.getElementById('irisNotchLupa');
        var hostAviso = document.getElementById('irisNotchAviso');
        if (!notch || !panel) { return; }
        if (buscadorNotchAbierto) {
            cerrarBuscadorNotch();
            return;
        }
        cerrarOtrosDialogos(null);
        cerrarAreaPopover();

        // Fijar el ancho del propio Notch en reposo para no expandirse a los costados
        var anchoReposo = notch.offsetWidth || 0;
        if (window.innerWidth >= 640 && anchoReposo > 0) {
            var topeAncho = Math.min(Math.max(300, anchoReposo), Math.max(300, window.innerWidth - 48));
            notch.style.width = topeAncho + 'px';
        } else {
            notch.style.width = '';
        }

        // Regla estricta buscador <-> notificaciones: pausar y ocultar sin perder estado ni timer
        if (avisoActual) {
            if (avisoActual.fijo) {
                limpiarTemporizador();
            } else {
                pausarAviso();
            }
            if (hostAviso) { hostAviso.hidden = true; }
            notch.classList.remove('tiene-aviso', 'tiene-aviso-largo');
            notch.style.minWidth = '';
        }

        buscadorNotchAbierto = true;
        notch.classList.add('es-buscando');
        panel.hidden = false;
        if (lupa) { lupa.setAttribute('aria-expanded', 'true'); }
        actualizarIndicadorColaBuscador();

        indicePaleta = 0;
        if (input) { input.value = ''; }
        pintarResultadosPaleta('');
        if (input) { input.focus(); }
    }

    function alternarModalAtajos() {
        var dialogo = document.getElementById('irisModalAyuda');
        if (!dialogo) { return; }
        if (dialogo.open) {
            dialogo.close();
        } else {
            abrirModalAyuda('pantalla');
        }
    }

    function abrirAtajosDesdeAPI() {
        abrirModalAyuda('atajos');
    }

    function cerrarOtrosDialogos(excepto) {
        document.querySelectorAll('dialog.iris-dialog[open]').forEach(function (dlg) {
            if (dlg !== excepto) { dlg.close(); }
        });
    }

    /* ----------------------------------------------------------------------
       4b. Centro de Ayuda unificado (F1), Primeros pasos y Práctica interactiva
       ---------------------------------------------------------------------- */
    function hayTicketEnMostrador() {
        if (!document.getElementById('mostrador')) { return false; }
        if (document.querySelectorAll('#renglones .renglon').length > 0) { return true; }
        var vacio = document.getElementById('ticketVacio');
        if (vacio && vacio.classList.contains('d-none')) { return true; }
        if (document.querySelectorAll('#pagos .btn-quitar-pago').length > 0) { return true; }
        var clubCliente = document.getElementById('clubCliente');
        if (clubCliente && !clubCliente.classList.contains('d-none')) { return true; }
        return false;
    }

    function encontrarBotonMasMostrador() {
        var renglon = document.querySelector('#renglones .renglon');
        if (!renglon) { return null; }
        var botones = renglon.querySelectorAll('.btn-icono');
        for (var i = 0; i < botones.length; i++) {
            if (botones[i].textContent.trim() === '+') {
                return botones[i];
            }
        }
        return renglon;
    }

    function leerCantidadPrimerRenglon() {
        var campo = document.querySelector('#renglones .renglon input[aria-label="Cantidad"]');
        if (!campo) { return 0; }
        var num = Number(String(campo.value || '0').trim().replace(',', '.'));
        return isFinite(num) ? num : 0;
    }

    function limpiarStorageMostrador() {
        var mostrador = document.getElementById('mostrador');
        if (!mostrador || !mostrador.dataset.turno) { return; }
        try {
            window.sessionStorage.removeItem('cafeto.ticket.' + mostrador.dataset.turno);
        } catch (e) { /* storage bloqueado */ }
    }

    function limpiarPracticaMostrador() {
        if (!document.getElementById('mostrador')) { return; }
        ['modalDescuento', 'modalCliente', 'modalAjuste', 'modalRadar', 'modalConfirmar'].forEach(function (id) {
            var modal = document.getElementById(id);
            if (modal && modal.classList.contains('show') && window.bootstrap && window.bootstrap.Modal) {
                var inst = window.bootstrap.Modal.getInstance(modal);
                if (inst) { inst.hide(); }
            }
        });

        var tope = 50;
        while (tope-- > 0) {
            var btnQuitarItem = document.querySelector('#renglones .renglon .btn-outline-danger');
            if (!btnQuitarItem) { break; }
            btnQuitarItem.click();
        }

        tope = 10;
        while (tope-- > 0) {
            var btnQuitarPago = document.querySelector('#pagos .btn-quitar-pago');
            if (!btnQuitarPago) { break; }
            btnQuitarPago.click();
        }

        var clubCliente = document.getElementById('clubCliente');
        var clubQuitar = document.getElementById('clubQuitar');
        if (clubCliente && !clubCliente.classList.contains('d-none') && clubQuitar) {
            clubQuitar.click();
        }

        var buscador = document.getElementById('buscar');
        if (buscador && buscador.value) {
            buscador.value = '';
            buscador.dispatchEvent(new Event('input', { bubbles: true }));
        }

        limpiarStorageMostrador();
        sincronizarBilletesRapidos();
        sincronizarCantidadesCatalogo();
    }

    function limpiarModalPractica(selectorModal) {
        var modal = document.querySelector(selectorModal);
        if (!modal) { return; }
        modal.querySelectorAll('form').forEach(function (form) {
            try { form.reset(); } catch (e) { /* ignorado */ }
            form.querySelectorAll('.is-invalid, .is-valid').forEach(function (el) {
                el.classList.remove('is-invalid', 'is-valid');
                el.removeAttribute('aria-invalid');
            });
        });
        if (modal.classList.contains('show') && window.bootstrap && window.bootstrap.Modal) {
            var inst = window.bootstrap.Modal.getInstance(modal) || window.bootstrap.Modal.getOrCreateInstance(modal);
            if (inst) { inst.hide(); }
        }
    }

    var practicaEstadoTemp = {
        inicioAvisosAbiertos: null,
        reportesBtnSerie: null,
        reportesEstadoInicial: null
    };

    var GUIAS_PANTALLA = [
        {
            coincide: function (p) { return p === '/'; },
            eyebrow: 'Inicio · Panel del día',
            titulo: 'Cómo usar el Inicio',
            resumen: 'Es tu punto de partida: resume alertas urgentes, accesos rápidos según tu rol y los números del día.',
            pasos: [
                'Revisá primero las alertas rojas si aparecen: son vencimientos, pagos vencidos o cierres con diferencia que requieren atención.',
                'Los avisos operativos amarillos quedan agrupados en la cápsula "avisos operativos pendientes" para no llenar la pantalla; abrila cuando quieras repasarlos.',
                'Usá el botón principal en ámbar para entrar directo a Vender o Abrir la caja, y las demás tarjetas para tareas diarias.',
                'En el Notch superior ves en tiempo real qué cajas están abiertas y si hay pedidos de autorización esperando.'
            ],
            consejo: 'Presioná Ctrl+K en cualquier momento para saltar a cualquier pantalla sin buscar en el menú.',
            interactiva: true,
            etiquetaPractica: 'Iniciar práctica guiada',
            alIniciar: function () {
                var det = document.querySelector('details.iris-avisos-secundarios');
                practicaEstadoTemp.inicioAvisosAbiertos = det ? Boolean(det.open) : null;
            },
            limpiar: function () {
                var det = document.querySelector('details.iris-avisos-secundarios');
                if (det && practicaEstadoTemp.inicioAvisosAbiertos === false) {
                    det.open = false;
                }
                practicaEstadoTemp.inicioAvisosAbiertos = null;
            },
            mensajeFin: 'Recorrido de Inicio completado.',
            tour: [
                { selector: '#irisNotch', titulo: 'Estado de cajas y avisos', texto: 'Acá ves en vivo qué cajas están abiertas (sin mostrar montos de arqueo) y si la caja pidió una autorización a distancia.' },
                { selector: '.iris-onboarding', titulo: 'Configuración inicial del local', texto: 'Mientras falten datos del comercio o confirmar las reglas, este aviso te lleva directo a Ajustes del local.' },
                {
                    obtenerNodo: function () {
                        var det = document.querySelector('details.iris-avisos-secundarios');
                        if (det && !det.open) { return det.querySelector('summary') || det; }
                        return document.querySelector('.iris-avisos-criticos, .iris-avisos-secundarios');
                    },
                    titulo: 'Desplegá los avisos secundarios',
                    texto: 'Lo crítico queda siempre visible en rojo; los avisos secundarios se guardan plegados en esta cápsula. Tocala para abrirla y ver qué contiene.',
                    espera: function () {
                        var det = document.querySelector('details.iris-avisos-secundarios');
                        return det && !det.open ? 'Tocá la cápsula de avisos para desplegarla…' : '';
                    },
                    condicion: function () {
                        var det = document.querySelector('details.iris-avisos-secundarios');
                        return Boolean(det && det.open);
                    }
                },
                { selector: '.grilla-atajos', titulo: 'Accesos rápidos', texto: 'El botón ámbar es tu acción principal del turno; las demás tarjetas te llevan en 1 clic a cambios, recepción, producción, mermas o inventario.' },
                { selector: '.grilla-resumen', titulo: 'Resumen del día', texto: 'Muestra ventas del día, cajas abiertas y acceso directo a Reportes (solo para perfiles con permiso).' }
            ]
        },
        {
            coincide: function (p) { return p.indexOf('/ventas/mostrador') === 0; },
            eyebrow: 'Mostrador · Ventas rápidas',
            titulo: 'Cómo cobrar en el Mostrador',
            resumen: 'Armá el ticket tocando productos o pasando el lector de códigos de barras. Todos los precios y vueltos los calcula el servidor.',
            pasos: [
                'Buscá por nombre o escaneá el código en el buscador (atajo F2). También podés filtrar por Más vendidos, Cafetería o Minisuper.',
                'Tocá cada producto para sumarlo al ticket; ajustá cantidades con los botones − y + o escribiendo el peso/cantidad.',
                'Si el cliente pertenece al Club Cafeto, identificalo por teléfono, DNI o QR para aplicar sus beneficios.',
                'Elegí cómo paga (F4 selecciona Efectivo). Si dejás el monto recibido vacío cobra exacto; si escribís con cuánto paga, el servidor calcula el vuelto. Pulsá Cobrar (F9).'
            ],
            consejo: 'Si un cliente paga con dos medios (por ejemplo Efectivo + Transferencia), tocá "Pagar con dos medios" debajo de Efectivo.',
            interactiva: true,
            esMostrador: true,
            etiquetaPractica: 'Practicar una venta (sin cobrar)',
            verificarInicio: function () {
                if (hayTicketEnMostrador()) {
                    return {
                        ok: false,
                        motivo: 'Tenés un ticket abierto en el mostrador. Cobralo o vacialo primero para practicar sin perder esa venta.'
                    };
                }
                return { ok: true };
            },
            limpiar: limpiarPracticaMostrador,
            mensajeFin: 'Práctica terminada · Limpiamos el mostrador por vos.',
            tour: [
                {
                    selector: '#catalogo',
                    titulo: 'Sumá un producto al ticket',
                    texto: 'Tocá cualquier producto del catálogo para agregarlo. En una venta real también podés escanear el código de barras.',
                    espera: 'Tocá cualquier producto del catálogo…',
                    condicion: function () {
                        return document.querySelectorAll('#renglones .renglon').length > 0;
                    }
                },
                {
                    dinamico: true,
                    obtenerNodo: encontrarBotonMasMostrador,
                    alEntrar: function (ctx) {
                        ctx.cantInicial = leerCantidadPrimerRenglon();
                    },
                    titulo: 'Aumentá la cantidad',
                    texto: 'Tocá el botón + del renglón para sumar una unidad más (también podés escribir un peso o cantidad a mano).',
                    espera: 'Tocá el botón + en el ticket…',
                    condicion: function (ctx) {
                        return leerCantidadPrimerRenglon() > (ctx.cantInicial || 0);
                    }
                },
                {
                    obtenerNodo: function () {
                        return document.querySelector('.js-medio[data-efectivo="1"]') || document.getElementById('medios');
                    },
                    titulo: 'Elegí Efectivo',
                    texto: 'Tocá Efectivo (o presioná la tecla F4) para habilitar el cobro inmediato o ingresar con cuánto te pagan.',
                    espera: 'Tocá Efectivo o presioná F4…',
                    condicion: function () {
                        return Boolean(document.querySelector('#pagos input[aria-label="Efectivo recibido"]'));
                    }
                },
                {
                    dinamico: true,
                    obtenerNodo: function () {
                        return document.querySelector('#pagos input[aria-label="Efectivo recibido"]') || document.getElementById('pago');
                    },
                    alEntrar: function (ctx) {
                        var totalNodo = document.getElementById('tTotal');
                        ctx.totalAlEntrar = leerMontoFormateado(totalNodo ? totalNodo.textContent : '') || 0;
                    },
                    titulo: 'Monto recibido o pago exacto',
                    texto: 'Si paga justo no hace falta escribir nada. Para calcular vuelto, escribí acá con cuánto te paga (por ejemplo 20000) y salí del campo o pulsá Tab.',
                    espera: 'Escribí un monto recibido (o tocá Saltar paso para pago exacto)…',
                    condicion: function (ctx) {
                        var filaVuelto = document.getElementById('filaVuelto');
                        var tVuelto = document.getElementById('tVuelto');
                        if (filaVuelto && !filaVuelto.classList.contains('d-none') && tVuelto && tVuelto.textContent.trim().length > 0) {
                            return true;
                        }
                        var cobrarBtn = document.getElementById('cobrar');
                        var campo = document.querySelector('#pagos input[aria-label="Efectivo recibido"]');
                        var recibido = leerMontoInput(campo ? campo.value : '');
                        return Boolean(recibido !== null && ctx.totalAlEntrar > 0 && recibido >= ctx.totalAlEntrar && cobrarBtn && !cobrarBtn.disabled);
                    }
                },
                {
                    dinamico: true,
                    obtenerNodo: function () {
                        var vuelto = document.getElementById('filaVuelto');
                        if (vuelto && !vuelto.classList.contains('d-none') && esVisibleEnPantalla(vuelto)) {
                            return vuelto;
                        }
                        return document.getElementById('cobrar') || document.getElementById('pago') || document.getElementById('ticket');
                    },
                    titulo: '¡Listo! Ya sabés armar y cotizar',
                    texto: 'En una venta real acá apretarías Cobrar (F9). En modo práctica nunca cobramos nada real: tocá abajo y te dejamos el mostrador limpio en cero.',
                    botonFinal: 'Terminar y limpiar mostrador'
                }
            ]
        },
        {
            coincide: function (p) { return p === '/ventas/caja/' || p.indexOf('/ventas/caja/') === 0; },
            eyebrow: 'Mostrador · Puesto de caja',
            titulo: 'Cómo abrir, operar y cerrar la caja',
            resumen: 'Cada dispositivo recuerda su caja física y trabaja con arqueo ciego para cuidar la transparencia del turno.',
            pasos: [
                'Si el equipo todavía no está vinculado a un cajón, elegí primero en qué caja estás (Cafetería o Minisuper).',
                'Para abrir el turno, cargá el fondo inicial en efectivo que hay en el cajón y confirmá.',
                'Durante el turno usá los botones superiores para registrar refuerzos o retiros, hacer un arqueo parcial (Corte X) o cerrar el turno.',
                'Al cerrar, contá la plata real del cajón e ingresala sin ver lo esperado (arqueo ciego). Si la diferencia supera la tolerancia, podés pedir autorización a distancia.'
            ],
            consejo: 'Si cerrás con diferencia y el supervisor no está presente, usá "Pedir autorización a distancia": le llegará el aviso en vivo al Notch y por correo/WhatsApp.',
            tour: [
                { selector: '.encabezado', titulo: 'Puesto de caja actual', texto: 'Muestra el cajón vinculado a este dispositivo y si el turno está abierto o cerrado.' },
                { selector: '.iris-caja-acciones, main.pagina form', titulo: 'Operación del turno', texto: 'Desde acá abrís el turno o abrís en un clic Refuerzo o retiro, Cerrar turno o Arqueo parcial.' }
            ]
        },
        {
            coincide: function (p) { return p.indexOf('/ventas/autorizaciones') === 0; },
            eyebrow: 'Supervisión · Autorizaciones a distancia',
            titulo: 'Cómo responder pedidos de la caja',
            resumen: 'Cuando un cajero necesita aprobar un descuento sobre tope, un fiado, un ajuste o un cierre con diferencia, aparece acá en vivo.',
            pasos: [
                'Revisá el motivo, la caja que lo pidió y el importe o detalle del ticket.',
                'Cada pedido vence automáticamente a los 10 minutos para evitar aprobaciones viejas.',
                'Tocá "Aprobar" o "Rechazar": la caja se entera sola en 2 segundos sin que nadie tenga que volver a cargar el ticket.'
            ],
            consejo: 'Aunque estés en otra pantalla de Cafeto, el Notch superior te avisará en vivo en cuanto llegue un pedido nuevo.',
            tour: [
                { selector: '.filtro-estado', titulo: 'Filtro de pedidos', texto: 'Alterná entre los pedidos esperando respuesta ahora y el historial de aprobados, rechazados o vencidos.' },
                { selector: '.iris-pantalla-cuerpo .row, .iris-pantalla-cuerpo .aviso', titulo: 'Tarjetas de aprobación', texto: 'Desde cada tarjeta ves qué se pidió y aprobás o rechazás en un clic.' }
            ]
        },
        {
            coincide: function (p) { return p.indexOf('/negocio/parametros') === 0; },
            eyebrow: 'Ajustes del local · Configuración',
            titulo: 'Cómo configurar tu local',
            resumen: 'Define la identidad fiscal del comercio (lo que sale en tickets y PDF) y las reglas operativas de caja y stock.',
            pasos: [
                'En "El comercio" completá razón social, nombre de fantasía, CUIT, domicilio, localidad, teléfono y correo.',
                'En "Cómo se opera" ajustá el redondeo de ventas, la tolerancia de arqueo, el fondo sugerido, días para cambios y aviso de vencimientos.',
                'Hacé clic en Guardar: las reglas nuevas aplican desde la próxima operación y el aviso de primer inicio desaparece solo.'
            ],
            consejo: 'Cada modificación queda guardada en el Registro de Actividad con foto de antes y después.',
            tour: [
                { selector: 'main.pagina form .col-xl-6:first-of-type .tarjeta', titulo: 'Datos del comercio', texto: 'Completá CUIT, domicilio, teléfono y correo: aparecen en tus tickets y reportes exportados.' },
                { selector: 'main.pagina form .col-xl-6:last-of-type .tarjeta', titulo: 'Reglas de operación', texto: 'Definí cuánto se tolera de diferencia al cerrar caja, el fondo sugerido y los días de aviso por vencimiento.' },
                { selector: 'main.pagina form button[type="submit"]', titulo: 'Guardar cambios', texto: 'Al guardar confirmás la configuración inicial del local y desbloqueás la puesta en marcha.' }
            ]
        },
        {
            coincide: function (p) { return p.indexOf('/negocio/reportes') === 0 || p.indexOf('/negocio/resumen') === 0; },
            eyebrow: 'Reportes · Análisis del negocio',
            titulo: 'Cómo leer y exportar Reportes',
            resumen: 'Combiná el tablero interactivo del período con los 11 informes detallados descargables en Excel y PDF.',
            pasos: [
                'Elegí un período arriba (Hoy, 7 días, Este mes, 90 días) para actualizar los 4 gráficos interactivos.',
                'Tocá cualquier cápsula de informe (Ventas por día, Productos y márgenes, Medios de pago, Stock valorizado, etc.) sin recargar el tablero.',
                'Usá los botones directos Excel o PDF dentro de la tarjeta del informe para descargar exactamente lo que estás viendo filtrado.'
            ],
            consejo: 'En cada gráfico podés prender o apagar series y abrir "Ver los datos" para leer la tabla completa.',
            interactiva: true,
            etiquetaPractica: 'Practicar en Reportes',
            alIniciar: function () {
                practicaEstadoTemp.reportesBtnSerie = null;
                practicaEstadoTemp.reportesEstadoInicial = null;
            },
            limpiar: function () {
                var btn = practicaEstadoTemp.reportesBtnSerie;
                var inicial = practicaEstadoTemp.reportesEstadoInicial;
                if (btn && btn.isConnected && inicial !== null && btn.getAttribute('aria-pressed') !== inicial) {
                    btn.click();
                }
                practicaEstadoTemp.reportesBtnSerie = null;
                practicaEstadoTemp.reportesEstadoInicial = null;
            },
            mensajeFin: 'Práctica de Reportes completada · Dejamos los gráficos como estaban.',
            tour: [
                { selector: '#periodosTablero', titulo: 'Período del tablero', texto: 'Cambiá el rango de fechas para recalcular ingresos, egresos, ganancia neta y horas pico.' },
                {
                    obtenerNodo: function () {
                        return document.querySelector('[data-grafico="evolucion"] [data-serie="egresos"]')
                            || document.querySelector('[data-grafico="evolucion"] [data-serie]');
                    },
                    alEntrar: function (ctx) {
                        var btn = document.querySelector('[data-grafico="evolucion"] [data-serie="egresos"]')
                            || document.querySelector('[data-grafico="evolucion"] [data-serie]');
                        ctx.btnSerie = btn;
                        ctx.estadoInicial = btn ? btn.getAttribute('aria-pressed') : null;
                        practicaEstadoTemp.reportesBtnSerie = btn;
                        practicaEstadoTemp.reportesEstadoInicial = ctx.estadoInicial;
                    },
                    titulo: 'Prendé o apagá una serie',
                    texto: 'Tocá "Egresos" para apagarlo o volver a prenderlo en el gráfico al instante sin recargar la página.',
                    espera: 'Tocá el botón "Egresos" del gráfico…',
                    condicion: function (ctx) {
                        return Boolean(ctx.btnSerie && ctx.btnSerie.getAttribute('aria-pressed') !== ctx.estadoInicial);
                    }
                },
                { selector: '.capsulas-reportes', titulo: 'Elegí el informe', texto: 'Cambiá de informe sin perder tu lugar en la pantalla.' },
                { selector: '.tarjeta-informe', titulo: 'Filtros y descarga directa', texto: 'Descargá Excel o PDF en 1 clic con el mismo filtro que ves en pantalla.' }
            ]
        },
        {
            coincide: function (p) { return p.indexOf('/stock/sugerencias') === 0; },
            eyebrow: 'Stock · Mejora continua',
            titulo: 'Cómo aprobar Sugerencias de mejora',
            resumen: 'Revisa cambios propuestos automáticamente sobre el catálogo: nada se modifica hasta que vos lo aprobás.',
            pasos: [
                'Compará en cada tarjeta el valor de "Hoy" contra "Se propone" y leé el motivo.',
                'Si estás de acuerdo con una sola, tocá "Aplicar". Si querés descartarla, tocá "Descartar".',
                'Para aprobar varias juntas, tildá sus casillas (o "Marcar todas") y tocá "Aplicar seleccionadas".'
            ],
            consejo: 'Si una sugerencia no cumple alguna validación del producto, el sistema avisa y aplica igual todas las demás válidas.',
            tour: [
                { selector: '.barra-masiva', titulo: 'Acción masiva', texto: 'Tildá varias sugerencias obvias y aplicalas todas juntas con un conteo claro antes de confirmar.' },
                { selector: '.iris-sugerencia-card', titulo: 'Comparación Hoy → Se propone', texto: 'Verificá cada propuesta antes de aprobar; todo queda auditado a tu nombre.' }
            ]
        },
        {
            coincide: function (p) { return p.indexOf('/usuarios') === 0; },
            eyebrow: 'Equipo y Permisos',
            titulo: 'Cómo gestionar Usuarios',
            resumen: 'Administrá quién entra al sistema, con qué permisos trabaja y entregá claves provisorias de forma segura.',
            pasos: [
                'Tocá "Nuevo usuario" para dar de alta a una persona con DNI, nombre, correo y perfil (el usuario se genera solo).',
                'Si otorgás perfil Administrador, el sistema te pedirá confirmar con tu propia clave.',
                'Desde el botón "⋯" de cada tarjeta podés restablecer la clave, cambiar el perfil o prestar un perfil temporal por reemplazo.'
            ],
            consejo: 'Los usuarios nunca se borran físicamente: se dan de baja lógica y podés reactivarlos cuando vuelvan.',
            interactiva: true,
            etiquetaPractica: 'Practicar alta (sin guardar)',
            verificarInicio: function () {
                var modal = document.getElementById('modalCrear');
                if (modal && modal.classList.contains('show')) {
                    return { ok: false, motivo: 'Cerrá primero el formulario de nuevo usuario que tenés abierto para practicar desde cero.' };
                }
                return { ok: true };
            },
            limpiar: function () { limpiarModalPractica('#modalCrear'); },
            mensajeFin: 'Práctica terminada · Cerramos el formulario sin crear ningún usuario.',
            obtenerTour: function () {
                if (document.querySelector('[data-bs-target="#modalCrear"]') && document.querySelector('#modalCrear #id_dni_usuario')) {
                    return [
                        {
                            selector: '[data-bs-target="#modalCrear"]',
                            titulo: 'Abrí el formulario de alta',
                            texto: 'Tocá "Nuevo usuario". En modo práctica nada se guarda: el botón Guardar queda bloqueado.',
                            espera: 'Tocá "Nuevo usuario"…',
                            condicion: function () {
                                var m = document.getElementById('modalCrear');
                                return Boolean(m && m.classList.contains('show'));
                            }
                        },
                        {
                            dinamico: true,
                            requiereModal: '#modalCrear',
                            obtenerNodo: function () {
                                return document.querySelector('#modalCrear #id_dni_usuario') || document.querySelector('#modalCrear .modal-body');
                            },
                            titulo: 'Probá cargar un DNI',
                            texto: 'Escribí al menos 4 números de prueba en el campo DNI. El alias del usuario se genera solo al guardar.',
                            espera: 'Escribí al menos 4 números en DNI…',
                            condicion: function () {
                                var inp = document.querySelector('#modalCrear #id_dni_usuario');
                                return Boolean(inp && inp.value.replace(/\D/g, '').length >= 4);
                            }
                        },
                        {
                            dinamico: true,
                            requiereModal: '#modalCrear',
                            obtenerNodo: function () {
                                return document.querySelector('#modalCrear .modal-footer') || document.getElementById('modalCrear');
                            },
                            titulo: '¡Listo! Ya conocés el alta',
                            texto: 'En un alta real, al guardar se genera una clave provisoria de 1 solo uso. Tocá abajo para cerrar y dejar todo limpio.',
                            botonFinal: 'Terminar y cerrar sin guardar'
                        }
                    ];
                }
                return [
                    { selector: '.encabezado .acciones', titulo: 'Altas y exportaciones', texto: 'Creá usuarios nuevos, exportá el directorio o transferí la titularidad si sos titular.' },
                    { selector: '#herramientas', titulo: 'Buscar y filtrar', texto: 'Presioná F2 para buscar por nombre, DNI o alias, o filtrá por perfil y estado.' },
                    { selector: '.grilla > article.tarjeta', titulo: 'Tarjeta de cada persona', texto: 'Consultá la ficha, editá el correo o abrí el menú "⋯" para acciones de seguridad.' }
                ];
            }
        },
        {
            coincide: function (p) { return p.indexOf('/perfiles') === 0; },
            eyebrow: 'Equipo y Permisos',
            titulo: 'Cómo gestionar Perfiles y Permisos',
            resumen: 'Definí qué puede ver y hacer cada rol dentro de Cafeto sin dar permisos de más.',
            pasos: [
                'Cada perfil agrupa permisos concretos por módulo (Mostrador, Stock, Compras, Reportes, Seguridad).',
                'Tocá "Nuevo perfil" para crear un rol a medida o editá uno existente.',
                'Los permisos se aplican automáticamente al menú y a cada botón de la pantalla.'
            ],
            consejo: 'Usá perfiles temporales en Usuarios cuando alguien cubre a un encargado por pocos días.',
            tour: [
                { selector: '.encabezado .acciones', titulo: 'Crear perfil', texto: 'Armá perfiles a medida según el puesto de trabajo.' },
                { selector: '.grilla > article.tarjeta', titulo: 'Detalle del perfil', texto: 'Consultá qué permisos tiene asignados cada rol.' }
            ]
        },
        {
            coincide: function (p) { return p.indexOf('/stock/productos') === 0; },
            eyebrow: 'Inventario y Stock · Catálogo',
            titulo: 'Cómo gestionar Productos',
            resumen: 'Consultá, creá o editá productos simples, elaborados con receta o combos con total control de stock y costos.',
            pasos: [
                'Usá F2 para ir directo al buscador y filtrá por rubro, marca, proveedor o vigencia.',
                'Tocá "Nuevo producto" para cargar nombre, código de barras, precio de venta y stock mínimo.',
                'Usá las pestañas superiores para moverte rápido entre Productos, Marcas, Rubros y Proveedores.'
            ],
            consejo: 'Podés importar planillas Excel o actualizar precios en masa desde los botones del encabezado de Productos.',
            interactiva: true,
            etiquetaPractica: 'Practicar alta (sin guardar)',
            verificarInicio: function () {
                var modal = document.getElementById('modalCrear');
                if (modal && modal.classList.contains('show')) {
                    return { ok: false, motivo: 'Cerrá primero el formulario de nuevo producto que tenés abierto para practicar desde cero.' };
                }
                return { ok: true };
            },
            limpiar: function () { limpiarModalPractica('#modalCrear'); },
            mensajeFin: 'Práctica terminada · Cerramos el formulario sin guardar ningún producto.',
            obtenerTour: function () {
                if (document.querySelector('[data-bs-target="#modalCrear"]') && document.querySelector('#modalCrear [name="nombre_producto"]')) {
                    return [
                        {
                            selector: '[data-bs-target="#modalCrear"]',
                            titulo: 'Abrí el formulario de alta',
                            texto: 'Tocá "Nuevo producto". En modo práctica podés explorar y escribir tranquilo: el botón Guardar queda bloqueado.',
                            espera: 'Tocá "Nuevo producto"…',
                            condicion: function () {
                                var m = document.getElementById('modalCrear');
                                return Boolean(m && m.classList.contains('show'));
                            }
                        },
                        {
                            dinamico: true,
                            requiereModal: '#modalCrear',
                            obtenerNodo: function () {
                                return document.querySelector('#modalCrear [name="nombre_producto"]') || document.querySelector('#modalCrear .modal-body');
                            },
                            titulo: 'Escribí un nombre de prueba',
                            texto: 'Escribí al menos 3 letras (por ejemplo "Café prueba"). Así es como después lo vas a buscar en el mostrador.',
                            espera: 'Escribí al menos 3 letras en el nombre…',
                            condicion: function () {
                                var inp = document.querySelector('#modalCrear [name="nombre_producto"]');
                                return Boolean(inp && inp.value.trim().length >= 3);
                            }
                        },
                        {
                            dinamico: true,
                            requiereModal: '#modalCrear',
                            obtenerNodo: function () {
                                return document.querySelector('#modalCrear .modal-footer') || document.getElementById('modalCrear');
                            },
                            titulo: '¡Perfecto! Así se carga un producto',
                            texto: 'En un alta real completarías rubro, precio y tocarías Guardar. Tocá abajo para cerrar y dejar todo limpio sin guardar nada.',
                            botonFinal: 'Terminar y cerrar sin guardar'
                        }
                    ];
                }
                return [
                    { selector: '.iris-subnav-wrap', titulo: 'Pestañas del área', texto: 'Cambiá de vista dentro del mismo módulo en un clic (en tablet/celular se abre desde el selector desplegable).' },
                    { selector: '#herramientas', titulo: 'Buscador y filtros', texto: 'Presioná F2 para escribir al instante; los selectores aplican el filtro automáticamente.' },
                    { selector: '.grilla > article.tarjeta', titulo: 'Fichas de catálogo', texto: 'Cada tarjeta permite ver el detalle completo, editar o dar de baja lógica.' }
                ];
            }
        },
        {
            coincide: function (p) { return p.indexOf('/stock/') === 0; },
            eyebrow: 'Inventario y Stock',
            titulo: 'Cómo gestionar el catálogo y el stock',
            resumen: 'Consultá, creá o editá registros del inventario, revisá vencimientos, cargá producciones o hacé conteos.',
            pasos: [
                'Usá F2 para ir directo al buscador y filtrá por rubro, marca, proveedor o vigencia.',
                'Las pestañas superiores te dejan moverte rápido entre Productos, Marcas, Rubros, Proveedores, Producción y Vencimientos.',
                'Todas las bajas son lógicas y quedan auditadas para no perder el historial de ventas.'
            ],
            consejo: 'Podés volver a Productos en 1 clic desde la primera pestaña superior.',
            tour: [
                { selector: '.iris-subnav-wrap', titulo: 'Pestañas del área', texto: 'Cambiá de vista dentro del mismo módulo en un clic (en tablet/celular se abre desde el selector desplegable).' },
                { selector: '#herramientas', titulo: 'Buscador y filtros', texto: 'Presioná F2 para escribir al instante; los selectores aplican el filtro automáticamente.' },
                { selector: '.grilla > article.tarjeta', titulo: 'Fichas de catálogo', texto: 'Cada tarjeta permite ver el detalle completo, editar o dar de baja lógica.' }
            ]
        },
        {
            coincide: function (p) { return p.indexOf('/compras') === 0; },
            eyebrow: 'Compras y Gastos',
            titulo: 'Cómo registrar compras, órdenes y egresos',
            resumen: 'Controlá lo que pedís a proveedores, recibí facturas con control de lotes y seguí tus vencimientos de pago.',
            pasos: [
                'Usá las pestañas superiores para alternar entre Compras/Recepción, Órdenes de compra y Egresos/Pagos.',
                'Al recibir mercadería, cargá cantidades y vencimientos para que el stock y los lotes queden actualizados.',
                'En Egresos revisá qué facturas vencen pronto y registrá los pagos para mantener el flujo al día.'
            ],
            consejo: 'Si una orden llega con diferencias, queda marcada para revisarla con el proveedor sin trabar el ingreso de lo recibido.',
            tour: [
                { selector: '.iris-subnav-wrap', titulo: 'Flujo de compras', texto: 'Movete entre recepción de mercadería, órdenes y pagos desde las pestañas superiores.' },
                { selector: '#herramientas, .filtro-estado', titulo: 'Filtros rápidos', texto: 'Encontrá comprobantes o pagos pendientes por fecha, proveedor o estado.' }
            ]
        }
    ];

    var PASOS_PUESTA_EN_MARCHA = [
        {
            num: 1,
            titulo: 'Configurar datos y reglas del local',
            detalle: 'Completá CUIT, domicilio, teléfono, tolerancia de arqueo y redondeo.',
            url: '/negocio/parametros/',
            etiqueta: 'Ir a Ajustes del local'
        },
        {
            num: 2,
            titulo: 'Revisar cajas y medios de pago',
            detalle: 'Verificá tus cajas físicas (Cafetería, Minisuper) y recargos por tarjeta/QR.',
            url: '/ventas/cajas_registradoras/',
            etiqueta: 'Ver cajas'
        },
        {
            num: 3,
            titulo: 'Cargar productos, marcas y proveedores',
            detalle: 'Cargá manualmente o importá tu planilla de productos con costos y precios.',
            url: '/stock/productos/',
            etiqueta: 'Ir a Productos'
        },
        {
            num: 4,
            titulo: 'Dar de alta a tu equipo y permisos',
            detalle: 'Creá usuarios Operadores y Encargados con sus claves provisorias.',
            url: '/usuarios/',
            etiqueta: 'Ir a Usuarios'
        },
        {
            num: 5,
            titulo: 'Abrir turno de caja y empezar a vender',
            detalle: 'Vinculá este equipo a su cajón, cargá el fondo inicial y pasá al Mostrador.',
            url: '/ventas/caja/',
            etiqueta: 'Ir a Caja'
        }
    ];

    var practicaActiva = false;
    var guiaTourActual = null;
    var tourPasosActivos = [];
    var tourIndice = 0;
    var tourElementoActual = null;
    var tourPasoCtx = {};
    var tourObservador = null;
    var tourAvanceTimer = null;
    var tourVerificandoAvance = false;

    function obtenerGuiaActual() {
        var ruta = window.location.pathname || '/';
        for (var i = 0; i < GUIAS_PANTALLA.length; i++) {
            if (GUIAS_PANTALLA[i].coincide(ruta)) {
                return GUIAS_PANTALLA[i];
            }
        }
        var h1 = document.querySelector('#contenido h1');
        var tituloH1 = h1 ? h1.textContent.trim() : 'Esta pantalla';
        return {
            eyebrow: 'Guía rápida · Cafeto',
            titulo: 'Cómo usar ' + tituloH1,
            resumen: 'Consultá, filtrá y gestioná los registros de esta sección con el mismo esquema de todo Cafeto.',
            pasos: [
                'Usá el buscador (tecla F2) y los filtros superiores para encontrar rápido lo que necesitás.',
                'Las pestañas soldadas arriba te permiten cambiar entre pantallas del mismo módulo sin volver al menú.',
                'Las acciones delicadas siempre te piden confirmación antes de guardar o dar de baja.'
            ],
            consejo: 'Presioná Ctrl+K para buscar cualquier pantalla por nombre o F1 para abrir esta ayuda y ver todos los atajos.',
            tour: [
                { selector: '.iris-subnav-wrap', titulo: 'Sub-pestañas del módulo', texto: 'Alterná entre las pantallas relacionadas sin perder contexto.' },
                { selector: '#herramientas', titulo: 'Búsqueda y filtros', texto: 'Presioná F2 para enfocar el buscador al instante.' },
                { selector: '.encabezado .acciones', titulo: 'Acciones principales', texto: 'Desde acá creás registros nuevos o exportás lo que estás viendo.' }
            ]
        };
    }

    function esVisibleEnPantalla(el) {
        if (!el || !el.isConnected) { return false; }
        var rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    }

    function obtenerNodoPaso(paso) {
        if (!paso) { return null; }
        if (typeof paso.obtenerNodo === 'function') {
            return paso.obtenerNodo();
        }
        if (paso.selector) {
            return document.querySelector(paso.selector);
        }
        return null;
    }

    function resolverPasosTour(guia) {
        var definiciones = typeof guia.obtenerTour === 'function' ? guia.obtenerTour() : (guia.tour || []);
        var lista = [];
        definiciones.forEach(function (paso) {
            if (paso.dinamico) {
                lista.push(paso);
                return;
            }
            var nodo = obtenerNodoPaso(paso);
            if (esVisibleEnPantalla(nodo)) {
                lista.push(paso);
            }
        });
        return lista;
    }

    function seleccionarTabAyuda(nombre) {
        var destino = (nombre === 'pasos' || nombre === 'atajos') ? nombre : 'pantalla';
        var mapa = [
            { clave: 'pantalla', tab: document.getElementById('irisAyudaTabPantalla'), panel: document.getElementById('irisAyudaPanelPantalla') },
            { clave: 'pasos', tab: document.getElementById('irisAyudaTabPasos'), panel: document.getElementById('irisAyudaPanelPasos') },
            { clave: 'atajos', tab: document.getElementById('irisAyudaTabAtajos'), panel: document.getElementById('irisAyudaPanelAtajos') }
        ];
        mapa.forEach(function (item) {
            var activo = item.clave === destino;
            if (item.tab) {
                item.tab.setAttribute('aria-selected', activo ? 'true' : 'false');
                item.tab.tabIndex = activo ? 0 : -1;
            }
            if (item.panel) {
                item.panel.hidden = !activo;
            }
        });
    }

    function pintarModalAyuda() {
        var guia = obtenerGuiaActual();
        var eyebrow = document.getElementById('irisAyudaEyebrow');
        var titulo = document.getElementById('irisAyudaTitulo');
        var resumen = document.getElementById('irisAyudaResumen');
        var bloqueo = document.getElementById('irisAyudaBloqueo');
        var bloqueoTxt = document.getElementById('irisAyudaBloqueoTexto');
        var lista = document.getElementById('irisAyudaLista');
        var consejo = document.getElementById('irisAyudaConsejo');
        var btnTour = document.getElementById('irisAyudaIniciarTour');
        var btnTourTxt = document.getElementById('irisAyudaIniciarTourTexto');
        var seguroNota = document.getElementById('irisAyudaSeguroNota');
        var roadmap = document.getElementById('irisAyudaRoadmap');

        if (eyebrow) { eyebrow.textContent = guia.eyebrow; }
        if (titulo) { titulo.textContent = guia.titulo; }
        if (resumen) { resumen.textContent = guia.resumen; }

        if (lista) {
            lista.textContent = '';
            guia.pasos.forEach(function (txt) {
                var li = document.createElement('li');
                li.textContent = txt;
                lista.appendChild(li);
            });
        }

        if (consejo) {
            consejo.textContent = '';
            if (guia.consejo) {
                var ic = document.createElement('i');
                ic.className = 'bi bi-lightbulb-fill';
                ic.setAttribute('aria-hidden', 'true');
                var sp = document.createElement('span');
                sp.textContent = guia.consejo;
                consejo.appendChild(ic);
                consejo.appendChild(sp);
                consejo.hidden = false;
            } else {
                consejo.hidden = true;
            }
        }

        var pasosDisponibles = resolverPasosTour(guia);
        var tieneInteractivos = pasosDisponibles.some(function (p) { return Boolean(p.espera && p.condicion); });
        var chequeo = typeof guia.verificarInicio === 'function' ? guia.verificarInicio() : { ok: true };

        if (bloqueo && bloqueoTxt) {
            if (!chequeo.ok && chequeo.motivo) {
                bloqueoTxt.textContent = chequeo.motivo;
                bloqueo.hidden = false;
            } else {
                bloqueoTxt.textContent = '';
                bloqueo.hidden = true;
            }
        }

        if (btnTour) {
            btnTour.hidden = pasosDisponibles.length === 0;
            btnTour.disabled = !chequeo.ok;
            if (btnTourTxt) {
                btnTourTxt.textContent = (tieneInteractivos && guia.etiquetaPractica)
                    ? guia.etiquetaPractica
                    : 'Iniciar recorrido guiado';
            }
        }

        if (seguroNota) {
            seguroNota.hidden = !(pasosDisponibles.length > 0 && tieneInteractivos);
        }

        if (roadmap) {
            var permitidas = {};
            recogerPantallasDelMenu().forEach(function (p) { permitidas[p.href] = p.enlace; });
            var rutaActual = window.location.pathname || '/';
            roadmap.textContent = '';

            PASOS_PUESTA_EN_MARCHA.forEach(function (paso) {
                var li = document.createElement('li');
                li.className = 'iris-ayuda-roadmap-item' + (rutaActual.indexOf(paso.url) === 0 ? ' is-actual' : '');

                var badge = document.createElement('span');
                badge.className = 'iris-ayuda-roadmap-num';
                badge.textContent = String(paso.num);

                var cuerpo = document.createElement('div');
                cuerpo.className = 'iris-ayuda-roadmap-txt';
                var tit = document.createElement('strong');
                tit.textContent = paso.titulo;
                var det = document.createElement('span');
                det.textContent = paso.detalle;
                cuerpo.appendChild(tit);
                cuerpo.appendChild(det);

                li.appendChild(badge);
                li.appendChild(cuerpo);

                var enlaceMenu = permitidas[paso.url];
                if (enlaceMenu) {
                    var a = document.createElement('a');
                    a.className = 'btn btn-sm btn-secundario iris-ayuda-roadmap-ir';
                    a.href = paso.url;
                    a.textContent = paso.etiqueta;
                    a.addEventListener('click', function (ev) {
                        var dlg = document.getElementById('irisModalAyuda');
                        if (dlg && dlg.open) { dlg.close(); }
                        if (enlaceMenu.isConnected) {
                            ev.preventDefault();
                            enlaceMenu.click();
                        }
                    });
                    li.appendChild(a);
                }

                roadmap.appendChild(li);
            });
        }
    }

    function abrirModalAyuda(pestana) {
        var dialogo = document.getElementById('irisModalAyuda');
        if (!dialogo) { return; }
        cerrarTour(false);
        cerrarOtrosDialogos(dialogo);
        pintarModalAyuda();
        seleccionarTabAyuda(pestana || 'pantalla');
        if (!dialogo.open) {
            dialogo.showModal();
        }
    }

    function limpiarResaltadoTour() {
        if (tourElementoActual) {
            tourElementoActual.classList.remove('iris-tour-destacado');
            tourElementoActual = null;
        }
    }

    function limpiarEsperaPaso() {
        if (tourObservador) {
            tourObservador.disconnect();
            tourObservador = null;
        }
        if (tourAvanceTimer) {
            window.clearTimeout(tourAvanceTimer);
            tourAvanceTimer = null;
        }
        tourVerificandoAvance = false;
    }

    function cerrarTour(completada) {
        var estabaActiva = practicaActiva;
        var guiaAnterior = guiaTourActual;

        limpiarEsperaPaso();
        limpiarResaltadoTour();
        practicaActiva = false;
        guiaTourActual = null;
        tourPasosActivos = [];
        tourIndice = 0;
        tourPasoCtx = {};
        document.documentElement.classList.remove('iris-practica-activa');

        var caja = document.getElementById('irisTourCaja');
        if (caja) { caja.hidden = true; }

        if (estabaActiva && guiaAnterior && typeof guiaAnterior.limpiar === 'function') {
            try {
                guiaAnterior.limpiar();
            } catch (e) { /* limpieza tolerante */ }
        }

        if (estabaActiva && completada && guiaAnterior && guiaAnterior.mensajeFin) {
            encolarAviso(guiaAnterior.mensajeFin, 'exito');
        }
    }

    function rectangulosSeSolapan(a, b) {
        return !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);
    }

    function posicionarCajaTour(nodo, caja) {
        if (!nodo || !caja) { return; }
        if (window.innerWidth < 640) {
            caja.style.top = '';
            caja.style.left = '';
            return;
        }
        var rect = nodo.getBoundingClientRect();
        var anchoCaja = caja.offsetWidth || 350;
        var altoCaja = caja.offsetHeight || 210;
        var margen = 14;
        var vw = window.innerWidth;
        var vh = window.innerHeight;

        var candidatos = [
            // 1. Abajo del elemento
            {
                top: rect.bottom + margen,
                left: Math.max(margen, Math.min(vw - anchoCaja - margen, rect.left))
            },
            // 2. Arriba del elemento
            {
                top: rect.top - altoCaja - margen,
                left: Math.max(margen, Math.min(vw - anchoCaja - margen, rect.left))
            },
            // 3. A la derecha del elemento (ideal para #catalogo en Mostrador)
            {
                top: Math.max(76, Math.min(vh - altoCaja - margen, rect.top + 16)),
                left: rect.right + margen
            },
            // 4. A la izquierda del elemento (ideal para #ticket / #irisBilletes en Mostrador)
            {
                top: Math.max(76, Math.min(vh - altoCaja - margen, rect.top - 10)),
                left: rect.left - anchoCaja - margen
            }
        ];

        var elegido = null;
        for (var i = 0; i < candidatos.length; i++) {
            var c = candidatos[i];
            var dentro = c.top >= margen && (c.top + altoCaja) <= (vh - margen) &&
                         c.left >= margen && (c.left + anchoCaja) <= (vw - margen);
            if (!dentro) { continue; }
            var cajaRect = {
                top: c.top,
                left: c.left,
                right: c.left + anchoCaja,
                bottom: c.top + altoCaja
            };
            if (!rectangulosSeSolapan(cajaRect, rect)) {
                elegido = c;
                break;
            }
        }

        if (!elegido) {
            elegido = {
                top: Math.max(margen, vh - altoCaja - margen),
                left: Math.max(margen, vw - anchoCaja - margen)
            };
        }

        caja.style.top = Math.round(elegido.top) + 'px';
        caja.style.left = Math.round(elegido.left) + 'px';
    }

    function evaluarPasoInteractivo() {
        if (!practicaActiva || tourVerificandoAvance) { return; }
        var paso = tourPasosActivos[tourIndice];
        if (!paso || typeof paso.condicion !== 'function') { return; }

        if (paso.requiereModal) {
            var m = document.querySelector(paso.requiereModal);
            if (!m || !m.classList.contains('show')) {
                cerrarTour(false);
                return;
            }
        }

        var cumplido = false;
        try {
            cumplido = Boolean(paso.condicion(tourPasoCtx));
        } catch (e) {
            cumplido = false;
        }
        if (!cumplido) { return; }

        tourVerificandoAvance = true;
        if (tourObservador) {
            tourObservador.disconnect();
            tourObservador = null;
        }

        var esperaEl = document.getElementById('irisTourEspera');
        var esperaTxt = document.getElementById('irisTourEsperaTexto');
        if (esperaEl) {
            esperaEl.classList.add('is-ok');
            var ic = esperaEl.querySelector('i');
            if (ic) { ic.className = 'bi bi-check-circle-fill'; }
            if (esperaTxt) { esperaTxt.textContent = '¡Bien! Avanzando…'; }
        }

        var pasoActualIdx = tourIndice;
        tourAvanceTimer = window.setTimeout(function () {
            tourAvanceTimer = null;
            if (!practicaActiva || tourIndice !== pasoActualIdx) { return; }
            if (pasoActualIdx >= tourPasosActivos.length - 1) {
                cerrarTour(true);
            } else {
                mostrarPasoTour(pasoActualIdx + 1);
            }
        }, 420);
    }

    function mostrarPasoTour(indice) {
        var caja = document.getElementById('irisTourCaja');
        if (!caja || !tourPasosActivos.length) { cerrarTour(false); return; }
        if (indice < 0 || indice >= tourPasosActivos.length) { cerrarTour(false); return; }

        limpiarEsperaPaso();
        limpiarResaltadoTour();

        tourIndice = indice;
        tourPasoCtx = {};
        var paso = tourPasosActivos[tourIndice];

        if (typeof paso.alEntrar === 'function') {
            try { paso.alEntrar(tourPasoCtx); } catch (e) { /* ignorado */ }
        }

        var nodo = obtenerNodoPaso(paso);
        if (!nodo || !esVisibleEnPantalla(nodo)) {
            if (tourIndice < tourPasosActivos.length - 1) {
                mostrarPasoTour(tourIndice + 1);
                return;
            }
            cerrarTour(true);
            return;
        }

        tourElementoActual = nodo;
        tourElementoActual.classList.add('iris-tour-destacado');
        try {
            tourElementoActual.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
        } catch (e) {
            tourElementoActual.scrollIntoView();
        }

        var textoEspera = typeof paso.espera === 'function' ? paso.espera(tourPasoCtx) : (paso.espera || '');
        var esInteractivo = Boolean(textoEspera && typeof paso.condicion === 'function');
        var esUltimo = tourIndice === tourPasosActivos.length - 1;

        var pasoTxt = document.getElementById('irisTourPaso');
        var titTxt = document.getElementById('irisTourTitulo');
        var descTxt = document.getElementById('irisTourTexto');
        var esperaEl = document.getElementById('irisTourEspera');
        var esperaTxt = document.getElementById('irisTourEsperaTexto');
        var btnAnt = document.getElementById('irisTourAnterior');
        var btnSaltar = document.getElementById('irisTourSaltar');
        var btnSig = document.getElementById('irisTourSiguiente');

        if (pasoTxt) { pasoTxt.textContent = 'Paso ' + (tourIndice + 1) + ' de ' + tourPasosActivos.length; }
        if (titTxt) { titTxt.textContent = paso.titulo; }
        if (descTxt) { descTxt.textContent = paso.texto; }

        if (esperaEl) {
            esperaEl.classList.remove('is-ok');
            var ic = esperaEl.querySelector('i');
            if (ic) { ic.className = 'bi bi-hand-index-thumb-fill'; }
            if (esperaTxt) { esperaTxt.textContent = textoEspera || 'Esperando que hagas esta acción…'; }
            esperaEl.hidden = !esInteractivo;
        }

        if (btnAnt) {
            btnAnt.disabled = tourIndice === 0;
            btnAnt.hidden = tourIndice === 0
                || (esInteractivo && Boolean(paso.dinamico))
                || (esUltimo && Boolean(paso.botonFinal));
        }
        if (btnSaltar) {
            btnSaltar.hidden = !esInteractivo;
        }
        if (btnSig) {
            btnSig.hidden = esInteractivo;
            if (esUltimo) {
                btnSig.textContent = paso.botonFinal || 'Finalizar';
            } else {
                btnSig.textContent = 'Siguiente';
            }
        }

        caja.hidden = false;
        window.requestAnimationFrame(function () {
            posicionarCajaTour(tourElementoActual, caja);
            window.setTimeout(function () {
                if (tourElementoActual && !caja.hidden) {
                    posicionarCajaTour(tourElementoActual, caja);
                }
            }, 260);
        });

        if (esInteractivo) {
            if (window.MutationObserver) {
                var raizObs = document.getElementById('contenido') || document.body;
                tourObservador = new MutationObserver(evaluarPasoInteractivo);
                tourObservador.observe(raizObs, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'open', 'aria-pressed', 'disabled', 'hidden'] });
            }
            evaluarPasoInteractivo();
        }
    }

    function iniciarTourPantalla() {
        var guia = obtenerGuiaActual();
        if (typeof guia.verificarInicio === 'function') {
            var chequeo = guia.verificarInicio();
            if (!chequeo.ok) {
                pintarModalAyuda();
                return;
            }
        }
        var pasos = resolverPasosTour(guia);
        if (!pasos.length) { return; }

        var dlg = document.getElementById('irisModalAyuda');
        if (dlg && dlg.open) { dlg.close(); }

        if (typeof guia.alIniciar === 'function') {
            try { guia.alIniciar(); } catch (e) { /* ignorado */ }
        }

        practicaActiva = true;
        guiaTourActual = guia;
        tourPasosActivos = pasos;
        document.documentElement.classList.add('iris-practica-activa');
        mostrarPasoTour(0);
    }

    /* ----------------------------------------------------------------------
       5. Fallback light-dismiss para <dialog closedby="any"> (Safari)
       ---------------------------------------------------------------------- */
    function conectarLightDismissDialogos() {
        if ('closedBy' in HTMLDialogElement.prototype) { return; }

        function estaAfuera(evento, dialogo) {
            var rect = dialogo.getBoundingClientRect();
            return evento.clientX < rect.left || evento.clientX > rect.right ||
                   evento.clientY < rect.top || evento.clientY > rect.bottom;
        }

        document.querySelectorAll('dialog.iris-dialog[closedby="any"]').forEach(function (dialogo) {
            if (dialogo.dataset.irisLightDismiss === '1') { return; }
            dialogo.dataset.irisLightDismiss = '1';
            var presionadoAfuera = false;
            dialogo.addEventListener('pointerdown', function (e) {
                presionadoAfuera = estaAfuera(e, dialogo);
            });
            dialogo.addEventListener('pointerup', function (e) {
                if (presionadoAfuera && estaAfuera(e, dialogo)) {
                    dialogo.close();
                }
                presionadoAfuera = false;
            });
        });
    }

    /* ----------------------------------------------------------------------
       6. Accesibilidad del drawer lateral en Tablet/Celular + estado de red
       ---------------------------------------------------------------------- */
    function sincronizarDrawer(abierto) {
        var contenido = document.querySelector('.contenido');
        var esMovil = window.innerWidth < 1100;
        var botonAbrir = document.querySelector('[data-lateral-abrir]');
        if (botonAbrir) {
            botonAbrir.setAttribute('aria-expanded', abierto && esMovil ? 'true' : 'false');
        }
        if (contenido && 'inert' in contenido) {
            contenido.inert = Boolean(abierto && esMovil);
        }
    }

    function cerrarDrawer() {
        var lateral = document.querySelector('.lateral');
        var velo = document.querySelector('.velo');
        if (lateral && lateral.classList.contains('abierta')) {
            lateral.classList.remove('abierta');
            if (velo) { velo.classList.remove('visible'); }
            sincronizarDrawer(false);
            var botonAbrir = document.querySelector('[data-lateral-abrir]');
            if (botonAbrir && window.innerWidth < 1100) {
                botonAbrir.focus();
            }
        }
    }

    function sincronizarEstadoRedRail() {
        var enLinea = navigator.onLine;
        var estado = document.getElementById('irisRailEstado');
        if (!estado) { return; }
        estado.classList.toggle('sin-red', !enLinea);
        var texto = estado.querySelector('.js-rail-red-texto');
        if (texto) {
            texto.textContent = enLinea ? 'Cafeto · NAS Local' : 'Sin conexión';
        }
    }

    function evaluarDesbordeChips() {
        document.querySelectorAll('.barra .chips-cajas').forEach(function (nav) {
            nav.classList.toggle('desborda-chips', nav.scrollWidth > nav.clientWidth + 2);
        });
    }

    /* ----------------------------------------------------------------------
       6b. Popover flotante de sub-pantallas para el Rail PC (modo "Solo 9 áreas")
       ---------------------------------------------------------------------- */
    var soportaPopover = typeof HTMLElement !== 'undefined' &&
        Object.prototype.hasOwnProperty.call(HTMLElement.prototype, 'popover');
    var grupoPopoverAbierto = null;

    function popoverAbierto(el) {
        if (!el) { return false; }
        if (soportaPopover) {
            try { return el.matches(':popover-open'); } catch (e) { /* fallback */ }
        }
        return el.classList.contains('is-open');
    }

    function cerrarAreaPopover() {
        var pop = document.getElementById('irisAreaPopover');
        if (!pop) { return; }
        grupoPopoverAbierto = null;
        if (soportaPopover) {
            try {
                if (pop.matches(':popover-open')) { pop.hidePopover(); }
            } catch (e) { /* nada */ }
        }
        pop.classList.remove('is-open');
    }

    function abrirAreaPopover(detalles, resumen) {
        var pop = document.getElementById('irisAreaPopover');
        var tituloEl = document.getElementById('irisAreaPopoverTitulo');
        var listaEl = document.getElementById('irisAreaPopoverLista');
        if (!pop || !listaEl || !detalles) { return; }

        var claveGrupo = detalles.dataset.grupo || '';
        if (popoverAbierto(pop) && grupoPopoverAbierto === claveGrupo) {
            cerrarAreaPopover();
            return;
        }

        var enlaces = detalles.querySelectorAll('a.lateral-link');
        if (!enlaces.length) { return; }

        var spanTitulo = resumen.querySelector('span');
        var nombreArea = spanTitulo ? spanTitulo.textContent.trim() : '';
        var iconoAreaEl = resumen.querySelector('.bi');
        var iconoArea = '';
        if (iconoAreaEl) {
            iconoAreaEl.classList.forEach(function (c) {
                if (c.indexOf('bi-') === 0) { iconoArea = c; }
            });
        }

        if (tituloEl) {
            tituloEl.textContent = '';
            if (iconoArea) {
                var icTit = document.createElement('i');
                icTit.className = 'bi ' + iconoArea;
                icTit.setAttribute('aria-hidden', 'true');
                tituloEl.appendChild(icTit);
            }
            var txtTit = document.createElement('span');
            txtTit.textContent = nombreArea;
            tituloEl.appendChild(txtTit);
        }

        listaEl.textContent = '';
        enlaces.forEach(function (enlace) {
            var activo = enlace.classList.contains('activo');
            var href = enlace.getAttribute('href');
            var spanTxt = enlace.querySelector('span');
            var texto = (spanTxt ? spanTxt.textContent : enlace.textContent || '').trim();
            var icOrigen = enlace.querySelector('i');
            var icono = 'bi-chevron-right';
            if (icOrigen) {
                icOrigen.classList.forEach(function (c) {
                    if (c.indexOf('bi-') === 0) { icono = c; }
                });
            }

            var a = document.createElement('a');
            a.className = 'iris-area-popover-item' + (activo ? ' is-active' : '');
            a.href = href;
            if (activo) { a.setAttribute('aria-current', 'page'); }

            var ic = document.createElement('i');
            ic.className = 'bi ' + icono;
            ic.setAttribute('aria-hidden', 'true');
            var sp = document.createElement('span');
            sp.textContent = texto;

            a.appendChild(ic);
            a.appendChild(sp);

            a.addEventListener('click', function (ev) {
                cerrarAreaPopover();
                if (enlace && enlace.isConnected) {
                    ev.preventDefault();
                    enlace.click();
                }
            });

            listaEl.appendChild(a);
        });

        grupoPopoverAbierto = claveGrupo;

        if (soportaPopover) {
            try { pop.showPopover(); } catch (e) { pop.classList.add('is-open'); }
        } else {
            pop.classList.add('is-open');
        }

        // Posicionar pegado a la derecha del Rail, con clamp para no salirse del viewport
        var lateral = document.querySelector('.lateral');
        var rectLateral = lateral ? lateral.getBoundingClientRect() : { right: 232 };
        var rectRow = resumen.getBoundingClientRect();
        var altoPop = pop.offsetHeight || 240;
        var topIdeal = Math.max(12, Math.min(window.innerHeight - altoPop - 12, rectRow.top - 8));
        var leftIdeal = Math.min(window.innerWidth - (pop.offsetWidth || 260) - 12, rectLateral.right + 10);

        pop.style.top = Math.round(topIdeal) + 'px';
        pop.style.left = Math.round(Math.max(12, leftIdeal)) + 'px';
    }

    /* ----------------------------------------------------------------------
       6c. Mostrador · Billetes rápidos de efectivo (sin duplicar cálculos)
       ---------------------------------------------------------------------- */
    var observadorMostrador = null;

    function desconectarMostrador() {
        if (observadorMostrador) {
            observadorMostrador.disconnect();
            observadorMostrador = null;
        }
    }

    function leerMontoFormateado(texto) {
        if (!texto) { return null; }
        var parte = String(texto).split('(')[0];
        var m = parte.match(/[\d.]+(?:,\d+)?/);
        if (!m) { return null; }
        var num = Number(m[0].replace(/\./g, '').replace(',', '.'));
        return isFinite(num) && num > 0 ? num : null;
    }

    function leerMontoInput(texto) {
        if (!texto || !String(texto).trim()) { return null; }
        var valor = (window.Cafeto && window.Cafeto.leerNumero)
            ? window.Cafeto.leerNumero(texto, 2)
            : Number(String(texto).trim().replace(',', '.'));
        return typeof valor === 'number' && isFinite(valor) && valor > 0 ? valor : null;
    }

    function formatearMontoParaInput(num) {
        var redondeado = Math.round(num * 100) / 100;
        if (Math.abs(redondeado - Math.round(redondeado)) < 0.001) {
            return String(Math.round(redondeado));
        }
        return redondeado.toFixed(2).replace('.', ',');
    }

    function obtenerExactoEfectivo(campoEfectivo) {
        if (!campoEfectivo) { return null; }
        var fila = campoEfectivo.parentElement;
        if (fila) {
            var importeNodo = fila.querySelector('.small.text-secondary');
            var montoFila = leerMontoFormateado(importeNodo ? importeNodo.textContent : '');
            if (montoFila !== null) { return montoFila; }
        }
        var pagos = document.getElementById('pagos');
        if (pagos && pagos.children.length === 1) {
            var totalNodo = document.getElementById('tTotal');
            return leerMontoFormateado(totalNodo ? totalNodo.textContent : '');
        }
        return null;
    }

    function sincronizarBilletesRapidos() {
        var pagos = document.getElementById('pagos');
        if (!pagos) { return; }

        var campoEfectivo = pagos.querySelector('input[aria-label="Efectivo recibido"]');
        if (campoEfectivo && campoEfectivo.placeholder !== 'Exacto (o monto recibido)') {
            campoEfectivo.placeholder = 'Exacto (o monto recibido)';
        }

        var barra = document.getElementById('irisBilletes');
        if (!barra) { return; }
        var ticketVacio = document.getElementById('ticketVacio');
        var hayItems = Boolean(ticketVacio && ticketVacio.classList.contains('d-none'));
        var mostrar = Boolean(campoEfectivo && hayItems);
        barra.hidden = !mostrar;
        if (!mostrar) { return; }

        var actualNum = leerMontoInput(campoEfectivo.value);
        var exactoNum = obtenerExactoEfectivo(campoEfectivo);

        barra.querySelectorAll('[data-iris-billete]').forEach(function (btn) {
            var tipo = btn.dataset.irisBillete;
            var activo = false;
            if (tipo === 'exacto') {
                activo = actualNum !== null && exactoNum !== null && Math.abs(actualNum - exactoNum) < 0.01;
            } else {
                var valorBillete = Number(tipo);
                activo = actualNum !== null && Math.abs(actualNum - valorBillete) < 0.01;
            }
            btn.setAttribute('aria-pressed', activo ? 'true' : 'false');
        });
    }

    function leerCantidadesTicketMostrador(mostrador) {
        var mapa = Object.create(null);
        if (!mostrador || !mostrador.dataset.turno) { return mapa; }
        try {
            var crudo = window.sessionStorage.getItem('cafeto.ticket.' + mostrador.dataset.turno);
            if (!crudo) { return mapa; }
            var datos = JSON.parse(crudo);
            if (!datos || !Array.isArray(datos.items)) { return mapa; }
            datos.items.forEach(function (item) {
                var id = String(item.producto || '');
                var cant = Number(String(item.cantidad || '0').replace(',', '.'));
                if (id && isFinite(cant) && cant > 0) {
                    mapa[id] = (mapa[id] || 0) + cant;
                }
            });
        } catch (e) { /* storage bloqueado */ }
        return mapa;
    }

    function formatearCantidadBadge(valor) {
        var redondeado = Math.round(valor * 1000) / 1000;
        if (Math.abs(redondeado - Math.round(redondeado)) < 0.0005) {
            return String(Math.round(redondeado));
        }
        return String(redondeado).replace('.', ',');
    }

    function sincronizarCantidadesCatalogo() {
        var mostrador = document.getElementById('mostrador');
        var catalogo = document.getElementById('catalogo');
        if (!mostrador || !catalogo) { return; }

        var cantidades = leerCantidadesTicketMostrador(mostrador);
        catalogo.querySelectorAll('.producto-boton[data-producto]').forEach(function (btn) {
            var id = String(btn.dataset.producto || '');
            var cant = cantidades[id] || 0;
            var badge = btn.querySelector('.iris-prod-cant');
            if (cant > 0) {
                var texto = formatearCantidadBadge(cant);
                btn.classList.add('en-ticket');
                if (!badge) {
                    badge = document.createElement('span');
                    badge.className = 'iris-prod-cant';
                    btn.appendChild(badge);
                }
                var etiqueta = '×' + texto;
                if (badge.textContent !== etiqueta) {
                    badge.textContent = etiqueta;
                }
                badge.setAttribute('aria-label', 'En el ticket: ' + texto);
            } else {
                btn.classList.remove('en-ticket');
                if (badge) { badge.remove(); }
            }
        });
    }

    function sincronizarMostrador() {
        sincronizarBilletesRapidos();
        sincronizarCantidadesCatalogo();
    }

    function conectarMostrador() {
        desconectarMostrador();
        var mostrador = document.getElementById('mostrador');
        var pagos = document.getElementById('pagos');
        if (!mostrador || !pagos) { return; }

        sincronizarMostrador();
        if (window.MutationObserver) {
            observadorMostrador = new MutationObserver(sincronizarMostrador);
            observadorMostrador.observe(pagos, { childList: true });
            var renglones = document.getElementById('renglones');
            if (renglones) {
                observadorMostrador.observe(renglones, { childList: true });
            }
            var catalogo = document.getElementById('catalogo');
            if (catalogo) {
                observadorMostrador.observe(catalogo, { childList: true });
            }
        }
    }

    /* ----------------------------------------------------------------------
       6d. Guardrails de seguridad durante la Práctica interactiva (0 escrituras)
       ---------------------------------------------------------------------- */
    var SELECTOR_BLOQUEO_PRACTICA = '#cobrar, #remotaPedir, #ajusteConfirmar, #ajusteRemotaPedir, #clienteGuardar, [hx-post], [hx-delete], [hx-put], [hx-patch]';

    document.addEventListener('click', function (evento) {
        if (!practicaActiva) { return; }
        if (evento.target && evento.target.closest && evento.target.closest('#irisTourCaja')) { return; }
        var prohibido = evento.target && evento.target.closest && evento.target.closest(SELECTOR_BLOQUEO_PRACTICA);
        if (prohibido) {
            evento.preventDefault();
            evento.stopPropagation();
            evento.stopImmediatePropagation();
            encolarAviso('Modo práctica: acción bloqueada para no guardar datos.', 'info');
        }
    }, true);

    document.addEventListener('submit', function (evento) {
        if (!practicaActiva) { return; }
        var form = evento.target;
        if (!form || form.tagName !== 'FORM') { return; }
        var metodo = (form.getAttribute('method') || 'get').toLowerCase();
        if (metodo !== 'get' && metodo !== 'dialog') {
            evento.preventDefault();
            evento.stopPropagation();
            evento.stopImmediatePropagation();
            encolarAviso('Modo práctica: el guardado está bloqueado.', 'info');
        }
    }, true);

    document.addEventListener('keydown', function (evento) {
        if (!practicaActiva) { return; }
        if (evento.key === 'F9') {
            evento.preventDefault();
            evento.stopPropagation();
            evento.stopImmediatePropagation();
            encolarAviso('Modo práctica: F9 está bloqueado.', 'info');
            return;
        }
        if (evento.key === 'Escape') {
            var tourCaja = document.getElementById('irisTourCaja');
            if (tourCaja && !tourCaja.hidden) {
                evento.preventDefault();
                evento.stopPropagation();
                cerrarTour(false);
            }
        }
    }, true);

    document.addEventListener('shown.bs.modal', function () {
        if (practicaActiva) {
            window.requestAnimationFrame(evaluarPasoInteractivo);
        }
    });

    document.addEventListener('hidden.bs.modal', function (evento) {
        if (!practicaActiva) { return; }
        var paso = tourPasosActivos[tourIndice];
        if (paso && paso.requiereModal && evento.target && evento.target.matches && evento.target.matches(paso.requiereModal)) {
            cerrarTour(false);
        } else {
            evaluarPasoInteractivo();
        }
    });

    /* ----------------------------------------------------------------------
       6e. Visor modal nativo de Comprobantes y Tickets (80 mm) + Print limpio
       ---------------------------------------------------------------------- */
    var RE_COMPROBANTE_80MM = /^\/(?:ventas\/(?:ventas\/\d+\/ticket|turnos\/\d+\/recaudacion|caja\/arqueo-parcial\/\d+|caja\/movimientos\/\d+\/vale|cambios\/\d+\/comprobante)|fidelizacion\/cuentas-corrientes\/recibos\/\d+)\/?$/;
    var ETIQUETAS_COMPROBANTE = ['article', 'h1', 'h2', 'h3', 'div', 'span', 'small', 'strong', 'b', 'em', 'br', 'p', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'ul', 'ol', 'li', 'hr'];
    var ATRIBUTOS_COMPROBANTE = ['class', 'colspan', 'rowspan', 'scope'];
    var SET_ETIQUETAS_COMPROBANTE = {};
    ETIQUETAS_COMPROBANTE.forEach(function (tag) { SET_ETIQUETAS_COMPROBANTE[tag] = true; });
    var SET_ATRIBUTOS_COMPROBANTE = {};
    ATRIBUTOS_COMPROBANTE.forEach(function (attr) { SET_ATRIBUTOS_COMPROBANTE[attr] = true; });

    var comprobanteAbort = null;

    function esUrlComprobante80mm(enlace) {
        if (!enlace || !enlace.getAttribute) { return false; }
        if (enlace.dataset && enlace.dataset.irisComprobanteDirecto === '1') { return false; }
        var href = enlace.getAttribute('href');
        if (!href || href === '#') { return false; }
        try {
            var url = new URL(href, window.location.origin);
            if (url.origin !== window.location.origin) { return false; }
            return RE_COMPROBANTE_80MM.test(url.pathname);
        } catch (e) {
            return false;
        }
    }

    function clonarNodoSeguroComprobante(nodo) {
        if (nodo.nodeType === 3) {
            return document.createTextNode(nodo.nodeValue || '');
        }
        if (nodo.nodeType !== 1) {
            return null;
        }
        var tag = nodo.tagName.toLowerCase();
        if (!SET_ETIQUETAS_COMPROBANTE[tag]) {
            return null;
        }
        var copia = document.createElement(tag);
        for (var i = 0; i < nodo.attributes.length; i++) {
            var attr = nodo.attributes[i];
            var nombre = attr.name.toLowerCase();
            if (SET_ATRIBUTOS_COMPROBANTE[nombre]) {
                copia.setAttribute(nombre, attr.value);
            }
        }
        for (var j = 0; j < nodo.childNodes.length; j++) {
            var hijo = clonarNodoSeguroComprobante(nodo.childNodes[j]);
            if (hijo) { copia.appendChild(hijo); }
        }
        return copia;
    }

    function insertarPapelSanitizado(contenedor, origenNodo) {
        if (!contenedor || !origenNodo) { return; }
        if (typeof Document !== 'undefined' && typeof Document.parseHTML === 'function') {
            try {
                var limpio = Document.parseHTML(origenNodo.innerHTML, {
                    sanitizer: {
                        elements: ETIQUETAS_COMPROBANTE,
                        attributes: ATRIBUTOS_COMPROBANTE
                    }
                });
                if (limpio && limpio.body) {
                    contenedor.replaceChildren.apply(contenedor, Array.prototype.slice.call(limpio.body.childNodes));
                    return;
                }
            } catch (e) { /* cae al clonado allowlist offline */ }
        }
        var frag = document.createDocumentFragment();
        for (var i = 0; i < origenNodo.childNodes.length; i++) {
            var copia = clonarNodoSeguroComprobante(origenNodo.childNodes[i]);
            if (copia) { frag.appendChild(copia); }
        }
        contenedor.replaceChildren(frag);
    }

    function limpiarImpresionComprobante() {
        document.body.classList.remove('iris-imprimiendo-comprobante');
        var host = document.getElementById('irisPrintHost');
        if (host) { host.textContent = ''; }
    }

    function imprimirComprobanteModal() {
        var papel = document.getElementById('irisComprobantePapel');
        var host = document.getElementById('irisPrintHost');
        if (!papel || papel.hidden || !papel.childNodes.length || !host) { return; }

        var copia = document.createElement('div');
        copia.className = 'iris-comprobante-papel';
        for (var i = 0; i < papel.childNodes.length; i++) {
            copia.appendChild(papel.childNodes[i].cloneNode(true));
        }
        host.replaceChildren(copia);
        document.body.classList.add('iris-imprimiendo-comprobante');
        try {
            window.print();
        } catch (e) {
            limpiarImpresionComprobante();
        }
    }

    function conectarModalComprobante() {
        var dialogo = document.getElementById('irisModalComprobante');
        if (!dialogo || dialogo.dataset.irisConectado === '1') { return; }
        dialogo.dataset.irisConectado = '1';

        dialogo.addEventListener('close', function () {
            if (comprobanteAbort) {
                try { comprobanteAbort.abort(); } catch (e) { /* nada */ }
                comprobanteAbort = null;
            }
            limpiarImpresionComprobante();
            dialogo.returnValue = '';
        });

        // Aislar focusin y teclado cuando un modal Bootstrap (#modalCobrado / #modalVenta) está debajo
        dialogo.addEventListener('focusin', function (evento) {
            evento.stopPropagation();
        });

        dialogo.addEventListener('keydown', function (evento) {
            if (evento.ctrlKey || evento.altKey || evento.metaKey) { return; }
            if (evento.key === 'p' || evento.key === 'P') {
                evento.preventDefault();
                evento.stopPropagation();
                imprimirComprobanteModal();
                return;
            }
            if (evento.key === 'Enter' || evento.key === ' ') {
                evento.stopPropagation();
            }
        });
    }

    function abrirModalComprobante(url, tituloInicial) {
        var dialogo = document.getElementById('irisModalComprobante');
        if (!dialogo) {
            window.open(url, '_blank', 'noopener');
            return;
        }
        conectarModalComprobante();
        cerrarTour(false);
        cerrarOtrosDialogos(dialogo);

        var tituloEl = document.getElementById('irisComprobanteTitulo');
        var cargandoEl = document.getElementById('irisComprobanteCargando');
        var errorEl = document.getElementById('irisComprobanteError');
        var errorTxt = document.getElementById('irisComprobanteErrorTexto');
        var papelEl = document.getElementById('irisComprobantePapel');
        var linkPestana = document.getElementById('irisComprobantePestana');
        var linkPestanaTxt = document.getElementById('irisComprobantePestanaTexto');
        var btnImprimir = document.getElementById('irisComprobanteImprimir');

        if (comprobanteAbort) {
            try { comprobanteAbort.abort(); } catch (e) { /* nada */ }
        }
        comprobanteAbort = typeof AbortController === 'function' ? new AbortController() : null;
        var senial = comprobanteAbort ? comprobanteAbort.signal : undefined;

        var tituloLimpio = String(tituloInicial || '').replace(/\s+/g, ' ').trim();
        if (tituloEl) { tituloEl.textContent = tituloLimpio || 'Comprobante'; }
        if (linkPestana) { linkPestana.href = url; }
        if (linkPestanaTxt) { linkPestanaTxt.textContent = 'Abrir en pestaña'; }
        if (cargandoEl) { cargandoEl.hidden = false; }
        if (errorEl) { errorEl.hidden = true; }
        if (papelEl) {
            papelEl.hidden = true;
            papelEl.textContent = '';
        }
        if (btnImprimir) { btnImprimir.disabled = true; }

        if (!dialogo.open) {
            dialogo.showModal();
        }

        window.fetch(url, {
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            signal: senial
        }).then(function (resp) {
            if (resp.status === 401 || (resp.redirected && resp.url && resp.url.indexOf('/login') !== -1)) {
                if (dialogo.open) { dialogo.close(); }
                window.location.assign('/login/');
                return null;
            }
            if (!resp.ok) {
                throw new Error('HTTP ' + resp.status);
            }
            return resp.text();
        }).then(function (html) {
            if (html === null) { return; }
            var parser = new window.DOMParser();
            var doc = parser.parseFromString(html, 'text/html');
            var papelOrigen = doc.querySelector('article.comprobante-papel');
            if (!papelOrigen) {
                throw new Error('Formato de comprobante no reconocido');
            }

            if (tituloEl && doc.title) {
                var partesTitulo = doc.title.split('·');
                var limpio = (partesTitulo.length > 1 ? partesTitulo.slice(0, -1).join('·') : doc.title).trim();
                if (limpio) { tituloEl.textContent = limpio; }
            }

            if (linkPestanaTxt && doc.querySelector('.acciones form')) {
                linkPestanaTxt.textContent = 'Revisar en pestaña';
            }

            insertarPapelSanitizado(papelEl, papelOrigen);
            if (cargandoEl) { cargandoEl.hidden = true; }
            if (papelEl) { papelEl.hidden = false; }
            if (btnImprimir) {
                btnImprimir.disabled = false;
                btnImprimir.focus();
            }
        }).catch(function (err) {
            if (err && err.name === 'AbortError') { return; }
            if (cargandoEl) { cargandoEl.hidden = true; }
            if (errorTxt) {
                errorTxt.textContent = 'No se pudo cargar el comprobante acá. Podés abrirlo en una pestaña aparte.';
            }
            if (errorEl) { errorEl.hidden = false; }
        });
    }

    window.addEventListener('afterprint', limpiarImpresionComprobante);

    /* ----------------------------------------------------------------------
       7. Delegación de eventos globales (0 atributos on*=)
       ---------------------------------------------------------------------- */
    document.addEventListener('click', function () {
        if (practicaActiva) {
            window.requestAnimationFrame(evaluarPasoInteractivo);
        }
    });

    document.addEventListener('click', function (evento) {
        if (evento.target.closest('[data-iris-comprobante-imprimir]')) {
            evento.preventDefault();
            imprimirComprobanteModal();
            return;
        }

        var enlaceComp = evento.target.closest('a[href]');
        if (enlaceComp &&
            evento.button === 0 &&
            !evento.ctrlKey && !evento.metaKey && !evento.shiftKey && !evento.altKey &&
            esUrlComprobante80mm(enlaceComp)) {
            evento.preventDefault();
            abrirModalComprobante(enlaceComp.href, enlaceComp.getAttribute('title') || enlaceComp.textContent);
            return;
        }

        var btnBillete = evento.target.closest('[data-iris-billete]');
        if (btnBillete) {
            evento.preventDefault();
            var campoEfectivo = document.querySelector('#pagos input[aria-label="Efectivo recibido"]');
            if (!campoEfectivo) { return; }
            var tipo = btnBillete.dataset.irisBillete;
            var nuevoValor = '';
            if (tipo === 'exacto') {
                var exacto = obtenerExactoEfectivo(campoEfectivo);
                nuevoValor = exacto !== null ? formatearMontoParaInput(exacto) : '';
            } else {
                nuevoValor = tipo;
            }
            campoEfectivo.value = nuevoValor;
            campoEfectivo.dispatchEvent(new Event('input', { bubbles: true }));
            campoEfectivo.dispatchEvent(new Event('change', { bubbles: true }));
            sincronizarBilletesRapidos();
            return;
        }

        var btnVerClave = evento.target.closest('[data-iris-ver-clave]');
        if (btnVerClave) {
            evento.preventDefault();
            var envase = btnVerClave.closest('.iris-clave-wrap');
            var campo = envase ? envase.querySelector('input') : null;
            if (campo) {
                var mostrar = campo.type === 'password';
                campo.type = mostrar ? 'text' : 'password';
                btnVerClave.setAttribute('aria-pressed', mostrar ? 'true' : 'false');
                var icOjo = btnVerClave.querySelector('i');
                if (icOjo) { icOjo.className = mostrar ? 'bi bi-eye-slash' : 'bi bi-eye'; }
                campo.focus();
            }
            return;
        }

        var btnRailColapsar = evento.target.closest('[data-iris-rail-colapsar]');
        if (btnRailColapsar) {
            evento.preventDefault();
            cerrarAreaPopover();
            var nuevoColapso = !railColapsadoActual();
            guardar(CLAVE_RAIL_COLAPSADO, nuevoColapso ? '1' : '0');
            aplicarColapsoRail(nuevoColapso);
            return;
        }

        var btnRailModo = evento.target.closest('[data-iris-rail-modo]');
        if (btnRailModo) {
            evento.preventDefault();
            cerrarAreaPopover();
            var siguienteModo = modoRailActual() === 'areas' ? 'completo' : 'areas';
            guardar(CLAVE_RAIL_MODO, siguienteModo);
            aplicarModoRail(siguienteModo);
            return;
        }

        if (evento.target.closest('[data-iris-area-popover-cerrar]')) {
            evento.preventDefault();
            cerrarAreaPopover();
            return;
        }

        // En PC (>= 1100px) en modo "Solo 9 áreas" o con el Rail contraído (< 140px):
        // con Rail contraído despliega el popover flotante; en "Solo 9 áreas" expandido,
        // clic en título va al 1° ítem y clic en la flechita ▾ abre el popover.
        var resumenArea = evento.target.closest('.lateral details.lateral-desplegable > summary');
        if (resumenArea && window.innerWidth >= 1100 && (modoRailActual() === 'areas' || railColapsadoActual())) {
            evento.preventDefault();
            var detalles = resumenArea.closest('details.lateral-desplegable');
            if (!detalles) { return; }
            if (railColapsadoActual() || evento.target.closest('[data-iris-area-popover]')) {
                abrirAreaPopover(detalles, resumenArea);
            } else {
                cerrarAreaPopover();
                var primero = detalles.querySelector('a.lateral-link');
                if (primero) { primero.click(); }
            }
            return;
        }

        // Fallback click-afuera del popover en navegadores sin Popover API nativo
        if (!soportaPopover) {
            var pop = document.getElementById('irisAreaPopover');
            if (pop && pop.classList.contains('is-open') && !pop.contains(evento.target)) {
                cerrarAreaPopover();
            }
        }

        if (evento.target.closest('[data-iris-paleta-cerrar]')) {
            evento.preventDefault();
            cerrarBuscadorNotch();
            return;
        }

        var btnPaleta = evento.target.closest('[data-iris-paleta-abrir]');
        if (btnPaleta) {
            evento.preventDefault();
            cerrarDrawer();
            abrirPaleta();
            return;
        }

        if (buscadorNotchAbierto) {
            var notchCont = document.getElementById('irisNotch');
            if (!notchCont || !notchCont.contains(evento.target)) {
                cerrarBuscadorNotch();
            }
        }

        var btnAtajos = evento.target.closest('[data-iris-atajos-abrir]');
        if (btnAtajos) {
            evento.preventDefault();
            cerrarDrawer();
            abrirModalAyuda('atajos');
            return;
        }

        var btnAyuda = evento.target.closest('[data-iris-ayuda-abrir]');
        if (btnAyuda) {
            evento.preventDefault();
            cerrarDrawer();
            abrirModalAyuda(btnAyuda.dataset.irisAyudaAbrir || 'pantalla');
            return;
        }

        var btnAyudaTab = evento.target.closest('[data-iris-ayuda-tab]');
        if (btnAyudaTab) {
            evento.preventDefault();
            seleccionarTabAyuda(btnAyudaTab.dataset.irisAyudaTab);
            return;
        }

        if (evento.target.closest('[data-iris-atajos-desde-ayuda]')) {
            evento.preventDefault();
            abrirModalAyuda('atajos');
            return;
        }

        if (evento.target.closest('[data-iris-tour-iniciar]')) {
            evento.preventDefault();
            iniciarTourPantalla();
            return;
        }

        if (evento.target.closest('[data-iris-tour-cerrar]')) {
            evento.preventDefault();
            cerrarTour(false);
            return;
        }

        if (evento.target.closest('[data-iris-tour-anterior]')) {
            evento.preventDefault();
            mostrarPasoTour(tourIndice - 1);
            return;
        }

        if (evento.target.closest('[data-iris-tour-siguiente]')) {
            evento.preventDefault();
            if (tourIndice >= tourPasosActivos.length - 1) {
                cerrarTour(true);
            } else {
                mostrarPasoTour(tourIndice + 1);
            }
            return;
        }

        var btnSubnav = evento.target.closest('[data-iris-subnav-abrir]');
        if (btnSubnav) {
            evento.preventDefault();
            abrirSubnavSheet(btnSubnav);
            return;
        }

        // Botón de tema en barra móvil/tablet -> delega al único [data-tema-alternar]
        var btnTemaMovil = evento.target.closest('[data-iris-tema-alternar]');
        if (btnTemaMovil) {
            evento.preventDefault();
            var botonPrincipal = document.querySelector('[data-tema-alternar]');
            if (botonPrincipal) { botonPrincipal.click(); }
            return;
        }

        var btnSonido = evento.target.closest('[data-iris-sonido-alternar]');
        if (btnSonido) {
            evento.preventDefault();
            var siguiente = !sonidoActivo();
            guardar(CLAVE_SONIDO, siguiente ? '1' : '0');
            actualizarBotonesSonido();
            if (siguiente) { tocarChime('exito'); }
            return;
        }

        if (evento.target.closest('[data-lateral-abrir]')) {
            window.requestAnimationFrame(function () {
                var lateral = document.querySelector('.lateral');
                var abierto = Boolean(lateral && lateral.classList.contains('abierta'));
                sincronizarDrawer(abierto);
                if (abierto) {
                    var cerrar = lateral.querySelector('[data-lateral-cerrar]');
                    if (cerrar) { cerrar.focus(); }
                }
            });
            return;
        }

        if (evento.target.closest('.velo, [data-lateral-cerrar]')) {
            sincronizarDrawer(false);
            var btnAbrir = document.querySelector('[data-lateral-abrir]');
            if (btnAbrir && window.innerWidth < 1100) {
                btnAbrir.focus();
            }
        }
    });

    // Atajos de teclado globales (compatibles con vender.js y Bootstrap modals)
    document.addEventListener('keydown', function (evento) {
        // No interferir si no hay sesión/shell activo
        if (!document.querySelector('.lateral')) { return; }

        // No interrumpir el modal de post-cobro en Mostrador
        var postCobro = document.getElementById('modalCobrado');
        if (postCobro && postCobro.classList.contains('show')) { return; }

        if (evento.key === 'F1') {
            evento.preventDefault();
            alternarModalAtajos();
            return;
        }

        if ((evento.ctrlKey || evento.metaKey) && evento.key.toLowerCase() === 'k') {
            evento.preventDefault();
            abrirPaleta();
            return;
        }

        if (evento.key === 'ArrowRight' && window.innerWidth >= 1100 && (modoRailActual() === 'areas' || railColapsadoActual())) {
            var resumenFoco = evento.target && evento.target.closest && evento.target.closest('.lateral details.lateral-desplegable > summary');
            if (resumenFoco) {
                evento.preventDefault();
                abrirAreaPopover(resumenFoco.closest('details.lateral-desplegable'), resumenFoco);
                var primerItem = document.querySelector('#irisAreaPopoverLista a');
                if (primerItem) { primerItem.focus(); }
                return;
            }
        }

        if (evento.key === 'Escape') {
            if (buscadorNotchAbierto) {
                evento.preventDefault();
                cerrarBuscadorNotch();
                var btnLupa = document.getElementById('irisNotchLupa');
                if (btnLupa) { btnLupa.focus(); }
                return;
            }
            var tourCaja = document.getElementById('irisTourCaja');
            if (tourCaja && !tourCaja.hidden) {
                evento.preventDefault();
                cerrarTour(false);
                return;
            }
            var popArea = document.getElementById('irisAreaPopover');
            if (popoverAbierto(popArea)) {
                cerrarAreaPopover();
                return;
            }
            var lateral = document.querySelector('.lateral.abierta');
            if (lateral) {
                evento.preventDefault();
                cerrarDrawer();
                return;
            }
            if (avisoActual && !document.querySelector('dialog[open], .modal.show')) {
                cerrarAvisoActual();
            }
            return;
        }

        // Navegación por flechas entre las pestañas del modal ? Ayuda (WAI-ARIA tabs)
        if ((evento.key === 'ArrowLeft' || evento.key === 'ArrowRight' || evento.key === 'Home' || evento.key === 'End') &&
            evento.target && evento.target.matches && evento.target.matches('.iris-ayuda-tabs [role="tab"]')) {
            var tabs = Array.prototype.slice.call(document.querySelectorAll('.iris-ayuda-tabs [role="tab"]'));
            if (tabs.length > 1) {
                evento.preventDefault();
                var idxActual = tabs.indexOf(evento.target);
                var siguienteIdx = idxActual;
                if (evento.key === 'ArrowRight') { siguienteIdx = (idxActual + 1) % tabs.length; }
                else if (evento.key === 'ArrowLeft') { siguienteIdx = (idxActual - 1 + tabs.length) % tabs.length; }
                else if (evento.key === 'Home') { siguienteIdx = 0; }
                else if (evento.key === 'End') { siguienteIdx = tabs.length - 1; }
                var destinoTab = tabs[siguienteIdx];
                if (destinoTab) {
                    seleccionarTabAyuda(destinoTab.dataset.irisAyudaTab);
                    destinoTab.focus();
                }
            }
            return;
        }

        // F2 fuera de Mostrador enfoca el buscador de la pantalla (Mostrador lo maneja vender.js)
        if (evento.key === 'F2' && !document.getElementById('mostrador')) {
            var buscador = document.querySelector('#contenido .herramientas input[type="search"], #contenido #herramientas input[name="q"]');
            if (buscador) {
                evento.preventDefault();
                buscador.focus();
                buscador.select();
            }
        }
    });

    // Filtro de entrada en #descuentoPorcentaje (Mostrador): bloquea negativos y letras al vuelo
    document.addEventListener('keydown', function (evento) {
        if (!evento.target || evento.target.id !== 'descuentoPorcentaje') { return; }
        if (evento.ctrlKey || evento.metaKey || evento.altKey) { return; }
        var k = evento.key;
        if (k.length === 1 && !/[\d.,]/.test(k)) {
            evento.preventDefault();
        }
    });

    // Pre-validación de input[data-minimo] antes del modal data-confirmar en forms sin .form-una-vez
    // (Merma, Traspaso, Recepción, Cambios). Corre en window capture ANTES del listener de cafeto.js.
    window.addEventListener('submit', function (evento) {
        var form = evento.target;
        if (!form || !form.matches || !form.matches('form') || form.matches('.form-una-vez')) { return; }
        if (form.dataset.confirmado === '1') { return; }
        var boton = evento.submitter && evento.submitter.form === form ? evento.submitter : null;
        var tieneConfirmar = !!(form.dataset.confirmar || (boton && boton.dataset.confirmar));
        if (!tieneConfirmar) { return; }
        var leer = window.Cafeto && window.Cafeto.leerNumero;
        if (typeof leer !== 'function') { return; }

        var primero = null;
        form.querySelectorAll('input[data-minimo]').forEach(function (control) {
            if (control.disabled || control.closest('.d-none')) { return; }
            var contenedor = control.closest('.input-group') || control;
            var padre = contenedor.parentNode;
            if (padre) {
                var viejo = padre.querySelector(':scope > .error-campo.js-numero');
                if (viejo) { viejo.remove(); }
            }
            control.classList.remove('is-invalid');
            control.removeAttribute('aria-invalid');
            if (control.value.trim() === '') { return; }
            var dec = parseInt(control.dataset.decimales, 10);
            var numero = leer(control.value, isNaN(dec) ? 2 : dec);
            var minimo = parseFloat(control.dataset.minimo);
            var texto = null;
            if (isNaN(numero)) {
                texto = 'Escribí un número válido (sin letras).';
            } else if (numero < minimo) {
                texto = minimo > 0 ? 'Tiene que ser mayor que cero.' : 'No puede ser negativo.';
            }
            if (!texto) { return; }
            var error = document.createElement('p');
            error.className = 'error-campo js-numero';
            error.setAttribute('role', 'alert');
            error.textContent = texto;
            contenedor.insertAdjacentElement('afterend', error);
            control.classList.add('is-invalid');
            control.setAttribute('aria-invalid', 'true');
            if (!primero) { primero = control; }
        });
        if (primero) {
            evento.preventDefault();
            evento.stopImmediatePropagation();
            primero.focus();
        }
    }, true);

    // Interacción interna de la paleta Ctrl+K, billetes en Mostrador y evaluación de práctica
    document.addEventListener('input', function (evento) {
        if (evento.target && evento.target.id === 'descuentoPorcentaje') {
            var limpio = String(evento.target.value || '').replace(/[^\d.,]/g, '');
            var partes = limpio.split(/[.,]/);
            if (partes.length > 2) {
                limpio = partes[0] + ',' + partes.slice(1).join('');
            }
            if (limpio !== evento.target.value) {
                evento.target.value = limpio;
            }
        }
        if (evento.target && evento.target.id === 'irisPaletaBuscar') {
            indicePaleta = 0;
            pintarResultadosPaleta(evento.target.value);
            return;
        }
        if (evento.target && evento.target.closest && evento.target.closest('#pagos')) {
            sincronizarBilletesRapidos();
        }
        if (practicaActiva) {
            evaluarPasoInteractivo();
        }
    });

    document.addEventListener('change', function (evento) {
        if (evento.target && evento.target.closest && evento.target.closest('#pagos')) {
            sincronizarBilletesRapidos();
        }
        if (practicaActiva) {
            evaluarPasoInteractivo();
        }
    });

    document.addEventListener('keydown', function (evento) {
        if (!evento.target || evento.target.id !== 'irisPaletaBuscar') { return; }
        if (!itemsPaletaActuales.length) { return; }
        if (evento.key === 'ArrowDown') {
            evento.preventDefault();
            indicePaleta = (indicePaleta + 1) % itemsPaletaActuales.length;
            actualizarSeleccionPaleta();
        } else if (evento.key === 'ArrowUp') {
            evento.preventDefault();
            indicePaleta = (indicePaleta - 1 + itemsPaletaActuales.length) % itemsPaletaActuales.length;
            actualizarSeleccionPaleta();
        } else if (evento.key === 'Enter') {
            evento.preventDefault();
            var elegido = itemsPaletaActuales[indicePaleta];
            cerrarBuscadorNotch();
            if (elegido && elegido.enlace && elegido.enlace.isConnected) {
                elegido.enlace.click();
            } else if (elegido) {
                window.location.assign(elegido.href);
            }
        }
    });

    // Sincronización de tema, modo de Rail y autorizaciones entre pestañas
    window.addEventListener('storage', function (evento) {
        if (evento.key === CLAVE_AUTH_SYNC && evento.newValue) {
            try {
                var sync = JSON.parse(evento.newValue);
                if (sync && typeof sync.cantidad === 'number') {
                    pintarPildoraAuth(sync.cantidad, sync.url);
                    if (sync.ultimoId) { guardarSesion(CLAVE_AUTH_VISTO, String(sync.ultimoId)); }
                }
            } catch (e) { /* JSON inválido ignorado */ }
            return;
        }
        if (evento.key === CLAVE_RAIL_MODO) {
            aplicarModoRail(evento.newValue === 'areas' ? 'areas' : 'completo');
            cerrarAreaPopover();
            return;
        }
        if (evento.key === CLAVE_RAIL_COLAPSADO) {
            aplicarColapsoRail(evento.newValue === '1');
            cerrarAreaPopover();
            return;
        }
        if (evento.key !== 'cafeto.tema') { return; }
        var tema = evento.newValue;
        if (tema !== 'claro' && tema !== 'oscuro') { return; }
        var raiz = document.documentElement;
        raiz.setAttribute('data-tema', tema);
        raiz.setAttribute('data-bs-theme', tema === 'claro' ? 'light' : 'dark');
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) { meta.setAttribute('content', tema === 'claro' ? '#E6E0D4' : '#121128'); }
        document.dispatchEvent(new CustomEvent('cafeto:tema', { detail: { tema: tema } }));
    });

    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') {
            consultarAutorizaciones();
        }
    });

    var timerResizeTablero = null;
    window.addEventListener('resize', function () {
        if (window.innerWidth >= 1100) {
            sincronizarDrawer(false);
        } else {
            cerrarAreaPopover();
        }
        document.querySelectorAll('[data-iris-subnav]').forEach(evaluarDesbordeSubnav);
        evaluarDesbordeChips();
        var tourCaja = document.getElementById('irisTourCaja');
        if (tourCaja && !tourCaja.hidden && tourElementoActual) {
            posicionarCajaTour(tourElementoActual, tourCaja);
        }
        // tablero.js:342 al cruzar ANCHO_ESTRECHO (420px) redibuja con setOption sin llamar a resize():
        // re-invocar CafetoTablero.iniciar() ejecuta grafico.resize() de forma idempotente en cada gráfico vivo.
        if (timerResizeTablero) { window.clearTimeout(timerResizeTablero); }
        timerResizeTablero = window.setTimeout(function () {
            if (window.CafetoTablero && typeof window.CafetoTablero.iniciar === 'function') {
                window.CafetoTablero.iniciar(document);
            }
        }, 160);
    });

    window.addEventListener('online', function () {
        sincronizarEstadoRedRail();
        consultarAutorizaciones();
    });
    window.addEventListener('offline', sincronizarEstadoRedRail);

    /* ----------------------------------------------------------------------
       8. Arranque e integración con HTMX (DOMContentLoaded + htmx:afterSettle)
       ---------------------------------------------------------------------- */
    function inicializarShell(raiz) {
        aplicarModoRail(modoRailActual());
        aplicarColapsoRail(railColapsadoActual());
        conectarLightDismissDialogos();
        conectarModalComprobante();
        actualizarBotonesSonido();
        sincronizarEstadoRedRail();
        observarSubnavs(raiz);
        evaluarDesbordeChips();
        restaurarAvisoPersistente();
        promoverMensajesAlNotch(raiz);
        conectarMostrador();
        iniciarPollingAuth();
    }

    document.addEventListener('DOMContentLoaded', function () {
        inicializarShell(document);
        window.setTimeout(consultarAutorizaciones, 1200);
    });

    document.addEventListener('htmx:oobBeforeSwap', function (evento) {
        if (buscadorNotchAbierto) {
            buscadorNotchAbierto = false;
        }
        var fragmento = evento.detail && evento.detail.fragment;
        if (!fragmento || !fragmento.querySelector) { return; }
        var nuevoNotch = fragmento.querySelector('#irisNotch');
        if (nuevoNotch) {
            sincronizarClasesNotch(nuevoNotch, avisoActual);
        }
    });

    document.addEventListener('htmx:beforeSwap', function (evento) {
        if (evento.target && evento.target.id === 'contenido') {
            cerrarBuscadorNotch();
            desconectarMostrador();
            cerrarTour(false);
        }
    });

    window.addEventListener('beforeunload', function () {
        if (practicaActiva && guiaTourActual && guiaTourActual.esMostrador) {
            limpiarStorageMostrador();
        }
    });

    document.addEventListener('htmx:afterSwap', function (evento) {
        var destino = evento.target;
        if (destino && !destino.isConnected && destino.id) {
            destino = document.getElementById(destino.id);
        }
        restaurarAvisoPersistente();
        if (destino) {
            cerrarAreaPopover();
            observarSubnavs(destino);
            evaluarDesbordeChips();
            sincronizarDrawer(false);
        }
    });

    document.addEventListener('htmx:afterSettle', function (evento) {
        var destino = evento.target;
        if (destino && !destino.isConnected && destino.id) {
            destino = document.getElementById(destino.id);
        }
        inicializarShell(destino || document);
    });

    window.CafetoIris = {
        avisar: encolarAviso,
        abrirPaleta: abrirPaleta,
        abrirAtajos: abrirAtajosDesdeAPI,
        abrirAyuda: abrirModalAyuda,
        abrirComprobante: abrirModalComprobante,
        consultarAutorizaciones: consultarAutorizaciones
    };
})();
