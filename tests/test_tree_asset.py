"""Tree import coordinates and leaf atlas regression."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).parents[1]/'tools/assets'))
from tree_obj import convert


class TreeImportTests(unittest.TestCase):
    def test_upright_centimetres_and_explicit_leaf_uv_transform(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            arrays=[np.array([[1,2,3],[2,2,3],[1,3,3]],dtype='<f4'),
                    np.array([[.25,.5],[.5,.5],[.25,.75]],dtype='<f4'),
                    np.array([[0,1,0]]*3,dtype='<f4'),np.array([0,1,2],dtype='<u4')]
            blob=bytearray();views=[]
            for a in arrays:
                views.append(dict(byteOffset=len(blob),byteLength=a.nbytes));blob.extend(a.tobytes())
            doc=dict(bufferViews=views,accessors=[dict(bufferView=i,componentType=5125 if i==3 else 5126,
                         count=3,type='VEC3' if i in (0,2) else 'VEC2' if i==1 else 'SCALAR') for i in range(4)],
                materials=[dict(name='leaves',pbrMetallicRoughness=dict(baseColorTexture=dict(extensions={
                    'KHR_texture_transform':dict(scale=[2,3],offset=[.1,-.2])})))],
                meshes=[dict(primitives=[dict(attributes=dict(POSITION=0,TEXCOORD_0=1,NORMAL=2),indices=3,material=0)])])
            (root/'tree-source.gltf').write_text(json.dumps(doc));(root/'island_tree_02.bin').write_bytes(blob)
            lines=convert(root).read_text().splitlines()
            vertices=np.array([[float(x) for x in line.split()[1:]] for line in lines if line.startswith('v ')])
            normals=np.array([[float(x) for x in line.split()[1:]] for line in lines if line.startswith('vn ')])
            uv=np.array([[float(x) for x in line.split()[1:]] for line in lines if line.startswith('vt ')])
            np.testing.assert_allclose(vertices[0],[100,-300,200])
            np.testing.assert_allclose(normals,[[0,0,1]]*3)
            np.testing.assert_allclose(uv[0],[.6,-.3])
            self.assertIn('usemtl leaves',lines)
            self.assertIn('f 1/1/1 2/2/2 3/3/3',lines)
