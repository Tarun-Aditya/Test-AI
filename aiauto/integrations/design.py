"""Test design from a context pack: positive, negative, boundary, role and regression cases.

The rule engine works with AI off. It reads the acceptance criteria and the form rules found in the code
(required, format, min/max length, numeric range, error messages, the submit button, the success text) and
writes each case as steps in Autopilot's plain-English format, so a case runs as it is. With AI allowed, the
model adds cases on top; every case keeps its origin (rules / ai / edited) and a trace to the criterion."""
from __future__ import annotations

import logging
import re
import uuid

from .context import summary_text

log = logging.getLogger("aiauto.design")

VALID = {"email": "test.user@example.com", "tel": "5551234567", "url": "https://example.com", "date": "2030-01-15",
         "number": "42", "textarea": "Test note", "search": "test"}
INVALID_FORMAT = {"email": "not-an-email", "tel": "abc", "url": "not a url", "number": "abc", "date": "31-31-2030"}
Q = r"[\"“”']"


def _case(title, ctype, steps, *, ac=None, source="", expected="", pre=(), data=None, sensitive=(), priority=None, origin="rules"):
    return {"id": uuid.uuid4().hex[:10], "title": title, "type": ctype, "priority": priority or
            {"positive": "High", "regression": "High", "negative": "Normal", "boundary": "Normal", "role": "Normal"}.get(ctype, "Normal"),
            "ac": ac, "source": source, "preconditions": list(pre), "steps": steps, "expected": expected,
            "data": dict(data or {}), "sensitive": list(sensitive), "origin": origin, "status": "draft", "notes": [], "external": {}}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _words(s: str) -> set[str]:
    return {w for w in _norm(s).split() if w not in ("the", "a", "an", "field", "input", "box", "your", "of", "and")}


# ---------------------------------------------------------------------------------- what the criteria say
RULES = [
    ("required", re.compile(r"(?i)(?:the\s+)?(?P<f>[a-z][\w\s-]{1,30}?)\s+(?:is|are)\s+(?:required|mandatory)")),
    ("format", re.compile(r"(?i)(?:the\s+)?(?P<f>[a-z][\w\s-]{1,30}?)\s+(?:is\s+required\s+and\s+)?(?:must|should)\s+be\s+(?:a\s+)?valid\s+(?P<t>e-?mail(?:\s+address)?|phone(?:\s+number)?|url|date|number)")),
    ("format", re.compile(r"(?i)\band\s+(?:must|should)\s+be\s+(?:a\s+)?valid\s+(?P<t>e-?mail|phone|url|date|number)")),
    ("range", re.compile(r"(?i)(?:the\s+)?(?P<f>[a-z][\w\s-]{1,30}?)\s+(?:must|should|has\s+to|needs\s+to)\s+be\s+between\s+(?P<a>\d+)\s+and\s+(?P<b>\d+)(?P<chars>\s+char\w*)?")),
    ("maxlength", re.compile(r"(?i)(?:the\s+)?(?P<f>[a-z][\w\s-]{1,30}?)\s+(?:must|should|can|may)?\s*(?:be|have|contain)?\s*(?:at\s+most|max(?:imum)?(?:\s+of)?|up\s+to|no\s+more\s+than|limited\s+to)\s+(?P<n>\d+)\s+char")),
    ("minlength", re.compile(r"(?i)(?:the\s+)?(?P<f>[a-z][\w\s-]{1,30}?)\s+(?:must|should|has\s+to|needs\s+to)\s*(?:be|have|contain)?\s*(?:at\s+least|min(?:imum)?(?:\s+of)?|no\s+(?:less|fewer)\s+than)\s+(?P<n>\d+)\s+char")),
    ("min", re.compile(r"(?i)(?:the\s+)?(?P<f>[a-z][\w\s-]{1,30}?)\s+(?:must|should)\s+be\s+(?:at\s+least|no\s+less\s+than|>=?)\s+(?P<n>\d+)\b(?!\s*char)")),
    ("max", re.compile(r"(?i)(?:the\s+)?(?P<f>[a-z][\w\s-]{1,30}?)\s+(?:must|should)\s+be\s+(?:at\s+most|no\s+more\s+than|<=?)\s+(?P<n>\d+)\b(?!\s*char)")),
]
ERROR_MSG = re.compile(rf"(?i)(?:shows?|displays?|see|gets?|with)\s+(?:an?\s+|the\s+)?(?:error|message|warning)?\s*(?:message\s+)?{Q}(?P<m>[^\"“”']+){Q}")
ROLE = re.compile(r"(?i)\bonly\s+(?P<role>[a-z]+?)s?\s+(?:can|may|are\s+able\s+to)\s+(?P<what>[^.,;]+)")
GWT = re.compile(r"(?is)^\s*given\s+(?P<g>.+?)(?:,\s*|\s+)when\s+(?P<w>.+?)(?:,\s*|\s+)then\s+(?P<t>.+?)\.?\s*$")
QUOTED = re.compile(rf"{Q}([^\"“”']+){Q}")


