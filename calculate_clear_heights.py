from revit_rpc import revit
import numpy as np
from matplotlib.path import Path
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.validation import make_valid

try:
    from Autodesk.Revit.DB import (
        FilteredElementCollector, View3D, ReferenceIntersector,
        FindReferenceTarget, XYZ, SpatialElementBoundaryOptions,
        SpatialElementBoundaryLocation, Transaction,
        BuiltInParameter, BuiltInCategory, ElementMulticategoryFilter,
        Line, Plane, SketchPlane, PlanarFace
    )
    from System.Collections.Generic import List
except ImportError:
    pass

__revit__: str

# Constants from main.py
GRID_SPACING_MIN = 3.28 * 0.5  # ~0.5m for small rooms
GRID_SPACING_MAX = 3.28 * 1.5  # ~1.5m for large rooms
AREA_MIN = 10.76 * 10   # 10 m²
AREA_MAX = 10.76 * 100  # 100 m²
BOUNDARY_INSET = 0.328  # ~10cm
THRESHOLD = 0.08  # 8% area threshold


def calculate_adaptive_grid_spacing(room_area):
    """Calculate grid spacing based on room area."""
    if room_area <= AREA_MIN:
        return GRID_SPACING_MIN
    elif room_area >= AREA_MAX:
        return GRID_SPACING_MAX
    else:
        t = (room_area - AREA_MIN) / (AREA_MAX - AREA_MIN)
        return GRID_SPACING_MIN + t * (GRID_SPACING_MAX - GRID_SPACING_MIN)


def get_room_boundary_points(room, options):
    """Extract boundary curves from a room and return as list of boundary loops."""
    boundary_segments = room.GetBoundarySegments(options)
    if not boundary_segments or boundary_segments.Count == 0:
        return []
    
    boundary_loops = []
    for segment_list in boundary_segments:
        loop_points = []
        for segment in segment_list:
            curve = segment.GetCurve()
            loop_points.append(curve.GetEndPoint(0))
        if loop_points:
            boundary_loops.append(loop_points)
    return boundary_loops


def generate_points_along_boundary(boundary_points, segment_length, z_height):
    """Generate points along a boundary with specified segment length."""
    if not boundary_points or len(boundary_points) < 2:
        return []
    
    points = []
    n = len(boundary_points)
    
    for i in range(n):
        x1, y1 = boundary_points[i]
        x2, y2 = boundary_points[(i + 1) % n]
        
        dx = x2 - x1
        dy = y2 - y1
        edge_length = (dx * dx + dy * dy) ** 0.5
        
        if edge_length < 0.001:
            continue
        
        num_segments = max(1, int(edge_length / segment_length))
        
        for j in range(num_segments):
            t = j / num_segments
            x = x1 + t * dx
            y = y1 + t * dy
            points.append(XYZ(x, y, z_height))
    
    return points


def inset_boundary_loop_tuples(boundary_tuples, inset_distance):
    """Inset a boundary loop by a specified distance."""
    if not boundary_tuples or len(boundary_tuples) < 3:
        return []
    
    n = len(boundary_tuples)
    inset_points = []
    
    for i in range(n):
        x1, y1 = boundary_tuples[(i - 1) % n]
        x2, y2 = boundary_tuples[i]
        x3, y3 = boundary_tuples[(i + 1) % n]
        
        dx1 = x2 - x1
        dy1 = y2 - y1
        len1 = (dx1 * dx1 + dy1 * dy1) ** 0.5
        if len1 < 0.001:
            continue
        dx1 /= len1
        dy1 /= len1
        
        dx2 = x3 - x2
        dy2 = y3 - y2
        len2 = (dx2 * dx2 + dy2 * dy2) ** 0.5
        if len2 < 0.001:
            continue
        dx2 /= len2
        dy2 /= len2
        
        n1x = -dy1
        n1y = dx1
        n2x = -dy2
        n2y = dx2
        
        nx = n1x + n2x
        ny = n1y + n2y
        nlen = (nx * nx + ny * ny) ** 0.5
        if nlen < 0.001:
            continue
        nx /= nlen
        ny /= nlen
        
        dot = nx * n1x + ny * n1y
        if abs(dot) < 0.001:
            continue
        
        offset = inset_distance / dot
        offset = max(-inset_distance * 10, min(inset_distance * 10, offset))
        
        inset_points.append((x2 + nx * offset, y2 + ny * offset))
    
    if len(inset_points) < 3:
        return []
    
    area = 0
    for i in range(len(inset_points)):
        x1, y1 = inset_points[i]
        x2, y2 = inset_points[(i + 1) % len(inset_points)]
        area += (x2 - x1) * (y2 + y1)
    
    original_area = 0
    for i in range(len(boundary_tuples)):
        x1, y1 = boundary_tuples[i]
        x2, y2 = boundary_tuples[(i + 1) % len(boundary_tuples)]
        original_area += (x2 - x1) * (y2 + y1)
    
    if original_area * area < 0 or abs(area) < inset_distance * inset_distance * 0.1:
        return []
    
    return inset_points


