"""Business-language paragraphs (third person, no quotes, several fields per sentence, expected results in plain words)
become steps with the built-in rules: no AI involved."""
import pytest

from aiauto.models import RunConfig
from aiauto.nlp import narrative
from aiauto.nlp.parser import parse_text
from aiauto.nlp.rules import parse_detailed
from aiauto.runner import run_test

from .conftest import EXAMPLES, _serve


def steps_of(text):
    d = parse_detailed(text)
    return [(s.action.value, s.target, s.value) for s in d["steps"]], d


SHOP = ("As a customer I want to buy a wireless mouse. I navigate to the DemoShop home page, search for wireless mouse and select "
        "the first Logitech product from the results. I choose quantity 2 and add the item to my cart. When I open the cart, the "
        "subtotal should show 2 items and the product name should be displayed.")
SIGNUP = ("The visitor lands on the sign up page and enters a valid email address test.user@example.com, a username testuser, age 30 "
          "and a password of at least 8 characters, then submits the registration form. The system should confirm that the account "
          "was created successfully.")
LOGIN = ("User opens the login page, provides username alice and password secret, ticks the remember me option, picks India as the "
         "country and clicks the login button. After login the dashboard greets the user with a welcome message for alice. The user "
         "then goes to the Products menu and opens Laptops, and the Laptops page is shown.")


def test_shop_paragraph_has_no_quotes_and_still_becomes_steps():
    got, d = steps_of(SHOP)
    assert got == [("navigate", None, "${base_url}"), ("type", "search box", "wireless mouse"), ("press", None, "Enter"),
                   ("click", "Logitech", None), ("select", "quantity", "2"), ("click", "Add to Cart button", None),
                   ("click", "Cart link", None), ("assert_text", None, "2 items")]
    assert d["unparsed"] == [] and d["ignored"] == ["As a customer I want to buy a wireless mouse"]
    assert any("product name" in n and "Not checked" in n for n in d["notes"])      # nothing concrete to assert: said so
    assert any("base_url" in n for n in d["notes"])                                   # no page address in the text: said so


def test_several_fields_in_one_sentence_and_a_secret_that_is_only_described():
    got, d = steps_of(SIGNUP)
    assert got == [("navigate", None, "${base_url}"), ("type", "Email", "test.user@example.com"), ("type", "Username", "testuser"),
                   ("type", "Age", "30"), ("type", "Password", "${password}"), ("click", "submit button", None),
                   ("assert_text", None, "account created")]
    assert d["sensitive"] == ["password"] and len(d["data"]["password"]) >= 8          # made up, masked, long enough


def test_login_paragraph_passwords_become_masked_data_and_expectations_are_split():
    got, d = steps_of(LOGIN)
    assert ("type", "Username", "alice") in got and ("type", "Password", "${password}") in got
    assert ("check", "remember me checkbox", None) in got and ("select", "country", "India") in got
    assert [g for g in got if g[0] == "assert_text"] == [("assert_text", None, "welcome"), ("assert_text", None, "alice"),
                                                         ("assert_text", None, "Laptops")]
    assert ("click", "Laptops", None) in got and d["unparsed"] == []
    assert d["data"] == {"password": "secret"} and d["sensitive"] == ["password"]      # the secret left the steps


def test_a_password_in_the_text_never_stays_in_a_step_value():
    for text in ("The user logs in with email jane@example.com and password Pass@1234.",
                 "Enter email john@x.com and password abc12345",
                 'The user enters "alice" as the username and "secret" as the password.'):
        t, _ = parse_text(text, {}, use_llm=False)
        assert not any(s.value in ("Pass@1234", "abc12345", "secret") for s in t.steps), text
        assert "password" in t.sensitive and t.data["password"] in ("Pass@1234", "abc12345", "secret"), text


def test_a_value_that_starts_like_a_separator_word_stays_whole():
    # "the name Asha" once became the value "ha": "as" was taken for the word 'as' in "name as Asha"
    for name in ("Asha", "Isabel", "Ofelia", "Ashok", "Ivy", "Aston"):
        got, _ = steps_of(f"The visitor enters the name {name} and age 30.")
        assert got[0][2] == name and got[1][2] == "30", (name, got)
    got, _ = steps_of("The visitor enters the name as Asha and the email is a@b.co.")
    assert got == [("type", "Full name", "Asha"), ("type", "Email", "a@b.co")]


def test_gherkin_and_numbered_lists_and_titles():
    got, d = steps_of("Scenario: Successful login\nGiven the user is on the login page\nWhen the user enters username alice and password "
                      "secret\nAnd clicks the Login button\nThen the dashboard should display the text Welcome alice")
    assert got == [("navigate", None, "${base_url}"), ("type", "Username", "alice"), ("type", "Password", "${password}"),
                   ("click", "Login button", None), ("assert_text", None, "Welcome alice")]
    assert d["ignored"] == ["Scenario: Successful login"]
    got, _ = steps_of("1. Open the application\n2. Enter email john@x.com and password abc12345\n3. Click Sign in")
    assert got[:3] == [("navigate", None, "${base_url}"), ("type", "Email", "john@x.com"), ("type", "Password", "${password}")]