def criteria_rules(ac: list[str]) -> list[dict]:
    """Field rules stated in the acceptance criteria: [{field, kind, ...}]"""
    out = []
    for idx, line in enumerate(ac):
        start = len(out)
        for part in re.split(r"(?i);\s*|\.\s+(?=[A-Z])", line):
            for kind, rx in RULES:
                for m in rx.finditer(part):
                    f = (m.groupdict().get("f") or "").strip()
                    f = re.sub(r"(?i)^(?:and|also|then|the|a|an)\s+", "", f)
                    f = re.sub(r"(?i)\s+(?:is|are)\s+(?:required|mandatory)(?:\s+and)?$|\s+(?:field|input|value)$", "", f).strip()
                    if kind == "format" and not f:      # "... and must be a valid email": same field as before
                        f = out[-1]["field"] if out else ""
                    if not f:
                        continue
                    r = {"field": f, "kind": kind, "ac": idx + 1, "text": line}
                    if kind == "format":
                        t = m.group("t").lower()
                        r["format"] = "email" if t.startswith("e") else "tel" if t.startswith("phone") else t.split()[0]
                    elif kind == "range":
                        r.update(a=int(m.group("a")), b=int(m.group("b")), chars=bool(m.group("chars")))
                    elif kind in ("maxlength", "minlength", "min", "max"):
                        r["n"] = int(m.group("n"))
                    if any(x["field"] == f and x["kind"] == kind for x in out[start:]):
                        continue                       # two patterns saw the same rule
                    out.append(r)
            # a message ("shows 'Enter a valid email'") belongs to the rule written just before it: the last one in
            # its clause, or, for a clause that is only the message, the last rule earlier in the line
            em = ERROR_MSG.search(part)
            if em and len(out) > start and not out[-1].get("error"):
                out[-1]["error"] = em.group("m")
    return out


def _merge_fields(code_fields: list[dict], rules: list[dict]) -> list[dict]:
    """One entry per field: code order first, rules from the criteria matched to it by name."""
    fields = [dict(f, rules=[], source="code") for f in code_fields]
    for r in rules:
        best, score = None, 0.0
        for f in fields:
            fw, rw = _words(f["name"]) | _words(f.get("attr_name", "")) | _words(f.get("id", "")), _words(r["field"])
            s = len(fw & rw) / max(1, len(rw))
            if s > score:
                best, score = f, s
        if best is None or score < 0.5:
            best = {"name": r["field"].strip().title() if r["field"].islower() else r["field"].strip(), "type": "text",
                    "required": False, "rules": [], "source": "criteria"}
            fields.append(best)
        best["rules"].append(r)
        if r["kind"] == "required":
            best["required"] = True
        if r["kind"] == "format":
            best["type"] = r["format"]
        if r["kind"] == "maxlength":
            if not best.get("maxlength"):          # the page caps it itself (maxlength attribute): keep "capped"
                best["maxlength"], best["maxlength_from"] = str(r["n"]), "criteria"
        if r["kind"] == "minlength":
            best["minlength"] = str(r["n"])
        if r["kind"] == "range":
            if r["chars"]:
                best["minlength"] = str(r["a"])
                if not best.get("maxlength"):
                    best["maxlength"], best["maxlength_from"] = str(r["b"]), "criteria"
            else:
                best["min"], best["max"] = str(r["a"]), str(r["b"])
                best["type"] = "number" if best.get("type") in (None, "text") else best["type"]
        if r["kind"] == "min":
            best["min"] = str(r["n"])
        if r["kind"] == "max":
            best["max"] = str(r["n"])
        if r.get("error"):
            best.setdefault("errors", {})[r["kind"]] = r["error"]
    return fields