def create_grid_points_bounding_box(boundary_loops, grid_spacing, boundary_inset, z_height, profile=False):
    """Create grid points within the polygon using a bounding box approach."""
    import time
    
    if not boundary_loops:
        return []
    
    t_start = time.time()
    
    outer_boundary = boundary_loops[0]
    holes = boundary_loops[1:] if len(boundary_loops) > 1 else []
    
    outer_vertices = np.array([(p.X, p.Y) for p in outer_boundary])
    outer_path = Path(outer_vertices)
    
    hole_paths = []
    for hole in holes:
        hole_vertices = np.array([(p.X, p.Y) for p in hole])
        hole_paths.append(Path(hole_vertices))
    
    all_points = []
    
    original_boundary_tuples = [(p.X, p.Y) for p in outer_boundary]
    inset_boundary = inset_boundary_loop_tuples(original_boundary_tuples, boundary_inset)
    
    if inset_boundary:
        corner_coords = np.array(inset_boundary)
        corners_valid = np.ones(len(corner_coords), dtype=bool)
        for hole_path in hole_paths:
            corners_in_hole = hole_path.contains_points(corner_coords)
            corners_valid &= ~corners_in_hole
        
        for i, (cx, cy) in enumerate(corner_coords):
            if corners_valid[i]:
                all_points.append(XYZ(cx, cy, z_height))
        
        boundary_points = generate_points_along_boundary(inset_boundary, grid_spacing, z_height)
        
        if boundary_points:
            boundary_coords = np.array([(p.X, p.Y) for p in boundary_points])
            boundary_valid = np.ones(len(boundary_coords), dtype=bool)
            for hole_path in hole_paths:
                in_hole = hole_path.contains_points(boundary_coords)
                boundary_valid &= ~in_hole
            
            for i, point in enumerate(boundary_points):
                if boundary_valid[i]:
                    all_points.append(point)
    
    min_x = outer_vertices[:, 0].min()
    max_x = outer_vertices[:, 0].max()
    min_y = outer_vertices[:, 1].min()
    max_y = outer_vertices[:, 1].max()
    
    min_x += boundary_inset
    max_x -= boundary_inset
    min_y += boundary_inset
    max_y -= boundary_inset
    
    if min_x >= max_x or min_y >= max_y:
        return all_points
    
    x_coords = np.arange(min_x, max_x + grid_spacing / 2, grid_spacing)
    y_coords = np.arange(min_y, max_y + grid_spacing / 2, grid_spacing)
    
    xx, yy = np.meshgrid(x_coords, y_coords)
    grid_coords = np.column_stack([xx.ravel(), yy.ravel()])
    
    inside_outer = outer_path.contains_points(grid_coords)
    
    inside_holes = np.zeros(len(grid_coords), dtype=bool)
    for hole_path in hole_paths:
        inside_holes |= hole_path.contains_points(grid_coords)
    
    valid_mask = inside_outer & ~inside_holes
    valid_coords = grid_coords[valid_mask]
    
    for x, y in valid_coords:
        all_points.append(XYZ(x, y, z_height))
    
    if profile:
        t_end = time.time()
        print(f"    [PROFILE] Grid generation total: {t_end - t_start:.4f}s")
    
    return all_points


def get_face_polygon(face):
    """Convert a Revit Face to a 2D Shapely Polygon."""
    loops = face.GetEdgesAsCurveLoops()
    if not loops:
        return None

    polys = []
    for loop in loops:
        points = []
        for curve in loop:
            pt = curve.GetEndPoint(0)
            points.append((pt.X, pt.Y))

        if len(points) >= 3:
            poly = ShapelyPolygon(points)
            if not poly.is_valid:
                poly = make_valid(poly)
            polys.append(poly)

    if not polys:
        return None

    combined_poly = polys[0]
    for hole in polys[1:]:
        combined_poly = combined_poly.difference(hole)

    return combined_poly


