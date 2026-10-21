"""Dry-run browser automation with Playwright.

DRY RUN ONLY. Submission is impossible by construction:
  * This module never clicks anything; it only fills fields and reads them back.
  * An init script cancels every form submit event, disables form.submit()/requestSubmit(), and
    swallows clicks on submit buttons, before any page script runs.
  * Every non-GET network request is aborted, and once the page has loaded the browser is taken
    fully offline before any field is touched, so nothing typed can leave it by any route
    (including GET query strings, beacons, or WebSockets).
Visible CAPTCHA challenges and access blocks pause the run; they are never bypassed. Invisible,
score-based CAPTCHAs (which only act on submit) are noted but don't block a dry run.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from backend.safety.field_classifier import FormField

log = logging.getLogger(__name__)

SUBMIT_GUARD = r"""
(() => {
  window.__applypilot = { blockedSubmits: 0 };
  const block = (e) => { e.preventDefault(); e.stopImmediatePropagation(); window.__applypilot.blockedSubmits++; };
  window.addEventListener('submit', block, true);
  window.addEventListener('click', (e) => {
    const t = e.target && e.target.closest && e.target.closest('button, input[type=submit], input[type=image]');
    if (t && (t.type === 'submit' || t.type === 'image' || (t.tagName === 'BUTTON' && !t.getAttribute('type') && t.form))) block(e);
  }, true);
  HTMLFormElement.prototype.submit = function () { window.__applypilot.blockedSubmits++; };
  HTMLFormElement.prototype.requestSubmit = function () { window.__applypilot.blockedSubmits++; };
})();
"""

EXTRACT_FIELDS = r"""
() => {
  const textOf = (el) => (el ? (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim() : '');
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const GENERIC = /^(attach|upload|choose file|browse|enter manually|select\.*|search)$/i;
  const labelFor = (el) => {
    const by = el.getAttribute('aria-labelledby');
    const fromBy = by ? by.split(/\s+/).map((id) => textOf(document.getElementById(id))).join(' ').trim() : '';
    if (fromBy && !GENERIC.test(fromBy)) return fromBy;
    if (el.labels && el.labels.length && !GENERIC.test(textOf(el.labels[0]))) return textOf(el.labels[0]);
    if (el.getAttribute('aria-label') && !GENERIC.test(el.getAttribute('aria-label'))) return el.getAttribute('aria-label');
    const wrap = el.closest('label');
    if (wrap && !GENERIC.test(textOf(wrap))) return textOf(wrap);
    const group = el.closest('[role=group][aria-labelledby], fieldset');
    if (group) {
      const g = group.getAttribute('aria-labelledby');
      const t = g ? textOf(document.getElementById(g)) : textOf(group.querySelector('legend'));
      if (t) return t;
    }
    // Nearest preceding label-like text in the same field wrapper (e.g. Greenhouse upload groups).
    let n = el;
    for (let i = 0; i < 5 && n; i++) {
      n = n.parentElement;
      // Stop before reaching a container that holds other fields: its label isn't ours.
      if (!n || n.querySelectorAll('input:not([type=hidden]), select, textarea').length > 1) break;
      const lab = n && n.querySelector(':scope > label, :scope > .label, :scope > [id$="-label"], :scope > h3, :scope > h4');
      if (lab && !GENERIC.test(textOf(lab)) && !lab.contains(el)) return textOf(lab);
    }
    return el.placeholder || el.name || el.id || '';
  };
  const fields = [];
  const seenGroups = new Set();
  let i = 0;
  for (const el of document.querySelectorAll('input, select, textarea, [role=combobox]')) {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role');
    const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search'].includes(type)) continue;
    // Skip hidden validation helpers, honeypots, and widget internals.
    if (el.closest('[aria-hidden=true]') || (el.tabIndex === -1 && type !== 'file' && !el.name && !el.id)) continue;
    if (type !== 'file' && !visible(el)) continue;
    if (role === 'combobox' && tag !== 'input' && el.querySelector('input[role=combobox]')) continue;
    const key = el.name || el.id || `field_${i++}`;
    if (type === 'radio' || type === 'checkbox') {
      const group = el.name ? [...document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`)] : [el];
      if (seenGroups.has(key)) continue;
      seenGroups.add(key);
      group.forEach((g) => g.setAttribute('data-applypilot-key', key));
      const fs = el.closest('fieldset');
      const question = fs && fs.querySelector('legend') ? textOf(fs.querySelector('legend')) : (group.length > 1 ? (el.getAttribute('aria-label') || key) : labelFor(el));
      const kind = type === 'checkbox' && group.length > 1 ? 'custom' : type;
      fields.push({ key, label: question, field_type: kind, required: group.some((g) => g.required),
                    options: type === 'radio' ? group.map(labelFor) : [], name: el.name || el.id || null });
      continue;
    }
    el.setAttribute('data-applypilot-key', key);
    const label = labelFor(el);
    let ftype = tag === 'select' ? 'select' : tag === 'textarea' ? 'textarea' : role === 'combobox' ? 'combobox' : type;
    if (!['text', 'email', 'tel', 'url', 'textarea', 'select', 'file', 'number', 'date', 'combobox'].includes(ftype)) ftype = 'text';
    if (role === 'combobox' && tag !== 'input') ftype = 'custom';
    fields.push({
      key, label, field_type: ftype,
      required: el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label),
      options: tag === 'select' ? [...el.options].filter((o) => o.value !== '').map((o) => o.text.trim()) : [],
      name: el.name || el.id || null,
    });
  }
  return fields;
}
"""

READ_VALUE = r"""
(key) => {
  const els = [...document.querySelectorAll(`[data-applypilot-key="${CSS.escape(key)}"]`)];
  if (!els.length) return null;
  const el = els[0];
  if (el.type === 'radio') { const c = els.find((e) => e.checked); return c ? (c.labels && c.labels[0] ? c.labels[0].innerText.trim() : c.value) : ''; }
  if (el.type === 'checkbox') return el.checked ? 'Yes' : '';
  if (el.type === 'file') return el.files && el.files[0] ? el.files[0].name : '';
  if (el.tagName === 'SELECT') return el.selectedOptions[0] ? el.selectedOptions[0].text.trim() : '';
  if (el.getAttribute('role') === 'combobox') {
    const control = el.closest('[class*=control]') || el.parentElement.parentElement;
    const single = control && control.querySelector('[class*=single-value], [class*=singleValue]');
    return single ? single.innerText.trim() : el.value;
  }
  return el.value;
}
"""

CAPTCHA_FRAME = ("recaptcha", "hcaptcha", "turnstile", "challenges.cloudflare")
BLOCK_PHRASES = ("verify you are human", "access denied", "are you a robot", "unusual traffic")


@dataclass
class FillResult:
    ok: bool
    actual: str | None
    error: str | None = None


@dataclass
class DryRunResult:
    url: str
    final_url: str | None = None
    http_status: int | None = None
    page_title: str = ""
    page_text: str = ""
    fields: list[FormField] = field(default_factory=list)
    form_frame: str | None = None
    fills: dict[str, FillResult] = field(default_factory=dict)
    pause_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    blocked_requests: list[str] = field(default_factory=list)
    blocked_submits: int = 0
    screenshot_path: str | None = None


def guarded_context(browser, blocked: list[str]):
    """A browser context where submitting a form or sending data is impossible."""
    context = browser.new_context(accept_downloads=False)
    context.add_init_script(SUBMIT_GUARD)

    def guard(route):
        if route.request.method not in ("GET", "HEAD", "OPTIONS"):
            blocked.append(f"{route.request.method} {route.request.url[:200]}")
            return route.abort()
        return route.continue_()

    context.route("**/*", guard)
    return context


PlanFn = Callable[[list[FormField], str], dict[str, str | None]]


def run_dry_run(url: str, plan: PlanFn, *, screenshot_path: Path, resume_path: Path | None,
                headless: bool = True, timeout_ms: int = 30000) -> DryRunResult:
    """Open the application page, extract fields, fill planned values, read them back. Never submits."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    result = DryRunResult(url=url)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = guarded_context(browser, result.blocked_requests)
        page = context.new_page()
        page.on("dialog", lambda d: d.dismiss())
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightError:
                pass
            result.final_url = page.url
            result.http_status = response.status if response else None
            result.page_title = page.title()

            # From here on nothing leaves the browser, whatever the page's scripts try.
            context.set_offline(True)

            frames = [f for f in page.frames if not any(c in f.url for c in CAPTCHA_FRAME)]
            texts = []
            for fr in frames:
                try:
                    texts.append(fr.inner_text("body"))
                except PlaywrightError:
                    pass
            result.page_text = "\n".join(texts)[:50000]

            if result.http_status and result.http_status >= 400:
                result.pause_reasons.append(f"Page returned HTTP {result.http_status}")
            low = result.page_text.lower()
            if any(p in low for p in BLOCK_PHRASES) or _visible_captcha(page):
                result.pause_reasons.append("CAPTCHA or bot check detected; ApplyPilot will not bypass it")
            elif any(c in f.url for f in page.frames for c in CAPTCHA_FRAME):
                result.notes.append("Page uses an invisible CAPTCHA that only runs on submit; a real submission may "
                                    "ask you to complete a challenge yourself")
            if result.pause_reasons:
                page.screenshot(path=str(screenshot_path), full_page=True)
                result.screenshot_path = str(screenshot_path)
                return result

            # The application form may live in an iframe (company career sites embedding Greenhouse).
            extracted = [(fr, fr.evaluate(EXTRACT_FIELDS)) for fr in frames]
            frame, raw = max(extracted, key=lambda x: len(x[1]))
            result.form_frame = None if frame == page.main_frame else frame.url[:200]
            result.fields = [FormField(**f) for f in raw]
            for f in result.fields:
                if f.field_type == "combobox":
                    options = _combobox_options(frame, f.key)
                    if options is None:
                        f.field_type = "custom"  # couldn't open it; the user fills it
                    else:
                        f.options = options
            if not result.fields:
                result.pause_reasons.append("No application form fields found on this page")
            values = plan(result.fields, result.page_text)

            for f in result.fields:
                value = values.get(f.key)
                if value in (None, ""):
                    continue
                loc = frame.locator(f'[data-applypilot-key="{f.key}"]')
                try:
                    if f.field_type == "file":
                        if resume_path is None or value != resume_path.name:
                            raise ValueError("Only the approved resume file can be attached")
                        # Many sites (Greenhouse included) upload a file the instant it's attached, so the
                        # dry run never attaches: it only records which approved file will be used.
                        result.fills[f.key] = FillResult(ok=True, actual=value, error=None)
                        continue
                    elif f.field_type == "select":
                        loc.first.select_option(label=value)
                    elif f.field_type == "combobox" and not f.options:
                        # Type-ahead: choosing a suggestion needs a live search, which the offline dry run
                        # can't do. The saved answer is recorded and entered at submission.
                        result.fills[f.key] = FillResult(ok=True, actual=value, error=None)
                        continue
                    elif f.field_type == "combobox":
                        _choose_option(frame, f.key, value)
                    elif f.field_type == "radio":
                        frame.get_by_label(value, exact=True).and_(loc).first.check()
                    elif f.field_type == "checkbox":
                        if value.lower() in ("yes", "true", "checked"):
                            loc.first.check()
                    else:
                        loc.first.fill(value)
                    actual = frame.evaluate(READ_VALUE, f.key)
                    if f.field_type == "combobox" and not _same(actual, value):
                        actual = _selected_option(frame, f.key) or actual  # display may be abbreviated ("+1")
                    ok = _same(actual, value)
                    result.fills[f.key] = FillResult(ok=ok, actual=actual, error=None if ok else "Value didn't stick")
                except (PlaywrightError, ValueError) as exc:
                    result.fills[f.key] = FillResult(ok=False, actual=None, error=str(exc).splitlines()[0][:200])
                    page.keyboard.press("Escape")

            result.blocked_submits = sum(
                fr.evaluate("window.__applypilot ? window.__applypilot.blockedSubmits : 0") for fr in frames)
            page.screenshot(path=str(screenshot_path), full_page=True)
            result.screenshot_path = str(screenshot_path)
        except PlaywrightError as exc:
            result.pause_reasons.append(f"Browser error: {str(exc).splitlines()[0][:200]}")
        finally:
            context.close()
            browser.close()
    return result


def _visible_captcha(page) -> bool:
    """A challenge the user would have to solve: a visible widget that isn't the invisible kind."""
    for fr in page.frames:
        if not any(c in fr.url for c in CAPTCHA_FRAME) or "size=invisible" in fr.url:
            continue
        try:
            box = fr.frame_element().bounding_box()
        except Exception:
            continue
        if box and box["width"] > 100 and box["height"] > 60:
            return True
    return False


def _open_dropdown(frame, key: str) -> None:
    """Open a dropdown with the keyboard (focus + ArrowDown), which works even inside iframes that keep
    resizing and so never count as "stable" for a mouse click. Falls back to a click."""
    loc = frame.locator(f'[data-applypilot-key="{key}"]').first
    loc.focus(timeout=3000)
    frame.page.keyboard.press("ArrowDown")
    frame.wait_for_timeout(100)
    if loc.get_attribute("aria-expanded") != "true":
        loc.click(timeout=3000, force=True)


def _choose_option(frame, key: str, value: str) -> None:
    """Select an option by keyboard: type to filter, arrow to the exact match, Enter. Mouse clicks on
    options don't register reliably in some embedded forms."""
    _open_dropdown(frame, key)
    frame.page.keyboard.type(value)
    frame.wait_for_timeout(200)
    shown = frame.evaluate("""() => [...document.querySelectorAll('[role=option]')]
        .filter(o => o.offsetWidth || o.offsetHeight).map(o => (o.innerText || '').replace(/\\s+/g, ' ').trim())""")
    norm = " ".join(value.split())
    if norm not in shown:
        frame.page.keyboard.press("Escape")
        raise ValueError(f"Option '{value}' isn't offered")
    for _ in range(shown.index(norm)):
        frame.page.keyboard.press("ArrowDown")
    frame.page.keyboard.press("Enter")
    frame.wait_for_timeout(150)


def _selected_option(frame, key: str) -> str | None:
    """Reopen a dropdown and read which option it marks as selected (exact, unlike its display text)."""
    from playwright.sync_api import Error as PlaywrightError

    try:
        _open_dropdown(frame, key)
        frame.wait_for_timeout(150)
        selected = frame.evaluate("""() => {
            const o = [...document.querySelectorAll('[role=option]')].find(o =>
                o.getAttribute('aria-selected') === 'true' || /is-selected/.test(o.className));
            return o ? (o.innerText || '').replace(/\\s+/g, ' ').trim() : null;
        }""")
        frame.page.keyboard.press("Escape")
        return selected
    except PlaywrightError:
        return None


def _combobox_options(frame, key: str) -> list[str] | None:
    """Open a dropdown widget just long enough to read its options.

    Returns [] for type-ahead lists (no options until the user searches) and None if the widget
    couldn't be opened at all.
    """
    from playwright.sync_api import Error as PlaywrightError

    read = """(key) => {
        const el = document.querySelector(`[data-applypilot-key="${CSS.escape(key)}"]`);
        const own = el && (el.getAttribute('aria-controls') || el.getAttribute('aria-owns'));
        const scope = (own && document.getElementById(own)) || document;
        const shown = (o) => !!(o.offsetWidth || o.offsetHeight || o.getClientRects().length);
        return [...scope.querySelectorAll('[role=option]')].filter(shown)
                 .map(o => (o.innerText || '').trim()).filter(Boolean);
    }"""
    try:
        _open_dropdown(frame, key)
        options: list[str] = []
        for _ in range(8):  # options can render a moment after the menu opens
            frame.wait_for_timeout(125)
            options = frame.evaluate(read, key)
            if options:
                break
        frame.page.keyboard.press("Escape")
        return list(dict.fromkeys(options))[:200]
    except PlaywrightError:
        return None


def _same(actual: str | None, wanted: str) -> bool:
    return actual is not None and " ".join(actual.split()) == " ".join(wanted.split())
