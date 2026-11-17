# Recap — Alerte Telegram Steam Deck OLED 512 Go reconditionné

> Handoff pour reprendre le projet dans Claude Code (local). Contexte, état actuel, points ouverts, puis le code complet.

## Objectif

Être prévenu sur Telegram quand le **Steam Deck OLED 512 Go – Valve Certified Refurbished** revient en stock sur
https://store.steampowered.com/sale/steamdeckrefurbished (région **France**).
Solution retenue : **GitHub Action gratuite** (repo public) qui tourne toutes les ~15 min.

## Ce qu'on sait de la page Steam

- **Rendu JS** : le bloc produits/achat est rendu en JavaScript. Un simple `requests.get` / WebFetch ne voit que menu + footer, d'où le choix de Playwright (Chromium headless).
- **Hors stock** : quand tout est en rupture, aucune carte produit n'est affichée ; seule la FAQ dit « Certified Refurbished units are currently out of stock ».
- **Ancienne API morte** : `https://api.steampowered.com/IPhysicalGoodsService/CheckInventoryAvailableByPackage/v1?origin=https://store.steampowered.com&country_code=FR&packageid=...` renvoie **404**. Packageids testés : 903905, 903906, 903907, 1202542, 1202547.
- **Région** : la région est lue dans le HTML (`"COUNTRY":"FR"`, parfois encodé `&quot;`). Depuis une IP française on obtient `FR`.
- **Statut au 15/11/2026** : OLED 512 Go en rupture.

## Fonctionnement du script (`check_stock.py`)

