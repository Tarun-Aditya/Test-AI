"""Free-form English -> step sentences, without an LLM (the fallback when Claude is not configured).

    "Go to the shop, search for a wireless mouse and open the first Logitech result.
     Add 2 of them to the cart, then go to the cart and make sure it shows Logitech."

becomes

    Open https://shop...           Type "wireless mouse" into the search box     Press Enter
    Click the first result containing "Logitech"    Select "2" from Quantity    Click Add to Cart button
    Click Cart    Verify page contains "Logitech"

Each produced sentence remembers the clause it came from (shown in reports as the step's origin).
With Claude configured this module is not used for parsing: the model reads the whole paragraph.
"""
from __future__ import annotations

import re

VERBS = (r"open|go|navigate|visit|launch|browse|load|click|tap|press|hit|type|enter|fill|input|write|put|select|choose|pick|"
         r"check|uncheck|tick|untick|hover|mouse|scroll|wait|pause|verify|assert|ensure|confirm|make\s+sure|expect|validate|"
         r"search|look\s+for|find|log\s*in|login|sign\s*in|log\s*out|logout|sign\s*out|add|upload|attach|drag|move|accept|dismiss|allow|reject|close|"
         r"switch|refresh|reload|take|capture|submit|set|clear|double|right|see|should|view|use|apply|remove|delete|"
         r"increase|decrease|proceed|continue|buy|purchase|checkout|book|save|sort|filter|expand|collapse|right-click|double-click")

_LEAD = re.compile(
    r"^(?:(?:and|so|also|now|then|next|first(?:ly)?|second(?:ly)?|finally|lastly|afterwards|after\s+that|once\s+(?:that'?s\s+)?(?:done|loaded|there)|"
    r"at\s+the\s+end|in\s+the\s+end|please|kindly|just)\s*,?\s+|"
    r"(?:i|we|you)\s+(?:want|need|would\s+like|have|'d\s+like)\s+to\s+|(?:the\s+)?(?:user|tester|test)\s+(?:should|must|will|needs?\s+to)\s+|"
    r"(?:let'?s|go\s+ahead\s+and|try\s+to|it\s+should|we\s+should|you\s+should|i\s+should)\s+|"
    r"(?:on|in|from)\s+(?:the\s+)?[\w\s-]{1,30}?\s+(?:page|screen|form|tab|dialog|section|site),?\s+)", re.I)

_SPLIT = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z$\"'])|\n+|;\s*|\s*,?\s+and\s+then\s+|\s*,?\s+then\s+|\s*,\s*after\s+that,?\s+|"
    r"\s*,?\s+(?:and\s+)?(?:finally|lastly|afterwards)\s*,?\s+|\s*,\s*(?:and\s+)?next\s*,?\s+|"
    rf"\s*,\s*(?:and\s+)?(?=(?:{VERBS})\b)|\s+and\s+(?=(?:{VERBS})\b)", re.I)

Q = "\""


def _q(v: str) -> str:
    v = v.strip().strip(".,").strip()
    m = re.fullmatch(r"[\"“'‘`](.*)[\"”'’`]", v)
    return m.group(1) if m else v


ORD = r"(?P<ord>first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th))"
NOUN = r"(?:product|result|item|one|link|listing|entry|option|row|card|search\s+result|thing)s?"

MACROS: list[tuple[re.Pattern, callable]] = []


def macro(rx: str):
    def deco(fn):
        MACROS.append((re.compile(rx, re.I), fn))
        return fn
    return deco


@macro(r"^(?:search|look|find)\s+(?:for\s+)?(?:a\s+|an\s+|the\s+|some\s+)?(?P<q>.+?)(?:\s+(?:in|on|using|via)\s+(?:the\s+)?search(?:\s+(?:box|bar|field))?)?$")
def _search(m):
    q = _q(m["q"])
    if re.match(r"^(?:the\s+)?(?:search\s+)?(?:box|bar|field|button)$", q, re.I):
        return None
    return [f'Type "{q}" into the search box', "Press Enter"]


