# Fiche d’essai N69AP

Utilise une fiche par essai. Garde le dossier brut sur ton ordinateur. Les commandes ci-dessous se lancent depuis le dépôt avec Python 3.11 ou ultérieur.

| À relever | Valeur |
|---|---|
| Mode choisi (ou « inconnu ») | |
| Heure locale et décalage UTC au début | |
| Dernier message affiché par l’ordinateur et heure | |
| Dernière image visible sur le SE et heure | |
| Heure du retour visible sous iOS 15, le cas échéant | |
| Identifiant de capture et chemin affichés par l’outil | |

**Collecter.** Si tu utilises déjà la commande de capture intégrée à l’envoi, elle relève les empreintes avant l’envoi puis lance la collecte après le retour de la commande hôte. Une capture séparée reste possible depuis le bouton **Collect diagnostics** ou depuis le terminal. Pour un mode incertain, omets `--mode`. Un seul collecteur suffit.

```sh
./n69ui logs --mode full --wait 180
```

Le résumé affiche le mode demandé et celui du manifeste. `mismatch` signale une contradiction. `unavailable` ou `invalid_mode` laisse le mode construit inconnu. Une capture séparée relève les images présentes sur disque à cet instant. Le répertoire imprimé à la fin contient l’identité de l’essai, la chronologie UTC et les rapports d’origine disponibles.

**Lire et partager.** Remplace les chemins d’exemple par les dossiers indiqués par l’outil.

```sh
./n69ui summary logs/capture-EXEMPLE
./n69ui share logs/capture-EXEMPLE essai-soir.tar.gz
./n69ui compare logs/capture-PREMIER logs/capture-SECOND
./n69ui coverage
```

L’archive contient le résumé, les événements reconnus de la chronologie et les champs structurés autorisés. Les identifiants matériels, sorties SSH, tickets, noms de fichiers de panic et textes libres restent dans le dossier privé. Pour un panic pertinent, lis localement son heure, son build, son motif et le service watchdog éventuel avant de choisir un extrait à communiquer.

**Interpréter.** Une réponse du téléphone avec le build et un ECID concordant situe le SE à l’instant de cette réponse. Un rapport daté dans la fenêtre de l’essai reste à attribuer. Une déconnexion USB, un échec SSH ou un rétroéclairage ne datent pas le reset. Si deux événements ne peuvent pas être ordonnés, inscris « ordre inconnu ».

## Une question par essai

Tant que l’entrée dans le noyau 20H392 reste inconnue, garde la question initiale `donor-entry` pour les trois modes. Après une trace attribuable, choisis une seule observation avec `--question` et consigne son heure.

| Mode noyau | Changements par rapport au mode précédent | Observation à choisir selon l’avancement |
|---|---|---|
| minimal | Socle commun, retrait des pilotes iPad concernés et diagnostic de panic | Entrée dans le noyau donneur sur ce SE |
| safe-minimal | PMU et adaptation SEP | Probe PMU (`pmu`) ou réponse SEP (`sep`) |
| full | Chestnut, Pinot et SPI/SPIN69 | Alimentation de la dalle (`display-power`), image (`display`) ou entrée tactile (`touch`) |

Les trois modes conservent le groupe rootfs `core`. Le résumé indique les patchs noyau du manifeste et les ressources rootfs du dernier reçu local de l’installateur. Un ancien manifeste ou un reçu absent laisse la sélection correspondante inconnue. Comparer deux modes qui changent plusieurs pilotes ne permet pas d’isoler la cause d’un redémarrage.

Une réponse USB en direct du SE identifié annonçant 20H392 après l’envoi peut servir de référence de réponse du système. La capture conserve la sortie brute, son empreinte et les six empreintes hôte. L’identité précise du noyau exécuté et l’affichage de SpringBoard demandent leurs propres observations.
