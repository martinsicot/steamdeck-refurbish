"""Tests de la détection (DETECT_JS sur HTML simulés) et des transitions d'état."""
import json
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check_stock  # noqa: E402

FAQ = "<div><h2>FAQ</h2><p>Certified Refurbished units are currently out of stock.</p></div>"


def card(title, price, button=True):
    btn = '<a class="btn">Add to Cart</a>' if button else '<span>Out of stock</span>'
    return f'<div class="card"><div class="title">{title}</div><div>{price}</div>{btn}</div>'


PAGES = {
    # Section OLED avec 512 et 1TB, section LCD avec 64 (sans bouton) et 256
    "sections": f"""
      <section><h2>Steam Deck OLED</h2>
        <div class="row">{card("512 GB", "419,00€")}{card("1 TB", "479,00€")}</div></section>
      <section><h2>Steam Deck LCD</h2>
        <div class="row">{card("64 GB", "279,00€", button=False)}{card("256 GB", "319,00€")}</div></section>
      {FAQ}""",
    "lcd_512_only": f"""
      <section><h2>Steam Deck LCD</h2><div>{card("512 GB", "359,00€")}</div></section>
      <section><h2>Steam Deck OLED</h2><div>{card("512 GB", "419,00€", button=False)}</div></section>
      {FAQ}""",
    "model_in_card": f"""<div>{card("Steam Deck OLED 512 GB", "419,00€")}</div>{FAQ}""",
    "nothing": f"<div><h1>Steam Deck Certified Refurbished</h1></div>{FAQ}",
    # Structure réelle relevée sur Steam (17/11/2026), une ligne en stock, les autres en rupture
    "real_steam": "".join(
        f'''<div class="Panel Focusable"><div class="_7Na7-QNyAn4-"><div><div class="SppL95yx9BU-">Steam Deck {name} - Valve Certified Refurbished</div></div><div class="vZlB-C6RUio-"><div class="OClIuwfiMKQ- zdh0LkaHoKw-"><div class="XPrPiD27WdE-"><div class="GW1050ijiP0- CartBtn"><span> {btn}</span></div><div class="ZLlm68-AqhM- StoreSalePriceWidgetContainer"><div class="OySWwOLLxFc-">{price}</div></div></div></div></div></div></div>'''
        for name, price, btn in [
            ("512 GB OLED", "619,00€", "Out of stock"),
            ("1TB OLED", "739,00€", "Add to Cart"),
            ("64 GB LCD", "299,00€", "Out of stock"),
            ("256 GB LCD", "339,00€", "Out of stock"),
            ("512 GB LCD", "379,00€", "Out of stock"),
        ]
    ) + FAQ,
}

EXPECTED = {
    "sections": {"OLED-512GB", "OLED-1TB", "LCD-256GB"},
    "lcd_512_only": {"LCD-512GB"},
    "model_in_card": {"OLED-512GB"},
    "nothing": set(),
    "real_steam": {"OLED-1TB"},
}


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser.new_page()
        browser.close()


@pytest.mark.parametrize("name", PAGES)
def test_detection(page, name):
    page.set_content(f"<html><body>{PAGES[name]}</body></html>")
    items = page.evaluate(check_stock.DETECT_JS)
    assert {check_stock.item_key(i) for i in items if i["in_stock"]} == EXPECTED[name]


def test_real_structure_sees_out_of_stock_rows(page):
    page.set_content(f"<html><body>{PAGES['real_steam']}</body></html>")
    items = page.evaluate(check_stock.DETECT_JS)
    assert {check_stock.item_key(i): i["price"] for i in items} == {
        "OLED-512GB": "619,00€", "OLED-1TB": "739,00€",
        "LCD-64GB": "299,00€", "LCD-256GB": "339,00€", "LCD-512GB": "379,00€",
    }


def test_price_read(page):
    page.set_content(f"<html><body>{PAGES['model_in_card']}</body></html>")
    (item,) = page.evaluate(check_stock.DETECT_JS)
    assert item["price"] == "419,00€"


# --- transitions d'état ---------------------------------------------------------

def it(model, cap, price="1,00€", in_stock=True):
    return {"model": model, "capacity": cap, "price": price, "in_stock": in_stock, "text": ""}


@pytest.fixture
def env(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(check_stock, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(check_stock, "send_telegram", sent.append)
    monkeypatch.delenv("TEST_TELEGRAM", raising=False)
    holder = {"items": []}
    monkeypatch.setattr(check_stock, "fetch_items", lambda: holder["items"])
    return sent, holder, tmp_path / "state.json"


def test_transitions(env):
    sent, holder, state_file = env

    holder["items"] = [it("OLED", "512GB", in_stock=False)]
    check_stock.main()  # rien en stock, pas d'état
    assert sent == []

    holder["items"] = [it("OLED", "512GB", "419,00€")]
    check_stock.main()
    assert len(sent) == 1 and "EN STOCK" in sent[0] and "OLED 512 Go" in sent[0] and "419,00€" in sent[0]

    check_stock.main()  # pas de changement -> pas de doublon
    assert len(sent) == 1

    holder["items"] = [it("OLED", "512GB"), it("LCD", "256GB")]
    check_stock.main()  # seul le nouveau est annoncé
    assert len(sent) == 2 and "LCD 256 Go" in sent[1] and "OLED" not in sent[1]

    holder["items"] = [it("OLED", "512GB", in_stock=False), it("LCD", "256GB")]
    check_stock.main()
    assert len(sent) == 3 and "rupture" in sent[2] and "OLED 512 Go" in sent[2]

    state = json.loads(state_file.read_text())
    assert set(state["available"]) == {"LCD-256GB"}


def test_legacy_state_migration(env):
    sent, holder, state_file = env
    state_file.write_text(json.dumps({"in_stock": True, "since": "2026-01-01T00:00:00+00:00"}))
    holder["items"] = [it("OLED", "512GB")]
    check_stock.main()  # déjà en stock dans l'ancien état -> pas de nouvelle alerte
    assert sent == []
    assert set(json.loads(state_file.read_text())["available"]) == {"OLED-512GB"}


def test_no_product_at_all_fails(env):
    _, holder, _ = env
    holder["items"] = []
    with pytest.raises(RuntimeError):
        check_stock.main()
