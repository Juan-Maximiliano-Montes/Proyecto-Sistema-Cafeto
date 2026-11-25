/* ==========================================================================
   Cafeto · comportamiento común a todas las pantallas
   --------------------------------------------------------------------------
   Sin dependencias salvo Bootstrap (y jQuery + Select2 donde haya un combo
   de catálogo). Cada pieza se engancha por un atributo o una clase, así una
   pantalla nueva la usa sin escribir JavaScript:

     data-tema-alternar         botón que cambia entre tema oscuro y claro
     data-lateral-abrir         botón que abre la barra lateral (celular)
     form[data-confirmar]       pide confirmación antes de enviar
     form[data-acreedor]        exige proveedor o destinatario (uno solo)
     form.form-una-vez          confirma con un resumen y apaga el botón
     [data-formato=cuit|dni|telefono]   formatea mientras se tipea
     select[data-auto-enviar]   envía su formulario al cambiar
     .js-renglones              renglones de un formset (agregar/quitar)
     select.js-catalogo         combo con búsqueda por AJAX (Select2)
     [data-modal-auto]          modal que se abre al cargar la página
     [data-parecidos]           al escribir o salir del campo avisa iguales o parecidos
     .js-alta-rapida            "+" que despliega el alta del dato padre
     [hx-boost]                 filtros, paginador, cápsulas y menú sin recargar

   Cafeto.inicializar(raiz) engancha todo lo de arriba en un pedazo nuevo
   de la página: al cargar y después de cada actualización parcial.
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
        document.dispatchEvent(new CustomEvent('cafeto:tema', {detail: {tema: tema}}));
    }
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
    // Sin red se apaga lo que necesita el servidor; al volver se prende sólo
    // lo que apagó la red: un «Cobrar» con el ticket en $ 0 o un «Sacar del
    // cajón» con la caja cerrada siguen apagados.
    function estadoDeRed() {
        var enLinea = navigator.onLine;
        var aviso = document.querySelector('.sin-conexion');
        if (aviso) { aviso.classList.toggle('visible', !enLinea); }
        document.querySelectorAll('[data-requiere-red]').forEach(function (control) {
            if (!enLinea) {
                if (!control.disabled) {
                    control.disabled = true;
                    control.dataset.deshabilitadoPorRed = '1';
                }
            } else if (control.dataset.deshabilitadoPorRed === '1') {
                control.disabled = false;
                delete control.dataset.deshabilitadoPorRed;
            }
        });
    }
    window.addEventListener('online', estadoDeRed);
    window.addEventListener('offline', estadoDeRed);

    // ---------------------------------------------------------------------
    //  Confirmación con un modal propio (reemplaza a los alert del navegador)
    // ---------------------------------------------------------------------
    function escapar(texto) {
        var div = document.createElement('div');
        div.textContent = texto == null ? '' : String(texto);
        return div.innerHTML;
    }

    var Cafeto = window.Cafeto = window.Cafeto || {};
    Cafeto.escapar = escapar;

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
                // Se abrió encima de otro modal (un formulario, un egreso):
                // Bootstrap le saca al body el estado de "modal abierto" al
                // cerrar éste, y el de abajo quedaría con la página corriendo
                // por detrás.
                if (document.querySelector('.modal.show')) {
                    document.body.classList.add('modal-open');
                    document.body.style.overflow = 'hidden';
                }
                resolver(respuesta);
            }
            aceptar.addEventListener('click', alAceptar);
            modal.addEventListener('hidden.bs.modal', alCerrar);
            // Enter confirma (el foco va al botón de aceptar), Escape cancela.
            // En lo peligroso (borrar, anular) el foco no va al botón rojo.
            if (!opciones.peligro) {
                modal.addEventListener('shown.bs.modal', function () { aceptar.focus(); }, {once: true});
            }
            instancia.show();
        });
    };

    function apagarBotones(form) {
        form.querySelectorAll('button[type=submit]').forEach(function (boton) {
            if (boton.disabled) { return; }
            boton.dataset.textoOriginal = boton.innerHTML;
            boton.disabled = true;
            boton.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Guardando…';
        });
    }

    function encenderBotones(raiz) {
        (raiz || document).querySelectorAll('button[type=submit][data-texto-original]')
            .forEach(function (boton) {
                boton.innerHTML = boton.dataset.textoOriginal;
                delete boton.dataset.textoOriginal;
                boton.disabled = false;
            });
    }

    //  Volver con la flecha «atrás» no recarga la página: el navegador la
    //  saca del caché del historial (bfcache) tal como quedó, con el botón
    //  apagado en «Guardando…» y el formulario marcado como ya confirmado.
    //  La pantalla quedaba muerta: no se podía volver a guardar ni salía la
    //  confirmación. Al reaparecer se devuelve todo a su estado inicial.
    window.addEventListener('pageshow', function (evento) {
        if (!evento.persisted) { return; }
        encenderBotones(document);
        document.querySelectorAll('form[data-confirmado]').forEach(function (form) {
            delete form.dataset.confirmado;
        });
    });

    // form[data-confirmar="pregunta"]: una acción que cambia el estado.
    // La pregunta también puede ir en el botón (button[data-confirmar]) cuando
    // el formulario tiene dos salidas distintas, como Aprobar y Rechazar.
    document.addEventListener('submit', function (evento) {
        var form = evento.target;
        if (!form.matches || !form.matches('form')) { return; }
        var boton = evento.submitter && evento.submitter.form === form ? evento.submitter : null;
        var origen = boton && boton.dataset.confirmar ? boton : form;
        if (!origen.dataset.confirmar || form.dataset.confirmado === '1') { return; }
        evento.preventDefault();
        // Un form.form-una-vez con pregunta propia (abrir la caja, el arqueo
        // parcial) muestra también lo cargado, y no se confirma un monto mal
        // escrito.
        // Este modal reemplaza al resumen genérico: sin stopPropagation se
        // abrían dos confirmaciones sobre el mismo modal y la segunda pisaba
        // a la primera.
        var resumen = '';
        if (form.matches('.form-una-vez')) {
            evento.stopPropagation();
            if (!numerosValidos(form)) { return; }
            resumen = resumenDelFormulario(form);
        }
        Cafeto.confirmar({
            titulo: origen.dataset.titulo || '¿Confirmás?',
            texto: origen.dataset.confirmar,
            html: resumen ? '<p class="mb-2">' + escapar(origen.dataset.confirmar) + '</p>' + resumen : '',
            aceptar: origen.dataset.aceptar,
            // data-peligro (sin valor) o data-peligro="1": botón rojo.
            peligro: origen.dataset.peligro !== undefined && origen.dataset.peligro !== '0'
        }).then(function (ok) {
            if (!ok) { return; }
            form.dataset.confirmado = '1';
            if (boton) { form.requestSubmit(boton); } else { form.requestSubmit(); }
        });
    }, true);

    // form.form-una-vez: resumen de lo que se va a guardar, y el botón se
    // apaga una sola vez (un doble clic no da de alta dos veces).
    //  Un grupo de radios tiene su pregunta en el <legend> del <fieldset>
    //  (el modo de cierre) o en el aria-label del grupo de botones (cuándo
    //  se paga un egreso). Lo que se muestra en el resumen es esa pregunta,
    //  no el name del control.
    function preguntaDelGrupo(control) {
        var campo = control.closest('fieldset');
        var leyenda = campo && campo.querySelector('legend');
        if (leyenda && leyenda.textContent.trim()) { return leyenda.textContent.trim(); }
        var grupo = control.closest('[role="group"][aria-label], [role="radiogroup"][aria-label]');
        if (grupo) { return grupo.getAttribute('aria-label').trim(); }
        return control.name;
    }

    function resumenDelFormulario(form) {
        var filas = [];
        var raiz = form.querySelector('.modal-body') || form;
        raiz.querySelectorAll('[name]').forEach(function (control) {
            if (control.type === 'hidden' || control.name === 'csrfmiddlewaretoken') { return; }
            if (/-DELETE$/.test(control.name)) { return; }
            // Un grupo de radios es UNA respuesta: las opciones que no se
            // eligieron no se listan. Antes salían las tres («Cerrar la
            // caja: —», «Cambio de turno: —») y el resumen decía lo
            // contrario de lo que se iba a hacer.
            if (control.type === 'radio' && !control.checked) { return; }
            var renglon = control.closest('.js-renglon');
            if (renglon && renglon.classList.contains('d-none')) { return; }
            if (control.closest('.d-none')) { return; }
            var etiqueta = form.querySelector('label[for="' + control.id + '"]');
            var nombre = etiqueta ? etiqueta.textContent.replace('*', '').trim() : control.name;
            var valor;
            if (control.type === 'radio') {
                // La etiqueta del radio es la OPCIÓN elegida; el nombre de
                // la fila es la pregunta del grupo.
                valor = nombre === control.name ? control.value : nombre;
                var fuerte = etiqueta && etiqueta.querySelector('strong');
                if (fuerte && fuerte.textContent.trim()) { valor = fuerte.textContent.trim(); }
                nombre = preguntaDelGrupo(control);
            } else if (control.type === 'checkbox') {
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
        var tabla = filas.length ? '<table class="table table-sm mb-0"><tbody>' + filas.join('') + '</tbody></table>' : '';
        return avisosDeMonto(form) + tabla;
    }

    //  Un monto chiquísimo casi siempre es un error de tipeo: en la planilla
    //  del local hay gastos cargados como "28.8" donde el papel decía
    //  $28.800. El servidor no lo puede rechazar (un gasto de $50 existe:
    //  una bolsa de hielo), así que se pregunta ANTES de guardar, con el
    //  monto escrito en pesos para que se lea de un vistazo.
    //  El umbral lo pone cada campo con data-avisar-si-menor.
    function avisosDeMonto(form) {
        var avisos = [];
        form.querySelectorAll('input[data-avisar-si-menor]').forEach(function (control) {
            if (control.disabled || control.closest('.d-none')) { return; }
            var tope = leerNumero(control.dataset.avisarSiMenor, decimalesDe(control));
            var valor = leerNumero(control.value, decimalesDe(control));
            if (isNaN(tope) || isNaN(valor) || valor <= 0 || valor >= tope) { return; }
            var etiqueta = form.querySelector('label[for="' + control.id + '"]');
            avisos.push('<p class="aviso aviso-aviso mb-2"><i class="bi bi-exclamation-triangle-fill"></i>' +
                '<span>' + escapar(etiqueta ? etiqueta.textContent.replace('*', '').trim() : 'El monto') +
                ' es de <strong>' + escapar(pesosDeNumero(valor)) + '</strong>, menos de ' +
                escapar(pesosDeNumero(tope)) + '. Fijate que no te falte un cero: ' +
                escapar(pesosDeNumero(valor)) + ' no es lo mismo que ' +
                escapar(pesosDeNumero(valor * 1000)) + '.</span></p>');
        });
        return avisos.join('');
    }

    //  Punto de venta de una compra informal: un ticket de la verdulería o
    //  un remito escrito a mano no lo traen impreso, pero la base exige
    //  uno. El campo declara con qué valor se completa
    //  (data-punto-por-defecto) y para qué comprobantes
    //  (data-tipos-sin-punto); el servidor hace lo mismo al guardar, así
    //  que sin JavaScript tampoco traba.
    function ajustarPuntoPorDefecto(form) {
        var punto = form.querySelector('input[data-punto-por-defecto]');
        var tipo = form.querySelector('[name="tipo_comprobante"]');
        if (!punto || !tipo) { return; }
        var sin = (punto.dataset.tiposSinPunto || '').split(',');
        var informal = sin.indexOf(tipo.value) !== -1;
        if (informal && punto.value.trim() === '') {
            punto.value = punto.dataset.puntoPorDefecto;
        }
        var ayuda = document.getElementById(punto.getAttribute('aria-describedby') || '');
        if (ayuda) { ayuda.classList.toggle('d-none', !informal); }
    }
    document.addEventListener('change', function (evento) {
        var tipo = evento.target.closest && evento.target.closest('[name="tipo_comprobante"]');
        if (tipo && tipo.form) { ajustarPuntoPorDefecto(tipo.form); }
    });

    var FORMATO_PESOS = window.Intl && window.Intl.NumberFormat
        ? new Intl.NumberFormat('es-AR', {style: 'currency', currency: 'ARS'}) : null;
    function pesosDeNumero(valor) {
        return FORMATO_PESOS ? FORMATO_PESOS.format(valor) : '$ ' + textoDeNumero(valor, 2);
    }

    // form[data-acreedor]: un egreso se le paga a un proveedor O a otro
    // destinatario, uno solo (Egreso.clean, DD-25.1). El servidor ya lo
    // rechaza, pero volviendo a la lista: la ventana se cerraba y había que
    // cargar todo de nuevo. Acá se avisa al lado del campo, antes de enviar.
    // Va en la fase de captura para frenar también el resumen de confirmación.
    document.addEventListener('submit', function (evento) {
        var form = evento.target;
        if (!form.matches || !form.matches('[data-acreedor]')) { return; }
        var proveedor = form.querySelector('[name="proveedor"]');
        var destino = form.querySelector('[name="destinatario"]');
        if (!destino) { return; }
        var hayProveedor = !!(proveedor && proveedor.value);
        var hayDestino = destino.value.trim() !== '';
        var columna = destino.closest('.col-md-6, .col-12') || destino.parentNode;
        var viejo = columna.querySelector(':scope > .error-campo.js-acreedor');
        if (viejo) { viejo.remove(); }
        destino.classList.remove('is-invalid');
        if (hayProveedor !== hayDestino) { return; }
        evento.preventDefault();
        evento.stopImmediatePropagation();
        var error = document.createElement('p');
        error.className = 'error-campo js-acreedor';
        error.setAttribute('role', 'alert');
        error.textContent = hayProveedor
            ? 'Se le paga a un proveedor o a otro destinatario, no a los dos: dejá uno solo.'
            : (proveedor ? 'Indicá a quién se le paga: elegí el proveedor o escribí el destinatario.'
                         : 'Indicá a quién se le paga.');
        columna.appendChild(error);
        destino.classList.add('is-invalid');
        destino.focus();
    }, true);

    // Los importes y cantidades se revisan antes del resumen: un «-500» o
    // un «abc» no llegan a la ventana de confirmación como si estuvieran
    // bien (DD-103). Las mismas reglas que leer_numero del servidor.
    //  Las mismas reglas que ``leer_numero`` de nucleo/formularios.py, en el
    //  mismo orden, porque lo que el navegador acepta y lo que el servidor
    //  guarda tienen que ser el MISMO número:
    //
    //    * con coma y punto, el que va ÚLTIMO es el separador decimal;
    //    * sólo con coma, la coma es decimal ('1500,50');
    //    * sólo con punto: si el valor admite tres decimales (una cantidad
    //      en kilos) el punto es decimal ('1.250' kg = 1,25); en un importe,
    //      un punto seguido de exactamente tres dígitos es de MILES
    //      ('1.500' = mil quinientos) y si no, decimal ('12.5').
    //
    //  Esa última regla es la que faltaba: con parseFloat, el precio '1.500'
    //  que dibuja el servidor se leía como 1,5 y la calculadora de precios
    //  devolvía cualquier cosa.
    var DIGITOS_EN_BASE = 12;

    function leerNumero(texto, decimales) {
        decimales = decimales === undefined || decimales === null ? 2 : decimales;
        texto = String(texto === null || texto === undefined ? '' : texto)
            .replace(/[\s$]/g, '').replace(/\u2212/g, '-');
        if (!/^-?[\d.,]+$/.test(texto) || !/\d/.test(texto)) { return NaN; }
        var hayComa = texto.indexOf(',') !== -1, hayPunto = texto.indexOf('.') !== -1;
        if (hayComa && hayPunto) {
            var decimal = texto.lastIndexOf(',') > texto.lastIndexOf('.') ? ',' : '.';
            var miles = decimal === ',' ? '.' : ',';
            texto = texto.split(miles).join('').split(decimal).join('.');
        } else if (hayComa) {
            if ((texto.match(/,/g) || []).length > 1) { return NaN; }
            texto = texto.split(',').join('.');
        } else if (hayPunto) {
            var partes = texto.split('.');
            var grupos = partes.slice(1);
            var todosDeTres = grupos.every(function (p) { return p.length === 3; });
            if (decimales < 3 && todosDeTres) {
                texto = partes.join('');
            } else if (partes.length > 2) {
                return NaN;
            }
        }
        // Lo que Decimal() acepta y nada más: un '1.23.4' no es un número.
        if (!/^-?(\d+\.?\d*|\.\d+)$/.test(texto)) { return NaN; }
        var numero = parseFloat(texto);
        if (isNaN(numero) || !isFinite(numero)) { return NaN; }
        // No más decimales que los que guarda la base, ni más dígitos que
        // los que entran en la columna (12 en total).
        var coma = texto.indexOf('.');
        if (coma !== -1 && texto.length - coma - 1 > decimales) { return NaN; }
        if (Math.abs(numero) >= Math.pow(10, DIGITOS_EN_BASE - decimales)) { return NaN; }
        return numero;
    }

    //  Y al revés: un número se escribe en un campo como se escribe acá,
    //  con coma decimal. Con punto, Django lo lee como separador de MILES
    //  (LANGUAGE_CODE='es-ar' y USE_THOUSAND_SEPARATOR=True) y un precio de
    //  '1400.00' se guardaba como $140.000.
    function textoDeNumero(valor, decimales) {
        decimales = decimales === undefined || decimales === null ? 2 : decimales;
        return valor.toFixed(decimales).replace('.', ',');
    }

    function escribirNumero(control, valor, decimales) {
        if (!control) { return; }
        control.value = textoDeNumero(valor, decimales);
    }

    Cafeto.leerNumero = leerNumero;
    Cafeto.textoDeNumero = textoDeNumero;

    function decimalesDe(control) {
        var declarados = control && control.dataset ? control.dataset.decimales : null;
        var numero = declarados === undefined || declarados === null || declarados === ''
            ? NaN : parseInt(declarados, 10);
        return isNaN(numero) ? 2 : numero;
    }

    function numerosValidos(form) {
        var primero = null;
        form.querySelectorAll('input[data-minimo]').forEach(function (control) {
            if (control.disabled || control.closest('.d-none')) { return; }
            var contenedor = control.closest('.input-group') || control;
            var viejo = contenedor.parentNode.querySelector(':scope > .error-campo.js-numero');
            if (viejo) { viejo.remove(); }
            control.classList.remove('is-invalid');
            if (control.value.trim() === '') { return; }
            var numero = leerNumero(control.value, decimalesDe(control));
            var minimo = parseFloat(control.dataset.minimo);
            var texto = null;
            if (isNaN(numero)) {
                texto = 'Escribí un número, por ejemplo 1500 o 1500,50.';
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
        if (primero) { primero.focus(); }
        return !primero;
    }

    document.addEventListener('input', function (evento) {
        var control = evento.target;
        if (!control.matches || !control.matches('input[data-minimo].is-invalid')) { return; }
        control.classList.remove('is-invalid');
        control.removeAttribute('aria-invalid');
        var contenedor = control.closest('.input-group') || control;
        var viejo = contenedor.parentNode.querySelector(':scope > .error-campo.js-numero');
        if (viejo) { viejo.remove(); }
    });

    document.addEventListener('submit', function (evento) {
        var form = evento.target;
        if (!form.matches('.form-una-vez')) { return; }
        if (form.dataset.confirmado === '1') { apagarBotones(form); return; }
        evento.preventDefault();
        if (!numerosValidos(form)) { return; }
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
                    // Si al cambiar el tipo cambia el selector (de receta a
                    // combo), lo ya elegido deja de valer: se limpia, para
                    // que no quede un yogur en la receta de una pizza.
                    var marcarSelectores = function () {
                        cuerpo.querySelectorAll('select.js-catalogo').forEach(function (sel) {
                            sel.dataset.paraUsado = Cafeto.paraDelCatalogo(sel);
                        });
                    };
                    control.addEventListener('change', function () {
                        cuerpo.querySelectorAll('select.js-catalogo').forEach(function (sel) {
                            var ahora = Cafeto.paraDelCatalogo(sel);
                            if (sel.value && sel.dataset.paraUsado !== undefined &&
                                    sel.dataset.paraUsado !== ahora && typeof jQuery !== 'undefined') {
                                jQuery(sel).val(null).trigger('change');
                            }
                            sel.dataset.paraUsado = ahora;
                        });
                    });
                    control.addEventListener('change', sincronizar);
                    sincronizar();
                    marcarSelectores();
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
    //  ``data-para`` fija el selector; si además trae ``data-para-segun``
    //  (nombre de otro campo del formulario) y ``data-para-mapa``
    //  ("VALOR:selector,VALOR:selector"), el selector cambia con ese campo:
    //  la receta de un elaborado ofrece insumos; el combo, lo vendible.
    Cafeto.paraDelCatalogo = function (combo) {
        var para = combo.getAttribute('data-para') || '';
        var segun = combo.getAttribute('data-para-segun');
        if (!segun) { return para; }
        var formulario = combo.closest('form');
        var control = formulario && formulario.querySelector('[name="' + segun + '"]');
        var mapa = {};
        (combo.getAttribute('data-para-mapa') || '').split(',').forEach(function (par) {
            var kv = par.split(':');
            if (kv.length === 2) { mapa[kv[0]] = kv[1]; }
        });
        return (control && mapa[control.value]) || para;
    };

    Cafeto.enchufarCatalogo = function (contenedor) {
        if (typeof jQuery === 'undefined' || !jQuery.fn.select2) { return; }
        jQuery(contenedor).find('select.js-catalogo').each(function () {
            var combo = jQuery(this);
            if (combo.data('select2') || !this.isConnected || this.offsetParent === null) { return; }
            if (this.dataset.paraUsado === undefined) { this.dataset.paraUsado = Cafeto.paraDelCatalogo(this); }
            combo.select2({
                width: '100%',
                placeholder: combo.find('option[value=""]').text() || 'Buscá un producto',
                allowClear: true,
                // El contenido del modal y no su cuerpo: el cuerpo tiene
                // scroll, y Select2 ubicaba la lista sin descontar lo que se
                // había bajado (aparecía lejos del campo).
                dropdownParent: combo.closest('.modal-content, body').first(),
                ajax: {
                    url: combo.data('catalogo'),
                    dataType: 'json',
                    delay: 250,
                    cache: true,
                    data: function (p) {
                        var datos = {q: p.term || '', page: p.page || 1};
                        // Qué selector es (receta, combo, beneficio): el
                        // servidor decide qué tipos de producto ofrece.
                        var para = Cafeto.paraDelCatalogo(combo[0]);
                        if (para) { datos.para = para; }
                        return datos;
                    }
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
                Cafeto.inicializar(destino);
            })
            .catch(function () {
                destino.innerHTML = '<div class="aviso aviso-error mb-0"><i class="bi bi-wifi-off"></i>' +
                    '<span>No se pudo cargar. Revisá la conexión y probá de nuevo.</span></div>';
            });
    };

    document.addEventListener('shown.bs.modal', function (evento) {
        var modal = evento.target;
        if (!modal.classList || !modal.classList.contains('modal')) { return; }
        Cafeto.enchufarCatalogo(modal);
        var primero = modal.querySelector('[autofocus], .modal-body input:not([type=hidden]), .modal-body select');
        if (primero) { primero.focus(); }
        marcarDesbordes();
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

    // ---------------------------------------------------------------------
    //  Diseño: la altura real de la barra de arriba (para que un ancla no
    //  quede debajo) y las tablas que no entran a lo ancho (primera
    //  columna fija y sombra que avisa que hay más a la derecha).
    // ---------------------------------------------------------------------
    function medirBarra() {
        var barra = document.querySelector('.barra');
        document.documentElement.style.setProperty('--alto-barra', (barra ? barra.offsetHeight : 0) + 'px');
    }
    function marcarDesbordes() {
        document.querySelectorAll('.table-responsive').forEach(function (caja) {
            caja.classList.toggle('desborda', caja.scrollWidth > caja.clientWidth + 1);
        });
    }
    var tamanios = window.ResizeObserver ? new ResizeObserver(function () { medirBarra(); marcarDesbordes(); }) : null;
    var barraObservada = null;
    function observarBarra() {
        var barra = document.querySelector('.barra');
        if (!tamanios || barra === barraObservada) { return; }
        if (barraObservada) { tamanios.unobserve(barraObservada); }
        barraObservada = barra;
        if (barra) { tamanios.observe(barra); }
    }
    if (!tamanios) { window.addEventListener('resize', function () { medirBarra(); marcarDesbordes(); }); }
    document.addEventListener('toggle', marcarDesbordes, true);

    function todos(contenedor, selector) {
        var lista = Array.prototype.slice.call(contenedor.querySelectorAll(selector));
        if (contenedor.matches && contenedor.matches(selector)) { lista.unshift(contenedor); }
        return lista;
    }

    // ---------------------------------------------------------------------
    //  Cafeto.inicializar(raiz): todo lo que se engancha a un pedazo nuevo.
    //  Cafeto.limpiar(raiz): lo que hay que soltar antes de reemplazarlo.
    // ---------------------------------------------------------------------
    Cafeto.inicializar = function (contenedor) {
        contenedor = contenedor || document;
        Cafeto.activarRenglones(contenedor);
        Cafeto.activarDependencias(contenedor);
        Cafeto.enchufarCatalogo(contenedor);
        estadoDeRed();
        todos(contenedor, 'select[data-lotes]').forEach(function (s) {
            if (s.value && !s.dataset.lotesPedidos) { s.dataset.lotesPedidos = '1'; cargarLotes(s); }
        });
        todos(contenedor, '.js-camara').forEach(function (boton) { boton.hidden = !puedeCamara; });
        todos(contenedor, 'input[data-punto-por-defecto]').forEach(function (punto) {
            if (punto.form) { ajustarPuntoPorDefecto(punto.form); }
        });
        // Sólo una ventana (.modal) se abre sola: el atributo en otro
        // elemento —un botón— dejaba el fondo oscuro y la pantalla trabada.
        todos(contenedor, '.modal[data-modal-auto]').forEach(function (modal) {
            if (modal.dataset.abiertaSola) { return; }
            modal.dataset.abiertaSola = '1';
            bootstrap.Modal.getOrCreateInstance(modal).show();
        });
        if (tamanios) {
            todos(contenedor, '.table-responsive').forEach(function (caja) { tamanios.observe(caja); });
        }
        observarBarra();
        medirBarra();
        marcarDesbordes();
        if (window.CafetoTablero) { window.CafetoTablero.iniciar(contenedor); }
    };

    Cafeto.limpiar = function (contenedor) {
        if (!contenedor) { return; }
        if (window.CafetoTablero) { window.CafetoTablero.liberar(contenedor); }
        if (typeof jQuery !== 'undefined' && jQuery.fn.select2) {
            jQuery(contenedor).find('select.js-catalogo').each(function () {
                var combo = jQuery(this);
                if (combo.data('select2')) { combo.select2('destroy'); }
            });
        }
        if (typeof bootstrap !== 'undefined') {
            todos(contenedor, '.modal').forEach(function (modal) {
                var instancia = bootstrap.Modal.getInstance(modal);
                if (instancia) { instancia.dispose(); }
            });
            todos(contenedor, '[data-bs-toggle="dropdown"]').forEach(function (boton) {
                var instancia = bootstrap.Dropdown.getInstance(boton);
                if (instancia) { instancia.dispose(); }
            });
        }
        if (tamanios) {
            todos(contenedor, '.table-responsive').forEach(function (caja) { tamanios.unobserve(caja); });
        }
    };

    document.addEventListener('DOMContentLoaded', function () { Cafeto.inicializar(document); });

    // ---------------------------------------------------------------------
    //  Navegación parcial (htmx): filtros, paginador, cápsulas, períodos y
    //  menú cambian sólo #contenido, sin volver arriba ni redibujar el
    //  menú. Una pantalla con JavaScript propio que no se reinicia
    //  (data-reinicia) se abre entera: sus scripts sólo corren al cargar.
    // ---------------------------------------------------------------------
    var CLAVE_FIRMAS = 'cafeto.firmas';
    function firmaDe(doc) {
        var partes = [];
        doc.querySelectorAll('head link[rel="stylesheet"]').forEach(function (hoja) {
            partes.push('css:' + hoja.getAttribute('href'));
        });
        doc.querySelectorAll('body script').forEach(function (s) {
            var tipo = (s.getAttribute('type') || '').toLowerCase();
            if (s.hasAttribute('data-global') || (tipo && tipo !== 'text/javascript' && tipo !== 'module')) { return; }
            partes.push((s.hasAttribute('data-reinicia') ? '' : '!') + (s.getAttribute('src') || 'en-linea:' + s.textContent.length));
        });
        return {texto: partes.join('|'), reinicia: partes.every(function (p) { return p.charAt(0) !== '!'; })};
    }
    function firmas() {
        try { return JSON.parse(leer(CLAVE_FIRMAS) || '{}') || {}; } catch (e) { return {}; }
    }
    function anotarFirma(ruta, texto) {
        var todas = firmas();
        if (todas[ruta] === texto) { return; }
        var claves = Object.keys(todas);
        if (claves.length > 300) { claves.slice(0, 100).forEach(function (c) { delete todas[c]; }); }
        todas[ruta] = texto;
        guardar(CLAVE_FIRMAS, JSON.stringify(todas));
    }
    var firmaActual = null;
    document.addEventListener('DOMContentLoaded', function () {
        firmaActual = firmaDe(document);
        anotarFirma(location.pathname, firmaActual.texto);
    });

    function urlDe(elemento) {
        try {
            if (elemento.tagName === 'FORM') {
                var url = new URL(elemento.getAttribute('action') || '', location.href);
                url.search = new URLSearchParams(new FormData(elemento)).toString();
                return url;
            }
            return elemento.href ? new URL(elemento.href) : null;
        } catch (e) { return null; }
    }
    function esDescarga(elemento, url) {
        return elemento.hasAttribute('download') || /\/exportar|\.(pdf|xlsx|csv)$/i.test(url.pathname) ||
               url.searchParams.has('formato');
    }
    function sePuedeParcial(url) {
        if (!firmaActual || !firmaActual.reinicia || url.origin !== location.origin) { return false; }
        var conocida = firmas()[url.pathname];
        return conocida === undefined || conocida === firmaActual.texto;
    }
    function irA(url, elemento) {
        quitarEspera();
        if (elemento && elemento.tagName === 'FORM') { HTMLFormElement.prototype.submit.call(elemento); return; }
        window.location.assign(url.href || url);
    }
    function reducirMovimiento() {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }
    function ponerEspera() {
        raiz.classList.add('cargando-parcial');
        var contenido = document.getElementById('contenido');
        if (contenido) { contenido.setAttribute('aria-busy', 'true'); }
    }
    function quitarEspera() {
        raiz.classList.remove('cargando-parcial');
        var contenido = document.getElementById('contenido');
        if (contenido) { contenido.removeAttribute('aria-busy'); }
    }
    function marcarMenu(enlaces) {
        var lateral = document.querySelector('.lateral');
        if (!lateral) { return; }
        lateral.querySelectorAll('.lateral-link').forEach(function (enlace) {
            var activo = enlaces.indexOf(enlace.getAttribute('href')) >= 0;
            enlace.classList.toggle('activo', activo);
            if (activo) { enlace.setAttribute('aria-current', 'page'); } else { enlace.removeAttribute('aria-current'); }
        });
        lateral.querySelectorAll('details.lateral-desplegable').forEach(function (area) {
            if (area.querySelector('.lateral-link.activo')) {
                area.setAttribute('data-actual', '');
                if (!area.open) { area.open = true; }
            } else {
                area.removeAttribute('data-actual');
            }
        });
    }
    function descripcionDelFoco() {
        var activo = document.activeElement;
        var contenido = document.getElementById('contenido');
        if (!activo || !contenido || activo === document.body) { return null; }
        if (!contenido.contains(activo) && !(activo.form && contenido.contains(activo.form))) { return null; }
        return {id: activo.id, nombre: activo.getAttribute('name'), etiqueta: activo.tagName.toLowerCase(),
                href: activo.tagName === 'A' ? activo.getAttribute('href') : null};
    }
    function devolverFoco(foco) {
        if (!foco || (document.activeElement && document.activeElement !== document.body)) { return; }
        var buscar = function (atributo, valor) {
            return Array.prototype.find.call(document.querySelectorAll('#contenido ' + foco.etiqueta + '[' + atributo + ']'),
                                             function (e) { return e.getAttribute(atributo) === valor; });
        };
        var nuevo = (foco.id && document.getElementById(foco.id)) || (foco.nombre && buscar('name', foco.nombre)) ||
                    (foco.href && buscar('href', foco.href));
        if (nuevo) { nuevo.focus({preventScroll: true}); }
    }

    var enCurso = null;
    document.addEventListener('htmx:confirm', function (evento) {
        var elemento = evento.detail.elt;
        var url = elemento && urlDe(elemento);
        if (!url || (!esDescarga(elemento, url) && sePuedeParcial(url))) { return; }
        evento.preventDefault();
        irA(url, elemento);
    });
    document.addEventListener('htmx:beforeRequest', function (evento) {
        var elemento = evento.detail.elt;
        var desdeMenu = !!(elemento && elemento.closest && elemento.closest('.lateral'));
        enCurso = {desdeMenu: desdeMenu, foco: desdeMenu ? null : descripcionDelFoco(), menu: null,
                   desplazado: window.scrollY};
        if (desdeMenu) {
            var lateral = document.querySelector('.lateral.abierta');
            if (lateral) { lateral.classList.remove('abierta'); }
            var velo = document.querySelector('.velo.visible');
            if (velo) { velo.classList.remove('visible'); }
        }
        ponerEspera();
    });
    document.addEventListener('htmx:beforeSwap', function (evento) {
        var detalle = evento.detail;
        if (!detalle.boosted) { return; }
        var xhr = detalle.xhr;
        var url = new URL(xhr.responseURL || detalle.pathInfo.finalRequestPath, location.href);
        quitarEspera();
        if (xhr.status >= 400) { detalle.shouldSwap = false; irA(url); return; }
        var doc = new DOMParser().parseFromString(xhr.responseText, 'text/html');
        var destino = detalle.target && detalle.target.id;
        var firma = firmaDe(doc);
        anotarFirma(url.pathname, firma.texto);
        if (!destino || !doc.getElementById(destino) || !firma.reinicia || !firmaActual || firma.texto !== firmaActual.texto) {
            detalle.shouldSwap = false;
            irA(url);
            return;
        }
        detalle.pathInfo.anchor = undefined;
        if (enCurso) {
            enCurso.menu = Array.prototype.map.call(doc.querySelectorAll('.lateral .lateral-link.activo'),
                                                    function (enlace) { return enlace.getAttribute('href'); });
        }
        Cafeto.limpiar(detalle.target);
    });
    document.addEventListener('htmx:afterSwap', function (evento) {
        var nuevo = evento.target;
        if (nuevo && !nuevo.isConnected && nuevo.id) { nuevo = document.getElementById(nuevo.id); }
        if (!nuevo || !nuevo.isConnected) { return; }
        if (enCurso && nuevo.id !== 'barra') {
            if (enCurso.menu) { marcarMenu(enCurso.menu); enCurso.menu = null; }
            if (!enCurso.desdeMenu) { window.scrollTo({top: enCurso.desplazado, behavior: 'instant'}); }
        }
        Cafeto.inicializar(nuevo);
    });
    document.addEventListener('htmx:afterSettle', function (evento) {
        var destino = evento.target;
        if (destino && !destino.isConnected && destino.id) { destino = document.getElementById(destino.id); }
        if (window.CafetoTablero && destino && destino.isConnected && destino.id !== 'barra') {
            window.CafetoTablero.iniciar(destino);
        }
        var hecho = enCurso;
        enCurso = null;
        if (!hecho) { return; }
        if (hecho.desdeMenu) {
            var contenido = document.getElementById('contenido');
            if (contenido) { contenido.focus({preventScroll: true}); }
            window.scrollTo({top: 0, behavior: reducirMovimiento() ? 'auto' : 'smooth'});
        } else {
            devolverFoco(hecho.foco);
        }
    });
    document.addEventListener('htmx:afterRequest', function () { quitarEspera(); });
    document.addEventListener('htmx:sendError', function (evento) {
        quitarEspera();
        enCurso = null;
        if (navigator.onLine) {
            var url = urlDe(evento.detail.elt);
            if (url) { irA(url, evento.detail.elt); }
        } else {
            estadoDeRed();
        }
    });

    // ---------------------------------------------------------------------
    //  Ayudas de carga (DD-70)
    //  1. Al salir de un campo vigilado (nombre, CUIT, DNI...) se pregunta
    //     si ya hay algo igual o parecido, y se muestra al lado.
    //  2. El "+" de un campo que apunta a otra tabla despliega esa alta en
    //     la misma pantalla; al guardar, queda elegida en el campo.
    // ---------------------------------------------------------------------
    function columnaDe(campo) {
        return campo.closest('.col-md-6, .col-12') || campo.parentNode;
    }
    function quitarAviso(campo) {
        var viejo = columnaDe(campo).querySelector(':scope > .aviso-parecidos');
        if (viejo) { viejo.remove(); }
    }
    function elegirEnCampo(idCampo, id, texto) {
        var select = document.getElementById(idCampo);
        if (!select) { return false; }
        var opcion = Array.prototype.find.call(select.options, function (o) { return o.value === String(id); });
        if (!opcion) {
            opcion = new Option(texto, id, true, true);
            select.add(opcion);
        }
        select.value = String(id);
        select.dispatchEvent(new Event('change', { bubbles: true }));
        if (window.jQuery && window.jQuery(select).data('select2')) { window.jQuery(select).trigger('change'); }
        return true;
    }
    function cerrarAltaRapida(panel) {
        panel.hidden = true;
        panel.innerHTML = '';
    }

    // Se consulta al salir del campo y también tras una pausa al escribir.
    // Antes se usaba el evento "change", que el navegador NO dispara si el
    // texto final es igual al que había al entrar: borrar "Leche" y volver
    // a escribir "Leche" borraba el aviso y no lo volvía a pedir.
    var PAUSA_PARECIDOS = 700;
    function esCampoParecidos(nodo) {
        return nodo && nodo.matches && nodo.matches('[data-parecidos]');
    }
    document.addEventListener('input', function (evento) {
        var campo = evento.target;
        if (!esCampoParecidos(campo)) { return; }
        quitarAviso(campo);
        delete campo.dataset.parecidosMostrado;
        clearTimeout(campo._esperaParecidos);
        campo._esperaParecidos = setTimeout(function () { consultarParecidos(campo); }, PAUSA_PARECIDOS);
    });
    document.addEventListener('focusout', function (evento) {
        var campo = evento.target;
        if (!esCampoParecidos(campo)) { return; }
        clearTimeout(campo._esperaParecidos);
        consultarParecidos(campo);
    });
    function consultarParecidos(campo) {
        var valor = (campo.value || '').trim();
        // Ya está a la vista el aviso de este mismo texto: no se repite.
        if (campo.dataset.parecidosMostrado === valor &&
                columnaDe(campo).querySelector(':scope > .aviso-parecidos')) { return; }
        quitarAviso(campo);
        delete campo.dataset.parecidosMostrado;
        if (valor.length < 2) { return; }
        var dentroDeAlta = campo.closest('.alta-rapida');
        var excluir = '';
        var formulario = dentroDeAlta ? null : campo.closest('form');
        if (formulario) {
            var m = (formulario.getAttribute('action') || '').match(/\/(\d+)\/editar\/?$/);
            if (m) { excluir = m[1]; }
        }
        var url = campo.dataset.parecidos + '&valor=' + encodeURIComponent(valor) +
            (excluir ? '&excluir=' + excluir : '');
        fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (datos) {
                if (!datos || !datos.resultados || !datos.resultados.length) { return; }
                if ((campo.value || '').trim() !== valor) { return; }   // ya cambió
                var exacto = campo.dataset.parecidosModo === 'exacto' ||
                    datos.resultados.some(function (r) { return r.igual; });
                var aviso = document.createElement('div');
                aviso.className = 'aviso-parecidos aviso ' + (exacto ? 'aviso-error' : 'aviso-aviso') + ' py-2 mt-2 mb-0 small';
                aviso.setAttribute('role', 'status');
                var titulo = exacto
                    ? 'Ya existe ' + escapar(datos.singular) + ' con ese dato:'
                    : '¿Es alguno de estos? Ya hay parecidos cargados:';
                var html = '<i class="bi ' + (exacto ? 'bi-exclamation-octagon-fill' : 'bi-lightbulb-fill') + '"></i><div>' +
                    '<div class="mb-1">' + titulo + '</div><div class="d-flex flex-wrap gap-1">';
                datos.resultados.forEach(function (r) {
                    var etiqueta = escapar(r.texto) + (r.baja ? ' <span class="text-tenue">· dado de baja</span>' : '');
                    if (dentroDeAlta) {
                        html += '<button type="button" class="btn btn-sm btn-secundario js-usar-existente" data-id="' + r.id +
                            '" data-texto="' + escapar(r.texto) + '">' + etiqueta + '</button>';
                    } else {
                        html += '<a class="btn btn-sm btn-secundario" href="' + escapar(r.url) + '">' + etiqueta + '</a>';
                    }
                });
                html += '</div><div class="mt-1 text-secondary">' + (dentroDeAlta
                    ? 'Tocá el que corresponda y queda elegido, sin cargarlo de nuevo.'
                    : 'Si es uno de estos, no hace falta cargarlo: tocalo para verlo' +
                      (datos.resultados.some(function (r) { return r.baja; }) ? ' o reactivarlo.' : '.')) + '</div></div>';
                aviso.innerHTML = html;
                quitarAviso(campo);           // dos consultas cruzadas: queda una sola
                columnaDe(campo).appendChild(aviso);
                campo.dataset.parecidosMostrado = valor;
            })
            .catch(function () { /* el aviso es una ayuda: sin red, se guarda y avisa el servidor */ });
    }

    function marcarCamposDelPanel(panel) {
        // No son del formulario de afuera: así no viajan con él ni aparecen
        // en su resumen de confirmación.
        panel.querySelectorAll('input, select, textarea').forEach(function (c) {
            c.setAttribute('form', 'alta-rapida-sin-formulario');
        });
    }
    function abrirAltaRapida(boton) {
        var destino = boton.dataset.destino;
        var panel = document.querySelector('.alta-rapida-panel[data-para="' + destino + '"]');
        if (!panel) { return; }
        if (!panel.hidden) { cerrarAltaRapida(panel); return; }
        panel.dataset.url = boton.dataset.url;
        panel.hidden = false;
        panel.innerHTML = '<p class="text-secondary small mt-2 mb-0"><span class="spinner-border spinner-border-sm me-1"></span>Cargando…</p>';
        fetch(boton.dataset.url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.text(); })
            .then(function (html) {
                panel.innerHTML = html;
                marcarCamposDelPanel(panel);
                var primero = panel.querySelector('input:not([type=hidden]), select, textarea');
                if (primero) { primero.focus(); }
            })
            .catch(function () {
                panel.innerHTML = '<div class="aviso aviso-error small mt-2 mb-0"><i class="bi bi-wifi-off"></i><span>No se pudo abrir el alta. Probá de nuevo.</span></div>';
            });
    }
    function guardarAltaRapida(panel) {
        var datos = new FormData();
        panel.querySelectorAll('input[name], select[name], textarea[name]').forEach(function (c) {
            if ((c.type === 'checkbox' || c.type === 'radio') && !c.checked) { return; }
            datos.append(c.name, c.value);
        });
        var csrf = panel.closest('form') && panel.closest('form').querySelector('[name=csrfmiddlewaretoken]');
        if (csrf) { datos.append('csrfmiddlewaretoken', csrf.value); }
        datos.append('rapido', '1');
        var boton = panel.querySelector('.js-alta-rapida-guardar');
        if (boton) { boton.disabled = true; }
        fetch(panel.dataset.url, { method: 'POST', body: datos, credentials: 'same-origin',
                                   headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) {
                var tipo = r.headers.get('content-type') || '';
                return (tipo.indexOf('json') >= 0 ? r.json() : r.text()).then(function (cuerpo) { return { r: r, cuerpo: cuerpo }; });
            })
            .then(function (res) {
                if (res.r.ok && res.cuerpo && res.cuerpo.ok) {
                    var destino = panel.dataset.para;
                    elegirEnCampo(destino, res.cuerpo.id, res.cuerpo.texto);
                    cerrarAltaRapida(panel);
                    var campo = document.getElementById(destino);
                    if (campo) {
                        var ok = document.createElement('div');
                        ok.className = 'form-text text-exito';
                        ok.innerHTML = '<i class="bi bi-check-circle-fill me-1"></i>' + escapar(res.cuerpo.mensaje) + ' Quedó elegido.';
                        panel.insertAdjacentElement('beforebegin', ok);
                        setTimeout(function () { ok.remove(); }, 6000);
                    }
                } else if (typeof res.cuerpo === 'string') {
                    panel.innerHTML = res.cuerpo;      // el alta con sus errores
                    marcarCamposDelPanel(panel);
                } else {
                    throw new Error('respuesta inesperada');
                }
            })
            .catch(function () {
                if (boton) { boton.disabled = false; }
                var error = document.createElement('div');
                error.className = 'aviso aviso-error small mt-2 mb-0';
                error.innerHTML = '<i class="bi bi-wifi-off"></i><span>No se pudo guardar. Revisá la conexión y probá de nuevo.</span>';
                panel.appendChild(error);
            });
    }
    document.addEventListener('click', function (evento) {
        var abrir = evento.target.closest('.js-alta-rapida');
        if (abrir) { evento.preventDefault(); abrirAltaRapida(abrir); return; }
        var panel = evento.target.closest('.alta-rapida-panel');
        if (!panel) { return; }
        if (evento.target.closest('.js-alta-rapida-cancelar')) { cerrarAltaRapida(panel); return; }
        if (evento.target.closest('.js-alta-rapida-guardar')) { guardarAltaRapida(panel); return; }
        var existente = evento.target.closest('.js-usar-existente');
        if (existente) {
            elegirEnCampo(panel.dataset.para, existente.dataset.id, existente.dataset.texto);
            cerrarAltaRapida(panel);
        }
    });
    // Enter dentro del alta rápida guarda esa alta, no el formulario de afuera.
    document.addEventListener('keydown', function (evento) {
        if (evento.key !== 'Enter') { return; }
        var panel = evento.target.closest && evento.target.closest('.alta-rapida-panel');
        if (!panel || evento.target.tagName === 'TEXTAREA') { return; }
        evento.preventDefault();
        guardarAltaRapida(panel);
    });
    // ---------------------------------------------------------------------
    //  Calculadora del alta de productos (DD-86): [data-calculo] en costo,
    //  iva, utilidad y precio.  Precio = Costo × (1 + IVA) × (1 + Utilidad).
    //  Va en los dos sentidos: se cambia la utilidad y sale el precio; se
    //  cambia el precio y sale la utilidad. El servidor guarda el precio.
    // ---------------------------------------------------------------------
    //  El costo, el IVA, la utilidad y el precio se leen con las mismas
    //  reglas que el servidor (leerNumero): el precio "1.500" que dibuja
    //  Django son mil quinientos pesos, no uno con cincuenta.
    function numeroDe(control) {
        if (!control || control.value === '') { return null; }
        var valor = leerNumero(control.value, decimalesDe(control));
        return isNaN(valor) ? null : valor;
    }
    function recalcular(origen) {
        var formulario = origen.form;
        if (!formulario) { return; }
        var campo = function (nombre) { return formulario.querySelector('[data-calculo="' + nombre + '"]'); };
        var precio = campo('precio'), utilidad = campo('utilidad');
        if (!precio || !utilidad) { return; }
        var costo = numeroDe(campo('costo')), iva = numeroDe(campo('iva')) || 0;
        if (costo === null || costo <= 0) { return; }
        var base = costo * (1 + iva / 100);
        if (!isFinite(base) || base <= 0) { return; }
        var queCambio = origen.dataset.calculo;
        if (queCambio === 'precio' || (queCambio !== 'utilidad' && utilidad.value === '')) {
            var p = numeroDe(precio);
            if (p !== null && p > 0) { escribirNumero(utilidad, (p / base - 1) * 100, 2); }
            return;
        }
        var u = numeroDe(utilidad);
        if (u !== null) { escribirNumero(precio, base * (1 + u / 100), 2); }
    }
    ['input', 'change'].forEach(function (tipo) {
        document.addEventListener(tipo, function (evento) {
            var control = evento.target.closest && evento.target.closest('[data-calculo]');
            if (control) { recalcular(control); }
        });
    });
    document.addEventListener('click', function (evento) {
        var paso = evento.target.closest('.js-redondeo [data-paso]');
        if (paso) {
            var precio = document.getElementById(paso.closest('.js-redondeo').dataset.destino);
            var valor = numeroDe(precio), salto = parseFloat(paso.dataset.paso);
            if (precio && valor !== null && valor > 0 && salto > 0) {
                escribirNumero(precio, Math.max(salto, Math.round(valor / salto) * salto), 2);
                recalcular(precio);
            }
            return;
        }
        var generar = evento.target.closest('.js-codigo-interno');
        if (!generar) { return; }
        var destino = document.getElementById(generar.dataset.destino);
        generar.disabled = true;
        fetch(generar.dataset.url, {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}})
            .then(function (respuesta) { return respuesta.json(); })
            .then(function (datos) { if (datos.codigo && destino) { destino.value = datos.codigo; destino.focus(); } })
            .catch(function () {})
            .then(function () { generar.disabled = false; });
    });
    // Las casillas de una importación viajan en el formulario de confirmar.
    document.addEventListener('change', function (evento) {
        var casilla = evento.target.closest && evento.target.closest('.js-opcion-importar');
        if (!casilla) { return; }
        document.querySelectorAll('input[type="hidden"][data-opcion="' + casilla.dataset.opcion + '"]')
            .forEach(function (oculto) { oculto.value = casilla.checked ? '1' : ''; });
    });
    // ---------------------------------------------------------------------
    //  Leer un código con la cámara (DD-91): botones .js-camara[data-destino].
    //  Usa el lector de códigos del propio navegador (BarcodeDetector). Donde
    //  no existe, o la página no está en una conexión segura (la cámara sólo
    //  se presta con https o en localhost), el botón directamente no aparece.
    // ---------------------------------------------------------------------
    var puedeCamara = 'BarcodeDetector' in window && navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
    function leerConCamara(destino) {
        var capa = document.createElement('div');
        capa.className = 'camara-capa';
        capa.setAttribute('role', 'dialog');
        capa.setAttribute('aria-modal', 'true');
        capa.setAttribute('aria-label', 'Leer un código con la cámara');
        capa.innerHTML = '<div class="camara-caja"><video playsinline muted></video>' +
            '<p class="camara-ayuda" role="status">Apuntá al código de barras.</p>' +
            '<button type="button" class="btn btn-secundario btn-xl w-100">Cancelar</button></div>';
        document.body.appendChild(capa);
        var video = capa.querySelector('video'), ayuda = capa.querySelector('.camara-ayuda');
        var cancelar = capa.querySelector('button'), flujo = null, reloj = null, cerrado = false;
        function cerrar(codigo) {
            if (cerrado) { return; }
            cerrado = true;
            window.clearInterval(reloj);
            if (flujo) { flujo.getTracks().forEach(function (pista) { pista.stop(); }); }
            capa.remove();
            destino.focus();
            if (codigo) {
                destino.value = codigo;
                destino.dispatchEvent(new Event('input', {bubbles: true}));
                destino.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}));
            }
        }
        cancelar.addEventListener('click', function () { cerrar(null); });
        capa.addEventListener('keydown', function (e) { if (e.key === 'Escape') { cerrar(null); } });
        cancelar.focus();
        var formatos = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf'];
        Promise.resolve(window.BarcodeDetector.getSupportedFormats ? window.BarcodeDetector.getSupportedFormats() : formatos)
            .then(function (hay) {
                var lector = new window.BarcodeDetector({formats: formatos.filter(function (f) { return hay.indexOf(f) !== -1; })});
                return navigator.mediaDevices.getUserMedia({video: {facingMode: {ideal: 'environment'}}, audio: false})
                    .then(function (camara) {
                        if (cerrado) { camara.getTracks().forEach(function (pista) { pista.stop(); }); return; }
                        flujo = camara; video.srcObject = camara;
                        return video.play().then(function () {
                            reloj = window.setInterval(function () {
                                lector.detect(video).then(function (codigos) {
                                    if (codigos.length && codigos[0].rawValue) { cerrar(codigos[0].rawValue); }
                                }).catch(function () { /* un cuadro que no se pudo leer: se prueba el siguiente */ });
                            }, 250);
                        });
                    });
            })
            .catch(function () {
                ayuda.textContent = 'No se pudo usar la cámara. Fijate que el navegador tenga permiso, o escribí el código.';
            });
    }
    document.addEventListener('click', function (evento) {
        var boton = evento.target.closest && evento.target.closest('.js-camara');
        var destino = boton && document.querySelector(boton.dataset.destino);
        if (destino) { leerConCamara(destino); }
    });
})();

/* «Imprimir» sin JavaScript en línea (las páginas sueltas usan imprimir.js). */
(function () {
    'use strict';
    document.addEventListener('click', function (evento) {
        if (evento.target.closest && evento.target.closest('[data-imprimir]')) { window.print(); }
    });
})();
