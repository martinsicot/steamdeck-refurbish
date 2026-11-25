# HANDOFF — Alerte stock Steam Deck reconditionnés (France)

> Document de passation : à lire **en premier** pour reprendre le projet (humain ou IA).
> Il décrit l'état réel au **18/11/2026**. Le code du repo fait foi ; ce document explique le pourquoi,
> ce qui a été vérifié, et les pièges déjà rencontrés.

## 1. Résumé

- **But** : recevoir un message **Telegram** quand un Steam Deck *Valve Certified Refurbished*, **n'importe quel modèle**,
  passe en stock (et quand il repasse en rupture) sur
  <https://store.steampowered.com/sale/steamdeckrefurbished>, **région France**.
- **Où ça tourne** : GitHub Actions, sur le repo public `martinsicot/steamdeck-refurbish`, toutes les ~5 min de 6h à minuit, toutes les ~15 min de minuit à 6h (heure de Paris).
- **Déclencheurs** : le cron GitHub **et** une crontab sur la machine locale du propriétaire (voir §6 et §7).
- **État au 18/11/2026** : tout fonctionne de bout en bout (région FR confirmée depuis GitHub, Telegram OK). Les 5 modèles sont en rupture.

## 2. Architecture

```
 cron GitHub (schedule)  ─┐
                          ├─► workflow "Steam Deck refurb alert" (.github/workflows/steamdeck.yml)
 crontab locale ──► gh workflow run (workflow_dispatch) ─┘
                                   │
                    job gate : run planifié hors de la plage de son cron ?  ─► oui : stop
                                   │ non
                    job check : python check_stock.py
                                   │   Playwright/Chromium charge la page (rendu JS),
                                   │   lit les 5 lignes produit (modèle, capacité, prix, bouton)
                                   ├─► compare avec state.json ─► message Telegram si changement
                                   ├─► commit de state.json s'il a changé (github-actions[bot])
                                   └─► artefact "debug" (page.png + page.html, 2 jours)
```

## 3. Fichiers du repo

| Fichier | Rôle |
|---|---|
| `check_stock.py` | Script principal. `DETECT_JS` (exécuté dans la page) trouve chaque ligne produit, `diff_state()` calcule les modèles apparus ou disparus, `main()` envoie Telegram et écrit `state.json`. |
| `.github/workflows/steamdeck.yml` | Workflow planifié et manuel : jobs `gate` puis `check`, upload du debug, commit de `state.json`. Input `test_telegram` pour un message de test. |
| `.github/workflows/tests.yml` | `pytest` à chaque push (sauf les commits qui ne touchent que `state.json`). |
| `tests/test_detect.py` | Détection sur des HTML simulés, dont `real_steam` (structure DOM réelle relevée le 17/11/2026), transitions d'état, migration de l'ancien format, échec si aucun produit. |
| `state.json` | Modèles actuellement en stock. Écrit uniquement par l'Action. |
| `local/` | Outils de déclenchement et de journalisation **sur la machine locale** (voir §7). |
| `requirements.txt` | `playwright==1.48.0`. |
| `README.md` | Mise en place rapide pour un humain. |

## 4. Faits vérifiés sur la page Steam (17/11/2026)

- **Rendu JS** : un simple `curl` ne voit pas les produits, d'où l'usage de Playwright.
- **Les produits sont affichés même en rupture**, une ligne par modèle, avec un bouton « Out of stock ».
  L'ancienne hypothèse « aucune carte affichée en rupture » était **fausse**. Structure d'une ligne :
  ```html
  <div class="Panel Focusable"> … <div class="SppL95yx9BU-">Steam Deck 512 GB OLED - Valve Certified Refurbished</div>
    … <div class="GW1050ijiP0- CartBtn"><span> Out of stock</span></div>
    … <div class="OySWwOLLxFc-">619,00€</div> …
  ```
  Les classes sont générées (hash) : le script ne s'y fie pas. Il cherche le texte du bouton, puis remonte au plus petit
  ancêtre qui ne contient qu'une seule capacité.