1. Ouvre `…/steamdeckrefurbished?cc=FR&l=english` avec Playwright, scrolle, attend le chargement.
2. Échoue (exit 1 → run rouge + e-mail GitHub) si la page n'a pas chargé ou si la région ≠ `FR`.
3. JS dans la page : pour chaque bouton visible « Add to Cart / Ajouter au panier », remonte jusqu'à la plus petite carte qui contient **une seule** capacité (64/256/512 GB, 1 TB). Le modèle OLED/LCD est lu dans la carte, sinon dans le titre de section au-dessus (frères précédents).
4. En stock = une carte `512GB` + `OLED`.
5. `state.json` (commité par l'Action) évite les doublons : un message au passage rupture → stock, un autre au retour en rupture.
6. Un screenshot et le HTML de la page sont uploadés en artefact `debug` (rétention 2 jours).
7. Avec `workflow_dispatch` et l'input `test_telegram=true`, le script envoie un message de test.

## Testé / non testé

- **OK** : détection sur 4 pages HTML simulées (OLED 512 en stock ; seulement LCD 512 ; modèle dans la carte ; rien en stock).
- **OK** : transitions d'état et anti-doublon (Telegram mocké).
- **OK** : compilation Python + YAML valide.
- **Non testé — jamais lancé sur le vrai Steam** : le cloud de Claude est bloqué par Steam.
- **Non testé — page en stock jamais vue** : la structure DOM réelle est supposée, donc les sélecteurs sont à valider au premier restock (logs `Achetable : …`).
- **Non testé — `?cc=FR` depuis un runner GitHub (IP US)** : on ne sait pas si Steam affiche FR pour le stock physique. À vérifier au 1er run (`Région détectée par Steam : FR`).

## Pistes d'amélioration (à faire dans Claude Code)

1. **Endpoint de stock** : en local, ouvrir la page dans Chrome → DevTools → Network, repérer la requête qui renvoie le stock (filtrer `api.steampowered.com` / `ajax`). Si trouvé : remplacer Playwright par un simple `requests` (run de quelques secondes, plus de dépendance Chromium).
2. **Région US** : si le runner GitHub donne US malgré `?cc=FR`, tester d'autres paramètres ou cookies côté requête ; en dernier recours, faire tourner le script sur une machine en France (cron local / Raspberry Pi / VPS FR).
3. **Test DOM réaliste** : récupérer le HTML d'une page où un autre modèle est en stock (artefact `debug/page.html`) et ajouter un test sur ce HTML.
4. **Optionnel** : ajouter un heartbeat Telegram hebdo (« le bot tourne toujours »).

## Mise en place (rappel)

1. **Bot Telegram** : `@BotFather` → `/newbot` → token. Envoyer `/start` au bot, puis ouvrir `https://api.telegram.org/bot<TOKEN>/getUpdates` → `chat.id`.
2. **Repo GitHub public** (minutes Actions illimitées). Pousser les fichiers ci-dessous, dont `.github/workflows/steamdeck.yml`.
3. **Secrets** : *Settings → Secrets and variables → Actions* → `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Ils sont chiffrés, masqués dans les logs et inaccessibles aux forks. Ne jamais les mettre dans le code, ne pas ajouter de collaborateurs.
4. **Premier lancement** : *Actions → Steam Deck refurb alert → Run workflow*, cocher le test Telegram. Vérifier la réception du message et la région `FR` dans les logs.
5. **Si « Save state » échoue** : *Settings → Actions → General → Workflow permissions → Read and write*.
6. **Inactivité** : GitHub désactive les crons d'un repo public après 60 jours sans activité. Il suffit de cliquer sur *Enable workflow* pour relancer.

## Arborescence

```
steamdeck-refurb-alert/
├── .github/workflows/steamdeck.yml
├── .gitignore
├── README.md
├── check_stock.py
└── requirements.txt
```

## Code complet

### `check_stock.py`

```python
#!/usr/bin/env python3
"""
Alerte Telegram quand le Steam Deck OLED 512 Go "Certified Refurbished"
est disponible sur le Steam Store (France).

- Charge la page avec Chromium headless (le bloc d'achat est généré en JS).
- Cherche un bouton "Add to Cart" rattaché à un produit "OLED" + "512 GB".
- N'envoie une alerte qu'au changement d'état (rupture -> stock, stock -> rupture),
  grâce au fichier state.json commité par la GitHub Action.
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

# Script exécuté dans la page : liste les produits achetables (bouton "Add to Cart" visible).
DETECT_JS = r"""
() => {
  const norm = s => (s || "").replace(/\s+/g, " ").trim();
  const btnRe = /^(add to cart|ajouter au panier|buy now|acheter)$/i;
  const capRe = /\b(?:64|256|512)\s?GB(?![a-z])|\b1\s?TB(?![a-z])/gi;
  const modelOf = t => {
    const o = /OLED/i.test(t), l = /\bLCD/i.test(t);
    return o && !l ? "OLED" : l && !o ? "LCD" : (o && l ? "BOTH" : null);
  };
  const items = [];
  const seen = new Set();
  const candidates = [...document.querySelectorAll("a, button, [role=button], div, span")]
    .filter(e => btnRe.test(norm(e.innerText)) && e.getClientRects().length > 0);
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
    items.push({ capacity: caps[0].toUpperCase().replace(/\s/g, ""), model, price, text: text.slice(0, 300) });
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
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"in_stock": False}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


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

    items = fetch_items()
    for it in items:
        log(f"Achetable : {it['model'] or '?'} {it['capacity']} {it['price'] or ''} | {it['text'][:120]}")
    if not items:
        log("Aucun produit reconditionné achetable sur la page.")

    target = [i for i in items if i["capacity"] == "512GB" and i["model"] == "OLED"]
    in_stock = bool(target)
    log(f"OLED 512 Go en stock : {in_stock}")

    state = load_state()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if in_stock and not state.get("in_stock"):
        price = target[0]["price"] or "prix non lu"
        send_telegram(
            f"Steam Deck OLED 512 Go reconditionné EN STOCK ({price}) !\n{PAGE_URL}"
        )
        state = {"in_stock": True, "since": now}
    elif not in_stock and state.get("in_stock"):
        send_telegram("Steam Deck OLED 512 Go reconditionné : de nouveau en rupture.")
        state = {"in_stock": False, "since": now}
    save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # l'échec fait passer le run en rouge -> e-mail de GitHub
        log(f"ERREUR : {e}")
        sys.exit(1)
```

### `.github/workflows/steamdeck.yml`

```yaml
name: Steam Deck refurb alert

on:
  schedule:
    - cron: "*/15 * * * *"   # toutes les 15 min (GitHub peut décaler de quelques minutes)
  workflow_dispatch:
    inputs:
      test_telegram:
        description: "Envoyer un message Telegram de test"
        type: boolean
        default: false

permissions:
  contents: write   # pour commiter state.json

concurrency:
  group: steamdeck-alert
  cancel-in-progress: false

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Cache Chromium (Playwright)
        uses: actions/cache@v4
        with:
          path: ~/.cache/ms-playwright
          key: pw-${{ runner.os }}-${{ hashFiles('requirements.txt') }}

      - name: Install
        run: |
          pip install -r requirements.txt
          python -m playwright install --with-deps chromium

      - name: Check stock
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          TEST_TELEGRAM: ${{ inputs.test_telegram }}
        run: python check_stock.py

      - name: Screenshot de debug
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: debug
          path: debug/
          retention-days: 2

      - name: Save state
        if: success()
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add state.json
          git diff --cached --quiet || (git commit -m "state: stock update" && git push)
```

### `requirements.txt`

```text
playwright==1.48.0
```

### `.gitignore`

```text
debug/
__pycache__/
```
