/* ==========================================================================
   Cafeto · renglones de producto (compras y órdenes de compra)
   --------------------------------------------------------------------------
   Arma en el navegador una lista de renglones {producto, cantidad, costo,
   lotes[]} y la deja como JSON en un <input type=hidden>. El servidor la
   vuelve a validar entera: esto es sólo comodidad para cargar.

     <div data-renglones
          data-campo="#renglones"               input oculto con el JSON
          data-info="/stock/api/lotes/"         datos del producto elegido
          data-con-lotes="1"                    pide lotes si el producto vence
          data-precio="Costo unitario">
       <select class="js-catalogo js-nuevo-producto" data-catalogo="..."></select>
       <div class="js-lista"></div>
       <span class="js-total"></span>
     </div>
   ========================================================================== */
(function () {
    'use strict';
    var pesos = new Intl.NumberFormat('es-AR', {style: 'currency', currency: 'ARS'});
    function numero(texto) {
        // Misma regla que el servidor (nucleo.formularios.leer_numero).
        texto = String(texto || '').trim().replace(/\s|\$/g, '');
        if (!texto) { return NaN; }
        if (texto.indexOf(',') !== -1 && texto.indexOf('.') !== -1) {
            var dec = texto.lastIndexOf(',') > texto.lastIndexOf('.') ? ',' : '.';
            texto = texto.split(dec === ',' ? '.' : ',').join('').replace(dec, '.');
        } else if (texto.indexOf(',') !== -1) {
            texto = texto.replace(',', '.');
        }
        return Number(texto);
    }
    function el(tag, clase, texto) {
        var n = document.createElement(tag);
        if (clase) { n.className = clase; }
        if (texto != null) { n.textContent = texto; }
        return n;
    }
    function campo(valor, placeholder, ancho, tipo) {
        var input = el('input', 'form-control form-control-sm');
        input.type = tipo || 'text';
        if (!tipo) { input.inputMode = 'decimal'; }
        input.value = valor || '';
        input.placeholder = placeholder || '';
        if (ancho) { input.style.width = ancho; }
        input.setAttribute('aria-label', placeholder || '');
        return input;
    }

    function Renglones(raiz) {
        var oculto = document.querySelector(raiz.dataset.campo);
        var lista = raiz.querySelector('.js-lista');
        var total = raiz.querySelector('.js-total');
        var conLotes = raiz.dataset.conLotes === '1';
        var etiquetaPrecio = raiz.dataset.precio || 'Precio';
        var renglones = [];
        try { renglones = JSON.parse(oculto.value || '[]') || []; } catch (e) { renglones = []; }

        function guardar() {
            oculto.value = JSON.stringify(renglones);
            var suma = renglones.reduce(function (acc, r) {
                var v = numero(r.cantidad) * numero(r.costo);
                return acc + (isNaN(v) ? 0 : v);
            }, 0);
            if (total) { total.textContent = pesos.format(suma); }
        }

        function dibujar() {
            lista.textContent = '';
            if (!renglones.length) {
                lista.appendChild(el('p', 'text-secondary small mb-0', 'Todavía no cargaste ningún producto.'));
            }
            renglones.forEach(function (r, i) {
                var fila = el('div', 'renglon-carga');
                var cabeza = el('div', 'd-flex flex-wrap align-items-center gap-2');
                cabeza.appendChild(el('strong', 'me-auto', r.nombre));
                var cant = campo(r.cantidad, 'Cantidad', '6.5rem');
                cant.addEventListener('input', function () { r.cantidad = cant.value; guardar(); });
                var precio = campo(r.costo, etiquetaPrecio, '8rem');
                precio.addEventListener('input', function () { r.costo = precio.value; guardar(); });
                var quitar = el('button', 'btn btn-sm btn-outline-danger'); quitar.type = 'button';
                quitar.innerHTML = '<i class="bi bi-x-lg"></i>'; quitar.title = 'Quitar renglón';
                quitar.addEventListener('click', function () { renglones.splice(i, 1); guardar(); dibujar(); });
                cabeza.append(cant, el('span', 'small text-tenue', r.unidad || ''), precio, quitar);
                fila.appendChild(cabeza);

                if (conLotes && r.controla) {
                    if (!r.lotes || !r.lotes.length) { r.lotes = [{vence: '', cantidad: '', numero: ''}]; }
                    var lotes = el('div', 'd-flex flex-column gap-1 mt-2 ps-3 border-start');
                    lotes.appendChild(el('span', 'small text-marca', 'Va por lote: cargá cada vencimiento'));
                    r.lotes.forEach(function (l, j) {
                        var lineaLote = el('div', 'd-flex flex-wrap gap-2 align-items-center');
                        var vence = campo(l.vence, 'Vence el', '10rem', 'date');
                        vence.addEventListener('input', function () { l.vence = vence.value; guardar(); });
                        var cl = campo(l.cantidad, r.lotes.length === 1 ? 'Todo' : 'Cantidad', '6rem');
                        cl.addEventListener('input', function () { l.cantidad = cl.value; guardar(); });
                        var nl = campo(l.numero, 'N° de lote', '8rem', 'text');
                        nl.addEventListener('input', function () { l.numero = nl.value; guardar(); });
                        lineaLote.append(vence, cl, nl);
                        if (r.lotes.length > 1) {
                            var sacar = el('button', 'btn btn-sm btn-link text-peligro p-0'); sacar.type = 'button';
                            sacar.innerHTML = '<i class="bi bi-x-circle"></i>'; sacar.title = 'Quitar lote';
                            sacar.addEventListener('click', function () { r.lotes.splice(j, 1); guardar(); dibujar(); });
                            lineaLote.appendChild(sacar);
                        }
                        lotes.appendChild(lineaLote);
                    });
                    var otro = el('button', 'btn btn-sm btn-link p-0 align-self-start', '+ Otro vencimiento'); otro.type = 'button';
                    otro.addEventListener('click', function () { r.lotes.push({vence: '', cantidad: '', numero: ''}); guardar(); dibujar(); });
                    lotes.appendChild(otro);
                    fila.appendChild(lotes);
                }
                lista.appendChild(fila);
            });
            guardar();
        }

        function agregar(id) {
            if (renglones.some(function (r) { return r.producto === id; })) {
                Cafeto.confirmar({titulo: 'Ya está cargado', texto: 'Ese producto ya tiene un renglón: sumá la cantidad ahí.', aceptar: 'Entendido'});
                return;
            }
            fetch(raiz.dataset.info + '?producto=' + id, {headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'})
                .then(function (r) { return r.json(); })
                .then(function (p) {
                    renglones.push({producto: id, nombre: p.nombre, controla: p.controla, unidad: p.unidad,
                                    cantidad: '', costo: p.costo && Number(p.costo) > 0 ? String(p.costo).replace('.', ',') : '', lotes: []});
                    dibujar();
                    var entradas = lista.querySelectorAll('.renglon-carga:last-child input');
                    if (entradas.length) { entradas[0].focus(); }
                });
        }

        // Otra parte de la pantalla (el lector de facturas) suma renglones
        // ya armados: si el producto ya estaba, se reemplaza su renglón.
        raiz.addEventListener('cafeto:sumar-renglones', function (evento) {
            (evento.detail || []).forEach(function (nuevo) {
                var previo = renglones.findIndex(function (r) { return r.producto === nuevo.producto; });
                if (previo >= 0) { renglones[previo] = nuevo; } else { renglones.push(nuevo); }
            });
            dibujar();
        });
        raiz.addEventListener('cafeto:vaciar-renglones', function () { renglones = []; dibujar(); });

        if (typeof jQuery !== 'undefined') {
            jQuery(raiz).on('change', '.js-nuevo-producto', function () {
                var id = Number(this.value);
                if (id) { agregar(id); }
                jQuery(this).val(null).trigger('change.select2');
            });
        }
        dibujar();
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-renglones]').forEach(function (raiz) { Renglones(raiz); });
    });
})();
