import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np
from pyproj import Geod
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

spec = importlib.util.spec_from_file_location(
    "download", Path(__file__).resolve().parents[1] / "tools/geodata/download.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AcquisitionTests(unittest.TestCase):
    def test_quota_counts_temporary_and_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = module.Store(root, 1_000_000, 0)
            (root / "interrupted.part").write_bytes(b"x" * 700_000)
            with self.assertRaises(module.LimitError):
                store.write("result.bin", b"y" * 300_000)
            self.assertFalse((root / "result.bin").exists())
            self.assertEqual((root / "interrupted.part").stat().st_size, 700_000)

    def test_replacement_reserves_old_and_new(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = module.Store(root, 1_000_000, 0)
            store.write("result.bin", b"a" * 450_000)
            with self.assertRaises(module.LimitError):
                store.write("result.bin", b"b" * 550_000)
            self.assertEqual((root / "result.bin").read_bytes(), b"a" * 450_000)

    def test_corruption_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = module.Job(module.Store(root, 2_000_000, 0), None, {"files": {}})
            job.put("relief/test.bin", b"real-data")
            self.assertTrue(job.cached("relief/test.bin"))
            (root / "relief/test.bin").write_bytes(b"bad-data!")
            self.assertFalse(job.cached("relief/test.bin"))
            self.assertFalse(list(root.rglob("*.part")))

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "escape").symlink_to("/tmp", target_is_directory=True)
            with self.assertRaises(module.LimitError):
                module.Store(root, 1_000_000, 0).check(1)

    def reuse_jobs(self, root):
        source, target = root/"source", root/"target"
        source.mkdir(); target.mkdir()
        (source/".lock").touch()
        identity = dict(schema=1, lat=48., lon=2., radius=1000, dem_step=5,
                        ortho_step=.5, roads=False, overpass="unused")
        previous = module.Job(module.Store(source, 10_000_000, 0), None,
            dict(job=identity, crs_wkt="test-projection", files={}, sources={
                "relief": dict(status="complete"), "batiments": dict(status="complete", count=0)}))
        previous.put("relief/test.bin", b"terrain-fixture")
        previous.put("batiments/test.geojson", b'{"features":[]}')
        previous.put("orthophotos/test.bin", b"imagery-must-not-be-reused")
        current = module.Job(module.Store(target, 10_000_000, 0), None,
            dict(job=dict(identity, ortho_step=.2), crs_wkt="test-projection", files={}, sources={}))
        return source, previous, current

    def test_reuse_preserves_sources_and_excludes_old_imagery(self):
        with tempfile.TemporaryDirectory() as directory:
            source, previous, current = self.reuse_jobs(Path(directory))
            original = (source/"manifest.json").read_bytes()
            module.reuse_sources(current, source)
            self.assertEqual(set(current.manifest["files"]), {"relief/test.bin", "batiments/test.geojson"})
            self.assertTrue(all(current.cached(n) for n in current.manifest["files"]))
            self.assertEqual(current.manifest["sources"]["batiments"]["count"], 0)
            self.assertEqual(current.manifest["job"]["ortho_step"], .2)
            self.assertEqual((source/"manifest.json").read_bytes(), original)
            module.reuse_sources(current, source)  # Safe, idempotent resume.

    def test_reuse_rejects_another_area(self):
        with tempfile.TemporaryDirectory() as directory:
            source, previous, current = self.reuse_jobs(Path(directory))
            current.manifest["job"]["lat"] += 1
            with self.assertRaisesRegex(ValueError, "lat"):
                module.reuse_sources(current, source)
            self.assertEqual(current.manifest["files"], {})

    def test_reuse_rejects_corrupt_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source, previous, current = self.reuse_jobs(Path(directory))
            (source/"relief/test.bin").write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "corrompue"):
                module.reuse_sources(current, source)
            self.assertEqual(current.manifest["files"], {})

    def test_resume_discounts_only_verified_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = module.Job(module.Store(root, 2_000_000, 0), None, {"files": {}})
            job.put("relief/a.bin",b"intact")
            job.put("relief/b.bin",b"original")
            self.assertEqual(job.verified_bytes(),len(b"intactoriginal"))
            (root/"relief/b.bin").write_bytes(b"corrupt")
            (root/"foreign.bin").write_bytes(b"foreign-not-discounted")
            self.assertEqual(job.verified_bytes(),len(b"intact"))

    def test_parallel_fetch_is_bounded_and_preserves_commit_order(self):
        import threading
        import time
        lock = threading.Lock()
        active = maximum = 0
        def fetch(value):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                time.sleep(.004 * (4-value%4))
                return value*value
            finally:
                with lock:
                    active -= 1
        results = list(module.bounded_map(fetch, range(12), 4))
        self.assertEqual(results, [(i,i*i) for i in range(12)])
        self.assertGreater(maximum, 1)
        self.assertLessEqual(maximum, 4)
        self.assertEqual(active, 0)

    def test_altimetry_batch_limit_is_checked_before_any_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"not-created"
            for batch in (0, 4097):
                args = module.parser().parse_args(["--lat", "48", "--lon", "2",
                    "--alti-batch-size", str(batch), "--output", str(output)])
                with self.assertRaises(ValueError):
                    module.run(args)
                self.assertFalse(output.exists())

    def test_zone_distance_and_geotiff_roundtrip(self):
        crs, box, aoi = module.zone(48.85, 2.35, 1000)
        geod = Geod(ellps="WGS84")
        for lon, lat in aoi["features"][0]["geometry"]["coordinates"][0]:
            self.assertAlmostEqual(geod.inv(2.35, 48.85, lon, lat)[2], 1000, places=5)
            self.assertTrue(box[0] <= lon <= box[2] and box[1] <= lat <= box[3])
        transform = from_origin(-1000, 1000, 5, 5)
        array = np.array([[1., -99999.], [3., 4.]], dtype="float32")
        data = module.raster_bytes(array, transform, crs, -99999.)
        with MemoryFile(data) as mem, mem.open() as src:
            np.testing.assert_equal(src.read(1), array)
            self.assertEqual(src.transform, transform)
            self.assertEqual(src.nodata, -99999.)
            self.assertTrue(src.crs.is_projected)


if __name__ == "__main__":
    unittest.main()
