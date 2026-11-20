"""Binary-format regression checks, independent of Unreal and network."""
import importlib.util
import json
from pathlib import Path
import struct
import sys
sys.path.insert(0, str(Path(__file__).parents[1]/"tools/geodata"))
import tempfile
import unittest
import numpy as np

spec = importlib.util.spec_from_file_location('prepare', Path(__file__).parents[1]/'tools/geodata/prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PreparedFormatTests(unittest.TestCase):
    def test_glb_lengths_axes_indices_normals_and_embedded_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'mesh.glb'
            prepare.glb(p, [(np.array([[0,0,2],[1,0,2],[0,1,2]]),[0,1,2],[[0,0],[1,0],[0,1]],0)], b'PNG-test')
            data = p.read_bytes()
        magic, version, length = struct.unpack_from('<III',data)
        self.assertEqual((magic,version,length),(0x46546c67,2,len(data)))
        size, kind = struct.unpack_from('<II',data,12)
        self.assertEqual(kind,0x4e4f534a)
        doc = json.loads(data[20:20+size])
        binary = data[28+size:]
        def read(idx, dtype, width):
            acc = doc['accessors'][idx]
            view = doc['bufferViews'][acc['bufferView']]
            return np.frombuffer(binary,dtype=dtype,count=acc['count']*width,offset=view['byteOffset']).reshape(-1,width)
        prim = doc['meshes'][0]['primitives'][0]
        np.testing.assert_allclose(read(prim['attributes']['POSITION'],'<f4',3),[[0,2,0],[1,2,0],[0,2,-1]])
        np.testing.assert_allclose(read(prim['attributes']['NORMAL'],'<f4',3),[[0,1,0]]*3)
        np.testing.assert_array_equal(read(prim['indices'],'<u4',1).ravel(),[0,1,2])
        view = doc['bufferViews'][doc['images'][0]['bufferView']]
        self.assertEqual(binary[view['byteOffset']:view['byteOffset']+view['byteLength']],b'PNG-test')
        self.assertEqual(doc['buffers'][0]['byteLength'],len(binary))

    def test_north_to_south_terrain_faces_upward(self):
        x, y = np.meshgrid(np.linspace(0,20,5),np.linspace(10,0,3))
        enu = np.column_stack((x.ravel(),y.ravel(),(x*.1+y*.2).ravel()))
        tri = enu[prepare.grid_triangles(4,2)]
        cross = np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
        self.assertTrue(np.all(cross[:,2] > 0), 'Terrain must face the sky in ENU')

    def test_roof_crossing_tile_border_keeps_geographic_uvs(self):
        corners = np.array([[8,2],[12,2],[12,6],[8,6]],dtype=float)
        for cell in ((0,0,10,10),(10,0,20,10)):
            bounds = prepare.orthophoto_extent(cell,[corners],.2)
            uv = prepare.project_uv(corners,bounds)
            self.assertTrue(np.all((uv>0)&(uv<1)))
            x0,y0,x1,y1 = bounds
            decoded = np.column_stack((x0+uv[:,0]*(x1-x0),y1-uv[:,1]*(y1-y0)))
            np.testing.assert_allclose(decoded,corners)
            self.assertLess(uv[2,1],uv[1,1], 'North must map toward the top of the image')

    def test_roof_is_textured_and_upward_while_walls_stay_neutral(self):
        corners = np.array([[0,0],[4,0],[4,3],[0,3]],dtype=float)
        verts = np.column_stack((np.tile(corners,(2,1)),[0]*4+[8]*4))
        walls, roof = prepare.building_meshes(verts,corners,(-1,-1,5,4))
        self.assertIsNone(walls[2])
        self.assertEqual((walls[3],walls[4],len(walls[1])),(1,'building_walls',30))
        self.assertEqual((roof[3],roof[4],len(roof[1])),(0,'building_roofs',6))
        np.testing.assert_allclose(roof[2],prepare.project_uv(corners,(-1,-1,5,4)))
        triangles = np.array(roof[0])[np.array(roof[1]).reshape(-1,3)]
        normals = np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
        self.assertTrue(np.all(normals[:,2]>0))

    def test_bounding_box_contains_extremes(self):
        points = np.array([[-2,3,7],[5,-9,8]])
        b = prepare.box(points)['box']
        centre, half = np.array(b[:3]), np.diag(np.array(b[3:]).reshape(3,3))
        self.assertTrue(np.all(points >= centre-half))
        self.assertTrue(np.all(points <= centre+half))


if __name__ == '__main__':
    unittest.main()
