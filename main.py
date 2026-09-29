from revit_rpc import revit
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import Point as ShapelyPoint
from shapely.validation import make_valid

try:
    from Autodesk.Revit.DB import (
        Transaction, XYZ, Line, Plane, SketchPlane, BuiltInCategory,
        BuiltInParameter, SpatialElementBoundaryOptions,
        SpatialElementBoundaryLocation, PlanarFace, View3D, FilteredElementCollector,
        ReferenceIntersector, FindReferenceTarget, ElementMulticategoryFilter,
        ParameterFilterElement, ElementId
    )
    from System.Collections.Generic import List
except ImportError:
    pass

__revit__: str


def get_3d_view_by_name(doc, view_name):
    """Get a 3D view by its name."""
    collector = FilteredElementCollector(doc).OfClass(View3D)
    for view in collector:
        if view.Name == view_name and not view.IsTemplate:
            return view
    return None


def get_closest_valid_ref(doc, refs, max_distance, require_upward_normal):
    """Find the closest hit on a room-bounding element with correct face normal and return its Reference."""
    closest_dist = None
    closest_ref = None

    for ref_with_context in refs:
        ref = ref_with_context.GetReference()
        if not ref:
            continue

        proximity = ref_with_context.Proximity
        if closest_dist is not None and proximity >= closest_dist:
            continue
        if proximity > max_distance:
            continue

        elem = doc.GetElement(ref.ElementId)
        if not elem:
            continue

        param = elem.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
        if not param or param.AsInteger() != 1:
            continue

        try:
            face = elem.GetGeometryObjectFromReference(ref)
            if face:
                uv = ref.UVPoint
                normal = face.ComputeNormal(uv) if uv else face.ComputeNormal(face.GetBoundingBox().Min)

                if require_upward_normal and normal.Z <= 0:
                    continue
                if not require_upward_normal and normal.Z >= 0:
                    continue
        except:
            pass

        closest_dist = proximity
        closest_ref = ref

    return closest_ref


