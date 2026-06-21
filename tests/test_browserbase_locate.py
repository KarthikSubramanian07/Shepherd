"""
Tests for BrowserbaseDriver._locate_text — the fix for the click that hung on an
aria-hidden "Where to?" label (element never visible → 20s of retries → fail).

No browser: a tiny fake Playwright `page`/`locator` models visibility so the
selection logic (interactive role → first visible text → fallback) is verified.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.browserbase_driver import BrowserbaseDriver as BD


class FakeLocator:
    def __init__(self, tag, matches):
        self.tag = tag                 # identifier for assertions
        self._matches = matches        # list[bool] — visibility per nth match

    def count(self):
        return len(self._matches)

    def nth(self, i):
        return FakeLocator(f"{self.tag}#{i}", [self._matches[i]])

    def is_visible(self):
        return self._matches[0]

    @property
    def first(self):
        return FakeLocator(f"{self.tag}.first", self._matches[:1])


class FakePage:
    """Models a Google-Flights-like page: a hidden 'Where to?' label plus a
    visible combobox of the same accessible name."""
    def __init__(self, *, role_visible=True, text_visible=False):
        self._role_visible = role_visible
        self._text_visible = text_visible

    def get_by_role(self, role, name=None, exact=False):
        # Only the combobox exposes this accessible name.
        if role == "combobox":
            return FakeLocator(f"role:{role}", [self._role_visible])
        return FakeLocator(f"role:{role}", [])  # count 0 → skipped

    def get_by_text(self, text, exact=False):
        # First match is the aria-hidden label (not visible), then a visible one
        # only if text_visible is set.
        return FakeLocator("text", [False, self._text_visible])

    def locator(self, sel):
        return FakeLocator(f"css:{sel}", [True])


def test_prefers_interactive_role_over_hidden_label():
    page = FakePage(role_visible=True, text_visible=False)
    loc = BD._locate_text(page, "Where to?")
    assert loc.tag == "role:combobox#0"   # the visible combobox, not the hidden label


def test_falls_back_to_first_visible_text_when_no_role():
    # No interactive control, but a visible text node exists at index 1.
    page = FakePage(role_visible=False, text_visible=True)
    loc = BD._locate_text(page, "Where to?")
    assert loc.tag == "text#1"            # skipped the hidden index-0 label


def test_last_resort_first_match_when_nothing_visible():
    page = FakePage(role_visible=False, text_visible=False)
    loc = BD._locate_text(page, "Where to?")
    assert loc.tag == "text.first"        # original behavior — surfaces a clear error


def test_selector_takes_precedence_and_prefers_visible():
    page = FakePage()
    loc = BD._locate(page, {"op": "click", "selector": "button#go"})
    # Selector path now picks the first VISIBLE match (FakePage.locator → visible).
    assert loc.tag == "css:button#go#0"


def test_first_visible_is_bounded():
    # 100 matches, none visible → must not scan all of them.
    scanned = {"n": 0}

    class Counting(FakeLocator):
        def nth(self, i):
            scanned["n"] += 1
            return FakeLocator(f"x#{i}", [False])

    loc = Counting("big", [False] * 100)
    assert BD._first_visible(loc, limit=8) is None
    assert scanned["n"] <= 8


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
