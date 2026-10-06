"""Geohash Proximity Engine & Spatial Bounding Box Index.

Implements:
1. Base32 Geohashing: Interleaved bitwise encoding and decoding of (lat, lon) coordinates.
2. 8-Neighbor Calculation: Computes adjacent grid cells to solve the boundary discontinuity problem.
3. Haversine Distance: Spherical great-circle distance calculation for precision filtering.
4. GeohashSpatialIndex: High-performance in-memory spatial index supporting radius queries.
"""

from dataclasses import dataclass
import math
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

# Standard Geohash Base32 alphabet (excludes 'a', 'i', 'l', 'o' to avoid confusion)
BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
BASE32_MAP = {c: i for i, c in enumerate(BASE32)}
EARTH_RADIUS_KM = 6371.0088


def encode_geohash(latitude: float, longitude: float, precision: int = 7) -> str:
    """Encodes latitude and longitude into a Base32 Geohash string.

    Args:
        latitude: [-90.0, 90.0]
        longitude: [-180.0, 180.0]
        precision: Number of Base32 characters (1 to 12).
    """
    if not (-90.0 <= latitude <= 90.0):
        raise ValueError(f"Latitude {latitude} out of bounds [-90, 90]")
    if not (-180.0 <= longitude <= 180.0):
        raise ValueError(f"Longitude {longitude} out of bounds [-180, 180]")

    lat_interval = [-90.0, 90.0]
    lon_interval = [-180.0, 180.0]

    geohash = []
    bits = [16, 8, 4, 2, 1]
    bit = 0
    ch = 0
    is_even = True  # Even bit = longitude, Odd bit = latitude

    while len(geohash) < precision:
        if is_even:
            mid = (lon_interval[0] + lon_interval[1]) / 2.0
            if longitude >= mid:
                ch |= bits[bit]
                lon_interval[0] = mid
            else:
                lon_interval[1] = mid
        else:
            mid = (lat_interval[0] + lat_interval[1]) / 2.0
            if latitude >= mid:
                ch |= bits[bit]
                lat_interval[0] = mid
            else:
                lat_interval[1] = mid

        is_even = not is_even
        if bit < 4:
            bit += 1
        else:
            geohash.append(BASE32[ch])
            bit = 0
            ch = 0

    return "".join(geohash)


def decode_geohash_bbox(geohash: str) -> Tuple[float, float, float, float]:
    """Decodes a Geohash string into its bounding box (min_lat, min_lon, max_lat, max_lon)."""
    if not geohash:
        raise ValueError("Geohash cannot be empty")

    lat_interval = [-90.0, 90.0]
    lon_interval = [-180.0, 180.0]
    is_even = True

    for c in geohash.lower():
        if c not in BASE32_MAP:
            raise ValueError(f"Invalid geohash character: '{c}'")
        cd = BASE32_MAP[c]
        for mask in [16, 8, 4, 2, 1]:
            if is_even:
                mid = (lon_interval[0] + lon_interval[1]) / 2.0
                if cd & mask:
                    lon_interval[0] = mid
                else:
                    lon_interval[1] = mid
            else:
                mid = (lat_interval[0] + lat_interval[1]) / 2.0
                if cd & mask:
                    lat_interval[0] = mid
                else:
                    lat_interval[1] = mid
            is_even = not is_even

    return lat_interval[0], lon_interval[0], lat_interval[1], lon_interval[1]


def decode_geohash(geohash: str) -> Tuple[float, float]:
    """Decodes a Geohash string to the center (latitude, longitude) of its bounding box."""
    min_lat, min_lon, max_lat, max_lon = decode_geohash_bbox(geohash)
    return (min_lat + max_lat) / 2.0, (min_lon + max_lon) / 2.0


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates spherical distance between two points in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_KM * c


def get_geohash_neighbors(geohash: str) -> Dict[str, str]:
    """Calculates all 8 neighboring geohash cells (N, NE, E, SE, S, SW, W, NW)."""
    min_lat, min_lon, max_lat, max_lon = decode_geohash_bbox(geohash)
    precision = len(geohash)

    lat_height = max_lat - min_lat
    lon_width = max_lon - min_lon

    center_lat = (min_lat + max_lat) / 2.0
    center_lon = (min_lon + max_lon) / 2.0

    def wrap_lat(lat: float) -> float:
        return max(-89.9999, min(89.9999, lat))

    def wrap_lon(lon: float) -> float:
        while lon > 180.0:
            lon -= 360.0
        while lon < -180.0:
            lon += 360.0
        return lon

    directions = {
        "N": (center_lat + lat_height, center_lon),
        "S": (center_lat - lat_height, center_lon),
        "E": (center_lat, center_lon + lon_width),
        "W": (center_lat, center_lon - lon_width),
        "NE": (center_lat + lat_height, center_lon + lon_width),
        "SE": (center_lat - lat_height, center_lon + lon_width),
        "NW": (center_lat + lat_height, center_lon - lon_width),
        "SW": (center_lat - lat_height, center_lon - lon_width),
    }

    neighbors: Dict[str, str] = {}
    for direction, (lat, lon) in directions.items():
        neighbors[direction] = encode_geohash(wrap_lat(lat), wrap_lon(lon), precision=precision)

    return neighbors


