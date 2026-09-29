const CARD_TAG = "discogs-random-record-card";

class DiscogsRandomRecordCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = { entity: "sensor.discogs_random_pick" };
    this._spinning = false;
    this._pickStartedAt = 0;
    this._previousPickId = "";
    this._lastPickId = "";
    this._seenEntityState = false;
    this._timeout = null;
    this._lastRenderKey = "";
  }

  setConfig(config) {
    if (!config) throw new Error("Discogs Random Record: configuration is required");
    this._config = { entity: "sensor.discogs_random_pick", ...config };
    this._render(true);
  }

  set hass(hass) {
    this._hass = hass;
    const entity = hass?.states?.[this._config.entity];
    const attributes = entity?.attributes || {};
    const incomingPickId = attributes.pick_id || "";
    if (!this._seenEntityState) {
      this._lastPickId = incomingPickId;
      this._seenEntityState = true;
    } else if (attributes.status === "picking" && incomingPickId && incomingPickId !== this._lastPickId) {
      this._previousPickId = this._lastPickId;
      this._lastPickId = incomingPickId;
      this._spinning = true;
      this._pickStartedAt = Date.now();
    }
    if (this._spinning && attributes.status === "selected" &&
        attributes.pick_id && attributes.pick_id !== this._previousPickId) {
      this._lastPickId = attributes.pick_id;
      const wait = Math.max(0, 1700 - (Date.now() - this._pickStartedAt));
      window.clearTimeout(this._settleTimer);
      this._settleTimer = window.setTimeout(() => {
        this._spinning = false;
        this._clearRequestTimeout();
        this._render(true);
      }, wait);
    } else if (this._spinning && ["empty", "error"].includes(attributes.status) &&
               attributes.pick_id !== this._previousPickId) {
      this._lastPickId = attributes.pick_id || this._lastPickId;
      this._spinning = false;
      this._clearRequestTimeout();
    }
    this._render();
  }

  getCardSize() { return 4; }

  static getStubConfig() {
    return { entity: "sensor.discogs_random_pick" };
  }

  _clearRequestTimeout() {
    window.clearTimeout(this._timeout);
    this._timeout = null;
  }

  async _pick() {
    if (this._spinning || !this._hass) return;
    const current = this._hass.states?.[this._config.entity];
    this._previousPickId = current?.attributes?.pick_id || "";
    this._spinning = true;
    this._pickStartedAt = Date.now();
    this._render(true);
    this._timeout = window.setTimeout(() => {
      this._spinning = false;
      this._render(true, "No result arrived. Check that Discogs Connector is running and your collection is loaded.");
    }, 30000);
    try {
      await this._hass.connection.sendMessagePromise({
        type: "fire_event",
        event_type: "discogs_random_pick_requested",
        event_data: { source: "dashboard" },
      });
    } catch (error) {
      this._spinning = false;
      this._clearRequestTimeout();
      this._render(true, `Could not request a pick: ${error?.message || error}`);
    }
  }

  _safeImageUrl(value) {
    try {
      const url = new URL(value, window.location.href);
      return ["https:", "http:"].includes(url.protocol) ? url.href : "";
    } catch (_) { return ""; }
  }

  _render(force = false, messageOverride = "") {
    if (!this.shadowRoot || !this._config) return;
    const entity = this._hass?.states?.[this._config.entity];
    const a = entity?.attributes || {};
    const renderKey = [entity?.state, a.status, a.pick_id, a.artwork_url,
      a.title, a.artist, a.release_year, a.master_year, this._spinning, messageOverride].join("|");
    if (!force && renderKey === this._lastRenderKey) return;
    this._lastRenderKey = renderKey;
    const selected = a.status === "selected" && entity;
    const imageUrl = selected ? this._safeImageUrl(a.artwork_url || a.release_artwork_url || a.master_artwork_url) : "";
    const status = messageOverride || (this._spinning ? "Shuffling your collection…" :
      selected ? `${a.collection_size || ""} records in your collection` :
      a.status === "empty" ? (a.error || "Load your Discogs collection first") :
      a.status === "error" ? (a.error || "The last pick failed") :
      "Choose a record from your collection");
    const cover = imageUrl
      ? `<img class="cover ${this._spinning ? "spinning" : ""}" src="${this._escape(imageUrl)}" alt="Album cover for ${this._escape(a.title || "selected release")}">`
      : `<div class="vinyl ${this._spinning ? "spinning" : ""}" aria-label="Vinyl record animation"><i></i></div>`;
    const yearParts = [];
    if (a.release_year) yearParts.push(`Edition ${this._escape(a.release_year)}`);
    if (a.master_year && String(a.master_year) !== String(a.release_year)) yearParts.push(`Originally ${this._escape(a.master_year)}`);
    const formats = Array.isArray(a.formats) ? a.formats.filter(Boolean).join(" · ") : "";
    const releaseLink = selected && a.discogs_url && this._safeImageUrl(a.discogs_url)
      ? `<a class="release-link" href="${this._escape(a.discogs_url)}" target="_blank" rel="noopener noreferrer">View release on Discogs ↗</a>` : "";
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;color:var(--primary-text-color,#e8edf5);font-family:var(--ha-card-header-font-family,inherit)}
        ha-card{overflow:hidden;border-radius:var(--ha-card-border-radius,18px);background:linear-gradient(135deg,var(--ha-card-background,var(--card-background-color,#172033)),color-mix(in srgb,var(--primary-color,#55c6bc) 11%,var(--ha-card-background,var(--card-background-color,#172033))));border:1px solid color-mix(in srgb,var(--primary-color,#55c6bc) 18%,transparent);box-shadow:var(--ha-card-box-shadow,0 10px 34px #0002)}
        .layout{display:grid;grid-template-columns:minmax(112px,168px) minmax(0,1fr);gap:clamp(16px,3vw,28px);align-items:center;padding:clamp(16px,3vw,26px)}
        .art{position:relative;aspect-ratio:1;border-radius:14px;overflow:hidden;background:linear-gradient(150deg,#22334a,#101722);display:grid;place-items:center;box-shadow:0 8px 24px #0004}
        .cover{width:100%;height:100%;object-fit:cover}.vinyl{width:82%;height:82%;border-radius:50%;background:repeating-radial-gradient(circle,#111 0 3px,#242c35 3px 4px);display:grid;place-items:center;box-shadow:0 4px 16px #0009}.vinyl i{width:27%;height:27%;border-radius:50%;background:var(--primary-color,#55c6bc);border:4px solid #d4e8e5;box-sizing:border-box}
        .spinning{animation:disc-spin 1.15s linear infinite}.copy{min-width:0}.eyebrow{text-transform:uppercase;letter-spacing:.15em;font-size:.72rem;font-weight:700;color:var(--primary-color,#55c6bc);margin:0 0 8px}.title{font-size:clamp(1.3rem,3.2vw,2rem);line-height:1.12;letter-spacing:-.025em;margin:0 0 7px;overflow-wrap:anywhere}.artist{font-size:1.02rem;opacity:.88;margin:0}.meta{font-size:.86rem;opacity:.7;margin:10px 0 0;line-height:1.5}.status{font-size:.83rem;opacity:.67;margin:12px 0 0;min-height:1.2em}.controls{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:18px}button{border:0;border-radius:999px;padding:11px 17px;background:var(--primary-color,#55c6bc);color:var(--text-primary-color,#102126);font:600 .92rem/1.2 system-ui,sans-serif;cursor:pointer;transition:transform .16s ease,filter .16s ease}button:hover:not(:disabled){transform:translateY(-1px);filter:brightness(1.06)}button:disabled{opacity:.6;cursor:wait}.release-link{font-size:.82rem;color:var(--secondary-text-color,var(--primary-color,#55c6bc));text-decoration:none}.release-link:hover{text-decoration:underline}.error{color:var(--error-color,#ff8b8b);opacity:1}
        @keyframes disc-spin{to{transform:rotate(360deg)}}
        @media(max-width:440px){.layout{grid-template-columns:92px minmax(0,1fr);gap:14px;padding:14px}.title{font-size:1.2rem}.controls{margin-top:12px}}
        @media(prefers-reduced-motion:reduce){.spinning{animation-duration:4s}button{transition:none}}
      </style>
      <ha-card><div class="layout"><div class="art">${cover}</div><div class="copy">
        <p class="eyebrow">${this._spinning ? "Vinyl roulette" : selected ? "Your next spin" : "Discogs collection"}</p>
        <h2 class="title">${selected ? this._escape(a.title || entity.state) : this._spinning ? "Finding a record…" : "Pick a random record"}</h2>
        ${selected ? `<p class="artist">${this._escape(a.artist || "Unknown artist")}</p>` : ""}
        ${selected && yearParts.length ? `<p class="meta">${yearParts.join(" · ")}</p>` : ""}
        ${selected && formats ? `<p class="meta">${this._escape(formats)}</p>` : ""}
        <p class="status ${messageOverride || a.status === "error" || a.status === "empty" ? "error" : ""}" aria-live="polite">${this._escape(status)}</p>
        <div class="controls"><button type="button" id="pick" ${this._spinning ? "disabled" : ""}>${this._spinning ? "Shuffling…" : selected ? "Spin again" : "Pick a record"}</button>${releaseLink}</div>
      </div></div></ha-card>`;
    this.shadowRoot.querySelector("#pick")?.addEventListener("click", () => this._pick());
  }

  _escape(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
    })[char]);
  }
}

if (!customElements.get(CARD_TAG)) customElements.define(CARD_TAG, DiscogsRandomRecordCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "Discogs Random Record",
    description: "Spin your locally cached Discogs collection and reveal a random album.",
    preview: true,
  });
}

console.info("%c DISCOGS RANDOM RECORD %c Discogs Connector", "background:#102126;color:#55c6bc;font-weight:700", "background:#55c6bc;color:#102126;font-weight:700");

