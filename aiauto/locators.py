"""Intent-driven locator identification.

1. A JS snapshot (identical for Playwright and Selenium) lists the candidate elements on the
   page with their human-facing attributes (text, label, placeholder, aria-label, role ...)
   and, for each, a set of *unique* CSS/XPath locators ranked by robustness.
2. Candidates are scored against the step's natural-language target and its action
   (you type into inputs, select from <select>, check checkboxes ...).
3. If the top matches are close and Claude is available, Claude breaks the tie.
4. The winning element's best locator becomes the step's replayable locator; the others are
   stored as fallbacks for self-healing on the next run.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import Action, Locator, Step

SNAPSHOT_JS = r"""
return (function(mode){
  const INTERACTIVE = 'a,button,input,select,textarea,summary,label,option,[role],[onclick],[contenteditable=""],[contenteditable=true],[tabindex],[data-testid],[data-test],[data-qa],li,img,svg,h1,h2,h3,h4,h5,h6,span,div,p,td,th,nav *';
  const q = s => String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"');
  const xq = s => { s = String(s); if (s.indexOf('"') < 0) return '"'+s+'"'; if (s.indexOf("'") < 0) return "'"+s+"'";
                    return 'concat("' + s.split('"').join('",\'"\',"') + '")'; };
  const norm = s => (s||'').replace(/\s+/g,' ').trim();
  const visible = el => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none' && parseFloat(st.opacity||'1') > 0; };
  const countCss = s => { try { return document.querySelectorAll(s).length; } catch(e) { return 0; } };
  const countXp = s => { try { return document.evaluate('count('+s+')', document, null, XPathResult.NUMBER_TYPE, null).numberValue; } catch(e) { return 0; } };
  const looksDynamic = v => /\d{4,}|[a-f0-9]{8,}|^(ember|react|ng|mui|css)-?\d|:r\d|__/i.test(v||'');
  function labelOf(el){
    if (el.id) { const l = document.querySelector('label[for="'+q(el.id)+'"]'); if (l) return norm(l.innerText); }
    const p = el.closest('label'); if (p) return norm(p.innerText);
    const lb = el.getAttribute('aria-labelledby'); if (lb) { const t = lb.split(/\s+/).map(i=>document.getElementById(i)).filter(Boolean).map(e=>e.innerText).join(' '); if (t) return norm(t); }
    return '';
  }
  function cssPath(el){
    if (el.id && !looksDynamic(el.id) && countCss('#'+CSS.escape(el.id))===1) return '#'+CSS.escape(el.id);
    const parts = [];
    while (el && el.nodeType === 1 && el !== document.documentElement) {
      let part = el.tagName.toLowerCase();
      if (el.id && !looksDynamic(el.id)) { parts.unshift('#'+CSS.escape(el.id)); break; }
      const parent = el.parentElement;
      if (parent) { const same = Array.from(parent.children).filter(c=>c.tagName===el.tagName);
        if (same.length>1) part += ':nth-of-type('+(same.indexOf(el)+1)+')'; }
      parts.unshift(part); el = parent;
    }
    return parts.join(' > ');
  }
  function xpathPath(el){
    const parts=[];
    while (el && el.nodeType===1) { let i=1, s=el.previousElementSibling;
      while (s) { if (s.tagName===el.tagName) i++; s=s.previousElementSibling; }
      parts.unshift(el.tagName.toLowerCase()+'['+i+']'); el=el.parentElement; }
    return '/'+parts.join('/');
  }
  function locatorsFor(el, info){
    const out = []; const tag = info.tag;
    const addCss = (s, why) => { if (countCss(s)===1) out.push({strategy:'css', value:s, why}); };
    const addXp  = (s, why) => { if (countXp(s)===1) out.push({strategy:'xpath', value:s, why}); };
    for (const a of ['data-testid','data-test','data-qa','data-cy']) { const v = el.getAttribute(a); if (v) addCss('['+a+'="'+q(v)+'"]', a); }
    if (el.id && !looksDynamic(el.id)) addCss('#'+CSS.escape(el.id), 'id');
    if (info.name) addCss(tag+'[name="'+q(info.name)+'"]', 'name');
    if (info.aria_label) addCss(tag+'[aria-label="'+q(info.aria_label)+'"]', 'aria-label');
    if (info.placeholder) addCss(tag+'[placeholder="'+q(info.placeholder)+'"]', 'placeholder');
    if (info.label && el.id) addXp('//'+tag+'[@id=//label[normalize-space()='+xq(info.label)+']/@for]', 'label');
    if (info.text && info.text.length <= 80) {
      addXp('//'+tag+'[normalize-space()='+xq(info.text)+']', 'text');
      if (info.role) addXp('//*[@role='+xq(info.role)+' and normalize-space()='+xq(info.text)+']', 'role+text');
    }
    if (info.title) addCss(tag+'[title="'+q(info.title)+'"]', 'title');
    if (info.alt) addCss(tag+'[alt="'+q(info.alt)+'"]', 'alt');
    if (tag==='a' && info.href && info.href.length < 150) addCss('a[href="'+q(el.getAttribute('href'))+'"]', 'href');
    if (tag==='input' && info.type && ['submit','button','reset'].includes(info.type) && info.value) addCss('input[type="'+info.type+'"][value="'+q(info.value)+'"]', 'value');
    out.push({strategy:'css', value: cssPath(el), why:'css-path'});
    out.push({strategy:'xpath', value: xpathPath(el), why:'xpath-path'});
    return out;
  }
  const seen = new Set(); const res = [];
  for (const el of document.querySelectorAll(INTERACTIVE)) {
    if (seen.has(el) || res.length > 1500) continue; seen.add(el);
    const tag = el.tagName.toLowerCase();
    const vis = visible(el);
    if (!vis && !(tag==='input' && ['checkbox','radio','file'].includes((el.type||'')))) continue;
    let text = norm(el.innerText || el.textContent || '');
    // skip large containers whose text is mostly their children's
    if (['div','span','p','li','td','th','nav'].includes(tag) && (text.length > 120 || el.children.length > 3)) continue;
    if (['div','span','p','li','td','th'].includes(tag) && !text && !el.getAttribute('role') && !el.onclick) continue;
    const r = el.getBoundingClientRect();
    const info = {
      tag, text: text.slice(0, 200), id: el.id || '', name: el.getAttribute('name') || '',
      type: (el.getAttribute('type') || '').toLowerCase(), role: el.getAttribute('role') || '',
      aria_label: el.getAttribute('aria-label') || '', placeholder: el.getAttribute('placeholder') || '',
      title: el.getAttribute('title') || '', alt: el.getAttribute('alt') || '',
      value: (tag==='input' && ['submit','button','reset'].includes((el.type||''))) ? (el.value||'') : '',
      href: el.getAttribute('href') || '', label: labelOf(el), visible: vis,
      testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-qa') || '',
      editable: el.isContentEditable || false,
      options: tag==='select' ? Array.from(el.options).slice(0,30).map(o=>norm(o.text)) : [],
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    };
    info.locators = locatorsFor(el, info);
    res.push(info);
  }
  return res;
})();
"""

ROLE_WORDS = {
    "button": {"button", "input:submit", "input:button", "input:reset", "role:button", "a"},
    "btn": {"button", "input:submit", "input:button", "role:button"},
    "link": {"a", "role:link"},
    "field": {"input", "textarea", "role:textbox"},
    "textbox": {"input", "textarea", "role:textbox"},
    "input": {"input", "textarea"},
    "box": {"input", "textarea", "select"},
    "dropdown": {"select", "role:combobox", "role:listbox", "button"},
    "menu": {"a", "button", "role:menu", "role:menuitem", "li", "nav"},
    "checkbox": {"input:checkbox", "role:checkbox"},
    "radio": {"input:radio", "role:radio"},
    "tab": {"role:tab", "a", "button", "li"},
    "image": {"img", "svg"},
    "icon": {"svg", "img", "i", "span", "button", "a"},
    "heading": {"h1", "h2", "h3", "h4", "h5", "h6"},
    "title": {"h1", "h2", "h3"},
}

STOP = {"the", "a", "an", "on", "in", "into", "to", "of", "for", "with", "at", "field", "button", "link", "box",
        "textbox", "input", "dropdown", "menu", "checkbox", "radio", "tab", "icon", "image", "element", "option",
        "btn", "text", "area", "page", "heading", "title"}


def _kinds(c: dict) -> set[str]:
    k = {c["tag"]}
    if c.get("type"):
        k.add(f'{c["tag"]}:{c["type"]}')
    if c.get("role"):
        k.add(f'role:{c["role"]}')
    return k


def _tokens(s: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s or "")
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t]


def _sim(target: str, text: str) -> float:
    """0..1 similarity between the user's words and an element attribute."""
    if not target or not text:
        return 0.0
    t, x = target.lower().strip(), text.lower().strip()
    if t == x:
        return 1.0
    tt = [w for w in _tokens(t) if w not in STOP] or _tokens(t)
    xt = set(_tokens(x))
    if not tt or not xt:
        return 0.0
    overlap = sum(1 for w in tt if w in xt or any(w in y or y in w for y in xt if len(y) > 2 and len(w) > 2)) / len(tt)
    ratio = SequenceMatcher(None, " ".join(tt), x[:120]).ratio()
    contains = 0.15 if " ".join(tt) in x else 0.0
    # Penalise very long text that merely contains the words
    length_pen = min(0.3, max(0, len(x) - 3 * len(t)) / 400)
    return max(0.0, min(1.0, 0.6 * overlap + 0.3 * ratio + contains - length_pen))


