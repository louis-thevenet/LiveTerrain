#!/usr/bin/env python3
"""Download a checksum-verified CC0 tree source for Unreal import. Powered by Poly Haven."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'LiveTerrain/1.0 (local asset import)'})
    with urllib.request.urlopen(req,timeout=60) as response:
        return response.read()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=Path('.build/tree-assets/island_tree_02'))
    args=ap.parse_args()
    catalog=json.loads(fetch('https://api.polyhaven.com/files/island_tree_02'))
    model=catalog['fbx']['2k']['fbx']
    geometry=catalog['gltf']['1k']['gltf']
    files={'tree-source.gltf':geometry,'island_tree_02.bin':geometry['include']['island_tree_02.bin'],**model['include']}
    records={}
    for name,record in files.items():
        path=args.output/name
        if '..' in Path(name).parts or Path(name).is_absolute():
            raise ValueError('Unsafe source filename')
        data=path.read_bytes() if path.exists() else fetch(record['url'])
        if len(data)!=record['size'] or hashlib.md5(data).hexdigest()!=record['md5']:
            raise ValueError(f'Invalid source checksum: {name}')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
        records[name]=dict(url=record['url'],bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        print(f'Verified {name}: {len(data)} bytes',flush=True)
    manifest=dict(asset='Island Tree 02',source='https://polyhaven.com/a/island_tree_02',
                  license='CC0 1.0',credit='Powered by Poly Haven',files=records)
    (args.output/'source.json').write_text(json.dumps(manifest,indent=2)+'\n')
    from tree_obj import convert
    print('Unreal import source:',convert(args.output),flush=True)


if __name__=='__main__':
    main()
