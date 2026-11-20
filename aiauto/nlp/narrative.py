"""Business-language paragraphs -> step sentences, without an LLM.

Real test descriptions are paragraphs written by analysts, not by testers: third person ("The visitor lands on the sign
up page and enters ..."), present tense, no quotes, several fields in one sentence, expected results in business words
("the system should confirm that the account was created successfully"), and background sentences ("As a customer I want
to ..."). The step rules (rules.py) want imperative sentences with quoted values, so this module is the translation:

    normalize()  sentence level: drops narrative, removes the subject, puts verbs in the imperative
    rewrite()    clause level: first page -> Open ${base_url}; "enters email x, username y, age 30" -> one Type step per
                 field; "the system should confirm that ..." -> Verify page contains "..."; "submits the form" -> a click

What it cannot know it says so (Context.notes) instead of guessing silently: the page address, wording of expected
messages that was paraphrased, checks with no concrete text. Values typed into password-like fields are lifted out of the
text into masked test data (${password}), so a secret written in a paragraph never lands in a script or report.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------------- verbs
_BASE = ("open navigate go visit land launch browse click tap press hit type enter fill input provide supply specify select choose "
         "pick check uncheck tick untick hover scroll wait verify validate confirm search look find add upload attach drag drop "
         "accept dismiss close switch refresh reload submit set clear see view apply remove delete increase decrease proceed "
         "continue buy purchase checkout book save sort filter expand collapse register use start press log sign key fill "
         "type choose leave select place order pay transfer send create update edit change modify download print share post schedule cancel approve reject assign invite activate deactivate").split()
_PHRASAL = {"log": "in|out|on", "sign": "in|out|up", "fill": "in|out", "key": "in", "look": "for|at", "land": "on", "go": "to|back",
            "switch": "to", "scroll": "to|down|up"}


def _third(v: str) -> str:
    if v.endswith(("s", "x", "z", "ch", "sh", "o")):
        return v + "es"
    if v.endswith("y") and v[-2:-1] not in "aeiou":
        return v[:-1] + "ies"
    return v + "s"


THIRD = {_third(v): v for v in dict.fromkeys(_BASE)}
THIRD.update({"does": "do", "goes": "go", "logs": "log", "signs": "sign", "keys": "key"})
_VERB3 = "|".join(sorted(THIRD, key=len, reverse=True))
_VERB = "|".join(sorted(set(_BASE), key=len, reverse=True))

SUBJECT = (r"(?:(?:the|a|an|that|this|each|every)\s+)?(?:(?:[a-z-]+)\s+){0,2}?"
           r"(?:users?|visitors?|customers?|shoppers?|buyers?|testers?|admins?|administrators?|members?|guests?|employees?|"
           r"managers?|reviewers?|clients?|persons?|applicants?|patients?|students?|candidates?|operators?|agents?|someone|somebody|"
           r"he|she|they|i|we|you)")
_SUBJ_VERB = re.compile(rf"^(?:(?:then|and|also|now|next|finally|when|once|after\s+that|given|whenever|if)\s*,?\s+)*{SUBJECT}\s+"
                        rf"(?:(?:then|also|now|next|finally|first|again|just)\s+)?(?=(?:{_VERB3}|{_VERB})\b)", re.I)

# background sentences: a user story, a title, a precondition label
_CONTEXT = [
    re.compile(r"^as\s+(?:an?|the)\s+[^,]{2,60}?,?\s+i\s+(?:want|need|would\s+like|wish|can|should)\b", re.I),
    re.compile(r"^(?:in\s+order\s+to|so\s+that|because|this\s+(?:test|scenario|case)|the\s+(?:purpose|goal|objective|aim)|the\s+test\s+(?:case|scenario)|"
               r"scenario|test\s*case|feature|background|story|epic|objective|purpose|description|summary|note|precondition|pre-?requisite)s?\b", re.I),
    re.compile(r"^(?:verify|ensure|check|confirm|validate|test)\s+that\b.+\b(?:when|if|whenever|for\s+(?:an?|the)?)\b", re.I),   # a title with a condition
]
_STORY_LABEL = re.compile(r"^(?:scenario|test\s*case|feature|description|objective|purpose|summary|steps?|expected(?:\s+results?)?|"
                          r"precondition|pre-?requisites?|actions?|test\s+steps?|given|when|then|and|but)\s*[:\-]\s*", re.I)

# --------------------------------------------------------------------------------------------- fields
# canonical label -> the words a business paragraph uses for it
FIELDS: dict[str, str] = {
    "Email": r"e-?mail(?:\s+(?:address|id))?",
    "Username": r"user\s*name|user\s*id|login(?:\s+(?:id|name))?|login",
    "Password": r"pass\s*word|passcode|pin",
    "First name": r"first\s+name|given\s+name|forename",
    "Last name": r"last\s+name|surname|family\s+name",
    "Full name": r"full\s+name|name",
    "Age": r"age",
    "Phone": r"(?:phone|mobile|telephone|contact)(?:\s+(?:number|no\.?))?",
    "Address": r"(?:street\s+|billing\s+|shipping\s+|postal\s+)?address(?:\s+line\s*\d)?",
    "City": r"city|town",
    "Zip": r"zip(?:\s*code)?|postal\s*code|post\s*code|pin\s*code",
    "State": r"state|province|region",
    "Country": r"country",
    "Date of birth": r"date\s+of\s+birth|birth\s*date|dob",
    "Card number": r"(?:credit\s+|debit\s+)?card\s+number|card\s+no\.?",
    "CVV": r"cvv|cvc|security\s+code",
    "Company": r"company(?:\s+name)?|organi[sz]ation",
    "Quantity": r"quantity|qty",
    "Message": r"message|comments?|feedback|description|notes?|subject|title",
    "Search": r"search\s+(?:term|text|keyword|query)",
}
SECRET_FIELDS = {"Password", "CVV", "Card number"}
_FIELD_ALT = "|".join(f"(?P<f{i}>{rx})" for i, (_, rx) in enumerate(FIELDS.items()))
_QUAL = r"(?:an?|the|his|her|their|my|our|valid|invalid|correct|incorrect|wrong|new|existing|registered|unique|random|proper|same|some)"
_ITEM = re.compile(rf"^(?:{_QUAL}\s+)*(?:{_FIELD_ALT})(?:\s+(?:field|box|value|input))?(?:\s+(?:of|as|is|being|set\s+to|like|equal\s+to)\s+|\s*[:=]\s*|\s+)(?P<v>.+)$", re.I)
_ITEM_REV = re.compile(rf"^(?P<v>\S+)\s+(?:as|for|in|into)\s+(?:the\s+)?(?:{_FIELD_ALT})(?:\s+(?:field|box))?$", re.I)
_ENTER = re.compile(r"^(?:enter|provide|supply|specify|fill\s+in|fill|type|input|key\s+in|use|give|put|set)\s+(?:in\s+)?"
                    r"(?:the\s+following\s+|these\s+|his\s+|her\s+|their\s+|valid\s+|invalid\s+|correct\s+|incorrect\s+)*(?:details|data|credentials|information|values)?\s*:?\s*(?P<rest>.+)$", re.I)
_ON_FORM = re.compile(r"\s+(?:on|in|into|at)\s+(?:the\s+)?[\w\s-]{1,30}?\s+(?:form|page|screen|section|dialog|popup|modal)$", re.I)

_PAGE_WORD = r"(?:page|screen|site|website|web\s*site|application|app|portal|url|home\s*page|homepage|login|landing\s+page)"
_URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|in|co|dev|app|ai)(?:/\S*)?\b", re.I)
_NAV = re.compile(rf"^(?:open|navigate\s+to|go\s+to|visit|launch|land\s+on|browse\s+to|arrive\s+(?:at|on)|access|load)\s+(?:the\s+|our\s+|that\s+)?(?P<what>.+?)\s*$", re.I)
_AM_ON = re.compile(rf"^(?:{SUBJECT}\s+(?:is|are|am)|am|is|are)\s+(?:currently\s+|already\s+)?(?:on|at|in)\s+(?:the\s+)?(?P<what>.+?)\s*$", re.I)
_SUBMIT = re.compile(r"^(?P<v>submit|save)\s+(?:the\s+|this\s+|that\s+|his\s+|her\s+|their\s+|my\s+)?(?:[\w'-]+\s*){0,3}$", re.I)
_PARTICIPLE_NOUN = r"(?:account|user|order|record|profile|entry|booking|registration|payment|item|ticket|message|request)"
_FILLER_ADV = re.compile(r"\b(?:successfully|correctly|properly|already|automatically|immediately|clearly|now|also|then)\b", re.I)
_AUX = re.compile(r"\b(?:was|were|is|are|been|has|have|had|being|gets|got|(?:will|shall)\s+be)\b", re.I)   # "must be between" keeps its be
_GENERIC_WORDS = {"product", "products", "price", "prices", "name", "names", "details", "detail", "list", "results", "result", "items", "item",
                  "information", "info", "data", "text", "message", "messages", "page", "content", "section", "value", "values", "total",
                  "amount", "balance", "status", "button", "buttons", "link", "links", "options", "option", "table", "form", "image", "images"}
_GENERIC_SUBJ = re.compile(r"^(?:the\s+)?(?:system|application|app|site|website|page|screen|ui|portal|platform|software|server|result|results|message|"
                           r"user\s+interface|dashboard)$", re.I)


@dataclass
class Context:
    """What the pass learned about the paragraph besides its steps."""
    ignored: list[str] = field(default_factory=list)      # background sentences left out
    notes: list[str] = field(default_factory=list)        # things the reader should check
    data: dict[str, str] = field(default_factory=dict)    # values lifted out of the text (key -> value)
    sensitive: list[str] = field(default_factory=list)
    navigated: bool = False


def _q(v: str) -> str:
    return v.replace('"', "'").strip()


def _strip_quotes(v: str) -> str:
    v = v.strip().strip(".,;").strip()
    m = re.fullmatch(r"[\"“'‘`](.*)[\"”'’`]", v)
    return (m.group(1) if m else v).strip()


# --------------------------------------------------------------------------------------------- sentence level
def _sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:\d+[.)]|[-*•]|step\s*\d+\s*[:.)-])\s*", "", line.strip(), flags=re.I)
        if not line:
            continue
        # sentence end: . ! ? followed by a capital, not inside a URL / e-mail / number / "e.g."
        parts = re.split(r"(?<=[a-z0-9\)\"'])(?<!\be\.g)(?<!\bi\.e)(?<!\betc)[.!?]\s+(?=[A-Z\"'])", line)
        out.extend(p.strip() for p in parts if p.strip())
    return out


_PLACE = re.compile(rf"^(?:at|on|in|during|from|within|under)\s+(?:the\s+)?[\w\s-]{{1,30}}?\s*,?\s+(?={SUBJECT}\s+(?:{_VERB3}|{_VERB})\b)", re.I)


def _lemma(sentence: str) -> str:
    """Third person -> imperative: drop the subject, put its verb and the verbs after "and" / "then" in the base form."""
    s = _PLACE.sub("", sentence.strip().rstrip(".!"))       # "At checkout the customer enters ..." -> "The customer enters ..."
    m = _SUBJ_VERB.match(s)
    changed = bool(m)
    if m:
        s = s[m.end():]
    first = re.match(rf"^({_VERB3})\b", s, re.I)
    if first:                                         # "Opens the login page" (subject left out)
        s = THIRD[first.group(1).lower()] + s[first.end():]
        changed = True
    if not changed:
        return sentence
    return _connectors(s)


def _connectors(s: str) -> str:
    return re.sub(rf"((?:,\s*(?:and\s+)?(?:then\s+)?|\s+and\s+(?:then\s+)?|\s+then\s+|;\s*))({_VERB3})\b",
                  lambda m: m.group(1) + THIRD[m.group(2).lower()], s, flags=re.I)


_EXPECT_START = re.compile(r",\s*(?:and\s+)?(?=(?:the|a|an|no|any|all|each)\b[^,]{1,80}?\b(?:should|must|will|shall|is|are|was)\b)", re.I)


def normalize(text: str, ctx: Context) -> str:
    """Narrative -> imperative prose (one sentence per line). Background sentences go to ctx.ignored."""
    out = []
    for sent in _sentences(text):
        body = _STORY(sent)
        if body is None:
            ctx.ignored.append(sent.rstrip("."))
            continue
        out.extend(x for x in _EXPECT_START.split(_lemma(body)) if x.strip())     # an action, then what should follow
    return "\n".join(out)


def _STORY(sent: str) -> str | None:
    """The sentence without its label / Gherkin keyword, or None when it is background (a user story, a title)."""
    s = sent.strip()
    if re.match(r"^(?:scenario(?:\s+outline)?|test\s*case|feature|story|epic|summary|description|objective|purpose|background|title|name)\s*(?:#?\d+)?\s*[:\-]", s, re.I):
        return None                                   # a title or a framing label: nothing to do
    if re.match(r"^(?:steps?|expected(?:\s+results?)?|precondition|pre-?requisites?|actions?|test\s+steps?)\s*[:\-]", s, re.I):
        s = re.sub(r"^[^:\-]+[:\-]\s*", "", s)
        if not s:
            return None
    # Given / When / Then / And / But: keep the sentence, drop the keyword ("Then the user should ..." stays an expectation)
    s = re.sub(r"^(?:given|when|and|but)\s*[:,]?\s+(?=\S)", "", s, flags=re.I)
    s = re.sub(r"^then\s*[:,]?\s+(?!(?:the\s+)?\w+\s+(?:should|must|will))", "", s, flags=re.I)
    if any(rx.match(s) for rx in _CONTEXT):
        return None
    return s


# --------------------------------------------------------------------------------------------- clause level
def _split_expectations(c: str) -> list[str]:
    parts = re.split(r"\s*,?\s+(?:and|but|while|plus)\s+(?=(?:the|a|an|no|any|all|each|an?\s+error)\b[^,]*?(?:\b(?:should|must|will|shall)\b|"
                     r"\b(?:is|are)\s+(?:also\s+)?(?:shown|displayed|visible|present|listed|available|created|saved|opened|loaded|seen)\b))", c, flags=re.I)
    return [p for p in parts if p.strip()]


def _phrase(x: str) -> str:
    """'the account was created successfully' -> 'account created'."""
    x = re.sub(r"^(?:that|how|if|whether)\s+", "", x.strip().strip(".,"), flags=re.I)
    x = re.sub(r"^(?:the|an?|his|her|their)\s+", "", x, flags=re.I)
    x = re.sub(r"^(?:text|words?|phrase|label|caption|heading|header|title|wording|string)\s+(?=\S)", "", x, flags=re.I)
    x = _FILLER_ADV.sub(" ", x)
    x = _AUX.sub(" ", x)
    return re.sub(r"\s+", " ", x).strip()


def _quoted_or_phrase(x: str) -> str:
    m = re.search(r"[\"“]([^\"”]+)[\"”]", x)
    return m.group(1) if m else _phrase(x)


_EXPECT = [
    # "an error message stating that age must be between 18 and 99 must be displayed"
    (re.compile(r"^(?:an?\s+|the\s+)?(?:(?:error|success|warning|validation|confirmation|alert|info)\s+)?messages?\s+"
                r"(?:stating|saying|reading|indicating|that\s+(?:says|states|reads)|like|with|showing|displaying|of)\s+(?:that\s+)?(?P<x>.+?)\s+"
                r"(?:must|should|will|shall)\s+(?:also\s+)?(?:be\s+)?(?:displayed|shown|visible|presented|appear|returned|seen)$", re.I), "msg"),
    # "no account should be created"
    (re.compile(rf"^(?:no|not\s+any|neither)\s+(?P<x>{_PARTICIPLE_NOUN})\s+(?:should|must|will|shall)\s+(?:be\s+)?(?P<v>created|saved|registered|submitted|placed|added|stored|sent|booked)$", re.I), "none"),
    # "the system should confirm that the account was created", "the page should show welcome back"
    (re.compile(r"^(?P<s>.+?)\s+(?:should|must|will|shall)\s+(?:also\s+)?(?:confirm|show|display|indicate|inform|notify|tell|say|read|contain|list|include|have|present|give|return)s?"
                r"\s+(?:the\s+user\s+|that\s+)?(?:that\s+)?(?P<x>.+)$", re.I), "says"),
    # "the dashboard greets the user with a welcome message for alice"
    (re.compile(r"^(?P<s>.+?)\s+(?:greets?|welcomes?|shows?|displays?|presents?|gives?)\s+(?:the\s+user\s+|him\s+|her\s+|them\s+)?(?:with\s+)?(?P<x>(?:an?\s+)?[\w\s-]*?message.*|(?:an?\s+)?welcome.*)$", re.I), "says"),
    # "the Laptops page is shown", "the cart page is displayed"
    (re.compile(r"^(?:the\s+)?(?P<x>.+?)\s+(?:page|screen|section|view|tab)\s+(?:is|gets|will\s+be|should\s+be|must\s+be|opens?|loads?)\s+(?:shown|displayed|visible|opened|loaded|presented|open|there)?$", re.I), "page"),
    # "the product name should be displayed" / "5 results are shown"
    (re.compile(r"^(?P<x>.+?)\s+(?:should|must|will|shall|is|are)\s+(?:also\s+)?(?:be\s+)?(?:displayed|shown|visible|present|listed|available|appear(?:ing)?|there|seen|returned)$", re.I), "shown"),
    # "the user is logged in", "the order is placed"
    (re.compile(r"^(?:the\s+)?(?P<x>[\w\s-]+?)\s+(?:is|are|was|has\s+been|have\s+been|gets?)\s+(?P<v>created|saved|registered|submitted|placed|added|logged\s+in|signed\s+in|updated|deleted|removed|sent|booked|confirmed)(?:\s+successfully)?$", re.I), "done"),
]


class _Orig:
    """A regex match made on masked text, read back from the original: groups are sliced out of the real string."""
    def __init__(self, m: re.Match, original: str):
        self.m, self.o = m, original

    def __getitem__(self, name):
        return None if self.m.start(name) < 0 else self.o[self.m.start(name):self.m.end(name)]


def _expectation(clause: str, ctx: Context) -> list[str] | None:
    # quoted text is data, never grammar: "Age must be 18-99" must not be read as a "must" statement, yet a quoted value is the
    # exact text to check. The patterns run on a copy with the quoted words blanked out; the values come from the original.
    c = clause.strip().rstrip(".")
    masked = re.sub(r'"([^"]*)"|“([^”]*)”', lambda m: m.group(0)[0] + "x" * (len(m.group(0)) - 2) + m.group(0)[-1], c)
    return _expectation_masked(c, ctx, masked)


def _expectation_masked(c: str, ctx: Context, masked: str) -> list[str] | None:
    lead = re.match(r"^(?:then|and|also|finally|after\s+that|afterwards)\s*,?\s+", c, re.I)
    if lead:
        c, masked = c[lead.end():], masked[lead.end():]
    for rx, kind in _EXPECT:
        mm = rx.match(masked)
        if not mm:
            continue
        m = _Orig(mm, c)
        if kind == "msg":
            phrase = _phrase(m["x"])
            ctx.notes.append(f'Check the wording: the text says the message states "{phrase}"; use the exact message the page shows.')
            return [f'Verify page contains "{_q(phrase)}"']
        if kind == "none":
            return [f'Verify page does not contain "{_q(m["x"].lower())} created"']
        if kind == "says":
            x = m["x"]
            mm = re.match(r"^(?:an?\s+)?(?P<k>[\w-]+)\s+message\s+(?:for|to|saying|with|containing)\s+(?P<n>.+)$", x, re.I)
            if mm:
                return [f'Verify page contains "{_q(mm["k"].lower())}"', f'Verify page contains "{_q(_strip_quotes(mm["n"]))}"']
            mm = re.match(r"^(?:an?\s+)?(?P<k>[\w-]+)\s+message$", x, re.I)
            if mm:
                return [f'Verify page contains "{_q(mm["k"].lower())}"']
            phrase = _quoted_or_phrase(x)
            if not phrase:
                return None
            words = re.findall(r"[a-z]+", phrase.lower())
            if not re.search(r"[\"“]|\d|@", x) and words and all(w in _GENERIC_WORDS for w in words):
                ctx.notes.append(f'Not checked: "{c}" names no concrete text. Add the text the page should show.')
                return []
            if not re.search(r"[\"“]", x):
                ctx.notes.append(f'Check the wording: "{phrase}" is how the text puts it; edit it to the exact text the page shows.')
            return [f'Verify page contains "{_q(phrase)}"']
        if kind == "page":
            x = _phrase(m["x"])
            return [f'Verify page contains "{_q(x)}"'] if x and not _GENERIC_SUBJ.match(x) else None
        if kind == "shown":
            x = _quoted_or_phrase(m["x"])
            concrete = re.search(r"[\"“]|\d|[A-Z]{2,}|@", m["x"]) or re.search(r"\b[A-Z][a-z]+\b", m["x"][1:])
            if concrete and not _GENERIC_SUBJ.match(x):
                if not re.search(r"[\"“]", m["x"]):
                    ctx.notes.append(f'Check the wording: "{x}" is how the text puts it; edit it to the exact text the page shows.')
                return [f'Verify page contains "{_q(x)}"']
            ctx.notes.append(f'Not checked: "{c}" names no concrete text. Add the text the page should show.')
            return []
        if kind == "done":
            x, v = _phrase(m["x"]), re.sub(r"\s+in$", "", m["v"].strip().lower())
            if v in ("logged in", "signed in"):
                ctx.notes.append('Check the wording: logged-in state is checked as a page without the sign-in form is not possible by text; '
                                 "add what the page shows after sign-in (a name, a welcome text).")
                return []
            return [f'Verify page contains "{_q(x)} {v}"']
    if re.match(r"^(?P<x>.+?)\s+(?:should|must|will|shall)\s+(?:not\s+|never\s+)?(?:be|have|match|equal|exceed|remain|stay|contain|show|display)\b.*$", masked, re.I):
        ctx.notes.append(f'Not checked: "{c}" names no text the page shows. Add what the page should show (or not show).')
        return []
    return None


def _field_label(m: re.Match) -> str:
    for i, label in enumerate(FIELDS):
        if m.groupdict().get(f"f{i}"):
            return label
    return "Field"


def _gen_secret(n: int) -> str:
    return ("Aa1!" + "x" * max(n, 8))[: max(n, 8)]


def _entry(rest: str, ctx: Context) -> list[str] | None:
    """'a valid email address a@b.com, a username testuser, age 30 and a password of at least 8 characters' -> Type steps."""
    rest = _ON_FORM.sub("", rest.strip().rstrip("."))
    items = [i.strip() for i in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+|;\s*", rest) if i.strip()]
    steps, hit, skipped = [], 0, []
    for it in items:
        it = re.sub(r"^(?:and|also|then)\s+", "", it, flags=re.I)
        m = _ITEM.match(it)
        label, val = None, None
        if m:
            label, val = _field_label(m), m["v"]
        else:
            m = _ITEM_REV.match(it)
            if m:
                label, val = _field_label(m), m["v"]
        if label is None:
            g = re.match(r"^(?:the\s+|a\s+|an\s+)?(?P<f>[a-z][a-z -]{1,30}?)\s+(?P<v>[\w@.+#$%/-]*\d[\w@.+#$%/-]*|[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)*|[\w.+-]+@[\w.-]+)$", it)
            if g and len(g["f"].split()) <= 3 and not re.search(r"\b(?:and|or|to|the|of|in|on)\b", g["f"]):
                label, val = g["f"].strip().capitalize(), g["v"]
        if label is None:
            skipped.append(it)
            continue
        hit += 1
        val = _strip_quotes(val)
        atleast = re.match(r"^(?:at\s+least|min(?:imum)?(?:\s+of)?|more\s+than)\s+(\d+)\s+(?:chars?|characters?|digits?|letters?)", val, re.I)
        if atleast or re.match(r"^(?:valid|invalid|correct|any|some|random|a\s+\w+)$", val, re.I) or not val:
            if label in SECRET_FIELDS:
                key = label.lower().replace(" ", "_")
                n = int(atleast.group(1)) if atleast else 8
                ctx.data.setdefault(key, _gen_secret(n)); ctx.sensitive.append(key)
                ctx.notes.append(f"No {label.lower()} was given: a test value was made up ({key}, masked). Replace it under Test data if needed.")
                steps.append(f"Type ${{{key}}} into {label}")
            else:
                ctx.notes.append(f'No value was given for {label.lower()}: "{it}". Add it under Test data and use ${{{label.lower().replace(" ", "_")}}}.')
            continue
        if label in SECRET_FIELDS:
            key = label.lower().replace(" ", "_")
            ctx.data[key] = val
            if key not in ctx.sensitive:
                ctx.sensitive.append(key)
            steps.append(f"Type ${{{key}}} into {label}")
        else:
            steps.append(f'Type "{_q(val)}" into {label}')
    if hit:
        for it in skipped:
            ctx.notes.append(f'Not typed: "{it}" (in "{rest}") has no field name or value the rules could use.')
    return steps if hit else None


def _first_nav(what: str, ctx: Context, clause: str) -> list[str] | None:
    w = what.strip()
    url = _URL.search(w)
    if url:
        u = url.group(0).rstrip(".,")
        ctx.navigated = True
        return [f"Open {u if u.startswith(('http', 'www')) else 'https://' + u}"]
    if re.search(_PAGE_WORD + r"$", w, re.I) or re.search(r"\b(?:site|website|application|app|portal)\b", w, re.I):
        if not ctx.navigated:
            ctx.navigated = True
            ctx.notes.append(f'"{clause}" gives no address: it opens ${{base_url}}. Set base_url under Test data to the page\'s address (add its path if it is not the home page).')
            return ["Open ${base_url}"]
        return None            # later "go to the X page": left to the generic rules (a click on X)
    return None


def rewrite(clause: str, ctx: Context) -> list[str] | None:
    """One clause -> step sentences. None: not handled here (the ordinary rules take it). []: nothing to do for it."""
    c = clause.strip().rstrip(".,")
    if not c:
        return []
    c = re.sub(r"^(?:then|and|also|next|finally|now|first|after\s+that|afterwards)\s*,?\s+", "", c, flags=re.I)
    c = re.sub(r"^after\s+(?:a\s+)?(?:successful\s+)?(?:log\s*in|login|sign\s*in|submission|registration|checkout|payment|search|that)\s*,?\s+", "", c, flags=re.I)
    c = re.sub(r"^(?:in\s+the\s+end|at\s+the\s+end|on\s+success|if\s+successful)\s*,?\s+", "", c, flags=re.I)
    if re.search(r"\$\{[^}]*\}|\{\{[^}]*\}\}", c):
        return None                                   # a ${placeholder} means the author structured it: the ordinary rules read it
    if re.match(r'^[^"]*"[^"]*"', c) and re.match(r"^(?:click|type|press|select|open|verify|wait|hover|check|uncheck|enter|fill|input)\b", c, re.I) \
            and len(re.findall(r'"[^"]*"', c)) < 2:
        return None                                   # already a quoted, imperative step
    parts = _split_expectations(c)
    if len(parts) > 1:
        out: list[str] = []
        for p in parts:
            r = rewrite(p, ctx)
            if r is None:
                return None
            out.extend(r)
        return out
    m = _AM_ON.match(c)
    if m:                                             # "I am on the login page" (a precondition)
        r = _first_nav(m["what"], ctx, clause)
        return r if r is not None else []
    m = _NAV.match(c)
    if m:
        r = _first_nav(m["what"], ctx, clause)
        if r is not None:
            return r
    m = _ENTER.match(c)
    if m:
        r = _entry(m["rest"], ctx)
        if r is not None:
            return r
    e = _expectation(c, ctx)
    if e is not None:
        return e
    m = re.match(r"^(?:log\s*in|login|sign\s*in)(?:\s+(?:to|into|on)\s+(?:the\s+|our\s+)?[\w\s-]+?)?(?:\s+as\s+(?:an?\s+|the\s+)?[\w\s-]+?)?(?:\s+successfully)?$", c, re.I)
    if m:                                             # "The administrator signs in": no credentials were given
        ctx.notes.append(f'"{clause.strip()}" gives no credentials: add username and password under Test data (${{username}}, ${{password}}).')
        return ["Type ${username} into the username or email field", "Type ${password} into the password field", "Click the Log in or Sign in button"]
    m = _SUBMIT.match(c)
    if m:
        return ["Click Save"] if m["v"].lower() == "save" else ["Click the submit button"]
    m = re.match(r"^(?P<v>open|click(?:\s+on)?|pick|select|choose|view)\s+(?P<x>(?:the\s+)?(?:first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th))\s+.+?)"
                 r"\s+(?:listed|displayed|shown|available|below|above|there|on\s+the\s+list)$", c, re.I)
    if m:
        return [f"{m['v']} {m['x']}"]
    m = re.match(r"^(?P<v>check|tick|untick|uncheck|enable|disable|toggle|turn\s+on|turn\s+off)\s+(?:the\s+|on\s+)?(?P<x>.+?)\s+(?:option|checkbox|check\s*box|toggle|switch|setting)$", c, re.I)
    if m:
        verb = {"tick": "Check", "enable": "Check", "turn on": "Check", "untick": "Uncheck", "disable": "Uncheck", "turn off": "Uncheck"}.get(re.sub(r"\s+", " ", m["v"].lower()), m["v"].capitalize())
        return [f"{verb} the {m['x']} checkbox"]
    # "select the first Logitech product from the results" -> the ordinal rule wants the noun last
    m = re.match(r"^(?P<v>open|click(?:\s+on)?|pick|select|choose|view)\s+(?P<x>(?:the\s+)?(?:first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th))\s+.+?)"
                 r"\s+(?:from|in|on|out\s+of|among)\s+(?:the\s+)?(?:search\s+)?(?:results?|list|listing|page|options)$", c, re.I)
    if m:
        return [f"{m['v']} {m['x']}"]
    return None


def fallback(clause: str) -> list[str] | None:
    """Last resort for a clause nothing else understood: 'open Laptops' / 'select the Products menu' is a click on that name."""
    c = clause.strip().rstrip(".,")
    m = re.match(r"^(?:open|go\s+to|visit|select|choose|pick|navigate\s+to|access|view)\s+(?:on\s+)?(?:the\s+)?(?P<x>[A-Za-z0-9][\w &/-]{0,40}?)"
                 r"(?:\s+(?P<k>link|menu|tab|section|option|category|button|item))?$", c, re.I)
    if not m or _URL.search(c) or re.search(r"\b(?:page|screen|site|website|application|app)$", c, re.I):
        return None
    return [f"Click {m['x'].strip()}" + (f" {m['k']}" if m["k"] else "")]


_SECRET_TARGET = re.compile(r"pass\s*word|passcode|\bpin\b|\bcvv\b|\bcvc\b|security\s+code|card\s+number", re.I)


def lift_secrets(steps: list, data: dict, sensitive: list[str], only_derived: bool = True) -> None:
    """A literal typed into a password-like field becomes masked test data: the step keeps ${password}, the value moves to data
    and is masked in screenshots, reports and scripts. With only_derived, only steps the tool worked out from prose count (they
    carry an origin); a step line the author wrote by hand ('Type "x" into Card number') is exactly what they meant."""
    for st in steps:
        if st.action.value not in ("type", "append") or not st.target or not st.value:
            continue
        if only_derived and not st.origin:
            continue
        if not _SECRET_TARGET.search(st.target) or re.search(r"\$\{[^}]+\}|\{\{[^}]+\}\}", st.value):
            continue
        key = re.sub(r"[^a-z0-9]+", "_", re.sub(r"(?i)\b(?:the|field|box|input|enter|your)\b", "", st.target).strip().lower()).strip("_") or "password"
        key = "password" if "pass" in key else key
        base, n = key, 2
        while key in data and str(data[key]) != st.value:
            key, n = f"{base}_{n}", n + 1
        data[key] = st.value
        if key not in sensitive:
            sensitive.append(key)
        st.value = "${" + key + "}"


def ensure_secret_values(steps: list, data: dict, sensitive: list[str]) -> list[str]:
    """${password} (or pin / cvv) used by a step with no value anywhere: make one up, masked, and say so. A paragraph that only
    describes a password ("at least 8 characters") has no value to give; the run still needs one."""
    notes = []
    for st in steps:
        for key in re.findall(r"\$\{\s*([\w.]+)\s*\}", st.value or ""):
            if key in data or not re.search(r"pass|pin|cvv|cvc|secret", key, re.I):
                continue
            data[key] = _gen_secret(12)
            if key not in sensitive:
                sensitive.append(key)
            notes.append(f"No {key.replace('_', ' ')} was given: a test value was made up (masked). Replace it under Test data if the app needs a specific one.")
    return notes
