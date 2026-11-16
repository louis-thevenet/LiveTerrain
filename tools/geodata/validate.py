#!/usr/bin/env python3
"""Validate actual prepared GLBs: buffers, bounds, indices, ECEF and shared edges."""
import argparse
import json
from pathlib import Path
import struct
import numpy as np
from pyproj import Transformer
from rasterio.io import MemoryFile


def validate(folder):
    tileset = json.loads((folder/'tileset.json').read_text())
    zone = json.loads((folder/'zone.json').read_text())
    transform = np.array(tileset['root']['transform']).reshape(4,4).T
    inv = Transformer.from_crs(4978,4979,always_xy=True)
    textured_roofs = zone.get('preparation',{}).get('roof_orthophoto',False)
    projected = None
    if textured_roofs:
        source = json.loads((folder/'source-manifest.json').read_text())
        projected = Transformer.from_crs(4326,source['crs_wkt'],always_xy=True)
    edge_heights = {}
    triangles = buildings = roofs = 0
    max_imagery_error_m = 0.
    for tile in tileset['root']['children']:
        path = (folder/tile['content']['uri']).resolve()
        assert folder.resolve() in path.parents
        data = path.read_bytes()
        assert struct.unpack_from('<III',data) == (0x46546c67,2,len(data)), path
        size, kind = struct.unpack_from('<II',data,12)
        assert kind == 0x4e4f534a
        doc = json.loads(data[20:20+size])
        binary = data[28+size:]
        assert len(binary) == doc['buffers'][0]['byteLength']
        def accessor(index):
            a = doc['accessors'][index]
            v = doc['bufferViews'][a['bufferView']]
            width = {'SCALAR':1,'VEC2':2,'VEC3':3}[a['type']]
            dtype = {5126:'<f4',5125:'<u4'}[a['componentType']]
            nbytes = a['count']*width*4
            assert nbytes <= v['byteLength'] and v['byteOffset']+v['byteLength'] <= len(binary)
            return np.frombuffer(binary,dtype=dtype,count=a['count']*width,offset=v['byteOffset']).reshape(-1,width)
        b = tile['boundingVolume']['box']
        centre = np.array(b[:3])
        half = np.diag(np.array(b[3:]).reshape(3,3))
        tile_walls = tile_roofs = 0
        for idx, prim in enumerate(doc['meshes'][0]['primitives']):
            role = prim.get('extras',{}).get('role','terrain' if idx == 0 else 'buildings')
            p = accessor(prim['attributes']['POSITION'])
            assert np.all(np.isfinite(p))
            enu = p[:,[0,2,1]].copy()
            enu[:,1] *= -1
            assert np.all(np.abs(enu-centre) <= half+.001), path
            indices = accessor(prim['indices']).ravel()
            assert len(indices)%3 == 0 and indices.max() < len(p)
            triangles += len(indices)//3
            normals = accessor(prim['attributes']['NORMAL'])
            assert np.all(np.isfinite(normals))
            if role == 'building_walls':
                assert len(indices)%30 == 0 and prim['material'] == 1
                assert 'TEXCOORD_0' not in prim['attributes']
                tile_walls = len(indices)//30
                buildings += tile_walls
            elif role == 'buildings':  # Previous format: roof and walls share one neutral primitive.
                assert len(indices)%36 == 0
                buildings += len(indices)//36
            elif role == 'building_roofs':
                assert len(indices)%6 == 0 and prim['material'] == 0
                tile_roofs = len(indices)//6
                roofs += tile_roofs
            else:
                assert role == 'terrain' and prim['material'] == 0
            if role in ('terrain','building_roofs'):
                assert np.all(normals[:,1] > 0), f'{role} normals face downward: {path}'
                tri = p[indices.reshape(-1,3)]
                cross = np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
                assert np.all(cross[:,1] > 0), f'{role} triangles face downward: {path}'
                uv = accessor(prim['attributes']['TEXCOORD_0'])
                assert np.all((uv>=0)&(uv<=1)), (path,role)
                if role == 'terrain':
                    for point in enu:
                        key = tuple(np.round(point[:2],3))
                        if key in edge_heights:
                            assert abs(edge_heights[key]-point[2]) < .002, (path,key)
                        edge_heights[key] = point[2]
                ecef = enu @ transform[:3,:3].T + transform[:3,3]
                lo, la, h = inv.transform(*ecef.T)
                assert np.max(np.abs(lo-zone['longitude'])) < .1
                assert np.max(np.abs(la-zone['latitude'])) < .1
                assert np.all(np.isfinite(h))
                if textured_roofs:
                    x,y = projected.transform(lo,la)
                    x0,y0,x1,y1 = doc['extras']['orthophoto_bounds']
                    expected_uv = np.column_stack(((x-x0)/(x1-x0),(y1-y)/(y1-y0)))
                    # GLB positions are float32 ENU metres. At 5 km their
                    # quantization reaches 0.25 mm, amplified in normalized
                    # UVs on the narrow tiles at the acquisition boundary.
                    # Check registration in metres, independent of tile width.
                    span = np.array([x1-x0,y1-y0])
                    max_imagery_error_m = max(max_imagery_error_m,float(np.max(np.abs((uv-expected_uv)*span))))
                    np.testing.assert_allclose(uv*span,expected_uv*span,atol=.002,rtol=0,
                        err_msg=f'{role} imagery is geographically misaligned: {path}')
        if textured_roofs:
            assert tile_roofs == tile_walls, path
        view = doc['bufferViews'][doc['images'][0]['bufferView']]
        png = binary[view['byteOffset']:view['byteOffset']+view['byteLength']]
        with MemoryFile(png) as mem, mem.open() as image:
            rgb = image.read()
            assert rgb.shape[0] == 3 and np.std(rgb) > 1, f'Empty/uniform imagery: {path}'
    assert buildings == zone['buildings'], (buildings,zone['buildings'])
    if textured_roofs:
        assert roofs == buildings, (roofs,buildings)
        print(f'Validated {roofs} geographically textured roofs; walls remain neutral')
        print(f'Maximum imagery registration error: {max_imagery_error_m:.6f} m (limit 0.002 m)')
    print(f'Validated {len(tileset["root"]["children"])} real tiles, {buildings} boxes, {triangles} triangles; buffers, bounds, imagery, ECEF and shared vertices OK')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('folder',type=Path,nargs='?',default=Path('Prepared/zone'))
    validate(p.parse_args().folder)
