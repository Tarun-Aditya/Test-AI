from aiauto.data import iterations, load_data, substitute
from aiauto.models import Action
from aiauto.nlp import rules


def steps(text):
    s, unparsed = rules.parse(text)
    assert not unparsed, unparsed
    return [(x.action, x.target, x.value) for x in s]


def test_basic_sentences():
    assert steps("""
        Go to example.com
        Click on the "Sign in" button
        Type "alice@x.io" into the email field
        Enter ${pwd} in password
        Hover over Products menu
        Select "India" from country dropdown
        Press Enter
        Double click the row
        Right click on file
        Wait 2 seconds
        Scroll down
        Verify page contains "Welcome"
        Verify the url contains "/dashboard"
        Check Remember me
        Uncheck newsletter
    """) == [
        (Action.NAVIGATE, None, "https://example.com"),
        (Action.CLICK, "Sign in button", None),
        (Action.TYPE, "email field", "alice@x.io"),
        (Action.TYPE, "password", "${pwd}"),
        (Action.HOVER, "Products menu", None),
        (Action.SELECT, "country dropdown", "India"),
        (Action.PRESS, None, "Enter"),
        (Action.DOUBLE_CLICK, "row", None),
        (Action.RIGHT_CLICK, "file", None),
        (Action.WAIT, None, "2"),
        (Action.SCROLL, None, "down"),
        (Action.ASSERT_TEXT, None, "Welcome"),
        (Action.ASSERT_URL, None, "/dashboard"),
        (Action.CHECK, "Remember me", None),
        (Action.UNCHECK, "newsletter", None),
    ]


def test_compound_sentence_and_quotes_protected():
    got = steps('Type "now then go" in search and then press Enter; click Search')
    assert got == [(Action.TYPE, "search", "now then go"), (Action.PRESS, None, "Enter"), (Action.CLICK, "Search", None)]


def test_numbered_list_and_key_combo():
    got = steps("1. Open https://a.dev\n2) press ctrl+a")
    assert got == [(Action.NAVIGATE, None, "https://a.dev"), (Action.PRESS, None, "Control+A")]


def test_unparsed_reported():
    _, unparsed = rules.parse("Do something magical")
    assert unparsed == ["Do something magical"]


def test_data_substitution_and_rows():
    d = load_data('{"user": {"name": "a"}, "list": [1, 2]}')
    assert substitute("hi ${user.name} {{list[1]}} ${missing}", d) == "hi a 2 ${missing}"
    assert load_data("a=1; b=two") == {"a": "1", "b": "two"}
    rows = iterations({"env": "qa", "rows": [{"u": 1}, {"u": 2}]})
    assert rows == [{"env": "qa", "u": 1}, {"env": "qa", "u": 2}]


def test_new_phrasings():
    from aiauto.nlp.rules import parse_sentence as ps
    s = ps('Click the first result containing "Logitech"')
    assert (s.action, s.target, s.nth) == (Action.CLICK, "Logitech", 0)
    assert ps("Type ${x} into Last name field").nth is None          # "Last name" is not an ordinal
    assert ps("Click Accept cookies if shown").optional
    s = ps("Drag Card A to Done column")
    assert (s.action, s.target, s.value) == (Action.DRAG, "Card A", "Done column")
    assert ps("Pause 60 seconds for manual SSO login").action == Action.PAUSE
    assert ps("Dismiss the next alert").value == "dismiss"
    assert ps('Verify alert says "Saved"').action == Action.ASSERT_DIALOG
    assert ps("switch to tab 2").value == "1"
    assert ps("Open ${base_url}/index.html").value == "${base_url}/index.html"


def test_sensitive_normalize_and_mask():
    from aiauto.data import MASK, Masker, masked_data, normalize
    data, sens = normalize({"user": {"name": "a", "pwd": {"value": "S3cret", "sensitive": True}}}, ["token"])
    assert data == {"user": {"name": "a", "pwd": "S3cret"}} and sens == ["token", "user.pwd"]
    assert masked_data(data, sens)["user"]["pwd"] == MASK
    assert Masker(data, sens)("typed S3cret here") == f"typed {MASK} here"


