import { escapeHtml, escapeJsStr } from './dom.js';

export function syncSlotCarousel(track) {
  if (!track) return;
  const wrap = track.closest('.cgrid-slot-carousel-wrap');
  if (!wrap) return;
  const maxScroll = Math.max(0, track.scrollWidth - track.clientWidth);
  const overflowing = maxScroll > 2;
  wrap.classList.toggle('is-overflowing', overflowing);
  wrap.classList.toggle('can-prev', overflowing && track.scrollLeft > 2);
  wrap.classList.toggle('can-next', overflowing && track.scrollLeft < maxScroll - 2);
}

export function syncAllSlotCarousels() {
  document.querySelectorAll('.cgrid-slot-carousel').forEach(syncSlotCarousel);
}

export function scrollSlotCarousel(btn, dir) {
  const wrap = btn?.closest('.cgrid-slot-carousel-wrap');
  const track = wrap?.querySelector('.cgrid-slot-carousel');
  if (!track) return;
  const step = Math.max(180, Math.round(track.clientWidth * 0.65));
  track.scrollBy({ left: dir * step, behavior: 'smooth' });
}

if (typeof window !== 'undefined') {
  window.addEventListener('resize', syncAllSlotCarousels);
}

export function renderSlotCarouselHtml(photos, options = {}) {
  if (!Array.isArray(photos) || photos.length === 0) return '';
  const extraClass = options?.className ? ` ${escapeHtml(String(options.className).trim())}` : '';
  const trailingAdd = options?.trailingAddAction && options.trailingAddAction.onclick
    ? `
      <button
        type="button"
        class="cgrid-slot-thumb cgrid-slot-add-tile"
        title="${escapeHtml(options.trailingAddAction.label || 'Add photo')}"
        aria-label="${escapeHtml(options.trailingAddAction.label || 'Add photo')}"
        onclick="event.stopPropagation(); ${options.trailingAddAction.onclick}"
      >+</button>
    `
    : '';
  return `
    <div class="cgrid-slot-carousel-wrap${extraClass}" onclick="event.stopPropagation()">
      <button
        type="button"
        class="cgrid-carousel-arrow prev"
        aria-label="Scroll carousel left"
        onclick="event.stopPropagation(); scrollSlotCarousel(this, -1)"
      >‹</button>
      <div
        class="cgrid-slot-carousel"
        onscroll="syncSlotCarousel(this)"
        onwheel="if (Math.abs(event.deltaY) > Math.abs(event.deltaX) && this.scrollWidth > this.clientWidth + 2) { event.preventDefault(); this.scrollLeft += event.deltaY; }"
      >
        ${photos.map(p => {
          const safeNavId = Number(p.navId) || 0;
          const label = p.label || p.caption || 'Photo';
          const clickAction = p.navType === 'overall'
            ? `openOverallPhotoViewer(${Number(p.locationId) || safeNavId}, ${Number(p.overallIndex) || 0})`
            : p.navType === 'item' && safeNavId
              ? `navToItem(${safeNavId})`
              : (safeNavId
                ? `navToDrawer(${safeNavId}, true)`
                : `openLightbox('${escapeJsStr(p.url)}', '${escapeJsStr(label)}')`);
          return `
            <button
              type="button"
              class="cgrid-slot-thumb"
              title="${escapeHtml(label)}"
              aria-label="${escapeHtml(label)}"
              onclick="event.stopPropagation(); ${clickAction}"
            >
              <img src="${escapeHtml(p.thumb)}" alt="${escapeHtml(label)}" loading="lazy">
            </button>
          `;
        }).join('')}
        ${trailingAdd}
      </div>
      <button
        type="button"
        class="cgrid-carousel-arrow next"
        aria-label="Scroll carousel right"
        onclick="event.stopPropagation(); scrollSlotCarousel(this, 1)"
      >›</button>
    </div>
  `;
}
