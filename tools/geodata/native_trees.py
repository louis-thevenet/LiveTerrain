#!/usr/bin/env python3
"""Replace baked placeholder trees with shared native-mesh placements.
Preserves the existing geographic anchors, imagery, hedges and parked cars.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import numpy as np
import prepare
from add_scene_features import read_tile

DTYPE=np.dtype([('ecef','<f8',(3,)),('height','<f4'),('diameter','<f4')])


def extract(meshes, transform):
    trunks=next((m for m in meshes if m[4]=='tree' and m[3]==4),None)
    crowns=next((m for m in meshes if m[4]=='tree' and m[3]==2),None)
    if trunks is None:
        if any(m[4]=='tree' for m in meshes):raise ValueError('Unrecognized placeholder tree format')
        return np.empty(0,dtype=DTYPE)
    if len(trunks[0])%24 or crowns is None or len(crowns[0])//52!=len(trunks[0])//24:
        raise ValueError('Invalid placeholder trunk/crown geometry')
    p=np.asarray(trunks[0]).reshape(-1,24,3)
    base=p[:,:4].mean(axis=1)
    top=p[:,-4:].mean(axis=1)
    result=np.empty(len(base),dtype=DTYPE)
    result['ecef']=base@transform[:3,:3].T+transform[:3,3]
    result['height']=np.linalg.norm(top-base,axis=1)/.56
    crown=np.asarray(crowns[0]).reshape(-1,52,3)
    result['diameter']=np.ptp(crown[:,:,0],axis=1)
    if not np.all(np.isfinite(result['ecef'])) or np.any(result['height']<=0) or np.any(result['diameter']<=0):
        raise ValueError('Invalid tree placement')
    return result


def convert(base,output):
    base,output=base.resolve(),output.resolve()
    if output==base or base in output.parents:raise ValueError('Use a separate output directory')
    if output.exists() and any(output.iterdir()):raise ValueError('Output must be empty')
    zone=json.loads((base/'zone.json').read_text())
    if zone.get('native_trees'):raise ValueError('Base already uses native trees')
    tileset=json.loads((base/'tileset.json').read_text())
    transform=np.asarray(tileset['root']['transform']).reshape(4,4).T
    records=[]
    output.mkdir(parents=True,exist_ok=True)
    for i,tile in enumerate(tileset['root']['children']):
        name=tile['content']['uri']
        source=(base/name).resolve()
        if base not in source.parents:raise ValueError('Unsafe tile URI')
        target=output/name
        target.parent.mkdir(parents=True,exist_ok=True)
        meshes,png,bounds=read_tile(source,allow_features=True)
        trees=extract(meshes,transform)
        if len(trees):
            records.append(trees)
            keep=[m for m in meshes if m[4]!='tree']
            prepare.glb(target,keep,png,bounds,attribution=zone.get('attribution'))
        else:
            try:os.link(source,target)
            except OSError:shutil.copy2(source,target)
        if i%100==0:print(f'Native tree conversion: {i}/{len(tileset["root"]["children"])} tiles',flush=True)
    data=np.concatenate(records) if records else np.empty(0,dtype=DTYPE)
    if len(data)!=zone['scene_features']['counts']['tree']:
        raise ValueError('Tree count changed during conversion')
    raw=struct.pack('<4sII',b'LTRE',1,len(data))+data.tobytes()
    (output/'trees.bin').write_bytes(raw)
    zone['native_trees']=dict(file='trees.bin',schema=1,count=len(data),sha256=hashlib.sha256(raw).hexdigest(),
        mesh='/Game/Vegetation/IslandTree02/SM_IslandTree02.SM_IslandTree02',
        source='https://polyhaven.com/a/island_tree_02',license='CC0 1.0',credit='Powered by Poly Haven',
        placement='Existing geographic anchors preserved; tree species and dimensions are illustrative')
    zone['scene_features']['geometry']='Native textured instanced tree mesh; procedural hedges and parked cars'
    prepare.dump(output/'zone.json',zone)
    prepare.dump(output/'tileset.json',tileset)
    for name in ('source-manifest.json','scene-features.geojson'):
        if (base/name).exists():shutil.copy2(base/name,output/name)
    print(f'Native tree instances: {len(data)}; {len(raw)} bytes; {output}',flush=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    convert(args.base,args.output)


if __name__=='__main__':main()
