"""Deterministic plain-English -> Step parser.

Used when no LLM is configured, and as a safety net if the LLM call fails.
Handles sentences such as:
  Open https://example.com
  Click on the "Login" button
  Type "alice" into the username field
  Enter ${password} in password
  Hover over Products menu
  Select "India" from country dropdown
  Press Enter
  Verify page contains "Welcome"
  Wait 2 seconds
"""
from __future__ import annotations

import re

from ..models import Action, Step

Q = r"""["'“”‘’`]"""                     # any quote char
QV = rf"{Q}(?P<v>.+?){Q}"                  # quoted value
QT = rf"{Q}(?P<t>.+?){Q}"                  # quoted target

_FILLER = re.compile(
    r"^(?:the|a|an|on|at|in|into|to|from|over|onto|inside|field|button|link)\s+|"
    r"\s+(?:field|box|textbox|input|button|link|icon|tab|menu|dropdown|element|option|checkbox|radio)$",
    re.I,
)


def _clean_target(t: str | None) -> str | None:
    if not t:
        return t
    t = t.strip().strip(".").strip()
    t = re.sub(rf"^{Q}|{Q}$", "", t)
    # keep the descriptive role word in the target but drop leading articles/prepositions
    t = re.sub(r"^(?:on|at|in|into|to|from|over|onto|inside)\s+", "", t, flags=re.I)
    t = re.sub(r"^(?:the|a|an)\s+", "", t, flags=re.I)
    return t or None


def _split_sentences(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:\d+[.)]|[-*•]|step\s*\d+\s*[:.)-])\s*", "", line, flags=re.I).strip()
        if not line:
            continue
        # split on ' then ' / ' and then ' / ';' / '. ' -- but never inside quotes
        quoted: list[str] = []

        def _hide(m: re.Match) -> str:
            quoted.append(m.group(0))
            return f"\x00{len(quoted) - 1}\x00"

        masked = re.sub(r'"[^"]*"|“[^”]*”|\'[^\']*\'(?=\s|$|[.,;])', _hide, line)
        parts = re.split(r"\s*(?:;|\.\s+(?=[A-Z])|,?\s+and then\s+|,?\s+then\s+)\s*", masked, flags=re.I)
        for p in parts:
            p = re.sub(r"\x00(\d+)\x00", lambda m: quoted[int(m.group(1))], p).strip().rstrip(".")
            if p:
                lines.append(p)
    return lines


PATTERNS: list[tuple[re.Pattern, Action, str]] = []


def _p(regex: str, action: Action, kind: str = "") -> None:
    PATTERNS.append((re.compile(regex, re.I), action, kind))


URL = r"(?P<v>(?:https?://|www\.|file://|data:|\$\{|\{\{)\S+|[\w.-]+\.(?:com|org|net|io|dev|in|co|app|ai|edu|gov)(?:/\S*)?)"

