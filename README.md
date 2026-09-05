# Veille d'alternances « gestionnaire de paie » vers Telegram

Ce programme vous alerte sur Telegram pour les nouvelles annonces correspondant à **gestionnaire de paie**, en alternance ou en emploi classique. Les postes classiques peuvent ainsi être contactés pour proposer une alternance. Il mémorise les annonces déjà envoyées dans `state.json`, afin de ne pas vous les renvoyer.

## Sources

| Source | Mode de collecte | Configuration |
|---|---|---|
| France Travail | API officielle | Identifiant et secret d'application France Travail |
| La bonne alternance | API officielle | Clé API La bonne alternance |
| HelloWork | Une page de résultats publique, une requête par exécution | Aucune |
| Indeed | Emails des alertes officielles, lus via IMAP (facultatif) | Compte Indeed + mot de passe d'application de la boîte email |

Indeed n'est volontairement pas « scrapé » : le site propose des alertes emploi officielles et l'automatisation ne contourne ni protections ni conditions d'accès. Créez une alerte Indeed avec `gestionnaire de paie` et votre zone géographique ; le programme transférera les liens reçus à Telegram si la partie IMAP est configurée.

### Exclure des annonces indésirables

Dans `.env`, renseignez `EXCLUDED_KEYWORDS` avec des mots ou expressions séparés par des virgules. Par exemple :

```env
EXCLUDED_KEYWORDS=formation,cfa,ecole,universite
```

Une annonce dont le titre ou la description contient l'un de ces termes ne sera pas envoyée. Laissez cette ligne vide si vous ne souhaitez rien exclure. Les accents ne sont pas obligatoires : `ecole` et `école` peuvent être indiqués tous les deux si nécessaire.

## Mise en route

1. Créez un bot Telegram auprès de **@BotFather**. Ouvrez une discussion avec ce bot, envoyez-lui un message, puis récupérez votre identifiant de discussion via :
   `https://api.telegram.org/botVOTRE_TOKEN/getUpdates`
2. Copiez `.env.example` en `.env` et complétez au minimum `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`. Ajoutez les accès API pour France Travail et La bonne alternance quand vous les avez.
3. Installez les dépendances et lancez un premier essai :

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/python jobs_to_telegram.py
   ```

Au premier lancement, les annonces actuellement trouvées sont envoyées. Les lancements suivants n'enverront que les nouveautés. Pour repartir de zéro, supprimez seulement `state.json`.

## Exécution automatique (macOS)

Pour une vérification chaque matin à 08:00, ouvrez `crontab -e` et ajoutez (adaptez le chemin) :

```cron
0 8 * * * /Users/charles/Documents/Codex/2026-09-05/po/.venv/bin/python /Users/charles/Documents/Codex/2026-09-05/po/jobs_to_telegram.py >> /Users/charles/Documents/Codex/2026-09-05/po/veille.log 2>&1
```

La bonne alternance recommande de respecter ses limites de débit ; ce programme effectue au plus une recherche par source et par lancement.

## Fonctionnement sans ordinateur : GitHub Actions

1. Créez un dépôt **privé** sur GitHub, puis importez les fichiers de ce dossier (y compris `.github/workflows/veille.yml`, mais jamais `.env`).
2. Dans le dépôt, ouvrez **Settings → Secrets and variables → Actions**, puis créez les secrets qui portent les mêmes noms que les champs renseignés dans `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, et éventuellement les clés France Travail, La bonne alternance et IMAP).
3. Dans l'onglet **Actions**, autorisez le workflow « Veille offres vers Telegram ». Il s'exécute automatiquement toutes les six heures. Vous pouvez aussi l'exécuter manuellement avec le bouton **Run workflow**.

Le workflow enregistre `state.json` dans le dépôt après chaque exécution : cette mémoire évite les doublons, même lorsque GitHub exécute le programme sur un nouveau serveur. Gardez le dépôt privé ; le fichier ne contient que des identifiants hachés d'annonces, pas les secrets Telegram.
