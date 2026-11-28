from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess

import pytest


STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "static"
JS_DIR = STATIC_DIR / "js"


class _ModalAttributeInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.all_ids: set[str] = set()
        self.modals: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v or "") for k, v in attrs}
        el_id = attr_map.get("id")
        if el_id:
            self.all_ids.add(el_id)
        classes = attr_map.get("class", "").split()
        if "modal-scrim" in classes:
            self.modals.append(attr_map)


def test_index_html_modal_aria_skip_link_and_live_regions():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    inspector = _ModalAttributeInspector()
    inspector.feed(html)

    assert '<a href="#app-workspace" class="skip-link' in html
    assert '<main id="app-workspace" class="app-workspace is-home-canvas" tabindex="-1">' in html
    assert 'id="toast-shelf"' in html
    assert 'role="status" aria-live="polite"' in html
    assert 'id="app-confirm-modal"' in html
    assert 'id="lightbox-modal" class="lightbox-overlay no-print" role="dialog" aria-modal="true"' in html
    assert 'class="lightbox-close-btn"' in html
    assert len(inspector.modals) >= 20

    for modal in inspector.modals:
        modal_id = modal.get("id", "<unknown>")
        assert modal.get("role") in {"dialog", "alertdialog"}, f"{modal_id} missing dialog role"
        assert modal.get("aria-modal") == "true", f"{modal_id} missing aria-modal='true'"
        labelled_by = modal.get("aria-labelledby") or modal.get("aria-label")
        assert labelled_by, f"{modal_id} missing aria-labelledby / aria-label"
        if modal.get("aria-labelledby"):
            assert modal["aria-labelledby"] in inspector.all_ids, (
                f"{modal_id} aria-labelledby='{modal['aria-labelledby']}' points to missing element id"
            )

    capture_html = (STATIC_DIR / "capture.html").read_text(encoding="utf-8")
    assert '<main class="capture-card">' in capture_html
    assert ":focus-visible" in capture_html
    assert "prefers-reduced-motion: reduce" in capture_html


def test_no_missing_img_alt_or_blocking_confirm_calls():
    for path in sorted(list(STATIC_DIR.glob("*.html")) + list(JS_DIR.glob("*.js"))):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<img\b[^>]*>", text, re.IGNORECASE | re.DOTALL):
            assert re.search(r"\balt\s*=", match.group(0), re.IGNORECASE), (
                f"{path.name} has <img> without alt attribute: {match.group(0)[:80]}"
            )

    confirm_re = re.compile(r"\b(?:window\.)?confirm\s*\(")
    for js_name in ("modals.js", "views.js"):
        src = (JS_DIR / js_name).read_text(encoding="utf-8")
        assert not confirm_re.search(src), f"{js_name} still calls blocking window.confirm()"


def test_wcag_css_focus_rules_and_sw_shell_registration():
    app_css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
    assert ":focus-visible" in app_css
    assert ".skip-link" in app_css
    assert ".sr-only" in app_css
    assert "prefers-reduced-motion: reduce" in app_css

    unit_css = (STATIC_DIR / "css" / "unit-setup.css").read_text(encoding="utf-8")
    assert ":focus-visible" in unit_css
    assert "prefers-reduced-motion: reduce" in unit_css
    setup_input_block = re.search(r"\.setup-input:focus\s*\{[^}]*\}", unit_css, re.DOTALL)
    assert setup_input_block and "outline: none" not in setup_input_block.group(0)

    sw_src = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    assert "'/js/a11y.js'" in sw_src


