"""Static scene geometry anchored to measured points or mapped vegetation areas."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from shapely.geometry import shape, Point, box
from shapely.ops import transform as project
from shapely.prepared import prep

# Material indices follow the terrain and wall materials in prepare.glb.
COLORS = [(0.20, .29, .10), (.28, .37, .13), (.32, .23, .14),
          (.64, .67, .69), (.12, .16, .19), (.035, .035, .04), (.36, .08, .06)]
ROLES = ('tree', 'hedge', 'parked_car')


def positive(props, key, default, maximum):
    try:
        value = float(props.get(key, default))
    except (ValueError, TypeError):
        raise ValueError(f'Invalid feature {key}') from None
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f'Feature {key} must be in (0, {maximum}] metres')
    return value


def load(path, transformer, radius, tile_size):
    """Read WGS84 points, hedge lines and vegetation areas with provenance."""
    if path is None:
        return {}, None
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    if data.get('type') != 'FeatureCollection' or 'crs' in data:
        raise ValueError('Scene features must be RFC 7946 WGS84 GeoJSON')
    tiles, seen = {}, set()
    for feature in data['features']:
        props = dict(feature.get('properties') or {})
        kind = props.get('kind')
        if kind not in (*ROLES, 'woodland', 'hedgerow'):
            raise ValueError(f'Unsupported scene feature kind: {kind}')
        if not props.get('source'):
            raise ValueError('Each scene feature needs a source attribution')
        geometry = shape(feature['geometry'])
        area = kind in ('woodland', 'hedgerow')
        expected = geometry.geom_type if area and geometry.geom_type in ('Polygon','MultiPolygon') else 'LineString' if kind == 'hedge' else 'Point'
        if geometry.is_empty or geometry.geom_type != expected or not geometry.is_valid:
            raise ValueError(f'{kind} requires a valid {expected}')
        if area and geometry.geom_type not in ('Polygon','MultiPolygon'):
            raise ValueError('Vegetation footprints require polygons')
        coords = np.asarray(geometry.bounds).reshape(2,2) if area else np.asarray(geometry.coords)
        if not np.all(np.isfinite(coords)) or np.any(np.abs(coords[:, 0]) > 180) or np.any(np.abs(coords[:, 1]) > 90):
            raise ValueError('Invalid WGS84 feature coordinates')
        geometry = project(transformer.transform, geometry)
        if area:
            spacing = positive(props, 'spacing', 8 if kind == 'woodland' else 2, 50)
            if spacing < 1:
                raise ValueError('Vegetation spacing must be at least 1 metre')
            geometry = geometry.intersection(box(-radius,-radius,radius,radius)).buffer(-1)
            anchors = []
            if not geometry.is_empty:
                x0,y0,x1,y1 = geometry.bounds
                if (x1-x0)*(y1-y0)/spacing**2 > 2000000:
                    raise ValueError('Vegetation footprint has too many candidate trees')
                covered = prep(geometry)
                # Stable spatial jitter breaks rows; mapped polygons constrain centres.
                for ix in range(math.floor(x0/spacing),math.ceil(x1/spacing)):
                    for iy in range(math.floor(y0/spacing),math.ceil(y1/spacing)):
                        seed = hashlib.sha256(f'{ix},{iy}'.encode()).digest()
                        point = Point((ix+.2+.6*seed[0]/255)*spacing,(iy+.2+.6*seed[1]/255)*spacing)
                        if covered.covers(point):
                            anchors.append(point)
            kind = 'tree' if kind == 'woodland' else 'hedge'
            if kind == 'hedge':
                props['width'] = props.get('crown_diameter', 2.4)
        elif kind == 'hedge':
            # Segment long hedges so they stream with their own terrain tile.
            spacing = positive(props, 'width', 1.4, 20)
            count = math.ceil(geometry.length / max(spacing, 1))
            if count > 100000:
                raise ValueError('Hedge has too many segments')
            anchors = [geometry.interpolate((i+.5)/count, normalized=True) for i in range(count)]
        else:
            anchors = [geometry]
        for anchor in anchors:
            x, y = anchor.x, anchor.y
            if not (-radius <= x < radius and -radius <= y < radius):
                continue
            identity = (kind, round(x, 3), round(y, 3))
            if identity in seen:
                continue
            seen.add(identity)
            key = (math.floor((y+radius)/tile_size), math.floor((x+radius)/tile_size))
            tiles.setdefault(key, []).append((kind, x, y, props))
    return tiles, dict(sha256=hashlib.sha256(raw).hexdigest(),
                      attribution=data.get('attribution', ''),
                      sources=sorted({str((f.get('properties') or {}).get('source')) for f in data['features']}))


class Geometry:
    def __init__(self):
        self.batches = {}

    def add(self, points, faces, material, role):
        vertices, indices = self.batches.setdefault((material, role), ([], []))
        offset = len(vertices)
        vertices.extend(points)
        indices.extend(offset+i for i in faces)

    def ellipsoid(self, center, radii, material, role):
        points = []
        rings, sectors = 6, 10
        # One vertex per pole avoids degenerate triangles and zero normals.
        points.append(np.asarray(center) + [0, 0, radii[2]])
        for ring in range(1, rings):
            phi = math.pi*ring/rings
            for sector in range(sectors):
                theta = 2*math.pi*sector/sectors
                points.append(np.asarray(center) + np.asarray(radii)*[math.sin(phi)*math.cos(theta), math.sin(phi)*math.sin(theta), math.cos(phi)])
        bottom = len(points)
        points.append(np.asarray(center) - [0, 0, radii[2]])
        faces = []
        for s in range(sectors):
            n = (s+1)%sectors
            faces.extend((0, 1+s, 1+n))
            for r in range(rings-2):
                a, b = 1+r*sectors+s, 1+r*sectors+n
                faces.extend((a, a+sectors, b, b, a+sectors, b+sectors))
            faces.extend((bottom, 1+(rings-2)*sectors+n, 1+(rings-2)*sectors+s))
        self.add(points, faces, material, role)

    def block(self, center, dimensions, heading, material, role, taper=1):
        # Heading is clockwise from geographic north. Local X is width, Y length.
        width, length, height = dimensions
        angle = math.radians(heading)
        c, s = math.cos(angle), math.sin(angle)
        points = []
        for z, scale in ((-height/2, 1), (height/2, taper)):
            for x, y in ((-width/2,-length/2),(width/2,-length/2),(width/2,length/2),(-width/2,length/2)):
                x, y = x*scale, y*scale
                points.append(np.asarray(center) + [c*x+s*y, -s*x+c*y, z])
        # Separate faces for hard body edges.
        for face in ((0,3,2,1),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7),(4,5,6,7)):
            self.add([points[i] for i in face], (0,1,2,0,2,3), material, role)


def meshes(features, sample, positions):
    geometry, counts = Geometry(), dict.fromkeys(ROLES, 0)
    for kind, x, y, props in features:
        ground, valid = sample(np.array([x]), np.array([y]))
        if not valid[0]:
            continue
        z = float(ground[0])
        if kind in ('tree', 'hedge'):
            height = positive(props, 'height', 8 if kind == 'tree' else 2, 80)
            diameter = positive(props, 'crown_diameter' if kind == 'tree' else 'width', 5 if kind == 'tree' else 1.4, 50)
            if kind == 'tree':
                geometry.block((x,y,z+height*.28), (.24,.24,height*.56), 0, 4, kind)
                geometry.ellipsoid((x,y,z+height*.66), (diameter/2,diameter/2,height*.34), 2, kind)
                # Overlapping crowns break the silhouette without inventing tree locations.
                geometry.ellipsoid((x+diameter*.2,y,z+height*.72), (diameter*.32,diameter*.36,height*.28), 3, kind)
            else:
                geometry.ellipsoid((x,y,z+height*.5), (diameter*.7,diameter*.7,height*.5), 3, kind)
        else:
            if 'heading' not in props:
                raise ValueError('Parked cars require a measured heading (degrees clockwise from north)')
            heading = float(props['heading'])
            if not math.isfinite(heading):
                raise ValueError('Car heading must be finite')
            width = positive(props, 'width', 1.8, 3)
            length = positive(props, 'length', 4.4, 12)
            height = positive(props, 'height', 1.5, 4)
            paint = 8 if props.get('color') == 'red' else 5
            geometry.block((x,y,z+height*.34), (width,length,height*.40), heading, paint, kind, .94)
            geometry.block((x,y,z+height*.66), (width*.88,length*.54,height*.40), heading, 6, kind, .76)
            geometry.block((x,y,z+height*.87), (width*.68,length*.40,height*.04), heading, paint, kind)
            a = math.radians(heading)
            for side in (-1,1):
                for end in (-1,1):
                    dx, dy = side*width*.48, end*length*.31
                    wheel = (x+math.cos(a)*dx+math.sin(a)*dy, y-math.sin(a)*dx+math.cos(a)*dy, z+height*.19)
                    geometry.block(wheel, (.18,length*.15,height*.38), heading, 7, kind)
        counts[kind] += 1
    result = []
    for (material, role), (vertices, indices) in geometry.batches.items():
        vertices = np.asarray(vertices)
        result.append((positions(vertices[:,0], vertices[:,1], vertices[:,2]), indices, None, material, role))
    return result, counts
