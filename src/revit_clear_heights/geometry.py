"""
Geometry module for 2D Shapely polygon operations and Revit geometry conversions.
"""

from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import Point as ShapelyPoint
from shapely.validation import make_valid
from shapely.ops import unary_union

try:
    from Autodesk.Revit.DB import (
        XYZ, Line, SpatialElementBoundaryOptions,
        SpatialElementBoundaryLocation, PlanarFace
    )
except ImportError:
    pass

__revit__: str


def robust_difference(p1, p2):
    """Safely compute difference p1 - p2 with fallback repair."""
    if not p1 or p1.is_empty:
        return p1
    if not p2 or p2.is_empty:
        return p1
    try:
        res = p1.difference(p2)
        if not res.is_valid:
            res = make_valid(res)
        return res
    except Exception:
        try:
            res = make_valid(p1).difference(make_valid(p2))
            if not res.is_valid:
                res = make_valid(res)
            return res
        except Exception:
            try:
                res = p1.buffer(0).difference(p2.buffer(0))
                if not res.is_valid:
                    res = make_valid(res)
                return res
            except Exception:
                return p1


def robust_intersection(p1, p2):
    """Safely compute intersection p1 ∩ p2 with fallback repair."""
    if not p1 or p1.is_empty:
        return None
    if not p2 or p2.is_empty:
        return None
    try:
        res = p1.intersection(p2)
        if not res.is_valid:
            res = make_valid(res)
        return res
    except Exception:
        try:
            res = make_valid(p1).intersection(make_valid(p2))
            if not res.is_valid:
                res = make_valid(res)
            return res
        except Exception:
            try:
                res = p1.buffer(0).intersection(p2.buffer(0))
                if not res.is_valid:
                    res = make_valid(res)
                return res
            except Exception:
                return None


def robust_union(p1, p2):
    """Safely compute union p1 ∪ p2 with fallback repair."""
    if not p1 or p1.is_empty:
        return p2
    if not p2 or p2.is_empty:
        return p1
    try:
        res = p1.union(p2)
        if not res.is_valid:
            res = make_valid(res)
        return res
    except Exception:
        try:
            res = make_valid(p1).union(make_valid(p2))
            if not res.is_valid:
                res = make_valid(res)
            return res
        except Exception:
            try:
                res = p1.buffer(0).union(p2.buffer(0))
                if not res.is_valid:
                    res = make_valid(res)
                return res
            except Exception:
                return p1


def robust_contains(p1, p2):
    """Check if p1 contains or covers p2 with buffer(0) fallback."""
    if not p1 or p1.is_empty or not p2 or p2.is_empty:
        return False
    try:
        return p1.contains(p2) or p1.covers(p2)
    except Exception:
        try:
            return p1.buffer(0).contains(p2.buffer(0)) or p1.buffer(0).covers(p2.buffer(0))
        except Exception:
            return False


def get_face_polygon(face, doc=None, sketch_plane=None, z_height=0.0, create_model_lines=False):
    """Convert a Revit Face to a 2D Shapely Polygon ('shadow')."""
    loops = face.GetEdgesAsCurveLoops()
    if not loops:
        return None

    polys = []
    for i, loop in enumerate(loops):
        points = []
        for curve in loop:
            pts = curve.Tessellate()
            for pt in pts:
                p = (pt.X, pt.Y)
                if not points or points[-1] != p:
                    points.append(p)

        if len(points) >= 3:
            if points[0] != points[-1]:
                points.append(points[0])

            poly = ShapelyPolygon(points)
            if not poly.is_valid:
                poly = make_valid(poly)
            polys.append(poly)

    if not polys:
        return None

    outer_polys = []
    holes = []
    polys.sort(key=lambda p: p.area, reverse=True)

    for p in polys:
        is_hole = False
        for outer in outer_polys:
            if robust_contains(outer, p):
                holes.append(p)
                is_hole = True
                break
        if not is_hole:
            outer_polys.append(p)

    final_outers = []
    for outer in outer_polys:
        current_outer = outer
        for hole in holes:
            if robust_contains(current_outer, hole):
                current_outer = robust_difference(current_outer, hole)
        if current_outer and not current_outer.is_empty:
            final_outers.append(current_outer)

    try:
        combined_poly = unary_union(final_outers)
    except Exception:
        combined_poly = unary_union([p.buffer(0) for p in final_outers])

    if create_model_lines and doc and sketch_plane and combined_poly:
        draw_shapely_polygon(combined_poly, doc, sketch_plane, z_height)

    return combined_poly