def test_a11y_focus_trap_and_accessible_confirm_modal_behavioral():
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    a11y_url = (JS_DIR / "a11y.js").as_uri()
    dom_url = (JS_DIR / "dom.js").as_uri()
    script = f"""
import assert from 'node:assert/strict';

function makeEl(id, {{ tag = 'div', classes = [], text = '' }} = {{}}) {{
  const classSet = new Set(classes);
  const attrs = {{}};
  const listeners = {{}};
  const el = {{
    id,
    tagName: tag.toUpperCase(),
    textContent: text,
    style: {{}},
    dataset: {{}},
    hidden: false,
    disabled: false,
    children: [],
    parent: null,
    isConnected: true,
    focusCalls: 0,
    setAttribute(k, v) {{ attrs[k] = String(v); }},
    getAttribute(k) {{ return k in attrs ? attrs[k] : null; }},
    addEventListener(type, fn) {{ (listeners[type] ||= []).push(fn); }},
    contains(target) {{
      if (target === el) return true;
      return el.children.some(c => c.contains(target));
    }},
    closest(sel) {{
      let cur = el;
      while (cur) {{
        if (sel === '[hidden]' && cur.hidden) return cur;
        cur = cur.parent;
      }}
      return null;
    }},
    querySelector(sel) {{
      return el.querySelectorAll(sel)[0] || null;
    }},
    querySelectorAll(sel) {{
      const matches = [];
      const selectors = sel.split(',').map(s => s.trim());
      function matchOne(node, s) {{
        if (s === '.modal-card') return node.classList.contains('modal-card');
        if (s === '.modal-title') return node.classList.contains('modal-title');
        if (s === '[autofocus]') return node.getAttribute('autofocus') !== null;
        if (s.startsWith('button')) return node.tagName === 'BUTTON' && !node.disabled;
        return false;
      }}
      function walk(node) {{
        for (const c of node.children) {{
          if (selectors.some(s => matchOne(c, s))) matches.push(c);
          walk(c);
        }}
      }}
      walk(el);
      return matches;
    }},
    focus() {{
      el.focusCalls += 1;
      fakeDoc.activeElement = el;
    }},
    classList: {{
      add: (...cs) => cs.forEach(c => classSet.add(c)),
      remove: (...cs) => cs.forEach(c => classSet.delete(c)),
      toggle: (c, f) => (f ? classSet.add(c) : classSet.delete(c)),
      contains: (c) => classSet.has(c),
    }},
    appendChild(child) {{
      child.parent = el;
      el.children.push(child);
      return child;
    }},
  }};
  return el;
}}

const openerBtn = makeEl('opener-btn', {{ tag: 'button', text: 'Delete Item' }});
const modal = makeEl('app-confirm-modal', {{ classes: ['modal-scrim'] }});
const titleEl = makeEl('app-confirm-title', {{ tag: 'h3', classes: ['modal-title'] }});
const msgEl = makeEl('app-confirm-msg', {{ tag: 'p' }});
const cancelBtn = makeEl('app-confirm-cancel-btn', {{ tag: 'button', text: 'Cancel' }});
const okBtn = makeEl('app-confirm-ok-btn', {{ tag: 'button', text: 'Delete' }});
modal.appendChild(titleEl);
modal.appendChild(msgEl);
modal.appendChild(cancelBtn);
modal.appendChild(okBtn);

const docListeners = {{}};
const elMap = new Map([
  ['opener-btn', openerBtn],
  ['app-confirm-modal', modal],
  ['app-confirm-title', titleEl],
  ['app-confirm-msg', msgEl],
  ['app-confirm-cancel-btn', cancelBtn],
  ['app-confirm-ok-btn', okBtn],
]);

const fakeDoc = {{
  activeElement: openerBtn,
  getElementById(id) {{ return elMap.get(id) || null; }},
  querySelectorAll(sel) {{
    if (sel.includes('.modal-scrim')) return [modal];
    return [];
  }},
  addEventListener(type, fn) {{ (docListeners[type] ||= []).push(fn); }},
  createElement(tag) {{ return makeEl('dyn', {{ tag }}); }},
}};

globalThis.window = {{ confirm: () => true }};
globalThis.document = fakeDoc;

const a11y = await import({a11y_url!r});
const dom = await import({dom_url!r});

a11y.initDialogA11y(fakeDoc);
assert.equal(modal.getAttribute('role'), 'dialog');
assert.equal(modal.getAttribute('aria-modal'), 'true');
assert.equal(modal.getAttribute('aria-labelledby'), 'app-confirm-title');

// Open confirmAction -> Promise resolves true on confirm
const p1 = dom.confirmAction('Delete screw?', {{ title: 'Delete item?', confirmText: 'Delete' }});
assert.equal(modal.classList.contains('open'), true);
assert.equal(titleEl.textContent, 'Delete item?');
assert.equal(msgEl.textContent, 'Delete screw?');
assert.equal(okBtn.classList.contains('btn-danger'), true);

a11y.activateDialogFocus(modal, fakeDoc);
assert.equal(fakeDoc.activeElement, cancelBtn);

// Tab wraps from last (okBtn) -> first (cancelBtn), Shift+Tab wraps first -> last
okBtn.focus();
let preventedForward = false;
a11y.handleDialogFocusTrapKeydown({{ key: 'Tab', shiftKey: false, preventDefault() {{ preventedForward = true; }} }}, fakeDoc);
assert.equal(preventedForward, true);
assert.equal(fakeDoc.activeElement, cancelBtn);

let preventedBack = false;
a11y.handleDialogFocusTrapKeydown({{ key: 'Tab', shiftKey: true, preventDefault() {{ preventedBack = true; }} }}, fakeDoc);
assert.equal(preventedBack, true);
assert.equal(fakeDoc.activeElement, okBtn);

dom.resolveConfirmModal(true);
assert.equal(await p1, true);
assert.equal(modal.classList.contains('open'), false);
a11y.deactivateDialogFocus(modal, fakeDoc);
assert.equal(fakeDoc.activeElement, openerBtn);

// Scrim click cancels cleanly; superseded prompt resolves false
const pFirst = dom.confirmAction('First?');
const pSecond = dom.confirmAction('Second?');
assert.equal(await pFirst, false);
dom.closeConfirmOnScrim({{ target: {{ id: 'app-confirm-modal' }} }});
assert.equal(await pSecond, false);

// Missing modal falls back to window.confirm safely
elMap.delete('app-confirm-modal');
assert.equal(await dom.confirmAction('Fallback?'), true);

process.exit(0);
"""
    proc = subprocess.run(  # noqa: S603
        [node_exe, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, f"Phase 4 Node behavioral check failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
