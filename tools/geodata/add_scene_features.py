#!/usr/bin/env python3
"""Add static features to an existing prepared zone without resampling its imagery.
Writes a new zone; unchanged GLBs are hard-linked (copied across filesystems).
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct

import numpy as np
from pyproj import Transformer

import prepare
import scene_features


def read_tile(path, allow_features=False):
    data = path.read_bytes()
    size, kind = struct.unpack_from('<II',data,12)
    if struct.unpack_from('<III',data) != (0x46546c67,2,len(data)) or kind != 0x4e4f534a:
        raise ValueError('Invalid GLB')
    doc = json.loads(data[20:20+size])
    binary = data[28+size:]
    def read(index):
        a = doc['accessors'][index]
        v = doc['bufferViews'][a['bufferView']]
        width = {'SCALAR':1,'VEC2':2,'VEC3':3}[a['type']]
        p = np.frombuffer(binary, dtype={5126:'<f4',5125:'<u4'}[a['componentType']], count=a['count']*width, offset=v['byteOffset']).reshape(-1,width)
        return p
    meshes = []
    for primitive in doc['meshes'][0]['primitives']:
        attrs = primitive['attributes']
        enu = read(attrs['POSITION'])[:,[0,2,1]].copy()
        enu[:,1] *= -1
        role = primitive.get('extras',{}).get('role')
        if role not in ('terrain','building_walls','building_roofs') and not (allow_features and role in scene_features.ROLES):
            raise ValueError('Base must contain only terrain and buildings; use the original zone to avoid duplicate features')
        meshes.append((enu,read(primitive['indices']).ravel(), read(attrs['TEXCOORD_0']) if 'TEXCOORD_0' in attrs else None,primitive['material'],role))
    v = doc['bufferViews'][doc['images'][0]['bufferView']]
    return meshes, binary[v['byteOffset']:v['byteOffset']+v['byteLength']], doc['extras']['orthophoto_bounds']


def ground_sampler(mesh, transform, gps_to_local, offset):
    enu, indices = mesh[:2]
    ecef = enu @ transform[:3,:3].T + transform[:3,3]
    lon, lat, height = Transformer.from_crs(4978,4979,always_xy=True).transform(*ecef.T)
    x,y = gps_to_local.transform(lon,lat)
    coords = np.column_stack((x,y))
    tri = coords[np.asarray(indices).reshape(-1,3)]
    heights = (np.asarray(height)-offset)[np.asarray(indices).reshape(-1,3)]
    a,b,c = tri[:,0],tri[:,1],tri[:,2]
    v0,v1 = b-a,c-a
    det = v0[:,0]*v1[:,1]-v0[:,1]*v1[:,0]
    def sample(xx,yy):
        result, good = [], []
        for point in zip(xx,yy):
            v2 = np.asarray(point)-a
            u = (v2[:,0]*v1[:,1]-v2[:,1]*v1[:,0])/det
            v = (v0[:,0]*v2[:,1]-v0[:,1]*v2[:,0])/det
            matches = np.flatnonzero((u>=-1e-7)&(v>=-1e-7)&(u+v<=1+1e-7))
            good.append(bool(len(matches)))
            i = matches[0] if len(matches) else 0
            result.append(heights[i] @ [1-u[i]-v[i],u[i],v[i]] if len(matches) else 0)
        return np.asarray(result),np.asarray(good)
    return sample


def enrich(base, output, features):
    base,output = base.resolve(),output.resolve()
    if output == base or base in output.parents:
        raise ValueError('Output must be outside the original prepared zone')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be empty')
    zone = json.loads((base/'zone.json').read_text())
    if any(zone.get('scene_features',{}).get('counts',{}).values()):
        raise ValueError('Use the original zone without scene features as the base')
    manifest = json.loads((base/'source-manifest.json').read_text())
    tileset = json.loads((base/'tileset.json').read_text())
    xy = Transformer.from_crs(4326,manifest['crs_wkt'],always_xy=True)
    gps = Transformer.from_crs(manifest['crs_wkt'],4326,always_xy=True)
    ecef = Transformer.from_crs(4979,4978,always_xy=True)
    transform = np.array(tileset['root']['transform']).reshape(4,4).T
    rotation,origin = transform[:3,:3],transform[:3,3]
    def positions(x,y,z):
        lon,lat = gps.transform(x,y)
        return (np.array(ecef.transform(lon,lat,np.asarray(z)+zone['height_offset'])).T-origin) @ rotation
    tiles,source = scene_features.load(features,xy,manifest['job']['radius'],zone['preparation']['tile_size_m'])
    counts = dict.fromkeys(scene_features.ROLES,0)
    attribution = '; '.join(source['sources'])
    output.mkdir(parents=True,exist_ok=True)
    bounds = []
    for tile_index, child in enumerate(tileset['root']['children']):
        if tile_index % 100 == 0:
            print(f'Adding features: tile {tile_index}/{len(tileset["root"]["children"])}',flush=True)
        name = child['content']['uri']
        if not re.fullmatch(r'tile_\d+_\d+\.glb',name):
            raise ValueError('Unsupported base tile layout')
        row,col = map(int,re.findall(r'\d+',name))
        if (row,col) in tiles:
            meshes,png,image_bounds = read_tile(base/name)
            terrain = next(m for m in meshes if m[4]=='terrain')
            new,added = scene_features.meshes(tiles[(row,col)],ground_sampler(terrain,transform,xy,zone['height_offset']),positions)
            for kind,count in added.items():
                counts[kind] += count
            meshes.extend(new)
            points = np.vstack([m[0] for m in meshes])
            prepare.glb(output/name,meshes,png,image_bounds,attribution=attribution)
            child['boundingVolume'] = prepare.box(points)
        else:
            try:
                os.link(base/name,output/name)
            except OSError:
                shutil.copy2(base/name,output/name)
        b = child['boundingVolume']['box']
        center,half = np.array(b[:3]),np.diag(np.array(b[3:]).reshape(3,3))
        bounds.extend((center-half,center+half))
    tileset['root']['boundingVolume'] = prepare.box(np.array(bounds))
    tileset['asset']['copyright'] += '; '+attribution
    zone['scene_features'] = dict(counts=counts,source=source,geometry='prototype procedural models; dimensions default where unmeasured')
    zone['attribution'] += '; '+attribution
    prepare.dump(output/'zone.json',zone)
    prepare.dump(output/'tileset.json',tileset)
    shutil.copy2(base/'source-manifest.json',output/'source-manifest.json')
    shutil.copy2(features,output/'scene-features.geojson')
    print(f'Prepared static features in {output}: {counts}',flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--features',type=Path,required=True)
    args = ap.parse_args()
    enrich(args.base,args.output,args.features)


if __name__ == '__main__':
    main()
