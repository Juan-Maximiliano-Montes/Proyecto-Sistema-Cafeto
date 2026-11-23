(function () {
    'use strict';
    var raiz = document.getElementById('mostrador');
    var csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
    var CLAVE = 'cafeto.ticket.' + raiz.dataset.turno;

    // ------------------------------------------------------------------
    //  Estado: lo que pide el cliente. Se guarda en el navegador para que
    //  recargar la página no borre el ticket a medio armar.
    // ------------------------------------------------------------------
    var estado = {items: [], pagos: [], token: raiz.dataset.token, cliente: null, beneficios: []};
    try {
        var guardado = JSON.parse(window.sessionStorage.getItem(CLAVE) || 'null');
        if (guardado && Array.isArray(guardado.items)) {
            estado = guardado;
            if (!Array.isArray(estado.beneficios)) { estado.beneficios = []; }
        }
    } catch (e) { /* sin almacenamiento: arranca vacío */ }
    function persistir() {
        try { window.sessionStorage.setItem(CLAVE, JSON.stringify(estado)); } catch (e) { /* nada */ }
    }
    var nombres = {};       // id -> {nombre, detalle, fraccionable, unidad}
    (estado.items || []).forEach(function (i) { nombres[i.producto] = i.info || {}; });

    // ------------------------------------------------------------------
    //  Formato argentino
    // ------------------------------------------------------------------
    var pesos = new Intl.NumberFormat('es-AR', {style: 'currency', currency: 'ARS'});
    var numero = new Intl.NumberFormat('es-AR', {maximumFractionDigits: 3});

    //  Las cantidades viajan como texto y se tipean a mano: hay que leerlas
    //  con las MISMAS reglas que el servidor (Cafeto.leerNumero, que replica
    //  leer_numero con tres decimales, los de un kilo).
    //
    //  Sin esto, Number('abc') daba NaN, se escribía la cadena "NaN" en la
    //  cantidad del renglón, se guardaba así en el navegador y el ticket
    //  entero quedaba roto hasta vaciarlo: cada cotización contestaba un
    //  error que no decía nada y los botones + y − seguían sumando sobre
    //  NaN. Ahora lo que no es un número no entra.
    function cantidadDe(texto, porDefecto) {
        var leer = window.Cafeto && window.Cafeto.leerNumero;
        var valor = leer ? leer(texto, 3) : Number(String(texto).replace(',', '.'));
        if (typeof valor !== 'number' || isNaN(valor) || !isFinite(valor)) {
            return porDefecto;
        }
        return valor;
    }
    function textoDeCantidad(valor) {
        return String(Math.round(valor * 1000) / 1000);
    }
    //  El ticket a medio armar vuelve del navegador: una cantidad que quedó
    //  inservible de antes (un "NaN" de una versión anterior, algo editado a
    //  mano en el almacenamiento) no arrastra el error a esta sesión.
    estado.items = (estado.items || []).filter(function (item) {
        var valor = cantidadDe(item.cantidad, 0);
        if (valor <= 0) { return false; }
        item.cantidad = textoDeCantidad(valor);
        return true;
    });
    function $(id) { return document.getElementById(id); }
    function plata(v) { return pesos.format(Number(v || 0)); }
    function el(tag, clase, texto) {
        var n = document.createElement(tag);
        if (clase) { n.className = clase; }
        if (texto != null) { n.textContent = texto; }
        return n;
    }

    // ------------------------------------------------------------------
    //  Catálogo
    // ------------------------------------------------------------------
    var filtro = {q: '', sector: 'mas_vendidos', pagina: 1};
    var pedidoCatalogo = null;
    function cargarCatalogo(agregar) {
        if (pedidoCatalogo) { pedidoCatalogo.abort(); }
        pedidoCatalogo = new AbortController();
        var url = raiz.dataset.catalogo + '?' + new URLSearchParams(filtro).toString();
        fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin', signal: pedidoCatalogo.signal})
            .then(function (r) { if (r.status === 401) { location.reload(); } return r.json(); })
            .then(function (datos) {
                var grilla = $('catalogo');
                if (!agregar) { grilla.textContent = ''; }
                datos.productos.forEach(function (p) {
                    nombres[p.id] = {nombre: p.nombre, detalle: p.detalle, fraccionable: p.fraccionable, unidad: p.unidad};
                    var boton = el('button', 'producto-boton');
                    boton.type = 'button';
                    boton.dataset.producto = p.id;
                    boton.disabled = p.agotado && !p.ajustable;
                    if (p.agotado && p.ajustable) { boton.dataset.sinStock = '1'; boton.classList.add('sin-stock'); }
                    if (p.imagen) {
                        var foto = el('img', 'producto-foto');
                        foto.src = p.imagen; foto.alt = ''; foto.loading = 'lazy';
                        foto.addEventListener('error', function () { this.remove(); });
                        boton.appendChild(foto);
                    }
                    boton.appendChild(el('span', 'nombre', p.nombre));
                    if (p.detalle) { boton.appendChild(el('span', 'stock', p.detalle)); }
                    if (p.rebaja) { boton.appendChild(el('span', 'insignia insignia-aviso align-self-start', 'Oferta por vencer')); }
                    if (p.stock !== null) {
                        boton.appendChild(el('span', 'stock', p.agotado ? (p.vencido ? 'Sin stock apto' : 'Sin stock') : (p.vencido ? 'Aptas: ' : 'Stock: ') + numero.format(Number(p.stock)) + ' ' + p.unidad));
                        if (p.vencido) { boton.appendChild(el('span', 'stock text-peligro', 'Vencidas: ' + numero.format(Number(p.vencido)) + ' ' + p.unidad + ' (no se venden)')); }
                    } else if (p.sin_componentes) {
                        boton.appendChild(el('span', 'stock', 'Sin stock apto de lo que lleva'));
                    }
                    boton.appendChild(el('span', 'precio', plata(p.precio) + (p.fraccionable ? ' / ' + p.unidad : '')));
                    grilla.appendChild(boton);
                });
                if (!agregar && !datos.productos.length) {
                    grilla.appendChild(el('p', 'text-secondary', 'No hay productos que coincidan.'));
                }
                $('masProductos').classList.toggle('d-none', !datos.hay_mas);
            })
            .catch(function (e) { if (e.name !== 'AbortError') { mostrarError('No se pudo cargar el catálogo. Revisá la conexión.'); } });
    }
    var esperaBusqueda = null;
    $('buscar').addEventListener('input', function () {
        clearTimeout(esperaBusqueda);
        esperaBusqueda = setTimeout(function () { filtro.q = $('buscar').value; filtro.pagina = 1; cargarCatalogo(false); }, 250);
    });
    // Enter: el lector de códigos de barras "tipea" el código y aprieta
    // Enter. Se busca en el momento y, si el código es exacto o queda un
    // solo producto, va directo al ticket.
    $('buscar').addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') { return; }
        e.preventDefault();
        clearTimeout(esperaBusqueda);
        var q = $('buscar').value.trim();
        if (!q) { return; }
        fetch(raiz.dataset.catalogo + '?' + new URLSearchParams({q: q, sector: '', pagina: 1}).toString(),
              {headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'})
            .then(function (r) { return r.json(); })
            .then(function (datos) {
                var vendibles = datos.productos.filter(function (p) { return !p.agotado; });
                var exacto = vendibles.find(function (p) { return p.codigo && p.codigo === q; });
                var sinStock = !exacto && datos.productos.length === 1 && datos.productos[0].ajustable ? datos.productos[0] : null;
                if (sinStock) { nombres[sinStock.id] = {nombre: sinStock.nombre, detalle: sinStock.detalle, fraccionable: sinStock.fraccionable, unidad: sinStock.unidad}; $('buscar').value = ''; pedirAjuste(sinStock.id); }
                var elegido = exacto || (vendibles.length === 1 ? vendibles[0] : null);
                datos.productos.forEach(function (p) {
                    nombres[p.id] = {nombre: p.nombre, detalle: p.detalle, fraccionable: p.fraccionable, unidad: p.unidad};
                });
                if (elegido) { agregar(elegido.id); $('buscar').value = ''; }
                else if (!datos.productos.length) { mostrarError('No hay ningún producto que coincida con "' + q + '".'); }
                filtro.q = elegido ? '' : q; filtro.pagina = 1; cargarCatalogo(false);
            });
    });
    document.querySelectorAll('.js-sector').forEach(function (b) {
        b.addEventListener('click', function () {
            document.querySelectorAll('.js-sector').forEach(function (o) {
                o.classList.replace('btn-primary', 'btn-secundario'); o.setAttribute('aria-pressed', 'false');
            });
            b.classList.replace('btn-secundario', 'btn-primary'); b.setAttribute('aria-pressed', 'true');
            $('buscar').value = ''; filtro.q = '';
            filtro.sector = b.dataset.sector; filtro.pagina = 1; cargarCatalogo(false);
        });
    });
    $('masProductos').addEventListener('click', function () { filtro.pagina += 1; cargarCatalogo(true); });
    $('catalogo').addEventListener('click', function (e) {
        var b = e.target.closest('.producto-boton');
        if (b && !b.disabled) {
            if (b.dataset.sinStock) { pedirAjuste(Number(b.dataset.producto)); } else { agregar(Number(b.dataset.producto)); }
        }
    });

    // ------------------------------------------------------------------
    //  Ticket
    // ------------------------------------------------------------------
    // ------------------------------------------------------------------
    //  Figura en 0 pero está en la mano: se cuenta ahí mismo (DD-90).
    // ------------------------------------------------------------------
    var porAjustar = null;
    function pedirAjuste(id) {
        porAjustar = id;
        var info = nombres[id] || {};
        $('ajusteNombre').textContent = info.nombre || '';
        $('ajusteUnidad').textContent = info.unidad || 'u.';
        ['ajusteCantidad', 'ajusteUsuario', 'ajusteClave'].forEach(function (c) { $(c).value = ''; });
        $('ajusteError').classList.add('d-none');
        bootstrap.Modal.getOrCreateInstance($('modalAjuste')).show();
    }
    $('modalAjuste').addEventListener('shown.bs.modal', function () { $('ajusteCantidad').focus(); });
    $('modalAjuste').addEventListener('hidden.bs.modal', function () { $('buscar').focus(); });
    function confirmarAjuste() {
        var boton = $('ajusteConfirmar');
        if (boton.disabled || porAjustar === null) { return; }
        boton.disabled = true;
        enviar(raiz.dataset.ajuste, {producto: porAjustar, cantidad: $('ajusteCantidad').value,
                                     remota: ajusteRemota.id,
                                     autoriza: {usuario: $('ajusteUsuario').value, clave: $('ajusteClave').value}})
            .then(function (r) {
                if (!r.ok) { $('ajusteError').textContent = r.error; $('ajusteError').classList.remove('d-none'); return; }
                ajusteRemota.id = null;
                var id = porAjustar; porAjustar = null;
                bootstrap.Modal.getInstance($('modalAjuste')).hide();
                agregar(id); cargarCatalogo(false);
            })
            .catch(function () { $('ajusteError').textContent = 'No se pudo guardar: revisá la conexión.'; $('ajusteError').classList.remove('d-none'); })
            .then(function () { boton.disabled = false; });
    }
    // Quien aprueba ajustes no está: se le pide a distancia (DD-94). Al
    // aprobarse, el ajuste se hace solo con la cantidad que se pidió.
    var ajusteRemota = {id: null, reloj: null, cantidad: null};
    function pintarAjusteRemota(texto, tipo, conPin) {
        var caja = $('ajusteRemota');
        caja.classList.remove('d-none', 'aviso-info', 'aviso-exito', 'aviso-error', 'aviso-aviso');
        caja.classList.add('aviso-' + (tipo || 'info'));
        $('ajusteRemotaIcono').className = 'bi ' + ({exito: 'bi-check-circle-fill', error: 'bi-x-circle-fill', aviso: 'bi-exclamation-triangle-fill'}[tipo] || 'bi-hourglass-split');
        $('ajusteRemotaTexto').textContent = texto;
        $('ajusteRemotaPinGrupo').classList.toggle('d-none', !conPin);
        $('ajusteRemotaPedir').classList.toggle('d-none', !!conPin);
    }
    function pararAjusteRemota() { clearInterval(ajusteRemota.reloj); ajusteRemota.reloj = null; }
    function seguirAjusteRemota(r) {
        if (r.estado === 'PENDIENTE') {
            pintarAjusteRemota('Esperando respuesta. Vence en ' + minutos(r.restan) + ' min. Si te dictan el PIN, escribilo acá.', 'info', true);
        } else if (r.estado === 'APROBADA') {
            pararAjusteRemota();
            pintarAjusteRemota('Lo autorizó ' + r.autorizo + ' (' + (r.via || '').toLowerCase() + '). Ajustando…', 'exito', false);
            $('ajusteCantidad').value = ajusteRemota.cantidad;
            confirmarAjuste();
        } else if (r.estado === 'RECHAZADA') {
            pararAjusteRemota(); ajusteRemota.id = null;
            pintarAjusteRemota('Rechazaron el pedido: no se ajusta el stock.', 'error', false);
        } else if (r.estado === 'ANULADA') {
            pararAjusteRemota(); ajusteRemota.id = null;
            pintarAjusteRemota('El pedido venció sin respuesta. Podés pedirlo de nuevo.', 'aviso', false);
        }
    }
    function urlAjusteRemota(sufijo) { return raiz.dataset.autorizacion.replace('/0/', '/' + ajusteRemota.id + '/') + (sufijo || ''); }
    function preguntarAjusteRemota() {
        if (!ajusteRemota.id) { return; }
        fetch(urlAjusteRemota(), {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}})
            .then(function (r) { return r.json(); })
            .then(function (r) { if (r.ok && ajusteRemota.id) { seguirAjusteRemota(r); } })
            .catch(function () { /* sin red: se vuelve a preguntar en 2 segundos */ });
    }
    $('ajusteRemotaPedir').addEventListener('click', function () {
        var boton = $('ajusteRemotaPedir');
        if (porAjustar === null) { return; }
        boton.disabled = true;
        var cantidad = $('ajusteCantidad').value;
        enviar(raiz.dataset.pedirAutorizacion, {tipo: 'ajuste', producto: porAjustar, cantidad: cantidad}).then(function (r) {
            boton.disabled = false;
            if (!r.ok) { $('ajusteError').textContent = r.error; $('ajusteError').classList.remove('d-none'); return; }
            $('ajusteError').classList.add('d-none');
            ajusteRemota = {id: r.id, reloj: setInterval(preguntarAjusteRemota, 2000), cantidad: cantidad};
            seguirAjusteRemota(r);
            $('ajusteRemotaPin').value = ''; $('ajusteRemotaPin').focus();
        }).catch(function () { boton.disabled = false; $('ajusteError').textContent = 'No se pudo pedir la autorización. Revisá la conexión.'; $('ajusteError').classList.remove('d-none'); });
    });
    $('ajusteRemotaPinOk').addEventListener('click', function () {
        if (!ajusteRemota.id) { return; }
        enviar(urlAjusteRemota('pin/'), {pin: $('ajusteRemotaPin').value}).then(function (r) {
            if (!r.ok) { $('ajusteError').textContent = r.error; $('ajusteError').classList.remove('d-none'); if (r.estado && r.estado !== 'PENDIENTE') { seguirAjusteRemota(r); } return; }
            $('ajusteError').classList.add('d-none'); seguirAjusteRemota(r);
        }).catch(function () { $('ajusteError').textContent = 'No se pudo validar el PIN. Revisá la conexión.'; $('ajusteError').classList.remove('d-none'); });
    });
    $('ajusteRemotaPin').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); $('ajusteRemotaPinOk').click(); } });
    $('modalAjuste').addEventListener('hidden.bs.modal', function () {
        if (ajusteRemota.id) { enviar(urlAjusteRemota('cancelar/'), {}).catch(function () {}); }
        pararAjusteRemota(); ajusteRemota = {id: null, reloj: null, cantidad: null};
        $('ajusteRemota').classList.add('d-none'); $('ajusteRemotaPedir').classList.remove('d-none');
    });
    $('ajusteConfirmar').addEventListener('click', confirmarAjuste);
    $('modalAjuste').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); confirmarAjuste(); } });

    var ultimoAgregado = null;
    function agregar(id) {
        ultimoAgregado = id;
        var item = estado.items.find(function (i) { return i.producto === id; });
        if (item) { item.cantidad = textoDeCantidad(cantidadDe(item.cantidad, 0) + 1); }
        else { estado.items.push({producto: id, cantidad: '1', descuento: '', motivo: '', detalle: '', info: nombres[id]}); }
        cambio();
    }
    function cambio() {
        // Lo aprobado a distancia es ESTE ticket: si cambia, el pedido no sirve.
        if (remota && remota.id && !remota.cobrarAlAprobar) { olvidarRemota('El ticket cambió: se canceló el pedido de autorización a distancia.'); }
        persistir(); dibujarRenglones(); cotizarPronto();
    }

    function dibujarRenglones(cotizados) {
        var lista = $('renglones');
        lista.textContent = '';
        $('ticketVacio').classList.toggle('d-none', estado.items.length > 0);
        var porProducto = {};
        // Los regalos del club van aparte: son otro renglón aunque sean el
        // mismo producto que se está comprando.
        (cotizados || []).forEach(function (r) { if (!r.beneficio) { porProducto[r.producto] = r; } });
        var regalos = $('regalos');
        regalos.textContent = '';
        (cotizados || []).filter(function (r) { return r.beneficio; }).forEach(function (r) {
            var fila = el('div', 'renglon');
            var izq = el('div', 'min-w-0');
            izq.appendChild(el('div', 'fw-semibold text-truncate', numero.format(Number(r.cantidad)) + ' × ' + (r.nombre_corto || r.nombre)));
            izq.appendChild(el('div', 'small text-marca', 'Regalo del club'));
            fila.append(izq, el('div', 'text-end monto', plata(r.subtotal)));
            regalos.appendChild(fila);
        });
        estado.items.forEach(function (item, i) {
            var info = item.info || nombres[item.producto] || {};
            var calc = porProducto[item.producto];
            var fila = el('div', 'renglon' + (item.producto === ultimoAgregado ? ' ultimo' : ''));
            var izq = el('div', 'min-w-0');
            izq.appendChild(el('div', 'fw-semibold text-truncate', info.nombre || ('Producto ' + item.producto)));
            if (info.detalle) { izq.appendChild(el('div', 'small text-tenue text-truncate', info.detalle)); }
            if (calc && Number(calc.descuento) > 0) {
                izq.appendChild(el('div', 'small text-marca', '−' + numero.format(Number(calc.porcentaje)) + ' % · ' + calc.motivo_texto));
            }
            var der = el('div', 'text-end monto', calc ? plata(calc.subtotal) : '…');
            var controles = el('div', 'd-flex align-items-center gap-1');
            controles.style.gridColumn = '1 / -1';
            var menos = el('button', 'btn btn-sm btn-secundario btn-icono', '−'); menos.type = 'button';
            var cant = el('input', 'form-control form-control-sm text-center'); cant.style.width = '5rem';
            cant.inputMode = info.fraccionable ? 'decimal' : 'numeric';
            cant.value = item.cantidad; cant.setAttribute('aria-label', 'Cantidad');
            var mas = el('button', 'btn btn-sm btn-secundario btn-icono', '+'); mas.type = 'button';
            var unidad = el('span', 'small text-tenue', info.unidad || '');
            var desc = el('button', 'btn btn-sm btn-secundario ms-auto'); desc.type = 'button'; desc.title = 'Descuento';
            desc.innerHTML = '<i class="bi bi-percent"></i>';
            var quitar = el('button', 'btn btn-sm btn-outline-danger'); quitar.type = 'button'; quitar.title = 'Quitar';
            quitar.innerHTML = '<i class="bi bi-x-lg"></i>';
            var paso = info.fraccionable ? 0.1 : 1;
            menos.addEventListener('click', function () {
                var v = cantidadDe(item.cantidad, paso) - paso;
                if (v <= 0) { estado.items.splice(i, 1); } else { item.cantidad = textoDeCantidad(v); }
                cambio();
            });
            mas.addEventListener('click', function () {
                item.cantidad = textoDeCantidad(cantidadDe(item.cantidad, 0) + paso);
                cambio();
            });
            //  Lo que no es una cantidad no se guarda: el campo vuelve a lo
            //  que había y se marca en rojo. Antes se guardaba el texto tal
            //  cual ("1,2,3", "medio kilo") y el ticket quedaba trabado con
            //  un error del servidor que no señalaba ningún renglón.
            cant.addEventListener('change', function () {
                if (cant.value.trim() === '') { cant.value = '1'; }
                var valor = cantidadDe(cant.value, null);
                if (valor === null || valor <= 0) {
                    cant.value = item.cantidad;
                    cant.classList.add('is-invalid');
                    cant.setAttribute('aria-invalid', 'true');
                    return;
                }
                cant.classList.remove('is-invalid');
                cant.removeAttribute('aria-invalid');
                item.cantidad = textoDeCantidad(valor);
                cambio();
            });
            quitar.addEventListener('click', function () { estado.items.splice(i, 1); cambio(); });
            desc.addEventListener('click', function () { abrirDescuento(item); });
            controles.append(menos, cant, mas, unidad, desc, quitar);
            fila.append(izq, der, controles);
            lista.appendChild(fila);
        });
    }

    // ------------------------------------------------------------------
    //  Pagos
    // ------------------------------------------------------------------
    function dibujarPagos(cobros) {
        var cont = $('pagos');
        cont.textContent = '';
        var porMedio = {};
        (cobros || []).forEach(function (c) { porMedio[c.medio] = c; });
        document.querySelectorAll('.js-medio').forEach(function (b) {
            var elegido = estado.pagos.some(function (p) { return p.medio === Number(b.dataset.medio); });
            b.classList.toggle('btn-primary', elegido); b.classList.toggle('btn-secundario', !elegido);
        });
        estado.pagos.forEach(function (pago, i) {
            var boton = document.querySelector('.js-medio[data-medio="' + pago.medio + '"]');
            var efectivo = boton && boton.dataset.efectivo === '1';
            var calc = porMedio[pago.medio];
            var fila = el('div', 'd-flex flex-wrap align-items-center gap-2');
            fila.appendChild(el('span', 'fw-semibold me-auto', boton ? boton.childNodes[0].textContent.trim() : 'Medio'));
            if (estado.pagos.length > 1) {
                var monto = el('input', 'form-control form-control-sm'); monto.style.width = '7rem';
                monto.inputMode = 'decimal'; monto.placeholder = i === estado.pagos.length - 1 ? 'El resto' : 'Monto';
                monto.value = pago.monto || ''; monto.setAttribute('aria-label', 'Monto con este medio');
                monto.addEventListener('change', function () { pago.monto = monto.value.trim(); persistir(); cotizarPronto(); });
                fila.appendChild(monto);
            }
            if (calc) { fila.appendChild(el('span', 'small text-secondary', plata(calc.importe) + (Number(calc.recargo) ? ' (recargo ' + plata(calc.recargo) + ')' : ''))); }
            if (efectivo) {
                var recibido = el('input', 'form-control'); recibido.style.width = '8rem';
                recibido.inputMode = 'decimal'; recibido.placeholder = 'Te da…';
                recibido.value = pago.recibido || ''; recibido.setAttribute('aria-label', 'Efectivo recibido');
                recibido.addEventListener('change', function () { pago.recibido = recibido.value.trim(); persistir(); cotizarPronto(); });
                fila.appendChild(recibido);
            }
            var quitar = el('button', 'btn btn-sm btn-link text-peligro p-0 btn-quitar-pago'); quitar.type = 'button';
            quitar.innerHTML = '<i class="bi bi-x-circle" aria-hidden="true"></i>'; quitar.title = 'Quitar este medio';
            quitar.setAttribute('aria-label', 'Quitar ' + (boton ? boton.childNodes[0].textContent.trim() : 'este medio'));
            quitar.addEventListener('click', function () { estado.pagos.splice(i, 1); persistir(); dibujarPagos(); cotizarPronto(); });
            fila.appendChild(quitar);
            cont.appendChild(fila);
        });
        $('mixto').classList.toggle('d-none', estado.pagos.length !== 1);
        pintarFiado();
    }
    var mixtoActivo = false;
    $('mixto').addEventListener('click', function () { mixtoActivo = true; });
    document.querySelectorAll('.js-medio').forEach(function (b) {
        b.addEventListener('click', function () {
            var id = Number(b.dataset.medio);
            var ya = estado.pagos.findIndex(function (p) { return p.medio === id; });
            if (ya !== -1) { estado.pagos.splice(ya, 1); }
            else if (mixtoActivo && estado.pagos.length === 1) { estado.pagos.push({medio: id, monto: '', recibido: ''}); mixtoActivo = false; }
            else { estado.pagos = [{medio: id, monto: '', recibido: ''}]; }
            persistir(); dibujarPagos(); cotizarPronto();
        });
    });

    // ------------------------------------------------------------------
    //  Descuento
    // ------------------------------------------------------------------
    var itemDescuento = null;
    function abrirDescuento(item) {
        itemDescuento = item;
        var info = item.info || nombres[item.producto] || {};
        $('descuentoProducto').textContent = info.nombre || '';
        $('descuentoPorcentaje').value = item.descuento || '';
        $('descuentoMotivo').value = item.motivo || '';
        $('descuentoDetalle').value = item.detalle || '';
        bootstrap.Modal.getOrCreateInstance($('modalDescuento')).show();
    }
    $('aplicarDescuento').addEventListener('click', function () {
        itemDescuento.descuento = $('descuentoPorcentaje').value.trim();
        itemDescuento.motivo = $('descuentoMotivo').value;
        itemDescuento.detalle = $('descuentoDetalle').value.trim();
        bootstrap.Modal.getInstance($('modalDescuento')).hide();
        cambio();
    });
    $('quitarDescuento').addEventListener('click', function () {
        itemDescuento.descuento = ''; itemDescuento.motivo = ''; itemDescuento.detalle = '';
        bootstrap.Modal.getInstance($('modalDescuento')).hide();
        cambio();
    });

    // ------------------------------------------------------------------
    //  Club Cafeto: identificar al cliente y elegir beneficios
    // ------------------------------------------------------------------
    function pintarCliente() {
        var c = estado.cliente;
        $('clubBuscar').classList.toggle('d-none', !!c);
        $('clubCliente').classList.toggle('d-none', !c);
        if (!c) {
            $('clubBeneficios').textContent = '';
            $('clubMora').classList.add('d-none'); $('clubCuenta').classList.add('d-none');
            pintarFiado();
            return;
        }
        $('clubNombre').textContent = c.nombre;
        $('clubDatos').textContent = c.categoria + ' · ' + numero.format(c.disponibles) + ' puntos disponibles';
        var cr = c.credito || {};
        $('clubMora').classList.toggle('d-none', !cr.aviso);
        $('clubMora').querySelector('span').textContent = cr.aviso ? cr.aviso + ' Para fiarle o que use puntos, lo tiene que autorizar la dueña.' : '';
        var cuenta = '';
        if (Number(cr.debe) > 0 && !cr.aviso) { cuenta = 'Debe ' + plata(cr.debe) + ' en cuenta corriente.'; }
        if (cr.nota) { cuenta += (cuenta ? ' ' : '') + 'Nota: ' + cr.nota; }
        $('clubCuenta').textContent = cuenta;
        $('clubCuenta').classList.toggle('d-none', !cuenta);
        pintarFiado();
    }
    // ------------------------------------------------------------------
    //  Fiado (DD-91): la fecha en que va a pagar, y el radar de morosos.
    // ------------------------------------------------------------------
    function hayFiado() {
        return estado.pagos.some(function (p) {
            var b = document.querySelector('.js-medio[data-medio="' + p.medio + '"]');
            return b && b.dataset.fiado === '1';
        });
    }
    function mostrarAutorizacion(motivo) {
        $('autorizacionTitulo').textContent = /fia|cuenta corriente|puntos|beneficio/i.test(motivo || '')
            ? 'Autoriza quien maneja el fiado' : 'Autoriza el descuento';
        $('autorizacion').classList.remove('d-none');
    }
    // El plazo lo propone la ficha del cliente (lo configura la dueña); si
    // la cajera eligió otra fecha, se respeta.
    var venceAutomatico = true;
    $('venceFiado').addEventListener('change', function () { venceAutomatico = false; });
    function pintarFiado() {
        var hay = hayFiado();
        $('fiado').classList.toggle('d-none', !hay);
        var plazo = (estado.cliente && estado.cliente.credito && Number(estado.cliente.credito.plazo)) || 30;
        if (hay && (venceAutomatico || !$('venceFiado').value)) {
            var d = new Date(); d.setDate(d.getDate() + plazo);
            $('venceFiado').value = d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
            venceAutomatico = true;
        }
        $('venceAyuda').textContent = 'Se propone en ' + plazo + (plazo === 1 ? ' día' : ' días') +
            (estado.cliente && estado.cliente.credito && estado.cliente.credito.plazo ? ' (lo que tiene acordado)' : '') +
            '. Cambiala si acordaron otra fecha.';
        $('fiadoSinCliente').classList.toggle('d-none', !hay || !!estado.cliente);
    }

    // ------------------------------------------------------------------
    //  Autorización a distancia (DD-94): la caja pide, la dueña contesta
    //  del celular (enlace, bandeja o PIN) y el mismo ticket se cobra.
    // ------------------------------------------------------------------
    var remota = {id: null, estado: null, reloj: null, cobrarAlAprobar: false};
    function urlRemota(sufijo) { return raiz.dataset.autorizacion.replace('/0/', '/' + remota.id + '/') + (sufijo || ''); }
    function pintarRemota(texto, tipo, conPin) {
        var caja = $('remota');
        caja.classList.remove('d-none', 'aviso-info', 'aviso-exito', 'aviso-error', 'aviso-aviso');
        caja.classList.add('aviso-' + (tipo || 'info'));
        $('remotaIcono').className = 'bi ' + ({exito: 'bi-check-circle-fill', error: 'bi-x-circle-fill', aviso: 'bi-exclamation-triangle-fill'}[tipo] || 'bi-hourglass-split');
        $('remotaTexto').textContent = texto;
        $('remotaPinGrupo').classList.toggle('d-none', !conPin);
        $('remotaCancelar').classList.toggle('d-none', !conPin);
        $('remotaPedir').classList.toggle('d-none', !!conPin);
    }
    function minutos(seg) { var m = Math.floor(seg / 60), s = seg % 60; return m + ':' + ('0' + s).slice(-2); }
    function pararRemota() { clearInterval(remota.reloj); remota.reloj = null; }
    function olvidarRemota(avisar) {
        if (remota.id && (remota.estado === 'PENDIENTE' || remota.estado === 'APROBADA')) {
            enviar(urlRemota('cancelar/'), {}).catch(function () {});
        }
        pararRemota();
        remota = {id: null, estado: null, reloj: null, cobrarAlAprobar: false};
        if (estado.remota) { delete estado.remota; persistir(); }
        $('remota').classList.add('d-none');
        $('remotaPedir').classList.remove('d-none');
        if (avisar) { mostrarError(avisar); }
    }
    function seguirRemota(r) {
        remota.estado = r.estado;
        if (r.estado === 'PENDIENTE') {
            pintarRemota('Esperando respuesta. Le avisamos por ' + (remota.avisos || 'la bandeja del sistema') + '. Vence en ' + minutos(r.restan) + ' min. Si te dictan el PIN, escribilo acá.', 'info', true);
        } else if (r.estado === 'APROBADA') {
            pararRemota();
            pintarRemota('Lo autorizó ' + r.autorizo + ' (' + (r.via || '').toLowerCase() + '). Cobrando…', 'exito', false);
            remota.cobrarAlAprobar = true;
            cotizar();
        } else if (r.estado === 'RECHAZADA') {
            pararRemota();
            pintarRemota('Rechazaron el pedido. Cambiá el ticket o cobralo de otra forma.', 'error', false);
            remota.id = null;
        } else if (r.estado === 'ANULADA') {
            pararRemota();
            pintarRemota('El pedido venció sin respuesta. Podés pedirlo de nuevo.', 'aviso', false);
            remota.id = null;
        }
    }
    function preguntarRemota() {
        if (!remota.id) { return; }
        fetch(urlRemota(), {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}})
            .then(function (r) { return r.json(); })
            .then(function (r) { if (r.ok && remota.id) { seguirRemota(r); } })
            .catch(function () { /* sin red: se vuelve a preguntar en 2 segundos */ });
    }
    $('remotaPedir').addEventListener('click', function () {
        var boton = $('remotaPedir');
        boton.disabled = true;
        enviar(raiz.dataset.pedirAutorizacion, pedido()).then(function (r) {
            boton.disabled = false;
            if (!r.ok) { mostrarError(r.error); return; }
            mostrarError('');
            remota = {id: r.id, estado: r.estado, reloj: null, cobrarAlAprobar: false,
                      avisos: (r.avisos || []).join(' y ') || 'la bandeja del sistema'};
            estado.remota = {id: r.id, avisos: remota.avisos}; persistir();
            seguirRemota(r);
            remota.reloj = setInterval(preguntarRemota, 2000);
            $('remotaPin').value = ''; $('remotaPin').focus();
        }).catch(function () { boton.disabled = false; mostrarError('No se pudo pedir la autorización. Revisá la conexión.'); });
    });
    $('remotaPinOk').addEventListener('click', function () {
        if (!remota.id) { return; }
        enviar(urlRemota('pin/'), {pin: $('remotaPin').value}).then(function (r) {
            if (!r.ok) { mostrarError(r.error); if (r.estado && r.estado !== 'PENDIENTE') { seguirRemota(r); } return; }
            mostrarError(''); seguirRemota(r);
        }).catch(function () { mostrarError('No se pudo validar el PIN. Revisá la conexión.'); });
    });
    $('remotaPin').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); $('remotaPinOk').click(); } });
    $('remotaCancelar').addEventListener('click', function () { olvidarRemota(''); });
    // Se recargó la página (o se cambió de usuario y se volvió) con un
    // pedido en curso: se lo sigue esperando con el mismo ticket.
    function retomarRemota() {
        if (!estado.remota || !estado.remota.id) { return; }
        remota = {id: estado.remota.id, estado: 'PENDIENTE', reloj: null, cobrarAlAprobar: false,
                  avisos: estado.remota.avisos};
        $('autorizacion').classList.remove('d-none');
        pintarRemota('Retomando el pedido de autorización…', 'info', true);
        preguntarRemota();
        remota.reloj = setInterval(preguntarRemota, 2000);
    }
    if ($('radar')) {
        $('radar').addEventListener('click', function () {
            var cuerpo = $('radarLista');
            cuerpo.textContent = 'Buscando…';
            bootstrap.Modal.getOrCreateInstance($('modalRadar')).show();
            fetch(raiz.dataset.morosos, {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}})
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    cuerpo.textContent = '';
                    if (!d.morosos.length) { cuerpo.textContent = 'Nadie está en mora.'; return; }
                    d.morosos.forEach(function (m) {
                        var fila = el('div', 'radar-fila');
                        var izq = el('div', 'min-w-0');
                        izq.appendChild(el('div', 'fw-semibold', m.nombre + (m.suspendido ? ' · suspendido' : '')));
                        if (m.nota) { izq.appendChild(el('div', 'small', m.nota)); }
                        izq.appendChild(el('div', 'small text-secondary', Number(m.vencido) > 0
                            ? 'Debe ' + plata(m.debe) + ' · ' + m.dias + (m.dias === 1 ? ' día' : ' días') + ' de atraso' : 'Debe ' + plata(m.debe)));
                        var ir = el('a', 'btn btn-sm btn-primary', 'Cobrarle');
                        ir.href = raiz.dataset.cuentas + m.id + '/';
                        fila.appendChild(izq); fila.appendChild(ir); cuerpo.appendChild(fila);
                    });
                })
                .catch(function () { cuerpo.textContent = 'No se pudo cargar la lista. Probá de nuevo.'; });
        });
    }
    var esperaParecidos = null;
    function buscarParecidos() {
        clearTimeout(esperaParecidos);
        esperaParecidos = setTimeout(function () {
            var q = 'dni=' + encodeURIComponent($('ncDni').value) + '&telefono=' + encodeURIComponent($('ncTelefono').value)
                  + '&apellido=' + encodeURIComponent($('ncApellido').value) + '&nombre=' + encodeURIComponent($('ncNombre').value);
            fetch(raiz.dataset.parecidos + '?' + q, {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}})
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    var caja = $('clienteParecidos');
                    caja.classList.toggle('d-none', !d.parecidos.length);
                    caja.querySelector('span').textContent = d.parecidos.map(function (p) {
                        return 'Ojo: ya hay un cliente con ' + p.por + ': ' + p.nombre + (p.de_baja ? ' (dado de baja)' : '')
                            + (p.aviso ? '. ' + p.aviso : (Number(p.debe) > 0 ? '. Debe ' + plata(p.debe) + '.' : '.'))
                            + (p.nota ? ' Nota: ' + p.nota + '.' : '');
                    }).join(' ') + (d.parecidos.length ? ' Si es la misma persona, cerrá esto e identificalo con su DNI o su teléfono.' : '');
                })
                .catch(function () { /* sin red: el alta valida igual */ });
        }, 350);
    }
    ['ncDni', 'ncTelefono', 'ncApellido', 'ncNombre'].forEach(function (id) { $(id).addEventListener('input', buscarParecidos); });
    function pintarBeneficios(lista) {
        var cont = $('clubBeneficios');
        cont.textContent = '';
        (lista || []).forEach(function (b) {
            var fila = el('div', 'd-flex align-items-center gap-2 small');
            var texto = b.descripcion + (b.porcentaje ? ' (' + numero.format(Number(b.porcentaje)) + ' %)' : '');
            if (b.automatico_descuento) {
                fila.appendChild(el('span', 'insignia ' + (b.aplicado ? 'insignia-exito' : 'insignia-neutra'), b.aplicado ? 'Se aplica' : 'Sin efecto'));
                fila.appendChild(el('span', 'me-auto', texto));
            } else {
                var elegido = estado.beneficios.indexOf(b.id) !== -1;
                fila.appendChild(el('span', 'me-auto', texto + (b.puntos ? ' · ' + numero.format(b.puntos) + ' pts' : ' · regalo de la categoría')));
                var tiene = estado.cliente ? Number(estado.cliente.disponibles || 0) : 0;
                var falta = !elegido && b.puntos ? b.puntos - tiene : 0;
                var boton = el('button', 'btn btn-sm ' + (elegido ? 'btn-primary' : 'btn-secundario'),
                               elegido ? 'Quitar' : (falta > 0 ? 'Faltan ' + numero.format(falta) : (b.puntos ? 'Canjear' : 'Aplicar')));
                boton.type = 'button';
                if (falta > 0) { boton.disabled = true; boton.title = 'Tiene ' + numero.format(tiene) + ' puntos'; }
                boton.addEventListener('click', function () {
                    var i = estado.beneficios.indexOf(b.id);
                    if (i === -1) { estado.beneficios.push(b.id); } else { estado.beneficios.splice(i, 1); }
                    cambio();
                });
                fila.appendChild(boton);
            }
            cont.appendChild(fila);
        });
    }
    function identificar() {
        var q = $('clubTexto').value.trim();
        if (!q) { return; }
        fetch(raiz.dataset.cliente + '?' + new URLSearchParams({q: q}).toString(),
              {headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'})
            .then(function (r) { if (r.status === 401) { location.reload(); } return r.json(); })
            .then(function (r) {
                if (!r.ok) { mostrarError(r.error); return; }
                mostrarError('');
                estado.cliente = r.cliente; estado.beneficios = [];
                $('clubTexto').value = '';
                pintarCliente(); cambio();
            })
            .catch(function () { mostrarError('No se pudo buscar al cliente. Revisá la conexión.'); });
    }
    $('clubIdentificar').addEventListener('click', identificar);
    $('clubTexto').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); identificar(); } });
    $('clubQuitar').addEventListener('click', function () { estado.cliente = null; estado.beneficios = []; pintarCliente(); cambio(); });
    if ($('clubNuevo')) {
        $('clubNuevo').addEventListener('click', function () {
            ['ncApellido', 'ncNombre', 'ncTelefono', 'ncDni', 'ncEmail', 'ncNacimiento'].forEach(function (i) { $(i).value = ''; });
            $('ncTelefono').value = /^[\d\s()+-]{7,}$/.test($('clubTexto').value.trim()) ? $('clubTexto').value.trim() : '';
            $('clienteError').classList.add('d-none'); $('clienteTarjeta').classList.add('d-none');
            $('clienteGuardar').disabled = false;
            bootstrap.Modal.getOrCreateInstance($('modalCliente')).show();
        });
    }
    $('clienteGuardar').addEventListener('click', function () {
        var nacimiento = $('ncNacimiento').value.trim();
        var m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(nacimiento);
        var cuerpo = {
            apellido_cliente: $('ncApellido').value, nombre_cliente: $('ncNombre').value,
            telefono_cliente: $('ncTelefono').value, dni_cliente: $('ncDni').value,
            email_cliente: $('ncEmail').value,
            fecha_nacimiento: m ? m[3] + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[1]).slice(-2) : nacimiento
        };
        $('clienteGuardar').disabled = true;
        enviar(raiz.dataset.altaCliente, cuerpo).then(function (r) {
            if (!r.ok) {
                $('clienteError').querySelector('span').textContent = r.error;
                $('clienteError').classList.remove('d-none');
                $('clienteGuardar').disabled = false;
                return;
            }
            estado.cliente = r.cliente; estado.beneficios = [];
            $('clienteError').classList.add('d-none');
            $('clienteTarjeta').href = r.tarjeta; $('clienteTarjeta').classList.remove('d-none');
            pintarCliente(); cambio();
        }).catch(function () { $('clienteGuardar').disabled = false; });
    });

    // ------------------------------------------------------------------
    //  Servidor
    // ------------------------------------------------------------------
    function pedido() {
        var cuerpo = {
            token: estado.token,
            cliente: estado.cliente ? estado.cliente.id : null,
            beneficios: estado.beneficios || [],
            items: estado.items.map(function (i) { return {producto: i.producto, cantidad: i.cantidad, descuento: i.descuento, motivo: i.motivo, detalle: i.detalle}; }),
            pagos: estado.pagos
        };
        if (hayFiado() && $('venceFiado').value) { cuerpo.vence_fiado = $('venceFiado').value; }
        if (remota && remota.id && remota.estado === 'APROBADA') {
            cuerpo.remota = remota.id;
        } else if (!$('autorizacion').classList.contains('d-none') && $('autorizaUsuario').value) {
            cuerpo.autoriza = {usuario: $('autorizaUsuario').value, clave: $('autorizaClave').value};
        }
        return cuerpo;
    }
    function enviar(url, cuerpo, senal) {
        return fetch(url, {
            method: 'POST', credentials: 'same-origin', signal: senal,
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify(cuerpo)
        }).then(function (r) {
            if (r.status === 401) { location.reload(); throw new Error('sesión'); }
            //  Un 400 (pedido mal armado) y un 403 (sin permiso, token
            //  vencido) traen el MOTIVO en el cuerpo: "ese producto está
            //  dado de baja", "el turno ya se cerró", "no tenés permiso
            //  para descontar". Tirándolos todos como error de red, la
            //  pantalla decía «Revisá la conexión» con la conexión
            //  perfecta, y la cajera no tenía nada que arreglar.
            if (!r.ok && (r.status === 400 || r.status === 403)) {
                return r.json().then(function (datos) {
                    var texto = datos && (datos.error || datos.detail);
                    if (!texto) { throw new Error('servidor'); }
                    datos.ok = false;
                    datos.error = texto;
                    return datos;
                }, function () { throw new Error('servidor'); });
            }
            if (!r.ok && r.status !== 200) { throw new Error('servidor'); }
            return r.json();
        });
    }
    function mostrarError(texto) {
        var caja = $('errorTicket');
        caja.querySelector('span').textContent = texto || '';
        caja.classList.toggle('d-none', !texto);
    }

    var ultima = null, enCurso = null, esperaCotizar = null;
    function cotizarPronto() { clearTimeout(esperaCotizar); esperaCotizar = setTimeout(cotizar, 200); }
    function cotizar() {
        if (enCurso) { enCurso.abort(); }
        if (!estado.items.length) {
            ultima = null; mostrarError(''); pintarTotales(null); dibujarRenglones(); dibujarPagos(); return;
        }
        enCurso = new AbortController();
        enviar(raiz.dataset.cotizar, pedido(), enCurso.signal).then(function (r) {
            if (!r.ok) {
                ultima = null;
                if (remota) { remota.cobrarAlAprobar = false; }
                mostrarError(r.error);
                if (/autoriz|tope/i.test(r.error)) { mostrarAutorizacion(r.error); }
                $('cobrar').disabled = true;
                return;
            }
            mostrarError('');
            ultima = r;
            if (r.cliente) { estado.cliente = r.cliente; pintarCliente(); }
            pintarBeneficios(r.beneficios);
            if (r.pide_autorizacion) { mostrarAutorizacion(hayFiado() ? 'fiado' : ''); }
            dibujarRenglones(r.renglones);
            dibujarPagos(r.cobros);
            pintarTotales(r);
            if (remota && remota.cobrarAlAprobar && !$('cobrar').disabled) { remota.cobrarAlAprobar = false; $('cobrar').click(); }
        }).catch(function (e) {
            if (e.name !== 'AbortError') { mostrarError('No se pudo calcular el ticket. Revisá la conexión.'); $('cobrar').disabled = true; }
        });
    }
    function pintarTotales(r) {
        $('tSubtotal').textContent = plata(r ? r.subtotal : 0);
        var beneficio = r && Number(r.descuento) > 0;
        document.querySelectorAll('.js-beneficio').forEach(function (n) { n.classList.toggle('d-none', !beneficio); });
        if (beneficio) { $('tBeneficio').textContent = '−' + plata(r.descuento); $('tBeneficioTexto').textContent = r.descuento_texto; }
        var redondeo = r && Number(r.redondeo) !== 0, recargo = r && Number(r.recargo) !== 0;
        document.querySelectorAll('.js-redondeo').forEach(function (n) { n.classList.toggle('d-none', !redondeo); });
        document.querySelectorAll('.js-recargo').forEach(function (n) { n.classList.toggle('d-none', !recargo); });
        if (redondeo) { $('tRedondeo').textContent = plata(r.redondeo); }
        if (recargo) { $('tRecargo').textContent = plata(r.recargo); }
        $('tTotal').textContent = plata(r ? r.total : 0);
        $('barraTotal').textContent = plata(r ? r.total : 0);
        $('barraCantidad').textContent = estado.items.length + (estado.items.length === 1 ? ' producto' : ' productos');
        var vuelto = r && Number(r.vuelto) > 0;
        $('filaVuelto').classList.toggle('d-none', !vuelto);
        if (vuelto) { $('tVuelto').textContent = plata(r.vuelto); }
        $('cobrar').disabled = !(r && r.cobros && r.cobros.length) || !navigator.onLine;
    }

    // Antes de cobrar, el resumen de lo que se va a cobrar (DD-101): un toque
    // de más o un medio mal elegido se ve acá y no en el arqueo.
    function resumenDelCobro() {
        var r = ultima || {};
        var filas = [['Total a cobrar', plata(r.total)]];
        (r.cobros || []).forEach(function (c) {
            var boton = document.querySelector('.js-medio[data-medio="' + c.medio + '"]');
            var nombre = boton ? boton.childNodes[0].textContent.trim() : 'Medio';
            filas.push([nombre, plata(c.importe) + (Number(c.recargo) ? ' (con ' + plata(c.recargo) + ' de recargo)' : '')]);
        });
        var efectivo = estado.pagos.filter(function (p) {
            var b = document.querySelector('.js-medio[data-medio="' + p.medio + '"]');
            return b && b.dataset.efectivo === '1' && p.recibido;
        })[0];
        if (efectivo) { filas.push(['Efectivo recibido', plata(String(efectivo.recibido).replace(',', '.'))]); }
        if (Number(r.vuelto) > 0) { filas.push(['Vuelto', plata(r.vuelto)]); }
        if (estado.cliente) { filas.push(['Cliente', estado.cliente.nombre]); }
        if (hayFiado()) { filas.push(['Cuenta corriente', 'Se anota en la cuenta' + (estado.cliente ? ' de ' + estado.cliente.nombre : '')]); }
        return '<dl class="tarjeta-datos fs-6 mb-0">' + filas.map(function (f) {
            return '<dt>' + Cafeto.escapar(f[0]) + '</dt><dd class="monto">' + Cafeto.escapar(f[1]) + '</dd>';
        }).join('') + '</dl>';
    }
    var confirmando = false;
    $('cobrar').addEventListener('click', function () {
        if ($('cobrar').disabled || confirmando) { return; }
        confirmando = true;
        var resumen;
        try { resumen = resumenDelCobro(); } catch (e) { resumen = ''; }
        Cafeto.confirmar({titulo: '¿Confirmamos el cobro?', html: resumen, aceptar: 'Sí, cobrar'})
            .then(function (ok) { confirmando = false; if (ok) { cobrarYa(); } else { $('buscar').focus(); } });
    });
    function cobrarYa() {
        var boton = $('cobrar');
        boton.disabled = true;
        boton.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Cobrando…';
        enviar(raiz.dataset.cobrar, pedido()).then(function (r) {
            boton.innerHTML = '<i class="bi bi-check2-circle me-1"></i>Cobrar';
            if (!r.ok) { mostrarError(r.error); boton.disabled = false; return; }
            pararRemota(); remota = {id: null, estado: null, reloj: null, cobrarAlAprobar: false};
            delete estado.remota;
            $('remota').classList.add('d-none'); $('remotaPedir').classList.remove('d-none');
            $('cobradoNumero').textContent = 'Venta N° ' + r.venta + (r.repetida ? ' (ya estaba registrada)' : '');
            $('cobradoTotal').textContent = plata(r.total);
            $('cobradoVuelto').textContent = plata(r.vuelto);
            $('cobradoBloqueVuelto').classList.toggle('d-none', Number(r.vuelto) <= 0);
            $('cobradoTicket').href = r.ticket;
            var f = r.fiado;
            $('cobradoFiado').classList.toggle('d-none', !f);
            if (f) {
                var vence = f.vence.split('-');
                $('cobradoFiado').textContent = 'Quedó anotado en la cuenta de ' + f.cliente + ': ' + plata(f.monto) +
                    ', paga hasta el ' + vence[2] + '/' + vence[1] + '/' + vence[0] + '.';
            }
            var p = r.puntos;
            $('cobradoPuntos').classList.toggle('d-none', !p);
            if (p) {
                $('cobradoPuntos').textContent = p.diferidos
                    ? p.cliente + ': los puntos de esta compra se suman cuando la pague.'
                    : p.cliente + ': +' + numero.format(p.ganados) + ' puntos' +
                      (p.canjeados ? ' (canjeó ' + numero.format(p.canjeados) + ')' : '') +
                      '. Le quedan ' + numero.format(p.disponibles) + '.' +
                      (p.ascendio ? ' ¡Subió a ' + p.categoria + '!' : '');
            }
            bootstrap.Modal.getOrCreateInstance($('modalCobrado')).show();
            // Lo vendido ya bajó el stock: la grilla tiene que mostrarlo.
            cargarCatalogo(false);
        }).catch(function () {
            // Puede haberse cobrado y perdido la respuesta: el MISMO token
            // hace que reintentar devuelva la misma venta, no una nueva.
            boton.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>Reintentar cobro';
            boton.disabled = false;
            mostrarError('No llegó la respuesta del servidor. Tocá el botón Reintentar cobro: si la venta ya se había cobrado, no se cobra dos veces.');
        });
    }
    function nuevaVenta() {
        estado = {items: [], pagos: [], cliente: null, beneficios: [], token: (window.crypto && crypto.randomUUID) ? crypto.randomUUID().replace(/-/g, '') : ''};
        if (!estado.token) { location.reload(); return; }
        $('autorizaUsuario').value = ''; $('autorizaClave').value = '';
        olvidarRemota('');
        $('autorizacion').classList.add('d-none');
        $('venceFiado').value = ''; venceAutomatico = true;
        persistir(); ultima = null; mostrarError('');
        dibujarRenglones(); dibujarPagos(); pintarTotales(null); pintarCliente();
        $('buscar').value = ''; $('buscar').focus();
        filtro.q = ''; filtro.pagina = 1; cargarCatalogo(false);
    }
    $('nuevaVenta').addEventListener('click', function () { bootstrap.Modal.getInstance($('modalCobrado')).hide(); nuevaVenta(); });

    // ------------------------------------------------------------------
    //  Teclado de caja: F2 buscar, F4 efectivo, F9 cobrar. Con la venta
    //  cobrada a la vista: Enter o espacio = nueva venta, P = ticket.
    // ------------------------------------------------------------------
    $('modalCobrado').addEventListener('shown.bs.modal', function () { $('nuevaVenta').focus(); });
    $('modalCobrado').addEventListener('hidden.bs.modal', function () { $('buscar').focus(); });
    document.addEventListener('keydown', function (e) {
        if (e.ctrlKey || e.altKey || e.metaKey) { return; }
        var cobrado = $('modalCobrado').classList.contains('show');
        if (cobrado) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('nuevaVenta').click(); }
            else if (e.key === 'p' || e.key === 'P') { e.preventDefault(); $('cobradoTicket').click(); }
            return;
        }
        if (document.querySelector('.modal.show')) { return; }
        if (e.key === 'F2') { e.preventDefault(); $('buscar').focus(); $('buscar').select(); }
        else if (e.key === 'F4') {
            e.preventDefault();
            var efectivo = document.querySelector('.js-medio[data-efectivo="1"]');
            if (!efectivo) { return; }
            var id = Number(efectivo.dataset.medio);
            if (!(estado.pagos.length === 1 && estado.pagos[0].medio === id)) {
                estado.pagos = [{medio: id, monto: '', recibido: ''}];
                persistir(); dibujarPagos(); cotizarPronto();
            }
            var teDa = document.querySelector('#pagos input[aria-label="Efectivo recibido"]');
            if (teDa) { teDa.focus(); teDa.select(); }
        }
        else if (e.key === 'F9') { e.preventDefault(); if (!$('cobrar').disabled) { $('cobrar').click(); } }
    });
    $('vaciar').addEventListener('click', function () {
        if (!estado.items.length) { return; }
        Cafeto.confirmar({titulo: '¿Vaciar el ticket?', texto: 'Se quitan todos los productos cargados.', aceptar: 'Sí, vaciar', peligro: true})
            .then(function (ok) { if (ok) { estado.items = []; estado.pagos = []; estado.cliente = null; estado.beneficios = []; pintarCliente(); cambio(); } });
    });
    $('autorizaClave').addEventListener('change', cotizarPronto);

    cargarCatalogo(false);
    // Lo que vende otra caja u otro dispositivo también mueve el stock: la
    // grilla se refresca sola cada 45 s y al volver a la pestaña. Sólo con
    // la primera página a la vista, para no desarmar lo que se está
    // recorriendo con "Ver más".
    function refrescarCatalogo() {
        if (document.visibilityState === 'visible' && filtro.pagina === 1) { cargarCatalogo(false); }
    }
    setInterval(refrescarCatalogo, 45000);
    document.addEventListener('visibilitychange', refrescarCatalogo);
    dibujarRenglones(); dibujarPagos(); pintarCliente();
    if (estado.items.length) { cotizar(); }
    retomarRemota();
})();
