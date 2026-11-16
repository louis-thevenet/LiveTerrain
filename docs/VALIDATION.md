# Vérifications du prototype

Environnement : NixOS Linux x86_64, Unreal Engine 5.8.4, SDK Epic
v26 / Clang 20.1.8, Cesium for Unreal 2.31.0 (sources officielles),
cesium-native 0.66.0. Vérifications réalisées le 15 novembre 2026.

## Compilation

- Compilation de `extern/` par CMake/Ninja avec la toolchain Unreal officielle,
  puis installation des bibliothèques dans le plugin du projet : réussie.
- UBT `LiveTerrainEditor Linux Development` : `Result: Succeeded`.
- Relance incrémentale : aucune source C++ recompilée, UBT terminé en 8,71 s.
- Aucun fichier de l'installation Unreal modifié pour intégrer Cesium.
- Le descripteur officiel Cesium indique encore 5.6.0 : `setup` adapte
  uniquement son champ `EngineVersion` à 5.8.0 pour que le plugin soit chargé
  au bon moment (`PostConfigInit`). Son code C++ reste inchangé.
- Les sources de Cesium émettent encore des avertissements de dépréciation UE ;
  ils ne bloquent pas la compilation.

## Données réelles et tests

Source : dossier `geodata/` existant, acquisition de rayon 1 000 m centrée sur
48.79040813456057 N, 1.9750679431401221 E. Aucune nouvelle acquisition lancée.

- Vérification SHA-256 des fichiers avant conversion.
- 64 GLB, 1 893 boîtes de bâtiments, 352 388 triangles au total.
- Terrain au pas du MNT (5 m), textures préparées à 1 m, tuiles de 256 m.
- `make validate` : buffers, indices, bounding boxes, images non uniformes,
  positions ECEF, normales orientées vers le haut et sommets partagés valides sur les 64 tuiles réelles.
- `make test` : huit tests Python réussis (acquisition et encodeur GLB).
- Le test de l'encodeur utilise une petite fixture géométrique ; il ne remplace
  pas la conversion et la vérification des données IGN réelles ci-dessus.

Correction verticale choisie pour cet essai : **0 m explicite**, donc altitude
ellipsoïdale approximative. Le test ne certifie pas le datum vertical absolu,
la précision topographique ni le millésime des données sources.

Une première inspection visuelle a révélé des triangles de terrain orientés
vers le bas : la grille est parcourue du nord au sud. Le sens des indices a été
corrigé ; un test de régression et le validateur des tuiles réelles contrôlent
désormais explicitement l'orientation des faces et des normales. La capture
corrigée montre bien les orthophotos depuis le dessus.

## Reproduction

```bash
nix develop
make build
make validate
make test
make run RUN_ARGS='-GeoSmokeTest -ResX=1280 -ResY=720 -windowed'
```

Le scénario de jeu force le cache Cesium à zéro, capture le centre, déplace la
caméra à +800 m puis −800 m, sort à 5 km, revient au centre et quitte. Les statistiques
`Smoke cache` indiquent le nombre de tuiles chargées et les octets suivis par
cesium-native ; les captures sont dans `Saved/Screenshots/`.

## Exécution graphique et streaming sans réseau

Essai effectué dans un nouvel espace réseau Linux (aucune interface externe),
avec le cache de shaders sur disque du projet :

```bash
export UNREAL_ENGINE_ROOT=/home/louis/src/unreal-engine/engine
nix run .#ue-shell -- unshare --user --map-current-user --net \
  "$UNREAL_ENGINE_ROOT/Engine/Binaries/Linux/UnrealEditor" \
  "$PWD/LiveTerrain.uproject" /Engine/Maps/Entry -game -DDC=Offline \
  -GeoData="$PWD/Prepared/zone" -GeoSmokeTest -windowed -nosound -unattended
```

Budget de cache natif forcé à **0 octet** pour cet essai ; les tuiles requises
pour les vues restent chargées. Les octets ci-dessous sont ceux suivis par
cesium-native, pas toute la mémoire du processus ou de la carte graphique.

| Position caméra | Tuiles chargées, racine comprise | Octets de données |
| --- | ---: | ---: |
| Centre | 21 | 8 419 719 |
| +800 m est | 25 | 10 224 958 |
| −800 m est | 39 | 16 478 848 |
| +5 000 m est, hors de la zone | 1 | 0 |
| Retour au centre | 21 | 8 419 719 |

Le retour au centre provoque de nouvelles lectures locales et restitue le
terrain texturé. Le compteur de chargement atteint 100 % aux points de capture.
La vue corrigée du terrain, des orthophotos et des volumes est conservée dans
[prototype.png](prototype.png). Aucun téléchargement géographique ni appel
Cesium ion n'est nécessaire pour cette exécution.

