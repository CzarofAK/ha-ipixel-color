/**
 * iPIXEL Card Editor
 * Shared configuration editor for all card types
 */

export class iPIXELSimpleEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  setConfig(config) {
    this._config = config;
    this.render();
  }

  set hass(hass) {
    this._hass = hass;
    this.render();
  }

  render() {
    if (!this._hass) return;
    const entities = Object.keys(this._hass.states)
      .filter(e => e.startsWith('text.') || e.startsWith('switch.'))
      .sort();

    this.shadowRoot.innerHTML = `
      <style>
        .row { margin-bottom: 12px; }
        label { display: block; margin-bottom: 4px; font-weight: 500; font-size: 0.9em; }
        select, input {
          width: 100%;
          padding: 8px;
          border: 1px solid var(--divider-color, #ccc);
          border-radius: 4px;
          background: var(--card-background-color);
          color: inherit;
          box-sizing: border-box;
        }
      </style>
      <div class="row">
        <label>Entity</label>
        <select id="entity">
          <option value="">Select entity</option>
          ${entities.map(e => `
            <option value="${e}" ${this._config?.entity === e ? 'selected' : ''}>
              ${this._hass.states[e]?.attributes?.friendly_name || e}
            </option>
          `).join('')}
        </select>
      </div>
      <div class="row">
        <label>Name (optional)</label>
        <input type="text" id="name" value="${this._config?.name || ''}" placeholder="Display name">
      </div>`;

    this.shadowRoot.querySelectorAll('select, input').forEach(el => {
      el.addEventListener('change', () => this.fireConfig());
    });
  }

  fireConfig() {
    this.dispatchEvent(new CustomEvent('config-changed', {
      detail: {
        config: {
          type: this._config?.type || 'custom:ipixel-display-card',
          entity: this.shadowRoot.getElementById('entity')?.value,
          name: this.shadowRoot.getElementById('name')?.value || undefined,
        }
      },
      bubbles: true,
      composed: true,
    }));
  }
}