def get_room_polygon(room):
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
            pt = curve.GetEndPoint(0)
            points.append((pt.X, pt.Y))

        if len(points) >= 3:
            poly = ShapelyPolygon(points)
            if not poly.is_valid:
                poly = make_valid(poly)
            polys.append(poly)

    if not polys:
        return None

    combined_poly = polys[0]
    for hole in polys[1:]:
        combined_poly = combined_poly.difference(hole)

    return combined_poly


def get_z_on_planar_face(face, x, y):
    """Evaluate the exact Z height of a PlanarFace at a given (X, Y) coordinate."""
    if isinstance(face, PlanarFace) or (hasattr(face, "FaceNormal") and hasattr(face, "Origin")):
        n = face.FaceNormal
        o = face.Origin

        if abs(n.Z) < 1e-6:
            return None

        z = o.Z - (n.X * (x - o.X) + n.Y * (y - o.Y)) / n.Z
        return z

    return None


def get_face_identifier(face):
    """Generate a unique identifier for a face based on its geometric properties."""
    try:
        if hasattr(face, "Origin") and hasattr(face, "FaceNormal"):
            # For planar faces, use origin and normal
            origin = face.Origin
            normal = face.FaceNormal
            # Round to avoid floating point precision issues
            return (
                round(origin.X, 6), round(origin.Y, 6), round(origin.Z, 6),
                round(normal.X, 6), round(normal.Y, 6), round(normal.Z, 6)
            )
        else:
            # For non-planar faces, use bounding box center
            bbox = face.GetBoundingBox()
            if bbox:
                center_x = (bbox.Min.X + bbox.Max.X) / 2
                center_y = (bbox.Min.Y + bbox.Max.Y) / 2
                center_z = (bbox.Min.Z + bbox.Max.Z) / 2
                return (round(center_x, 6), round(center_y, 6), round(center_z, 6))
    except:
        pass
    # Fallback: use object id (not ideal but better than nothing)
    return id(face)


def find_face_from_ray_hit(doc, refs, max_distance, require_upward_normal, debug=False):
    """Find the closest face from ray hits with correct normal direction.
    
    Returns:
        Tuple of (distance, face, element) or (None, None, None) if no valid hit
    """
    closest_distance = None
    closest_face = None
    closest_elem = None
    
    for ref_with_context in refs:
        ref = ref_with_context.GetReference()
        if ref is None:
            continue
            
        proximity = ref_with_context.Proximity
        
        if closest_distance is not None and proximity >= closest_distance:
            continue
        
        if proximity > max_distance:
            continue
        
        elem_id = ref.ElementId
        elem = doc.GetElement(elem_id)
        if not elem:
            continue
            
        param = elem.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
        if not param or param.AsInteger() != 1:
            continue
        
        try:
            face = elem.GetGeometryObjectFromReference(ref)
            if face:
                uv = ref.UVPoint
                if uv:
                    normal = face.ComputeNormal(uv)
                else:
                    normal = face.ComputeNormal(face.GetBoundingBox().Min)
                
                if require_upward_normal and normal.Z <= 0:
                    continue
                if not require_upward_normal and normal.Z >= 0:
                    continue
                
                closest_distance = proximity
                closest_face = face
                closest_elem = elem
        except:
            pass
    
    return closest_distance, closest_face, closest_elem


