/* ==========================================================================
   Cafeto · tablero interactivo de Reportes y del Resumen del mes (DD-100)

   Dibuja en SVG, sin librerías, los datos que la página trae en
   <script type="application/json" id="datosTablero"> (no se ejecuta: no
   choca con la CSP). Cada barra se puede recorrer con el teclado (Tab y
   flechas) y muestra su detalle al pasar el mouse, tocarla o enfocarla.
   Las mismas cifras están en las tablas «Ver los datos», que se leen sin
   JavaScript.
   ========================================================================== */
(function () {
    'use strict';
    var fuente = document.getElementById('datosTablero');
    if (!fuente) { return; }
    var datos;
    try { datos = JSON.parse(fuente.textContent); } catch (e) { return; }

    var NS = 'http://www.w3.org/2000/svg';
    var moneda = new Intl.NumberFormat('es-AR', {style: 'currency', currency: 'ARS', maximumFractionDigits: 2});
    var entero = new Intl.NumberFormat('es-AR', {maximumFractionDigits: 3});
    var NOMBRES = {vendido: 'Vendido', interes: 'Recargos por mora', costo: 'Costo de lo vendido',
                   gastos: 'Gastos pagados', mermas: 'Mermas', incobrable: 'Incobrable'};

    function num(texto) { return Number(texto) || 0; }
    function pesos(valor) { return moneda.format(num(valor)); }
    function corto(valor) {
        var v = Math.abs(valor), signo = valor < 0 ? '−' : '';
        if (v >= 1e6) { return signo + '$ ' + entero.format(Math.round(v / 1e5) / 10) + ' M'; }
        if (v >= 1e3) { return signo + '$ ' + entero.format(Math.round(v / 100) / 10) + ' mil'; }
        return signo + '$ ' + entero.format(Math.round(v));
    }
    function nodo(tag, atributos, padre) {
        var n = document.createElementNS(NS, tag);
        Object.keys(atributos || {}).forEach(function (k) { n.setAttribute(k, atributos[k]); });
        if (padre) { padre.appendChild(n); }
        return n;
    }
    function texto(padre, x, y, contenido, clase, ancla) {
        var t = nodo('text', {x: x, y: y, 'class': clase || '', 'text-anchor': ancla || 'start'}, padre);
        t.textContent = contenido;
        return t;
    }
    function escalones(maximo) {
        if (maximo <= 0) { return 1; }
        var bruto = maximo / 4, potencia = Math.pow(10, Math.floor(Math.log10(bruto)));
        var paso = [1, 2, 2.5, 5, 10].map(function (m) { return m * potencia; })
            .find(function (p) { return p >= bruto; });
        return paso;
    }

    /* ---------------------------------------------------------- detalle -- */
    function Detalle(figura) {
        var caja = document.createElement('div');
        caja.className = 'grafico-tip';
        caja.setAttribute('aria-hidden', 'true');
        caja.hidden = true;
        figura.querySelector('.grafico-lienzo').appendChild(caja);
        this.mostrar = function (elemento, lineas) {
            caja.textContent = '';
            lineas.forEach(function (linea, i) {
                var p = document.createElement(i ? 'div' : 'strong');
                if (Array.isArray(linea)) {
                    var a = document.createElement('span'); a.textContent = linea[0];
                    var b = document.createElement('span'); b.textContent = linea[1];
                    if (linea[2]) { b.className = linea[2]; }
                    p.className = 'grafico-tip-fila'; p.append(a, b);
                } else { p.textContent = linea; }
                caja.appendChild(p);
            });
            caja.hidden = false;
            var lienzo = caja.parentNode.getBoundingClientRect(), r = elemento.getBoundingClientRect();
            var x = r.left - lienzo.left + r.width / 2 - caja.offsetWidth / 2;
            x = Math.max(4, Math.min(x, lienzo.width - caja.offsetWidth - 4));
            var y = r.top - lienzo.top - caja.offsetHeight - 8;
            if (y < 0) { y = r.bottom - lienzo.top + 8; }
            caja.style.left = x + 'px';
            caja.style.top = y + 'px';
        };
        this.ocultar = function () { caja.hidden = true; };
    }

    function interactivo(elemento, detalle, etiqueta, lineas) {
        elemento.setAttribute('tabindex', '0');
        elemento.setAttribute('role', 'listitem');
        elemento.setAttribute('aria-label', etiqueta);
        elemento.classList.add('grafico-punto');
        var ver = function () { detalle.mostrar(elemento, lineas); elemento.classList.add('activo'); };
        var salir = function () { detalle.ocultar(); elemento.classList.remove('activo'); };
        elemento.addEventListener('pointerenter', ver);
        elemento.addEventListener('pointerleave', salir);
        elemento.addEventListener('focus', ver);
        elemento.addEventListener('blur', salir);
        elemento.addEventListener('keydown', function (evento) {
            if (evento.key === 'Escape') { salir(); return; }
            var hermanos = Array.prototype.slice.call(elemento.parentNode.querySelectorAll('.grafico-punto'));
            var i = hermanos.indexOf(elemento);
            var destino = {ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1,
                           Home: 0, End: hermanos.length - 1}[evento.key];
            if (destino != null && hermanos[destino]) { evento.preventDefault(); hermanos[destino].focus(); }
        });
    }

    function lienzoDe(figura, alto, resumen) {
        var lienzo = figura.querySelector('.grafico-lienzo');
        Array.prototype.slice.call(lienzo.querySelectorAll('svg')).forEach(function (s) { s.remove(); });
        var ancho = Math.max(280, Math.round(lienzo.clientWidth || 600));
        var svg = nodo('svg', {viewBox: '0 0 ' + ancho + ' ' + alto, width: ancho, height: alto,
                               role: 'group', 'aria-label': resumen, 'class': 'grafico-svg'});
        lienzo.insertBefore(svg, lienzo.firstChild);
        return {svg: svg, ancho: ancho, alto: alto};
    }

    function vacio(figura, mensaje) {
        var lienzo = figura.querySelector('.grafico-lienzo');
        lienzo.textContent = '';
        var p = document.createElement('p');
        p.className = 'grafico-sin-datos';
        p.textContent = mensaje;
        lienzo.appendChild(p);
    }

    function ejeY(svg, m, ancho, alto, minimo, maximo, formato) {
        var paso = escalones(Math.max(maximo, -minimo, 1));
        var desde = Math.floor(minimo / paso) * paso, hasta = Math.ceil(maximo / paso) * paso || paso;
        var y = function (v) { return m.arriba + (hasta - v) / (hasta - desde) * (alto - m.arriba - m.abajo); };
        for (var v = desde; v <= hasta + 1e-9; v += paso) {
            nodo('line', {x1: m.izq, x2: ancho - m.der, y1: y(v), y2: y(v), 'class': v === 0 ? 'eje-cero' : 'grilla'}, svg);
            texto(svg, m.izq - 6, y(v) + 4, formato(v), 'rotulo', 'end');
        }
        return y;
    }

    /* ------------------------------------- 1. ingresos, egresos y neta -- */
    function evolucion(figura, detalle) {
        var e = datos.evolucion, puntos = e.puntos;
        var activas = {};
        figura.querySelectorAll('[data-serie]').forEach(function (b) { activas[b.dataset.serie] = b.getAttribute('aria-pressed') === 'true'; });
        var hay = puntos.some(function (p) { return num(p.ingresos) || num(p.egresos); });
        if (!hay) { vacio(figura, 'No hubo ventas ni gastos en este período.'); return; }
        var t = e.total;
        var l = lienzoDe(figura, 280, 'Ingresos ' + pesos(t.ingresos) + ', egresos ' + pesos(t.egresos) +
                         ', ganancia neta ' + pesos(t.neta) + ', por ' + e.unidad + '.');
        var m = {izq: 64, der: 10, arriba: 14, abajo: 30};
        var valores = [0];
        puntos.forEach(function (p) {
            if (activas.ingresos) { valores.push(num(p.ingresos)); }
            if (activas.egresos) { valores.push(num(p.egresos)); }
            if (activas.neta) { valores.push(num(p.neta)); }
        });
        var y = ejeY(l.svg, m, l.ancho, l.alto, Math.min.apply(null, valores), Math.max.apply(null, valores), corto);
        var banda = (l.ancho - m.izq - m.der) / puntos.length;
        var barra = Math.max(1, Math.min(22, banda * 0.36));
        var cada = Math.ceil(puntos.length / Math.max(1, Math.floor((l.ancho - m.izq) / 46)));
        var lista = nodo('g', {role: 'list'}, l.svg);
        var linea = [];
        puntos.forEach(function (p, i) {
            var x = m.izq + banda * i + banda / 2;
            var g = nodo('g', {}, lista);
            nodo('rect', {x: x - banda / 2, y: m.arriba, width: banda, height: l.alto - m.arriba - m.abajo, 'class': 'zona'}, g);
            if (activas.ingresos) {
                nodo('rect', {x: x - barra - 1, y: y(num(p.ingresos)), width: barra, height: Math.max(0, y(0) - y(num(p.ingresos))), 'class': 'serie-ingresos', rx: 2}, g);
            }
            if (activas.egresos) {
                nodo('rect', {x: x + 1, y: y(num(p.egresos)), width: barra, height: Math.max(0, y(0) - y(num(p.egresos))), 'class': 'serie-egresos', rx: 2}, g);
            }
            linea.push([x, y(num(p.neta))]);
            if (i % cada === 0) { texto(l.svg, x, l.alto - 10, p.etiqueta, 'rotulo', 'middle'); }
            var periodo = p.desde === p.hasta ? p.etiqueta : p.etiqueta + ' al ' + p.hasta.split('-').reverse().slice(0, 2).join('/');
            var d = p.desglose;
            interactivo(g, detalle,
                        periodo + ': ingresos ' + pesos(p.ingresos) + ', egresos ' + pesos(p.egresos) + ', ganancia neta ' + pesos(p.neta),
                        [periodo,
                         [NOMBRES.vendido, pesos(d.vendido)], [NOMBRES.interes, pesos(d.interes)],
                         ['Ingresos', pesos(p.ingresos), 'fw-semibold'],
                         [NOMBRES.costo, '− ' + pesos(d.costo)], [NOMBRES.gastos, '− ' + pesos(d.gastos)],
                         [NOMBRES.mermas, '− ' + pesos(d.mermas)], [NOMBRES.incobrable, '− ' + pesos(d.incobrable)],
                         ['Ganancia neta', pesos(p.neta), num(p.neta) < 0 ? 'text-peligro fw-semibold' : 'text-exito fw-semibold']]);
        });
        if (activas.neta) {
            nodo('polyline', {points: linea.map(function (q) { return q.join(','); }).join(' '), 'class': 'serie-neta'}, l.svg);
            linea.forEach(function (q, i) {
                nodo('circle', {cx: q[0], cy: q[1], r: puntos.length > 40 ? 2 : 3.5,
                                'class': num(puntos[i].neta) < 0 ? 'punto-negativo' : 'punto-neta'}, l.svg);
            });
        }
    }

    /* ------------------------------------------- barras horizontales -- */
    function horizontales(figura, detalle, filas, resumen, vacioTexto) {
        if (!filas.length) { vacio(figura, vacioTexto); return; }
        var alto = filas.length * 44 + 8;
        var l = lienzoDe(figura, alto, resumen);
        var maximo = Math.max.apply(null, filas.map(function (f) { return Math.abs(f.valor); })) || 1;
        var lista = nodo('g', {role: 'list'}, l.svg);
        filas.forEach(function (f, i) {
            var y = 6 + i * 44;
            var g = nodo('g', {}, lista);
            nodo('rect', {x: 0, y: y - 4, width: l.ancho, height: 42, 'class': 'zona'}, g);
            texto(g, 0, y + 12, f.nombre, 'rotulo-fuerte');
            texto(g, l.ancho, y + 12, f.rotulo, 'rotulo', 'end');
            nodo('rect', {x: 0, y: y + 20, width: l.ancho, height: 12, rx: 6, 'class': 'pista'}, g);
            nodo('rect', {x: 0, y: y + 20, width: Math.max(3, Math.abs(f.valor) / maximo * l.ancho), height: 12, rx: 6,
                          'class': f.clase || (i === 0 ? 'serie-ingresos' : 'serie-egresos')}, g);
            interactivo(g, detalle, f.etiqueta, f.lineas);
        });
    }

    /* ------------------------------------------------ 2. medios de pago -- */
    function medios(figura, detalle) {
        var partes = datos.medios.partes;
        horizontales(figura, detalle, partes.map(function (p, i) {
            var porcentaje = entero.format(num(p.porcentaje)) + ' %';
            return {nombre: p.nombre, valor: num(p.monto), rotulo: porcentaje + ' · ' + pesos(p.monto),
                    clase: i === 0 ? 'serie-ingresos' : 'serie-egresos',
                    etiqueta: p.nombre + ': ' + porcentaje + ' de lo cobrado, ' + pesos(p.monto),
                    lineas: [p.nombre, ['Cobrado', pesos(p.monto)], ['Del total', porcentaje]]};
        }), 'Cobrado por medio de pago, total ' + pesos(datos.medios.total) + '.',
        'No hubo cobros en este período.');
    }

    /* ---------------------------------------- 3. productos y rubros -- */
    function top(figura, detalle) {
        var de = figura.querySelector('[data-top-de][aria-pressed="true"]').dataset.topDe;
        var por = figura.querySelector('[data-top-por][aria-pressed="true"]').dataset.topPor;
        var filas = datos.top[de][por];
        horizontales(figura, detalle, filas.map(function (f, i) {
            var margen = f.margen == null ? '—' : entero.format(num(f.margen)) + ' %';
            var valor = por === 'unidades' ? num(f.unidades) : num(por === 'monto' ? f.monto : f.ganancia);
            var rotulo = por === 'unidades' ? entero.format(num(f.unidades)) + ' u.' :
                         por === 'monto' ? pesos(f.monto) : pesos(f.ganancia) + ' · ' + margen;
            return {nombre: (i + 1) + '. ' + f.nombre, valor: valor, rotulo: rotulo,
                    clase: valor < 0 ? 'serie-negativa' : (i === 0 ? 'serie-ingresos' : 'serie-egresos'),
                    etiqueta: f.nombre + ': vendido ' + pesos(f.monto) + ', ' + entero.format(num(f.unidades)) +
                              ' unidades, ganancia ' + pesos(f.ganancia) + ', margen ' + margen,
                    lineas: [f.nombre, ['Vendido', pesos(f.monto)], ['Unidades', entero.format(num(f.unidades))],
                             ['Ganancia', pesos(f.ganancia), num(f.ganancia) < 0 ? 'text-peligro' : ''],
                             ['Margen sobre la venta', margen]]};
        }), (de === 'productos' ? 'Productos' : 'Rubros') + ' que más ' + {monto: 'vendieron', unidades: 'unidades movieron', ganancia: 'ganancia dejaron'}[por] + '.',
        'No hubo ventas en este período.');
    }

    /* ------------------------------------------------ 4. franja horaria -- */
    function franjas(figura, detalle) {
        var horas = datos.franjas.horas;
        if (!horas.length) { vacio(figura, 'No hubo ventas en este período.'); return; }
        var por = figura.querySelector('[data-franja-por][aria-pressed="true"]').dataset.franjaPor;
        var valor = function (h) { return por === 'tickets' ? h.tickets : num(h.monto); };
        var pico = horas.reduce(function (a, b) { return valor(b) > valor(a) ? b : a; });
        var l = lienzoDe(figura, 240, 'Ventas por hora. Hora pico: ' + pico.etiqueta + ', ' + pico.tickets +
                         ' tickets por ' + pesos(pico.monto) + '.');
        var m = {izq: por === 'tickets' ? 40 : 64, der: 6, arriba: 14, abajo: 30};
        var y = ejeY(l.svg, m, l.ancho, l.alto, 0, Math.max.apply(null, horas.map(valor)),
                     por === 'tickets' ? function (v) { return entero.format(v); } : corto);
        var banda = (l.ancho - m.izq - m.der) / horas.length;
        var lista = nodo('g', {role: 'list'}, l.svg);
        horas.forEach(function (h) {
            var x = m.izq + banda * horas.indexOf(h);
            var g = nodo('g', {}, lista);
            nodo('rect', {x: x, y: m.arriba, width: banda, height: l.alto - m.arriba - m.abajo, 'class': 'zona'}, g);
            nodo('rect', {x: x + banda * 0.15, y: y(valor(h)), width: banda * 0.7, height: Math.max(0, y(0) - y(valor(h))), rx: 2,
                          'class': h === pico ? 'serie-ingresos' : 'serie-egresos'}, g);
            texto(l.svg, x + banda / 2, l.alto - 10, String(h.hora), 'rotulo', 'middle');
            var promedio = h.tickets ? pesos(num(h.monto) / h.tickets) : '—';
            interactivo(g, detalle, 'De ' + h.hora + ' a ' + (h.hora + 1) + ' h: ' + h.tickets + ' tickets, ' + pesos(h.monto) + (h === pico ? '. Hora pico.' : ''),
                        ['De ' + h.hora + ' a ' + (h.hora + 1) + ' h' + (h === pico ? ' · hora pico' : ''),
                         ['Tickets', String(h.tickets)], ['Vendido', pesos(h.monto)], ['Ticket promedio', promedio]]);
        });
        var nota = figura.querySelector('.js-pico');
        if (nota) { nota.textContent = 'Hora pico: de ' + pico.hora + ' a ' + (pico.hora + 1) + ' h (' + pico.tickets + ' tickets, ' + pesos(pico.monto) + ').'; }
    }

    /* ------------------------------------------------------- arranque -- */
    var DIBUJOS = {evolucion: evolucion, medios: medios, top: top, franjas: franjas};
    var figuras = Array.prototype.slice.call(document.querySelectorAll('[data-grafico]'));
    var detalles = new Map();
    function dibujar(figura) {
        if (!detalles.has(figura)) { detalles.set(figura, new Detalle(figura)); }
        detalles.get(figura).ocultar();
        DIBUJOS[figura.dataset.grafico](figura, detalles.get(figura));
    }
    figuras.forEach(function (figura) {
        figura.addEventListener('click', function (evento) {
            var boton = evento.target.closest('button[aria-pressed]');
            if (!boton || !figura.contains(boton)) { return; }
            var grupo = boton.closest('[data-exclusivo]');
            if (grupo) {
                grupo.querySelectorAll('button[aria-pressed]').forEach(function (b) { b.setAttribute('aria-pressed', String(b === boton)); });
            } else {
                var encendidas = figura.querySelectorAll('[data-serie][aria-pressed="true"]').length;
                if (boton.getAttribute('aria-pressed') === 'true' && encendidas === 1) { return; }   // siempre queda una
                boton.setAttribute('aria-pressed', String(boton.getAttribute('aria-pressed') !== 'true'));
            }
            dibujar(figura);
        });
        dibujar(figura);
    });
    if ('ResizeObserver' in window) {
        var espera;
        var anchos = new Map();
        var observador = new ResizeObserver(function (entradas) {
            clearTimeout(espera);
            espera = setTimeout(function () {
                entradas.forEach(function (entrada) {
                    var figura = entrada.target.closest('[data-grafico]');
                    var ancho = Math.round(entrada.contentRect.width);
                    if (anchos.get(figura) !== ancho) { anchos.set(figura, ancho); dibujar(figura); }
                });
            }, 150);
        });
        figuras.forEach(function (f) { observador.observe(f.querySelector('.grafico-lienzo')); });
    }
})();
