# Alerte Steam Deck reconditionné (France)

GitHub Action qui vérifie toutes les ~15 min (de 6h à minuit, heure de Paris) la page
[Steam Deck Certified Refurbished](https://store.steampowered.com/sale/steamdeckrefurbished)
(région FR) et envoie un message Telegram dès qu'**un modèle** (LCD 64/256/512 Go, OLED 512 Go/1 To)
passe en stock, puis quand il repasse en rupture.

- `check_stock.py` : charge la page avec Playwright (rendu JS), lit chaque ligne produit
  (modèle, capacité, prix, bouton « Add to Cart » / « Out of stock »).
- `state.json` : modèles actuellement en stock, commité par l'Action pour éviter les doublons.
- Le run échoue (e-mail GitHub) si la page ne charge pas, si la région ≠ FR ou si aucun produit n'est reconnu.
- Artefact `debug` (screenshot + HTML) à chaque run, conservé 2 jours.

## Mise en place

1. **Bot Telegram** : `@BotFather` → `/newbot` → token. Envoyer `/start` au bot, puis ouvrir
   `https://api.telegram.org/bot<TOKEN>/getUpdates` pour lire `chat.id`.
2. **Repo public** (minutes Actions illimitées).
3. **Secrets** (*Settings → Secrets and variables → Actions*) : `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
4. **Permissions** : *Settings → Actions → General → Workflow permissions → Read and write*.
5. **Premier run** : *Actions → Steam Deck refurb alert → Run workflow*, cocher `test_telegram`.
   Vérifier la réception du message et `Région détectée par Steam : FR` dans les logs.
6. GitHub désactive les crons après 60 jours sans activité sur le repo : cliquer sur *Enable workflow*.

## En local

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests/
.venv/bin/python check_stock.py   # sans secrets : n'envoie rien tant que rien n'est en stock
```