Le budget Cesium est souple : certaines tuiles demeurent requises par l'état
de sélection même si elles ne sont pas dessinées. Le nombre chargé n'est donc
pas identique au nombre visible ; la sortie complète confirme leur libération.

L'installation actuelle active aussi les toolsets expérimentaux d'Unreal. En mode
`-game`, leur initialisation Python signale des classes éditeur absentes. Un contrôle
de structure de métadonnées Cesium signale aussi `FCesiumMetadataPropertyStatisticValue::Semantic`
non initialisé. Ces messages ne bloquent pas le parcours terrain/orthophotos/boîtes
vérifié ici ; ce prototype n'utilise pas les extensions de métadonnées concernées.

## Mise à jour de la résolution des orthophotos

Les tuiles produites lors de cette étape exploitaient le pas de l'acquisition
locale : **0,5 m/pixel**, contre 1 m/pixel dans l'essai initial ci-dessus.
Les textures des tuiles pleines passent de 256 × 256 à **512 × 512 pixels**.
La préparation choisit désormais par défaut `job.ortho_step` du manifeste,
et conserve ses paramètres dans `zone.json.preparation`.

- Aucun nouveau téléchargement ; reprojection depuis les GeoTIFF originaux.
- Les 64 tuiles passent le validateur ; les huit tests Python réussissent.
- Comparaison binaire : positions, normales, UV et indices des 64 tuiles
  identiques à la version précédente. Seules les images changent.
- Taille du dossier préparé : environ **36 Mio**, contre **18 Mio** auparavant.
- L'ancienne préparation reste dans `Prepared/zone-1m` pour comparaison.

Les mesures de cache et la capture de la section précédente concernent l'essai
initial à 1 m/pixel. Les nouvelles textures occupent davantage de mémoire ;
les limites de chargement et de cache restent configurables comme auparavant.

## Nouvelle acquisition à 20 cm/pixel

La version courante dans `Prepared/zone` utilise désormais les fichiers de
`geodata-20cm/`, acquis avec le téléchargeur existant et `--ortho-step 0.2`.
La section à 50 cm ci-dessus décrit la version précédente, conservée dans
`Prepared/zone-50cm`.

- 100 nouvelles orthophotos GeoTIFF téléchargées depuis le WMS IGN couleur.
- 49 fichiers MNT et 2 pages de bâtiments copiés et contrôlés par SHA-256,
  sans nouveaux appels de téléchargement pour ces sources.
- Statut `complete` pour les trois sources ; stockage comptabilisé : 0,241 Go.
- 64 GLB préparés, images pleines de 1280 × 1280 pixels contre 512 × 512.
- Comparaison binaire : géométrie, normales, indices et UV identiques dans
  les 64 tuiles à 50 cm et à 20 cm.
- Validation des 64 nouvelles tuiles réussie ; onze tests Python réussis,
  dont les nouveaux contrôles de réutilisation, de corruption et de zone.
- Préparation optimisée : seules les orthophotos dont l'emprise intersecte
  la tuile sont reprojetées ; les bounds source sont densifiés et calculés
  une fois dans la projection métrique du terrain.
- Taille préparée : environ 141 Mio, soit 148 Mo décimaux.

Un [échantillon réellement téléchargé à 20 cm](ign-20cm-preview.png) montre
le détail des routes et des véhicules. Attribution : IGN, Licence Ouverte 2.0.
La couche THR à 5–10 cm a été testée sur le même secteur : elle a renvoyé une
image blanche, donc elle n'a pas été utilisée pour la nouvelle acquisition.

## Nouvelle zone à 48.59110362034471 N, 2.337593065968919 E

Acquisition indépendante, rayon 2 000 m (rectangle d’environ 4 × 4 km),
MNT à 5 m et orthophotos demandées à 20 cm/pixel :

- 169 GeoTIFF MNT, 640 000 pixels, aucun nodata, sans repli RGE ALTI nécessaire.
- 400 orthophotos et 2 686 objets BD TOPO téléchargés ; trois sources `complete`.
- Volume comptabilisé par le téléchargeur : 0,799 Go.
- Préparation : 256 tuiles, 2 636 volumes retenus dans l’emprise métrique,
  1 350 320 triangles. Le filtrage des centres explique la différence avec
  le comptage WFS de l’enveloppe longitude/latitude.
- Validation complète des 256 tuiles réussie : buffers, indices, normales,
  géoréférencement, images et sommets partagés.
- Douze tests Python réussis après ajout du contrôle des lots altimétriques.