def auto_find_room_faces(doc, room, z_height, room_poly, grid_spacing, create_ray_lines=False, debug=False, ray_length_multiplicator=1.5, ray_casting_view_name="RAY_CASTING_VIEW"):
    """Automatically find floor and ceiling faces for a given room using ray casting."""
    view_3d = get_3d_view_by_name(doc, ray_casting_view_name)
    if not view_3d:
        if debug:
            print(f"3D view '{ray_casting_view_name}' not found.")
        return [], []

    categories = List[BuiltInCategory]()
    categories.Add(BuiltInCategory.OST_Floors)
    categories.Add(BuiltInCategory.OST_Ceilings)
    categories.Add(BuiltInCategory.OST_Roofs)
    cat_filter = ElementMulticategoryFilter(categories)
    intersector = ReferenceIntersector(cat_filter, FindReferenceTarget.Face, view_3d)

    unbounded_height_param = room.get_Parameter(BuiltInParameter.ROOM_HEIGHT)
    unbounded_height = unbounded_height_param.AsDouble() if unbounded_height_param else 10
    max_ray_distance = unbounded_height * ray_length_multiplicator

    minx, miny, maxx, maxy = room_poly.bounds
    grid_points = []

    x = minx + grid_spacing / 2
    while x < maxx:
        y = miny + grid_spacing / 2
        while y < maxy:
            if room_poly.contains(ShapelyPoint(x, y)):
                grid_points.append(XYZ(x, y, z_height))
            y += grid_spacing
        x += grid_spacing

    if debug:
        print(f"Casting rays from {len(grid_points)} points to find floor and ceiling faces...")

    floor_refs = []
    ceiling_refs = []
    seen_refs = set()

    for pt in grid_points:
        up_refs = intersector.Find(pt, XYZ(0, 0, 1))
        down_refs = intersector.Find(pt, XYZ(0, 0, -1))

        # Ceiling (must have downward-facing normal)
        c_ref = get_closest_valid_ref(doc, up_refs, max_ray_distance, require_upward_normal=False)
        c_pt = None
        if c_ref:
            rep = c_ref.ConvertToStableRepresentation(doc)
            if rep not in seen_refs:
                seen_refs.add(rep)
                ceiling_refs.append(c_ref)

            # If ray lines are requested, try to find the actual intersection point
            if create_ray_lines:
                for hit in up_refs:
                    if hit.GetReference().ConvertToStableRepresentation(doc) == rep:
                        c_pt = XYZ(pt.X, pt.Y, pt.Z + hit.Proximity)
                        break

        # Floor below (must have upward-facing normal)
        f_ref = get_closest_valid_ref(doc, down_refs, max_ray_distance, require_upward_normal=True)
        f_pt = None
        if f_ref:
            rep = f_ref.ConvertToStableRepresentation(doc)
            if rep not in seen_refs:
                seen_refs.add(rep)
                floor_refs.append(f_ref)

            # If ray lines are requested, try to find the actual intersection point
            if create_ray_lines:
                for hit in down_refs:
                    if hit.GetReference().ConvertToStableRepresentation(doc) == rep:
                        f_pt = XYZ(pt.X, pt.Y, pt.Z - hit.Proximity)
                        break

        # Draw vertical lines showing the raycast if requested
        if create_ray_lines and c_pt and f_pt:
            try:
                line = Line.CreateBound(f_pt, c_pt)
                line_direction = (c_pt - f_pt).Normalize()
                if abs(line_direction.Z) > 0.9:
                    plane_normal = XYZ(0, 1, 0)
                else:
                    plane_normal = XYZ.BasisZ.CrossProduct(line_direction).Normalize()

                plane = Plane.CreateByNormalAndOrigin(plane_normal, f_pt)
                sketch_plane = SketchPlane.Create(doc, plane)
                doc.Create.NewModelCurve(line, sketch_plane)
            except Exception as e:
                if debug:
                    print(f"Error drawing ray line: {e}")

    return floor_refs, ceiling_refs


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
            # Check if start and end points match
            if points[0] != points[-1]:
                points.append(points[0])

            poly = ShapelyPolygon(points)
            if not poly.is_valid:
                poly = make_valid(poly)
            polys.append(poly)

    if not polys:
        return None

    # Separate into outer boundaries and holes.
    # A polygon is a hole if it is completely contained within another polygon.
    outer_polys = []
    holes = []

    # Sort by area descending so we can easily check containment (holes are smaller than outers)
    polys.sort(key=lambda p: p.area, reverse=True)

    for p in polys:
        is_hole = False
        for outer in outer_polys:
            if outer.contains(p) or outer.covers(p):
                holes.append(p)
                is_hole = True
                break
        if not is_hole:
            outer_polys.append(p)

    # Perform differences for each outer poly with its containing holes
    final_outers = []
    for outer in outer_polys:
        current_outer = outer
        for hole in holes:
            if current_outer.contains(hole) or current_outer.covers(hole):
                current_outer = current_outer.difference(hole)
        final_outers.append(current_outer)

    # Union all the final outer polygons to create a MultiPolygon or Polygon
    from shapely.ops import unary_union
    combined_poly = unary_union(final_outers)

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

    # Separate into outer boundaries and holes.
    # A polygon is a hole if it is completely contained within another polygon.
    outer_polys = []
    holes = []

    # Sort by area descending so we can easily check containment (holes are smaller than outers)
    polys.sort(key=lambda p: p.area, reverse=True)

    for p in polys:
        is_hole = False
        for outer in outer_polys:
            if outer.contains(p) or outer.covers(p):
                holes.append(p)
                is_hole = True
                break
        if not is_hole:
            outer_polys.append(p)

    # Perform differences for each outer poly with its containing holes
    final_outers = []
    for outer in outer_polys:
        current_outer = outer
        for hole in holes:
            if current_outer.contains(hole) or current_outer.covers(hole):
                current_outer = current_outer.difference(hole)
        final_outers.append(current_outer)

    # Union all the final outer polygons to create a MultiPolygon or Polygon
    from shapely.ops import unary_union
    combined_poly = unary_union(final_outers)

    if create_model_lines and doc and sketch_plane and combined_poly:
        draw_shapely_polygon(combined_poly, doc, sketch_plane, z_height)

    return combined_poly


