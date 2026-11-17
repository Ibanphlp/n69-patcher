# N69AP boot diagnostics — prochain essai matériel

Objectif : séparer un échec avant iBEC, un rejet du KernelCache, un panic précoce et un blocage tardif. Les contrôles host ne prouvent pas que `launchd` a démarré.

## Avant de connecter le téléphone

1. Vérifier la branche/artifacts et ticket attendus, puis lancer `N69_PREFLIGHT_ONLY=1 ./send_n69_boot.sh`. Il doit afficher quatre lignes `IMG4 preflight OK` (KernelCache, DeviceTree, TrustCache, SEP). En cas d’échec, conserver le message : le script s’arrête avant l’étape de connexion/DFU.
2. Ouvrir une capture UART avant le boot si le câble est disponible : voir **N69AP_BOOT_GUIDE.md → Troubleshooting → Serial/UART checklist**. Enregistrer la session complète, pas seulement les lignes de panic.
3. Mettre l’iPhone SE (1re génération) en DFU, démarrer `./send_n69_boot.sh`, confirmer l’invite lorsque l’écran est noir et noter l’heure de début. Le script crée `logs/boot-<timestamp>.log`; garder ce fichier et la sortie du terminal.

## Pendant et après le boot

- **Pas de logo Apple après l’envoi de iBEC** : vérifier dans le log les codes retour de `send iBEC`, `iBEC go` (uniquement si demandé pour CPID) et `show iBEC boot logo`. Si une de ces étapes échoue, le problème est au niveau pwn/iBSS/iBEC ou de la communication irecovery, avant l’exécution du noyau. Si elles retournent 0 mais que le logo ne s’affiche pas, noter aussi ce résultat : un code retour réussi ne prouve pas que l’écran l’a rendu.
- **Logo visible** : iBEC a accepté les commandes du logo; cela ne prouve pas que `bootx` a été accepté ni que le noyau a démarré. Chercher les étapes d’envoi DT/TC/SEP/KC et `bootx` dans le log et vérifier leurs codes retour.
- **Préflight OK, `bootx` retourne une erreur** : conserver l’erreur et le contexte de récupération USB; l’échec est après iBEC mais ne démontre pas un panic noyau.
- **UART montre un panic avant l’initialisation de PE_Video/Pinot** : l’absence d’overlay graphique est attendue. Le hook écrit aussi un marqueur en RAM dans le mot `reserved` de son état : `0x4e363901` (entrée panic), `0x4e363902` (formatage), `0x4e363903` (capture de pile). Cette donnée n’est pas persistante et n’est pas imprimée par elle-même sur UART; pour la lire, il faut un accès mémoire/debugger compatible. Les sorties série du panic restent le canal pratique pour dater et identifier ce cas.
- **Messages UART continuent après le panic apparent ou atteignent les services de démarrage** : noter la dernière ligne et si le téléphone reste USB connecté, redémarre, ou retourne en Recovery. Un message montrant `launchd` est nécessaire pour conclure à un boot utilisateur tardif; un écran noir seul ne permet pas de conclure.

## À rapporter

Joindre `logs/boot-*.log`, le transcript UART (si disponible), mode (`safe-minimal`/`full`), résultat du préflight et chronologie observée (logo, bootx, Recovery/redémarrage). Sans le breakout série, ces observations réduisent l’espace des causes mais ne prouvent pas un boot complet jusqu’à `launchd`.
