"""Minimal geometry helpers (no third-party GIS dependencies).

Point-in-polygon uses the even-odd rule across all rings of a polygon, which
handles holes without needing to know ring orientation.
"""


def _ring_crossings(x, y, ring):
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_polygon(x, y, rings):
    """rings: list of rings (outer + holes) as [[x, y], ...]."""
    inside = False
    for ring in rings:
        if _ring_crossings(x, y, ring):
            inside = not inside
    return inside


def _bbox(rings):
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return (min(xs), min(ys), max(xs), max(ys))


def geometry_polygons(geom):
    """Yield polygons (list of rings) from a GeoJSON geometry."""
    if not geom:
        return
    gtype = geom.get("type")
    if gtype == "Polygon":
        if geom.get("coordinates"):
            yield geom["coordinates"]
    elif gtype == "MultiPolygon":
        for poly in geom.get("coordinates") or []:
            if poly:
                yield poly
    elif gtype == "GeometryCollection":
        for g in geom.get("geometries") or []:
            yield from geometry_polygons(g)


class PolygonIndex:
    """Bounding-box filtered point-in-polygon lookup over GeoJSON features."""

    def __init__(self, features):
        self.items = []
        for feat in features or []:
            props = feat.get("properties") or {}
            for poly in geometry_polygons(feat.get("geometry")):
                try:
                    bb = _bbox(poly)
                except ValueError:
                    continue
                self.items.append((bb, poly, props))

    def query(self, lon, lat):
        hits = []
        for (x0, y0, x1, y1), poly, props in self.items:
            if lon < x0 or lon > x1 or lat < y0 or lat > y1:
                continue
            if point_in_polygon(lon, lat, poly):
                hits.append(props)
        return hits

    def __len__(self):
        return len(self.items)