def get_z_on_planar_face(face, x, y):
    """Evaluate the exact Z height of a PlanarFace at a given (X, Y) coordinate."""
    if isinstance(face, PlanarFace) or (hasattr(face, "FaceNormal") and hasattr(face, "Origin")):
        n = face.FaceNormal
        o = face.Origin

        # If the normal's Z is zero, the face is perfectly vertical (cannot evaluate a single Z)
        if abs(n.Z) < 1e-6:
            return None

        # Plane equation: nx(X - ox) + ny(Y - oy) + nz(Z - oz) = 0
        # Solving for Z: Z = oz - (nx(X - ox) + ny(Y - oy)) / nz
        z = o.Z - (n.X * (x - o.X) + n.Y * (y - o.Y)) / n.Z
        return z

    return None


def draw_shapely_polygon(poly, doc, sketch_plane, z_height):
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
    if not base_poly:
        return None
    if not subtract_poly:
        result = base_poly
    else:
        result = base_poly.difference(subtract_poly)
        
    if create_model_lines and doc and sketch_plane and result:
        draw_shapely_polygon(result, doc, sketch_plane, z_height)
        
    return result


def get_intersection_polygon(polys, doc=None, sketch_plane=None, z_height=0.0, create_model_lines=False):
    """Returns the intersection of a list of polygons."""
    if not polys:
        return None
    result = polys[0]
    for p in polys[1:]:
        if not p or p.is_empty:
            return None
        result = result.intersection(p)
        
    if create_model_lines and doc and sketch_plane and result:
        draw_shapely_polygon(result, doc, sketch_plane, z_height)
        
    return result


def get_room_face_pairs(doc, room, z_height, room_poly, grid_spacing, create_ray_lines=False, debug=False, ray_length_multiplicator=1.5, ray_casting_view_name="RAY_CASTING_VIEW"):
    """
    Runs auto face detection and then pairs them together.
    Returns (floor_refs, ceiling_refs, pairs), where pairs is a list of (floor_data, ceiling_data) dictionaries.
    """
    floor_refs, ceiling_refs = auto_find_room_faces(doc, room, z_height, room_poly, grid_spacing,
                                                    create_ray_lines=create_ray_lines, debug=debug,
                                                    ray_length_multiplicator=ray_length_multiplicator,
                                                    ray_casting_view_name=ray_casting_view_name)
    
    if not floor_refs or not ceiling_refs:
        return floor_refs, ceiling_refs, []

    # Pre-process floors: sort by Z descending (highest first) and subtract upper from lower
    floors_data = []
    for ref in floor_refs:
        face = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
        poly = get_face_polygon(face)
        if poly:
            z = face.Origin.Z if hasattr(face, "Origin") else 0
            floors_data.append({'face': face, 'poly': poly, 'z': z, 'element_id': ref.ElementId.Value, 'ref': ref})

    floors_data.sort(key=lambda x: x['z'], reverse=True)
    accumulated_floors = None
    for data in floors_data:
        if accumulated_floors is not None:
            data['poly'] = get_subtracted_polygon(data['poly'], accumulated_floors)
            if data['poly'] and not data['poly'].is_empty:
                accumulated_floors = accumulated_floors.union(data['poly'])
        else:
            if data['poly'] and not data['poly'].is_empty:
                accumulated_floors = data['poly']

    # Pre-process ceilings: sort by Z ascending (lowest first) and subtract lower from upper
    ceilings_data = []
    for ref in ceiling_refs:
        face = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
        poly = get_face_polygon(face)
        if poly:
            z = face.Origin.Z if hasattr(face, "Origin") else 0
            ceilings_data.append({'face': face, 'poly': poly, 'z': z, 'element_id': ref.ElementId.Value, 'ref': ref})

    ceilings_data.sort(key=lambda x: x['z'])
    accumulated_ceilings = None
    for data in ceilings_data:
        if accumulated_ceilings is not None:
            data['poly'] = get_subtracted_polygon(data['poly'], accumulated_ceilings)
            if data['poly'] and not data['poly'].is_empty:
                accumulated_ceilings = accumulated_ceilings.union(data['poly'])
        else:
            if data['poly'] and not data['poly'].is_empty:
                accumulated_ceilings = data['poly']

    pairs = []
    for floor_data in floors_data:
        for ceiling_data in ceilings_data:
            if floor_data['element_id'] == ceiling_data['element_id']:
                continue
            pairs.append((floor_data, ceiling_data))
            
    return floor_refs, ceiling_refs, pairs


