#!/usr/bin/env python3
"""
Alerte Telegram quand un Steam Deck "Certified Refurbished" (n'importe quel
modèle : LCD / OLED, toutes capacités) est disponible sur le Steam Store (France).

- Charge la page avec Chromium headless (le bloc d'achat est généré en JS).
- Liste les produits ayant un bouton "Add to Cart" (modèle + capacité + prix).
- N'envoie une alerte qu'au changement d'état de chaque modèle (rupture -> stock,
  stock -> rupture), grâce au fichier state.json commité par la GitHub Action.
"""
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

PAGE_URL = "https://store.steampowered.com/sale/steamdeckrefurbished"
# cc=FR force la région du store (les runners GitHub sont aux US), l=english stabilise les textes.
FETCH_URL = PAGE_URL + "?cc=FR&l=english"
EXPECTED_COUNTRY = os.environ.get("EXPECTED_COUNTRY", "FR")
STATE_FILE = pathlib.Path(os.environ.get("STATE_FILE", "state.json"))
DEBUG_DIR = pathlib.Path(os.environ.get("DEBUG_DIR", "debug"))

# Script exécuté dans la page : liste les produits (bouton "Add to Cart" ou "Out of stock" visible).
# Structure réelle (nov. 2026) : une ligne par produit, ex.
#   "Steam Deck 512 GB OLED - Valve Certified Refurbished" | <div class="CartBtn">Out of stock</div> | 619,00€
DETECT_JS = r"""
() => {
  const norm = s => (s || "").replace(/\s+/g, " ").trim();
  const btnRe = /^(add to cart|ajouter au panier|buy now|acheter)$/i;
  const oosRe = /^(out of stock|rupture de stock|en rupture|épuisé|sold out)$/i;
  const capRe = /\b(?:64|256|512)\s?GB(?![a-z])|\b1\s?TB(?![a-z])/gi;
  const modelOf = t => {
    const o = /OLED/i.test(t), l = /\bLCD/i.test(t);
    return o && !l ? "OLED" : l && !o ? "LCD" : (o && l ? "BOTH" : null);
  };
  const items = [];
  const seen = new Set();
  const candidates = [...document.querySelectorAll("a, button, [role=button], div, span")]
    .filter(e => (btnRe.test(norm(e.innerText)) || oosRe.test(norm(e.innerText))) && e.getClientRects().length > 0);
  for (const btn of candidates) {
    // 1) plus petit ancêtre qui mentionne une capacité = la "carte" produit
    let card = btn, caps = [];
    for (let i = 0; i < 12 && card; i++) {
      card = card.parentElement;
      if (!card) break;
      caps = [...new Set((norm(card.innerText).match(capRe) || []).map(c => c.replace(/\s/g, "").toUpperCase()))];
      if (caps.length) break;
    }
    if (!card || caps.length !== 1) continue; // ambigu ou introuvable
    if (seen.has(card)) continue;
    seen.add(card);
    // 2) OLED ou LCD : dans la carte, sinon dans le titre de section le plus proche au-dessus
    let model = null, el = card;
    outer: for (let i = 0; i < 8 && el; i++) {
      const m = modelOf(norm(el.innerText));
      if (m === "OLED" || m === "LCD") { model = m; break; }
      for (let s = el.previousElementSibling; s; s = s.previousElementSibling) {
        const ms = modelOf(norm(s.innerText));
        if (ms === "OLED" || ms === "LCD") { model = ms; break outer; }
      }
      el = el.parentElement;
    }
    const text = norm(card.innerText);
    const price = (text.match(/\d+[.,]\d{2}\s?€|€\s?\d+[.,]?\d*/) || [null])[0];
    const in_stock = btnRe.test(norm(btn.innerText));
    items.push({ capacity: caps[0].toUpperCase().replace(/\s/g, ""), model, price, in_stock, text: text.slice(0, 300) });
  }
  return items;
}
"""


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def send_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read())
    if not body.get("ok"):
        raise RuntimeError(f"Telegram a refusé le message : {body}")
    log("Message Telegram envoyé.")


