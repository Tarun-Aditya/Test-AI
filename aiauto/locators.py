"""Intent-driven, engine-native locator identification.

1. SNAPSHOT_JS (identical for Playwright and Selenium) walks the page, including open shadow roots
   (any depth) and same-origin iframes (any depth). For every candidate element it records the
   human-facing attributes (text, ARIA role + accessible name, label, placeholder, alt, title ...),
   where it lives (iframe chain + shadow-host chain) and a list of *semantic* locator candidates
   ordered by robustness: test-id > stable id > role+name > label > placeholder > name > text >
   alt/title > attribute CSS > structural CSS > XPath. Auto-generated / dynamic ids and classes are
   ignored so the locators survive re-renders.
2. Python scores the candidates against the step's natural-language target and its action.
   Ordinals ("first result containing X") pick the nth match in document order.
3. Claude breaks ties when the top two candidates score close (if configured).
4. The runner then asks the *engine itself* to verify each locator natively (Playwright get_by_*,
   Selenium By.*): it must match exactly one element and that element must be the chosen one. The
   first verified locator becomes the step's locator; the next ones are stored as fallbacks.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import Action, Locator, Step

# Shared JavaScript helpers (used by the page snapshot and by the per-element locator builder).
JS_LIB = r"""
  const q = s => String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"');
  const norm = s => (s||'').replace(/\s+/g,' ').trim();
  // auto-generated ids/classes: long digit runs, hashes, framework prefixes, random letter+digit segments
  const dyn = v => !v || /\d{5,}|[a-f0-9]{10,}|^(ember|react|ng|mui|css|jss|sc|svelte|vue|uid|gen|auto)[-_:]?\w*\d|:r\w*:|__|^[0-9]/i.test(v)
    || String(v).split(/[-_:.]/).some(seg => seg.length >= 5 && /\d/.test(seg) && /[a-z]/i.test(seg) && !/^[a-z]+\d{1,2}$/i.test(seg));
  const TEXTY = new Set(['button','a','label','summary','option','h1','h2','h3','h4','h5','h6','li','td','th','span','div','p','strong','b','em','legend','caption','dt','dd','figcaption']);
  const FORM = new Set(['input','select','textarea']);
  function implicitRole(el){
    const t = el.tagName.toLowerCase(), ty = (el.getAttribute('type')||'text').toLowerCase();
    if (t==='button') return 'button';
    if (t==='a' && el.hasAttribute('href')) return 'link';
    if (t==='select') return (el.multiple || el.size>1) ? 'listbox' : 'combobox';
    if (t==='textarea') return 'textbox';
    if (t==='input') {
      if (['button','submit','reset','image'].includes(ty)) return 'button';
      if (ty==='checkbox') return 'checkbox'; if (ty==='radio') return 'radio';
      if (ty==='range') return 'slider'; if (ty==='number') return 'spinbutton';
      if (ty==='search') return 'searchbox';
      if (['text','email','tel','url','password',''].includes(ty)) return ty==='password' ? '' : 'textbox';
      return '';
    }
    if (/^h[1-6]$/.test(t)) return 'heading';
    if (t==='img' && el.getAttribute('alt')) return 'img';
    if (t==='option') return 'option';
    if (t==='li' && el.parentElement && /^(ul|ol)$/i.test(el.parentElement.tagName)) return 'listitem';
    return '';
  }
  const LABELS = new Map();   // root -> {for-id: label text}, built once per document / shadow root
  function labelFor(root, id){
    let m = LABELS.get(root);
    if (!m) { m = {}; try { for (const l of root.querySelectorAll('label[for]')) { const k = l.getAttribute('for'); if (!(k in m)) m[k] = norm(l.innerText||l.textContent); } } catch(e) {} LABELS.set(root, m); }
    return m[id];
  }
  function labelOf(el){
    const root = el.getRootNode();
    if (el.id) { const t = labelFor(root, el.id); if (t) return t; }
    const p = el.closest('label'); if (p) return norm(p.innerText||p.textContent);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(i=>root.getElementById ? root.getElementById(i) : document.getElementById(i)).filter(Boolean).map(e=>e.innerText||e.textContent).join(' '); if (norm(t)) return norm(t); }
    // a label laid out beside the field but not linked to it (grid forms: <div col><label>Email</label></div><div col><input></div>)
    if (FORM.has(el.tagName.toLowerCase())) {
      let p = el;
      for (let k = 0; k < 3 && p; k++, p = p.parentElement) {
        const prev = p.previousElementSibling;
        if (prev && /^(label|span|div|td|th|dt|p|strong|b|legend)$/i.test(prev.tagName) && !prev.querySelector('input,select,textarea,button')) {
          const t = norm(prev.innerText || prev.textContent);
          if (t && t.length <= 40) return t;
        }
        if (p.parentElement && p.parentElement.querySelectorAll('input,select,textarea').length > 1) break;   // a shared container: stop
      }
    }
    return '';
  }
  function accName(el, info){
    if (info.aria_label) return info.aria_label;
    if (FORM.has(info.tag) && info.label) return info.label;
    if (info.tag==='img') return info.alt;
    if (info.tag==='input' && ['button','submit','reset'].includes(info.type)) return info.value || '';
    if (['button','link','heading','tab','menuitem','option','listitem','checkbox','radio','switch','treeitem','cell'].includes(info.role) || TEXTY.has(info.tag))
      return info.text || info.title || '';
    return info.title || '';
  }
  function cssPathIn(el){
    const root = el.getRootNode();
    const count = s => { try { return root.querySelectorAll(s).length; } catch(e) { return 0; } };
    if (el.id && !dyn(el.id) && count('#'+CSS.escape(el.id))===1) return '#'+CSS.escape(el.id);
    // one stable class that only this element has ("btn-primary" among three look-alike buttons) beats a position
    const tg = el.tagName.toLowerCase();
    for (const cl of Array.from(el.classList||[])) {
      if (dyn(cl) || cl.length >= 40) continue;
      const sel = tg + '.' + CSS.escape(cl);
      if (count(sel)===1) return sel;
    }
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType===1) {
      let part = cur.tagName.toLowerCase();
      if (cur.id && !dyn(cur.id)) { parts.unshift('#'+CSS.escape(cur.id)); break; }
      const stable = Array.from(cur.classList||[]).filter(c=>!dyn(c) && c.length<40).slice(0,2);
      if (stable.length) part += '.'+stable.map(c=>CSS.escape(c)).join('.');
      const parent = cur.parentElement;
      if (parent) { const same = Array.from(parent.children).filter(c=>c.tagName===cur.tagName);
        if (same.length>1) part += ':nth-of-type('+(same.indexOf(cur)+1)+')'; }
      parts.unshift(part);
      if (count(parts.join(' > '))===1) break;
      cur = parent;
    }
    return parts.join(' > ');
  }
  function xpathPath(el){
    const parts=[];
    for (let cur=el; cur && cur.nodeType===1; cur=cur.parentElement) {
      let i=1; for (let s=cur.previousElementSibling; s; s=s.previousElementSibling) if (s.tagName===cur.tagName) i++;
      parts.unshift(cur.tagName.toLowerCase()+'['+i+']');
    }
    return '/'+parts.join('/');
  }
  function visible(el){
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    const st = (el.ownerDocument.defaultView||window).getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && parseFloat(st.opacity||'1') > 0.05;
  }
  function info(el){
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type')||'').toLowerCase();
    const text = norm(el.innerText || el.textContent || '');
    const i = {
      tag, type, text: text.slice(0,200), id: el.id||'', nameattr: el.getAttribute('name')||'',
      aria_label: norm(el.getAttribute('aria-label')), placeholder: el.getAttribute('placeholder')||'',
      title: el.getAttribute('title')||'', alt: el.getAttribute('alt')||'',
      value: (tag==='input' && ['submit','button','reset'].includes(type)) ? (el.value||'') : '',
      href: el.getAttribute('href')||'', label: labelOf(el),
      testid: el.getAttribute('data-testid')||el.getAttribute('data-test')||el.getAttribute('data-qa')||'',
      editable: !!el.isContentEditable, role: el.getAttribute('role') || implicitRole(el),
      draggable: el.getAttribute('draggable')==='true',
      cls: Array.from(el.classList||[]).filter(c => !dyn(c) && c.length < 40).slice(0, 8).join(' '),
      options: tag==='select' ? Array.from(el.options).slice(0,50).map(o=>norm(o.text)) : [],
      longtext: text.length > 300,
    };
    i.name = norm(accName(el, i));
    return i;
  }
  // Unique locators for ONE element, most robust first (built only for the element a step picked).
  function locatorsFor(el, i, inShadow){
    const c = []; const tag = i.tag;
    const add = (strategy, value, extra) => { if (value && String(value).length <= 120) c.push(Object.assign({strategy, value:String(value)}, extra||{})); };
    if (el.getAttribute('data-testid')) add('testid', el.getAttribute('data-testid'));
    for (const a of ['data-test','data-qa','data-cy','data-test-id']) if (el.getAttribute(a)) add('css', '['+a+'="'+q(el.getAttribute(a))+'"]');
    if (el.id && !dyn(el.id)) add('id', el.id);
    if (i.role && i.name && i.name.length <= 80) add('role', i.name, {role: i.role});
    if (FORM.has(tag) && i.label) add('label', i.label);
    if (i.placeholder) add('placeholder', i.placeholder);
    if (i.nameattr && !dyn(i.nameattr)) add('name', i.nameattr);
    if (i.text && i.text.length <= 80 && !FORM.has(tag)) add('text', i.text);
    if (i.alt) add('alt', i.alt);
    if (i.title) add('title', i.title);
    if (i.aria_label) add('css', tag+'[aria-label="'+q(i.aria_label)+'"]');
    if (tag==='input' && i.value && ['submit','button','reset'].includes(i.type)) add('css', 'input[type="'+i.type+'"][value="'+q(i.value)+'"]');
    if (tag==='a' && i.href && i.href.length < 100 && !/[?&]|\d{5,}/.test(i.href)) add('css', 'a[href="'+q(el.getAttribute('href'))+'"]');
    // single classes only this element has (which one is stable is decided later, against the step's words)
    { const root = el.getRootNode(); let n = 0;
      for (const cl of Array.from(el.classList||[])) {
        if (n >= 4 || dyn(cl) || cl.length >= 40) continue;
        const sel = tag + '.' + CSS.escape(cl);
        try { if (root.querySelectorAll(sel).length === 1) { add('css', sel); n++; } } catch(e) {}
      } }
    add('css', cssPathIn(el));
    if (!inShadow) add('xpath', xpathPath(el));
    return c;
  }
  // every match of a selector in a document, through open shadow roots too (what Playwright's CSS sees)
  function deepAll(root, sel, out){
    out = out || [];
    try { root.querySelectorAll(sel).forEach(e => out.push(e)); } catch(e) {}
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) deepAll(e.shadowRoot, sel, out); });
    return out;
  }
  // a selector for an <iframe> that matches only it: its id, name, title or src when one of them is unique, else its position
  function frameSel(f){
    const doc = f.ownerDocument, cands = [];
    if (f.id && !dyn(f.id)) cands.push('#' + CSS.escape(f.id));
    const nm = f.getAttribute('name'); if (nm && !dyn(nm)) cands.push('iframe[name="' + q(nm) + '"]');
    const ti = f.getAttribute('title'); if (ti) cands.push('iframe[title="' + q(ti) + '"]');
    const sr = f.getAttribute('src'); if (sr && sr.length < 100) cands.push('iframe[src="' + q(sr) + '"]');
    for (const c of cands) { try { if (deepAll(doc, c).length === 1) return c; } catch(e) {} }
    return cssPathIn(f);
  }
  // Where an element lives: iframe chain (outermost first) and shadow-host chain inside its own document.
  function chains(el){
    const shadow = []; let root = el.getRootNode();
    while (root && root.host) { shadow.unshift(cssPathIn(root.host)); root = root.host.getRootNode(); }
    const frames = []; let win = el.ownerDocument.defaultView;
    try { while (win && win.frameElement) { frames.unshift(frameSel(win.frameElement)); win = win.parent; } } catch(e) {}
    return {frames, shadow};
  }
