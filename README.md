# LiveTerrain — prototype géographique UE 5.8 hors ligne

Pour comprendre le code et transférer le prototype dans un projet existant :
[Architecture et guide d’intégration](docs/ARCHITECTURE_ET_INTEGRATION.md).

Le prototype consomme **les fichiers du script existant** `tools/geodata/download.py`.
Il ne télécharge rien depuis Unreal. Le pipeline est :

```text
manifest.json + relief/*.tif + orthophotos/*.tif + batiments/*.geojson
    → préparation Python/GDAL hors exécution
    → Prepared/zone/{tileset.json,zone.json,tile_*.glb}
    → CesiumGeoreference + Cesium3DTileset dans Unreal
```

## Installation et compilation (Linux x86_64 / NixOS)

Installation locale détectée : `/home/louis/src/unreal-engine/engine`, UE **5.8.4**,
SDK **v26 / Clang 20.1.8**. Le moteur est fourni séparément sous licence Epic.
Le Makefile ne modifie pas le moteur. Adapter `UE_ROOT` et, si nécessaire, `SDK`.
Les cibles actuelles concernent Linux x86_64 ; Windows/macOS ne sont pas implémentés.

Cesium est fixé à **v2.31.0**, commit
`7792b53600262735361532a67f8fb8f3b3bb0f16`, avec ses sous-modules officiels
(dont cesium-native 0.66). Cette version annonce UE 5.6–5.8 :
[release officielle](https://github.com/CesiumGS/cesium-unreal/releases/tag/v2.31.0).
La compilation suit le [guide Linux officiel](https://cesium.com/learn/cesium-unreal/ref-doc/developer-setup-linux.html) :
CMake sur **`extern/`**, toolchain Unreal officielle, `--target install` dans le
`Source/ThirdParty` du plugin, puis UBT. L'installation est dans
`Plugins/CesiumForUnreal`, à la place d'une installation globale Marketplace.
UBT compile les modules du plugin et ceux de l'application dans le même graphe.
`setup` adapte uniquement `EngineVersion` du descripteur local Cesium, de
`5.6.0` (valeur des sources officielles) à `5.8.0`. Cela permet le chargement
précoce `PostConfigInit` du plugin sans dialogue d'incompatibilité. Le code C++
du plugin reste inchangé ; cette adaptation est idempotente et versionnée dans
`tools/configure_cesium.py`.

```bash
nix develop
make setup                         # clone officiel récursif, vérification de version
make cesium JOBS=4                 # dépendances natives + modules UBT + application
make build                         # même compilation incrémentale
make prepare HEIGHT_OFFSET=0       # approximation verticale explicite, données déjà locales
make test
make validate                      # vérifie les tuiles réellement préparées
make run                           # scène de jeu avec caméra libre
# ou ouvrir l'éditeur puis cliquer Play :
make editor
```

`HEIGHT_OFFSET=0` est un choix de prototype, **pas une conversion IGN69 → WGS84
certifiée**. Pour un positionnement vertical absolu, fournir l'écart local connu
`h_ellipsoïde − H_IGN` en mètres. Voir les limitations ci-dessous.

Le shell Nix fournit les outils géographiques et de compilation ; le Makefile
utilise automatiquement `nix run .#ue-shell --` sous NixOS pour les binaires
ELF d'Epic. Ce FHS fournit Bash, bibliothèques système, Vulkan et outils CMake.
La toolchain reste celle d'Unreal, sans mélange avec la STL du compilateur système.
La première installation exige Internet (Git, cache Nix, dépendances vcpkg de Cesium).
Les dépendances vcpkg sont mises en cache par le mécanisme officiel ezvcpkg dans
`~/.ezvcpkg`. Garder ce cache et le magasin Nix pour reconstruire hors ligne.
Une fois compilé et préparé, le jeu lit directement les fichiers `file:///` :
aucun serveur HTTP local, token Cesium ion ou service cartographique n'est nécessaire.
Les cibles `run` et `editor` utilisent aussi le graphe natif `-DDC=Offline`,
avec un cache de shaders sur disque dans le projet, sans service Zen.
Le premier démarrage peut compiler les shaders pendant plusieurs minutes ;
les suivants réutilisent `DerivedDataCache/`.

Hors NixOS, installer les outils du guide Cesium et les dépendances Python de
`requirements.txt`, puis utiliser `make UE_ROOT=/chemin/UE_5.8 UE_WRAPPER= build`.
CMake, Ninja, Git, NASM et les outils de construction des dépendances vcpkg doivent
être sur le PATH. Fermer l'éditeur avant une reconstruction des modules.

```bash
make doctor
make native                       # uniquement CMake / cesium-native
make clean                        # Binaries et Intermediate de l'application
make clean-native                 # cache CMake et bibliothèques natives Cesium
make rebuild                      # clean + clean-native + build, séquentiellement
make build UE_ROOT=/autre/UE_5.8 JOBS=8
```

Les dépendances et sorties préparées sont ignorées par Git. Ni `clean` ni
`rebuild` n'effacent les acquisitions ou les données préparées. CMake/Ninja et
UBT déterminent les fichiers à reconstruire ; relancer `make build` ne force
pas une recompilation intégrale. Le Makefile est maintenu à la main : la génération
de fichiers projet Unreal peut le remplacer, il faut alors le restaurer depuis Git.

## Préparation des données réellement téléchargées

Le dossier `geodata/` existant couvre une zone de rayon 1 000 m près de
48.79040813 N, 1.97506794 E. Il est lu sans modification. La préparation refuse
les sources incomplètes et vérifie les SHA-256 du manifeste d'acquisition.
Elle ne fait aucun appel réseau et ne change pas le téléchargeur.

```bash
make prepare DATA=geodata OUTPUT=Prepared/zone HEIGHT_OFFSET=0
# Autre dossier et paramètres :
make prepare DATA=/donnees/zone OUTPUT=Prepared/autre HEIGHT_OFFSET=0 \
  PREPARE_ARGS='--tile-size 256 --texture-step 0.5 --default-height 8'
make run OUTPUT=Prepared/autre
```

### Extension à 5 km de rayon

Pour le périmètre étendu demandé autour du même point, avec les photos à 20 cm
et un MNT réel au pas de 10 m afin de rester dans le budget :

```bash
python tools/geodata/download.py \
  --lat 48.59110362034471 --lon 2.337593065968919 --radius 5000 \
  --dem-step 10 --alti-batch-size 4096 --ortho-step 0.2 --ortho-workers 4 \
  --output geodata-zone48_5911-2_3376-5km --download
make prepare DATA=geodata-zone48_5911-2_3376-5km \
  OUTPUT=Prepared/zone-48_5911-2_3376-5km HEIGHT_OFFSET=0
make run OUTPUT=Prepared/zone-48_5911-2_3376-5km
```

L’acquisition terminée contient 2 401 orthophotos, 256 fichiers MNT et
36 885 objets bâtiments, pour 5,19 Go de fichiers vérifiés. Le MNT ne comporte
aucun pixel sans altitude ; deux échantillons utilisent le repli RGE ALTI,
les autres proviennent du MNT LiDAR HD. Le plan initial estimait 15,9 Go,
pour 18 Go utilisables.
Les sources plus fines de la zone centrale de 2 km restent conservées. Le rayon
10 km à 20 cm avec MNT à 5 m était estimé à 77,3 Go et n’a pas été téléchargé.
La préparation utilise un cache de 128 GeoTIFF ouverts maximum et classe les
bâtiments par tuile avant génération pour supporter les milliers de fichiers.
Le PNG utilise une compression plus rapide, toujours sans perte de pixels.

La version préparée contient **1 600 tuiles, 36 697 bâtiments et leurs toits
texturés**, soit environ 3,1 Gio sur disque. Les positions, normales, UV,
images et raccords ont été validés sur toutes les tuiles. L’écart maximal
mesuré entre projection géographique et UV est de 0,252 mm.

### Nouvelle zone et point d’apparition

Zone demandée : **48.59110362034471 N, 2.337593065968919 E**, rayon **2 000 m**
(couverture rectangulaire d’environ 4 × 4 km). Acquisition indépendante :

```bash
python tools/geodata/download.py \
  --lat 48.59110362034471 --lon 2.337593065968919 --radius 2000 \
  --dem-step 5 --alti-batch-size 4096 --ortho-step 0.2 \
  --output geodata-zone48_5911-2_3376 --download
make prepare DATA=geodata-zone48_5911-2_3376 \
  OUTPUT=Prepared/zone-48_5911-2_3376 HEIGHT_OFFSET=0
make run OUTPUT=Prepared/zone-48_5911-2_3376
```

`--alti-batch-size` règle le nombre de points par requête (1 à 4096,
1000 par défaut). Le lot de 4096 a été vérifié sur une réponse IGN réelle ;
il réduit les allers-retours sans changer le pas du MNT ni le format produit.
Il n’entre pas dans l’identité géographique de l’acquisition : une reprise
avec des lots différents conserve les dalles finalisées après contrôle SHA-256.

Les valeurs par défaut du Makefile et de `Config/DefaultGame.ini` sélectionnent
désormais `Prepared/zone-48_5911-2_3376-5km` (rayon 5 km, toits texturés).
La zone centrale précédente reste accessible avec
`make run OUTPUT=Prepared/zone-48_5911-2_3376-roofs`, et la première zone avec
`make run OUTPUT=Prepared/zone`.

Le point d’apparition suit automatiquement le centre de `zone.json`, à **150 m
au-dessus de l’altitude centrale du terrain**. Il n’est pas fixé aux coordonnées
de la première zone. `CesiumGeoreference` et la caméra utilisent le même centre.
Le décalage vertical `HEIGHT_OFFSET=0` conserve la limite d’altitude absolue
approximative décrite plus bas.

### Acquisition plus fine à 20 cm/pixel

La version courante dans `Prepared/zone` utilise maintenant une **nouvelle
acquisition IGN à 20 cm/pixel**, conservée dans `geodata-20cm/`. Le dossier
`geodata/` initial (50 cm) reste intact. Pour reproduire cette acquisition :

```bash
python tools/geodata/download.py \
  --lat 48.79040813456057 --lon 1.9750679431401221 --radius 1000 \
  --dem-step 5 --ortho-step 0.2 --reuse-from geodata \
  --output geodata-20cm --download
make prepare DATA=geodata-20cm OUTPUT=Prepared/zone-20cm HEIGHT_OFFSET=0
make run OUTPUT=Prepared/zone-20cm
```

`--reuse-from` copie les MNT et bâtiments déjà acquis après contrôle de leur
zone, de leur projection et de leurs SHA-256. Il utilise le même quota et les
mêmes écritures atomiques que le téléchargeur. Les anciennes orthophotos ne
sont jamais copiées : les nouvelles requêtes IGN couvrent toute la zone au
pas demandé. Les métadonnées `reused_sources` conservent la provenance des
sources réutilisées. L'option est facultative ; la commande historique reste
compatible. Répéter la même commande reprend les images manquantes.

Les tuiles pleines de 256 m embarquent alors **1280 × 1280 pixels**, soit
6,25 fois le nombre de pixels de la version à 50 cm. Le dossier préparé est
d'environ 141 Mio (148 Mo décimaux) ; les acquisitions occupent environ 241 Mo.
Les anciennes préparations restent dans `Prepared/zone-50cm` et `zone-1m`.
Le relief et les bâtiments ne gagnent pas de résolution avec ces images.

Le [descriptif IGN BD ORTHO](https://geoservices.ign.fr/sites/default/files/2025-04/DC_BDORTHO_2-0.pdf)
décrit les acquisitions à environ 20 cm. Le service WMS couleur
`ORTHOIMAGERY.ORTHOPHOTOS` a été testé et téléchargé sur cette zone.
La couche `THR.ORTHOIMAGERY.ORTHOPHOTOS`, annoncée à 5–10 cm dans les capacités,
a renvoyé un échantillon blanc sur le secteur testé ; elle n'a pas été retenue.
Le millésime n'est pas inféré. Attribution : IGN, Licence Ouverte 2.0.

La destination doit être vide ou absente ; choisir un nouveau dossier pour
une autre préparation. Après une interruption, supprimer uniquement la destination
préparée incomplète avant de relancer. La source reste intacte.
`zone.json` est écrit en dernier et sert de marqueur de préparation terminée.

Le MNT AEQD est mosaïqué, puis échantillonné bilinéairement aux sommets des tuiles
métriques (pas du manifeste, 5 m sur les données présentes). Les pixels nodata
créent des trous ; aucune altitude fictive ne remplace ces trous dans les triangles.
Les orthophotos EPSG:4326 sont reprojetées dans cette même grille métrique,
rééchantillonnées au pas demandé dans le manifeste source par défaut (0,5 m
pour l’acquisition initiale, 0,2 m pour la nouvelle acquisition) et embarquées en PNG dans chaque GLB. `--texture-step`
permet de choisir explicitement un pas plus grossier pour réduire la mémoire.
Une tuile pleine de 256 m utilise ainsi 1280 × 1280 pixels avec la nouvelle
acquisition, 512 × 512 à 50 cm ou 256 × 256 à 1 m. Le pas choisi est conservé dans `zone.json`.
Un pas inférieur à celui des sources ne garantit aucun détail réel supplémentaire.
Les UV tiennent compte de l'origine supérieure des images. L'altitude et la
courbure terrestre sont conservées par une conversion WGS84 géodésique → ECEF,
puis un repère ENU local à double précision. Les coordonnées des GLB sont en
mètres, Y vers le haut selon glTF ; la matrice de la racine 3D Tiles rétablit ECEF.
Cesium assure ensuite la conversion vers les centimètres Unreal.

Chaque emprise Polygon/MultiPolygon BD TOPO devient une boîte orientée minimale.
Ce choix simplifie les contours concaves et les cours intérieures. La propriété
`hauteur` est employée si elle est positive et finie ; sinon `--default-height`.
Les boîtes ont une base plane au plus bas des quatre coins échantillonnés sur le
MNT et un toit au plus haut plus la hauteur ; les murs pénètrent donc légèrement
dans le sol sur les pentes.
Les Z des vecteurs ne sont pas mélangés aux altitudes du MNT.
Une boîte appartient à la tuile contenant son centre, sans duplication aux frontières.
Les bounding boxes incluent l'intégralité des bâtiments qui débordent d'une tuile.

## Toits et environnement de jour

La préparation projette maintenant les orthophotos **sur les toits des volumes**,
avec les mêmes coordonnées métriques que le terrain. Les murs et les dessous
conservent un matériau neutre. Le toit réutilise la texture du terrain de sa tuile.
L’image englobe les bâtiments qui traversent les limites d’une tuile, avec une
marge d’un texel et une grille de pixels commune : les UV ne sont pas rabattus
sur le bord de l’image. Les bâtiments restent des boîtes ; cette projection ne
reconstruit pas les pans de toiture ni les cours intérieures.

La texture peut donc dépasser les 1280 × 1280 pixels d’une cellule stricte de
256 m à 20 cm. Les grandes emprises sont rééchantillonnées si nécessaire pour
rester sous 4096 pixels par côté ; les emprises réellement couvertes sont
conservées dans les GLB (`extras.orthophoto_bounds`). Hors couverture des images
acquises, le fond reste neutre. `zone.json.preparation.roof_orthophoto` identifie
les préparations avec cette projection.

L’environnement est entièrement natif et local : `SkyAtmosphere`, soleil
`DirectionalLight`, `SkyLight` avec capture du ciel, `VolumetricCloud` avec le
matériau standard du moteur, brouillard exponentiel et volumétrique léger,
et exposition de jour. Il est créé par `LocalTerrainEnvironment.cpp`, sans
service météo ou asset téléchargé. Les réglages sont dans `Config/DefaultGame.ini` :

```ini
[LiveTerrain.Environment]
SunIntensityLux=50000
SunPitch=-45
SunYaw=-30
FogDensity=0.0015
VolumetricClouds=true
```

`VolumetricClouds=false` désactive les nuages et leurs ombres pour alléger le
rendu. Le premier chargement du matériau de nuages peut compiler des shaders.
Pour une future distribution cuite, inclure aussi le matériau standard de
nuages `/Engine/EngineSky/VolumetricClouds/m_SimpleVolumetricCloud_Inst`.

## Scène, configuration et streaming

La carte native vide `/Engine/Maps/Entry` lance le GameMode C++
`LocalTerrainGameMode`, qui crée le géoréférencement, le tileset local et l’environnement
de jour natif. Aucun asset Blueprint ou niveau généré n'est nécessaire.
Le joueur apparaît à 150 m au-dessus du terrain central, avec un `TerrainFlyPawn` volant (dérivé du DefaultPawn natif).
Commandes AZERTY : souris pour regarder, **Z/S** pour avancer/reculer,
**Q/D** pour aller à gauche/droite, **E/A** pour monter/descendre ; vitesse maximale 150 m/s. Dans PIE, cliquer dans la vue
pour capturer la souris, Shift+F1 pour la libérer.

Configuration dans `Config/DefaultGame.ini` :

```ini
[LiveTerrain.LocalData]
Directory=Prepared/zone
LoadDistanceMeters=10000
CacheMiB=1024
ConcurrentLoads=4
LogTileStats=true
```

`Directory` est relatif au projet ou absolu ; `-GeoData=/chemin/prepare` le remplace.
Pour comparer Play dans l’éditeur et le lancement standalone avec le même jeu
de données, utiliser `make editor OUTPUT=Prepared/ma-zone` et
`make run OUTPUT=Prepared/ma-zone`. Une ouverture directe du `.uproject` utilise
`Directory` dans `Config/DefaultGame.ini`. Le chemin du projet est résolu en
chemin absolu avant de construire l’URL `file:///`, y compris dans l’éditeur.
L'origine de `CesiumGeoreference` vient des coordonnées du manifeste et de l'altitude
centrale préparée. Le joueur, les textures, le relief et les bâtiments partagent
le même repère. Aucun Cesium World Terrain ou overlay réseau n'est créé.

**Un seul système de streaming : Cesium 3D Tiles.** World Partition convient
à des acteurs/assets cuits répartis dans une grande carte, mais ajouterait ici
un second découpage inutile. Les GeoTIFF/GeoJSON ne sont pas directement des
3D Tiles : la conversion est réelle et réalisée avant l'exécution.
Cesium lit et décode les fichiers en asynchrone, puis crée les ressources Unreal.
Un `UCesiumTileExcluder` spécialisé écarte les volumes hors de la distance caméra
configurée ; une tuile partiellement dans la sphère reste admissible. Le frustum
Cesium limite aussi les tuiles sélectionnées. Les préchargements de voisins et
parents sont désactivés. Les tuiles non sélectionnées restent dans le cache jusqu'à
son éviction : diminuer `CacheMiB` (0 pour un test d'éviction immédiate) permet
d'observer les déchargements. Ce budget Cesium n'est pas un plafond strict de
mémoire totale GPU/CPU, en particulier pour les tuiles visibles et les chargements en cours.

Les logs `LogLocalTerrain` indiquent le dossier et l'origine ; `LogCesium`
signale les erreurs de contenu et les statistiques sélection/chargement/cache.
`LogTileStats=false` réduit leur volume. Observer les chargements en se déplaçant
et en changeant de direction, puis comparer le cache à 0 et 1024 MiB.

## Limites du premier prototype

- Correction verticale **constante explicite**, approximative sur la zone : pas
  de grille RAF/IGN69 embarquée. À zéro, les couches restent alignées entre elles,
  mais la hauteur absolue ellipsoïdale est fausse. Pour une conversion rigoureuse,
  préparer une grille de datum vertical validée et adapter la conversion hors ligne.
- Un seul niveau de détail pour cette première zone de 2 km. La distance et le
  frustum limitent les chargements ; changer l'erreur écran ne réduit pas la
  géométrie des feuilles. Les très grandes zones demanderaient une pyramide LOD.
- Préparation en mémoire de la mosaïque MNT et de la liste de bâtiments ; adaptée
  aux rayons de 1–2 km, pas encore aux acquisitions régionales. Textures limitées
  à 4096 pixels par tuile ; 1280 × 1280 pixels sur la zone actuelle à 20 cm. Routes non affichées.
- Boîtes simples, pas de toitures/façades ni d'extrusion exacte ; précision limitée
  par le MNT, le WMS et les dates de prise de vue. Aucun paysage sous la zone acquise.
- Caméra volante, sans garantie de collisions/sol pour un personnage piéton.
- Prototype lancé via UnrealEditor (`-game`), pas encore de distribution autonome
  cuite. Une distribution devra cuire les matériaux Cesium et livrer `Prepared/`
  comme fichiers libres en conservant les attributions et licences des sources.

Le détail des vérifications réalisées est dans [docs/VALIDATION.md](docs/VALIDATION.md).

La documentation d'acquisition d'origine est conservée ci-dessous.

---

# Acquisition géographique pour LiveTerrain

L'outil indépendant d'acquisition IGN/OSM prépare les fichiers sources pour
un usage hors ligne. La conversion en meshes/3D Tiles et l'intégration Cesium
sont réalisées par les étapes de préparation et d'exécution décrites plus haut.

## NixOS

Activer les fonctionnalités Nix `nix-command` et `flakes`, puis :

```bash
nix develop
python tools/geodata/download.py --lat 48.8584 --lon 2.2945
python tools/geodata/download.py --lat 48.8584 --lon 2.2945 --download
```

La première commande fait seulement l'estimation et quelques requêtes de
métadonnées. La seconde réestime puis télécharge réellement. Rayon par défaut :
1 000 m. Aucun compte IGN ni clé API nécessaire pour ces services ouverts.
Le `flake.lock` fixe nixpkgs ; le shell fournit Python, Requests, NumPy,
PyProj, Rasterio, GDAL et PROJ. Aucune installation pip nécessaire sous NixOS.
Si le flake nouvellement créé n'est pas encore suivi par Git, utiliser
`nix develop path:.` ; après ajout des fichiers à Git, `nix develop` suffit.

Autre exemple :

```bash
python tools/geodata/download.py \
  --lat 45.764 --lon 4.8357 --radius 1500 \
  --dem-step 2 --ortho-step 0.5 --roads --output geodata/lyon --download
```

Hors NixOS, Python 3.11+ avec les dépendances de `requirements.txt` convient :

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Données et formats

| Dossier/fichier | Contenu |
| --- | --- |
| `relief/` | Petits GeoTIFF Float32 du MNT, nodata `-99999`, pas de 5 m par défaut |
| `orthophotos/` | GeoTIFF RVB issus du WMS IGN, pas demandé d'environ 50 cm |
| `batiments/` | Pages GeoJSON BD TOPO, géométries, attribut `hauteur`, altitudes et valeurs nulles d'origine |
| `routes/` | GeoJSON des voies OSM avec tags, seulement avec `--roads` |
| `zone.geojson` | Cercle demandé en WGS84 |
| `plan.json` | Estimation, comptage WFS, limites et avertissements |
| `manifest.json` | Paramètres, CRS WKT, sources, métadonnées, état par source et SHA-256 de chaque fichier |

L'acquisition couvre **le rectangle englobant** le cercle. Les MNT couvrent un
carré dans une projection locale équidistante ; la dernière ligne/colonne peut
dépasser de moins d'un pixel. Les photos et vecteurs couvrent son enveloppe
longitude/latitude. Aucun découpage strict au cercle n'est effectué.

Le MNT est **échantillonné par l'API altimétrique** : il ne s'agit pas d'un
téléchargement des dalles LiDAR natives ni de nuages LAZ. Chaque pixel est une
mesure réelle IGN. Priorité à `ign_lidar_hd_mnt_mono_wld`, puis RGE ALTI
`ign_rge_alti_wld` pour les pixels sans couverture. Les GeoTIFF et le manifeste
conservent les références des mesures et le nombre de pixels par source.
Les échecs réseau ne sont pas masqués par un repli. Les pixels restant sans
altitude deviennent nodata et la source est déclarée partielle.
Le pas de sortie, configurable à partir de 1 m, ne signifie pas que chaque
source possède cette précision. Le pas de 5 m réduit fortement le nombre
d'appels ; un pas de 1–2 m est utile pour un relief urbain plus détaillé.

Les MNT utilisent une projection AEQD locale dont le WKT est intégré aux
GeoTIFF et au manifeste. Les orthophotos sont en EPSG:4326 ; les GeoJSON sont
en longitude/latitude WGS84 (CRS84), avec les Z d'origine des bâtiments.
Les altitudes IGN sont des altitudes physiques du référentiel de la source
(notamment IGN69 en métropole), **pas des hauteurs ellipsoïdales WGS84**.
Les réponses du service ne fournissent pas systématiquement le datum vertical :
le script ne le convertit ni ne l'invente, et conserve les métadonnées reçues.
Pour une intégration Cesium ultérieure, il faudra résoudre cette distinction.

Les orthophotos sont des images rendues par WMS, réencodées sans perte en
GeoTIFF ; ce ne sont pas les livraisons natives BD ORTHO. La résolution demandée
n'améliore pas la résolution source. Un WMS peut renvoyer une image blanche
hors couverture sans erreur : l'état `complete` signifie que toutes les
requêtes d'image ont abouti, pas qu'une couverture photographique a été certifiée.
Le millésime des orthophotos n'est pas inféré.

La BD TOPO conserve `hauteur` en mètres quand elle existe ; aucune hauteur
n'est inventée à partir du nombre d'étages. Pagination triée par `cleabs`,
contrôle des doublons et du comptage final. Le service WFS ne propose pas de
snapshot transactionnel : une modification à nombre d'objets constant reste
possible pendant une acquisition. Les fichiers constituent un état local daté.
Les routes OSM utilisent Overpass, service communautaire en lecture seule ;
elles ne proviennent pas d'une API officielle de l'administration française.

## Quota et estimation

Le plafond est **20 Go décimaux**, avec **10 % de marge obligatoire** : au plus
18 Go utilisables par défaut dans le dossier de sortie. `--max-gb` peut abaisser
ce plafond ; `--safety` peut augmenter la marge. Le script conserve également
1 Go libre sur le système de fichiers (`--free-gb`).

Les services WMS/WFS/altimétriques fabriquent leurs réponses à la demande :
aucun Content-Length exact de l'acquisition complète n'existe à l'avance.
Le plan estime donc les volumes avant les données : grille et réponses
altimétriques majorées, pixels RVB majorés, 64 ko par bâtiment compté, réserve
OSM fixe. Ce sont des **estimations prudentes, pas des bornes garanties** pour
des objets exceptionnellement complexes. Si le plan dépasse le budget ou
l'espace libre, l'acquisition est refusée. Sur reprise, les octets des fichiers
sources dont le SHA-256 est vérifié sont déduits du volume restant à réserver,
pour éviter de les compter deux fois. Les fichiers corrompus, étrangers et
métadonnées restent comptabilisés sur disque. Le script n'efface jamais les
fichiers finalisés pour libérer du quota.

Avant **chaque écriture**, le script recompte tout le dossier, temporaires et
allocations disque inclus, vérifie le budget et l'espace libre, puis écrit via
un fichier `.part` et renommage atomique. Le remplacement réserve la coexistence
de l'ancien et du nouveau fichier. Les réponses réseau et les GeoTIFF sont
préparés en mémoire avec des lots bornés ; pas d'archive extraite ni de copie
temporaire volumineuse sur disque. Le manifeste et les plans sont aussi comptés.
Les téléchargements déjà présents sont inclus dans le budget, y compris les
fichiers étrangers au script. Les réponses HTTP ont également un plafond
d'octets contrôlé pendant la lecture.

Le verrou `.lock` interdit deux instances sur la même destination et les liens
symboliques sont refusés. Réserver ce dossier au script pendant l'acquisition :
aucun contrôle applicatif ne peut empêcher un autre programme d'écrire sur le
disque entre deux vérifications. Pour une garantie physique contre les écritures
concurrentes extérieures, utiliser en plus un quota de système de fichiers.
Le quota décrit le stockage local, pas le trafic réseau cumulé des tentatives.

## Interruptions et erreurs

Relancer **la même commande et le même dossier**. Les fichiers finalisés dont
le SHA-256 est correct sont réutilisés ; une unité interrompue est téléchargée
à nouveau. Il n'y a pas de reprise HTTP à l'octet sur ces réponses générées.
Les `.part` du dossier dédié sont supprimés au redémarrage. Les paramètres
géographiques et les résolutions doivent rester identiques ; sinon choisir
un nouveau dossier.

Le client effectue jusqu'à quatre tentatives sur les erreurs transitoires,
avec délais, et reste sous quatre requêtes par seconde au total.
`--ortho-workers 4` permet jusqu’à quatre requêtes d’image en cours ; le débit
global reste limité, les sessions HTTP sont séparées par thread et les écritures
avec contrôle de quota restent séquentielles. Les erreurs 400 temporaires
observées sur WMS sont réessayées au plus quatre fois, sans masquer un échec
persistant. Une erreur 429 impose
30 secondes d'attente. Les sources indépendantes continuent après une panne
de service ; une limite de stockage arrête immédiatement toute l'acquisition.
Les sorties standard et `manifest.json` signalent les indisponibilités.

Codes de sortie : `0` terminé (ou plan valide), `1` erreur bloquante/quota,
`2` acquisition partielle ou métadonnées indisponibles, `130` interruption.

## Services vérifiés et références

Vérification directe le **15 novembre 2026**, sans téléchargement simulé :

- [WMS IGN](https://data.geopf.fr/wms-r?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities) :
  couche `ORTHOIMAGERY.ORTHOPHOTOS` annoncée ; GetMap PNG utilisé.
- [WFS IGN](https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities) :
  `BDTOPO_V3:batiment` disponible, géométries et `hauteur` vérifiées sur réponse réelle.
- [API altimétrique officielle](https://data.geopf.fr/altimetrie/api/index.html),
  [schéma OpenAPI](https://data.geopf.fr/altimetrie/api/openapi.json),
  [catalogue](https://data.geopf.fr/altimetrie/resources) : POST JSON testé,
  altitudes et métadonnées LiDAR HD reçues.
- [Téléchargement natif MNT LiDAR HD](https://cartes.gouv.fr/telechargement/IGNF_MNT-LIDAR-HD) :
  l'ancien type WFS `IGNF_MNT-LIDAR-HD:dalle` est absent des capacités reçues
  et GetFeature renvoie HTTP 400. `/wcs` renvoie 404 ;
  `/telechargement/capabilities` renvoie 403. Ces routes ne sont **pas** utilisées
  comme si elles fonctionnaient. La version initiale privilégie donc l'API
  altimétrique opérationnelle. Elle ne prétend pas que les dalles natives sont
  indisponibles par tout autre moyen.
- [Documentation Overpass](https://wiki.openstreetmap.org/wiki/Overpass_API) :
  requêtes `way[highway]` avec géométrie. Service facultatif, susceptible d'être saturé.

IGN : [Licence Ouverte 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/),
attribuer les produits IGN et leurs dates quand elles sont connues.
OSM : [ODbL et attribution © OpenStreetMap contributors](https://www.openstreetmap.org/copyright).
Conserver ces attributions avec toute redistribution.

## Vérification locale

```bash
nix develop --command python -m unittest discover -s tests -v
# Petit vrai téléchargement pour vérifier la connectivité sans lancer 1 km² :
nix develop --command python tools/geodata/download.py \
  --lat 48.8584 --lon 2.2945 --radius 20 --output /tmp/liveterrain-smoke --download
gdalinfo /tmp/liveterrain-smoke/relief/mnt_000000_000000.tif
```

Les tests unitaires vérifient le quota, les remplacements atomiques,
la détection de corruption et la géométrie du périmètre. Ils sont distincts des
essais réseau réels ; ils ne constituent pas une preuve de disponibilité IGN.

Essais réalisés le 15 novembre 2026 : shell Nix construit, cinq tests unitaires
validés, acquisition réelle sur un rayon de 20 m à Paris (quatre sources),
reprise de cette acquisition avec réutilisation des fichiers, lot altimétrique
de 1 000 points sur une zone de 100 m de rayon, repli RGE ALTI sur une petite
zone en Guyane (quatre pixels hors couverture LiDAR), et cinq pages WFS
totalisant 4 695 bâtiments sans doublon.
Le plan par défaut autour de la tour Eiffel estimait 520 Mo environ, sans
les routes. Ces essais n'impliquent pas une couverture nationale exhaustive.

La distance de chargement courante est de **10 km**, avec un cache de **1 Gio**.
La zone acquise conserve son rayon de 2 km ; cette distance ne crée aucune
donnée géographique au-delà de l’emprise téléchargée.

Un compteur FPS et temps moyen par image est affiché en haut à droite par
`TerrainHUD`. Il est actif dans la vue de jeu, sans commande console ni
dépendance aux statistiques réservées aux builds de développement.