def load_state() -> dict:
    """État = {"available": {clé: {"price": ..., "since": ...}}}."""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            data = {}
        if isinstance(data.get("available"), dict):
            return data
        if data.get("in_stock"):  # ancien format (OLED 512 uniquement)
            return {"available": {"OLED-512GB": {"price": None, "since": data.get("since")}}}
    return {"available": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def item_key(item: dict) -> str:
    return f"{item.get('model') or '?'}-{item['capacity']}"


def label(key: str) -> str:
    model, cap = key.split("-", 1)
    cap = cap.replace("GB", " Go").replace("TB", " To")
    return f"Steam Deck {model if model != '?' else '(modèle ?)'} {cap}"


def diff_state(state: dict, items: list[dict], now: str) -> tuple[dict, list[str], list[str]]:
    """Renvoie (nouvel état, clés apparues, clés disparues)."""
    prev = state.get("available", {})
    current: dict[str, dict] = {}
    for it in items:
        k = item_key(it)
        if k in current:
            continue
        current[k] = {
            "price": it.get("price") or (prev.get(k) or {}).get("price"),
            "since": (prev.get(k) or {}).get("since") or now,
        }
    appeared = sorted(k for k in current if k not in prev)
    gone = sorted(k for k in prev if k not in current)
    return {"available": current}, appeared, gone


def fetch_items() -> list[dict]:
    DEBUG_DIR.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            locale="en-US",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        page.goto(FETCH_URL, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass
        # Scroll pour déclencher le lazy-loading éventuel
        for _ in range(6):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(700)
        page.wait_for_timeout(2_000)

        html = page.content()
        body_text = page.inner_text("body")
        page.screenshot(path=str(DEBUG_DIR / "page.png"), full_page=True)
        (DEBUG_DIR / "page.html").write_text(html)

        if "Certified Refurbished" not in body_text:
            raise RuntimeError("La page ne semble pas s'être chargée correctement.")

        m = re.search(r'(?:"|&quot;)COUNTRY(?:"|&quot;):(?:"|&quot;)([A-Z]{2})', html)
        country = m.group(1) if m else None
        log(f"Région détectée par Steam : {country}")
        if EXPECTED_COUNTRY and country and country != EXPECTED_COUNTRY:
            raise RuntimeError(
                f"Steam affiche la région {country} au lieu de {EXPECTED_COUNTRY} : "
                "le stock lu ne serait pas celui de la France."
            )

        items = page.evaluate(DETECT_JS)
        browser.close()
        return items


def main() -> int:
    if os.environ.get("TEST_TELEGRAM", "").lower() == "true":
        send_telegram("Test OK : le bot d'alerte Steam Deck reconditionné fonctionne.")

    products = fetch_items()
    for it in products:
        status = "EN STOCK" if it["in_stock"] else "rupture"
        log(f"{status:8} : {it['model'] or '?'} {it['capacity']} {it['price'] or ''} | {it['text'][:120]}")
    if not products:
        # Aujourd'hui Steam affiche chaque modèle même en rupture : 0 produit = page modifiée.
        raise RuntimeError("Aucun produit reconnu sur la page : la structure a peut-être changé.")
    items = [i for i in products if i["in_stock"]]

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state, appeared, gone = diff_state(load_state(), items, now)
    log(f"En stock : {', '.join(sorted(state['available'])) or 'rien'}")

    if appeared:
        lines = [
            f"• {label(k)} ({state['available'][k]['price'] or 'prix non lu'})" for k in appeared
        ]
        send_telegram("Steam Deck reconditionné EN STOCK !\n" + "\n".join(lines) + f"\n{PAGE_URL}")
    if gone:
        send_telegram(
            "De nouveau en rupture :\n" + "\n".join(f"• {label(k)}" for k in gone)
        )
    save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # l'échec fait passer le run en rouge -> e-mail de GitHub
        log(f"ERREUR : {e}")
        sys.exit(1)