"""

SNAPSHOT_JS = r"""
return (function(){
""" + JS_LIB + r"""
  const MAX = 25000;
  const out = []; const ELS = []; const BLOCKED = []; let uid = 0; const TOK = Math.random().toString(36).slice(2, 10);
  function ownText(el){ let t=''; for (const n of el.childNodes) if (n.nodeType===3) t += n.nodeValue; return norm(t); }
  function interesting(el, tag){
    if (FORM.has(tag) || ['button','a','summary','option','label','img','svg','iframe'].includes(tag)) return true;
    if (el.getAttribute('role') || el.onclick || el.hasAttribute('onclick') || el.isContentEditable && el.getAttribute('contenteditable')!==null
        || el.hasAttribute('tabindex') || el.hasAttribute('draggable') || el.getAttribute('data-testid')) return true;
    if (TEXTY.has(tag) && ownText(el)) return true;
    return false;
  }
  const SKIP = new Set(['script','style','noscript','template','head','meta','link','html','body','br','path','g']);
  function walk(root, frames, shadow){
    let list; try { list = root.querySelectorAll('*'); } catch(e) { return; }
    for (const el of list) {
      if (out.length >= MAX) return;
      const tag = el.tagName.toLowerCase();
      if (el.shadowRoot) walk(el.shadowRoot, frames, shadow.concat([cssPathIn(el)]));
      if (tag==='iframe' || tag==='frame') {
        let d = null; try { d = el.contentDocument; } catch(e) {}
        if (d && d.documentElement) walk(d, frames.concat([cssPathIn(el)]), []);
        else if (shadow.length === 0 && visible(el)) BLOCKED.push(frames.concat([cssPathIn(el)]));   // cross-origin: engine enters it
      }
      if (SKIP.has(tag) || el.hasAttribute('data-aiauto-box') || !interesting(el, tag)) continue;
      const type = (el.getAttribute('type')||'').toLowerCase();
      const vis = visible(el);
      // custom-skinned native controls: a transparent <select>/<input> laid over a styled span (SauceDemo's sort,
      // most "pretty" checkboxes). Still the element to act on, as long as it is laid out and not hidden.
      const skinned = !vis && (tag==='select' || tag==='input' || tag==='textarea') && (() => {
        const r = el.getBoundingClientRect(), st = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden'; })();
      if (!vis && !skinned && !(tag==='input' && ['checkbox','radio','file'].includes(type))) continue;
      const i = info(el);
      if (i.longtext) continue;  // big containers are never what the user means
      i.uid = ++uid; i.visible = vis; i.frames = frames; i.shadow = shadow;
      i.marker = TOK + ':' + i.uid;
      el.__aiautoId = i.marker;
      const anc = []; for (let p = el.parentElement, k = 0; p && k < 8; p = p.parentElement, k++) if (p.__aiautoMark && p.__aiautoMark[0] === TOK) anc.push(p.__aiautoMark[1]);
      el.__aiautoMark = [TOK, i.uid]; i.anc = anc;
      out.push(i); ELS.push(el);
    }
  }
  walk(document, [], []);
  try { window.__aiautoEls = ELS; window.__aiautoBlocked = BLOCKED; } catch (e) {}   // chosen element looked up by uid afterwards
  // large pages: only send back elements sharing a word with the step (less data over the wire)
  const W = /*WORDS*/[];
  if (W.length && out.length > 300) {
    const f = out.filter(i => { const h = [i.name, i.text, i.label, i.aria_label, i.placeholder, i.value, i.alt, i.title, i.testid, i.id, i.nameattr]
      .join(' ').toLowerCase().replace(/[_-]/g, ' '); return W.some(w => h.includes(w)); });
    if (f.length) return f;
  }
  return out;
})();
"""


def snapshot_js(words: list[str] | None = None) -> str:
    """SNAPSHOT_JS that, on pages with many elements, returns only elements sharing one of `words`."""
    import json
    return SNAPSHOT_JS.replace("/*WORDS*/[]", json.dumps([w.lower() for w in (words or [])]))


def step_words(target: str | None) -> list[str]:
    """Words used to pre-filter candidates on big pages (empty = no filtering, e.g. 'the first result')."""
    return [w for w in _content_tokens(target or "") if len(w) > 2 or w.isdigit()]


# In-page check of Selenium locator specs against ONE element: [by, value] -> matches exactly that element?
VERIFY_JS = r"""
const el = (window.__aiautoEls || [])[arguments[0] - 1]; if (!el) return null;
const root = el.getRootNode(), doc = el.ownerDocument;
const q = s => String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
const only = list => list.length === 1 && list[0] === el;
return arguments[1].map(spec => {
  if (!spec) return false;
  const [by, v] = spec;
  try {
    if (by === 'css selector') return only(root.querySelectorAll(v));
    if (by === 'id') return only(root.querySelectorAll('[id="' + q(v) + '"]'));
    if (by === 'name') return only(root.querySelectorAll('[name="' + q(v) + '"]'));
    if (by === 'link text') return only(Array.from(root.querySelectorAll('a')).filter(a => (a.innerText || '').trim() === v));
    if (by === 'xpath') { const r = doc.evaluate(v, doc, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      return r.snapshotLength === 1 && r.snapshotItem(0) === el; }
  } catch (e) { return false; }
  return false;
});
"""

# Human-facing info of one element (to check that a replayed fallback still points at what the step means).
INFO_EL_JS = "(el) => {" + JS_LIB + " return info(el); }"

# CSS path of an element within its own document / shadow root (used for iframe chains).
CSS_PATH_JS = "(el) => {" + JS_LIB + " return cssPathIn(el); }"

# Builds the full description + unique locators of a single element (the one a step picked).
# Called as a function of the element: (el, marker) => {...}
DESCRIBE_EL_JS = "(el, marker) => {" + JS_LIB + r"""
  const i = info(el); const ch = chains(el);
  i.frames = ch.frames; i.shadow = ch.shadow; i.visible = visible(el);
  if (marker) { el.__aiautoId = marker; i.marker = marker; } else { i.marker = el.__aiautoId || null; }
  i.locators = locatorsFor(el, i, ch.shadow.length > 0);
  return i;
}"""

ROLE_WORDS = {
    "button": {"button", "input:submit", "input:button", "input:reset", "role:button"},
    "btn": {"button", "input:submit", "input:button", "role:button"},
    "link": {"a", "role:link"},
    "field": {"input", "textarea", "role:textbox", "role:searchbox", "role:combobox", "role:spinbutton"},
    "textbox": {"input", "textarea", "role:textbox", "role:searchbox"},
    "input": {"input", "textarea"},
    "box": {"input", "textarea", "select", "role:searchbox"},
    "search": {"role:searchbox", "input:search"},
    "dropdown": {"select", "role:combobox", "role:listbox", "button"},
    "menu": {"a", "button", "role:menu", "role:menuitem", "li", "nav"},
    "checkbox": {"input:checkbox", "role:checkbox", "role:switch"},
    "radio": {"input:radio", "role:radio"},
    "tab": {"role:tab"},
    "image": {"img", "svg", "role:img"},
    "icon": {"svg", "img", "i", "button", "a"},
    "heading": {"h1", "h2", "h3", "h4", "h5", "h6", "role:heading"},
    "title": {"h1", "h2", "h3", "role:heading"},
    "option": {"option", "role:option", "li"},
    "slider": {"input:range", "role:slider"},
    "editor": {"role:textbox"},
}

STOP = {"the", "a", "an", "on", "in", "into", "to", "of", "for", "with", "at", "field", "button", "link", "box",
        "textbox", "input", "dropdown", "menu", "checkbox", "radio", "tab", "icon", "image", "element", "option",
        "btn", "text", "area", "page", "heading", "title", "item", "result", "results", "containing", "named",
        "called", "labelled", "labeled", "slider", "editor", "or", "and"}


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


def _content_tokens(target: str) -> list[str]:
    toks = _tokens(target)
    # "a"/"an"/"the" are articles only at the start ("the Login button"); "Card A", "Plan B" keep them
    keep = [w for i, w in enumerate(toks) if w not in STOP or (i > 0 and len(w) == 1)]
    return keep or toks


def _sim(target: str, text: str) -> float:
    """0..1 similarity between the user's words and an element attribute."""
    if not target or not text:
        return 0.0
    t, x = target.lower().strip(), text.lower().strip()
    if t == x:
        return 1.0
    tt = _content_tokens(t)
    xt = set(_tokens(x))
    if not tt or not xt:
        return 0.0
    if " ".join(tt) == " ".join(_tokens(x)):
        return 0.97
    overlap = sum(1 for w in tt if w in xt or any((w in y or y in w) and min(len(w), len(y)) > 2 for y in xt)) / len(tt)
    if overlap == 0:
        return 0.0          # no shared words: not worth the (comparatively slow) fuzzy comparison
    ratio = SequenceMatcher(None, " ".join(tt), x[:120]).ratio()
    contains = 0.15 if " ".join(tt) in x else 0.0
    length_pen = min(0.3, max(0, len(x) - 3 * len(t)) / 400)
    return max(0.0, min(1.0, 0.6 * overlap + 0.3 * ratio + contains - length_pen))


def _is_text_input(c: dict) -> bool:
    tag, typ, role = c["tag"], c.get("type", ""), c.get("role", "")
    return (tag == "input" and typ not in ("checkbox", "radio", "submit", "button", "reset", "file", "hidden", "image", "range", "color")) \
        or tag == "textarea" or c.get("editable") or role in ("textbox", "searchbox", "spinbutton")


def _action_fit(action: Action, c: dict) -> float:
    tag, typ, role = c["tag"], c.get("type", ""), c.get("role", "")
    clickable = tag in ("button", "a", "summary", "option", "label") or role in (
        "button", "link", "menuitem", "tab", "option", "checkbox", "radio", "switch", "treeitem") \
        or (tag == "input" and typ in ("submit", "button", "reset", "checkbox", "radio", "image"))
    if action in (Action.TYPE, Action.APPEND):
        if tag == "input" and typ in ("range", "color", "date", "time", "datetime-local", "month", "week", "number"):
            return 1.0
        return 1.0 if _is_text_input(c) else (0.2 if role == "combobox" else -1.0)
    if action == Action.SELECT:
        return 1.0 if tag == "select" else (0.4 if role in ("combobox", "listbox") else -0.6)
    if action in (Action.CHECK, Action.UNCHECK):
        return 1.0 if (typ in ("checkbox", "radio") or role in ("checkbox", "radio", "switch")) else (0.1 if tag == "label" else -0.6)
    if action == Action.UPLOAD:
        return 1.0 if typ == "file" else -1.0
    if action == Action.DRAG:
        return 0.6 if c.get("draggable") else 0.0
    if action in (Action.CLICK, Action.DOUBLE_CLICK, Action.RIGHT_CLICK):
        return 0.6 if clickable else (-0.2 if _is_text_input(c) else 0.0)
    if action == Action.HOVER:
        return 0.3 if clickable or tag in ("li", "nav") else 0.0
    return 0.0


FIELD_WEIGHTS = {"testid": 1.0, "label": 1.0, "aria_label": 1.0, "name": 1.0, "placeholder": 0.95, "text": 0.95,
                 "value": 0.9, "nameattr": 0.8, "id": 0.75, "title": 0.8, "alt": 0.8, "cls": 0.55}


def _norm_attr(f: str, v: str) -> str:
    return v.replace("_", " ").replace("-", " ") if f in ("nameattr", "id", "testid", "cls") else v


def score(step: Step, c: dict, target: str | None = None) -> float:
    target = target if target is not None else (step.target or "")
    best = max((w * _sim(target, _norm_attr(f, c.get(f) or "")) for f, w in FIELD_WEIGHTS.items()), default=0.0)
    ty = (c.get("type") or "").lower()
    if ty in ("submit", "reset") and ty in set(_tokens(target)):
        best = max(best, 0.9)      # "the submit button" names the control by its type; its label ("Create account") is anything
    c["_text_match"] = best
    s = best * 3.0
    # numbers the user wrote ("order 750", "row 3", "$25") must appear exactly; "Open order 5" is not order 750
    nums = [w for w in _content_tokens(target) if any(ch.isdigit() for ch in w)]
    if nums:
        hay = set(_tokens(" ".join(str(c.get(f) or "") for f in ("name", "text", "label", "aria_label", "value", "title", "alt", "placeholder", "context"))))
        if any(n not in hay for n in nums):
            s -= 2.5
    # an element whose visible name/text/label is exactly what the user wrote beats near-matches
    core = " ".join(_content_tokens(target))
    if core and any(" ".join(_tokens(c.get(f) or "")) == core for f in ("name", "text", "label", "aria_label", "placeholder", "value", "alt", "title")):
        s += 0.3
    elif core and any(" ".join(_tokens(c.get(f) or "")).startswith(core + " ") for f in ("name", "text", "label", "aria_label")):
        s += 0.15         # "Cart" -> "Cart, 2 items" rather than "Add to Cart"
    s += _action_fit(step.action, c)
    kinds = _kinds(c)
    for w in _tokens(target):
        if w in ROLE_WORDS and kinds & ROLE_WORDS[w]:
            s += 0.4
    # words the user gave that match where the element sits ("the View button for order 1002")
    ctx = c.get("context")
    if ctx and best < 0.999:
        own = set(_tokens(" ".join(str(c.get(f) or "") for f in ("name", "text", "label"))))
        extra = [w for w in _content_tokens(target) if w not in own]
        if extra and all(w in set(_tokens(ctx)) for w in extra):
            s += 0.6
    if step.action == Action.SELECT and step.value and any(step.value.split("|")[0].lower() == o.lower() for o in c.get("options", [])):
        s += 0.8
    if c.get("visible"):
        s += 0.1
    return round(s, 4)


def _contains_all(target: str, c: dict) -> bool:
    toks = _content_tokens(target)
    hay = " ".join(_tokens(" ".join(str(c.get(f) or "") for f in ("text", "name", "label", "aria_label", "alt", "title", "placeholder", "value"))
                           + " " + " ".join(_norm_attr(f, str(c.get(f) or "")) for f in ("testid", "id", "nameattr"))))
    return bool(toks) and all(t in hay for t in toks)


def _role_kinds(target: str) -> set[str]:
    """The element kinds a description names ("checkbox", "the first link", "todo checkbox")."""
    out: set[str] = set()
    if re.search(r"(?i)\btext\s*(?:box|field|input|area)\b|\binput\s+field\b", target or ""):
        out |= ROLE_WORDS["textbox"]                         # "the first text box"
    for w in _tokens(target):
        if w in ROLE_WORDS and w not in ("box", "field", "input", "menu", "icon", "option", "title"):
            out |= ROLE_WORDS[w]
    return out


_HAY = ("name", "text", "label", "aria_label", "placeholder", "value", "alt", "title", "testid", "id", "nameattr", "context")


def rank(step: Step, candidates: list[dict], target: str | None = None, top: int = 8) -> list[dict]:
    target = target if target is not None else (step.target or "")
    words = [w for w in _content_tokens(target) if len(w) > 2 or w.isdigit()]
    if words and len(candidates) > 200:
        # large pages: only score elements that share at least one word (or word part) with the step
        def shares(c):
            hay = " ".join(str(c.get(f) or "") for f in _HAY).lower().replace("_", " ").replace("-", " ")
            return any(w in hay for w in words)
        candidates = [c for c in candidates if shares(c)] or candidates
    scored = []
    for c in candidates:
        c = dict(c)
        c["score"] = score(step, c, target)
        scored.append(c)
    scored.sort(key=lambda c: c["score"], reverse=True)
    # a wrapper and its own child often tie (<li><a>Home</a></li>): keep the better-fitting one
    out, seen_uids = [], set()
    for c in scored:
        if c["uid"] in seen_uids:
            continue
        out.append(c)
        seen_uids.add(c["uid"])
        if len(out) >= top:
            break
    return out


def nth_match(step: Step, candidates: list[dict], target: str, n: int) -> tuple[dict | None, list[dict]]:
    """Ordinal pick ('first result containing Logitech'): nth match in document order, de-duplicated so a
    link and the span inside it count once."""
    pool = []
    generic = all(w in STOP for w in _tokens(target))      # "the first result" / "the second product"
    kinds = _role_kinds(target)
    for c in candidates:
        if generic and kinds:                               # "checkbox 1", "the second link": that kind, in page order
            ok = bool(_kinds(c) & kinds)
        elif generic:
            ok = (c.get("role") in ("link", "button") or c["tag"] in ("a", "button")) and len(c.get("name") or c.get("text") or "") >= 15
        else:
            # "the first todo checkbox": its words in its text, label or ids, and the kind when one is named
            ok = _contains_all(target, c) and (not kinds or bool(_kinds(c) & kinds))
        if ok and _action_fit(step.action, c) >= 0:
            c = dict(c)
            c["score"] = score(step, c, target)
            pool.append(c)
    pool.sort(key=lambda c: c["uid"])
    kept: list[dict] = []
    for c in pool:
        dup = next((k for k in kept if k["uid"] in c.get("anc", []) or c["uid"] in k.get("anc", [])), None)
        if dup is None:
            kept.append(c)
        elif c["score"] > dup["score"] and dup["uid"] in c.get("anc", []):
            kept[kept.index(dup)] = c       # prefer the better-scoring (usually clickable) one
    if n < 0:
        n = len(kept) + n
    win = kept[n] if 0 <= n < len(kept) else None
    if win:
        win["picked_by"] = "heuristic"
    return win, kept[:8]


TIE = 0.12      # top two closer than this (and genuinely different elements) = a real tie


def _related(a: dict, b: dict) -> bool:
    return a["uid"] in b.get("anc", []) or b["uid"] in a.get("anc", [])


def _same_destination(a: dict, b: dict, step: Step) -> bool:
    """Clicking either of two links to the same address does the same thing ("Cart" in the header and
    "Go to Cart" in a banner): not an ambiguity worth failing the step for."""
    if step.action not in (Action.CLICK, Action.HOVER):
        return False
    ha, hb = (a.get("href") or "").strip(), (b.get("href") or "").strip()
    useless = ("", "#")
    return a.get("tag") == b.get("tag") == "a" and ha == hb and ha not in useless and not ha.lower().startswith("javascript:")


def choose(step: Step, candidates: list[dict], target: str | None = None, use_llm: bool = True,
           on_ambiguous: str = "fail") -> tuple[dict | None, list[dict], str | None]:
    """Pick the element for this step. Returns (winner, ranked_candidates, problem).

    problem is None, or an explanation when no element can be chosen safely:
    nothing matched, or two different elements tie and neither Claude nor an ordinal settles it
    (with on_ambiguous="fail" the step then fails instead of guessing)."""
    target = target if target is not None else (step.target or "")
    if step.nth is None:     # "checkbox 1", "link 2", "the radio button 3": the Nth of that kind
        m = re.fullmatch(r"(?i)(?:the\s+)?(?P<k>[a-z][a-z ]*?)\s+(?:#|no\.?\s*|number\s+)?(?P<n>\d{1,2})", target.strip())
        if m and _role_kinds(m.group("k")) and all(w in STOP for w in _tokens(m.group("k"))) and int(m.group("n")) >= 1:
            w, ranked = nth_match(step, candidates, m.group("k"), int(m.group("n")) - 1)
            if w:
                return w, ranked, None
    if step.nth is not None:
        w, ranked = nth_match(step, candidates, target, step.nth)
        return w, ranked, None if w else "no match for that position"
    ranked = rank(step, candidates, target)
    # a winner needs textual evidence: being the right kind of element (the only textbox…) is not enough,
    # unless the description has no specific words at all ("the textbox")
    specific = any(w not in STOP for w in _tokens(target))
    eligible = [c for c in ranked if not specific or c.get("_text_match", 0) > 0.25]
    # "Click the primary button": text that merely mentions the words (a code sample, a caption) is not it.
    # Only that kind of element, or something interactive, may win; else the engine looks further (DOM walk).
    named = _role_kinds(target)
    if named and step.action in ACTING:
        eligible = [c for c in eligible if _kinds(c) & named or _interactive(c)]
    if step.action in (Action.TYPE, Action.APPEND, Action.SELECT, Action.UPLOAD):
        # you can't type into a label: its text may match best, the field is what the step acts on
        eligible = [c for c in eligible if _action_fit(step.action, c) >= 0]
    winner = eligible[0] if eligible and eligible[0]["score"] >= 1.0 else None
    if winner:
        winner["picked_by"] = "heuristic"
    close = len(ranked) > 1 and (winner is None or ranked[0]["score"] - ranked[1]["score"] < 0.35)
    if use_llm and close and ranked:
        from .nlp import llm
        if llm.available():
            probe = step.model_copy(update={"target": target})
            idx = llm.pick_candidate(probe, ranked)
            if idx is not None:
                winner = ranked[idx]
                winner["picked_by"] = "ai"
                return winner, ranked, None
    if winner is None:
        return None, ranked, "nothing on the page matches well enough"
    rivals = [c for c in eligible[1:4] if winner["score"] - c["score"] < TIE and not _related(winner, c)
              and not _same_destination(winner, c, step)]
    if rivals and on_ambiguous == "fail":
        names = "; ".join(describe(c) + (f" in {c['context']}" if c.get("context") else "") for c in [winner] + rivals)
        return None, ranked, (f"{len(rivals) + 1} different elements match equally well ({names}). "
                              "Say which one: e.g. 'the first …', 'the … in <section>', or use more of its text")
    return winner, ranked, None


ACTING = {Action.CLICK, Action.DOUBLE_CLICK, Action.RIGHT_CLICK, Action.CHECK, Action.UNCHECK, Action.SELECT, Action.TYPE,
          Action.APPEND, Action.UPLOAD}
_INTERACTIVE_ROLES = {"button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "menuitemcheckbox", "menuitemradio",
                      "option", "combobox", "listbox", "textbox", "searchbox", "spinbutton", "slider", "treeitem"}


def _interactive(c: dict) -> bool:
    return c["tag"] in ("button", "a", "input", "select", "textarea", "summary", "option") or (c.get("role") or "") in _INTERACTIVE_ROLES \
        or bool(c.get("editable"))


def describe(c: dict) -> str:
    bits = [c["tag"]]
    for k in ("type", "role"):
        if c.get(k):
            bits.append(f"{k}={c[k]}")
    for k in ("name", "text", "label", "aria_label", "placeholder", "nameattr", "id", "value"):
        if c.get(k):
            bits.append(f'"{c[k][:50]}"')
            break
    if c.get("ax"):
        bits.append("(role/name from Chrome accessibility tree)")
    if c.get("source") == "playwright-aria":
        bits.append("(Playwright snapshot)")
    if c.get("frames"):
        bits.append(f"(in iframe x{len(c['frames'])})")
    if c.get("shadow"):
        bits.append(f"(in shadow root depth {len(c['shadow'])})")
    return " ".join(bits)


def to_locators(c: dict) -> list[Locator]:
    desc = describe(c)
    return [Locator(strategy=l["strategy"], value=l["value"], role=l.get("role"), frames=c.get("frames", []),
                    shadow=c.get("shadow", []), score=c.get("score", 0), description=desc, picked_by=c.get("picked_by"),
                    code=l.get("code"))
            for l in c.get("locators", [])]
