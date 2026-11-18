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

SNAPSHOT_JS = r"""
return (function(){
  const MAX = 4000;
  const out = []; const ELS = []; let uid = 0; const TOK = Math.random().toString(36).slice(2, 10);
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
  function labelOf(el){
    const root = el.getRootNode();
    if (el.id) { try { const l = root.querySelector('label[for="'+q(el.id)+'"]'); if (l) return norm(l.innerText||l.textContent); } catch(e){} }
    const p = el.closest('label'); if (p) return norm(p.innerText||p.textContent);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(i=>root.getElementById ? root.getElementById(i) : document.getElementById(i)).filter(Boolean).map(e=>e.innerText||e.textContent).join(' '); if (norm(t)) return norm(t); }
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
  function candidates(el, info, inShadow){
    const c = []; const tag = info.tag;
    const add = (strategy, value, extra) => { if (value && String(value).length <= 120) c.push(Object.assign({strategy, value:String(value)}, extra||{})); };
    if (el.getAttribute('data-testid')) add('testid', el.getAttribute('data-testid'));
    for (const a of ['data-test','data-qa','data-cy','data-test-id']) if (el.getAttribute(a)) add('css', '['+a+'="'+q(el.getAttribute(a))+'"]');
    if (el.id && !dyn(el.id)) add('id', el.id);
    if (info.role && info.name && info.name.length <= 80) add('role', info.name, {role: info.role});
    if (FORM.has(tag) && info.label) add('label', info.label);
    if (info.placeholder) add('placeholder', info.placeholder);
    if (info.nameattr && !dyn(info.nameattr)) add('name', info.nameattr);
    if (info.text && info.text.length <= 80 && !FORM.has(tag)) add('text', info.text);
    if (info.alt) add('alt', info.alt);
    if (info.title) add('title', info.title);
    if (info.aria_label) add('css', tag+'[aria-label="'+q(info.aria_label)+'"]');
    if (tag==='input' && info.value && ['submit','button','reset'].includes(info.type)) add('css', 'input[type="'+info.type+'"][value="'+q(info.value)+'"]');
    if (tag==='a' && info.href && info.href.length < 100 && !/[?&]|\d{5,}/.test(info.href)) add('css', 'a[href="'+q(el.getAttribute('href'))+'"]');
    add('css', cssPathIn(el));
    if (!inShadow) add('xpath', xpathPath(el));
    return c;
  }
  function visible(el){
    const r = el.getBoundingClientRect();
    const st = (el.ownerDocument.defaultView||window).getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none' && parseFloat(st.opacity||'1') > 0.05;
  }
  function ownText(el){ let t=''; for (const n of el.childNodes) if (n.nodeType===3) t += n.nodeValue; return norm(t); }
  function interesting(el, tag){
    if (FORM.has(tag) || ['button','a','summary','option','label','img','svg','iframe'].includes(tag)) return true;
    if (el.getAttribute('role') || el.onclick || el.hasAttribute('onclick') || el.isContentEditable && el.getAttribute('contenteditable')!==null
        || el.hasAttribute('tabindex') || el.hasAttribute('draggable') || el.getAttribute('data-testid')) return true;
    if (TEXTY.has(tag) && ownText(el)) return true;
    return false;
  }
  function walk(root, frames, shadow){
    let list; try { list = root.querySelectorAll('*'); } catch(e) { return; }
    for (const el of list) {
      if (out.length >= MAX) return;
      const tag = el.tagName.toLowerCase();
      if (el.shadowRoot) walk(el.shadowRoot, frames, shadow.concat([cssPathIn(el)]));
      if (tag==='iframe' || tag==='frame') {
        try { const d = el.contentDocument; if (d && d.documentElement) walk(d, frames.concat([cssPathIn(el)]), []); } catch(e) {}
      }
      if (['script','style','noscript','template','head','meta','link','html','body','br','path','g'].includes(tag)) continue;
      if (!interesting(el, tag)) continue;
      const type = (el.getAttribute('type')||'').toLowerCase();
      const vis = visible(el);
      if (!vis && !(tag==='input' && ['checkbox','radio','file'].includes(type))) continue;
      let text = norm(el.innerText || el.textContent || '');
      if (text.length > 300) continue;  // big containers are never what the user means
      const r = el.getBoundingClientRect();
      const info = {
        uid: ++uid, tag, type, text: text.slice(0,200), id: el.id||'', nameattr: el.getAttribute('name')||'',
        aria_label: norm(el.getAttribute('aria-label')), placeholder: el.getAttribute('placeholder')||'',
        title: el.getAttribute('title')||'', alt: el.getAttribute('alt')||'',
        value: (tag==='input' && ['submit','button','reset'].includes(type)) ? (el.value||'') : '',
        href: el.getAttribute('href')||'', label: labelOf(el), visible: vis,
        testid: el.getAttribute('data-testid')||el.getAttribute('data-test')||el.getAttribute('data-qa')||'',
        editable: !!el.isContentEditable, role: el.getAttribute('role') || implicitRole(el),
        draggable: el.getAttribute('draggable')==='true',
        options: tag==='select' ? Array.from(el.options).slice(0,50).map(o=>norm(o.text)) : [],
        frames, shadow, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      };
      info.name = norm(accName(el, info));
      info.marker = TOK + ':' + info.uid;
      el.__aiautoId = info.marker;
      const anc = []; for (let p = el.parentElement, i = 0; p && i < 8; p = p.parentElement, i++) if (p.__aiautoMark && p.__aiautoMark[0] === TOK) anc.push(p.__aiautoMark[1]);
      el.__aiautoMark = [TOK, info.uid]; info.anc = anc;
      info.locators = candidates(el, info, shadow.length > 0);
      out.push(info);
      ELS.push(el);
    }
  }
  walk(document, [], []);
  try { window.__aiautoEls = ELS; } catch (e) {}   // lets an engine map candidates to browser-internal node ids (CDP)
  return out;
})();
"""

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
    return [w for w in _tokens(target) if w not in STOP] or _tokens(target)


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
                 "value": 0.9, "nameattr": 0.8, "id": 0.75, "title": 0.8, "alt": 0.8}