@macro(r"^(?:log\s*in|login|sign\s*in)(?:\s+(?:as|with|using))\s+(?:(?:the\s+)?(?:user(?:name)?|email)\s+)?(?P<u>\S+)\s+(?:and|with|/|,)\s+(?:(?:the\s+)?password\s+)?(?P<p>\S+)$")
def _login(m):
    return [f'Type "{_q(m["u"])}" into the username or email field', f'Type "{_q(m["p"])}" into the password field', "Click the Log in or Sign in button"]


@macro(r"^(?:log\s*in|login|sign\s*in)$")
def _login_click(m):
    return ["Click the Log in or Sign in button"]


@macro(rf"^(?:open|click(?:\s+on)?|pick|select|choose|view|go\s+to|tap(?:\s+on)?)\s+(?:on\s+)?(?:the\s+)?{ORD}\s+{NOUN}"
       r"(?:\s+(?:that\s+(?:mentions|contains|says|has|is\s+from|is\s+by|is\s+for)|from|by|for|containing|with|mentioning|matching|named|called|titled|about|of)\s+(?P<x>.+))?$")
def _nth(m):
    x = m["x"]
    return [f'Click the {m["ord"]} result containing "{_q(x)}"' if x else f'Click the {m["ord"]} result']


@macro(rf"^(?:open|click(?:\s+on)?|pick|select|choose|view|tap(?:\s+on)?)\s+(?:on\s+)?(?:the\s+)?{ORD}\s+(?P<x>.+?)\s+{NOUN}$")
def _nth2(m):
    return [f'Click the {m["ord"]} result containing "{_q(m["x"])}"']


@macro(r"^(?:add|put)\s+(?:(?P<n>\d+|\$\{[^}]+\}|\{\{[^}]+\}\})\s+(?:of\s+(?:them|those|these|it)\s+|items?\s+|units?\s+|pieces?\s+)?)?(?:it|this|that|them|the\s+(?:product|item|first\s+one)|one)?\s*"
       r"(?:to|in|into)\s+(?:the\s+|my\s+)?(?:shopping\s+)?(?:cart|basket|bag|trolley)$")
def _add_cart(m):
    return ([f'Select "{m["n"]}" from Quantity'] if m["n"] else []) + ["Click Add to Cart button"]


@macro(r"^(?:go\s+to|open|view|check|show)\s+(?:the\s+|my\s+)?(?:shopping\s+)?(?:cart|basket|bag|trolley)$")
def _cart(m):
    return ["Click the Cart link"]


@macro(r"^(?:accept|allow|agree\s+to)\s+(?:all\s+)?(?:the\s+)?cookies?(?:\s+(?:banner|popup|prompt))?(?:\s+if\s+\w+)?$")
def _cookies(m):
    return ["Click Accept cookies if shown"]


@macro(r"^(?:close|dismiss)\s+(?:the\s+|any\s+)?(?:popup|pop-up|modal|banner|overlay|dialog|notification)s?(?:\s+if\s+\w+)?$")
def _close_popup(m):
    return ["Click Close if shown"]


@macro(r"^(?:set|choose|select|pick|change)\s+(?:the\s+)?(?P<f>quantity|qty|size|colou?r|country|state|language|currency|variant|sort(?:\s+order)?|category)\s+(?:to|as|=|of)?\s*(?P<v>.+)$")
def _set_field(m):
    return [f'Select "{_q(m["v"])}" from {m["f"]}']


@macro(r"^(?:choose|select|pick)\s+(?P<v>.+?)\s+(?:as|for)\s+(?:the\s+|my\s+)?(?P<f>[\w\s-]{2,30})$")
def _choose_as(m):
    return [f'Select "{_q(m["v"])}" from {m["f"]}']


@macro(r"^(?:set|change|update)\s+(?:the\s+)?(?P<f>.+?)\s+(?:to|as|=)\s+(?P<v>.+)$")
def _set_any(m):
    return [f'Set {m["f"]} to "{_q(m["v"])}"']