def test_negative_expectations_and_paraphrase_warning():
    got, d = steps_of("The user enters age 17 on the sign up form and submits it. An error message stating that age must be between 18 "
                      "and 99 must be displayed and no account should be created.")
    assert got == [("type", "Age", "17"), ("click", "submit button", None),
                   ("assert_text", None, "age must be between 18 and 99"), ("assert_no_text", None, "account created")]
    assert any("Check the wording" in n for n in d["notes"])                           # paraphrased: use the exact message


def test_sign_in_without_credentials_asks_for_them_instead_of_clicking_blindly():
    got, d = steps_of("The administrator signs in, opens the Users menu and clicks the Add user button.")
    assert got[:3] == [("type", "username or email field", "${username}"), ("type", "password field", "${password}"),
                       ("click", "Log in or Sign in button", None)]
    assert "password" not in d["data"] and any("credentials" in n for n in d["notes"])  # a real login's password is never invented


def test_nothing_is_dropped_silently():
    got, d = steps_of("The visitor enters email a@b.com, a captcha and age 30.")
    assert got == [("type", "Email", "a@b.com"), ("type", "Age", "30")]
    assert any("captcha" in n for n in d["notes"])
    _, d = steps_of("The balance should not be negative.")
    assert d["unparsed"] == [] and any("Not checked" in n for n in d["notes"])


def test_a_value_typed_in_an_explicit_step_line_is_left_alone():
    t, _ = parse_text('Open https://x.test\nType "4111111111114242" into Card number\nType "pw1" into Password', {}, use_llm=False)
    assert [s.value for s in t.steps][1:] == ["4111111111114242", "pw1"] and t.sensitive == [] and t.data == {}   # as before


def test_step_per_line_and_quoted_input_still_work_as_before():
    got, d = steps_of('Open https://x.test\nType "alice" into Username\nClick Login\nVerify page contains "Welcome"')
    assert got == [("navigate", None, "https://x.test"), ("type", "Username", "alice"), ("click", "Login", None),
                   ("assert_text", None, "Welcome")]
    assert d["ignored"] == [] and d["notes"] == []


def test_parse_text_puts_lifted_values_into_the_test_data_without_overriding_yours():
    t, info = parse_text(LOGIN, {"password": "mine", "base_url": "http://x"}, use_llm=False)
    assert t.data["password"] == "mine" and "password" in t.sensitive
    t, info = parse_text(SIGNUP, {}, use_llm=False)
    assert t.data["password"] and "password" in t.sensitive and info["data_added"] == {"password": "••••••"}


def test_lift_secrets_also_covers_steps_an_ai_wrote():
    from aiauto.models import Action, Step
    steps = [Step(action=Action.TYPE, target="Password field", value="hunter2", raw="x"),
             Step(action=Action.TYPE, target="Email", value="a@b.c", raw="y")]
    data, sens = {}, []
    narrative.lift_secrets(steps, data, sens, only_derived=False)
    assert [s.value for s in steps] == ["${password}", "a@b.c"] and data == {"password": "hunter2"} and sens == ["password"]
    steps = [Step(action=Action.TYPE, target="Password", value="${password}", raw="x")]
    assert narrative.ensure_secret_values(steps, data := {}, sens := []) and data["password"] and sens == ["password"]


@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_a_business_paragraph_runs_end_to_end(engine, tmp_path):
    srv, url = _serve(EXAMPLES / "signup")
    try:
        t, info = parse_text(SIGNUP, {"base_url": url + "/signup.html"}, use_llm=False)
        s = run_test(t, RunConfig(engine=engine, record_video=False, live_view=False, timeout_ms=8000, use_llm=False), tmp_path)
        assert s.status.value == "passed", [(r.index + 1, r.error) for r in s.results if r.error]
        shown = " ".join(str(r.shown_value) for r in s.results)
        assert t.data["password"] not in shown                                          # the made-up password is masked everywhere
    finally:
        srv.shutdown()


def test_the_parse_endpoint_never_echoes_a_password_from_the_text(tmp_path, monkeypatch):
    """The wording a step came from ('from your text: ...') is shown in the app and the extension: it must not hold the secret."""
    import importlib
    monkeypatch.setenv("AIAUTO_DB", str(tmp_path / "h.db"))
    monkeypatch.setenv("AIAUTO_ARTIFACTS", str(tmp_path / "a"))
    import aiauto.server.app as appmod
    appmod = importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        r = c.post("/api/parse", json={"text": "The visitor enters age 30 and the password Zz9!secretvalue then submits it."},
                   headers={"X-Requested-With": "autopilot"}).json()
    assert "Zz9!secretvalue" not in str([r["test"]["steps"], r["notes"], r["ignored"]])
    assert r["test"]["data"]["password"] == "Zz9!secretvalue" and r["test"]["sensitive"] == ["password"]    # the owner still gets the value to run with