def test_natural_paragraph_is_split_into_steps():
    text = ("Go to https://shop.test, search for a wireless mouse and open the first Logitech result. "
            "Add 2 of them to the cart, then go to the cart and make sure it shows Logitech.")
    got = steps(text)
    assert got == [
        (Action.NAVIGATE, None, "https://shop.test"),
        (Action.TYPE, "search box", "wireless mouse"), (Action.PRESS, None, "Enter"),
        (Action.CLICK, "Logitech", None),
        (Action.SELECT, "Quantity", "2"), (Action.CLICK, "Add to Cart button", None),
        (Action.CLICK, "Cart link", None),
        (Action.ASSERT_TEXT, None, "Logitech"),
    ]
    s, _ = rules.parse(text)
    assert s[3].nth == 0 and s[1].origin == "search for a wireless mouse" and all(x.source == "rules" for x in s)


def test_natural_login_and_checks():
    got = steps("I want to log in as alice@x.io with password S3cret, then check that the dashboard says Welcome Alice. "
                "Finally accept the cookies and sign out.")
    assert got[:3] == [(Action.TYPE, "username or email field", "alice@x.io"), (Action.TYPE, "password field", "S3cret"),
                       (Action.CLICK, "Log in or Sign in button", None)]
    assert (Action.ASSERT_TEXT, None, "Welcome Alice") in got and got[-1] == (Action.CLICK, "sign out", None)


def test_names_are_readable_and_filled_per_run():
    from aiauto.nlp.parser import parse_text
    from aiauto.server.app import _display_name, _iteration_labels
    from aiauto.models import RunConfig
    t, _ = parse_text("Go to ${url}, search for a ${product} and open the first ${brand} result. Make sure it shows ${brand}.", {}, use_llm=False)
    assert "${" in t.name and "Search" in t.name
    named = _display_name(t.name, {"product": "mouse", "brand": "HP", "url": "x"}, [])
    assert named == 'Search "mouse" → first HP result → check "HP"'
    assert _iteration_labels([({"brand": "A", "pw": "1"}, RunConfig()), ({"brand": "B", "pw": "2"}, RunConfig(browser="chrome"))], ["pw"]) \
        == ["#1 A · chromium", "#2 B · chrome"]


def test_short_secrets_are_masked_only_as_whole_tokens():
    from aiauto.data import Masker
    m = Masker({"qty": "2", "pw": "hunter22"}, ["qty", "pw"])
    assert m("http://127.0.0.1:8765 qty 2, Cart (2), pw hunter22") == "http://127.0.0.1:8765 qty ••••••, Cart (••••••), pw ••••••"


def test_count_tolerant_locators_for_live_counts():
    from aiauto.models import Locator
    from aiauto.runner import Runner
    role = Locator(strategy="role", role="link", value="Cart, 0 items")
    text = Locator(strategy="text", value="Inbox (12)")
    out = Runner._count_tolerant([role, text], "Click the Cart link")
    assert [l.strategy for l in out] == ["xpath", "xpath"]
    assert 'starts-with(normalize-space(.), "Cart")' in out[0].value and '"Inbox"' in out[1].value
    assert Runner._count_tolerant([Locator(strategy="text", value="Order 750")], "Open order 750") == []   # number was asked for


def test_masking_json_keeps_it_valid():
    import json
    from aiauto.data import Masker
    m = Masker({"qty": "2"}, ["qty"])
    out = m.deep({"index": 2, "steps": [{"value": "2", "note": "add 2 items"}]})
    assert out == {"index": 2, "steps": [{"value": "••••••", "note": "add •••••• items"}]}
    json.dumps(out)
    assert Masker({"q": "2"}, ["q"]).deep({"agent": "box-2", "run_id": "x-2", "error": "on box-2 at 127.0.0.1"}) == \
        {"agent": "box-2", "run_id": "x-2", "error": "on box-2 at 127.0.0.1"}