NEG = r"(?:does\s*n[o']?t|do\s*n[o']?t|did\s*n[o']?t|should\s*n[o']?t|must\s*n[o']?t|can\s*n[o']?t|cannot|will\s+not|won't|never|no\s+longer|is\s+not|isn't|are\s+not|aren't)"


@macro(r"^(?:make\s+sure|ensure|check|confirm|verify|validate|expect|assert)\s+(?:that\s+)?(?:(?:the|my)\s+)?"
       r"(?:(?:web\s*)?page\s+|screen\s+|site\s+|it\s+|there\s+|you\s+|we\s+|i\s+|the\s+user\s+|user\s+)?" + NEG +
       r"\s+(?:shows?|displays?|contains?|has|have|includes?|lists?|says?|reads?|see|show\s+up|appears?)\s+(?P<x>.+)$")
def _verify_not(m):
    return [f'Verify page does not contain "{_clean_expect(m["x"])}"']


@macro(r"^(?:i|you|we|user|the\s+user|it)?\s*(?:should|must|will|can)\s*n[o']?t\s+(?:see|get|find|be\s+shown)\s+(?P<x>.+)$")
def _should_not_see(m):
    return [f'Verify page does not contain "{_clean_expect(m["x"])}"']


@macro(r"^(?:not|never|n't)\s+(?:be\s+able\s+to\s+)?(?:see|get|find|be\s+shown|show|display|contain|include|have)\s+(?P<x>.+)$")
def _not_see(m):      # what is left of "I should not see X" / "it should not show X" after the lead-in is dropped
    return [f'Verify page does not contain "{_clean_expect(m["x"])}"']


@macro(r"^(?P<x>[\"“'].+?[\"”'])\s+(?:should|must)\s*(?:n[o']?t|never)\s+(?:be\s+)?(?:visible|shown|displayed|appear|show\s+up)$")
def _quoted_not_visible(m):
    return [f'Verify page does not contain "{_q(m["x"])}"']


@macro(r"^(?:make\s+sure|ensure|check|confirm|verify|validate|expect|assert)\s+(?:that\s+)?"
       r"(?!(?:the\s+)?(?:page\s+)?(?:url|address|title|alert|dialog|popup|prompt|confirm)\b)(?:(?:the|my|a|an)\s+)?(?P<subj>(?:[\w'-]+\s+){0,4}?)"
       r"(?:shows?|displays?|contains?|has|have|includes?|lists?|says?|reads?|is\s+showing|are\s+showing|"
       r"should\s+(?:show|display|contain|have|say|list|include|read)|can\s+see|see|appears?)\s+(?P<x>.+)$")
def _verify(m):
    subj = (m["subj"] or "").strip()
    if re.search(NEG, " " + subj + " ", re.I) or re.search(r"\b(?:not|never|no)\b", subj, re.I):
        return None                      # a negation must never be read as a positive check
    if subj and not re.fullmatch(r"(?:web\s*)?(?:page|screen|site|it|there|you|we|i|they|cart|basket|result|results|"
                                 r"everything|this|that|window|app|dashboard|ui)", subj, re.I):
        # a specific element is named ("the username field contains …"): check that element's text/value
        return [f'Verify the {subj} contains "{_clean_expect(m["x"])}"']
    return [f'Verify page contains "{_clean_expect(m["x"])}"']


@macro(r"^(?:i|you|we|user|it)\s+(?:should|must|will|can)\s+(?:see|get|find|be\s+shown|be\s+on)\s+(?P<x>.+)$")
def _should_see(m):
    return [f'Verify page contains "{_clean_expect(m["x"])}"']


@macro(r"^(?:see|should\s+see|expect\s+to\s+see)\s+(?P<x>.+)$")
def _see(m):
    return [f'Verify page contains "{_clean_expect(m["x"])}"']


@macro(r"^(?P<x>[\"“'].+?[\"”'])\s+(?:should|must)\s+(?:be\s+)?(?:visible|shown|displayed|appear|show\s+up)$")
def _quoted_visible(m):
    return [f'Verify page contains "{_q(m["x"])}"']


