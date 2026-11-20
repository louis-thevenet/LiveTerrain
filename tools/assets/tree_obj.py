"""Convert Poly Haven's explicit glTF UVs to an upright Unreal OBJ import."""
import json
import math
from pathlib import Path
import numpy as np


def convert(root):
    root=Path(root)
    doc=json.loads((root/'tree-source.gltf').read_text())
    raw=(root/'island_tree_02.bin').read_bytes()
    def read(index):
        a=doc['accessors'][index]
        view=doc['bufferViews'][a['bufferView']]
        if 'byteStride' in view or 'sparse' in a:raise ValueError('Unsupported tree buffer layout')
        dtype={5126:'<f4',5125:'<u4',5123:'<u2'}[a['componentType']]
        width={'VEC3':3,'VEC2':2,'SCALAR':1}[a['type']]
        return np.frombuffer(raw,dtype=dtype,count=a['count']*width,
            offset=view.get('byteOffset',0)+a.get('byteOffset',0)).reshape(-1,width)
    with (root/'tree-correct-uv.obj').open('w') as out:
        out.write('mtllib tree-correct-uv.mtl\n')
        offset=1
        for primitive in doc['meshes'][0]['primitives']:
            # Source glTF is metres/Y-up; Unreal OBJ import expects cm/Z-up.
            p=read(primitive['attributes']['POSITION'])[:,[0,2,1]].copy()*100
            p[:,1]*=-1
            normal=read(primitive['attributes']['NORMAL'])[:,[0,2,1]].copy()
            normal[:,1]*=-1
            uv=read(primitive['attributes']['TEXCOORD_0']).copy()
            material=doc['materials'][primitive['material']]
            mapping=material['pbrMetallicRoughness']['baseColorTexture'].get('extensions',{}).get('KHR_texture_transform',{})
            uv*=np.array(mapping.get('scale',[1,1]))
            angle=mapping.get('rotation',0)
            uv=uv@np.array([[math.cos(angle),math.sin(angle)],[-math.sin(angle),math.cos(angle)]])
            uv+=np.array(mapping.get('offset',[0,0]))
            uv[:,1]=1-uv[:,1]  # OBJ UV origin is bottom-left.
            np.savetxt(out,p,fmt='v %.7g %.7g %.7g')
            np.savetxt(out,uv,fmt='vt %.7g %.7g')
            np.savetxt(out,normal,fmt='vn %.7g %.7g %.7g')
            out.write('usemtl '+material['name']+'\n')
            for a,b,c in read(primitive['indices']).reshape(-1,3)+offset:
                out.write(f'f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}\n')
            offset+=len(p)
    (root/'tree-correct-uv.mtl').write_text(''.join('newmtl '+m['name']+'\nKd 1 1 1\n\n' for m in doc['materials']))
    return root/'tree-correct-uv.obj'