def evaluate_face_pair(doc, floor_face, floor_poly, ceiling_face, ceiling_poly, room_poly, sketch_plane, z_height,
                       create_debug_lines=False, debug=False):
    """
    Evaluates the 2D intersection of a floor face, ceiling face, and the room boundary.
    Draws the intersection and evaluates clear heights at the vertices.
    """
    if not floor_poly or not ceiling_poly or floor_poly.is_empty or ceiling_poly.is_empty:
        return 0, [], 0.0

    # Perform the 3-way boolean intersection (Room ∩ Floor ∩ Ceiling)
    intersection = get_intersection_polygon([room_poly, floor_poly, ceiling_poly])

    if not intersection or intersection.is_empty:
        return 0, [], 0.0

    # Calculate the area percentage
    room_area = room_poly.area
    intersection_area = intersection.area
    area_pct = 0.0
    if room_area > 0:
        area_pct = (intersection_area / room_area) * 100

    geoms = []
    if intersection.geom_type == 'Polygon':
        geoms = [intersection]
    elif intersection.geom_type == 'MultiPolygon':
        geoms = intersection.geoms
    elif intersection.geom_type == 'GeometryCollection':
        geoms = [g for g in intersection.geoms if g.geom_type in ('Polygon', 'MultiPolygon')]
    else:
        return 0, [], area_pct

    evaluated_points = set()
    lines_created = 0

    for poly in geoms:
        # Draw the exterior boundary
        coords = list(poly.exterior.coords)
        for i in range(len(coords) - 1):
            x1, y1 = coords[i][0], coords[i][1]
            x2, y2 = coords[i + 1][0], coords[i + 1][1]

            evaluated_points.add((x1, y1))

            p1 = XYZ(x1, y1, z_height)
            p2 = XYZ(x2, y2, z_height)

            if create_debug_lines and p1.DistanceTo(p2) > 0.005:
                line = Line.CreateBound(p1, p2)
                doc.Create.NewModelCurve(line, sketch_plane)
                lines_created += 1

        # Draw the holes (if any)
        for hole in poly.interiors:
            h_coords = list(hole.coords)
            for i in range(len(h_coords) - 1):
                x1, y1 = h_coords[i][0], h_coords[i][1]
                x2, y2 = h_coords[i + 1][0], h_coords[i + 1][1]

                evaluated_points.add((x1, y1))

                p1 = XYZ(x1, y1, z_height)
                p2 = XYZ(x2, y2, z_height)

                if create_debug_lines and p1.DistanceTo(p2) > 0.005:
                    line = Line.CreateBound(p1, p2)
                    doc.Create.NewModelCurve(line, sketch_plane)
                    lines_created += 1

    clear_heights = []
    # Evaluate clear heights for this pair at the intersection vertices
    for x, y in evaluated_points:
        z1 = get_z_on_planar_face(floor_face, x, y)
        z2 = get_z_on_planar_face(ceiling_face, x, y)

        if z1 is not None and z2 is not None:
            clear_height_ft = abs(z1 - z2)
            clear_height_m = clear_height_ft * 0.3048
            clear_heights.append(clear_height_m)

    # Prepare the height string for the output
    if clear_heights:
        min_h = min(clear_heights)
        max_h = max(clear_heights)
        if abs(max_h - min_h) > 0.005:
            height_str = f"{min_h:.3f}m - {max_h:.3f}m"
        else:
            height_str = f"{min_h:.3f}m"
    else:
        height_str = "None"

    if debug and room_area > 0:
        intersection_area_m2 = intersection_area * 0.09290304
        print(f"-> Found intersection overlapping {area_pct:.2f}% of the room area (Intersection Area: {intersection_area_m2:.2f} m²). Clear height(s): {height_str}")

    # Return the evaluated heights with their overlap info (without area threshold filtering)
    return lines_created, clear_heights, area_pct


