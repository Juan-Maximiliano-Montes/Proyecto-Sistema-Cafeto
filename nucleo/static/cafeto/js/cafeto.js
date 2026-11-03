/* ==========================================================================
   Cafeto · comportamiento común a todas las pantallas
   --------------------------------------------------------------------------
   Sin dependencias salvo Bootstrap (y jQuery + Select2 donde haya un combo
   de catálogo). Cada pieza se engancha por un atributo o una clase, así una
   pantalla nueva la usa sin escribir JavaScript:

     data-tema-alternar         botón que cambia entre tema oscuro y claro
     data-lateral-abrir         botón que abre la barra lateral (celular)
     form[data-confirmar]       pide confirmación antes de enviar
     form.form-una-vez          confirma con un resumen y apaga el botón
     [data-formato=cuit|dni|telefono]   formatea mientras se tipea
     select[data-auto-enviar]   envía su formulario al cambiar
     .js-renglones              renglones de un formset (agregar/quitar)
     select.js-catalogo         combo con búsqueda por AJAX (Select2)
     [data-modal-auto]          modal que se abre al cargar la página
   ========================================================================== */
(function () {
    'use strict';

    // ---------------------------------------------------------------------
    //  Almacenamiento del navegador: puede no estar (modo privado), así
    //  que todo va con try/catch y nada depende de él.
    // ---------------------------------------------------------------------
    function leer(clave) { try { return window.localStorage.getItem(clave); } catch (e) { return null; } }
    function guardar(clave, valor) { try { window.localStorage.setItem(clave, valor); } catch (e) { /* nada */ } }

    // ---------------------------------------------------------------------
    //  Tema oscuro / claro
    // ---------------------------------------------------------------------
    var raiz = document.documentElement;
    function aplicarTema(tema) {
        raiz.setAttribute('data-tema', tema);
        raiz.setAttribute('data-bs-theme', tema === 'claro' ? 'light' : 'dark');
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) { meta.setAttribute('content', tema === 'claro' ? '#F4F1EA' : '#121128'); }
    }
    var temaGuardado = leer('cafeto.tema');
    if (temaGuardado === 'claro' || temaGuardado === 'oscuro') { aplicarTema(temaGuardado); }
    document.addEventListener('click', function (evento) {
        var boton = evento.target.closest('[data-tema-alternar]');
        if (!boton) { return; }
        var nuevo = raiz.getAttribute('data-tema') === 'claro' ? 'oscuro' : 'claro';
        aplicarTema(nuevo);
        guardar('cafeto.tema', nuevo);
    });

    // ---------------------------------------------------------------------
    //  Barra lateral en pantallas chicas
    // ---------------------------------------------------------------------
    document.addEventListener('click', function (evento) {
        var lateral = document.querySelector('.lateral');
        var velo = document.querySelector('.velo');
        if (!lateral) { return; }
        if (evento.target.closest('[data-lateral-abrir]')) {
            lateral.classList.add('abierta');
            if (velo) { velo.classList.add('visible'); }
        } else if (evento.target.closest('.velo, [data-lateral-cerrar]')) {
            lateral.classList.remove('abierta');
            if (velo) { velo.classList.remove('visible'); }
        }
    });

    // ---------------------------------------------------------------------
    //  Sin conexión: una tablet que pierde el wifi tiene que AVISAR antes
    //  de que alguien intente cobrar.
    // ---------------------------------------------------------------------
    function estadoDeRed() {
        var aviso = document.querySelector('.sin-conexion');
        if (aviso) { aviso.classList.toggle('visible', !navigator.onLine); }
        document.querySelectorAll('[data-requiere-red]').forEach(function (control) {
            control.disabled = !navigator.onLine;
        });
    }
    window.addEventListener('online', estadoDeRed);
    window.addEventListener('offline', estadoDeRed);
    document.addEventListener('DOMContentLoaded', estadoDeRed);

    // ---------------------------------------------------------------------
    //  Confirmación con un modal propio (reemplaza a los alert del navegador)
    // ---------------------------------------------------------------------
    function escapar(texto) {
        var div = document.createElement('div');
        div.textContent = texto == null ? '' : String(texto);
        return div.innerHTML;
    }

    var Cafeto = window.Cafeto = window.Cafeto || {};

    /**
     * Cafeto.confirmar({titulo, texto, html, aceptar, peligro}) -> Promise<boolean>
     */
    Cafeto.confirmar = function (opciones) {
        opciones = opciones || {};
        return new Promise(function (resolver) {
            var modal = document.getElementById('modalConfirmar');
            if (!modal || typeof bootstrap === 'undefined') {
                resolver(window.confirm(opciones.texto || opciones.titulo || '¿Continuar?'));
                return;
            }
            modal.querySelector('.js-titulo').textContent = opciones.titulo || '¿Confirmás?';
            var cuerpo = modal.querySelector('.js-cuerpo');
            cuerpo.innerHTML = opciones.html || (opciones.texto ? '<p class="mb-0">' + escapar(opciones.texto) + '</p>' : '');
            var aceptar = modal.querySelector('.js-aceptar');
            aceptar.textContent = opciones.aceptar || 'Sí, continuar';
            aceptar.className = 'btn js-aceptar ' + (opciones.peligro ? 'btn-danger' : 'btn-primary');
            var instancia = bootstrap.Modal.getOrCreateInstance(modal);
            var respuesta = false;
            function alAceptar() { respuesta = true; instancia.hide(); }
            function alCerrar() {
                aceptar.removeEventListener('click', alAceptar);
                modal.removeEventListener('hidden.bs.modal', alCerrar);
                resolver(respuesta);
            }
            aceptar.addEventListener('click', alAceptar);
            modal.addEventListener('hidden.bs.modal', alCerrar);
            instancia.show();
        });
    };

    function apagarBotones(form) {
        form.querySelectorAll('button[type=submit]').forEach(function (boton) {
            boton.disabled = true;
            boton.dataset.textoOriginal = boton.innerHTML;
            boton.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Guardando…';
        });
    }

    // form[data-confirmar="pregunta"]: una acción que cambia el estado.
    document.addEventListener('submit', function (evento) {
        var form = evento.target;
        if (!form.matches('form[data-confirmar]') || form.dataset.confirmado === '1') { return; }
        evento.preventDefault();
        Cafeto.confirmar({
            titulo: form.dataset.titulo || '¿Confirmás?',
            texto: form.dataset.confirmar,
            aceptar: form.dataset.aceptar,
            peligro: form.dataset.peligro === '1'
        }).then(function (ok) {
            if (ok) { form.dataset.confirmado = '1'; form.requestSubmit(); }
        });
    }, true);

    // form.form-una-vez: resumen de lo que se va a guardar, y el botón se
    // apaga una sola vez (un doble clic no da de alta dos veces).
    function resumenDelFormulario(form) {
        var filas = [];
        var raiz = form.querySelector('.modal-body') || form;
        raiz.querySelectorAll('[name]').forEach(function (control) {
            if (control.type === 'hidden' || control.name === 'csrfmiddlewaretoken') { return; }
            if (/-DELETE$/.test(control.name)) { return; }
            var renglon = control.closest('.js-renglon');
            if (renglon && renglon.classList.contains('d-none')) { return; }
            if (control.closest('.d-none')) { return; }
            var etiqueta = form.querySelector('label[for="' + control.id + '"]');
            var nombre = etiqueta ? etiqueta.textContent.replace('*', '').trim() : control.name;
            var valor;
            if (control.type === 'checkbox') {
                valor = control.checked ? 'Sí' : 'No';
            } else if (control.type === 'password') {
                valor = control.value ? '••••••' : '';
            } else if (control.tagName === 'SELECT') {
                valor = control.selectedIndex >= 0 ? control.options[control.selectedIndex].text : '';
            } else {
                valor = control.value;
            }
            valor = (valor || '').trim();
            filas.push('<tr><th class="text-start text-secondary fw-normal pe-3">' + escapar(nombre) +
                       '</th><td class="text-start">' +
                       (valor === '' ? '<span class="text-tenue">—</span>' : escapar(valor)) + '</td></tr>');
        });
        return filas.length ? '<table class="table table-sm mb-0"><tbody>' + filas.join('') + '</tbody></table>' : '';
    }

    document.addEventListener('submit', function (evento) {
        var form = evento.target;
        if (!form.matches('.form-una-vez')) { return; }
        if (form.dataset.confirmado === '1') { apagarBotones(form); return; }
        evento.preventDefault();
        Cafeto.confirmar({
            titulo: '¿Guardamos estos datos?',
            html: resumenDelFormulario(form),
            aceptar: 'Sí, guardar'
        }).then(function (ok) {
            if (ok) { form.dataset.confirmado = '1'; form.requestSubmit(); }
        });
    });

    // ---------------------------------------------------------------------
    //  Formateadores (CUIT, DNI, teléfono). Sólo ayuda visual: quien valida
    //  y normaliza es el modelo.
    // ---------------------------------------------------------------------
    var CORTES = {cuit: [2, 10], dni: [], telefono: [3]};
    var MAXIMOS = {cuit: 11, dni: 8, telefono: 15};

    function formatear(digitos, tipo) {
        var cortes = CORTES[tipo] || [], partes = [], desde = 0;
        for (var i = 0; i < cortes.length; i++) {
            if (digitos.length <= cortes[i]) { break; }
            partes.push(digitos.slice(desde, cortes[i]));
            desde = cortes[i];
        }
        partes.push(digitos.slice(desde));
        return partes.join('-');
    }

    function aplicarFormato(campo) {
        var tipo = campo.dataset.formato;
        if (!Object.prototype.hasOwnProperty.call(CORTES, tipo)) { return; }
        var antes = campo.value.slice(0, campo.selectionStart || 0).replace(/\D/g, '').length;
        campo.value = formatear(campo.value.replace(/\D/g, '').slice(0, MAXIMOS[tipo]), tipo);
        var posicion = 0, contados = 0;
        while (posicion < campo.value.length && contados < antes) {
            if (/\d/.test(campo.value[posicion])) { contados++; }
            posicion++;
        }
        try { campo.setSelectionRange(posicion, posicion); } catch (e) { /* no editable */ }
    }
    document.addEventListener('input', function (evento) {
        if (evento.target.dataset && evento.target.dataset.formato) { aplicarFormato(evento.target); }
    });
    document.addEventListener('blur', function (evento) {
        if (evento.target.dataset && evento.target.dataset.formato) { aplicarFormato(evento.target); }
    }, true);

    // select[data-auto-enviar]: la cantidad por página, los filtros.
    document.addEventListener('change', function (evento) {
        var control = evento.target;
        if (control.matches('[data-auto-enviar]') && control.form) {
            var pagina = control.form.querySelector('[name="pagina"]');
            if (pagina) { pagina.value = '1'; }
            control.form.requestSubmit();
        }
    });

    // ---------------------------------------------------------------------
    //  Renglones de un formset (receta, contenido de un combo)
    // ---------------------------------------------------------------------
    Cafeto.activarRenglones = function (contenedor) {
        contenedor.querySelectorAll('.js-renglones').forEach(function (bloque) {
            if (bloque.dataset.activo === '1') { return; }
            bloque.dataset.activo = '1';
            var prefijo = bloque.dataset.prefijo;
            var total = bloque.querySelector('[name="' + prefijo + '-TOTAL_FORMS"]');
            var cuerpo = bloque.querySelector('.js-renglones-cuerpo');
            var molde = bloque.querySelector('.js-molde-renglon');
            var vacio = bloque.querySelector('.js-renglones-vacio');

            function repintar() {
                vacio.classList.toggle('d-none', cuerpo.querySelectorAll('.js-renglon:not(.d-none)').length > 0);
            }
            bloque.addEventListener('click', function (evento) {
                var boton = evento.target.closest('.js-quitar-renglon');
                if (!boton || !bloque.contains(boton)) { return; }
                var renglon = boton.closest('.js-renglon');
                var tilde = renglon.querySelector('[name$="-DELETE"]');
                if (tilde) { tilde.checked = true; }
                renglon.classList.add('d-none');
                repintar();
            });
            bloque.querySelector('.js-agregar-renglon').addEventListener('click', function () {
                var indice = parseInt(total.value, 10);
                var envase = document.createElement('div');
                envase.innerHTML = molde.innerHTML.split('__prefix__').join(indice);
                cuerpo.appendChild(envase.firstElementChild);
                total.value = indice + 1;
                Cafeto.enchufarCatalogo(cuerpo);
                repintar();
            });

            // El bloque se muestra sólo para ciertos valores de otro campo
            // (la receta, sólo para elaborados, producidos y combos).
            var campo = bloque.dataset.dependeDe;
            if (campo) {
                var formulario = bloque.closest('form') || document;
                var control = formulario.querySelector('[name="' + campo + '"]');
                if (control) {
                    var sincronizar = function () {
                        var hace_falta = bloque.dataset.visibleSi.split(',').indexOf(control.value) !== -1;
                        bloque.classList.toggle('d-none', !hace_falta);
                        if (hace_falta) { Cafeto.enchufarCatalogo(bloque); }
                        cuerpo.querySelectorAll('[name$="-DELETE"]').forEach(function (tilde) {
                            var renglon = tilde.closest('.js-renglon');
                            if (!hace_falta) { tilde.checked = true; }
                            else if (!renglon.classList.contains('d-none')) { tilde.checked = false; }
                        });
                        repintar();
                    };
                    control.addEventListener('change', sincronizar);
                    sincronizar();
                }
            }
            repintar();
        });
    };

    // ---------------------------------------------------------------------
    //  Campos que dependen de otro: [data-visible-si="campo=VALOR,VALOR"]
    //  (en el producto: el proveedor sólo para lo que se compra, la receta
    //  sólo para lo que se prepara...). Es comodidad: el servidor valida.
    // ---------------------------------------------------------------------
    Cafeto.activarDependencias = function (contenedor) {
        contenedor.querySelectorAll('[data-visible-si]').forEach(function (bloque) {
            if (bloque.dataset.dependenciaActiva === '1') { return; }
            var partes = bloque.dataset.visibleSi.split('=');
            if (partes.length !== 2) { return; }
            var formulario = bloque.closest('form') || document;
            var controles = formulario.querySelectorAll('[name="' + partes[0] + '"]');
            if (!controles.length) { return; }
            bloque.dataset.dependenciaActiva = '1';
            var valores = partes[1].split(',');
            var sincronizar = function () {
                var control = controles[0], valor;
                if (control.type === 'radio') {
                    var marcado = formulario.querySelector('[name="' + partes[0] + '"]:checked');
                    valor = marcado ? marcado.value : '';
                } else if (control.type === 'checkbox') {
                    valor = control.checked ? 'si' : 'no';
                } else {
                    valor = control.value;
                }
                bloque.classList.toggle('d-none', valores.indexOf(valor) === -1);
            };
            controles.forEach(function (c) { c.addEventListener('change', sincronizar); });
            sincronizar();
        });
    };

    // ---------------------------------------------------------------------
    //  Combo con búsqueda (Select2 + AJAX), para catálogos grandes
    // ---------------------------------------------------------------------
    Cafeto.enchufarCatalogo = function (contenedor) {
        if (typeof jQuery === 'undefined' || !jQuery.fn.select2) { return; }
        jQuery(contenedor).find('select.js-catalogo').each(function () {
            var combo = jQuery(this);
            if (combo.data('select2') || !this.isConnected || this.offsetParent === null) { return; }
            combo.select2({
                width: '100%',
                placeholder: combo.find('option[value=""]').text() || 'Buscá un producto',
                allowClear: true,
                dropdownParent: combo.closest('.modal-body, .modal-content, body').first(),
                ajax: {
                    url: combo.data('catalogo'),
                    dataType: 'json',
                    delay: 250,
                    cache: true,
                    data: function (p) { return {q: p.term || '', page: p.page || 1}; }
                },
                language: {
                    noResults: function () { return 'Sin resultados'; },
                    searching: function () { return 'Buscando…'; },
                    errorLoading: function () { return 'No se pudo cargar el catálogo'; },
                    loadingMore: function () { return 'Cargando más…'; },
                    removeAllItems: function () { return 'Quitar la selección'; }
                }
            });
        });
    };

    // ---------------------------------------------------------------------
    //  Lotes de un producto: select[data-lotes="#idDelSelectDeLotes"]
    //  [data-lotes-url]. Al elegir el producto se traen sus lotes con stock.
    //  Con data-lote-nuevo="1" se ofrece además cargar un lote que el
    //  sistema no conocía (el inventario inicial).
    // ---------------------------------------------------------------------
    function cargarLotes(selectProducto) {
        var destino = document.querySelector(selectProducto.dataset.lotes);
        if (!destino) { return; }
        var elegido = destino.dataset.elegido || '';
        destino.innerHTML = '';
        var bloque = destino.closest('.js-bloque-lote');
        if (!selectProducto.value) { if (bloque) { bloque.classList.add('d-none'); } return; }
        fetch(selectProducto.dataset.lotesUrl + '?producto=' + encodeURIComponent(selectProducto.value),
              {headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'})
            .then(function (r) { return r.json(); })
            .then(function (datos) {
                var hay = datos.controla || datos.lotes.length;
                if (bloque) { bloque.classList.toggle('d-none', !hay); }
                var opciones = [['', datos.controla ? 'El que vence primero' : 'Sin lote']];
                datos.lotes.forEach(function (l) { opciones.push([String(l.id), l.texto]); });
                if (destino.dataset.loteNuevo === '1' && datos.controla) { opciones.push(['nuevo', 'Un lote que no está en el sistema…']); }
                opciones.forEach(function (o) {
                    var opcion = document.createElement('option');
                    opcion.value = o[0]; opcion.textContent = o[1];
                    if (o[0] === elegido) { opcion.selected = true; }
                    destino.appendChild(opcion);
                });
                destino.dispatchEvent(new Event('change', {bubbles: true}));
                var unidad = document.querySelector(selectProducto.dataset.unidad || '#_');
                if (unidad) { unidad.textContent = datos.unidad; }
            });
    }
    if (typeof jQuery !== 'undefined') {
        jQuery(document).on('change', 'select[data-lotes]', function () { cargarLotes(this); });
    }
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('select[data-lotes]').forEach(function (s) { if (s.value) { cargarLotes(s); } });
    });

    // ---------------------------------------------------------------------
    //  Fragmentos que llegan por fetch (ficha, formulario de edición)
    // ---------------------------------------------------------------------
    Cafeto.cargar = function (url, destino) {
        destino.innerHTML = '<div class="text-center text-secondary py-4">' +
            '<div class="spinner-border"></div><p class="mt-2 mb-0">Cargando…</p></div>';
        return fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'})
            .then(function (r) {
                if (r.status === 401) { window.location.reload(); throw new Error('sesión'); }
                return r.text();
            })
            .then(function (html) {
                destino.innerHTML = html;
                Cafeto.activarRenglones(destino);
                Cafeto.activarDependencias(destino);
                Cafeto.enchufarCatalogo(destino);
            })
            .catch(function () {
                destino.innerHTML = '<div class="aviso aviso-error mb-0"><i class="bi bi-wifi-off"></i>' +
                    '<span>No se pudo cargar. Revisá la conexión y probá de nuevo.</span></div>';
            });
    };

    document.addEventListener('DOMContentLoaded', function () {
        Cafeto.activarRenglones(document);
        Cafeto.activarDependencias(document);
        Cafeto.enchufarCatalogo(document);
        document.querySelectorAll('.modal').forEach(function (modal) {
            modal.addEventListener('shown.bs.modal', function () {
                Cafeto.enchufarCatalogo(modal);
                var primero = modal.querySelector('[autofocus], .modal-body input:not([type=hidden]), .modal-body select');
                if (primero) { primero.focus(); }
            });
        });
        document.querySelectorAll('[data-modal-auto]').forEach(function (modal) {
            bootstrap.Modal.getOrCreateInstance(modal).show();
        });
        // Abre un fragmento remoto en un modal: [data-remoto="url"][data-destino="#id"]
        document.addEventListener('click', function (evento) {
            var boton = evento.target.closest('[data-remoto]');
            if (!boton) { return; }
            evento.preventDefault();
            var modal = document.querySelector(boton.dataset.modal);
            var cuerpo = modal.querySelector('.js-remoto');
            var form = modal.querySelector('form');
            if (form && boton.dataset.accion) { form.action = boton.dataset.accion; }
            var titulo = modal.querySelector('.js-titulo-remoto');
            if (titulo && boton.dataset.titulo) { titulo.textContent = boton.dataset.titulo; }
            bootstrap.Modal.getOrCreateInstance(modal).show();
            Cafeto.cargar(boton.dataset.remoto, cuerpo);
        });
    });
})();
