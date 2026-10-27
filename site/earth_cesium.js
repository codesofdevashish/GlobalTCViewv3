/* High-resolution globe: CesiumJS + NASA GIBS imagery (no key or account needed).
   Base layers: VIIRS true colour for a chosen day (250 m, the storm's real clouds) or Blue Marble (500 m).
   Night side: city lights, blended by Cesium's real sun position. Storms: tracks in Saffir-Simpson colours,
   spinning symbols, pulses and labels. Same interface as earth.js, so the dashboard can use either.
   EarthHD.create(container, {onSelect, onSpin, nightUrl}) -> api | null */
(() => {
  "use strict";
  const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/wmts.cgi";
  const KT = 1.943844;

  function symbolCanvas(color) {
    const c = document.createElement("canvas"); c.width = c.height = 96; const g = c.getContext("2d");
    const gr = g.createRadialGradient(48, 48, 6, 48, 48, 48); gr.addColorStop(0, color); gr.addColorStop(0.45, color + "aa"); gr.addColorStop(1, color + "00");
    g.fillStyle = gr; g.beginPath(); g.arc(48, 48, 48, 0, Math.PI * 2); g.fill();
    g.strokeStyle = "#fff"; g.lineWidth = 6; g.lineCap = "round";
    g.beginPath(); g.moveTo(48, 39); g.bezierCurveTo(62, 34, 71, 44, 74, 59); g.stroke();
    g.beginPath(); g.moveTo(48, 57); g.bezierCurveTo(34, 62, 25, 52, 22, 37); g.stroke();
    g.fillStyle = "#fff"; g.beginPath(); g.arc(48, 48, 8, 0, Math.PI * 2); g.fill();
    return c;
  }
  function ringCanvas() {
    const c = document.createElement("canvas"); c.width = c.height = 96; const g = c.getContext("2d");
    g.strokeStyle = "#fff"; g.lineWidth = 4; g.beginPath(); g.arc(48, 48, 44, 0, Math.PI * 2); g.stroke(); return c;
  }
  const ymd = (ms) => { const d = new Date(ms); return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`; };

  function create(container, opts = {}) {
    const C = window.Cesium; if (!C || !container) return null;
    let viewer;
    try {
      C.Ion.defaultAccessToken = "";                       // nothing from Cesium ion is used
      viewer = new C.Viewer(container, {
        baseLayer: false, terrainProvider: new C.EllipsoidTerrainProvider(),
        animation: false, timeline: false, geocoder: false, homeButton: false, sceneModePicker: false, baseLayerPicker: false,
        navigationHelpButton: false, fullscreenButton: false, infoBox: false, selectionIndicator: false, shouldAnimate: true,
        creditContainer: Object.assign(document.createElement("div"), { className: "cz-credits" }),
      });
      container.appendChild(viewer.creditDisplay.container || document.createElement("div"));
    } catch (e) { console.warn("Cesium globe unavailable:", e); return null; }
    const scene = viewer.scene, globe = scene.globe;
    globe.enableLighting = true; globe.showGroundAtmosphere = true; globe.baseColor = C.Color.fromCssColorString("#0b2232");
    scene.skyAtmosphere.show = true; scene.fog.enabled = false;
    if (scene.sun) scene.sun.show = false; if (scene.moon) scene.moon.show = false;
    viewer.clock.currentTime = C.JulianDate.now(); viewer.clock.multiplier = 1;
    scene.screenSpaceCameraController.minimumZoomDistance = 2.5e5; scene.screenSpaceCameraController.maximumZoomDistance = 3.0e7;

    // ---- imagery: base (VIIRS of a day, or Blue Marble) + night lights
    const layers = viewer.imageryLayers;
    const wmts = (layer, set, fmt, maxLevel, dims) => new C.WebMapTileServiceImageryProvider({
      url: GIBS, layer, style: "default", format: fmt, tileMatrixSetID: set, maximumLevel: maxLevel,
      tilingScheme: new C.WebMercatorTilingScheme(), dimensions: dims, credit: new C.Credit("Imagery: NASA EOSDIS GIBS") });
    const blue = layers.addImageryProvider(wmts("BlueMarble_ShadedRelief_Bathymetry", "GoogleMapsCompatible_Level8", "image/jpeg", 8));
    blue.nightAlpha = 0.18;
    let viirs = null, viirsDay = null, base = "viirs";
    function setViirsDay(ms) {
      const day = ymd(ms); if (day === viirsDay && viirs) return; viirsDay = day;
      const nl = layers.addImageryProvider(wmts("VIIRS_SNPP_CorrectedReflectance_TrueColor", "GoogleMapsCompatible_Level9", "image/jpeg", 9, { TIME: day }), layers.indexOf(blue) + 1);
      nl.nightAlpha = 0.12; nl.show = base === "viirs";
      if (viirs) layers.remove(viirs, true); viirs = nl;
      if (nightLayer) layers.raiseToTop(nightLayer);
    }
    let nightLayer = null;
    if (opts.nightUrl && C.SingleTileImageryProvider.fromUrl) {
      C.SingleTileImageryProvider.fromUrl(opts.nightUrl).then((p) => { nightLayer = layers.addImageryProvider(p); nightLayer.dayAlpha = 0; nightLayer.nightAlpha = 0.9; })
        .catch(() => {});
    }
    const yesterday = Date.now() - 864e5;                  // yesterday's global composite is complete
    setViirsDay(yesterday);
    function setBase(which) { base = which; if (viirs) viirs.show = which === "viirs"; blue.show = true; }

    // ---- storms
    const sym = new Map(), ring = ringCanvas();
    const symFor = (c) => sym.get(c) || (sym.set(c, symbolCanvas(c)), sym.get(c));
    let replayT = null, entities = [], storms = [];
    const posAt = (s, t) => {                              // position and intensity at time t (null before genesis)
      const P = s.pts; if (!P.length) return null;
      if (t === null || t >= P[P.length - 1].t) return P[P.length - 1];
      if (t < P[0].t) return null;
      let i = 0; while (i < P.length - 2 && P[i + 1].t <= t) i++;
      const a = (t - P[i].t) / Math.max(1, P[i + 1].t - P[i].t), A = P[i], B = P[i + 1];
      return { lat: A.lat + a * (B.lat - A.lat), lon: A.lon + a * (B.lon - A.lon), kt: Math.round(A.kt + a * (B.kt - A.kt)) };
    };
    function clear() { for (const e of entities) viewer.entities.remove(e); entities = []; }
    function setStorms(list, selKey) {
      clear(); storms = list;
      list.forEach((s, si) => {
        const sel = s.key === selKey, P = s.pts;
        for (let i = 1; i < P.length; i++) {
          const seg = viewer.entities.add({ polyline: {
            positions: C.Cartesian3.fromDegreesArray([P[i - 1].lon, P[i - 1].lat, P[i].lon, P[i].lat]),
            width: sel ? 5 : 3, arcType: C.ArcType.GEODESIC,
            material: new C.PolylineGlowMaterialProperty({ color: C.Color.fromCssColorString(P[i].color).withAlpha(sel ? 1 : 0.7), glowPower: 0.18 }),
            show: new C.CallbackProperty(() => replayT === null || P[i].t <= replayT, false) } });
          entities.push(seg);
        }
        const position = new C.CallbackPositionProperty
          ? new C.CallbackPositionProperty(() => { const p = posAt(s, replayT); return p ? C.Cartesian3.fromDegrees(p.lon, p.lat, 2e4) : undefined; }, false)
          : new C.CallbackProperty(() => { const p = posAt(s, replayT); return p ? C.Cartesian3.fromDegrees(p.lon, p.lat, 2e4) : undefined; }, false);
        const t0 = performance.now() + si * 400;
        const e = viewer.entities.add({
          position, properties: { key: s.key },
          billboard: { image: symFor(s.color), scale: sel ? 0.62 : 0.48, disableDepthTestDistance: 0,
            rotation: new C.CallbackProperty(() => ((performance.now() - t0) / 1000) * (s.south ? 1.6 : -1.6), false),
            show: new C.CallbackProperty(() => !!posAt(s, replayT), false) },
          label: { text: new C.CallbackProperty(() => { const p = posAt(s, replayT); return p ? `${s.name}  ${p.kt} kt` : ""; }, false),
            font: "600 15px 'Barlow Condensed', 'Arial Narrow', sans-serif", fillColor: C.Color.WHITE, showBackground: true,
            backgroundColor: C.Color.fromCssColorString("rgba(6,12,18,0.75)"), pixelOffset: new C.Cartesian2(22, -18),
            horizontalOrigin: C.HorizontalOrigin.LEFT, disableDepthTestDistance: 0 },
        });
        entities.push(e);
        for (const off of [0, 0.5]) entities.push(viewer.entities.add({ position, billboard: { image: ring, color: new C.CallbackProperty(() => {
          const ph = (((performance.now() - t0) / 1700) + off) % 1; return C.Color.fromCssColorString(s.color).withAlpha(0.6 * (1 - ph)); }, false),
          scale: new C.CallbackProperty(() => { const ph = (((performance.now() - t0) / 1700) + off) % 1; return (sel ? 0.5 : 0.4) * (1 + 2.2 * ph); }, false),
          show: new C.CallbackProperty(() => !!posAt(s, replayT), false), disableDepthTestDistance: 0 } }));
      });
    }
    function setTime(ms) { replayT = ms === null || ms === undefined ? null : ms; if (replayT !== null) setViirsDay(replayT); else setViirsDay(yesterday); }

    // ---- picking, camera, spin
    const handler = new C.ScreenSpaceEventHandler(scene.canvas);
    handler.setInputAction((m) => {
      const p = scene.pick(m.position); const key = p && p.id && p.id.properties && p.id.properties.key && p.id.properties.key.getValue();
      if (key && opts.onSelect) opts.onSelect(key);
      if (spin) { spin = false; opts.onSpin && opts.onSpin(false); }
    }, C.ScreenSpaceEventType.LEFT_CLICK);
    handler.setInputAction(() => { if (spin) { spin = false; opts.onSpin && opts.onSpin(false); } }, C.ScreenSpaceEventType.LEFT_DOWN);
    let spin = false, home = null;
    viewer.clock.onTick.addEventListener(() => { if (spin) scene.camera.rotate(C.Cartesian3.UNIT_Z, -0.0009); });
    function focus(lat, lon, ms = 1400) {
      const h = Math.max(4.5e6, Math.min(scene.camera.positionCartographic.height || 1.6e7, 1.6e7));
      const dest = C.Cartesian3.fromDegrees(lon, lat, ms <= 1 ? 1.1e7 : h);
      if (!home) home = [lat, lon];
      if (ms <= 1) scene.camera.setView({ destination: dest }); else scene.camera.flyTo({ destination: dest, duration: ms / 1000 });
    }
    const zoom = (f) => { const h = scene.camera.positionCartographic.height; scene.camera.zoomIn(h * (1 - 1 / f)); };
    return {
      setStorms, focus, setTime, setBase,
      zoomIn: () => zoom(1.6), zoomOut: () => scene.camera.zoomOut(scene.camera.positionCartographic.height * 0.6),
      setSpin: (on) => { spin = !!on; }, reset: () => { if (home) focus(home[0], home[1], 1200); },
      hd: true, viewer,
    };
  }
  window.EarthHD = { create };
})();