def _norm_attr(f: str, v: str) -> str:
    return v.replace("_", " ").replace("-", " ") if f in ("nameattr", "id", "testid") else v


def score(step: Step, c: dict, target: str | None = None) -> float:
    target = target if target is not None else (step.target or "")
    best = max((w * _sim(target, _norm_attr(f, c.get(f) or "")) for f, w in FIELD_WEIGHTS.items()), default=0.0)
    s = best * 3.0
    s += _action_fit(step.action, c)
    kinds = _kinds(c)
    for w in _tokens(target):
        if w in ROLE_WORDS and kinds & ROLE_WORDS[w]:
            s += 0.4
    if step.action == Action.SELECT and step.value and any(step.value.split("|")[0].lower() == o.lower() for o in c.get("options", [])):
        s += 0.8
    if c.get("visible"):
        s += 0.1
    return round(s, 4)


def _contains_all(target: str, c: dict) -> bool:
    toks = _content_tokens(target)
    hay = " ".join(_tokens(" ".join(str(c.get(f) or "") for f in ("text", "name", "label", "aria_label", "alt", "title", "placeholder", "value"))))
    return bool(toks) and all(t in hay for t in toks)


def rank(step: Step, candidates: list[dict], target: str | None = None, top: int = 8) -> list[dict]:
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
    for c in candidates:
        if generic:
            ok = (c.get("role") in ("link", "button") or c["tag"] in ("a", "button")) and len(c.get("name") or c.get("text") or "") >= 15
        else:
            ok = _contains_all(target, c)
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


def choose(step: Step, candidates: list[dict], target: str | None = None, use_llm: bool = True) -> tuple[dict | None, list[dict]]:
    """Pick the element for this step. Returns (winner, ranked_candidates)."""
    target = target if target is not None else (step.target or "")
    if step.nth is not None:
        return nth_match(step, candidates, target, step.nth)
    ranked = rank(step, candidates, target)
    winner = ranked[0] if ranked and ranked[0]["score"] >= 1.0 else None
    if winner:
        winner["picked_by"] = "heuristic"
    ambiguous = len(ranked) > 1 and (winner is None or ranked[0]["score"] - ranked[1]["score"] < 0.35)
    if use_llm and ambiguous and ranked:
        from .nlp import llm
        if llm.available():
            probe = step.model_copy(update={"target": target})
            idx = llm.pick_candidate(probe, ranked)
            if idx is not None:
                winner = ranked[idx]
                winner["picked_by"] = "ai"
    return winner, ranked


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
    if c.get("frames"):
        bits.append(f"(in iframe x{len(c['frames'])})")
    if c.get("shadow"):
        bits.append(f"(in shadow root depth {len(c['shadow'])})")
    return " ".join(bits)


def to_locators(c: dict) -> list[Locator]:
    desc = describe(c)
    return [Locator(strategy=l["strategy"], value=l["value"], role=l.get("role"), frames=c.get("frames", []),
                    shadow=c.get("shadow", []), score=c.get("score", 0), description=desc, picked_by=c.get("picked_by"))
            for l in c.get("locators", [])]
