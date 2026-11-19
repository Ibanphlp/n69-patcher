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