def evaluate_face_pair(doc, floor_face, ceiling_face, room_poly, sketch_plane, z_height, 
                       create_intersection_lines=False, debug=False):
    """Evaluate the 2D intersection of a floor face, ceiling face, and room boundary.
    
    Returns:
        Tuple of (intersection_area, min_height, max_height, lines_created)
    """
    poly1 = get_face_polygon(floor_face)
    poly2 = get_face_polygon(ceiling_face)

    if not poly1 or not poly2:
        return 0, None, None, 0

    intersection = room_poly.intersection(poly1).intersection(poly2)

    if intersection.is_empty:
        return 0, None, None, 0

    intersection_area = intersection.area
    
    if debug:
        room_area = room_poly.area
        if room_area > 0:
            area_pct = (intersection_area / room_area) * 100
            print(f"    Face pair intersection: {area_pct:.2f}% of room area")

    geoms = []
    if intersection.geom_type == 'Polygon':
        geoms = [intersection]
    elif intersection.geom_type == 'MultiPolygon':
        geoms = intersection.geoms
    else:
        return 0, None, None, 0

    evaluated_points = set()
    lines_created = 0

    if create_intersection_lines:
        for poly in geoms:
            coords = list(poly.exterior.coords)
            for i in range(len(coords) - 1):
                x1, y1 = coords[i][0], coords[i][1]
                x2, y2 = coords[i + 1][0], coords[i + 1][1]

                evaluated_points.add((x1, y1))

                p1 = XYZ(x1, y1, z_height)
                p2 = XYZ(x2, y2, z_height)

                if p1.DistanceTo(p2) > 0.005:
                    try:
                        line = Line.CreateBound(p1, p2)
                        doc.Create.NewModelCurve(line, sketch_plane)
                        lines_created += 1
                    except:
                        pass

            for hole in poly.interiors:
                h_coords = list(hole.coords)
                for i in range(len(h_coords) - 1):
                    x1, y1 = h_coords[i][0], h_coords[i][1]
                    x2, y2 = h_coords[i + 1][0], h_coords[i + 1][1]

                    evaluated_points.add((x1, y1))

                    p1 = XYZ(x1, y1, z_height)
                    p2 = XYZ(x2, y2, z_height)

                    if p1.DistanceTo(p2) > 0.005:
                        try:
                            line = Line.CreateBound(p1, p2)
                            doc.Create.NewModelCurve(line, sketch_plane)
                            lines_created += 1
                        except:
                            pass
    else:
        # Just collect points without drawing
        for poly in geoms:
            coords = list(poly.exterior.coords)
            for x, y in coords:
                evaluated_points.add((x, y))
            for hole in poly.interiors:
                h_coords = list(hole.coords)
                for x, y in h_coords:
                    evaluated_points.add((x, y))

    clear_heights = []
    for x, y in evaluated_points:
        z1 = get_z_on_planar_face(floor_face, x, y)
        z2 = get_z_on_planar_face(ceiling_face, x, y)

        if z1 is not None and z2 is not None:
            clear_height_ft = abs(z1 - z2)
            clear_height_m = clear_height_ft * 0.3048
            clear_heights.append(clear_height_m)

    if not clear_heights:
        return intersection_area, None, None, lines_created

    min_height = min(clear_heights)
    max_height = max(clear_heights)

    return intersection_area, min_height, max_height, lines_created


def create_debug_model_line(doc, start_point, end_point):
    """Create a model line for debugging ray paths."""
    try:
        line = Line.CreateBound(start_point, end_point)
        
        line_direction = (end_point - start_point).Normalize()
        
        if abs(line_direction.Z) > 0.9:
            plane_normal = XYZ(0, 1, 0)
        else:
            plane_normal = XYZ.BasisZ.CrossProduct(line_direction).Normalize()
        
        plane = Plane.CreateByNormalAndOrigin(plane_normal, start_point)
        sketch_plane = SketchPlane.Create(doc, plane)
        
        model_line = doc.Create.NewModelCurve(line, sketch_plane)
        return model_line
    except:
        return None


def format_clear_height_result(min_height, max_height):
    """Format clear heights as string with German locale.
    
    Returns single value if min == max, or range if different.
    Format: "2,431 m" or "2,431 m - 3,231 m"
    """
    if min_height is None:
        return ""
    
    if max_height is None or abs(min_height - max_height) < 0.001:
        return f"{min_height:.3f} m".replace(".", ",")
    else:
        return f"{min_height:.3f} m - {max_height:.3f} m".replace(".", ",")