def _action_fit(action: Action, c: dict) -> float:
    tag, typ, role = c["tag"], c.get("type", ""), c.get("role", "")
    is_text_input = (tag == "input" and typ not in ("checkbox", "radio", "submit", "button", "reset", "file", "hidden", "image")) \
        or tag == "textarea" or c.get("editable") or role in ("textbox", "searchbox", "combobox")
    clickable = tag in ("button", "a", "summary", "option", "label") or role in ("button", "link", "menuitem", "tab", "option", "checkbox", "radio") \
        or (tag == "input" and typ in ("submit", "button", "reset", "checkbox", "radio", "image"))
    if action in (Action.TYPE, Action.APPEND):
        return 1.0 if is_text_input else -1.0
    if action == Action.SELECT:
        return 1.0 if tag == "select" else (0.3 if role in ("combobox", "listbox") else -0.6)
    if action in (Action.CHECK, Action.UNCHECK):
        return 1.0 if (typ in ("checkbox", "radio") or role in ("checkbox", "radio", "switch")) else (0.2 if tag == "label" else -0.6)
    if action == Action.UPLOAD:
        return 1.0 if typ == "file" else -1.0
    if action in (Action.CLICK, Action.DOUBLE_CLICK, Action.RIGHT_CLICK):
        return 0.6 if clickable else (-0.2 if is_text_input else 0.0)
    if action == Action.HOVER:
        return 0.3 if clickable or tag in ("li", "nav") else 0.0
    return 0.0