_p(rf"^(?:go\s+to|goto|open|navigate\s+to|visit|launch|browse\s+to|load)\s+(?:the\s+)?(?:url\s+|page\s+|site\s+|website\s+)?{Q}?{URL}{Q}?", Action.NAVIGATE)
_p(r"^(?:go\s+back|navigate\s+back|back)$", Action.BACK)
_p(r"^(?:refresh|reload)(?:\s+the)?(?:\s+page)?$", Action.REFRESH)
_p(rf"^(?:type|enter|input|fill(?:\s+in)?|write|put|key\s+in)\s+{QV}\s+(?:in|into|on|to|for|inside|as)\s+(?P<t>.+)$", Action.TYPE)
_p(r"^(?:type|enter|input|fill(?:\s+in)?|write|put)\s+(?P<v>\$\{[^}]+\}|\{\{[^}]+\}\}|\S+)\s+(?:in|into|on|to|for|inside|as)\s+(?P<t>.+)$", Action.TYPE)
_p(rf"^(?:fill|set)\s+(?P<t>.+?)\s+(?:with|to|as)\s+{QV}$", Action.TYPE)
_p(r"^(?:fill|set)\s+(?P<t>.+?)\s+(?:with|to|as)\s+(?P<v>\S+)$", Action.TYPE)
_p(rf"^(?:append|add)\s+{QV}\s+(?:in|into|to)\s+(?P<t>.+)$", Action.APPEND)
_p(rf"^(?:clear)\s+(?P<t>.+)$", Action.TYPE, "clear")
_p(rf"^(?:select|choose|pick)\s+{QV}\s+(?:from|in|on)\s+(?P<t>.+)$", Action.SELECT)
_p(r"^(?:select|choose|pick)\s+(?P<v>\S+)\s+(?:from|in)\s+(?P<t>.+)$", Action.SELECT)
_p(r"^(?:press|hit)\s+(?:the\s+)?(?P<v>enter|return|tab|escape|esc|space|backspace|delete|arrow\s*(?:up|down|left|right)|up|down|left|right|page\s*(?:up|down)|home|end|f\d{1,2}|(?:ctrl|control|shift|alt|meta|cmd)\s*\+\s*\w+)(?:\s+key)?(?:\s+(?:in|on)\s+(?P<t>.+))?$", Action.PRESS)
_p(r"^(?:double[\s-]?click|dbl[\s-]?click)\s+(?:on\s+)?(?P<t>.+)$", Action.DOUBLE_CLICK)
_p(r"^(?:right[\s-]?click|context[\s-]?click)\s+(?:on\s+)?(?P<t>.+)$", Action.RIGHT_CLICK)
_p(r"^(?:uncheck|untick|deselect|disable)\s+(?P<t>.+)$", Action.UNCHECK)
_p(r"^(?:check|tick|enable)\s+(?P<t>(?!that|if|whether).+)$", Action.CHECK)
_p(r"^(?:hover|mouse\s*over|move\s+(?:the\s+)?mouse\s+(?:to|over))\s+(?:over\s+|on\s+)?(?P<t>.+)$", Action.HOVER)
_p(rf"^(?:upload|attach)\s+{QV}\s+(?:to|in|into|using)\s+(?P<t>.+)$", Action.UPLOAD)
_p(rf"^(?:drag|move)\s+(?:the\s+)?(?P<t>.+?)\s+(?:and\s+drop\s+)?(?:to|onto|into|on|over)\s+(?:the\s+)?(?P<v>.+)$", Action.DRAG)
_p(r"^(?:pause|wait)\s+(?:for\s+)?(?P<v>\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds?)?\s+(?:for|to\s+(?:allow|let))\s+.*(?:login|log\s*in|sign\s*in|sso|mfa|otp|manual|captcha|user).*$", Action.PAUSE)
_p(r"^pause(?:\s+for)?\s+(?P<v>\d+(?:\.\d+)?)", Action.PAUSE)
_p(r"^(?P<v>accept|dismiss|cancel|confirm|ok)\s+(?:the\s+)?(?:next\s+)?(?:alert|dialog|popup|confirm(?:ation)?|prompt)s?$", Action.DIALOG)
_p(rf"^(?:verify|assert|check|ensure|confirm|expect)\s+(?:that\s+)?(?:the\s+)?(?:alert|dialog|popup|confirm(?:ation)?|prompt)\s+(?:says|shows|contains|has|reads|displays|text\s+is)\s+{QV}", Action.ASSERT_DIALOG)
_p(r"^(?:switch|go|move)\s+to\s+(?:the\s+)?(?:(?P<v>new|last|first|second|third|\d+)(?:st|nd|rd|th)?\s+)?(?:browser\s+)?(?:tab|window)(?:\s+(?P<n>\d+))?$", Action.SWITCH_TAB)
_p(r"^(?:click|tap|press|hit|submit|push)\s+(?:on\s+)?(?P<t>.+)$", Action.CLICK)
_p(r"^scroll\s+(?:down\s+)?to\s+(?P<t>.+)$", Action.SCROLL, "to")
_p(r"^scroll\s+(?P<v>up|down|top|bottom)(?:\s+(?:by\s+)?(?P<n>\d+))?", Action.SCROLL)
_p(r"^(?:wait|sleep|pause)\s+(?:for\s+)?(?P<v>\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds?)?$", Action.WAIT)
_p(r"^wait\s+(?:for|until)\s+(?P<t>.+?)(?:\s+(?:to\s+)?(?:be\s+)?(?:visible|appear|appears|shown|displayed|loaded))?$", Action.WAIT, "element")
_p(rf"^(?:verify|assert|check|ensure|confirm|validate|expect)\s+(?:that\s+)?(?:the\s+)?(?:page\s+)?(?:url|address)\s+(?:contains|is|has|includes|matches)\s+{QV}", Action.ASSERT_URL)
_p(rf"^(?:verify|assert|check|ensure|confirm|validate|expect)\s+(?:that\s+)?(?:the\s+)?(?:page\s+)?title\s+(?:contains|is|has|includes|equals)\s+{QV}", Action.ASSERT_TITLE)
_p(rf"^(?:verify|assert|check|ensure|confirm|validate|expect)\s+(?:that\s+)?(?:the\s+)?(?P<t>.+?)\s+(?:contains|has|shows|displays|includes|reads|says|has\s+text)\s+{QV}", Action.ASSERT_TEXT)
_p(rf"^(?:verify|assert|check|ensure|confirm|validate|expect|see)\s+(?:that\s+)?(?:the\s+)?(?:page\s+)?(?:contains|shows|displays|has|includes)?\s*(?:text\s+)?{QV}(?:\s+is\s+(?:visible|displayed|shown|present))?$", Action.ASSERT_TEXT)
_p(r"^(?:verify|assert|check|ensure|confirm|validate|expect)\s+(?:that\s+)?(?:the\s+)?(?P<t>.+?)\s+(?:is|are)\s+(?:visible|displayed|shown|present)$", Action.ASSERT_VISIBLE)
_p(r"^(?:take|capture|grab)\s+(?:a\s+)?(?:full\s+page\s+)?screenshot(?:\s+(?:named|as)\s+(?P<v>\S+))?", Action.SCREENSHOT)