def get_search_cells(geohash: str) -> Set[str]:
    """Returns the set of 9 search cells: the central cell + all 8 neighbors."""
    neighbors = get_geohash_neighbors(geohash)
    cells = set(neighbors.values())
    cells.add(geohash)
    return cells


def select_optimal_precision(radius_km: float) -> int:
    """Maps a search radius to the optimal Geohash precision level ensuring 9 cells enclose radius."""
    # Cell height approx: p1: 5000km, p2: 625km, p3: 156km, p4: 19.5km, p5: 4.9km, p6: 0.61km, p7: 153m, p8: 19m
    if radius_km > 600.0:
        return 1
    elif radius_km > 150.0:
        return 2
    elif radius_km > 30.0:
        return 3
    elif radius_km > 5.0:
        return 4
    elif radius_km > 1.0:
        return 5
    elif radius_km > 0.15:
        return 6
    elif radius_km > 0.03:
        return 7
    return 8


@dataclass
class SpatialPoint:
    id: str
    latitude: float
    longitude: float
    geohash: str
    metadata: Dict[str, Any]


@dataclass
class ProximityMatch:
    point: SpatialPoint
    distance_km: float


class GeohashSpatialIndex:
    """Thread-safe In-Memory Spatial Index powered by Geohash prefix lookups."""

    def __init__(self, index_precision: int = 8):
        self.index_precision = index_precision
        self._points: Dict[str, SpatialPoint] = {}
        # Maps geohash_prefix -> set of point_ids
        self._prefix_index: Dict[str, Set[str]] = {}
        self._lock = threading.Lock()

    def insert(self, point_id: str, latitude: float, longitude: float, metadata: Optional[Dict[str, Any]] = None) -> SpatialPoint:
        """Inserts or updates a spatial point."""
        gh = encode_geohash(latitude, longitude, precision=self.index_precision)
        point = SpatialPoint(
            id=point_id,
            latitude=latitude,
            longitude=longitude,
            geohash=gh,
            metadata=metadata or {},
        )

        with self._lock:
            # If point existed, remove from old prefix buckets
            if point_id in self._points:
                self._remove_from_index(point_id)

            self._points[point_id] = point

            # Index all prefixes from 1 to index_precision for fast prefix lookups
            for length in range(1, self.index_precision + 1):
                prefix = gh[:length]
                if prefix not in self._prefix_index:
                    self._prefix_index[prefix] = set()
                self._prefix_index[prefix].add(point_id)

            return point

    def _remove_from_index(self, point_id: str) -> None:
        old_point = self._points.get(point_id)
        if not old_point:
            return
        gh = old_point.geohash
        for length in range(1, self.index_precision + 1):
            prefix = gh[:length]
            if prefix in self._prefix_index:
                self._prefix_index[prefix].discard(point_id)
                if not self._prefix_index[prefix]:
                    del self._prefix_index[prefix]

    def remove(self, point_id: str) -> bool:
        with self._lock:
            if point_id in self._points:
                self._remove_from_index(point_id)
                del self._points[point_id]
                return True
            return False

    def query_radius(
        self,
        center_lat: float,
        center_lon: float,
        radius_km: float,
        limit: Optional[int] = None,
    ) -> List[ProximityMatch]:
        """Finds all points within radius_km using 9-cell Geohash expansion + Haversine filtering.

        Algorithm:
        1. Select optimal Geohash precision level P matching radius.
        2. Encode center point to precision P.
        3. Compute 8 neighbors + center = 9 bounding box cells.
        4. Collect candidate points whose Geohash matches any of the 9 cells.
        5. Filter candidates using exact Haversine distance <= radius_km.
        6. Sort by distance ascending and apply limit.
        """
        precision = min(self.index_precision, select_optimal_precision(radius_km))
        center_gh = encode_geohash(center_lat, center_lon, precision=precision)
        search_cells = get_search_cells(center_gh)

        candidate_ids: Set[str] = set()

        with self._lock:
            for cell in search_cells:
                if cell in self._prefix_index:
                    candidate_ids.update(self._prefix_index[cell])

            candidates = [self._points[pid] for pid in candidate_ids if pid in self._points]

        # Exact distance calculation & spherical filtering
        matches: List[ProximityMatch] = []
        for point in candidates:
            dist = haversine_distance_km(center_lat, center_lon, point.latitude, point.longitude)
            if dist <= radius_km:
                matches.append(ProximityMatch(point=point, distance_km=dist))

        matches.sort(key=lambda m: m.distance_km)

        if limit is not None:
            return matches[:limit]
        return matches

    def __len__(self) -> int:
        with self._lock:
            return len(self._points)
