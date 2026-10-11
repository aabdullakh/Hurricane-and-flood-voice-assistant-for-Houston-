/* Houston Flood Watch front end. No framework, no third-party calls except the ElevenLabs widget.
   Every value from the API is inserted with textContent (never innerHTML), so data can't inject markup. */
(function () {
  "use strict";

  const WIDGET_SRC = "https://unpkg.com/@elevenlabs/convai-widget-embed@0.19.0/dist/index.js";
  const WIDGET_SRI = "sha384-Hq6Ry47vrwXsbkoZrxzEYn2bHG8p+KtLso8+Tlz2FjHptRWI0FTgAKQoh25jF4Ms";
  const LEAFLET = "/vendor/leaflet-1.9.4/";
  const HOUSTON = [29.76, -95.37];
  const AREA = [[29.45, -96.0], [30.25, -94.85]]; // same box the server answers for
  const SEVERITY = { rising_fast: 0, rising: 1, not_rising: 2, no_trend: 3, no_recent_data: 4 };
  const MARKER_SIZE = { rising_fast: 30, rising: 24, not_rising: 16, no_trend: 16, no_recent_data: 16 };

  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid.nodeType ? kid : String(kid));
    return n;
  };

  // ---------------------------------------------------------------- icons (inline SVG, no external files)
  const PATHS = {
    up2: "M12 3 4 11l1.4 1.4L12 5.8l6.6 6.6L20 11 12 3Zm0 8-8 8 1.4 1.4 6.6-6.6 6.6 6.6L20 19l-8-8Z",
    up: "M12 6 4 14l1.4 1.4L12 8.8l6.6 6.6L20 14 12 6Z",
    level: "M4 11h16v2H4z",
    unknown: "M11 18h2v-2h-2v2Zm1-14a4 4 0 0 0-4 4h2a2 2 0 1 1 2 2c-.6 0-1 .4-1 1v2h2v-1.2A4 4 0 0 0 12 4Z",
    alert: "M12 2 1 21h22L12 2Zm1 15h-2v-2h2v2Zm0-4h-2V9h2v4Z",
    info: "M11 7h2v2h-2zm0 4h2v6h-2zm1-9a10 10 0 1 0 0 20 10 10 0 0 0 0-20Z",
    replay: "M12 5V2L7 6l5 4V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8Z",
  };
  const STATE_ICON = { rising_fast: "up2", rising: "up", not_rising: "level", no_trend: "unknown", no_recent_data: "unknown" };
  const RISK_ICON = { high: "alert", elevated: "alert", normal: "level", unknown: "unknown", info: "info" };
  const svg = (name, cls = "icon") => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true"); s.setAttribute("class", cls);
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", PATHS[name]); s.append(p);
    return s;
  };
  const markerDot = (state) => { const d = el("span", { class: "gauge-marker " + state, "aria-hidden": "true" }); d.append(svg(STATE_ICON[state], "")); return d; };
  const svgString = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="${PATHS[name]}"/></svg>`;

  // ---------------------------------------------------------------- language
  let lang = pickLanguage();
  const T = () => window.I18N[lang];
  const t = (key, ...args) => { const v = T()[key]; return typeof v === "function" ? v(...args) : v; };

  function pickLanguage() {
    const q = new URLSearchParams(location.search).get("lang");
    if (q === "en" || q === "es") return q;
    try { const s = localStorage.getItem("hfw-lang"); if (s === "en" || s === "es") return s; } catch (_) { /* storage blocked */ }
    return (navigator.language || "en").toLowerCase().startsWith("es") ? "es" : "en";
  }
  function setLanguage(next) {
    lang = next;
    try { localStorage.setItem("hfw-lang", next); } catch (_) { /* fine without it */ }
    applyStatic(); renderAll(); configureWidget();
  }
  function applyStatic() {
    document.documentElement.lang = lang;
    document.title = t("page_title");
    document.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = t(n.dataset.i18n); });
    document.querySelectorAll("[data-i18n-aria]").forEach((n) => n.setAttribute("aria-label", t(n.dataset.i18nAria)));
    document.querySelectorAll("[data-i18n-placeholder]").forEach((n) => n.setAttribute("placeholder", t(n.dataset.i18nPlaceholder)));
    document.querySelectorAll("[data-i18n-html]").forEach((n) => { n.innerHTML = t(n.dataset.i18nHtml); }); // our own static strings only
    document.querySelectorAll(".lang-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
    $("#talk-other-lang").lang = lang === "en" ? "es" : "en";
  }

  // ---------------------------------------------------------------- formatting
  const locale = () => (lang === "es" ? "es-US" : "en-US");
  const fmtTime = (iso) => {
    try { return new Intl.DateTimeFormat(locale(), { hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" }).format(new Date(iso)); }
    catch (_) { return ""; }
  };
  const fmtDateTime = (iso) => {
    try { return new Intl.DateTimeFormat(locale(), { weekday: "short", hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" }).format(new Date(iso)); }
    catch (_) { return iso || ""; }
  };
  const fmtNum = (n, digits = 2) => new Intl.NumberFormat(locale(), { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(n);
  const fmtLevel = (n) => new Intl.NumberFormat(locale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
  const ago = (m) => (m < 90 ? t("min_ago", m) : t("h_ago", Math.round(m / 60)));
  const trendWords = (tr) => {
    if (tr === null || tr === undefined) return t("trend_unknown");
    if (Math.abs(tr) < 0.05) return t("steady");
    return tr > 0 ? t("rising_by", fmtNum(Math.abs(tr), 1)) : t("falling_by", fmtNum(Math.abs(tr), 1));
  };
  const stateLabel = (s) => t("state_" + s);
  const eventName = (ev) => (lang === "es" && window.I18N.NWS_ES[ev]) || ev || t("lvl_other");
  const badge = (kind, text) => el("span", { class: "badge " + kind }, svg(RISK_ICON[kind] || "info", ""), text);
  const stateBadge = (s) => {
    const kind = s === "rising_fast" ? "high" : s === "rising" ? "elevated" : s === "not_rising" ? "normal" : "unknown";
    const b = el("span", { class: "badge " + kind }, svg(STATE_ICON[s], ""), stateLabel(s));
    return b;
  };

  // ---------------------------------------------------------------- API
  class ApiError extends Error { constructor(status, detail) { super(detail || "HTTP " + status); this.status = status; this.detail = detail; } }
  async function api(path, opts = {}) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), opts.timeout || 15000);
    try {
      const r = await fetch(path, { ...opts, signal: ctl.signal, headers: { Accept: "application/json", ...(opts.headers || {}) }, cache: "no-store" });
      let body = null;
      try { body = await r.json(); } catch (_) { /* not JSON */ }
      if (!r.ok) throw new ApiError(r.status, body && body.detail);
      return body;
    } finally { clearTimeout(timer); }
  }
  const errText = (e) => (e && e.status === 429 ? t("err_rate") : e && e.detail === "outside_area" ? t("err_outside") : t("err_network"));

  // ---------------------------------------------------------------- state
  const S = { overview: null, overviewError: false, shelters: null, sheltersError: false, detail: null, map: null, markers: new Map(), selected: null, pin: null };

  // ---------------------------------------------------------------- 1. status: one plain answer from live data
  function computeStatus(d) {
    const gauges = d.gauges || [];
    const count = (s) => gauges.filter((g) => g.state === s).length;
    const c = { rising_fast: count("rising_fast"), rising: count("rising"), not_rising: count("not_rising"),
                no_data: count("no_recent_data") + count("no_trend") };
    const fresh = gauges.length - count("no_recent_data");
    const alerts = d.alerts || [];
    const high = alerts.filter((a) => a.level === "high");
    const elevated = alerts.filter((a) => a.level === "elevated");
    if (high.length) return { level: "high", headline: t("h_high", eventName(high[0].event)), detail: t("d_high"), c };
    if (elevated.length) return { level: "elevated", headline: t("h_elevated", eventName(elevated[0].event)), detail: t("d_elevated"), c };
    if (c.rising_fast) return { level: "elevated", headline: t("h_rising_fast", c.rising_fast), detail: t("d_rising_fast", c.rising_fast), c };
    if (!d.alerts_available) return { level: "unknown", headline: t("h_alerts_down"), detail: t("d_alerts_down"), c };
    if (!fresh) return { level: "unknown", headline: t("h_no_gauges"), detail: t("d_calm"), c };
    return { level: "normal", headline: t("h_calm"), detail: (c.rising ? t("d_rising", c.rising) + " " : "") + t("d_calm"), c };
  }

  function renderStatus() {
    const box = $("#status"), h = $("#status-headline"), detail = $("#status-detail"), facts = $("#status-facts"), alertsBox = $("#status-alerts"), src = $("#status-source");
    const d = S.overview;
    if (!d) {
      box.dataset.level = S.overviewError ? "unknown" : "loading";
      h.textContent = S.overviewError ? t("h_failed") : t("status_loading");
      detail.textContent = S.overviewError ? t("d_failed") : "";
      facts.replaceChildren(); alertsBox.replaceChildren(); src.textContent = "";
      return;
    }
    const st = computeStatus(d);
    box.dataset.level = st.level;
    h.textContent = st.headline;
    detail.textContent = st.detail;
    facts.replaceChildren(...[["rising_fast", st.c.rising_fast, "f_rising_fast"], ["rising", st.c.rising, "f_rising"],
                               ["not_rising", st.c.not_rising, "f_not_rising"], ["no_recent_data", st.c.no_data, "f_no_data"]]
      .filter(([, n]) => n > 0) // only states that have gauges: shorter, same truth
      .map(([state, n, key]) => el("li", {}, markerDot(state), el("span", { class: "num", text: fmtNum(n, 0) }), t(key))));
    const rows = (d.alerts || []).map((a) => {
      const kind = a.level === "high" ? "high" : a.level === "elevated" ? "elevated" : "info";
      return el("div", { class: "alert-row " + kind }, badge(kind, a.level === "high" ? t("lvl_high") : a.level === "elevated" ? t("lvl_elevated") : t("lvl_other")),
        el("div", {}, el("b", { text: eventName(a.event) }), a.expires ? el("small", { text: t("expires", fmtDateTime(a.expires)) }) : null));
    });
    alertsBox.replaceChildren(...rows);
    let line = d.replay ? t("source_line_replay", (d.gauges || []).length, fmtTime(d.now)) : t("source_line", (d.gauges || []).length, fmtTime(d.now));
    const lastPoll = d.last_poll ? new Date(d.last_poll) : null;
    if (d.mode === "live" && (d.last_poll_error || !lastPoll || Date.now() - lastPoll.getTime() > 45 * 60 * 1000)) line += t("source_stale", lastPoll ? fmtTime(d.last_poll) : "–");
    src.textContent = line;
  }

  function renderReplay() {
    const b = $("#replay-banner"), r = S.overview && S.overview.replay;
    if (!r) { b.hidden = true; b.replaceChildren(); return; }
    b.hidden = false;
    b.replaceChildren(svg("replay"), el("span", { text: r.synthetic ? t("replay_synthetic") : t("replay_banner") }));
  }

  async function loadOverview() {
    try {
      S.overview = await api("/overview");
      S.overviewError = false;
    } catch (e) {
      S.overviewError = true; // keep the last good data if we had some
    }
    renderStatus(); renderReplay(); renderGaugeList(); renderLegend(); renderMarkers();
  }

  // ---------------------------------------------------------------- gauge list (works without the map)
  const sortedGauges = () => (S.overview ? [...S.overview.gauges] : [])
    .sort((a, b) => (SEVERITY[a.state] - SEVERITY[b.state]) || ((b.trend_ft_per_hr ?? -99) - (a.trend_ft_per_hr ?? -99)));
  function renderGaugeList() {
    const body = $("#gauge-rows");
    const gs = sortedGauges();
    if (!gs.length) { body.replaceChildren(el("tr", {}, el("td", { colspan: "5", text: S.overviewError ? t("h_failed") : t("loading") }))); return; }
    body.replaceChildren(...gs.map((g) => el("tr", {},
      el("td", {}, el("button", { type: "button", text: g.name, onclick: () => selectGauge(g.site_id, true) })),
      el("td", {}, stateBadge(g.state)),
      el("td", { text: `${fmtLevel(g.level_ft)} ${t("ft")}` }),
      el("td", { text: trendWords(g.trend_ft_per_hr) }),
      el("td", { text: ago(g.age_minutes) }))));
  }
  function renderLegend() {
    const th = S.overview && S.overview.thresholds;
    const leg = $("#legend");
    if (!th) { leg.replaceChildren(); return; }
    const item = (state, text) => el("li", {}, markerDot(state), text);
    leg.replaceChildren(
      item("rising_fast", t("leg_rising_fast", fmtNum(th.rise_high_ft_hr, 1))),
      item("rising", t("leg_rising", fmtNum(th.rise_elevated_ft_hr, 1))),
      item("not_rising", t("leg_not_rising")),
      item("no_recent_data", t("leg_no_data", fmtNum(th.stale_minutes / 60, 0))));
  }

  // ---------------------------------------------------------------- 3. map (Leaflet, loaded only when needed)
  let leafletPromise = null;
  function loadLeaflet() {
    if (leafletPromise) return leafletPromise;
    leafletPromise = new Promise((resolve, reject) => {
      const css = el("link", { rel: "stylesheet", href: LEAFLET + "leaflet.css", integrity: "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=", crossorigin: "anonymous" });
      const js = el("script", { src: LEAFLET + "leaflet.js", integrity: "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=", crossorigin: "anonymous" });
      js.onload = () => resolve(window.L); js.onerror = reject;
      document.head.append(css, js);
    });
    return leafletPromise;
  }
  async function initMap() {
    if (S.map) return;
    let L;
    try { L = await loadLeaflet(); } catch (_) { $("#map").replaceChildren(el("p", { class: "map-placeholder", text: t("map_failed") })); return; }
    $("#map").replaceChildren();
    const map = L.map("map", { center: HOUSTON, zoom: 10, minZoom: 9, maxZoom: 15, maxBounds: L.latLngBounds(AREA).pad(0.15), maxBoundsViscosity: 0.8, zoomControl: true, attributionControl: true });
    map.attributionControl.setPrefix(false);
    let tileErrors = 0, warned = false;
    L.tileLayer("/public/tiles/{z}/{x}/{y}.png", {
      minZoom: 9, maxZoom: 15, bounds: L.latLngBounds(AREA).pad(0.05), crossOrigin: false,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).on("tileerror", () => {
      if (++tileErrors >= 6 && !warned) { warned = true; showMapNotice(t("tiles_failed")); }
    }).addTo(map);
    map.on("click", (e) => checkPoint(e.latlng.lat, e.latlng.lng));
    S.map = map;
    renderMarkers();
  }
  function showMapNotice(text) {
    const n = el("p", { class: "msg-error", role: "status", text });
    $("#map").parentElement.insertBefore(n, $("#legend"));
  }
  function renderMarkers() {
    if (!S.map || !S.overview) return;
    const L = window.L;
    for (const m of S.markers.values()) m.remove();
    S.markers.clear();
    for (const g of sortedGauges().reverse()) { // most severe added last = drawn on top
      const size = MARKER_SIZE[g.state] || 16;
      const label = `${g.name}: ${stateLabel(g.state)}, ${fmtLevel(g.level_ft)} ${t("ft")}`;
      const icon = L.divIcon({ className: "", iconSize: [size, size], html: `<span class="gauge-marker ${g.state}">${svgString(STATE_ICON[g.state])}</span>` });
      const m = L.marker([g.lat, g.lon], { icon, keyboard: true, title: label, zIndexOffset: (5 - SEVERITY[g.state]) * 100, bubblingMouseEvents: false })
        .on("click", () => selectGauge(g.site_id, false))
        .addTo(S.map);
      const node = m.getElement();
      if (node) { node.setAttribute("role", "button"); node.setAttribute("aria-label", label); }
      S.markers.set(g.site_id, m);
    }
    if (S.selected) highlight(S.selected);
  }
  function highlight(siteId) {
    for (const [id, m] of S.markers) { const n = m.getElement(); if (n) n.classList.toggle("is-selected", id === siteId); }
  }
  function placePin(lat, lon) {
    if (!S.map) return;
    const L = window.L;
    if (S.pin) S.pin.remove();
    S.pin = L.marker([lat, lon], { icon: L.divIcon({ className: "", iconSize: [28, 28], html: '<span class="point-pin"><span></span></span>' }), keyboard: false, interactive: false, title: t("you_checked") }).addTo(S.map);
  }

  // ---------------------------------------------------------------- detail: the gauge plate
  function selectGauge(siteId, pan) {
    const g = S.overview && S.overview.gauges.find((x) => x.site_id === siteId);
    if (!g) return;
    S.selected = siteId; S.detail = { kind: "gauge", siteId };
    highlight(siteId);
    if (pan && S.map) S.map.setView([g.lat, g.lon], Math.max(S.map.getZoom(), 12));
    if (pan && !narrow()) $("#map-section").scrollIntoView({ block: "start" });
    renderDetail();
    $("#detail").focus({ preventScroll: true });
    revealDetail();
  }
  function gaugePlate(g) {
    const replay = !!(S.overview && S.overview.replay); // replay gauges are not real USGS sites: say so, and no USGS link
    return el("article", { class: "plate" },
      el("header", { class: "plate-head" }, el("div", {}, el("small", { text: (replay ? t("gauge_kicker_replay") : t("gauge_kicker")) + " · " + g.site_id }), el("h3", { id: "detail-title", text: g.name }))),
      el("div", { class: "plate-level" }, el("span", { class: "big", text: fmtLevel(g.level_ft) }), el("span", { class: "unit", text: t("ft") }), stateBadge(g.state)),
      el("dl", { class: "plate-rows" },
        el("div", {}, el("dt", { text: t("p_change") }), el("dd", { text: trendWords(g.trend_ft_per_hr) })),
        el("div", {}, el("dt", { text: t("p_reading") }), el("dd", { text: t("at_time", fmtTime(g.observed_at), ago(g.age_minutes)) }))),
      el("p", { class: "plate-note", text: t("gauge_note") }),
      el("div", { class: "plate-actions" },
        el("button", { type: "button", class: "btn btn-secondary", text: t("check_here"), onclick: () => checkPoint(g.lat, g.lon, { label: g.name }) }),
        replay ? null : el("a", { class: "btn btn-plain", href: `https://waterdata.usgs.gov/monitoring-location/USGS-${encodeURIComponent(g.site_id)}/`, rel: "noopener", text: t("usgs_link") })));
  }

  // ---------------------------------------------------------------- detail: conditions at a point
  let pointSeq = 0;
  async function checkPoint(lat, lon, opts = {}) {
    const rlat = Math.round(lat * 100) / 100, rlon = Math.round(lon * 100) / 100; // ~1 km, before it leaves the browser
    const seq = ++pointSeq;
    S.selected = null; highlight(null);
    placePin(rlat, rlon);
    S.detail = { kind: "point", loading: true, lat: rlat, lon: rlon, opts };
    renderDetail();
    try {
      const data = await api(`/public/conditions?lat=${rlat}&lon=${rlon}`);
      if (seq !== pointSeq) return;
      S.detail = { kind: "point", data, lat: rlat, lon: rlon, opts };
    } catch (e) {
      if (seq !== pointSeq) return;
      S.detail = { kind: "point", error: e, lat: rlat, lon: rlon, opts };
    }
    renderDetail();
    revealDetail();
  }
  function riskExplain(d) {
    const th = S.overview && S.overview.thresholds;
    if (d.risk === "high") return t("explain_high");
    if (d.risk === "elevated") return t("explain_elevated");
    if (d.risk === "low") return t("explain_low");
    if (d.nearest_gauge_miles !== null && th && d.nearest_gauge_miles > th.gauge_max_miles) return t("pt_too_far", t("miles", d.nearest_gauge_miles));
    if (!d.alerts_available) return t("pt_alerts_down");
    return t("explain_unknown");
  }
  function pointCard(s) {
    const title = s.opts.label ? t("pt_searched", s.opts.label) : t("pt_title");
    const head = [el("h3", { id: "detail-title", class: "detail-empty-title", text: title }),
                  el("p", { class: "plate-note", text: s.opts.approximate ? t("pt_approx") + " " + t("pt_rounded") : t("pt_rounded") })];
    if (s.loading) return el("div", { class: "plate" }, ...head, el("p", { role: "status", text: t("pt_loading") }));
    if (s.error) return el("div", { class: "plate" }, ...head, el("p", { class: "msg-error", text: errText(s.error) }),
      s.error.detail === "outside_area" ? null : el("div", { class: "plate-actions" }, el("button", { type: "button", class: "btn btn-secondary", text: t("retry"), onclick: () => checkPoint(s.lat, s.lon, s.opts) })));
    const d = s.data;
    const riskKind = d.risk === "high" ? "high" : d.risk === "elevated" ? "elevated" : d.risk === "low" ? "normal" : "unknown";
    const g = (d.nearest_gauges || [])[0];
    const alerts = d.alerts || [];
    const sh = d.shelters && d.shelters.shelters && d.shelters.shelters[0];
    const row = (label, ...content) => el("div", {}, el("dt", { text: label }), el("dd", {}, ...content));
    const askWhere = s.opts.label || (g && g.name) || null;
    return el("div", { class: "plate" }, ...head,
      el("div", {}, badge(riskKind, t("risk_" + (d.risk === "low" ? "low" : d.risk)))),
      el("p", { text: riskExplain(d) }),
      el("dl", { class: "plate-rows" },
        row(t("pt_nearest"), g ? [el("b", { text: g.name }), stateBadge(g.state), " ",
                                  el("span", { text: `${t("miles", g.distance_miles)} · ${fmtLevel(g.level_ft)} ${t("ft")} · ${trendWords(g.trend_ft_per_hr)} · ${ago(g.age_minutes)}` })]
                                : el("span", { text: d.nearest_gauge_miles !== null ? t("pt_too_far", t("miles", d.nearest_gauge_miles)) : t("h_no_gauges") })),
        row(t("pt_alerts"), !d.alerts_available ? t("pt_alerts_down") : alerts.length
          ? alerts.map((a) => el("span", { class: "alert-line" }, badge(a.level === "high" ? "high" : a.level === "elevated" ? "elevated" : "info", eventName(a.event)), " "))
          : t("pt_no_alerts")),
        row(t("pt_rain"), d.rain && d.rain.rain_past_3h_in !== null && d.rain.rain_next_3h_in !== null
          ? t("rain_text", fmtNum(d.rain.rain_past_3h_in), fmtNum(d.rain.rain_next_3h_in)) : t("rain_none")),
        row(t("pt_shelter"), !(d.shelters && d.shelters.available)
          ? [el("span", { text: t("pt_shelter_down") }), " ", el("a", { href: "tel:211", text: t("sh_call211") })]
          : sh ? [el("span", { text: t("shelter_line", sh.name, t("miles", sh.distance_miles)) }), " ",
                  el("small", { class: "plate-note", text: t("pt_shelter_source", fmtTime(d.shelters.checked_at)) })]
               : [el("span", { text: t("pt_shelter_none_lead") }), " ", el("a", { href: "tel:211", text: t("call_211_sentence") })])),
      d.replay ? el("p", { class: "msg-error", text: d.replay.synthetic ? t("replay_synthetic") : t("replay_banner") }) : null,
      d.data_as_of ? el("p", { class: "plate-note", text: t("pt_data_time", fmtTime(d.data_as_of)) }) : null,
      askWhere ? el("div", { class: "plate-actions" }, el("button", { type: "button", class: "btn btn-secondary", text: t("ask_assistant"), onclick: () => askAssistant(t("about_place", askWhere)) })) : null);
  }
  // On narrow screens the detail card sits below the map: bring it into view so a tap never seems to do nothing.
  const narrow = () => window.matchMedia("(max-width: 959px)").matches;
  const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function revealDetail() {
    if (narrow()) $("#detail").scrollIntoView({ block: "start", behavior: reduceMotion() ? "auto" : "smooth" });
  }

  function renderDetail() {
    const box = $("#detail");
    box.tabIndex = -1;
    if (!S.detail) {
      box.replaceChildren(el("h3", { id: "detail-title", class: "detail-empty-title", text: t("detail_empty_title") }), el("p", { text: t("detail_empty") }));
      return;
    }
    if (S.detail.kind === "gauge") {
      const g = S.overview && S.overview.gauges.find((x) => x.site_id === S.detail.siteId);
      box.replaceChildren(g ? gaugePlate(g) : el("p", { text: t("loading") }));
      return;
    }
    box.replaceChildren(pointCard(S.detail));
  }

  // ---------------------------------------------------------------- search
  function setupSearch() {
    const form = $("#search"), input = $("#search-input");
    const msg = el("p", { class: "search-msg", role: "status", "aria-live": "polite" });
    form.append(msg);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const q = input.value.trim();
      if (q.length < 2) return;
      const btn = form.querySelector("button");
      btn.disabled = true; msg.textContent = t("s_searching");
      try {
        const r = await api("/public/geocode", { method: "POST", body: JSON.stringify({ q }), headers: { "Content-Type": "application/json" } });
        if (!r.found) { msg.textContent = r.reason === "outside_area" ? t("s_outside") : t("s_not_found"); return; }
        msg.textContent = "";
        if (S.map) S.map.setView([r.lat, r.lon], 13);
        await checkPoint(r.lat, r.lon, { label: q, approximate: r.approximate });
        $("#detail").focus({ preventScroll: true });
        if (!narrow()) $("#detail").scrollIntoView({ block: "nearest" });
      } catch (err) {
        msg.textContent = err && err.status === 429 ? t("err_rate") : t("s_error");
      } finally { btn.disabled = false; }
    });
  }

  // ---------------------------------------------------------------- 4. shelters (official data only)
  async function loadShelters() {
    try { S.shelters = await api("/public/shelters?limit=20"); S.sheltersError = false; }
    catch (_) { S.sheltersError = true; }
    renderShelters();
  }
  function renderShelters() {
    const box = $("#shelter-list"), d = S.shelters;
    const call211 = () => el("a", { class: "btn btn-secondary", href: "tel:211", text: t("sh_call211") });
    const officialLinks = () => [el("a", { class: "btn btn-plain", href: "https://www.readyharris.org/", rel: "noopener", text: "ReadyHarris" }),
                                 el("a", { class: "btn btn-plain", href: "https://houstonoem.org/", rel: "noopener", text: "Houston OEM" })];
    if (S.sheltersError || (d && !d.available)) {
      box.replaceChildren(el("div", { class: "empty-state" }, el("h3", { text: t("sh_down") }), el("div", { class: "actions" }, call211(), ...officialLinks())));
      return;
    }
    if (!d) { box.replaceChildren(el("p", { text: t("loading") })); return; }
    const list = d.shelters || [];
    if (!list.length) {
      box.replaceChildren(el("div", { class: "empty-state" },
        el("h3", { text: t("sh_empty_title") }),
        el("p", { text: t("sh_empty_body", fmtNum(d.radius_miles, 0), fmtTime(d.checked_at)) }),
        el("p", { text: t("sh_how") }),
        el("div", { class: "actions" }, call211(), ...officialLinks())));
      return;
    }
    const yn = (v) => (v === true ? t("sh_yes") : v === false ? t("sh_no") : t("sh_unknown"));
    box.replaceChildren(
      ...list.map((s) => el("article", { class: "shelter" },
        el("h3", { text: s.name }),
        s.address ? el("p", { text: s.address }) : null,
        el("p", { class: "meta", text: t("sh_from_center", t("miles", s.distance_miles)) }),
        el("p", { class: "meta", text: `${t("sh_pets")}: ${yn(s.accepts_pets)} · ${t("sh_access")}: ${yn(s.accessible)}` + (s.organization ? ` · ${t("sh_org")}: ${s.organization}` : "") }))),
      el("p", { class: "plate-note", text: t("sh_source_line", fmtTime(d.checked_at)) }));
  }

  // ---------------------------------------------------------------- 2. voice widget
  let widgetPromise = null;
  const widget = () => $("elevenlabs-convai");
  function loadWidget() {
    if (widgetPromise) return widgetPromise;
    configureWidget();
    widgetPromise = new Promise((resolve, reject) => {
      const s = el("script", { src: WIDGET_SRC, integrity: WIDGET_SRI, crossorigin: "anonymous", async: true });
      s.onload = () => customElements.whenDefined("elevenlabs-convai").then(resolve);
      s.onerror = () => reject(new Error("widget failed"));
      document.head.append(s);
      setTimeout(() => reject(new Error("widget timeout")), 20000);
    });
    widgetPromise.catch(() => talkStatus(t("t_failed")));
    return widgetPromise;
  }
  function configureWidget() {
    const w = widget();
    if (!w || callActive()) return; // never switch language in the middle of a call
    w.setAttribute("language", lang);
    w.setAttribute("text-contents", JSON.stringify(T().widget_text));
  }
  const shadowButtons = () => { const r = widget() && widget().shadowRoot; return r ? [...r.querySelectorAll("button")] : []; };
  const buttonText = (b) => ((b.getAttribute("aria-label") || "") + " " + (b.textContent || "")).trim().toLowerCase();
  const callActive = () => {
    const ends = [window.I18N.en.widget_text.end_call, window.I18N.es.widget_text.end_call].map((x) => x.toLowerCase());
    return shadowButtons().some((b) => ends.some((e) => buttonText(b).includes(e)));
  };
  function talkStatus(text) { $("#talk-status").textContent = text || ""; }
  async function startTalking() {
    talkStatus(t("t_loading"));
    try { await loadWidget(); } catch (_) { return false; }
    widget().classList.add("is-active"); $("#talk-fab").hidden = true;
    await new Promise((r) => setTimeout(r, 300)); // let the widget render its first view
    const start = [window.I18N.en.widget_text.start_call, window.I18N.es.widget_text.start_call].map((x) => x.toLowerCase());
    const btn = shadowButtons().find((b) => start.some((s) => buttonText(b).includes(s)));
    if (btn) btn.click();
    else document.dispatchEvent(new CustomEvent("elevenlabs-agent:expand", { detail: { action: "expand" } }));
    talkStatus(t("t_open"));
    return true;
  }
  async function askAssistant(q) {
    if (widgetPromise && callActive()) {
      widget().dispatchEvent(new CustomEvent("elevenlabs-agent:user-message", { detail: { message: q }, bubbles: true, composed: true }));
      talkStatus(t("t_sent", q));
      return;
    }
    const ok = await startTalking();
    if (ok) talkStatus(t("t_say", q));
    $("#talk").scrollIntoView({ block: "nearest" });
  }
  function renderExamples() {
    $("#examples").replaceChildren(...T().examples.map((q) => el("li", {}, el("button", { type: "button", class: "example", text: q, onclick: () => askAssistant(q) }))));
  }

  // ---------------------------------------------------------------- wiring
  function renderAll() { renderStatus(); renderReplay(); renderExamples(); renderGaugeList(); renderLegend(); renderMarkers(); renderDetail(); renderShelters(); }

  function init() {
    applyStatic();
    renderAll();
    document.querySelectorAll(".lang-btn").forEach((b) => b.addEventListener("click", () => setLanguage(b.dataset.lang)));
    $("#talk-start").addEventListener("click", startTalking);
    $("#talk-fab").addEventListener("click", () => { $("#talk").scrollIntoView({ block: "start" }); startTalking(); });
    if ("IntersectionObserver" in window) { // "Talk" button shows whenever "Start talking" is off screen, until the widget is in use
      new IntersectionObserver((entries) => {
        const away = !entries[0].isIntersecting;
        $("#talk-fab").hidden = !away || widget().classList.contains("is-active");
      }).observe($("#talk-start"));
    }
    setupSearch();
    loadOverview(); setInterval(loadOverview, 60000);
    loadShelters();
    // Map: load when it's about to be seen (saves ~150 KB on phones that never scroll to it).
    const mapSection = $("#map-section");
    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) { io.disconnect(); initMap(); } }, { rootMargin: "400px" });
      io.observe(mapSection);
    } else initMap();
    // Voice widget (1.5 MB): after the page is up, so it never delays the answer.
    const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 1500));
    window.addEventListener("load", () => idle(() => loadWidget(), { timeout: 2500 }));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