def evaluate_room_clear_heights(doc, room, z_height, room_poly, grid_spacing, create_ray_lines=False, create_debug_lines=False, debug=False, sketch_plane=None, element_filter=None, ray_length_multiplicator=1.5, ray_casting_view_name="RAY_CASTING_VIEW"):
    """
    Evaluates all clear heights for a room by finding faces, pairing them, and evaluating each pair.
    Returns (total_lines_created, results), where results is a list of dicts: {'heights': [...], 'area_pct': float, 'is_ignored': bool}.
    """
    floor_refs, ceiling_refs, pairs = get_room_face_pairs(doc, room, z_height, room_poly, grid_spacing,
                                                          create_ray_lines=create_ray_lines, debug=debug,
                                                          ray_length_multiplicator=ray_length_multiplicator,
                                                          ray_casting_view_name=ray_casting_view_name)
    
    if not floor_refs:
        if debug:
            print("No floor faces found.")
        return 0, []

    if not ceiling_refs:
        if debug:
            print("No ceiling/roof faces found.")
        return 0, []

    total_lines_created = 0
    results = []

    # Iterate through every combination of (floor face) and (ceiling face)
    for floor_data, ceiling_data in pairs:
        lines_created, heights, area_pct = evaluate_face_pair(
            doc,
            floor_data['face'], floor_data['poly'],
            ceiling_data['face'], ceiling_data['poly'],
            room_poly, sketch_plane, z_height,
            create_debug_lines=create_debug_lines,
            debug=debug
        )

        is_ignored = False
        if element_filter:
            f_id = ElementId(floor_data['element_id'])
            c_id = ElementId(ceiling_data['element_id'])
            if element_filter.PassesFilter(doc, f_id) or element_filter.PassesFilter(doc, c_id):
                is_ignored = True
                if debug and area_pct > 0:
                    print(f"   -> This pair is IGNORED due to filter match.")

        total_lines_created += lines_created
        if heights:
            results.append({'heights': heights, 'area_pct': area_pct, 'is_ignored': is_ignored})
            
    return total_lines_created, results


def round_to_half_cm(value):
    """Rounds the value to the nearest 0.005 (0.5 cm)."""
    return round(value * 200) / 200