Les lots de 4 096 points ont été vérifiés sur une réponse IGN réelle avant
reprise de l’acquisition, sans modifier le pas ni perdre les dalles finalisées.
Le dossier source est `geodata-zone48_5911-2_3376`, la préparation est dans
`Prepared/zone-48_5911-2_3376`. La caméra utilise le centre de ce manifeste et
apparaît à +150 m du terrain central, mesuré à environ 79,93 m IGN.
Le décalage vertical reste 0 explicite : altitude ellipsoïdale approximative.
Les dossiers de la première zone restent disponibles.

## Toits, ciel, commandes et FPS

- 2 636 toits de la zone de 2 km projetés avec l’orthophoto du terrain,
  murs neutres, emprises d’image élargies pour les bâtiments traversant les bords.
- Validateur : orientation des toits, matériaux, UV dans l’image, et conversion
  inverse ECEF → coordonnées géographiques → AEQD contrôlées sur les 256 tuiles.
- Environnement natif compilé et rendu : atmosphère, soleil à 50 000 lux,
  capture SkyLight, nuages standard Unreal, brume volumétrique et exposition.
- `TerrainFlyPawn` utilise les axes AZERTY configurés : Z/S, Q/D, E/A et souris.
- Distance de chargement configurée à 10 km, cache de 1 Gio ; l’emprise acquise
  reste distincte de la distance de visibilité.
- `TerrainHUD` affiche les FPS et les millisecondes moyennes en haut à droite,
  sans dépendance à `stat fps` ou aux macros de statistiques de développement.
- Compilation UBT réussie ; capture de vérification hors écran en 1280 × 720 :
  [scene-sky-roofs-fps.png](scene-sky-roofs-fps.png). Les FPS de cette capture
  décrivent cet essai, pas une garantie pour d’autres résolutions ou machines.

Seize tests Python passent après les contrôles des UV de toits, des requêtes
parallèles bornées et de la déduction des fichiers vérifiés lors d’une reprise.

## Extension locale à 5 km de rayon

Acquisition terminée autour de 48.59110362034471 N, 2.337593065968919 E :
2 401 orthophotos demandées à 20 cm, 256 fichiers MNT au pas de 10 m,
37 pages WFS contenant 36 885 objets bâtiments. Les fichiers du manifeste
occupent 5 190 940 214 octets. Aucun pixel MNT sans altitude ; deux échantillons
proviennent du repli RGE ALTI, les autres du MNT LiDAR HD.

La préparation vérifie les SHA-256 de toutes les entrées. Le cache des GeoTIFF
est borné à 128 fichiers ouverts et les bâtiments sont indexés par tuile.
Comparaison intégrale sur l’ancienne zone de 2 km après cette optimisation :
les 256 tuiles conservent exactement leurs positions, normales, indices, UV,
matériaux et pixels RGB décodés. Le changement de compression PNG est sans
perte. Le validateur géographique et les seize tests Python passent.

Préparation complète : 1 600 tuiles, 36 697 boîtes et toits, 2 504 876 triangles.
Le validateur contrôle tous les buffers, limites, images, normales, sommets
partagés, positions ECEF et UV géographiques. Écart maximal image/géométrie
mesuré : 0,000252 m. La tolérance est exprimée en mètres (2 mm), car les
positions GLB float32 à 5 km sont quantifiées par pas d’environ 0,5 mm ;
une tolérance UV fixe amplifiait cet arrondi sur les tuiles étroites du bord.
Les fichiers acquis et les tuiles restent inchangés par ce contrôle.

Le Makefile et la configuration de jeu sélectionnent maintenant cette zone.
Le point d’apparition reste le centre GPS demandé, 150 m au-dessus du terrain
(altitude centrale MNT : 79,925 m avec décalage vertical explicite de 0 m).

Scénario de jeu complet exécuté dans un espace réseau Linux isolé, sans
connexion externe, cache natif forcé à zéro pour vérifier la libération :

| Position | Tuiles comptabilisées | Octets natifs |
| --- | ---: | ---: |
| Centre | 448 | 2 834 971 807 |
| +800 m | 975 | 6 559 372 438 |
| −800 m | 1 078 | 7 261 027 460 |
| +30 km, hors zone | 2 | 0 |
| Retour au centre | 448 | 2 834 971 807 |

Chargement à 100 % à chaque capture ; scénario terminé normalement. Les
compteurs de tuiles incluent les structures de sélection, tandis que les
octets correspondent aux données suivies par cesium-native. Le budget de
cache est souple : les tuiles requises peuvent dépasser 1 Gio en jeu normal.
Capture du retour au centre : [zone-5km-fps.png](zone-5km-fps.png), avec
terrain, orthophotos, bâtiments, ciel, nuages et compteur FPS.