@macro(r"^wait\s+(?:for\s+)?(?:the\s+)?(?:page|site|results?)\s+(?:to\s+)?(?:load|finish|appear)(?:ing)?$")
def _wait_load(m):
    return ["Wait 1 seconds"]


@macro(r"^(?:open|go\s+to|visit|navigate\s+to)\s+(?:the\s+)?(?P<x>[\w\s]+?)\s+(?:page|menu|tab|section)$")
def _open_section(m):
    return [f"Click {m['x']}"]


CLICKY = re.compile(r"^(?:sign\s*out|log\s*out|logout|proceed|continue|buy|purchase|checkout|check\s*out|book|save|submit|apply|remove|delete|"
                    r"expand|collapse|sort|next|previous|place\s+(?:the\s+)?order|confirm\s+(?:the\s+)?order|subscribe|register|sign\s*up|"
                    r"start|begin|finish|done|cancel|undo|retry|download|share|print|reset)\b", re.I)


def guess(clause: str) -> list[str] | None:
    """Last resort for short button-like phrases ("sign out", "proceed to checkout"): click the control with that text."""
    words = clause.split()
    if 1 <= len(words) <= 6 and CLICKY.match(clause) and not re.search(r"\b(?:that|if|whether|should)\b", clause, re.I):
        return [f'Click "{clause}"']
    return None


def _clean_expect(x: str) -> str:
    x = x.strip().rstrip(".")
    x = re.sub(r"^(?:a|an|the)\s+", "", x, flags=re.I)
    x = re.sub(r"\s+(?:message|text|label|on\s+(?:the\s+)?(?:page|screen)|in\s+(?:the\s+)?(?:cart|basket|list|results))$", "", x, flags=re.I)
    return _q(x)


def _mask_quotes(text: str) -> tuple[str, list[str]]:
    quoted: list[str] = []

    def hide(m):
        quoted.append(m.group(0))
        return f"\x00{len(quoted) - 1}\x00"

    return re.sub(r'"[^"\n]*"|“[^”\n]*”|\'[^\'\n]{1,80}\'(?=[\s,.;!?]|$)', hide, text), quoted


def _unmask(s: str, quoted: list[str]) -> str:
    return re.sub(r"\x00(\d+)\x00", lambda m: quoted[int(m.group(1))], s)


def clauses(text: str) -> list[str]:
    """Split prose into single-action clauses (quotes and URLs are never split)."""
    masked, quoted = _mask_quotes(text)
    # protect URLs / domains / ${vars} so their dots don't end sentences
    urls: list[str] = []
    masked = re.sub(r"(?:https?://|www\.)\S+|\$\{[^}]+\}|\b[\w-]+(?:\.[\w-]+)+(?:/\S*)?",
                    lambda m: urls.append(m.group(0).rstrip(".,")) or f"\x01{len(urls) - 1}\x01" + m.group(0)[len(m.group(0).rstrip('.,')):], masked)
    out = []
    for line in masked.splitlines():
        line = re.sub(r"^\s*(?:\d+[.)]|[-*•]|step\s*\d+\s*[:.)-])\s*", "", line, flags=re.I)
        for part in _SPLIT.split(line):
            part = (part or "").strip().strip(",").rstrip(".!").strip()
            if not part:
                continue
            for _ in range(3):
                part = _LEAD.sub("", part).strip()
            part = re.sub(r"\s*,?\s*please$", "", part, flags=re.I)
            part = re.sub(r"\x01(\d+)\x01", lambda m: urls[int(m.group(1))], part)
            out.append(_unmask(part, quoted))
    return [p for p in out if p]


def expand(clause: str) -> list[str] | None:
    """Rewrite one natural clause into step sentences, or None if no macro applies."""
    c = clause.strip()
    for rx, fn in MACROS:
        m = rx.match(c)
        if m:
            res = fn(m)
            if res:
                return res
    return None