- **Modèles et prix FR** : OLED 512 Go 619 €, OLED 1 To 739 €, LCD 64 Go 299 €, LCD 256 Go 339 €, LCD 512 Go 379 €.
- **Région** : lue dans le HTML (`"COUNTRY":"FR"`, parfois encodé en `&quot;`). `?cc=FR` **suffit** : un runner GitHub (IP US) obtient bien `FR`.
- **API morte** : `IPhysicalGoodsService/CheckInventoryAvailableByPackage` renvoie 404 (packageids 903905-903907, 1202542, 1202547).
- **Jamais observé** : une ligne **en stock**. Le texte « Add to Cart » est supposé (le script accepte aussi « Ajouter au panier », « Buy now », « Acheter »).
  Au premier restock, vérifier dans les logs que la ligne apparaît en `EN STOCK`.

## 5. Logique d'alerte et `state.json`

- Chaque produit reçoit une clé `MODELE-CAPACITE` : `OLED-512GB`, `OLED-1TB`, `LCD-64GB`, `LCD-256GB`, `LCD-512GB`
  (`?-…` si le modèle n'a pas pu être lu).
- Format : `{"available": {"OLED-512GB": {"price": "619,00€", "since": "<ISO UTC>"}}}`.
  L'ancien format `{"in_stock": true}` est migré automatiquement en `OLED-512GB`.
- À chaque run : **un seul** message pour les modèles apparus (avec leur prix et le lien), **un seul** pour les modèles
  repassés en rupture, et **rien** si rien ne change.
- Le run **échoue volontairement**, ce qui déclenche un e-mail GitHub, si : la page ne charge pas, la région n'est pas `FR`,
  ou **aucun produit** n'est reconnu (signe que la page a changé). Si l'envoi Telegram échoue, `state.json` n'est pas
  commité et l'alerte repart au run suivant.
- **Limite connue** : si la région n'est pas trouvée dans le HTML (`None`), la vérification de région est ignorée (pas d'échec).

## 6. Planification GitHub

- Deux crons, en **UTC**, qui évitent les créneaux :00 et :30, très chargés :
  - jour : `2-59/5 4-22 * * *` (toutes les 5 min). La plage 4h-22h UTC couvre 6h-minuit à Paris en été comme en hiver ;
  - nuit : `4-59/15 0-4,22,23 * * *` (toutes les 15 min). La plage 22h-4h UTC couvre 0h-6h à Paris en été comme en hiver.
  Le job `gate` identifie le cron via `github.event.schedule` et ne garde que sa plage exacte en heure de Paris
  (jour : à partir de 06:00 ; nuit : 00:00-05:59). Les déclenchements manuels passent toujours.
- **Incident du 17/11/2026** : **aucun** run `schedule` pendant environ 8h30 après le dernier push du workflow.
  Premier run planifié à 22:50 (heure de Paris), réussi. Pistes testées entre-temps : commits rattachés à un compte GitHub
  (e-mail noreply), décalage des minutes. Cause exacte inconnue : GitHub met parfois des heures à prendre en compte un
  planning sur un repo neuf ou après un push du workflow.
- Même actif, le cron GitHub peut **retarder** ou **sauter** des créneaux. C'est pour ça qu'il existe le déclencheur local (§7).
- GitHub **désactive les crons après 60 jours sans activité** sur le repo. Les commits de `state.json` comptent comme activité,
  mais seulement en cas de changement de stock. Pour réactiver : onglet Actions, *Enable workflow*.

## 7. Déclenchement et journaux locaux (`local/`)

Installés sur la machine du propriétaire, dans `~/.local/bin/` (copie de référence dans `local/`, installation : `local/install.sh`).