@revit
def main(debug=False, create_debug_lines=False, create_ray_lines=False, grid_spacing_min_m=0.5, grid_spacing_max_m=1.5,
         area_min_m2=10.0, area_max_m2=100.0, area_threshold_pct=5.0, filter_name=None, area_threshold_m2=10,
         dominant_area_pct_threshold=50.0, dominant_n_times=2.0, ray_length_multiplicator=1.5,
         clear_height_param_name="Lichte_Raumhöhe", dominant_clear_height_param_name="Lichte_Raumhöhe_Bestimmend",
         ray_casting_view_name="RAY_CASTING_VIEW"):
    doc = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument

    # 1. Get all selected rooms
    selection = uidoc.Selection.GetElementIds()
    rooms = []
    for eid in selection:
        elem = doc.GetElement(eid)
        if elem and elem.Category and elem.Category.Id.Value == int(BuiltInCategory.OST_Rooms):
            rooms.append(elem)

    if not rooms:
        if debug:
            print("Please select at least one Room before running the script.")
        return

    # 4.5 Find FilterElement if filter_name is provided
    element_filter = None
    if filter_name:
        filter_collector = FilteredElementCollector(doc).OfClass(ParameterFilterElement)
        for f in filter_collector:
            if f.Name == filter_name:
                element_filter = f.GetElementFilter()
                if debug:
                    print(f"Found filter '{filter_name}'.")
                break
        if not element_filter and debug:
            print(f"Warning: Filter '{filter_name}' not found.")

    # 5. Start transaction and autoselect faces
    t = Transaction(doc, "Calculate Clear Heights")
    t.Start()

    try:
        for room in rooms:
            if debug:
                room_number_param = room.get_Parameter(BuiltInParameter.ROOM_NUMBER)
                room_name_param = room.get_Parameter(BuiltInParameter.ROOM_NAME)
                room_number = room_number_param.AsString() if room_number_param else "Unknown"
                room_name = room_name_param.AsString() if room_name_param else "Unknown"
                print(f"\n--- Processing Room: {room_name} ({room_number}) ---")

            # 2. Calculate the target Z height (Level Elevation + Computation Height)
            level = doc.GetElement(room.LevelId)
            level_elevation = level.Elevation if level else 0

            computation_height_param = level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT)
            computation_height = computation_height_param.AsDouble() if computation_height_param else 0

            small_offset = 0.01  # ~3mm above floor level
            z_height = level_elevation + computation_height + small_offset

            # 3. Extract room polygon
            room_poly = get_room_polygon(room)
            if not room_poly:
                if debug:
                    print("Could not extract valid boundaries from the room.")
                continue

            # 4. Determine dynamic grid spacing
            room_area_param = room.get_Parameter(BuiltInParameter.ROOM_AREA)
            room_area_sqft = room_area_param.AsDouble() if room_area_param else 0
            room_area_m2 = room_area_sqft * 0.092903

            if room_area_m2 <= area_min_m2:
                grid_spacing_m = grid_spacing_min_m
            elif room_area_m2 >= area_max_m2:
                grid_spacing_m = grid_spacing_max_m
            else:
                t_val = (room_area_m2 - area_min_m2) / (area_max_m2 - area_min_m2)
                grid_spacing_m = grid_spacing_min_m + t_val * (grid_spacing_max_m - grid_spacing_min_m)

            grid_spacing = grid_spacing_m * 3.28084  # convert to internal units (feet)

            if debug:
                print(f"Room area: {room_area_m2:.1f}m2 -> Adaptive Grid Spacing: {grid_spacing_m:.2f}m")

            # 6. Evaluate intersections and draw boundary overlaps
            # Create a flat SketchPlane at the exact calculated z_height
            origin = XYZ(0, 0, z_height)
            plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, origin)
            sketch_plane = SketchPlane.Create(doc, plane)

            total_lines_created, evaluation_results = evaluate_room_clear_heights(
                doc, room, z_height, room_poly, grid_spacing,
                create_ray_lines=create_ray_lines,
                create_debug_lines=create_debug_lines,
                debug=debug,
                sketch_plane=sketch_plane,
                element_filter=element_filter,
                ray_length_multiplicator=ray_length_multiplicator,
                ray_casting_view_name=ray_casting_view_name
            )

            if debug and total_lines_created > 0:
                print(
                    f"Success! Created {total_lines_created} model lines at Z={z_height * 0.3048:.3f}m showing the overlaps.")

            # 7. Aggregate areas by height group and apply threshold filter
            height_areas = {}
            filtered_out_pct = 0.0
            filtered_out_area = 0.0

            for result in evaluation_results:
                area_pct = result['area_pct']
                heights = result['heights']
                is_ignored = result.get('is_ignored', False)
                
                if heights:
                    if is_ignored:
                        filtered_out_pct += area_pct
                        filtered_out_area += (area_pct / 100) * room_area_m2
                        continue

                    min_h = round_to_half_cm(min(heights))
                    max_h = round_to_half_cm(max(heights))
                    group_key = (min_h, max_h)
                    
                    if group_key not in height_areas:
                        height_areas[group_key] = {'area_pct': 0.0, 'heights': []}
                    
                    height_areas[group_key]['area_pct'] += area_pct
                    height_areas[group_key]['heights'].extend(heights)

            all_clear_heights = []
            
            total_included_pct = 0.0
            total_included_area = 0.0
            total_excluded_pct = 0.0
            total_excluded_area = 0.0
            
            dominant_height_result = None

            if debug and (height_areas or filtered_out_pct > 0):
                print("\n--- Clear Height Areas Summary ---")

            for (min_h, max_h), data in sorted(height_areas.items()):
                pct = data['area_pct']
                area_m2 = (pct / 100) * room_area_m2
                
                if min_h == max_h:
                    key_str = f"{min_h:.3f} m"
                else:
                    key_str = f"{min_h:.3f} m - {max_h:.3f} m"
                
                if pct >= dominant_area_pct_threshold:
                    other_pcts = [d['area_pct'] for k, d in height_areas.items() if k != (min_h, max_h)]
                    if not other_pcts or all(pct >= p * dominant_n_times for p in other_pcts):
                        rounded_pct = int(round(pct / 5.0)) * 5
                        dominant_height_result = f"{key_str.replace('.', ',')} (~{rounded_pct}%)"
                
                meets_pct_threshold = pct >= area_threshold_pct
                meets_area_threshold = (area_threshold_m2 is not None and area_m2 >= area_threshold_m2)
                
                if meets_pct_threshold or meets_area_threshold:
                    all_clear_heights.extend(data['heights'])
                    total_included_pct += pct
                    total_included_area += area_m2
                    if debug:
                        if meets_pct_threshold:
                            print(f"Clear height {key_str}: {area_m2:.2f} m² ({pct:.2f}%) -> INCLUDED (>= {area_threshold_pct}%)")
                        else:
                            print(f"Clear height {key_str}: {area_m2:.2f} m² ({pct:.2f}%) -> INCLUDED (>= {area_threshold_m2} m²)")
                else:
                    total_excluded_pct += pct
                    total_excluded_area += area_m2
                    if debug:
                        reason = f"< {area_threshold_pct}%"
                        if area_threshold_m2 is not None:
                            reason += f" and < {area_threshold_m2} m²"
                        print(f"Clear height {key_str}: {area_m2:.2f} m² ({pct:.2f}%) -> EXCLUDED ({reason})")

            if debug and (height_areas or filtered_out_pct > 0):
                total_sum_pct = total_included_pct + total_excluded_pct + filtered_out_pct
                total_sum_area = total_included_area + total_excluded_area + filtered_out_area
                print(f"---")
                print(f"Included: {total_included_area:.2f} m² ({total_included_pct:.2f}%)")
                excl_reason = f"< {area_threshold_pct}%"
                if area_threshold_m2 is not None:
                    excl_reason += f" and < {area_threshold_m2} m²"
                print(f"Excluded ({excl_reason}): {total_excluded_area:.2f} m² ({total_excluded_pct:.2f}%)")
                if filtered_out_pct > 0:
                    print(f"Filtered (Ignored): {filtered_out_area:.2f} m² ({filtered_out_pct:.2f}%)")
                print(f"Sum:      {total_sum_area:.2f} m² ({total_sum_pct:.2f}%)")
                print(f"Room Area:{room_area_m2:.2f} m²")

            if all_clear_heights:
                min_ch = round_to_half_cm(min(all_clear_heights))
                max_ch = round_to_half_cm(max(all_clear_heights))

                is_range = abs(max_ch - min_ch) >= 0.005

                # Format the result with German locale (comma instead of decimal point)
                if not is_range:
                    result = f"{min_ch:.3f} m".replace(".", ",")
                else:
                    result = f"{min_ch:.3f} m - {max_ch:.3f} m".replace(".", ",")

                if debug:
                    print("\n--- Clear Heights Summary ---")
                    print(f"Evaluated {len(all_clear_heights)} valid points across all intersecting floor-ceiling pairs.")
                    print(f"Resulting Clear Height: {result}")
                    if is_range and dominant_height_result:
                        print(f"Dominant Clear Height (>= {dominant_area_pct_threshold}% and >= {dominant_n_times}x others): {dominant_height_result}")

                if clear_height_param_name:
                    param = room.LookupParameter(clear_height_param_name)
                    if param:
                        param.Set(result)
                        if debug:
                            print(f"Successfully updated parameter '{clear_height_param_name}'.")
                    else:
                        if debug:
                            print(f"Parameter '{clear_height_param_name}' not found.")
                        
                if dominant_clear_height_param_name:
                    param_dom = room.LookupParameter(dominant_clear_height_param_name)
                    if param_dom:
                        if is_range and dominant_height_result:
                            param_dom.Set(dominant_height_result)
                            if debug:
                                print(f"Successfully updated parameter '{dominant_clear_height_param_name}'.")
                        else:
                            param_dom.Set("")
                            if debug:
                                print(f"No dominant clear height or single height room. Cleared parameter '{dominant_clear_height_param_name}'.")
                    else:
                        if debug:
                            print(f"Parameter '{dominant_clear_height_param_name}' not found.")
            else:
                if debug:
                    print("\nNo valid clear heights could be evaluated at the intersection vertices.")

        t.Commit()

    except Exception as e:
        t.RollBack()
        if debug:
            print(f"Error processing intersections: {e}")


if __name__ == "__main__":
    main(debug=True,filter_name="# IGNORE FOR CLEAR HEIGHT")
