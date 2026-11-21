/* ==========================================================================
   Cafeto · tablero de Reportes y del Resumen del mes (DD-100, DD-105)

   Apache ECharts dibuja lo que la página trae en
   <template id="datosTablero"> (inerte: no se ejecuta, no choca con la CSP
   ni lo borra htmx con allowScriptTags: false). Las mismas cifras están en
   las tablas «Ver los datos», que se leen sin JavaScript. Cafeto.inicializar
   llama a CafetoTablero.iniciar(raiz) al cargar y después de cada
   actualización parcial; Cafeto.limpiar llama a CafetoTablero.liberar(raiz)
   antes.
   ========================================================================== */
(function () {
    'use strict';

    var moneda = new Intl.NumberFormat('es-AR', {style: 'currency', currency: 'ARS', maximumFractionDigits: 2});
    var entero = new Intl.NumberFormat('es-AR', {maximumFractionDigits: 3});
    var NOMBRES = {vendido: 'Vendido', interes: 'Recargos por mora', costo: 'Costo de lo vendido',
                   gastos: 'Gastos pagados', mermas: 'Mermas', incobrable: 'Incobrable'};
    var ANCHO_ESTRECHO = 420;
    var CON_DESLIZADOR = 31;

    function num(texto) { return Number(texto) || 0; }
    function pesos(valor) { return moneda.format(num(valor)); }
    function corto(valor) {
        var v = Math.abs(valor), signo = valor < 0 ? '−' : '';
        if (v >= 1e6) { return signo + '$ ' + entero.format(Math.round(v / 1e5) / 10) + ' M'; }
        if (v >= 1e3) { return signo + '$ ' + entero.format(Math.round(v / 100) / 10) + ' mil'; }
        return signo + '$ ' + entero.format(Math.round(v));
    }
    function escapar(texto) {
        return String(texto == null ? '' : texto).replace(/[&<>"']/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
        });
    }
    function detalle(titulo, lineas, muestra) {
        var html = '<div class="grafico-tip-titulo">' +
            (muestra ? '<span class="muestra ' + muestra + '" aria-hidden="true"></span>' : '') + escapar(titulo) + '</div>';
        lineas.forEach(function (l) {
            html += '<div class="grafico-tip-fila' + (l[2] ? ' ' + l[2] : '') + '"><span>' + escapar(l[0]) +
                    '</span><span>' + escapar(l[1]) + '</span></div>';
        });
        return html;
    }
    function reducido() {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }
    function conAlfa(color, alfa) {
        var m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(color);
        if (!m) { return color; }
        return 'rgba(' + parseInt(m[1], 16) + ',' + parseInt(m[2], 16) + ',' + parseInt(m[3], 16) + ',' + alfa + ')';
    }
    function apretado(figura, selector, atributo) {
        var boton = figura.querySelector(selector + '[aria-pressed="true"]');
        return boton ? boton.getAttribute(atributo) : null;
    }

    function colores() {
        var estilo = getComputedStyle(document.documentElement);
        var v = function (nombre) { return estilo.getPropertyValue(nombre).trim(); };
        var cat = [];
        for (var i = 1; i <= 8; i++) { cat.push(v('--c-cat-' + i)); }
        return {
            texto: v('--c-texto'), texto2: v('--c-texto-2'), texto3: v('--c-texto-3'),
            divisor: v('--c-divisor'), borde: v('--c-borde'), superficie: v('--c-superficie'),
            activa: v('--c-superficie-activa'), peligro: v('--c-peligro'),
            ingresos: v('--c-serie-ingresos'), egresos: v('--c-serie-egresos'), neta: v('--c-serie-neta'),
            cat: cat, calor: [v('--c-calor-bajo'), v('--c-calor-alto')]
        };
    }

    function comun(c, resumen) {
        var quieto = reducido();
        return {
            animation: !quieto, animationDuration: 350, animationDurationUpdate: 250,
            textStyle: {fontFamily: 'Inter, system-ui, -apple-system, "Segoe UI", sans-serif', color: c.texto2},
            aria: {enabled: true, label: {description: resumen}},
            tooltip: {
                backgroundColor: c.superficie, borderColor: c.borde, borderWidth: 1, padding: [8, 10],
                textStyle: {color: c.texto, fontSize: 12}, confine: true,
                extraCssText: 'box-shadow: var(--c-sombra); border-radius: .5rem; min-width: 12rem;'
            }
        };
    }
    function ejeCategorias(c, datos, extra) {
        var eje = {type: 'category', data: datos, axisTick: {show: false},
                   axisLine: {lineStyle: {color: c.texto3}}, axisLabel: {color: c.texto2, hideOverlap: true}};
        Object.keys(extra || {}).forEach(function (k) { eje[k] = extra[k]; });
        return eje;
    }
    function ejeValores(c, formato) {
        return {type: 'value', axisLabel: {color: c.texto2, formatter: formato},
                splitLine: {lineStyle: {color: c.divisor}}};
    }

    /* ------------------------------------- 1. ingresos, egresos y neta -- */
    function evolucion(figura, datos, c) {
        var e = datos.evolucion, puntos = e.puntos, t = e.total;
        if (!puntos.some(function (p) { return num(p.ingresos) || num(p.egresos); })) {
            return {vacio: 'No hubo ventas ni gastos en este período.'};
        }
        var activas = {};
        figura.querySelectorAll('[data-serie]').forEach(function (b) { activas[b.dataset.serie] = b.getAttribute('aria-pressed') === 'true'; });
        var barra = {type: 'bar', barMaxWidth: 22, barGap: '12%', emphasis: {focus: 'series'}};
        var series = [];
        if (activas.ingresos) {
            series.push(Object.assign({name: 'Ingresos', data: puntos.map(function (p) { return num(p.ingresos); }),
                                       itemStyle: {color: c.ingresos, borderRadius: [4, 4, 0, 0]}}, barra));
        }
        if (activas.egresos) {
            series.push(Object.assign({name: 'Egresos', data: puntos.map(function (p) { return num(p.egresos); }),
                                       itemStyle: {color: c.egresos, borderRadius: [4, 4, 0, 0]}}, barra));
        }
        if (activas.neta) {
            series.push({
                name: 'Ganancia neta', type: 'line', smooth: 0.35, z: 5, symbol: 'circle',
                symbolSize: puntos.length > 40 ? 4 : 7, lineStyle: {color: c.neta, width: 2.25},
                itemStyle: {color: c.neta, borderColor: c.superficie, borderWidth: 1.5},
                data: puntos.map(function (p) {
                    return {value: num(p.neta), itemStyle: {color: num(p.neta) < 0 ? c.peligro : c.neta}};
                })
            });
        }
        var muchos = puntos.length > CON_DESLIZADOR;
        var desde = Math.max(0, puntos.length - CON_DESLIZADOR);
        return {
            resumen: 'Ingresos ' + pesos(t.ingresos) + ', egresos ' + pesos(t.egresos) + ', ganancia neta ' +
                     pesos(t.neta) + ', por ' + e.unidad + '.',
            opcion: {
                grid: {left: 8, right: 12, top: 40, bottom: muchos ? 48 : 8, containLabel: true},
                xAxis: ejeCategorias(c, puntos.map(function (p) { return p.etiqueta; })),
                yAxis: ejeValores(c, corto),
                tooltip: {
                    trigger: 'axis', axisPointer: {type: 'shadow', shadowStyle: {color: conAlfa(c.texto3, 0.12)}},
                    formatter: function (params) {
                        var p = puntos[params[0].dataIndex], d = p.desglose;
                        var periodo = p.desde === p.hasta ? p.etiqueta : p.etiqueta + ' al ' + p.hasta.split('-').reverse().slice(0, 2).join('/');
                        return detalle(periodo, [
                            [NOMBRES.vendido, pesos(d.vendido)], [NOMBRES.interes, pesos(d.interes)],
                            ['Ingresos', pesos(p.ingresos), 'fw-semibold'],
                            [NOMBRES.costo, '− ' + pesos(d.costo)], [NOMBRES.gastos, '− ' + pesos(d.gastos)],
                            [NOMBRES.mermas, '− ' + pesos(d.mermas)], [NOMBRES.incobrable, '− ' + pesos(d.incobrable)],
                            ['Egresos', pesos(p.egresos), 'fw-semibold'],
                            ['Ganancia neta', pesos(p.neta), num(p.neta) < 0 ? 'text-peligro fw-semibold' : 'text-exito fw-semibold']
                        ]);
                    }
                },
                dataZoom: muchos ? [
                    {type: 'inside', startValue: desde, endValue: puntos.length - 1},
                    {type: 'slider', startValue: desde, endValue: puntos.length - 1, height: 20, bottom: 8,
                     borderColor: c.borde, backgroundColor: c.activa, fillerColor: conAlfa(c.ingresos, 0.18),
                     dataBackground: {lineStyle: {color: c.texto3}, areaStyle: {color: c.divisor}},
                     handleStyle: {color: c.ingresos, borderColor: c.ingresos}, moveHandleStyle: {color: c.ingresos},
                     textStyle: {color: c.texto2}}
                ] : [],
                toolbox: {
                    right: 0, top: 0, itemSize: 16,
                    iconStyle: {borderColor: c.texto2}, emphasis: {iconStyle: {borderColor: c.ingresos, textFill: c.texto}},
                    feature: {saveAsImage: {title: 'Guardar como imagen', name: 'cafeto-ingresos-egresos-' + datos.desde + '-al-' + datos.hasta,
                                            pixelRatio: 2, backgroundColor: c.superficie}}
                },
                series: series
            }
        };
    }

    /* ------------------------------------------------ 2. medios de pago -- */
    function medios(figura, datos, c, estrecho) {
        var partes = datos.medios.partes;
        if (!partes.length) { return {vacio: 'No hubo cobros en este período.'}; }
        var porcentaje = function (i) { return entero.format(num(partes[i].porcentaje)) + ' %'; };
        var indice = {};
        partes.forEach(function (p, i) { indice[p.nombre] = i; });
        var centro = estrecho ? ['50%', '38%'] : ['32%', '50%'];
        return {
            resumen: 'Cobrado por medio de pago, total ' + pesos(datos.medios.total) + ': ' +
                     partes.map(function (p, i) { return p.nombre + ' ' + porcentaje(i); }).join(', ') + '.',
            opcion: {
                color: c.cat,
                title: {text: corto(num(datos.medios.total)), subtext: 'Cobrado', left: centro[0], top: centro[1],
                        textAlign: 'center', textVerticalAlign: 'middle', itemGap: 2,
                        textStyle: {color: c.texto, fontSize: 15, fontWeight: 600}, subtextStyle: {color: c.texto2, fontSize: 11}},
                legend: estrecho
                    ? {type: 'plain', bottom: 0, left: 'center', width: '96%', icon: 'circle', itemWidth: 10, itemHeight: 10,
                       itemGap: 12, textStyle: {color: c.texto2},
                       formatter: function (nombre) { return nombre + ' ' + porcentaje(indice[nombre]); }}
                    : {type: 'scroll', orient: 'vertical', right: 0, top: 'middle', icon: 'circle', itemWidth: 10, itemHeight: 10,
                       itemGap: 12, textStyle: {color: c.texto2}, pageTextStyle: {color: c.texto2},
                       formatter: function (nombre) { return nombre + '  ' + porcentaje(indice[nombre]); }},
                tooltip: {trigger: 'item', formatter: function (p) {
                    return detalle(p.name, [['Cobrado', pesos(partes[p.dataIndex].monto)], ['Del total', porcentaje(p.dataIndex)]],
                                   'muestra-cat-' + (p.dataIndex % 8 + 1));
                }},
                series: [{
                    type: 'pie', radius: estrecho ? ['40%', '60%'] : ['52%', '76%'], center: centro,
                    avoidLabelOverlap: true, label: {show: false}, labelLine: {show: false},
                    itemStyle: {borderColor: c.superficie, borderWidth: 2, borderRadius: 4},
                    emphasis: {scale: true, scaleSize: 6},
                    data: partes.map(function (p) { return {name: p.nombre, value: num(p.monto)}; })
                }]
            }
        };
    }

    /* ---------------------------------------- 3. productos y rubros -- */
    function top(figura, datos, c, estrecho) {
        var de = apretado(figura, '[data-top-de]', 'data-top-de') || 'productos';
        var por = apretado(figura, '[data-top-por]', 'data-top-por') || 'monto';
        var filas = datos.top[de][por];
        if (!filas.length) { return {vacio: 'No hubo ventas en este período.'}; }
        var valor = function (f) { return por === 'unidades' ? num(f.unidades) : num(por === 'monto' ? f.monto : f.ganancia); };
        var margen = function (f) { return f.margen == null ? '—' : entero.format(num(f.margen)) + ' %'; };
        var rotulo = function (f) {
            return por === 'unidades' ? entero.format(num(f.unidades)) + ' u.' :
                   por === 'monto' ? corto(num(f.monto)) : corto(num(f.ganancia)) + ' · ' + margen(f);
        };
        return {
            resumen: (de === 'productos' ? 'Productos' : 'Rubros') + ' que más ' +
                     {monto: 'vendieron', unidades: 'unidades movieron', ganancia: 'ganancia dejaron'}[por] + ': ' +
                     filas.slice(0, 3).map(function (f) { return f.nombre; }).join(', ') + '.',
            opcion: {
                grid: {left: 4, right: estrecho ? 64 : 110, top: 4, bottom: 4, containLabel: true},
                xAxis: {type: 'value', show: false},
                yAxis: ejeCategorias(c, filas.map(function (f, i) { return (i + 1) + '. ' + f.nombre; }), {
                    inverse: true, axisLine: {show: false},
                    axisLabel: {color: c.texto, width: estrecho ? 120 : 220, overflow: 'truncate', hideOverlap: false}
                }),
                tooltip: {trigger: 'item', formatter: function (p) {
                    var f = filas[p.dataIndex];
                    return detalle(f.nombre, [['Vendido', pesos(f.monto)], ['Unidades', entero.format(num(f.unidades))],
                                             ['Ganancia', pesos(f.ganancia), num(f.ganancia) < 0 ? 'text-peligro' : ''],
                                             ['Margen sobre la venta', margen(f)]]);
                }},
                series: [{
                    type: 'bar', barMaxWidth: 18, showBackground: true,
                    backgroundStyle: {color: c.activa, borderRadius: 4},
                    label: {show: true, position: 'right', color: c.texto2, formatter: function (p) { return rotulo(filas[p.dataIndex]); }},
                    data: filas.map(function (f, i) {
                        var v = valor(f);
                        return {value: v, itemStyle: {
                            color: v < 0 ? c.peligro : (i === 0 ? c.ingresos : c.egresos),
                            borderRadius: v < 0 ? [4, 0, 0, 4] : [0, 4, 4, 0]
                        }};
                    })
                }]
            }
        };
    }

    /* ------------------------------------------------ 4. franja horaria -- */
    function franjas(figura, datos, c) {
        var horas = datos.franjas.horas;
        var nota = figura.querySelector('.js-pico');
        if (!horas.length) {
            if (nota) { nota.textContent = ''; }
            return {vacio: 'No hubo ventas en este período.'};
        }
        var por = apretado(figura, '[data-franja-por]', 'data-franja-por') || 'tickets';
        var vista = apretado(figura, '[data-franja-vista]', 'data-franja-vista') || 'barras';
        var valor = function (celda) { return por === 'tickets' ? celda.tickets : num(celda.monto); };
        var formato = por === 'tickets' ? function (v) { return entero.format(v); } : corto;
        if (vista === 'calor') { return calor(datos, c, por, valor, formato, nota); }
        var pico = horas.reduce(function (a, b) { return valor(b) > valor(a) ? b : a; });
        var degradado = function (color) {
            return new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [
                {offset: 0, color: color}, {offset: 1, color: conAlfa(color, 0.35)}]);
        };
        if (nota) {
            nota.textContent = 'Hora pico: de ' + pico.hora + ' a ' + (pico.hora + 1) + ' h (' + pico.tickets +
                               ' tickets, ' + pesos(pico.monto) + ').';
        }
        return {
            resumen: 'Ventas por hora. Hora pico: ' + pico.etiqueta + ', ' + pico.tickets + ' tickets por ' + pesos(pico.monto) + '.',
            opcion: {
                grid: {left: 8, right: 8, top: 12, bottom: 8, containLabel: true},
                xAxis: ejeCategorias(c, horas.map(function (h) { return String(h.hora); })),
                yAxis: ejeValores(c, formato),
                tooltip: {trigger: 'axis', axisPointer: {type: 'shadow', shadowStyle: {color: conAlfa(c.texto3, 0.12)}},
                          formatter: function (params) {
                              var h = horas[params[0].dataIndex];
                              return detalle('De ' + h.hora + ' a ' + (h.hora + 1) + ' h' + (h === pico ? ' · hora pico' : ''), [
                                  ['Tickets', String(h.tickets)], ['Vendido', pesos(h.monto)],
                                  ['Ticket promedio', h.tickets ? pesos(num(h.monto) / h.tickets) : '—']]);
                          }},
                series: [{
                    type: 'bar', barMaxWidth: 28,
                    data: horas.map(function (h) {
                        return {value: valor(h), itemStyle: {color: degradado(h === pico ? c.ingresos : c.egresos), borderRadius: [4, 4, 0, 0]}};
                    })
                }]
            }
        };
    }

    function calor(datos, c, por, valor, formato, nota) {
        var horas = datos.franjas.horas, semana = datos.franjas.semana;
        var celdas = [], maximo = 0, mejor = null;
        semana.forEach(function (dia, y) {
            dia.horas.forEach(function (celda, x) {
                var v = valor(celda);
                celdas.push([x, y, v]);
                if (v > maximo) { maximo = v; mejor = {dia: dia.dia, hora: horas[x].hora, celda: celda}; }
            });
        });
        if (nota) {
            nota.textContent = mejor ? 'Más movimiento: ' + mejor.dia.toLowerCase() + ' de ' + mejor.hora + ' a ' +
                (mejor.hora + 1) + ' h (' + mejor.celda.tickets + ' tickets, ' + pesos(mejor.celda.monto) + ').' : '';
        }
        return {
            resumen: 'Mapa de calor de ' + (por === 'tickets' ? 'tickets' : 'lo vendido') + ' por día de la semana y hora.' +
                     (mejor ? ' Más movimiento el ' + mejor.dia.toLowerCase() + ' a las ' + mejor.hora + ' h.' : ''),
            opcion: {
                grid: {left: 8, right: 8, top: 8, bottom: 40, containLabel: true},
                xAxis: ejeCategorias(c, horas.map(function (h) { return String(h.hora); }), {splitArea: {show: false}}),
                yAxis: ejeCategorias(c, semana.map(function (d) { return d.corto; }), {inverse: true, axisLine: {show: false}}),
                visualMap: {
                    min: 0, max: Math.max(maximo, 1), calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
                    itemWidth: 10, itemHeight: 140, text: ['Más', 'Menos'], textGap: 6, textStyle: {color: c.texto2},
                    inRange: {color: c.calor}, formatter: formato
                },
                tooltip: {trigger: 'item', formatter: function (p) {
                    var x = p.value[0], y = p.value[1], celda = semana[y].horas[x], h = horas[x].hora;
                    return detalle(semana[y].dia + ' de ' + h + ' a ' + (h + 1) + ' h', [
                        ['Tickets', String(celda.tickets)], ['Vendido', pesos(celda.monto)]]);
                }},
                series: [{
                    type: 'heatmap', data: celdas, progressive: 0,
                    itemStyle: {borderColor: c.superficie, borderWidth: 2, borderRadius: 3},
                    emphasis: {itemStyle: {borderColor: c.texto, borderWidth: 1}}
                }]
            }
        };
    }

    /* ------------------------------------------------------- arranque -- */
    var DIBUJOS = {evolucion: evolucion, medios: medios, top: top, franjas: franjas};
    var vivos = new Map();
    var tamanios = window.ResizeObserver ? new ResizeObserver(function (entradas) {
        window.requestAnimationFrame(function () {
            entradas.forEach(function (entrada) {
                var registro = vivos.get(entrada.target.closest('[data-grafico]'));
                if (!registro || !registro.figura.isConnected) { return; }
                if (estrechez(registro) !== registro.estrecho) { dibujar(registro); }
                else if (registro.grafico) { registro.grafico.resize(); }
            });
        });
    }) : null;

    function fuenteDe(figura) {
        var raiz = (figura && figura.isConnected && figura.closest('#contenido, main, body')) || document;
        return raiz.querySelector('#datosTablero') || document.getElementById('datosTablero');
    }
    function textoDe(fuente) {
        if (!fuente) { return ''; }
        return fuente.content ? fuente.content.textContent : fuente.textContent;
    }
    function datosDe(figura) {
        var texto = textoDe(fuenteDe(figura));
        if (!texto) { return null; }
        try { return JSON.parse(texto); } catch (e) { return null; }
    }
    function estrechez(registro) { return registro.lienzo.clientWidth < ANCHO_ESTRECHO; }

    function mostrarVacio(registro, texto) {
        soltar(registro);
        var lienzo = registro.lienzo;
        lienzo.textContent = '';
        var aviso = document.createElement('p');
        aviso.className = 'grafico-sin-datos';
        aviso.textContent = texto;
        lienzo.appendChild(aviso);
    }
    function soltar(registro) {
        if (registro.grafico) { registro.grafico.dispose(); registro.grafico = null; }
        if (window.echarts && registro.lienzo) {
            var lugar = registro.lienzo.querySelector('.grafico-echarts');
            var previa = (lugar && window.echarts.getInstanceByDom(lugar)) || window.echarts.getInstanceByDom(registro.lienzo);
            if (previa && !previa.isDisposed()) { previa.dispose(); }
        }
    }

    function dibujar(registro, recrear) {
        var fuente = fuenteDe(registro.figura);
        var datos = datosDe(registro.figura);
        if (!datos) { return; }
        registro.firmaDatos = textoDe(fuente);
        if (!window.echarts) {
            mostrarVacio(registro, 'No se pudo cargar el gráfico: los números están en «Ver los datos».');
            return;
        }
        var c = colores();
        registro.estrecho = estrechez(registro);
        var armado = DIBUJOS[registro.figura.dataset.grafico](registro.figura, datos, c, registro.estrecho);
        if (armado.vacio) { mostrarVacio(registro, armado.vacio); return; }
        if (recrear) { soltar(registro); }
        if (!registro.grafico) {
            registro.lienzo.textContent = '';
            var lugar = document.createElement('div');
            lugar.className = 'grafico-echarts';
            registro.lienzo.appendChild(lugar);
            registro.grafico = window.echarts.init(lugar, null, {renderer: 'canvas'});
        }
        var opcion = comun(c, armado.resumen);
        Object.keys(armado.opcion).forEach(function (k) {
            opcion[k] = k === 'tooltip' ? Object.assign({}, opcion.tooltip, armado.opcion.tooltip) : armado.opcion[k];
        });
        registro.grafico.setOption(opcion, true);
    }

    function preparar(figura) {
        if (!figura || !figura.isConnected || !DIBUJOS[figura.dataset.grafico]) { return; }
        var firmaDatos = textoDe(fuenteDe(figura));
        if (vivos.has(figura)) {
            var actual = vivos.get(figura);
            var vivo = actual.grafico && !actual.grafico.isDisposed() && actual.lienzo.querySelector('canvas');
            if (vivo && actual.firmaDatos === firmaDatos) {
                actual.grafico.resize();
                return;
            }
            liberarRegistro(figura, actual);
        }
        var lienzo = figura.querySelector('.grafico-lienzo');
        if (!lienzo) { return; }
        var registro = {figura: figura, lienzo: lienzo, grafico: null, estrecho: null, firmaDatos: firmaDatos};
        vivos.set(figura, registro);
        dibujar(registro, true);
        if (tamanios) { tamanios.observe(lienzo); }
    }

    function liberarRegistro(figura, registro) {
        soltar(registro);
        if (tamanios) { tamanios.unobserve(registro.lienzo); }
        vivos.delete(figura);
    }

    window.CafetoTablero = {
        iniciar: function (raiz) {
            if (raiz && raiz !== document && !raiz.isConnected) {
                raiz = (raiz.id && document.getElementById(raiz.id)) || document.getElementById('contenido') || document;
            }
            raiz = raiz || document;
            vivos.forEach(function (registro, figura) { if (!figura.isConnected) { liberarRegistro(figura, registro); } });
            var figuras = Array.prototype.slice.call(raiz.querySelectorAll('[data-grafico]'));
            if (raiz.matches && raiz.matches('[data-grafico]')) { figuras.unshift(raiz); }
            figuras.forEach(preparar);
        },
        liberar: function (raiz) {
            if (!raiz) { return; }
            vivos.forEach(function (registro, figura) {
                if (raiz === document || raiz.contains(figura) || !figura.isConnected) { liberarRegistro(figura, registro); }
            });
        },
        redibujar: function () { vivos.forEach(function (registro) { dibujar(registro, true); }); }
    };

    document.addEventListener('click', function (evento) {
        var boton = evento.target.closest('[data-grafico] button[aria-pressed]');
        if (!boton) { return; }
        var figura = boton.closest('[data-grafico]');
        var grupo = boton.closest('[data-exclusivo]');
        if (grupo) {
            grupo.querySelectorAll('button[aria-pressed]').forEach(function (b) { b.setAttribute('aria-pressed', String(b === boton)); });
        } else {
            var encendidas = figura.querySelectorAll('[data-serie][aria-pressed="true"]').length;
            if (boton.getAttribute('aria-pressed') === 'true' && encendidas === 1) { return; }
            boton.setAttribute('aria-pressed', String(boton.getAttribute('aria-pressed') !== 'true'));
        }
        var registro = vivos.get(figura);
        if (registro) { dibujar(registro); } else { preparar(figura); }
    });

    document.addEventListener('cafeto:tema', function () { window.CafetoTablero.redibujar(); });
})();
