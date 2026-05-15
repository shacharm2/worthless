"""Static checks for the public website homepage story."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
WEBSITE = REPO_ROOT / "website"
HOMEPAGE = WEBSITE / "index.html"


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def homepage_text() -> str:
    return HOMEPAGE.read_text(encoding="utf-8")


def test_homepage_uses_approved_wor405_story() -> None:
    html = " ".join(homepage_text().split())
    lowered = html.lower()

    assert "Make leaked AI keys useless" in html
    assert "Put a seatbelt on your .env" in html
    assert "copied key alone cannot call the provider" in html
    assert "scanners find leaks" in lowered
    assert "vaults store secrets" in lowered
    assert "provider dashboards" in lowered
    assert "gateways manage spend" in lowered


def test_homepage_routes_visitors_to_install_docs_github_and_audit() -> None:
    html = homepage_text()

    assert "curl -sSL https://worthless.sh | sh" in html
    assert 'href="https://docs.wless.io/install/' in html
    assert 'href="https://github.com/shacharm2/worthless"' in html
    assert "Audit with AI" in html
    assert "https://raw.githubusercontent.com/shacharm2/worthless/main/install.sh" in html


def test_homepage_avoids_disallowed_launch_claims() -> None:
    html = homepage_text()
    forbidden = [
        "hard spend cap",
        "hard spending cap",
        "nothing. the leaked key",
        "reset-budget",
        "native Windows support",
        "AWS",
        "Stripe",
        "any secret",
        "any key",
        "get_key",
        "worthless.cloud",
        "waitlist",
        "localStorage",
        "tally.so",
    ]

    lowered = html.lower()
    for phrase in forbidden:
        assert phrase.lower() not in lowered


def test_homepage_local_links_resolve() -> None:
    parser = LinkParser()
    parser.feed(homepage_text())

    missing: list[str] = []
    for href in parser.hrefs:
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        local_path = href.split("#", 1)[0].split("?", 1)[0]
        if not local_path:
            continue
        if not (WEBSITE / local_path).exists():
            missing.append(href)

    assert missing == []
