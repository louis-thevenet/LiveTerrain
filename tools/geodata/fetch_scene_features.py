#!/usr/bin/env python3
"""Fetch mapped trees and hedges for an acquired zone; never invent parked cars."""
import argparse
import json
from pathlib import Path

from pyproj import Transformer
from download import Client, MB, encoded


def convert(data):
    if 'remark' in data:
        raise ValueError(f'Incomplete Overpass response: {data["remark"]}')
    features = []
    source = '© OpenStreetMap contributors, ODbL 1.0'
    for element in data.get('elements', []):
        tags = element.get('tags', {})
        props = dict(source=source, osm_id=f'{element["type"]}/{element["id"]}')
        for src, dst in (('height','height'), ('diameter_crown','crown_diameter'), ('width','width')):
            try:
                value = float(tags[src])
                if 0 < value <= (20 if dst == 'width' else 50 if dst == 'crown_diameter' else 80):
                    props[dst] = value
            except (KeyError, ValueError, TypeError):
                pass
        if element['type'] == 'node' and tags.get('natural') == 'tree':
            props['kind'] = 'tree'
            geometry = dict(type='Point', coordinates=[element['lon'], element['lat']])
        elif element['type'] == 'way' and tags.get('barrier') == 'hedge':
            coords = [[p['lon'], p['lat']] for p in element.get('geometry', [])]
            if len(coords) < 2:
                continue
            props['kind'] = 'hedge'
            geometry = dict(type='LineString', coordinates=coords)
        else:
            continue
        props['dimensions'] = 'mapped where available; otherwise prototype defaults'
        features.append(dict(type='Feature', properties=props, geometry=geometry))
    return dict(type='FeatureCollection', attribution=source,
                source_timestamp=data.get('osm3s', {}).get('timestamp_osm_base'), features=features)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, default=Path('geodata'), help='Acquisition directory')
    ap.add_argument('--output', type=Path, required=True, help='New GeoJSON file outside acquisition')
    ap.add_argument('--overpass', default='https://overpass-api.de/api/interpreter')
    args = ap.parse_args()
    if args.output.exists():
        ap.error('Output already exists; choose a new filename to preserve reviewed placements')
    root = args.input.resolve()
    if root in args.output.resolve().parents:
        ap.error('Output must be outside the acquisition directory')
    manifest = json.loads((root/'manifest.json').read_text())
    r = manifest['job']['radius']
    gps = Transformer.from_crs(manifest['crs_wkt'], 4326, always_xy=True)
    west, south, east, north = gps.transform_bounds(-r,-r,r,r,densify_pts=21)
    bbox = f'{south},{west},{north},{east}'
    query = f'[out:json][timeout:90];(node["natural"="tree"]({bbox});way["barrier"="hedge"]({bbox}););out geom;'
    data = Client().json(args.overpass, params={'data':query}, cap=32*MB)
    collection = convert(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as out:
        out.write(encoded(collection))
    print(f'Saved {len(collection["features"])} mapped features to {args.output}')


if __name__ == '__main__':
    main()
