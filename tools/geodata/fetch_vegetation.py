#!/usr/bin/env python3
"""Acquire IGN woody vegetation footprints; individual tree locations are estimated."""
import argparse
import json
from pathlib import Path
from pyproj import Transformer
from download import Client, WFS, MB, encoded, wfs_params


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--placements',type=Path,help='Append reviewed parked cars or mapped individual trees')
    args=ap.parse_args()
    if args.output.exists():
        ap.error('Output already exists; choose a new file')
    root=args.input.resolve()
    if root in args.output.resolve().parents:
        ap.error('Keep features outside the acquisition directory')
    manifest=json.loads((root/'manifest.json').read_text())
    gps=Transformer.from_crs(manifest['crs_wkt'],4326,always_xy=True)
    r=manifest['job']['radius']
    bounds=gps.transform_bounds(-r,-r,r,r,densify_pts=21)
    client=Client()
    features,seen=[],set()
    offset,total=0,None
    while total is None or offset<total:
        params=wfs_params(bounds,outputFormat='application/json',srsName='urn:ogc:def:crs:OGC:1.3:CRS84',count=1000,startIndex=offset,sortBy='cleabs A')
        params['typeNames']='BDTOPO_V3:zone_de_vegetation'
        data=client.json(WFS,params=params,cap=32*MB)
        matched=int(data['numberMatched'])
        if total is not None and total!=matched:
            raise ValueError('Vegetation dataset changed during acquisition')
        total=matched
        page=data['features']
        if len(page)!=min(1000,total-offset):
            raise ValueError('Incomplete vegetation pagination')
        for feature in page:
            props=feature['properties']
            identity=props['cleabs']
            if identity in seen:
                raise ValueError('Duplicate vegetation page')
            seen.add(identity)
            nature=props.get('nature','')
            if not any(word in nature.lower() for word in ('bois','forêt','bosquet','peupleraie','haie','verger','arborée')):
                continue
            hedge='haie' in nature.lower()
            feature['properties']=dict(kind='hedgerow' if hedge else 'woodland',
                source='IGN BD TOPO vegetation; Licence Ouverte 2.0',cleabs=identity,nature=nature,
                placement='estimated within mapped vegetation footprint; not individual surveyed trees',
                height=2.5 if hedge else 5 if 'verger' in nature.lower() else 12,
                spacing=2 if hedge else 8, crown_diameter=2.4 if hedge else 7)
            features.append(feature)
        offset+=len(page)
        print(f'Vegetation: {offset}/{total} polygons read',flush=True)
    if args.placements:
        placements=json.loads(args.placements.read_text())
        if placements.get('type')!='FeatureCollection' or 'crs' in placements:
            raise ValueError('Placements must be RFC 7946 WGS84 GeoJSON')
        features.extend(placements['features'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('xb') as f:
        f.write(encoded(dict(type='FeatureCollection',attribution='IGN BD TOPO vegetation / orthophotographies; Licence Ouverte 2.0',
                            source_timestamp=data.get('timeStamp'),features=features)))
    print(f'Saved {len(features)} sourced features to {args.output}',flush=True)


if __name__=='__main__':
    main()