- **Crontab** (`local/crontab.txt`) : `*/5 6-23 * * *` et `*/15 0-5 * * *`, en heure locale (Paris), lancent `steamdeck-trigger`.
- **`steamdeck-trigger`** : `gh workflow run`, retrouve le run créé, attend sa fin (max 8 min), lit son log, puis ajoute
  **une ligne JSON** dans `~/.local/state/steamdeck-trigger.log`, et **une ligne de tableau** dans `~/.local/state/steamdeck-stock.txt`.
  Ses erreurs propres vont dans `~/.local/state/steamdeck-trigger.err`.
  Exemple de ligne JSON :
  ```json
  {"date":"2026-11-17 15:49:59","triggered":true,"run_id":44107270057,"url":"https://github.com/…/actions/runs/44107270057",
   "status":"completed","conclusion":"success","skipped_night":false,"region":"FR","in_stock":[],
   "products":[{"model":"OLED","capacity":"512GB","price":"619,00€","in_stock":false}, …],"error":null}
  ```
- **`steamdeck-table`** : convertit le log JSON en tableau (`DISPO` en stock, `-` en rupture, `?` inconnu).
- **`steamdeck-status [N|-f]`** : en-tête et N dernières lignes du tableau, ou suivi en direct avec `-f`.
- Le premier fichier de log contient aussi quelques lignes texte plus anciennes (`OK déclenchement envoyé`).
  Les outils les ignorent (ils ne lisent que les lignes commençant par `{`).
- **Fonctionne** avec la session ouverte, écran verrouillé compris. **Ne fonctionne pas** si la machine est en veille ou éteinte
  (créneaux non rattrapés), ni de façon certaine avec la session fermée (le token `gh` est dans le trousseau de la session ;
  solution : `gh auth login --insecure-storage`).
- Les deux déclencheurs coexistent sans doublon d'alerte grâce à `state.json` et au `concurrency` du workflow.

## 8. Configuration GitHub (déjà en place)

- Repo **public**, ce qui donne des minutes Actions illimitées. Environ 216 runs par jour, d'à peu près 1 min chacun.
- Secrets Actions : `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. **Jamais** dans le code ni dans ce document.
- *Settings → Actions → General → Workflow permissions* : **Read and write** (nécessaire au commit de `state.json`).
- `gh` est connecté sur la machine locale avec le compte propriétaire `martinsicot`.

## 9. Conventions à respecter

- **Identité git** (config locale du repo) : `martinsicot <81321410+martinsicot@users.noreply.github.com>`.
  Ne **jamais** committer avec l'e-mail professionnel de la config globale.
- **Dates de commit en UTC** : `GIT_AUTHOR_DATE` et `GIT_COMMITTER_DATE` au format `…+0000`.
- **Pas de données personnelles** dans le repo (e-mail, chat id, token). Le repo est public.
- Ne pas réécrire l'historique ni forcer le push sans accord explicite.
- Langue du projet : français (logs, messages, docs).

## 10. Commandes utiles

```bash
# Tests et run local (sans secrets : n'envoie rien tant que rien n'est en stock)
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests/
STATE_FILE=/tmp/state.json .venv/bin/python check_stock.py

# GitHub
gh workflow run steamdeck.yml -f test_telegram=true                  # message Telegram de test
gh run list -w steamdeck.yml -e schedule -L 10                       # runs déclenchés par le cron GitHub
gh run view <id> --log | grep -E "Région|EN STOCK|rupture|ERREUR"

# Local
steamdeck-status            # tableau des 20 derniers runs
steamdeck-status -f         # suivi en direct
grep DISPO ~/.local/state/steamdeck-stock.txt
```

## 11. Pistes d'amélioration (non faites)

1. **Endpoint réseau du stock** : trouver dans DevTools → Network la requête qui renvoie le stock, et remplacer Playwright
   par un simple appel HTTP (run de quelques secondes).
2. **Heartbeat Telegram** hebdomadaire (« le bot tourne toujours »).
3. **cron-job.org** : déclencher `workflow_dispatch` depuis Internet, si la machine locale ne suffit pas et que le cron GitHub reste irrégulier.
   Il faut un token à accès limité (ce repo uniquement, permission *Actions : Read and write*).
4. Rendre bloquante l'absence de région détectée (§5, limite connue).