def _valid_value(f: dict) -> str:
    t = f.get("type") or "text"
    if t == "password":
        n = max(int(f.get("minlength") or 8), 8)
        return "Aa1!" + "x" * (n - 4)
    if t == "number" or f.get("min") or f.get("max"):
        lo, hi = int(f.get("min") or 1), int(f.get("max") or 100)
        return str((lo + hi) // 2)
    v = VALID.get(t, "testuser")
    if f.get("maxlength"):
        v = v[:int(f["maxlength"])]
    if f.get("minlength") and len(v) < int(f["minlength"]):
        v = (v + "x" * int(f["minlength"]))[:max(int(f["minlength"]), len(v))]
    return v


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "field"


# ---------------------------------------------------------------------------------- the flow a case runs
def _page_url(pack: dict) -> str:
    code = pack.get("code") or {}
    text = " ".join([pack["issue"].get("title", ""), pack["issue"].get("description", ""), *pack["issue"].get("acceptance", [])])
    pages = list(dict.fromkeys(code.get("pages") or []))
    m = re.search(r"(?i)\bon\s+the\s+(?P<p>[\w -]{2,30}?)\s+(?:page|screen|form)\b", text)
    if m and pages:
        want = re.sub(r"[^a-z0-9]", "", m.group("p").lower())
        hit = next((p for p in pages if want and want in re.sub(r"[^a-z0-9]", "", p.lower())), None)
        if hit:
            return "${base_url}/" + hit
    paths = re.findall(r"(?<![\w/])(/[a-z0-9][\w/.-]*)", text)
    if paths:
        return "${base_url}" + paths[0]
    if pages:
        return "${base_url}/" + pages[0]
    routes = [r for r in code.get("routes") or [] if ":" not in r and "<" not in r and "{" not in r]
    return "${base_url}" + (routes[0] if routes else "")


def _submit(pack: dict) -> str | None:
    text = " ".join(pack["issue"].get("acceptance", []) + [pack["issue"].get("description", "")])
    m = re.search(rf"(?i)\b(?:click|press|tap|select|hit)\s+(?:on\s+)?(?:the\s+)?(?:{Q}(?P<q>[^\"“”']+){Q}|(?P<b>[A-Z][\w ]{{1,30}}?))(?:\s+button)?(?=[,.;]|\s+then\b|\s+and\b|$)", text)
    if m:
        return (m.group("q") or m.group("b")).strip()
    buttons = (pack.get("code") or {}).get("buttons") or []
    return buttons[0] if buttons else None


def _success(pack: dict) -> str | None:
    for line in pack["issue"].get("acceptance", []):
        m = GWT.match(line)
        if m:
            q = QUOTED.findall(m.group("t"))
            if q:
                return q[0]
    # a criterion that says what the user sees, but not a rule's error message ("...; shows 'Age must be 18-99'")
    errors = {r["error"] for r in criteria_rules(pack["issue"].get("acceptance", [])) if r.get("error")}
    for line in pack["issue"].get("acceptance", []):
        if re.search(r"(?i)\b(?:see|shows?|displays?|redirect|confirmation|success)", line) and not re.search(r"(?i)error|invalid|not\b", line):
            q = [x for x in QUOTED.findall(line) if x not in errors]
            if q:
                return q[0]
    return None


def _flow(pack: dict, fields: list[dict], values: dict[str, str | None], success_check: bool = True) -> list[str]:
    """Open the page, fill every field (None = leave it empty), submit, and (optionally) check success."""
    steps = [f"Open {_page_url(pack)}"]
    for f in fields:
        v = values.get(f["name"], "")
        if v is None:
            continue
        if f.get("type") == "password":
            steps.append(f"Type ${{{_slug(f['name'])}}} into {f['name']}")
        else:
            steps.append(f'Type "{v}" into {f["name"]}')
    btn = _submit(pack)
    if btn:
        steps.append(f"Click {btn}")
    return steps


def _defaults(fields: list[dict]) -> tuple[dict, dict, list]:
    values = {f["name"]: _valid_value(f) for f in fields}
    data = {_slug(f["name"]): values[f["name"]] for f in fields if f.get("type") == "password"}
    sensitive = list(data)
    return values, data, sensitive


# ---------------------------------------------------------------------------------- the rule engine
def rule_cases(pack: dict) -> tuple[list[dict], list[str]]:
    issue = pack["issue"]
    ac = issue.get("acceptance") or []
    code = pack.get("code") or {}
    notes: list[str] = []
    rules = criteria_rules(ac)
    fields = _merge_fields(code.get("fields") or [], rules)
    values, data, sens = _defaults(fields)
    success = _success(pack)
    ok_check = [f'Verify page contains "{success}"'] if success else []
    blocked = [f'Verify page does not contain "{success}"'] if success else []
    if not success:
        notes.append("No success message found in the criteria: add a 'then I see \"...\"' or edit the checks")
    cases: list[dict] = []
    is_bug = (issue.get("type") or "").lower() in ("bug", "defect")

    # bug: the reported steps must now give the expected result
    if is_bug and issue.get("steps_to_reproduce"):
        steps = [re.sub(r"^\s*\d+[.)]\s*", "", s) for s in issue["steps_to_reproduce"]]
        exp = issue.get("expected_result") or ""
        q = QUOTED.findall(exp)
        if q:
            steps.append(f'Verify page contains "{q[0]}"')
        act_q = QUOTED.findall(issue.get("actual_result") or "")
        if act_q:
            steps.append(f'Verify page does not contain "{act_q[0]}"')
        cases.append(_case(f"Regression: {issue['title']}", "regression", steps, source="steps to reproduce", expected=exp))

    # one positive case per Given/When/Then criterion (its own words), else one happy path over the fields
    gwt_done = False
    for idx, line in enumerate(ac):
        m = GWT.match(line)
        if not m:
            continue
        gwt_done = True
        then = m.group("t")
        q = QUOTED.findall(then)
        steps = _flow(pack, fields, values) if fields else [f"Open {_page_url(pack)}", m.group("w").strip().rstrip(".")]
        if q:
            steps.append(f'Verify page contains "{q[0]}"')
        elif not fields:
            notes.append(f"Criterion {idx + 1}: no quoted text after 'then' to check")
        cases.append(_case(_title(issue, f"works when {m.group('w').strip()}"), "positive", steps, ac=idx + 1, source=line,
                           expected=then, pre=[m.group("g").strip()], data=data, sensitive=sens))
    if not gwt_done and fields:
        cases.append(_case(_title(issue, "works with valid details"), "positive", _flow(pack, fields, values) + ok_check,
                           expected=success or "", data=data, sensitive=sens))

    # the happy path fills every field with a valid value: it is the positive case for each field rule too
    field_acs = sorted({r["ac"] for r in rules})
    for c in cases:
        if c["type"] == "positive" and fields:
            c["covers"] = field_acs

    # per field: required, format, length and range rules -> negative and boundary cases
    for f in fields:
        name, err = f["name"], f.get("errors") or {}
        ac_no = next((r["ac"] for r in f["rules"]), None)
        fail = lambda kind: ([f'Verify page contains "{err[kind]}"'] if err.get(kind) else []) + blocked

        if f.get("required"):
            cases.append(_case(_title(issue, f"rejects an empty {name}"), "negative",
                               _flow(pack, fields, {**values, name: None}) + fail("required"), ac=ac_no,
                               source=_src(f, "required"), expected=f"{name} is required; the form is not accepted", data=data, sensitive=sens))
        fmt = f.get("type")
        if fmt in INVALID_FORMAT and fmt != "number":
            cases.append(_case(_title(issue, f"rejects an invalid {name}"), "negative",
                               _flow(pack, fields, {**values, name: INVALID_FORMAT[fmt]}) + fail("format"), ac=ac_no,
                               source=_src(f, "format"), expected=f"{name} must be a valid {fmt}", data=data, sensitive=sens))
        if f.get("maxlength"):
            n = int(f["maxlength"])
            at = ("abcdefghij" * (n // 10 + 1))[:n]
            over = at + "k"
            cases.append(_case(_title(issue, f"accepts {name} at the maximum length ({n})"), "boundary",
                               _flow(pack, fields, {**values, name: at}) + ok_check, ac=ac_no, source=_src(f, "maxlength"),
                               expected=f"{n} characters are accepted", data=data, sensitive=sens))
            if f.get("maxlength_from") == "criteria":
                cases.append(_case(_title(issue, f"rejects {name} over the maximum length ({n + 1})"), "negative",
                                   _flow(pack, fields, {**values, name: over}) + fail("maxlength"), ac=ac_no,
                                   source=_src(f, "maxlength"), expected=f"more than {n} characters are refused", data=data, sensitive=sens))
            else:   # the page itself caps the field (maxlength attribute): typing more keeps only n
                steps = _flow(pack, fields, {**values, name: over})
                steps.insert(-1 if _submit(pack) else len(steps), f'Verify the {name} field does not contain "{over}"')
                cases.append(_case(_title(issue, f"keeps {name} to {n} characters"), "boundary", steps, ac=ac_no,
                                   source=_src(f, "maxlength"), expected=f"the field holds at most {n} characters", data=data, sensitive=sens))
        if f.get("minlength") and f.get("type") != "number":
            n = int(f["minlength"])
            short = ("Aa1!" + "x" * n)[:max(n - 1, 0)] if f.get("type") == "password" else ("abcdefghij" * (n // 10 + 1))[:max(n - 1, 0)]
            enough = ("Aa1!" + "x" * n)[:n] if f.get("type") == "password" else ("abcdefghij" * (n // 10 + 1))[:n]
            key = _slug(name)
            pw = f.get("type") == "password"
            cases.append(_case(_title(issue, f"rejects {name} shorter than {n} characters"), "negative",
                               _flow(pack, fields, {**values, name: short}) + fail("minlength"), ac=ac_no, source=_src(f, "minlength"),
                               expected=f"fewer than {n} characters are refused", data={**data, key: short} if pw else data, sensitive=sens))
            cases.append(_case(_title(issue, f"accepts {name} at the minimum length ({n})"), "boundary",
                               _flow(pack, fields, {**values, name: enough}) + ok_check, ac=ac_no, source=_src(f, "minlength"),
                               expected=f"{n} characters are accepted", data={**data, key: enough} if pw else data, sensitive=sens))
        if f.get("min") is not None and f.get("type") in ("number", "text", None) and str(f.get("min")).lstrip("-").isdigit():
            lo = int(f["min"])
            cases.append(_case(_title(issue, f"accepts {name} = {lo} (minimum)"), "boundary",
                               _flow(pack, fields, {**values, name: str(lo)}) + ok_check, ac=ac_no, source=_src(f, "range"),
                               expected=f"{lo} is accepted", data=data, sensitive=sens))
            cases.append(_case(_title(issue, f"rejects {name} = {lo - 1} (below the minimum)"), "negative",
                               _flow(pack, fields, {**values, name: str(lo - 1)}) + fail("range"), ac=ac_no, source=_src(f, "range"),
                               expected=f"{lo - 1} is refused", data=data, sensitive=sens))
        if f.get("max") is not None and f.get("type") in ("number", "text", None) and str(f.get("max")).lstrip("-").isdigit():
            hi = int(f["max"])
            cases.append(_case(_title(issue, f"accepts {name} = {hi} (maximum)"), "boundary",
                               _flow(pack, fields, {**values, name: str(hi)}) + ok_check, ac=ac_no, source=_src(f, "range"),
                               expected=f"{hi} is accepted", data=data, sensitive=sens))
            cases.append(_case(_title(issue, f"rejects {name} = {hi + 1} (above the maximum)"), "negative",
                               _flow(pack, fields, {**values, name: str(hi + 1)}) + fail("range"), ac=ac_no, source=_src(f, "range"),
                               expected=f"{hi + 1} is refused", data=data, sensitive=sens))

    # roles: "only admins can ..." -> allowed and denied cases (need the test accounts)
    for idx, line in enumerate(ac):
        m = ROLE.search(line)
        if not m:
            continue
        role, what = m.group("role").lower(), m.group("what").strip()
        target = QUOTED.findall(what)
        check = target[0] if target else what
        base = [f"Open {_page_url(pack)}"]
        art = "an" if role[:1] in "aeiou" else "a"
        c1 = _case(_title(issue, f"{art} {role} can {what}"), "role", base + [f'Verify page contains "{check}"'], ac=idx + 1, source=line,
                   pre=[f"Signed in as a {role}"], data={f"{role}_user": f"${{users.{role}.username}}"})
        c2 = _case(_title(issue, f"a non-{role} can't {what}"), "role", base + [f'Verify page does not contain "{check}"'], ac=idx + 1,
                   source=line, pre=[f"Signed in as a user who is not a {role}"])
        for c in (c1, c2):
            c["notes"].append("Needs test accounts for each role, and the sign-in steps")
        cases += [c1, c2]

    # criteria nothing above understood: keep them visible as cases to complete by hand
    covered = {c["ac"] for c in cases if c["ac"]}
    for idx, line in enumerate(ac):
        if idx + 1 not in covered and not any(r["ac"] == idx + 1 for r in rules):
            c = _case(_title(issue, line[:60]), "positive", [f"Open {_page_url(pack)}"] + ([f'Verify page contains "{QUOTED.findall(line)[0]}"']
                      if QUOTED.findall(line) else []), ac=idx + 1, source=line, expected=line)
            c["notes"].append("Criterion not understood by the rules: complete the steps")
            cases.append(c)
    return _dedupe(cases), notes


def _title(issue: dict, what: str) -> str:
    what = re.sub(r"\s+", " ", what).strip().rstrip(".")
    return f"{issue['key']} · {what[0].upper() + what[1:]}" if what else issue["key"]


def _src(f: dict, kind: str) -> str:
    r = next((r for r in f.get("rules") or [] if r["kind"] == kind or (kind == "range" and r["kind"] in ("range", "min", "max"))), None)
    if r:
        return r["text"]
    attrs = ", ".join(f"{k}={f[k]}" for k in ("type", "maxlength", "minlength", "min", "max", "pattern") if f.get(k))
    return f"code: {f['name']}" + (f" ({'required, ' if f.get('required') else ''}{attrs})" if attrs or f.get("required") else "")


def _dedupe(cases: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in cases:
        k = (c["type"], _norm(c["title"]))
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


# ---------------------------------------------------------------------------------- checks every case gets
def check(cases: list[dict]) -> list[dict]:
    """Mark steps the engine wouldn't understand and placeholders without data."""
    from ..nlp import rules as rule_parser
    for c in cases:
        c["notes"] = [n for n in c.get("notes") or [] if not n.startswith(("Step not understood", "No value for"))]
        _, unparsed = rule_parser.parse("\n".join(c["steps"]))
        for u in unparsed:
            c["notes"].append(f"Step not understood: {u}")
        for ph in sorted(set(re.findall(r"\$\{([\w.-]+)\}", "\n".join(c["steps"])))):
            if ph != "base_url" and ph not in c.get("data", {}):
                c["notes"].append(f"No value for ${{{ph}}}")
    return cases


def coverage(pack: dict, cases: list[dict]) -> list[dict]:
    """Criterion x case types: which criteria lack a positive or a negative case."""
    out = []
    for idx, line in enumerate(pack["issue"].get("acceptance") or [], 1):
        kinds = {c["type"] for c in cases if (c.get("ac") == idx or idx in (c.get("covers") or [])) and c.get("status") != "rejected"}
        out.append({"ac": idx, "text": line, "types": sorted(kinds),
                    "missing": [k for k, ok in (("positive", {"positive", "boundary", "regression", "role"}),
                                                ("negative", {"negative", "boundary", "role"})) if not kinds & ok]})
    return out


# ---------------------------------------------------------------------------------- AI on top (optional)
AI_SYSTEM = """You design browser test cases for a story, as a senior QA engineer. You get the story, its acceptance
criteria, related docs, the form fields found in the code, and the cases a rule engine already wrote. Add the cases
that are missing: business rules, error paths, roles, states, edge cases a careful tester would try. Do not repeat
cases that already exist.

Each step is ONE plain-English browser action the test engine understands, e.g.:
  Open ${base_url}/signup.html
  Type "test.user@example.com" into Email
  Type ${password} into Password          (secret values as ${placeholders})
  Click Create account
  Verify page contains "Account created"
  Verify page does not contain "Account created"
  Select "France" from Country
Use the field labels and button texts from the code. Never invent URLs; use ${base_url} plus a path from the code."""

AI_SCHEMA = {"type": "object", "properties": {"cases": {"type": "array", "items": {"type": "object", "properties": {
    "title": {"type": "string"}, "type": {"type": "string", "enum": ["positive", "negative", "boundary", "role", "regression"]},
    "priority": {"type": "string", "enum": ["High", "Normal", "Low"]}, "ac": {"type": ["integer", "null"]},
    "preconditions": {"type": "array", "items": {"type": "string"}}, "steps": {"type": "array", "items": {"type": "string"}},
    "expected": {"type": "string"}}, "required": ["title", "type", "priority", "ac", "preconditions", "steps", "expected"],
    "additionalProperties": False}}}, "required": ["cases"], "additionalProperties": False}


def ai_cases(pack: dict, existing: list[dict]) -> list[dict]:
    from ..nlp import providers
    if not providers.available():
        return []
    have = "\n".join(f"- [{c['type']}] {c['title']}" for c in existing)
    user = summary_text(pack) + "\n\nCASES ALREADY WRITTEN:\n" + (have or "(none)")
    out = providers.call_json(AI_SYSTEM, user, AI_SCHEMA, max_tokens=16000, effort="medium")
    if not out:
        return []
    data = next((c.get("data") for c in existing if c.get("data")), {})
    sens = next((c.get("sensitive") for c in existing if c.get("sensitive")), [])
    cases = []
    for c in out.get("cases") or []:
        if not c.get("steps"):
            continue
        title = c["title"] if c["title"].startswith(pack["issue"]["key"]) else _title(pack["issue"], c["title"])
        cases.append(_case(title, c["type"], c["steps"], ac=c.get("ac"), source="AI", expected=c.get("expected", ""),
                           pre=c.get("preconditions") or [], priority=c.get("priority"), origin="ai", data=data, sensitive=sens))
    return cases


def design(pack: dict, use_ai: bool = False) -> dict:
    cases, notes = rule_cases(pack)
    if use_ai:
        try:
            extra = ai_cases(pack, cases)
            cases = _dedupe(cases + extra)
            if not extra:
                notes.append("AI was asked for more cases but none came back (no provider, or it declined)")
        except Exception as e:     # AI is optional: never block a design on it
            log.warning("AI test design failed: %s", e)
            notes.append(f"AI test design failed: {type(e).__name__}")
    return {"cases": check(cases), "notes": notes, "coverage": coverage(pack, cases)}
