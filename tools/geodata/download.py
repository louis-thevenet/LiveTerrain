#!/usr/bin/env python3
"""Acquisition IGN/OSM bornée, reprenable par unité atomique (Linux/NixOS)."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
import warnings
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET

import numpy as np
import requests
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from pyproj import CRS, Geod, Transformer

BASE = "https://data.geopf.fr"
WFS = BASE + "/wfs/ows"
ALTI = BASE + "/altimetrie/1.0/calcul/alti/rest/elevation.json"
LIDAR = "ign_lidar_hd_mnt_mono_wld"
RGE = "ign_rge_alti_wld"
MB = 1_000_000


class LimitError(RuntimeError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode()


class Store:
    """Quota sur tout le répertoire, fichiers temporaires et allocations inclus."""
    def __init__(self, root, limit, reserve):
        self.root = root
        self.limit = limit
        self.reserve = reserve

    def used(self):
        total = 0
        for directory, dirs, files in os.walk(self.root):
            for name in dirs + files:
                p = Path(directory) / name
                if p.is_symlink():
                    raise LimitError(f"Lien symbolique interdit dans la destination : {p}")
                st = p.stat()
                total += max(st.st_size, st.st_blocks * 512)
        return total + max(self.root.stat().st_size, self.root.stat().st_blocks * 512)

    def check(self, additional):
        # Arrondir les allocations et garder de la place pour les métadonnées FS.
        need = math.ceil(additional / 4096) * 4096 + 65536
        if self.used() + need > self.limit:
            raise LimitError("Quota de stockage atteint (temporaires compris)")
        if shutil.disk_usage(self.root).free < need + self.reserve:
            raise LimitError("Espace disque libre insuffisant avec la réserve demandée")

    def write(self, relative, data):
        p = self.root / relative
        self.check(len(data) + 16384)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        try:
            with tmp.open("wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)


class Client:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "LiveTerrain-geodata/1.0", "Accept-Encoding": "identity"})
        self.last = 0.0
        self._request_lock = threading.Lock()
        self._thread_sessions = threading.local()
        self._thread_sessions.session = self.session

    def request_session(self):
        session = getattr(self._thread_sessions, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self.session.headers)
            self._thread_sessions.session = session
        return session

    def get(self, url, *, params=None, payload=None, cap=32 * MB, method="GET"):
        for attempt in range(4):
            with self._request_lock:
                time.sleep(max(0, 0.25 - (time.monotonic() - self.last)))
                self.last = time.monotonic()
            try:
                with self.request_session().request(method, url, params=params, json=payload,
                                          stream=True, timeout=(15, 120)) as r:
                    if r.status_code in (429, 500, 502, 503, 504):
                        r.raise_for_status()
                    r.raise_for_status()
                    if int(r.headers.get("Content-Length", 0)) > cap:
                        raise LimitError(f"Réponse supérieure au plafond de {cap} octets : {url}")
                    chunks = bytearray()
                    for chunk in r.iter_content(65536):
                        if len(chunks) + len(chunk) > cap:
                            raise LimitError(f"Réponse trop volumineuse : {url}")
                        chunks.extend(chunk)
                    return bytes(chunks)
            except requests.RequestException as exc:
                status = getattr(exc.response, "status_code", None)
                if attempt == 3 or (status and status < 500 and status != 429
                                    and not (status == 400 and url == BASE + "/wms-r")):
                    raise
                time.sleep(30 if status == 429 else 2 ** attempt)
        raise RuntimeError("Échec réseau")

    def json(self, url, **kwargs):
        b = self.get(url, **kwargs)
        try:
            return json.loads(b)
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError(f"Réponse non JSON de {url}: {b[:200]!r}") from exc


def bounded_map(function, items, workers):
    """Bound in-flight results and preserve order; all commits stay on the caller."""
    if workers == 1:
        for item in items:
            yield item, function(item)
        return
    pending = deque()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for item in items:
            pending.append((item, pool.submit(function, item)))
            if len(pending) >= workers:
                first, future = pending.popleft()
                yield first, future.result()
        while pending:
            first, future = pending.popleft()
            yield first, future.result()


def wfs_params(box, **kwargs):
    return dict(service="WFS", version="2.0.0", request="GetFeature",
                typeNames="BDTOPO_V3:batiment", bbox=','.join(map(str, box)) + ",urn:ogc:def:crs:OGC:1.3:CRS84",
                **kwargs)


def matched(client, box):
    xml = ET.fromstring(client.get(WFS, params=wfs_params(box, resultType="hits")))
    count = xml.attrib.get("numberMatched", "unknown")
    if not count.isdigit():
        raise RuntimeError(f"Comptage WFS impossible : {ET.tostring(xml)[:300]!r}")
    return int(count)


def zone(lat, lon, radius):
    # Projection locale équidistante : utilisable aussi dans les DROM.
    crs = CRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs")
    to_gps = Transformer.from_crs(crs, 4326, always_xy=True)
    # Densifier les quatre côtés pour borner le rectangle projeté en longitude/latitude.
    ring = []
    for t in np.linspace(-radius, radius, 101):
        ring.extend([to_gps.transform(t, -radius), to_gps.transform(t, radius),
                     to_gps.transform(-radius, t), to_gps.transform(radius, t)])
    box = [min(p[0] for p in ring), min(p[1] for p in ring),
           max(p[0] for p in ring), max(p[1] for p in ring)]
    geod = Geod(ellps="WGS84")
    circle = [geod.fwd(lon, lat, az, radius)[:2] for az in range(361)]
    return crs, box, {"type": "FeatureCollection", "features": [{"type": "Feature",
        "properties": {"radius_m": radius}, "geometry": {"type": "Polygon", "coordinates": [circle]}}]}


def raster_bytes(array, transform, crs, nodata=None, tags=None):
    if array.ndim == 2:
        array = array[np.newaxis, :, :]
    with rasterio.Env(GDAL_PAM_ENABLED="NO", GDAL_CACHEMAX=32):
        with MemoryFile() as mem:
            with mem.open(driver="GTiff", height=array.shape[1], width=array.shape[2],
                          count=array.shape[0], dtype=array.dtype, crs=crs,
                          transform=transform, nodata=nodata, compress="deflate") as dst:
                dst.write(array)
                dst.update_tags(**(tags or {}))
            return mem.read()


class Job:
    def __init__(self, store, client, manifest):
        self.store, self.client, self.manifest = store, client, manifest

    def save(self):
        self.store.write("manifest.json", encoded(self.manifest))

    def cached(self, name):
        record = self.manifest["files"].get(name)
        path = self.store.root / name
        if not record or not path.is_file():
            return False
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(MB), b""):
                h.update(chunk)
        return h.hexdigest() == record["sha256"]

    def verified_bytes(self):
        """Discount only intact source bytes already present when reserving a resume."""
        return sum((self.store.root/name).stat().st_size
                   for name in self.manifest["files"] if self.cached(name))

    def put(self, name, data, **metadata):
        self.store.write(name, data)
        self.manifest["files"][name] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), **metadata)
        self.save()

    def terrain(self, args, crs):
        n = math.ceil(2 * args.radius / args.dem_step)
        inverse = Transformer.from_crs(crs, 4326, always_xy=True)
        available = {r["_id"] for r in self.client.json(BASE + "/altimetrie/resources")["resources"]}
        if not ({LIDAR, RGE} & available):
            raise RuntimeError("Aucune ressource MNT attendue dans le catalogue altimétrique")
        missing_total = fallback_total = 0
        for row in range(0, n, 64):
            for col in range(0, n, 64):
                name = f"relief/mnt_{row:06d}_{col:06d}.tif"
                if self.cached(name):
                    rec = self.manifest["files"][name]
                    missing_total += rec.get("nodata_pixels", 0)
                    fallback_total += rec.get("rge_pixels", 0)
                    continue
                h, w = min(64, n-row), min(64, n-col)
                xs, ys = np.meshgrid(-args.radius+(col+np.arange(w)+0.5)*args.dem_step,
                                    args.radius-(row+np.arange(h)+0.5)*args.dem_step)
                lon, lat = inverse.transform(xs.ravel(), ys.ravel())
                z = np.full(h*w, -99999., dtype="float32")
                source_counts = {}
                measures = []
                for resource in [LIDAR, RGE]:
                    if resource not in available:
                        continue
                    indices = np.flatnonzero(z == -99999.)
                    if not len(indices):
                        break
                    # Lots modestes, payload JSON documenté par OpenAPI.
                    for start in range(0, len(indices), args.alti_batch_size):
                        ix = indices[start:start+args.alti_batch_size]
                        response = self.client.json(ALTI, method="POST", payload={
                            "lon": "|".join(f"{v:.9f}" for v in lon[ix]),
                            "lat": "|".join(f"{v:.9f}" for v in lat[ix]),
                            "resource": resource, "measures": "true"})
                        values = response.get("elevations", [])
                        if len(values) != len(ix):
                            raise RuntimeError("Réponse altimétrique incomplète")
                        valid = 0
                        for i, value in zip(ix, values):
                            if abs(value["lon"] - lon[i]) > 1e-6 or abs(value["lat"] - lat[i]) > 1e-6:
                                raise RuntimeError("Ordre des points altimétriques inattendu")
                            height = value.get("z")
                            if height is not None and math.isfinite(height) and height > -9999:
                                z[i] = height
                                valid += 1
                                for measure in value.get("measures", []):
                                    # Conserver les références distinctes, sans multiplier les coordonnées.
                                    measure = {k: v for k, v in measure.items() if k != "z"}
                                    if measure not in measures:
                                        measures.append(measure)
                        source_counts[resource] = source_counts.get(resource, 0) + valid
                nodata = int(np.count_nonzero(z == -99999.))
                transform = from_origin(-args.radius+col*args.dem_step,
                                        args.radius-row*args.dem_step, args.dem_step, args.dem_step)
                data = raster_bytes(z.reshape(h, w), transform, crs, -99999.,
                                    {"sources": json.dumps(source_counts), "vertical_units": "metre",
                                     "vertical_reference": "IGN normal heights; see source metadata",
                                     "source_metadata": json.dumps(measures)})
                self.put(name, data, sources=source_counts, source_metadata=measures,
                         nodata_pixels=nodata, rge_pixels=source_counts.get(RGE, 0))
                missing_total += nodata
                fallback_total += source_counts.get(RGE, 0)
                print(f"  {name} ({nodata} pixels sans altitude)", flush=True)
        return dict(status="partial" if missing_total else "complete", nodata_pixels=missing_total,
                    rge_pixels=fallback_total, note="MNT échantillonné ; dalles natives LiDAR non téléchargées")

    def ortho(self, args, box):
        # WMS 1.3 CRS:84 impose longitude,latitude ; world transform identique au GetMap.
        geod = Geod(ellps="WGS84")
        width = math.ceil(max(geod.inv(box[0], y, box[2], y)[2] for y in [box[1], box[3]]) / args.ortho_step)
        height = math.ceil(geod.inv(box[0], box[1], box[0], box[3])[2] / args.ortho_step)
        dx, dy = (box[2]-box[0])/width, (box[3]-box[1])/height
        def units():
            for row in range(0, height, 1024):
                for col in range(0, width, 1024):
                    name = f"orthophotos/ortho_{row:06d}_{col:06d}.tif"
                    if self.cached(name):
                        continue
                    w, h = min(1024, width-col), min(1024, height-row)
                    bounds = [box[0]+col*dx, box[3]-(row+h)*dy, box[0]+(col+w)*dx, box[3]-row*dy]
                    params = dict(service="WMS", version="1.3.0", request="GetMap",
                                  layers="ORTHOIMAGERY.ORTHOPHOTOS", styles="", crs="CRS:84",
                                  bbox=','.join(map(str, bounds)), width=w, height=h, format="image/png")
                    yield name, params, bounds, w, h
        def fetch(unit):
            return self.client.get(BASE + "/wms-r", params=unit[1], cap=8*MB)
        for (name, params, bounds, w, h), b in bounded_map(fetch, units(), args.ortho_workers):
            if not b.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError(f"GetMap n'a pas renvoyé une image PNG : {b[:300]!r}")
            # Raster conversion, quota checks, manifest writes and fsync remain serial.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
                with MemoryFile(b) as mem, mem.open() as src:
                    array = src.read()
                    if src.width != w or src.height != h or src.count < 3:
                        raise RuntimeError("Dimensions ou canaux WMS inattendus")
            self.put(name, raster_bytes(array, from_origin(bounds[0], bounds[3], dx, dy), "EPSG:4326"),
                     request=params, source=BASE + "/wms-r")
            print(f"  {name}", flush=True)
        return {"status": "complete", "note": "Images WMS, résolution demandée approximative en mètres ; couverture non garantie par le service"}

    def buildings(self, box, count):
        seen = set()
        for offset in range(0, count, 1000):
            name = f"batiments/batiments_{offset:08d}.geojson"
            if self.cached(name):
                collection = json.loads((self.store.root/name).read_bytes())
            else:
                collection = self.client.json(WFS, params=wfs_params(box, outputFormat="application/json",
                    srsName="urn:ogc:def:crs:OGC:1.3:CRS84", count=min(1000, count-offset),
                    startIndex=offset, sortBy="cleabs A"))
            features = collection.get("features", [])
            if len(features) != min(1000, count-offset):
                raise RuntimeError("Pagination WFS incomplète ; relancer avec un nouveau dossier si le jeu a évolué")
            for feature in features:
                fid = feature["id"]
                if fid in seen:
                    raise RuntimeError("Doublon WFS : jeu modifié pendant la pagination")
                seen.add(fid)
            if not self.cached(name):
                self.put(name, encoded(collection), source=WFS, offset=offset)
        if matched(self.client, box) != count:
            raise RuntimeError("Le nombre de bâtiments a changé pendant l'acquisition")
        return {"status": "complete", "count": count,
                "note": "Hauteurs et altitudes d'origine conservées, y compris valeurs nulles"}

    def roads(self, args, box):
        name = "routes/routes.geojson"
        if self.cached(name):
            return {"status": "complete"}
        west, south, east, north = box
        query = f'[out:json][timeout:90];way["highway"]({south},{west},{north},{east});out geom;'
        result = self.client.json(args.overpass, params={"data": query}, cap=64*MB)
        if "remark" in result:
            raise RuntimeError(f"Overpass : {result['remark']}")
        features = []
        for way in result["elements"]:
            coords = [[p["lon"], p["lat"]] for p in way.get("geometry", [])]
            if len(coords) >= 2:
                features.append(dict(type="Feature", id=way["id"], properties=way.get("tags", {}),
                                     geometry=dict(type="LineString", coordinates=coords)))
        self.put(name, encoded(dict(type="FeatureCollection", features=features)),
                 source=args.overpass, query=query, copyright=result.get("osm3s"))
        return {"status": "complete", "count": len(features)}


def reuse_sources(job, source_root):
    """Copy verified, unchanged non-imagery sources using the normal quota store."""
    source_root = source_root.absolute()
    if any(p.is_symlink() for p in [source_root, *source_root.parents]):
        raise ValueError("Réutilisation : dossier source symbolique interdit")
    source_root = source_root.resolve()
    target = job.store.root.resolve()
    if source_root == target or source_root in target.parents or target in source_root.parents:
        raise ValueError("Réutilisation : dossiers source et destination doivent être séparés")
    with (source_root/".lock").open("rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Une acquisition modifie le dossier source")
        raw = (source_root/"manifest.json").read_bytes()
        previous = json.loads(raw)
        for key in ("schema", "lat", "lon", "radius", "dem_step"):
            if previous["job"][key] != job.manifest["job"][key]:
                raise ValueError(f"Réutilisation incompatible : {key}")
        if previous["crs_wkt"] != job.manifest["crs_wkt"]:
            raise ValueError("Réutilisation incompatible : projection")
        sources = ["relief", "batiments"]
        if job.manifest["job"]["roads"] and previous["job"].get("roads"):
            if previous["job"]["overpass"] == job.manifest["job"]["overpass"]:
                sources.append("routes")
        for source in sources:
            if previous["sources"].get(source, {}).get("status") != "complete":
                raise ValueError(f"Source à réutiliser incomplète : {source}")
            records = {n: r for n, r in previous["files"].items() if n.startswith(source + "/")}
            if not records:
                raise ValueError(f"Source à réutiliser vide : {source}")
            for name, rec in records.items():
                path = source_root/name
                if source_root not in path.resolve().parents or any(p.is_symlink() for p in [path, *path.parents]):
                    raise ValueError("Chemin source non sûr")
                data = path.read_bytes()
                if len(data) != rec["bytes"] or hashlib.sha256(data).hexdigest() != rec["sha256"]:
                    raise ValueError(f"Source à réutiliser corrompue : {name}")
                if job.manifest["files"].get(name, {}).get("sha256") != rec["sha256"] or not job.cached(name):
                    job.store.write(name, data)
                    job.manifest["files"][name] = rec
                    job.save()
            job.manifest["sources"][source] = previous["sources"][source]
            job.manifest.setdefault("reused_sources", {})[source] = dict(
                manifest_sha256=hashlib.sha256(raw).hexdigest(), directory=str(source_root), files=list(records))
            job.save()
            print(f"Réutilisation vérifiée : {source} ({len(records)} fichiers)", flush=True)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--radius", type=float, default=1000, help="rayon en mètres (défaut 1000)")
    p.add_argument("--output", type=Path, default=Path("geodata"))
    p.add_argument("--dem-step", type=float, default=5, help="pas du MNT en mètres, minimum 1")
    p.add_argument("--alti-batch-size", type=int, default=1000, help="points par requête altimétrique, de 1 à 4096 ; défaut 1000")
    p.add_argument("--ortho-workers", type=int, choices=range(1,5), default=1, help="requêtes image simultanées (1–4), débit global limité à 4/s")
    p.add_argument("--ortho-step", type=float, default=0.5, help="pas approximatif des images en mètres, minimum 0.2")
    p.add_argument("--max-gb", type=float, default=20, help="Go décimaux, plafond absolu 20")
    p.add_argument("--safety", type=float, default=0.1, help="fraction inutilisable du quota, minimum 0.1")
    p.add_argument("--free-gb", type=float, default=1, help="réserve disque libre supplémentaire")
    p.add_argument("--roads", action="store_true")
    p.add_argument("--overpass", default="https://overpass-api.de/api/interpreter")
    p.add_argument("--reuse-from", type=Path, help="réutiliser MNT/bâtiments vérifiés d’une acquisition de même zone ; seules les nouvelles orthophotos sont téléchargées")
    p.add_argument("--download", action="store_true", help="télécharger après estimation ; sinon plan seul")
    return p


def run(args):
    values = [args.lat, args.lon, args.radius, args.dem_step, args.ortho_step, args.max_gb, args.safety, args.free_gb]
    if not all(math.isfinite(x) for x in values):
        raise ValueError("Paramètres non finis")
    if not (-90 < args.lat < 90 and -180 <= args.lon <= 180 and 0 < args.radius <= 20000
            and args.dem_step >= 1 and args.ortho_step >= 0.2 and 1 <= args.alti_batch_size <= 4096 and 0.001 <= args.max_gb <= 20
            and 0.1 <= args.safety < 0.9 and args.free_gb >= 0):
        raise ValueError("Paramètres hors limites : rayon ≤ 20 km, quota entre 0.001 et 20 Go, marge ≥ 10 %")
    # Refuser les ancêtres symboliques avant de résoudre le chemin.
    root = args.output.absolute()
    if any(p.is_symlink() for p in [root, *root.parents]):
        raise ValueError("Destination ou ancêtre symbolique interdit")
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root, int(args.max_gb*1e9*(1-args.safety)), int(args.free_gb*1e9))
    store.check(16384)
    with (root/".lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Une acquisition utilise déjà cette destination")
        store.used()  # Vérifier les liens avant tout nettoyage.
        for part in root.rglob("*.part"):
            part.unlink()
        crs, box, aoi = zone(args.lat, args.lon, args.radius)
        identity = {k: getattr(args, k) for k in ["lat", "lon", "radius", "dem_step", "ortho_step", "roads", "overpass"]}
        identity["schema"] = 1
        manifest_path = root/"manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_bytes())
            if manifest["job"] != identity:
                raise ValueError("Ce dossier correspond à une autre zone ou configuration ; choisir --output")
        else:
            manifest = dict(job=identity, created=dt.datetime.now(dt.timezone.utc).isoformat(),
                            files={}, sources={}, crs_wkt=crs.to_wkt(), bbox_crs84=box)
        client = Client()
        job = Job(store, client, manifest)
        if args.reuse_from is not None and args.download:
            reuse_sources(job, args.reuse_from)
        errors = {}
        count = None
        try:
            count = (manifest["sources"]["batiments"]["count"]
                     if "batiments" in manifest.get("reused_sources", {})
                     else matched(client, box))
        except (requests.RequestException, ValueError, RuntimeError, ET.ParseError) as exc:
            errors["batiments"] = str(exc)
        # Estimations prudentes : PNG majoré, JSON vectoriel réservé à 64 ko/objet.
        # Ce ne sont pas des Content-Length : les services produisent les réponses à la demande.
        n = math.ceil(2*args.radius/args.dem_step)
        geod = Geod(ellps="WGS84")
        ow = math.ceil(max(geod.inv(box[0], y, box[2], y)[2] for y in [box[1], box[3]]) / args.ortho_step)
        oh = math.ceil(geod.inv(box[0], box[1], box[0], box[3])[2] / args.ortho_step)
        estimate = dict(relief=n*n*800 + math.ceil(n/64)**2*65536,
                        orthophotos=ow*oh*5 + math.ceil(ow/1024)*math.ceil(oh/1024)*65536,
                        batiments=(count*65536 if count is not None else 128*MB),
                        routes=128*MB if args.roads else 0)
        plan = dict(estimated_bytes=estimate, total_estimated_bytes=sum(estimate.values()),
                    usable_limit_bytes=store.limit, existing_bytes=store.used(), building_count=count,
                    warnings=errors, bbox_crs84=box, dem_pixels=n*n,
                    note="Volumes majorés estimés, non garantis ; contrôle du stockage avant chaque écriture")
        print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
        store.write("plan.json", encoded(plan))
        job = Job(store, client, manifest)
        job.save()
        store.write("zone.geojson", encoded(aoi))
        if not args.download:
            print("Plan seul. Ajouter --download pour lancer l'acquisition.")
            return 2 if errors else 0
        # The full estimate already includes finalized source files; do not count them twice.
        store.check(max(0, sum(estimate.values()) - job.verified_bytes()) + MB)
        operations = [("relief", lambda: job.terrain(args, crs)),
                      ("orthophotos", lambda: job.ortho(args, box)),
                      ("batiments", lambda: job.buildings(box, count))]
        if args.roads:
            operations.append(("routes", lambda: job.roads(args, box)))
        for source, operation in operations:
            if source in manifest.get("reused_sources", {}):
                names = manifest["reused_sources"][source]["files"]
                if manifest["sources"].get(source, {}).get("status") != "complete" or not all(job.cached(n) for n in names):
                    raise ValueError(f"Source réutilisée corrompue : {source}; relancer avec --reuse-from")
                print(f"Source locale conservée : {source}", flush=True)
                continue
            if source in errors:
                manifest["sources"][source] = dict(status="unavailable", error=errors[source])
                job.save()
                continue
            print(f"Acquisition : {source}", flush=True)
            manifest["sources"][source] = {"status": "in_progress"}
            job.save()
            try:
                manifest["sources"][source] = operation()
            except LimitError:
                raise  # Ne pas poursuivre les autres sources après un refus de quota.
            except (requests.RequestException, ValueError, RuntimeError, KeyError, ET.ParseError,
                    rasterio.errors.RasterioError) as exc:
                print(f"Source {source} indisponible/incomplète : {exc}", file=sys.stderr)
                manifest["sources"][source] = dict(status="unavailable_or_incomplete", error=str(exc))
            job.save()
        incomplete = any(v["status"] != "complete" for v in manifest["sources"].values())
        print(f"Acquisition {'partielle' if incomplete else 'terminée'} ; stockage comptabilisé : {store.used()/1e9:.3f} Go")
        return 2 if incomplete else 0


def main():
    try:
        return run(parser().parse_args())
    except KeyboardInterrupt:
        print("Interrompu. Relancer la même commande pour reprendre les unités manquantes.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