_OPTIONAL = re.compile(r"(?:,?\s+(?:if|when)\s+(?:it\s+|one\s+|the\s+\w+\s+)?(?:is\s+|are\s+)?(?:present|shown|visible|displayed|exists|appears|there|available|any)|\s+\(optional\)|,?\s+optionally)$", re.I)


def parse_sentence(sentence: str) -> Step | None:
    s = sentence.strip()
    optional = False
    if _OPTIONAL.search(s):
        s, optional = _OPTIONAL.sub("", s).strip(), True
    elif re.match(r"^(?:optionally|if present,?|if shown,?)\s+", s, re.I):
        s, optional = re.sub(r"^(?:optionally|if present,?|if shown,?)\s+", "", s, flags=re.I), True
    step = _parse(s, sentence)
    if step and optional:
        step.optional = True
    return step


def _parse(s: str, sentence: str) -> Step | None:
    for rx, action, kind in PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        g = m.groupdict()
        value = g.get("v")
        nth = None
        raw_t = g.get("t")
        if raw_t:
            raw_t = re.sub(r"^(?:on|at|in|into|to|over)\s+", "", raw_t.strip().rstrip("."), flags=re.I)
            raw_t, nth = _ordinal(raw_t)
        target = _clean_target(raw_t)
        if action == Action.NAVIGATE:
            if value and not re.match(r"^(?:https?:|file:|data:|\$\{|\{\{)", value, re.I):
                value = "https://" + value
        elif action == Action.TYPE and kind == "clear":
            value = ""
        elif action == Action.SCROLL:
            if kind == "to":
                value = None
            else:
                value = f"{value.lower()}{':' + g['n'] if g.get('n') else ''}"
        elif action == Action.WAIT and kind == "element":
            value = None
        elif action == Action.PRESS and value:
            value = _normalize_key(value)
        elif action == Action.SWITCH_TAB:
            word = (g.get("n") or value or "last").lower()
            value = {"new": "last", "last": "last", "first": "0", "second": "1", "third": "2"}.get(word, word)
            if value.isdigit() and g.get("n"):
                value = str(int(value) - 1)
        elif action == Action.DIALOG:
            value = "dismiss" if value.lower() in ("dismiss", "cancel") else "accept"
        elif action == Action.CLICK:
            # "click on Login" vs "click the 'Login' button"
            qm = re.search(QT, s) if nth is None else None
            if qm:
                rest = s[qm.end():].strip()
                role = re.match(r"(button|link|tab|icon|menu|checkbox|radio|option)\b", rest, re.I)
                target = qm.group("t") + (f" {role.group(1)}" if role else "")
        if action in (Action.ASSERT_TEXT, Action.WAIT) and target and \
                re.fullmatch(r"(?:web\s*)?(?:page|screen|body|site|window)", target, re.I):
            target = None
        if target:
            qm = re.fullmatch(QT, target)
            if qm:
                target = qm.group("t")
        if action == Action.DRAG and value:
            value, _ = _ordinal(_clean_target(value))
        return Step(action=action, target=target, value=value, raw=sentence, nth=nth)
    return None


