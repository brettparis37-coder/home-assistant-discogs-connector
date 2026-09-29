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
    this._frameTimer = null;
    this._finishTimer = null;
    this._lockTimer = null;
    this._shuffleArtworks = [];
    this._shuffleOrder = [];
    this._shuffleIndex = -1;
    this._shuffleImage = "";
    this._animatedPickId = "";
    this._animationFinished = false;
    this._finalCoverLocked = false;
    this._dominantColor = "";
    this._dominantColorPickId = "";
    this._shuffleDurationMs = 6200;
    this._frameIntervalMs = 160;
    this._lastRenderKey = "";
    this._states = null;
    this._unsubscribeStates = null;
  }

  connectedCallback() {
    const event = new CustomEvent("context-request", {
      bubbles: true,
      composed: true,
      cancelable: true,
    });
    event.context = "states";
    event.subscribe = true;
    event.callback = (states, unsubscribe) => {
      this._states = states;
      if (typeof unsubscribe === "function") this._unsubscribeStates = unsubscribe;
      this._handleEntityUpdate(states?.[this._config.entity]);
    };
    this.dispatchEvent(event);
    this._render();
  }

  disconnectedCallback() {
    this._unsubscribeStates?.();
    this._unsubscribeStates = null;
  }

  setConfig(config) {
    if (!config) throw new Error("Discogs Random Record: configuration is required");
    this._config = { entity: "sensor.discogs_random_pick", ...config };
    this._render(true);
  }

  set hass(hass) {
    this._hass = hass;
    this._handleEntityUpdate(hass?.states?.[this._config.entity]);
  }

  _currentEntity() {
    return this._states?.[this._config.entity] || this._hass?.states?.[this._config.entity];
  }

  _handleEntityUpdate(entity) {
    const attributes = entity?.attributes || {};
    const incomingPickId = attributes.pick_id || "";
    if (attributes.status === "selected" && incomingPickId) {
      this._prepareDominantColor(
        attributes.artwork_url || attributes.release_artwork_url || attributes.master_artwork_url || "",
        incomingPickId,
        attributes.dominant_color || "",
      );
    }
    if (!this._seenEntityState) {
      this._lastPickId = incomingPickId;
      this._seenEntityState = true;
      if (attributes.status === "picking" && incomingPickId) {
        this._previousPickId = "";
        this._startShuffle(attributes, incomingPickId);
      }
    } else if (attributes.status === "picking" && incomingPickId && incomingPickId !== this._lastPickId) {
      this._previousPickId = this._lastPickId;
      this._lastPickId = incomingPickId;
      this._startShuffle(attributes, incomingPickId);
    } else if (attributes.status === "selected" && incomingPickId && incomingPickId !== this._lastPickId) {
      // A fast event may deliver only the final sensor update to the browser.
      this._previousPickId = this._lastPickId;
      this._lastPickId = incomingPickId;
      this._startShuffle(attributes, incomingPickId);
    } else if (attributes.status === "picking" && incomingPickId === this._animatedPickId) {
      this._setShuffleArtworks(attributes.shuffle_artworks, attributes.final_artwork_url || attributes.artwork_url);
    }
    if (!this._spinning && attributes.status === "selected" &&
        incomingPickId && incomingPickId === this._animatedPickId) {
      this._animatedPickId = "";
      this._animationFinished = false;
      this._clearRequestTimeout();
    }
    if (this._spinning && attributes.status === "selected" &&
        attributes.pick_id === this._animatedPickId) {
      this._setShuffleArtworks(attributes.shuffle_artworks, attributes.final_artwork_url || attributes.artwork_url);
      if (Date.now() - this._pickStartedAt >= this._shuffleDurationMs - 700) {
        this._lockFinalCover(attributes);
      }
    } else if (this._spinning && ["empty", "error"].includes(attributes.status) &&
               attributes.pick_id !== this._previousPickId) {
      this._lastPickId = attributes.pick_id || this._lastPickId;
      this._stopShuffle();
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

  _setShuffleArtworks(values, finalArtwork = "") {
    const next = Array.isArray(values)
      ? [...new Set(values.map((value) => this._safeImageUrl(value)).filter(Boolean))]
      : [];
    const finalUrl = this._safeImageUrl(finalArtwork);
    const samePool = next.length === this._shuffleArtworks.length &&
      next.every((url, index) => url === this._shuffleArtworks[index]);
    if (next.length && (!samePool || !this._shuffleOrder.length)) {
      this._shuffleArtworks = next;
      const candidates = next.filter((url) => url !== finalUrl);
      for (let i = candidates.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [candidates[i], candidates[j]] = [candidates[j], candidates[i]];
      }
      this._shuffleOrder = candidates.slice(0, this._shuffleFrameCount());
      this._shuffleIndex = -1;
      this._shuffleImage = "";
    }
  }

  _shuffleFrameCount() {
    const holdMs = 700;
    let elapsed = 0;
    let frames = 0;
    while (elapsed < this._shuffleDurationMs - holdMs && frames < 1000) {
      frames++;
      const progress = elapsed / this._shuffleDurationMs;
      elapsed += 120 + Math.round(305 * progress * progress);
    }
    return frames;
  }

  _startShuffle(attributes = {}, pickId = "") {
    this._stopShuffle(false);
    this._spinning = true;
    this._pickStartedAt = Date.now();
    this._animatedPickId = pickId;
    this._shuffleArtworks = [];
    this._shuffleOrder = [];
    this._shuffleImage = "";
    this._shuffleIndex = -1;
    this._animationFinished = false;
    this._finalCoverLocked = false;
    this._dominantColor = "";
    this._dominantColorPickId = "";
    this._setShuffleArtworks(attributes.shuffle_artworks, attributes.final_artwork_url || attributes.artwork_url);
    this._prepareDominantColor(
      attributes.artwork_url || attributes.release_artwork_url || attributes.master_artwork_url || "",
      pickId,
      attributes.dominant_color || "",
    );
    this._advanceShuffleFrame();
    const waitForFinalCover = () => {
      const current = this._currentEntity()?.attributes || {};
      if (current.status === "selected" && current.pick_id === this._animatedPickId) {
        this._lockFinalCover(current);
      } else if (this._spinning) {
        this._lockTimer = window.setTimeout(waitForFinalCover, 100);
      }
    };
    this._lockTimer = window.setTimeout(waitForFinalCover, this._shuffleDurationMs - 700);
    this._finishTimer = window.setTimeout(() => {
      this._spinning = false;
      const current = this._currentEntity();
      const hasResult = current?.attributes?.status === "selected" &&
        current?.attributes?.pick_id === this._animatedPickId;
      this._animationFinished = !hasResult;
      if (hasResult) {
        this._animatedPickId = "";
        this._clearRequestTimeout();
      }
      this._render(true);
    }, this._shuffleDurationMs);
  }

  _advanceShuffleFrame() {
    if (!this._spinning) return;
    if (this._finalCoverLocked) {
      this._frameTimer = window.setTimeout(() => this._advanceShuffleFrame(), 100);
      return;
    }
    const elapsed = Date.now() - this._pickStartedAt;
    const progress = Math.min(1, elapsed / this._shuffleDurationMs);
    this._frameIntervalMs = 120 + Math.round(305 * progress * progress);
    if (this._shuffleIndex + 1 < this._shuffleOrder.length) {
      this._shuffleIndex++;
      this._shuffleImage = this._shuffleOrder[this._shuffleIndex];
      this._render(true);
    } else {
      // If the collection has fewer covers than the reel needs, hold the last unique cover.
      this._frameTimer = window.setTimeout(() => this._advanceShuffleFrame(), this._frameIntervalMs);
      return;
    }
    this._frameTimer = window.setTimeout(() => this._advanceShuffleFrame(), this._frameIntervalMs);
  }

  _stopShuffle(clearTimeout = true) {
    this._spinning = false;
    window.clearTimeout(this._frameTimer);
    window.clearTimeout(this._finishTimer);
    window.clearTimeout(this._lockTimer);
    this._frameTimer = null;
    this._finishTimer = null;
    this._lockTimer = null;
    this._animatedPickId = "";
    this._animationFinished = false;
    if (clearTimeout) this._clearRequestTimeout();
  }

  async _pick() {
    if (this._spinning || !this._hass) return;
    const current = this._currentEntity();
    this._previousPickId = current?.attributes?.pick_id || "";
    this._startShuffle();
    this._render(true);
    this._timeout = window.setTimeout(() => {
      this._stopShuffle(false);
      this._render(true, "No result arrived. Check that Discogs Connector is running and your collection is loaded.");
    }, 30000);
    try {
      await this._hass.connection.sendMessagePromise({
        type: "fire_event",
        event_type: "discogs_random_pick_requested",
        event_data: { source: "dashboard" },
      });
    } catch (error) {
      this._stopShuffle();
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

  _lockFinalCover(attributes = {}) {
    if (!this._spinning || this._finalCoverLocked) return;
    const url = this._safeImageUrl(attributes.artwork_url || attributes.release_artwork_url || attributes.master_artwork_url);
    if (!url) return;
    this._finalCoverLocked = true;
    this._shuffleImage = url;
    this._render(true);
  }

  _prepareDominantColor(value, pickId, providedColor = "") {
    const url = this._safeImageUrl(value);
    if (!url || this._dominantColorPickId === pickId) return;
    this._dominantColorPickId = pickId;
    this._dominantColor = "";
    if (/^#[0-9a-f]{6}$/i.test(providedColor)) {
      this._dominantColor = providedColor;
      this._render(true);
      return;
    }
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.onload = () => {
      if (this._dominantColorPickId !== pickId) return;
      try {
        const canvas = document.createElement("canvas");
        canvas.width = 24;
        canvas.height = 24;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        context.drawImage(image, 0, 0, 24, 24);
        const pixels = context.getImageData(0, 0, 24, 24).data;
        const buckets = new Map();
        for (let i = 0; i < pixels.length; i += 4) {
          const r = pixels[i], g = pixels[i + 1], b = pixels[i + 2];
          const max = Math.max(r, g, b), min = Math.min(r, g, b);
          if (pixels[i + 3] < 180 || max < 42 || min > 238 || max - min < 18) continue;
          const key = `${r >> 4},${g >> 4},${b >> 4}`;
          const entry = buckets.get(key) || { count: 0, r: 0, g: 0, b: 0 };
          entry.count++;
          entry.r += r; entry.g += g; entry.b += b;
          buckets.set(key, entry);
        }
        const dominant = [...buckets.values()].sort((a, b) => b.count - a.count)[0];
        if (!dominant) return;
        const scale = 0.48;
        const color = [dominant.r, dominant.g, dominant.b]
          .map((channel) => Math.round(channel / dominant.count * scale))
          .map((channel) => Math.max(24, Math.min(145, channel)));
        this._dominantColor = `rgb(${color.join(",")})`;
        this._render(true);
      } catch (_) {
        // Some artwork hosts disallow canvas sampling; retain the dashboard theme color.
      }
    };
    image.src = url;
  }

  _render(force = false, messageOverride = "") {
    if (!this.shadowRoot || !this._config) return;
    const entity = this._currentEntity();
    const a = entity?.attributes || {};
    const renderKey = [entity?.state, a.status, a.pick_id, a.artwork_url, a.dominant_color,
      a.title, a.artist, a.release_year, a.master_year, this._spinning, messageOverride].join("|");
    if (!force && renderKey === this._lastRenderKey) return;
    this._lastRenderKey = renderKey;
    const selected = a.status === "selected" && entity;
    const imageUrl = this._spinning
      ? this._shuffleImage
      : selected ? this._safeImageUrl(a.artwork_url || a.release_artwork_url || a.master_artwork_url) : "";
    const backgroundColor = !this._spinning && selected && this._dominantColorPickId === a.pick_id && this._dominantColor
      ? this._dominantColor : "var(--ha-card-background,var(--card-background-color,#172033))";
    const status = messageOverride || (this._spinning ? "Shuffling through your collection…" :
      this._animationFinished ? "Waiting for the selected album…" :
      selected ? `${a.collection_size || ""} records in your collection` :
      a.status === "empty" ? (a.error || "Load your Discogs collection first") :
      a.status === "error" ? (a.error || "The last pick failed") :
      "Choose a record from your collection");
    const prevImageUrl = this._finalCoverLocked
      ? this._shuffleOrder[this._shuffleIndex] || imageUrl
      : this._shuffleOrder[this._shuffleIndex - 1] || imageUrl;
    const nextImageUrl = this._finalCoverLocked
      ? imageUrl
      : this._shuffleOrder[this._shuffleIndex + 1] || imageUrl;
    const cover = imageUrl && this._spinning
      ? `<div class="reel-window" aria-label="Shuffling through album covers">
          <img class="reel-image reel-prev" src="${this._escape(prevImageUrl)}" alt="">
          <img class="reel-image reel-next" src="${this._escape(nextImageUrl)}" alt="">
          <img class="reel-image reel-active" style="--reel-step-ms:${this._frameIntervalMs}ms" src="${this._escape(imageUrl)}" alt="Album artwork shuffle">
        </div>`
      : imageUrl
      ? `<img class="cover" src="${this._escape(imageUrl)}" alt="Album cover for ${this._escape(a.title || "selected release")}">`
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
        ha-card{display:block;overflow:hidden;border-radius:var(--ha-card-border-radius,18px);border:1px solid color-mix(in srgb,var(--primary-color,#55c6bc) 28%,transparent);box-shadow:var(--ha-card-box-shadow,0 10px 34px #0002);min-height:218px;box-sizing:border-box;transition:background-color .45s ease}
        .layout{display:grid;grid-template-columns:clamp(112px,22vw,168px) minmax(0,1fr);gap:clamp(16px,3vw,28px);align-items:center;padding:clamp(16px,3vw,26px);min-height:218px;box-sizing:border-box}
        .art{position:relative;aspect-ratio:1;border-radius:14px;overflow:hidden;background:linear-gradient(150deg,#22334a,#101722);display:grid;place-items:center;perspective:900px;box-shadow:0 8px 24px #0004}
        .cover{width:100%;height:100%;object-fit:cover}.reel-window{position:absolute;inset:0;overflow:hidden;perspective:850px;transform-style:preserve-3d}.reel-image{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;border-radius:10px;backface-visibility:hidden}.reel-prev{transform:translateY(-70%) scale(.82) rotateX(34deg);filter:brightness(.58);opacity:.8}.reel-next{transform:translateY(70%) scale(.82) rotateX(-34deg);filter:brightness(.62);opacity:.86}.reel-active{z-index:2;animation:reel-rise var(--reel-step-ms,240ms) cubic-bezier(.16,.72,.22,1) both;box-shadow:0 8px 20px #0006}.vinyl{width:82%;height:82%;border-radius:50%;background:repeating-radial-gradient(circle,#111 0 3px,#242c35 3px 4px);display:grid;place-items:center;box-shadow:0 4px 16px #0009}.vinyl i{width:27%;height:27%;border-radius:50%;background:var(--primary-color,#55c6bc);border:4px solid #d4e8e5;box-sizing:border-box}
        .spinning{animation:disc-spin 1.15s linear infinite}.copy{min-width:0;min-height:166px;display:flex;flex-direction:column;justify-content:center}.eyebrow{text-transform:uppercase;letter-spacing:.15em;font-size:.72rem;font-weight:700;color:var(--primary-color,#55c6bc);margin:0 0 8px}.title{font-size:clamp(1.3rem,3.2vw,2rem);line-height:1.12;letter-spacing:-.025em;margin:0 0 7px;overflow-wrap:anywhere;min-height:2.24em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.artist{font-size:1.02rem;opacity:.88;margin:0;min-height:1.25em}.meta{font-size:.86rem;opacity:.7;margin:8px 0 0;line-height:1.5;min-height:1.3em}.reserved{visibility:hidden}.status{font-size:.83rem;opacity:.67;margin:12px 0 0;min-height:1.2em}.controls{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:14px;min-height:39px}button{border:0;border-radius:999px;padding:11px 17px;background:var(--primary-color,#55c6bc);color:var(--text-primary-color,#102126);font:600 .92rem/1.2 system-ui,sans-serif;cursor:pointer;transition:transform .16s ease,filter .16s ease}button:hover:not(:disabled){transform:translateY(-1px);filter:brightness(1.06)}button:disabled{opacity:.6;cursor:wait}.release-link{font-size:.82rem;color:var(--secondary-text-color,var(--primary-color,#55c6bc));text-decoration:none}.release-link:hover{text-decoration:underline}.error{color:var(--error-color,#ff8b8b);opacity:1}
        @keyframes disc-spin{to{transform:rotate(360deg)}}@keyframes reel-rise{0%{transform:translateY(78%) rotateX(-38deg) scale(.84);opacity:.28;filter:blur(1.5px) brightness(.7)}72%{transform:translateY(-3%) rotateX(3deg) scale(1.01);opacity:1;filter:blur(0) brightness(1)}100%{transform:translateY(0) rotateX(0) scale(1);opacity:1;filter:none}}
        @media(max-width:440px){ha-card,.layout{min-height:190px}.layout{grid-template-columns:92px minmax(0,1fr);gap:14px;padding:14px}.copy{min-height:160px}.title{font-size:1.2rem}.controls{margin-top:10px}}
        @media(prefers-reduced-motion:reduce){.spinning{animation-duration:4s}.reel-prev,.reel-next{display:none}.reel-active{animation:none}button{transition:none}}
      </style>
      <ha-card style="background-color:${backgroundColor}"><div class="layout"><div class="art">${cover}</div><div class="copy">
        <p class="eyebrow">${this._spinning ? "Vinyl roulette" : selected ? "Your next spin" : "Discogs collection"}</p>
        <h2 class="title">${this._spinning ? "Shuffling albums…" : selected ? this._escape(a.title || entity.state) : "Pick a random record"}</h2>
        <p class="artist ${selected && !this._spinning ? "" : "reserved"}">${selected && !this._spinning ? this._escape(a.artist || "Unknown artist") : "&nbsp;"}</p>
        <p class="meta ${selected && !this._spinning && yearParts.length ? "" : "reserved"}">${selected && !this._spinning && yearParts.length ? yearParts.join(" · ") : "&nbsp;"}</p>
        <p class="meta ${selected && !this._spinning && formats ? "" : "reserved"}">${selected && !this._spinning && formats ? this._escape(formats) : "&nbsp;"}</p>
        <p class="status ${messageOverride || a.status === "error" || a.status === "empty" ? "error" : ""}" aria-live="polite">${this._escape(status)}</p>
        <div class="controls"><button type="button" id="pick" ${this._spinning || this._animationFinished ? "disabled" : ""}>${this._spinning ? "Shuffling…" : selected ? "Spin again" : "Pick a record"}</button>${!this._spinning ? releaseLink : ""}</div>
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


