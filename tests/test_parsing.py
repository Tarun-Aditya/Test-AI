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
