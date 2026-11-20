#!/usr/bin/env python3
"""Convert the existing acquisition manifest to offline 3D Tiles 1.1 / GLB.
No networking. All raster inputs are verified and read using their actual CRS.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
from collections import OrderedDict
import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.transform import from_bounds
from rasterio.io import MemoryFile
from pyproj import Transformer
from shapely.geometry import shape
from shapely.geometry.polygon import orient
import scene_features


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2, allow_nan=False) + '\n')


def box(points):
    lo, hi = points.min(axis=0), points.max(axis=0)
    c, r = (lo + hi) / 2, np.maximum((hi - lo) / 2, .1)
    return {'box': [*c.tolist(), r[0], 0, 0, 0, r[1], 0, 0, 0, r[2]]}


def grid_triangles(nx, ny):
    """Counterclockwise triangles for a grid whose rows run north to south."""
    ids = np.arange((nx+1)*(ny+1)).reshape(ny+1,nx+1)
    i, j = ids[:-1,:-1].ravel(), ids[:-1,1:].ravel()
    k, m = ids[1:,:-1].ravel(), ids[1:,1:].ravel()
    return np.column_stack((i,k,j,j,k,m)).reshape(-1,3)


def project_uv(points, bounds):
    """Map AEQD east/north positions to the shared, north-up orthophoto."""
    points = np.asarray(points)
    xmin, ymin, xmax, ymax = bounds
    return np.column_stack(((points[:,0]-xmin)/(xmax-xmin), (ymax-points[:,1])/(ymax-ymin)))


def orthophoto_extent(tile_bounds, corners, step):
    """Include complete roofs crossing tile borders, on a common texel grid."""
    xmin, ymin, xmax, ymax = tile_bounds
    points = np.vstack(([xmin,ymin], [xmax,ymax], *corners))
    lo = np.floor((points.min(axis=0)+1e-9)/step)*step - step
    hi = np.ceil((points.max(axis=0)-1e-9)/step)*step + step
    return (float(lo[0]),float(lo[1]),float(hi[0]),float(hi[1]))


def building_meshes(verts, corners, bounds):
    """Keep walls/floor neutral and project the shared imagery onto the roof."""
    wall_positions, wall_indices = [], []
    for face in ((0,3,2,1),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)):
        start = len(wall_positions)
        wall_positions.extend(verts[list(face)])
        wall_indices.extend((start,start+1,start+2,start,start+2,start+3))
    return ((wall_positions,wall_indices,None,1,'building_walls'),
            (verts[4:8],[0,1,2,0,2,3],project_uv(corners,bounds),0,'building_roofs'))


def glb(path, meshes, png, image_bounds=None, attribution=None):
    """Write terrain, neutral walls and geographically textured roof primitives."""
    blob = bytearray()
    doc = dict(asset={'version': '2.0', 'generator': 'LiveTerrain', 'copyright': 'IGN: BD TOPO, orthophotographies et altimetrie; Licence Ouverte 2.0'}, bufferViews=[], accessors=[],
               materials=[{'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}, 'metallicFactor': 0, 'roughnessFactor': 1}, 'doubleSided': True},
                          {'pbrMetallicRoughness': {'baseColorFactor': [.65, .61, .54, 1], 'metallicFactor': 0, 'roughnessFactor': 1}, 'doubleSided': True}],
               textures=[{'source': 0, 'sampler': 0}], samplers=[{'magFilter': 9729, 'minFilter': 9729, 'wrapS': 33071, 'wrapT': 33071}])
    for color in scene_features.COLORS:
        doc["materials"].append({"pbrMetallicRoughness": {"baseColorFactor": [*color, 1], "metallicFactor": 0, "roughnessFactor": .85}, "doubleSided": True})
    if attribution:
        doc["asset"]["copyright"] += "; " + attribution
    def view(data):
        blob.extend(b'\0' * (-len(blob) % 4))
        idx = len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(blob), 'byteLength': len(data)})
        blob.extend(data)
        return idx
    def accessor(data, kind, component):
        idx = len(doc['accessors'])
        a = dict(bufferView=view(data.tobytes()), componentType=component, count=len(data), type=kind)
        if kind == 'VEC3':
            a.update(min=data.min(axis=0).tolist(), max=data.max(axis=0).tolist())
        doc['accessors'].append(a)
        return idx
    prims = []
    for mesh_index, mesh in enumerate(meshes):
        positions, indices, uv, mat = mesh[:4]
        role = mesh[4] if len(mesh) > 4 else ('terrain' if mesh_index == 0 else 'buildings')
        if not len(indices):
            continue
        # Convert ENU Z-up to glTF Y-up; Cesium applies the inverse axis transform.
        p = np.asarray(positions, dtype='<f4')[:, [0, 2, 1]].copy()
        p[:, 2] *= -1
        attrs = {'POSITION': accessor(p, 'VEC3', 5126)}
        # Area-weighted smooth normals; buildings duplicate face vertices below.
        ids = np.asarray(indices, dtype='<u4').reshape(-1, 3)
        normals = np.zeros_like(p)
        face = np.cross(p[ids[:, 1]] - p[ids[:, 0]], p[ids[:, 2]] - p[ids[:, 0]])
        for col in range(3):
            np.add.at(normals, ids[:, col], face)
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
        attrs['NORMAL'] = accessor(normals.astype('<f4'), 'VEC3', 5126)
        if uv is not None:
            attrs['TEXCOORD_0'] = accessor(np.asarray(uv, dtype='<f4'), 'VEC2', 5126)
        prims.append(dict(attributes=attrs, indices=accessor(ids.ravel(), 'SCALAR', 5125), material=mat, extras={'role':role}))
    if image_bounds is not None:
        doc['extras'] = {'orthophoto_bounds': list(image_bounds)}
    doc['images'] = [{'bufferView': view(png), 'mimeType': 'image/png'}]
    doc.update(meshes=[{'primitives': prims}], nodes=[{'mesh': 0}], scenes=[{'nodes': [0]}], scene=0)
    blob.extend(b'\0' * (-len(blob) % 4))
    doc['buffers'] = [{'byteLength': len(blob)}]
    js = json.dumps(doc, separators=(',', ':'), allow_nan=False).encode()
    js += b' ' * (-len(js) % 4)
    path.write_bytes(struct.pack('<III', 0x46546c67, 2, 28 + len(js) + len(blob)) +
                     struct.pack('<II', len(js), 0x4e4f534a) + js + struct.pack('<II', len(blob), 0x004e4942) + blob)


def prepare(args):
    root, out = args.input.resolve(), args.output.resolve()
    if out == root or root in out.parents:
        raise ValueError('Output must be outside the acquisition directory')
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output must be empty (use a new directory or remove the previous prepared output)')
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest['job']['schema'] != 1:
        raise ValueError('Unsupported acquisition schema')
    for source in ('relief', 'orthophotos', 'batiments'):
        if manifest['sources'].get(source, {}).get('status') != 'complete':
            raise ValueError(f'Incomplete acquisition: {source}')
    groups = {k: [] for k in ('relief', 'orthophotos', 'batiments')}
    for name, rec in manifest['files'].items():
        p = (root / name).resolve()
        if root not in p.parents:
            raise ValueError('Unsafe manifest path')
        if hashlib.sha256(p.read_bytes()).hexdigest() != rec['sha256']:
            raise ValueError(f'Corrupt input: {name}')
        for group in groups:
            if name.startswith(group + '/'):
                groups[group].append(p)
    if not all(groups.values()):
        raise ValueError('Missing terrain, imagery or building files')
    job = manifest['job']
    if args.texture_step is None:
        args.texture_step = float(job['ortho_step'])
    if not math.isfinite(args.texture_step) or args.texture_step <= 0:
        raise ValueError('texture-step must be positive and finite')
    if args.tile_size/args.texture_step > 4096:
        raise ValueError('Texture dimensions exceed 4096 pixels; increase texture-step')
    print(f'Imagery: {args.texture_step:g} m/pixel; source request {job["ortho_step"]:g} m/pixel', flush=True)
    lon, lat = job['lon'], job['lat']
    local = manifest['crs_wkt']
    gps = Transformer.from_crs(local, 4326, always_xy=True)
    xy = Transformer.from_crs(4326, local, always_xy=True)
    ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    l, a = np.radians([lon, lat])
    rotation = np.array([[-np.sin(l), -np.sin(a)*np.cos(l), np.cos(a)*np.cos(l)],
                         [np.cos(l), -np.sin(a)*np.sin(l), np.cos(a)*np.sin(l)],
                         [0, np.cos(a), np.sin(a)]])
    origin = np.array(ecef.transform(lon, lat, args.height_offset))
    transform = np.eye(4)
    transform[:3, :3], transform[:3, 3] = rotation, origin
    def positions(x, y, h):
        lo, la = gps.transform(x, y)
        return (np.array(ecef.transform(lo, la, np.asarray(h) + args.height_offset)).T - origin) @ rotation
    datasets = [rasterio.open(p) for p in sorted(groups['relief'])]
    try:
        dem, dt = merge(datasets)
        dem_crs, nodata = datasets[0].crs, datasets[0].nodata
    finally:
        for ds in datasets:
            ds.close()
    if dem_crs != rasterio.crs.CRS.from_wkt(local):
        raise ValueError('DEM CRS differs from acquisition manifest')
    buildings = []
    for p in groups['batiments']:
        for f in json.loads(p.read_text())['features']:
            if not f.get('geometry'):
                continue
            g = shape(f['geometry'])
            for poly in (list(g.geoms) if g.geom_type == 'MultiPolygon' else [g]):
                if poly.geom_type != 'Polygon' or poly.is_empty:
                    continue
                from shapely.ops import transform as geom_transform
                rect = geom_transform(xy.transform, poly).minimum_rotated_rectangle
                if rect.geom_type != 'Polygon':
                    continue
                rect = orient(rect, sign=1.0)
                prop = f.get('properties') or {}
                try:
                    h = float(prop.get('hauteur'))
                    if not math.isfinite(h) or h <= 0:
                        h = args.default_height
                except (TypeError, ValueError):
                    h = args.default_height
                buildings.append((rect, h))
    out.mkdir(parents=True, exist_ok=True)
    ortho_sources = []
    for path in sorted(groups['orthophotos']):
        with rasterio.open(path) as ds:
            ortho_sources.append((path,transform_bounds(ds.crs, local, *ds.bounds, densify_pts=21)))
    # Larger zones have thousands of GeoTIFFs; bound live file descriptors.
    ortho_cache = OrderedDict()
    tile_size = args.tile_size
    children, all_bounds = [], []
    count_buildings = 0
    radius = job['radius']
    step = job['dem_step']
    ntiles = math.ceil(2*radius/tile_size)
    features_path = getattr(args, "features", None)
    features_by_tile, features_source = scene_features.load(features_path, xy, radius, tile_size)
    feature_counts = dict.fromkeys(scene_features.ROLES, 0)
    buildings_by_tile = {}
    for rect, h in buildings:
        cx, cy = rect.centroid.coords[0]
        if not (math.isfinite(cx) and math.isfinite(cy) and -radius <= cx < radius and -radius <= cy < radius):
            continue
        key = (math.floor((cy+radius)/tile_size),math.floor((cx+radius)/tile_size))
        buildings_by_tile.setdefault(key,[]).append((rect,h))
    def sample(x, y):
        # Bilinear DEM sample at pixel centres, clamped only at outer coverage.
        col = np.clip((np.asarray(x)-dt.c)/dt.a-.5, 0, dem.shape[2]-1)
        row = np.clip((np.asarray(y)-dt.f)/dt.e-.5, 0, dem.shape[1]-1)
        c, r = col.astype(int), row.astype(int)
        c1, r1 = np.minimum(c+1, dem.shape[2]-1), np.minimum(r+1, dem.shape[1]-1)
        values = np.array([dem[0,r,c], dem[0,r,c1], dem[0,r1,c], dem[0,r1,c1]])
        valid = np.all(np.isfinite(values) & (values != nodata), axis=0)
        fx, fy = col-c, row-r
        h = values[0]*(1-fx)*(1-fy)+values[1]*fx*(1-fy)+values[2]*(1-fx)*fy+values[3]*fx*fy
        return h, valid
    try:
        for row in range(ntiles):
            for col in range(ntiles):
                xmin, ymin = -radius+col*tile_size, -radius+row*tile_size
                xmax, ymax = min(xmin+tile_size, radius), min(ymin+tile_size, radius)
                nx, ny = math.ceil((xmax-xmin)/step), math.ceil((ymax-ymin)/step)
                xx, yy = np.meshgrid(np.linspace(xmin,xmax,nx+1), np.linspace(ymax,ymin,ny+1))
                zz, valid = sample(xx.ravel(), yy.ravel())
                if not np.any(valid):
                    continue
                zz = np.where(valid, zz, 0)
                p = positions(xx.ravel(), yy.ravel(), zz)
                indices = grid_triangles(nx, ny)
                indices = indices[np.all(valid[indices], axis=1)]
                if not len(indices):
                    continue
                used = np.unique(indices)
                remap = np.zeros(len(p), dtype=np.uint32)
                remap[used] = np.arange(len(used))
                p, indices = p[used], remap[indices]
                tile_buildings = []
                for rect, h in buildings_by_tile.get((row,col),[]):
                    corners = np.array(rect.exterior.coords)[:4,:2]
                    base, good = sample(corners[:,0],corners[:,1])
                    if not np.all(good):
                        continue
                    levels = np.r_[np.full(4,base.min()),np.full(4,base.max()+h)]
                    verts = positions(np.tile(corners[:,0],2),np.tile(corners[:,1],2),levels)
                    tile_buildings.append((corners,verts))
                image_bounds = orthophoto_extent((xmin,ymin,xmax,ymax),
                    [corners for corners,verts in tile_buildings],args.texture_step)
                ixmin, iymin, ixmax, iymax = image_bounds
                width = max(1, math.ceil(round((ixmax-ixmin)/args.texture_step,8)))
                height = max(1, math.ceil(round((iymax-iymin)/args.texture_step,8)))
                if max(width,height) > 4096:
                    print(f'Roof extent exceeds 4096 pixels; resampling tile {row},{col}', flush=True)
                    width, height = min(width,4096), min(height,4096)
                # Uncovered imagery at the acquisition's outer edge remains neutral.
                rgb = np.empty((3,height,width), dtype='uint8')
                rgb[:] = np.array([210,205,195],dtype='uint8')[:,None,None]
                dst_transform = from_bounds(*image_bounds,width,height)
                uv = project_uv(np.column_stack((xx.ravel(),yy.ravel()))[used],image_bounds)
                # Reproject each WMS raster into the same metric grid as the terrain.
                for path, bounds in ortho_sources:
                    if bounds[2] < ixmin or bounds[0] > ixmax or bounds[3] < iymin or bounds[1] > iymax:
                        continue
                    ortho = ortho_cache.pop(path,None)
                    if ortho is None:
                        if len(ortho_cache) >= 128:
                            _, old = ortho_cache.popitem(last=False)
                            old.close()
                        ortho = rasterio.open(path)
                    ortho_cache[path] = ortho
                    for band in range(1,4):
                        reproject(rasterio.band(ortho,band), rgb[band-1], src_transform=ortho.transform,
                                  src_crs=ortho.crs, dst_transform=dst_transform, dst_crs=local,
                                  resampling=Resampling.bilinear, init_dest_nodata=False)
                with MemoryFile() as mem:
                    with mem.open(driver='PNG',width=width,height=height,count=3,dtype='uint8',transform=dst_transform,zlevel=3) as img:
                        img.write(rgb)
                    png = mem.read()
                bp, bi, rp, ri, ruv = [], [], [], [], []
                for corners, verts in tile_buildings:
                    walls, roof = building_meshes(verts,corners,image_bounds)
                    offset = len(bp)
                    bp.extend(walls[0]); bi.extend(offset+i for i in walls[1])
                    offset = len(rp)
                    rp.extend(roof[0]); ri.extend(offset+i for i in roof[1]); ruv.extend(roof[2])
                    count_buildings += 1
                points = np.vstack((p, np.array(bp).reshape(-1,3),np.array(rp).reshape(-1,3)))
                name = f'tile_{row}_{col}.glb'
                meshes = [(p,indices,uv,0,'terrain')]
                if bp:
                    meshes.extend(((bp,bi,None,1,'building_walls'),(rp,ri,ruv,0,'building_roofs')))
                feature_meshes, counts = scene_features.meshes(features_by_tile.get((row,col), []), sample, positions)
                meshes.extend(feature_meshes)
                if feature_meshes:
                    points = np.vstack((points, *(m[0] for m in feature_meshes)))
                for kind, count in counts.items():
                    feature_counts[kind] += count
                glb(out/name,meshes,png,image_bounds=image_bounds,
                    attribution="; ".join(features_source["sources"]) if features_source else None)
                bounds = box(points)
                children.append(dict(boundingVolume=bounds, geometricError=0, content={'uri':name}))
                all_bounds.extend([points.min(axis=0),points.max(axis=0)])
                print(f'{name}: {len(indices)} terrain triangles, {(len(bi)+len(ri))//3} building triangles', flush=True)
    finally:
        for ds in ortho_cache.values():
            ds.close()
    if not children:
        raise ValueError('No valid terrain tiles')
    (out/'source-manifest.json').write_bytes((root/'manifest.json').read_bytes())
    attribution = 'IGN; Licence Ouverte 2.0'
    if features_source:
        attribution += '; ' + '; '.join(features_source['sources'])
    dump(out/'tileset.json',dict(asset={'version':'1.1', 'copyright':attribution},geometricError=1e6,
         root=dict(boundingVolume=box(np.array(all_bounds)),transform=transform.T.ravel().tolist(),
                   geometricError=1e6,refine='REPLACE',children=children)))
    center_h, center_good = sample(np.array([0.]),np.array([0.]))
    if features_path is not None:
        (out/'scene-features.geojson').write_bytes(Path(features_path).read_bytes())
    dump(out/'zone.json',dict(schema=1,longitude=lon,latitude=lat,
         height=float(center_h[0]+args.height_offset) if center_good[0] else args.height_offset,
         height_offset=args.height_offset,vertical_reference='constant IGN normal to WGS84 ellipsoid offset; approximate',
         source_manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
         preparation=dict(tile_size_m=args.tile_size, texture_step_m=args.texture_step,
                          source_ortho_step_m=job['ortho_step'], default_height_m=args.default_height,
                          roof_orthophoto=True),
         scene_features=dict(counts=feature_counts, source=features_source, geometry='prototype procedural models; dimensions default where unmeasured'),
         tiles=len(children),buildings=count_buildings,attribution='IGN BD TOPO / orthophotographies / altimétrie; see original manifest'))
    print(f'Prepared {len(children)} tiles, {count_buildings} buildings in {out}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',type=Path,default=Path('geodata'))
    ap.add_argument('--output',type=Path,default=Path('Prepared/zone'))
    ap.add_argument('--tile-size',type=float,default=256)
    ap.add_argument('--texture-step',type=float,default=None,
                    help='Metres per texture pixel; defaults to ortho_step from the acquisition manifest')
    ap.add_argument('--features',type=Path,help='WGS84 GeoJSON with sourced tree, hedge and reviewed parked_car placements')
    ap.add_argument('--default-height',type=float,default=8)
    ap.add_argument('--height-offset',type=float,required=True,help='Explicit constant H_ellipsoid - H_IGN in metres; 0 is approximate only')
    args = ap.parse_args()
    for key in ('tile_size','default_height'):
        if not math.isfinite(getattr(args,key)) or getattr(args,key) <= 0:
            ap.error(f'{key} must be positive and finite')
    if not math.isfinite(args.height_offset):
        ap.error('height-offset must be finite')
    prepare(args)


if __name__ == '__main__':
    main()