@revit
def main(debug=False, create_ray_lines=False, create_intersection_lines=False, profile=False):
    """Calculate clear heights for selected rooms using face pair evaluation.
    
    Args:
        debug: Print debug information
        create_ray_lines: Create model lines for ray casting visualization
        create_intersection_lines: Create model lines for face intersection boundaries
        profile: Print timing information for performance analysis
    """
    import time
    
    doc = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument
    
    t_total_start = time.time()
    
    selection = uidoc.Selection.GetElementIds()
    if selection.Count == 0:
        print("No elements selected.")
        return
    
    view_3d = None
    collector = FilteredElementCollector(doc).OfClass(View3D)
    for view in collector:
        if view.Name == "RAY_CASTING_VIEW" and not view.IsTemplate:
            view_3d = view
            break
    
    if view_3d is None:
        print("3D view 'RAY_CASTING_VIEW' not found.")
        return
    
    categories = List[BuiltInCategory]()
    categories.Add(BuiltInCategory.OST_Floors)
    categories.Add(BuiltInCategory.OST_Ceilings)
    categories.Add(BuiltInCategory.OST_Roofs)
    category_filter = ElementMulticategoryFilter(categories)
    
    intersector = ReferenceIntersector(category_filter, FindReferenceTarget.Face, view_3d)
    
    boundary_options = SpatialElementBoundaryOptions()
    boundary_options.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish
    
    t = Transaction(doc, "Calculate Clear Heights")
    t.Start()
    
    try:
        for element_id in selection:
            room = doc.GetElement(element_id)
            
            if room.Category is None or room.Category.Id.Value != int(BuiltInCategory.OST_Rooms):
                continue
            
            if debug or profile:
                print(f"\n=== Processing Room {room.Id.Value} ===")
            
            t_room_start = time.time()
            
            # Get room parameters
            level = doc.GetElement(room.LevelId)
            level_elevation = level.Elevation if level else 0
            
            computation_height_param = level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT) if level else None
            computation_height = computation_height_param.AsDouble() if computation_height_param else 0
            
            small_offset = 0.01
            z_height = level_elevation + computation_height + small_offset
            
            room_area_param = room.get_Parameter(BuiltInParameter.ROOM_AREA)
            room_area = room_area_param.AsDouble() if room_area_param else 0
            grid_spacing = calculate_adaptive_grid_spacing(room_area)
            
            unbounded_height_param = room.get_Parameter(BuiltInParameter.ROOM_HEIGHT)
            unbounded_height = unbounded_height_param.AsDouble() if unbounded_height_param else 10
            max_ray_distance = unbounded_height * 1.5
            
            if debug:
                print(f"  Room area: {room_area * 0.0929:.1f}m²")
                print(f"  Grid spacing: {grid_spacing * 0.3048:.2f}m")
                print(f"  Z height: {z_height * 0.3048:.2f}m")
            
            # Get room boundary and polygon
            boundary_loops = get_room_boundary_points(room, boundary_options)
            if not boundary_loops:
                print(f"  Room {room.Id.Value}: No boundary found")
                continue
            
            room_poly = get_room_polygon(room)
            if not room_poly:
                print(f"  Room {room.Id.Value}: Could not create room polygon")
                continue
            
            room_area_2d = room_poly.area
            
            # Create grid points
            t_grid_start = time.time()
            grid_points = create_grid_points_bounding_box(
                boundary_loops, grid_spacing, BOUNDARY_INSET, z_height, profile=profile
            )
            t_grid_end = time.time()
            
            if not grid_points:
                print(f"  Room {room.Id.Value}: No grid points generated")
                continue
            
            if debug or profile:
                print(f"  Generated {len(grid_points)} grid points")
            if profile:
                print(f"  [PROFILE] Grid generation: {t_grid_end - t_grid_start:.3f}s")
            
            # Cast rays and collect face pairs
            t_ray_start = time.time()
            
            up_direction = XYZ(0, 0, 1)
            down_direction = XYZ(0, 0, -1)
            
            face_pairs = {}  # Cache: (floor_face_id, ceiling_face_id) -> (floor_face, ceiling_face, floor_elem, ceiling_elem)
            ray_lines_created = 0
            
            for point in grid_points:
                # Cast ray upward
                up_refs = intersector.Find(point, up_direction)
                
                # Find ceiling (downward-facing normal)
                ceiling_dist, ceiling_face, ceiling_elem = find_face_from_ray_hit(
                    doc, up_refs, max_ray_distance, require_upward_normal=False, debug=False
                )
                
                # Find floor above (upward-facing normal)
                floor_top_dist, floor_top_face, floor_top_elem = find_face_from_ray_hit(
                    doc, up_refs, max_ray_distance, require_upward_normal=True, debug=False
                )
                
                # Cast ray downward
                down_refs = intersector.Find(point, down_direction)
                
                # Find floor below (upward-facing normal)
                floor_below_dist, floor_below_face, floor_below_elem = find_face_from_ray_hit(
                    doc, down_refs, max_ray_distance, require_upward_normal=True, debug=False
                )
                
                # Determine which floor to use
                floor_face = None
                floor_elem = None
                floor_point = None
                
                if floor_top_dist is not None and ceiling_dist is not None and floor_top_dist < ceiling_dist:
                    floor_face = floor_top_face
                    floor_elem = floor_top_elem
                    floor_point = XYZ(point.X, point.Y, point.Z + floor_top_dist)
                elif floor_below_dist is not None:
                    floor_face = floor_below_face
                    floor_elem = floor_below_elem
                    floor_point = XYZ(point.X, point.Y, point.Z - floor_below_dist)
                
                # Create ray debug lines if requested
                if create_ray_lines and floor_point and ceiling_dist is not None:
                    ceiling_point = XYZ(point.X, point.Y, point.Z + ceiling_dist)
                    if create_debug_model_line(doc, floor_point, ceiling_point):
                        ray_lines_created += 1
                
                # Cache face pair using face identifiers instead of element IDs
                # This ensures different faces from the same element are cached separately
                if floor_face and ceiling_face:
                    floor_face_id = get_face_identifier(floor_face)
                    ceiling_face_id = get_face_identifier(ceiling_face)
                    pair_key = (floor_face_id, ceiling_face_id)
                    
                    if pair_key not in face_pairs:
                        face_pairs[pair_key] = (floor_face, ceiling_face, floor_elem, ceiling_elem)
            
            t_ray_end = time.time()
            
            if debug or profile:
                print(f"  Found {len(face_pairs)} unique face pairs")
            if profile:
                print(f"  [PROFILE] Ray casting and face collection: {t_ray_end - t_ray_start:.3f}s")
            if create_ray_lines and ray_lines_created > 0:
                print(f"  Created {ray_lines_created} ray debug lines")
            
            # Create sketch plane for intersection lines if needed
            sketch_plane = None
            if create_intersection_lines:
                try:
                    plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ(0, 0, z_height))
                    sketch_plane = SketchPlane.Create(doc, plane)
                except:
                    print("  Warning: Could not create sketch plane for intersection lines")
                    create_intersection_lines = False
            
            # Evaluate face pairs
            t_eval_start = time.time()
            
            valid_min_heights = []
            valid_max_heights = []
            total_intersection_lines = 0
            
            for pair_key, (floor_face, ceiling_face, floor_elem, ceiling_elem) in face_pairs.items():
                intersection_area, min_h, max_h, lines_created = evaluate_face_pair(
                    doc, floor_face, ceiling_face, room_poly, sketch_plane, z_height,
                    create_intersection_lines=create_intersection_lines, debug=debug
                )
                
                total_intersection_lines += lines_created
                
                if min_h is None or max_h is None:
                    continue
                
                # Filter by area threshold
                area_ratio = intersection_area / room_area_2d if room_area_2d > 0 else 0
                
                if area_ratio >= THRESHOLD:
                    valid_min_heights.append(min_h)
                    valid_max_heights.append(max_h)
                    if debug:
                        print(f"    Accepted pair (Floor {floor_elem.Id.Value}, Ceiling {ceiling_elem.Id.Value}): "
                              f"area={area_ratio*100:.1f}%, min={min_h:.3f}m, max={max_h:.3f}m")
                elif debug:
                    print(f"    Rejected pair (Floor {floor_elem.Id.Value}, Ceiling {ceiling_elem.Id.Value}): "
                          f"area={area_ratio*100:.1f}% < threshold {THRESHOLD*100:.1f}%")
            
            t_eval_end = time.time()
            
            if profile:
                print(f"  [PROFILE] Face pair evaluation: {t_eval_end - t_eval_start:.3f}s")
            if create_intersection_lines and total_intersection_lines > 0:
                print(f"  Created {total_intersection_lines} intersection boundary lines")
            
            # Calculate final result
            if not valid_min_heights or not valid_max_heights:
                print(f"  Room {room.Id.Value}: No valid face pairs found")
                continue
            
            final_min = min(valid_min_heights)
            final_max = max(valid_max_heights)
            
            result = format_clear_height_result(final_min, final_max)
            
            # Write to parameter
            param = room.LookupParameter("Lichte_Raumhöhe")
            if param:
                param.Set(result)
                print(f"  Room {room.Id.Value}: {result}")
            else:
                print(f"  Room {room.Id.Value}: Parameter 'Lichte_Raumhöhe' not found")
            
            if profile:
                t_room_end = time.time()
                print(f"  [PROFILE] Total room processing: {t_room_end - t_room_start:.3f}s")
        
        t.Commit()
        
        if profile:
            t_total_end = time.time()
            print(f"\n[PROFILE] Total execution time: {t_total_end - t_total_start:.3f}s")
        
        print("\nCommand executed successfully.")
    except Exception as e:
        t.RollBack()
        print(f"Error during execution: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