_ORD = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3, "fifth": 4,
        "5th": 4, "last": -1}


def _ordinal(target: str) -> tuple[str, int | None]:
    """'first result containing "Logitech"' -> ('Logitech', 0);  'second Add to cart button' -> ('Add to cart button', 1)"""
    m = re.match(r"^(?:the\s+)?(first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th))\s+(.+)$", target, re.I)
    if not m or re.match(r"(?:name|names|page|time|login|step)\b", m.group(2), re.I):
        return target, None          # "First name field", "last page" are names, not ordinals
    word, rest = m.group(1).lower(), m.group(2)
    n = _ORD.get(word, int(re.sub(r"\D", "", word) or 1) - 1)
    q = re.search(r"(?:containing|with|named|called|titled|labell?ed|matching|that says|saying)\s+" + QT, rest, re.I)
    if q:
        return q.group("t"), n
    rest = re.sub(r"^(?:search\s+)?(?:result|item|product|link|row|option|element|entry)s?\s+(?:for|of)\s+", "", rest, flags=re.I)
    rest = re.sub(rf"^{Q}(.+?){Q}(\s+.*)?$", lambda mm: mm.group(1) + (mm.group(2) or ""), rest)
    return rest.strip(), n


def _normalize_key(k: str) -> str:
    k = re.sub(r"\s+", "", k.strip()).lower()
    table = {
        "enter": "Enter", "return": "Enter", "tab": "Tab", "escape": "Escape", "esc": "Escape",
        "space": "Space", "backspace": "Backspace", "delete": "Delete", "home": "Home", "end": "End",
        "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
        "arrowup": "ArrowUp", "arrowdown": "ArrowDown", "arrowleft": "ArrowLeft", "arrowright": "ArrowRight",
        "pageup": "PageUp", "pagedown": "PageDown",
    }
    if "+" in k:
        mods = {"ctrl": "Control", "control": "Control", "shift": "Shift", "alt": "Alt", "meta": "Meta", "cmd": "Meta"}
        parts = k.split("+")
        return "+".join(mods.get(p, p.upper() if len(p) == 1 else table.get(p, p.capitalize())) for p in parts)
    if re.fullmatch(r"f\d{1,2}", k):
        return k.upper()
    return table.get(k, k.capitalize())


def parse(text: str) -> tuple[list[Step], list[str]]:
    """Free-form English or step-per-line text -> (steps, unparsed clauses).

    The text is split into single-action clauses (prose.clauses); natural intents such as
    "search for X", "log in as U with P", "add it to the cart", "make sure it shows X" are
    expanded into step sentences (prose.expand); each sentence is then matched by PATTERNS."""
    from . import prose
    steps: list[Step] = []
    unparsed: list[str] = []
    for clause in prose.clauses(text):
        expanded = prose.expand(clause)
        sentences = expanded or [clause]
        parsed = [parse_sentence(s) for s in sentences]
        if not all(parsed) and expanded:          # macro output failed: try the clause as written
            parsed = [parse_sentence(clause)]
        if not all(parsed):                       # short button-like phrase: click the control with that text
            expanded = prose.guess(clause)
            parsed = [parse_sentence(s) for s in expanded] if expanded else [None]
        if not all(parsed):
            unparsed.append(clause)
            continue
        for st in parsed:
            st.source = "rules"
            if expanded or st.raw != clause:
                st.origin = clause
            steps.append(st)
    return steps, unparsed
