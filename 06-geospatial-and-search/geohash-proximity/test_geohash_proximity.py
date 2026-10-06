"""Unit, Bounding Box Invariant, and Spatial Query Tests for Geohash Proximity Engine."""

import pytest

from core import (
    GeohashSpatialIndex,
    decode_geohash,
    decode_geohash_bbox,
    encode_geohash,
    get_geohash_neighbors,
    get_search_cells,
    haversine_distance_km,
)


class TestGeohashEncodingAndDecoding:
    def test_known_landmarks_encoding(self):
        # San Francisco
        gh_sf = encode_geohash(37.7749, -122.4194, precision=7)
        assert gh_sf.startswith("9q8yy")

        # London
        gh_london = encode_geohash(51.5074, -0.1278, precision=7)
        assert gh_london.startswith("gcpvj")

        # Sydney
        gh_sydney = encode_geohash(-33.8688, 151.2093, precision=7)
        assert gh_sydney.startswith("r3gx2")

    def test_decode_roundtrip_precision(self):
        lat_in, lon_in = 37.774929, -122.419416
        gh = encode_geohash(lat_in, lon_in, precision=8)

        lat_out, lon_out = decode_geohash(gh)
        # With precision 8 (approx 38m resolution), error is < 0.0005 degrees
        assert abs(lat_in - lat_out) < 0.0005
        assert abs(lon_in - lon_out) < 0.0005

    def test_out_of_bounds_validation(self):
        with pytest.raises(ValueError):
            encode_geohash(91.0, 0.0)
        with pytest.raises(ValueError):
            encode_geohash(0.0, 185.0)


class TestHaversineDistance:
    def test_zero_distance_same_point(self):
        assert haversine_distance_km(37.7749, -122.4194, 37.7749, -122.4194) == 0.0

    def test_san_francisco_to_new_york(self):
        # SF (37.7749, -122.4194) to NYC (40.7128, -74.0060)
        dist = haversine_distance_km(37.7749, -122.4194, 40.7128, -74.0060)
        # Approximately 4,130 km
        assert 4100.0 < dist < 4160.0


class TestNeighborsAndBoundaries:
    def test_8_neighbors_structure(self):
        gh = "9q8yy"
        neighbors = get_geohash_neighbors(gh)

        assert len(neighbors) == 8
        for d in ["N", "S", "E", "W", "NE", "SE", "NW", "SW"]:
            assert d in neighbors
            assert len(neighbors[d]) == len(gh)
            assert neighbors[d] != gh

        # Search cells must contain exactly 9 cells (center + 8 neighbors)
        cells = get_search_cells(gh)
        assert len(cells) == 9
        assert gh in cells


class TestGeohashSpatialIndex:
    def test_radius_query_filtering_and_sorting(self):
        index = GeohashSpatialIndex()

        # Center point: Downtown San Francisco (Market St & 4th)
        center_lat, center_lon = 37.7850, -122.4060

        # Insert test points at known approximate distances:
        # Driver A: ~0.5 km away (Powell St Station)
        index.insert("driver_A", 37.7844, -122.4080, {"name": "Alice"})
        # Driver B: ~1.8 km away (Ferry Building)
        index.insert("driver_B", 37.7955, -122.3937, {"name": "Bob"})
        # Driver C: ~4.5 km away (Golden Gate Park Panhandle)
        index.insert("driver_C", 37.7720, -122.4470, {"name": "Charlie"})
        # Driver D: ~12.0 km away (Oakland)
        index.insert("driver_D", 37.8044, -122.2712, {"name": "David"})

        # Query radius = 2.0 km -> Should only match Driver A and Driver B
        matches = index.query_radius(center_lat, center_lon, radius_km=2.0)
        assert len(matches) == 2

        matched_ids = [m.point.id for m in matches]
        assert matched_ids == ["driver_A", "driver_B"]

        # Results must be sorted ascending by true distance
        assert matches[0].distance_km < matches[1].distance_km
        assert matches[0].point.id == "driver_A"

    def test_boundary_discontinuity_defense(self):
        """Points across a geohash boundary must still be found via 9-cell neighbor search."""
        index = GeohashSpatialIndex(index_precision=6)

        # Place two points ~250 meters apart that sit on OPPOSITE sides of a geohash boundary
        # Cell 1: 37.7740, -122.4190
        # Cell 2: 37.7758, -122.4190
        p1_lat, p1_lon = 37.7760, -122.4190
        p2_lat, p2_lon = 37.7770, -122.4190

        gh1 = encode_geohash(p1_lat, p1_lon, precision=6)
        gh2 = encode_geohash(p2_lat, p2_lon, precision=6)
        assert gh1 != gh2  # Distinct geohash prefixes across cell boundary!

        index.insert("pickup_point", p1_lat, p1_lon)
        index.insert("nearby_driver", p2_lat, p2_lon)

        # Query from pickup_point with 500m radius
        matches = index.query_radius(p1_lat, p1_lon, radius_km=0.5)

        # Invariant: Both points MUST be found despite differing geohashes!
        matched_ids = {m.point.id for m in matches}
        assert "pickup_point" in matched_ids
        assert "nearby_driver" in matched_ids

    def test_update_and_remove(self):
        index = GeohashSpatialIndex()
        index.insert("car_1", 37.7850, -122.4060)
        assert len(index) == 1

        # Move car 50km away
        index.insert("car_1", 38.2000, -122.4060)
        assert len(index) == 1

        # Query near original spot -> should no longer find car_1
        matches = index.query_radius(37.7850, -122.4060, radius_km=5.0)
        assert len(matches) == 0

        # Remove completely
        assert index.remove("car_1") is True
        assert len(index) == 0
        assert index.remove("nonexistent") is False
