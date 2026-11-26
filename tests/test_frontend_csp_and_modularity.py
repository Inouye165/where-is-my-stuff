from pathlib import Path
import re
import shutil
import subprocess

import pytest


STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "static"
JS_DIR = STATIC_DIR / "js"


def test_html_pages_have_no_inline_script_blocks():
    inline_script_re = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
    for html_name in ("index.html", "setup.html", "capture.html"):
        html = (STATIC_DIR / html_name).read_text(encoding="utf-8")
        matches = inline_script_re.findall(html)
        assert not matches, f"{html_name} still contains inline <script> tag(s): {matches}"


def test_global_csp_headers_on_html_routes(client):
    for route in ("/capture.html", "/setup.html"):
        res = client.get(route)
        assert res.status_code == 200
        csp = res.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]
        assert "object-src 'none'" in csp
        assert "base-uri 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    home = client.get("/")
    assert home.status_code == 200
    home_csp = home.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in home_csp
    assert "object-src 'none'" in home_csp
    assert "base-uri 'self'" in home_csp
    assert "frame-ancestors 'none'" in home_csp


def test_new_frontend_modules_cached_in_sw_and_valid_syntax():
    sw_src = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    for asset in ("/js/theme-init.js", "/js/capture-page.js", "/js/events.js"):
        assert asset in sw_src, f"{asset} missing from sw.js APP_SHELL_URLS"
        assert (STATIC_DIR / asset.lstrip("/")).exists(), f"Missing file {asset}"

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    for js_name in ("theme-init.js", "capture-page.js", "events.js"):
        proc = subprocess.run([node_exe, "--check", str(JS_DIR / js_name)], capture_output=True, text=True)
        assert proc.returncode == 0, f"Syntax error in {js_name}:\n{proc.stderr}"


def test_delegated_event_dispatcher_behavior():
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_posix = JS_DIR.as_posix()
    script = f"""
    const listeners = {{}};
    const fakeDoc = {{
      addEventListener(type, fn) {{
        listeners[type] = fn;
      }}
    }};
    const eventsMod = await import('file://{js_posix}/events.js');
    let captured = null;
    eventsMod.registerActions({{
      openBox: (id, label) => {{ captured = {{ id, label }}; }},
    }});
    eventsMod.initDelegatedEvents(fakeDoc);

    const fakeBtn = {{
      dataset: {{ action: 'openBox', args: '[42, "Resistors"]' }},
      closest(sel) {{ return sel === '[data-action]' ? fakeBtn : null; }},
    }};
    await listeners.click({{
      target: fakeBtn,
      preventDefault() {{}},
    }});
    if (!captured || captured.id !== 42 || captured.label !== 'Resistors') {{
      throw new Error('Delegated click did not dispatch expected args: ' + JSON.stringify(captured));
    }}
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"Delegated dispatcher test failed:\n{proc.stderr}"
