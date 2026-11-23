/* ==========================================================================
   Cafeto · escanear la factura en «Recibir mercadería» (DD-98)

   Manda la foto, el PDF o la factura de muestra a /compras/escanear-factura/
   y con lo leído llena el formulario: proveedor, comprobante, fechas y los
   renglones (por el evento cafeto:sumar-renglones de renglones.js). Lo que
   no está en el catálogo se ofrece para darlo de alta en un clic. No guarda
   nada: la compra se registra con el botón de siempre.
   ========================================================================== */
(function () {
    'use strict';
    var raiz = document.getElementById('escaner');
    if (!raiz) { return; }
    var estado = document.getElementById('estadoEscaner');
    var nuevos = document.getElementById('nuevosEscaner');
    var listaNuevos = nuevos.querySelector('.js-nuevos');
    var csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
    var rubros = [];
    try { rubros = JSON.parse(document.getElementById('rubrosEscaner').textContent).rubros || []; } catch (e) { rubros = []; }
    var origenes = {muestra: 'Factura de muestra', n8n: 'Leída por la automatización', local: 'Leída en este equipo'};

    function el(tag, clase, texto) {
        var n = document.createElement(tag);
        if (clase) { n.className = clase; }
        if (texto != null) { n.textContent = texto; }
        return n;
    }
    function plural(cantidad, uno, varios) { return cantidad + ' ' + (cantidad === 1 ? uno : varios); }
    function aviso(tipo, texto, icono) {
        estado.textContent = '';
        var caja = el('div', 'aviso aviso-' + tipo + ' mb-0');
        caja.appendChild(el('i', 'bi ' + (icono || 'bi-info-circle-fill')));
        var cuerpo = el('span', null, texto);
        caja.appendChild(cuerpo);
        estado.appendChild(caja);
        return cuerpo;
    }
    function coma(valor) { return String(valor == null ? '' : valor).replace('.', ','); }
    function postJson(url, datos) {
        return fetch(url, {method: 'POST', credentials: 'same-origin', body: JSON.stringify(datos),
                           headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest'}})
            .then(function (r) { return r.json(); });
    }
    function poner(id, valor) {
        var campo = document.getElementById(id);
        if (campo && valor) { campo.value = valor; campo.dispatchEvent(new Event('change', {bubbles: true})); }
    }
    function elegirProveedor(id, texto) {
        var select = document.getElementById('proveedor');
        if (!select) { return; }
        if (!select.querySelector('option[value="' + id + '"]')) {
            var opcion = el('option', null, texto); opcion.value = id; select.appendChild(opcion);
        }
        select.value = String(id);
        if (window.jQuery) { window.jQuery(select).trigger('change'); }
        select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    function sumarRenglones(renglones) {
        var destino = document.querySelector('[data-renglones]');
        if (!destino || !renglones.length) { return; }
        destino.dispatchEvent(new CustomEvent('cafeto:sumar-renglones', {detail: renglones.map(function (r) {
            return {producto: r.producto, nombre: r.nombre, controla: r.controla, unidad: r.unidad || '',
                    cantidad: coma(r.cantidad), costo: coma(r.costo),
                    lotes: (r.lotes || []).map(function (l) { return {vence: l.vence || '', cantidad: l.cantidad || '', numero: l.numero || ''}; })};
        })}));
    }

    function proveedorNuevo(proveedor, cuerpo) {
        if (!proveedor.nombre) { return; }
        var texto = ' El proveedor "' + proveedor.nombre + '"' + (proveedor.cuit ? ' (CUIT ' + proveedor.cuit + ')' : '') + ' no está cargado.';
        cuerpo.appendChild(document.createTextNode(texto));
        if (raiz.dataset.puedeProveedor !== '1') {
            cuerpo.appendChild(document.createTextNode(' Pedile a quien administra que lo dé de alta, o elegilo de la lista si ya está con otro nombre.'));
            return;
        }
        var boton = el('button', 'btn btn-sm btn-primary ms-2', 'Dar de alta al proveedor');
        boton.type = 'button';
        boton.addEventListener('click', function () {
            boton.disabled = true;
            postJson(raiz.dataset.altaProveedor, {nombre: proveedor.nombre, cuit: proveedor.cuit}).then(function (r) {
                if (!r.ok) { boton.disabled = false; aviso('error', r.error, 'bi-x-circle-fill'); return; }
                elegirProveedor(r.id, r.texto);
                boton.replaceWith(el('strong', 'text-exito ms-1', r.mensaje + ' Quedó elegido.'));
            }).catch(function () { boton.disabled = false; aviso('error', 'No se pudo dar de alta. Revisá la conexión.', 'bi-wifi-off'); });
        });
        cuerpo.appendChild(boton);
    }

    function filaNueva(nuevo) {
        var fila = el('div', 'd-flex flex-wrap align-items-end gap-2 border-top pt-2');
        var nombre = el('input', 'form-control form-control-sm'); nombre.value = nuevo.descripcion; nombre.style.minWidth = '14rem';
        nombre.setAttribute('aria-label', 'Nombre del producto nuevo');
        var grupoNombre = el('label', 'flex-grow-1 small text-secondary', 'Producto' + (nuevo.codigo ? ' · código ' + nuevo.codigo : ''));
        grupoNombre.appendChild(nombre);
        var rubro = el('select', 'form-select form-select-sm'); rubro.setAttribute('aria-label', 'Rubro');
        var sugerido = el('option', null, 'Rubro sugerido'); sugerido.value = ''; rubro.appendChild(sugerido);
        rubros.forEach(function (r) { var o = el('option', null, r.nombre); o.value = r.id; rubro.appendChild(o); });
        var grupoRubro = el('label', 'small text-secondary', 'Rubro'); grupoRubro.appendChild(rubro);
        var costo = Number(nuevo.costo) || 0;
        var precio = el('input', 'form-control form-control-sm'); precio.inputMode = 'decimal'; precio.style.width = '8rem';
        precio.value = coma((costo * 1.4).toFixed(2)); precio.setAttribute('aria-label', 'Precio de venta');
        var grupoPrecio = el('label', 'small text-secondary', 'Precio de venta'); grupoPrecio.appendChild(precio);
        var datos = el('span', 'small text-secondary align-self-center', nuevo.cantidad + ' × $ ' + coma(nuevo.costo));
        var boton = el('button', 'btn btn-sm btn-primary js-dar-alta', 'Dar de alta y sumar'); boton.type = 'button';
        var quitar = el('button', 'btn btn-sm btn-link text-secondary', 'No va en esta compra'); quitar.type = 'button';
        quitar.addEventListener('click', function () {
            fila.remove();
            if (!listaNuevos.querySelector('button.js-dar-alta')) { nuevos.hidden = true; }
        });
        boton.addEventListener('click', function () {
            boton.disabled = true;
            var proveedor = (document.getElementById('proveedor') || {}).value || '';
            postJson(raiz.dataset.altaProducto, {nombre: nombre.value, codigo: nuevo.codigo, costo: nuevo.costo,
                                                 precio_venta: precio.value, rubro: rubro.value, proveedor: proveedor,
                                                 controla: (nuevo.lotes || []).length > 0}).then(function (r) {
                if (!r.ok) { boton.disabled = false; aviso('error', r.error, 'bi-x-circle-fill'); return; }
                sumarRenglones([{producto: r.producto, nombre: r.nombre, controla: r.controla, unidad: r.unidad,
                                 cantidad: nuevo.cantidad, costo: nuevo.costo, lotes: r.controla ? nuevo.lotes : []}]);
                fila.textContent = '';
                fila.appendChild(el('span', 'text-exito small', '✓ ' + r.nombre + ' quedó dado de alta (' + r.rubro + ', precio $ ' + coma(r.precio_venta) + ') y se sumó a la compra.'));
            }).catch(function () { boton.disabled = false; aviso('error', 'No se pudo dar de alta. Revisá la conexión.', 'bi-wifi-off'); });
        });
        fila.append(grupoNombre, datos, grupoRubro, grupoPrecio);
        if (raiz.dataset.puedeProducto === '1') { fila.appendChild(boton); }
        fila.appendChild(quitar);
        return fila;
    }

    function aplicar(r) {
        if (!r.ok) { aviso('error', r.error, 'bi-x-circle-fill'); return; }
        if (r.proveedor.existe) { elegirProveedor(r.proveedor.id, r.proveedor.nombre); }
        poner('tipo', r.tipo_comprobante);
        poner('punto', r.punto_venta);
        poner('numero', r.numero);
        poner('fecha', r.fecha);
        poner('vence_pago', r.vence);
        sumarRenglones(r.renglones);
        var total = r.renglones.length + r.nuevos.length;
        var cuerpo = aviso('exito', (origenes[r.origen] || 'Factura leída') + ': ' + plural(total, 'renglón', 'renglones') + ', ' +
                           r.renglones.length + ' ya en el catálogo. Controlá todo contra el papel antes de guardar.',
                           'bi-check-circle-fill');
        if (!r.proveedor.existe) { proveedorNuevo(r.proveedor, cuerpo); }
        listaNuevos.textContent = '';
        r.nuevos.forEach(function (n) { listaNuevos.appendChild(filaNueva(n)); });
        nuevos.hidden = !r.nuevos.length;
        if (r.nuevos.length && raiz.dataset.puedeProducto !== '1') {
            listaNuevos.appendChild(el('p', 'small text-secondary mb-0', 'Tu perfil no da de alta productos: pedíselo a quien administra o sacalos de la factura.'));
        }
    }

    //  Una factura que llegó por WhatsApp o por correo ya viene leída: la
    //  pantalla se abre con ?factura=N (o con el enlace de pre-carga) y los
    //  renglones reconocidos puestos. Los que NO se reconocieron venían sólo
    //  como una lista de texto en el aviso de arriba: había que darlos de
    //  alta a mano en Productos, en otra pestaña, y volver a cargar todo.
    //  Acá se arman las mismas filas de «Dar de alta y sumar» que al
    //  escanear con la cámara.
    function nuevosDeLaPrecarga() {
        var caja = document.getElementById('nuevosPrecarga');
        if (!caja) { return; }
        var lista = [];
        try { lista = JSON.parse(caja.textContent).nuevos || []; } catch (e) { return; }
        if (!lista.length) { return; }
        listaNuevos.textContent = '';
        lista.forEach(function (n) { listaNuevos.appendChild(filaNueva(n)); });
        nuevos.hidden = false;
        if (raiz.dataset.puedeProducto !== '1') {
            listaNuevos.appendChild(el('p', 'small text-secondary mb-0',
                'Tu perfil no da de alta productos: pedíselo a quien administra o sacalos de la factura.'));
        }
    }
    nuevosDeLaPrecarga();

    function enviar(datos) {
        aviso('info', 'Leyendo la factura…', 'bi-hourglass-split');
        datos.append('csrfmiddlewaretoken', csrf);
        fetch(raiz.dataset.url, {method: 'POST', body: datos, credentials: 'same-origin',
                                 headers: {'X-Requested-With': 'XMLHttpRequest'}})
            .then(function (r) { return r.json(); })
            .then(aplicar)
            .catch(function () { aviso('error', 'No se pudo leer la factura. Revisá la conexión y probá de nuevo.', 'bi-wifi-off'); });
    }

    document.getElementById('archivoFactura').addEventListener('change', function () {
        if (!this.files.length) { return; }
        var datos = new FormData();
        datos.append('archivo', this.files[0]);
        enviar(datos);
        this.value = '';
    });
    var muestra = document.getElementById('facturaMuestra');
    if (muestra) {
        muestra.addEventListener('click', function () {
            var datos = new FormData();
            datos.append('muestra', '1');
            enviar(datos);
        });
    }

    // Antes de «¿Guardamos la compra?»: si quedaron productos de la factura
    // sin dar de alta, la compra saldría incompleta (o en $ 0,00). Se frena
    // acá, antes del modal (en la captura de window, que corre primero).
    window.addEventListener('submit', function (evento) {
        var form = evento.target;
        if (!form.contains || !form.querySelector('[data-renglones]')) { return; }
        var pendientes = listaNuevos.querySelectorAll('button.js-dar-alta:not([disabled])').length;
        if (nuevos.hidden || !pendientes) { return; }
        evento.preventDefault();
        evento.stopImmediatePropagation();
        aviso('error', (pendientes === 1 ? 'Queda 1 producto' : 'Quedan ' + pendientes + ' productos') +
              ' de la factura sin dar de alta. ' + (pendientes === 1 ? 'Dalo de alta ' : 'Dalos de alta ') +
              '("Dar de alta y sumar") o sacalos de la factura antes de guardar la compra.', 'bi-exclamation-octagon-fill');
        nuevos.scrollIntoView({behavior: 'smooth', block: 'center'});
        var primero = listaNuevos.querySelector('button.js-dar-alta:not([disabled])');
        if (primero) { primero.focus({preventScroll: true}); }
    }, true);
})();