def get_room_polygon(room, doc=None, sketch_plane=None, z_height=0.0, create_model_lines=False):
    """Extract boundary curves from a room and convert to a 2D Shapely Polygon."""
    options = SpatialElementBoundaryOptions()
    options.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish

    boundary_segments = room.GetBoundarySegments(options)
    if not boundary_segments or boundary_segments.Count == 0:
        return None

    polys = []
    for segment_list in boundary_segments:
        points = []
        for segment in segment_list:
            curve = segment.GetCurve()
            pts = curve.Tessellate()
            for pt in pts:
                p = (pt.X, pt.Y)
                if not points or points[-1] != p:
                    points.append(p)

        if len(points) >= 3:
            poly = ShapelyPolygon(points)
            if not poly.is_valid:
                poly = make_valid(poly)
            polys.append(poly)

    if not polys:
        return None

    outer_polys = []
    holes = []
    polys.sort(key=lambda p: p.area, reverse=True)

    for p in polys:
        is_hole = False
        for outer in outer_polys:
            if robust_contains(outer, p):
                holes.append(p)
                is_hole = True
                break
        if not is_hole:
            outer_polys.append(p)

    final_outers = []
    for outer in outer_polys:
        current_outer = outer
        for hole in holes:
            if robust_contains(current_outer, hole):
                current_outer = robust_difference(current_outer, hole)
        if current_outer and not current_outer.is_empty:
            final_outers.append(current_outer)

    try:
        combined_poly = unary_union(final_outers)
    except Exception:
        combined_poly = unary_union([p.buffer(0) for p in final_outers])

    if create_model_lines and doc and sketch_plane and combined_poly:
        draw_shapely_polygon(combined_poly, doc, sketch_plane, z_height)

    return combined_poly


def get_z_on_planar_face(face, x, y):
    """Evaluate the exact Z height of a PlanarFace at a given (X, Y) coordinate."""
    if isinstance(face, PlanarFace) or (hasattr(face, "FaceNormal") and hasattr(face, "Origin")):
        n = face.FaceNormal
        o = face.Origin

        if abs(n.Z) < 1e-6:
            return None

        # Plane equation: nx(X - ox) + ny(Y - oy) + nz(Z - oz) = 0
        # Solving for Z: Z = oz - (nx(X - ox) + ny(Y - oy)) / nz
        z = o.Z - (n.X * (x - o.X) + n.Y * (y - o.Y)) / n.Z
        return z

    return None


def draw_shapely_polygon(poly, doc, sketch_plane, z_height):
    """Draw model lines representing a Shapely polygon at z_height."""
    if not poly or poly.is_empty:
        return
    geoms = [poly] if poly.geom_type == 'Polygon' else poly.geoms if poly.geom_type == 'MultiPolygon' else []
    for g in geoms:
        coords = list(g.exterior.coords)
        for i in range(len(coords) - 1):
            p1 = XYZ(coords[i][0], coords[i][1], z_height)
            p2 = XYZ(coords[i+1][0], coords[i+1][1], z_height)
            if p1.DistanceTo(p2) > 0.005:
                try:
                    line = Line.CreateBound(p1, p2)
                    doc.Create.NewModelCurve(line, sketch_plane)
                except Exception:
                    pass
        for hole in g.interiors:
            h_coords = list(hole.coords)
            for i in range(len(h_coords) - 1):
                p1 = XYZ(h_coords[i][0], h_coords[i][1], z_height)
                p2 = XYZ(h_coords[i+1][0], h_coords[i+1][1], z_height)
                if p1.DistanceTo(p2) > 0.005:
                    try:
                        line = Line.CreateBound(p1, p2)
                        doc.Create.NewModelCurve(line, sketch_plane)
                    except Exception:
                        pass


def get_subtracted_polygon(base_poly, subtract_poly, doc=None, sketch_plane=None, z_height=0.0, create_model_lines=False):
    """Subtracts one polygon from another and returns the resulting polygon."""
    result = robust_difference(base_poly, subtract_poly)

    if create_model_lines and doc and sketch_plane and result:
        draw_shapely_polygon(result, doc, sketch_plane, z_height)

    return result


def get_intersection_polygon(polys, doc=None, sketch_plane=None, z_height=0.0, create_model_lines=False):
    """Returns the intersection of a list of polygons."""
    if not polys:
        return None
    result = polys[0]
    for p in polys[1:]:
        result = robust_intersection(result, p)
        if not result or result.is_empty:
            return None

    if create_model_lines and doc and sketch_plane and result:
        draw_shapely_polygon(result, doc, sketch_plane, z_height)

    return result