def score(step: Step, c: dict) -> float:
    target = step.target or ""
    fields = {
        "testid": 1.0, "label": 1.0, "aria_label": 1.0, "placeholder": 0.95, "text": 0.95,
        "value": 0.9, "name": 0.8, "id": 0.75, "title": 0.8, "alt": 0.8,
    }
    best = 0.0
    for f, w in fields.items():
        v = c.get(f) or ""
        if f in ("name", "id", "testid"):
            v = v.replace("_", " ").replace("-", " ")
        best = max(best, w * _sim(target, v))
    s = best * 3.0
    s += _action_fit(step.action, c)
    # role words in the target ("Login *button*", "email *field*")
    kinds = _kinds(c)
    for w in _tokens(target):
        if w in ROLE_WORDS and kinds & ROLE_WORDS[w]:
            s += 0.4
    # select: the option the user wants exists in this dropdown
    if step.action == Action.SELECT and step.value and any(step.value.lower() == o.lower() for o in c.get("options", [])):
        s += 0.8
    if c.get("visible"):
        s += 0.1
    return round(s, 4)


def rank(step: Step, candidates: list[dict], top: int = 8) -> list[dict]:
    scored = []
    for c in candidates:
        c = dict(c)
        c["score"] = score(step, c)
        scored.append(c)
    scored.sort(key=lambda c: c["score"], reverse=True)
    # prefer the innermost element when a wrapper and its child tie (e.g. <li><a>Home</a></li>)
    return scored[:top]


def describe(c: dict) -> str:
    bits = [c["tag"]]
    for k in ("type", "role"):
        if c.get(k):
            bits.append(f"{k}={c[k]}")
    for k in ("text", "label", "aria_label", "placeholder", "name", "id", "value"):
        if c.get(k):
            bits.append(f'{k}="{c[k][:40]}"')
            break
    return " ".join(bits)


def choose(step: Step, candidates: list[dict], use_llm: bool = True) -> tuple[dict | None, list[dict]]:
    """Pick the element for this step. Returns (winner, ranked_candidates)."""
    ranked = rank(step, candidates)
    if not ranked or ranked[0]["score"] < 1.0:
        winner = None
    else:
        winner = ranked[0]
    ambiguous = len(ranked) > 1 and (winner is None or ranked[0]["score"] - ranked[1]["score"] < 0.35)
    if use_llm and ambiguous and ranked:
        from .nlp import llm
        if llm.available():
            idx = llm.pick_candidate(step, ranked)
            if idx is not None:
                winner = ranked[idx]
    return winner, ranked


def to_locators(c: dict) -> list[Locator]:
    desc = describe(c)
    return [Locator(strategy=l["strategy"], value=l["value"], score=c.get("score", 0), description=f'{desc} [{l["why"]}]')
            for l in c.get("locators", [])]
