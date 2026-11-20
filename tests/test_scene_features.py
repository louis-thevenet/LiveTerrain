"""Geographic placement, ground anchoring, and static scene integration."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from pyproj import CRS, Transformer
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).parents[1]/'tools/geodata'))
import scene_features
import fetch_scene_features
import prepare
import add_scene_features
import native_trees
import validate


class SceneFeatureTests(unittest.TestCase):
    def test_only_mapped_trees_and_hedges_are_acquired(self):
        result = fetch_scene_features.convert({'elements':[
            dict(type='node',id=1,lon=2,lat=48,tags={'natural':'tree','height':'12'}),
            dict(type='way',id=2,tags={'amenity':'parking'},geometry=[{'lon':2,'lat':48}]),
            dict(type='way',id=3,tags={'barrier':'hedge'},geometry=[{'lon':2,'lat':48},{'lon':2.001,'lat':48}])]})
        self.assertEqual([f['properties']['kind'] for f in result['features']], ['tree','hedge'])
        self.assertEqual(result['features'][0]['properties']['height'],12)
        with self.assertRaises(ValueError):
            fetch_scene_features.convert({'remark':'timed out'})

    def test_woodland_uses_only_its_mapped_footprint_and_respects_holes(self):
        from shapely.geometry import Polygon, mapping, Point
        from shapely.ops import transform
        local = CRS.from_proj4('+proj=aeqd +lat_0=48 +lon_0=2 +datum=WGS84 +units=m')
        gps = Transformer.from_crs(local,4326,always_xy=True)
        xy = Transformer.from_crs(4326,local,always_xy=True)
        polygon = Polygon([(-30,-30),(30,-30),(30,30),(-30,30)],
                          holes=[[(-10,-10),(10,-10),(10,10),(-10,10)]])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'woodland.geojson'
            path.write_text(json.dumps(dict(type='FeatureCollection',features=[dict(type='Feature',
                geometry=mapping(transform(gps.transform,polygon)),
                properties=dict(kind='woodland',source='test coverage',spacing=8))])))
            first,_=scene_features.load(path,xy,50,25)
            second,_=scene_features.load(path,xy,50,25)
            self.assertEqual(first,second)
            features=[f for tile in first.values() for f in tile]
            self.assertGreater(len(features),20)
            for kind,x,y,props in features:
                self.assertEqual(kind,'tree')
                self.assertTrue(polygon.contains(Point(x,y)))

    def test_models_grounding_heading_and_invalid_ground(self):
        def sample(x,y):
            return np.full(len(x),20.), x != 100
        def positions(x,y,z):
            return np.column_stack((x,y,z))
        for heading in (0,90):
            meshes, counts = scene_features.meshes([('parked_car',0,0,{'heading':heading})],sample,positions)
            points = np.vstack([m[0] for m in meshes])
            self.assertAlmostEqual(points[:,2].min(),20)
            self.assertEqual(counts['parked_car'],1)
            extent = np.ptp(points,axis=0)
            self.assertGreater(extent[1 if heading == 0 else 0], extent[0 if heading == 0 else 1])
        meshes, counts = scene_features.meshes([('tree',100,0,{})],sample,positions)
        self.assertEqual(meshes,[])
        self.assertEqual(counts['tree'],0)
        with self.assertRaisesRegex(ValueError,'heading'):
            scene_features.meshes([('parked_car',0,0,{})],sample,positions)
        with self.assertRaises(ValueError):
            scene_features.meshes([('tree',0,0,{'height':float('nan')})],sample,positions)

    def test_streamed_tiles_include_features_and_valid_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'input'
            out = Path(tmp)/'output'
            root.mkdir()
            local = CRS.from_proj4('+proj=aeqd +lat_0=48 +lon_0=2 +datum=WGS84 +units=m')
            manifest = dict(job=dict(schema=1,lat=48,lon=2,radius=10,dem_step=2,ortho_step=1),
                            crs_wkt=local.to_wkt(), files={},sources={k:{'status':'complete'} for k in ('relief','orthophotos','batiments')})
            for group, bands, dtype in [('relief',1,'float32'),('orthophotos',3,'uint8')]:
                (root/group).mkdir()
                path = root/group/'test.tif'
                with rasterio.open(path,'w',driver='GTiff',width=24,height=24,count=bands,dtype=dtype,
                                   crs=local,transform=from_origin(-12,12,1,1),nodata=0) as ds:
                    ds.write(np.full((1,24,24),20,dtype=dtype) if bands==1 else np.tile(np.arange(24,dtype='uint8'),(3,24,1))+50)
            (root/'batiments').mkdir()
            (root/'batiments/test.geojson').write_text(json.dumps(dict(type='FeatureCollection',features=[])))
            for p in root.rglob('*'):
                if p.is_file():
                    manifest['files'][str(p.relative_to(root))]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            (root/'manifest.json').write_text(json.dumps(manifest))
            gps = Transformer.from_crs(local,4326,always_xy=True)
            features = []
            for kind,x,y,props in [('tree',-2,-2,{}),('parked_car',2,2,{'heading':90}),('tree',200,200,{})]:
                features.append(dict(type='Feature',geometry=dict(type='Point',coordinates=gps.transform(x,y)),
                                     properties=dict(kind=kind,source='synthetic integration fixture',**props)))
            # A duplicate must not create two objects; west/south edges belong to one tile only.
            features.append(features[0])
            features.append(dict(type='Feature',geometry=dict(type='LineString',coordinates=[gps.transform(-8,4),gps.transform(8,4)]),
                                 properties=dict(kind='hedge',source='synthetic integration fixture')))
            path=Path(tmp)/'features.geojson'
            path.write_text(json.dumps(dict(type='FeatureCollection',features=features)))
            prepare.prepare(argparse.Namespace(input=root,output=out,tile_size=10,texture_step=1,
                                               default_height=8,height_offset=0,features=path))
            zone=json.loads((out/'zone.json').read_text())
            self.assertEqual(zone['scene_features']['counts'],dict(tree=1,hedge=12,parked_car=1))
            self.assertEqual((out/'scene-features.geojson').read_bytes(),path.read_bytes())
            validate.validate(out)
            # Enriching existing tiles must preserve imagery and original files.
            original = Path(tmp)/'original'
            prepare.prepare(argparse.Namespace(input=root,output=original,tile_size=10,texture_step=1,
                                               default_height=8,height_offset=0,features=None))
            old_bytes = (original/'tile_0_0.glb').read_bytes()
            enriched = Path(tmp)/'enriched'
            add_scene_features.enrich(original,enriched,path)
            validate.validate(enriched)
            self.assertEqual((original/'tile_0_0.glb').read_bytes(),old_bytes)
            native = Path(tmp)/'native'
            native_trees.convert(enriched,native)
            validate.validate(native)
            raw=(native/'trees.bin').read_bytes()
            instances=np.frombuffer(raw,dtype=native_trees.DTYPE,offset=12)
            self.assertEqual(len(instances),1)
            ecef=Transformer.from_crs(4979,4978,always_xy=True)
            expected=ecef.transform(*gps.transform(-2,-2),20)
            np.testing.assert_allclose(instances['ecef'][0],expected,atol=.001)
            self.assertAlmostEqual(float(instances['height'][0]),8,places=3)
            self.assertAlmostEqual(float(instances['diameter'][0]),5,places=3)
            with self.assertRaises(ValueError):
                native_trees.convert(native,Path(tmp)/'duplicate-native')

            self.assertEqual(json.loads((enriched/'zone.json').read_text())['scene_features']['counts'],zone['scene_features']['counts'])
            with self.assertRaises(ValueError):
                add_scene_features.enrich(enriched,Path(tmp)/'duplicate',path)
            features[0]['properties'].pop('source')
            path.write_text(json.dumps(dict(type='FeatureCollection',features=features)))
            with self.assertRaisesRegex(ValueError,'source'):
                scene_features.load(path,Transformer.from_crs(4326,local,always_xy=True),10,10)


if __name__ == '__main__':
    unittest.main()
