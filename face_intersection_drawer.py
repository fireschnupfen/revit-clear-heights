from revit_rpc import revit
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import Point as ShapelyPoint
from shapely.validation import make_valid

try:
    from Autodesk.Revit.DB import (
        Transaction, XYZ, Line, Plane, SketchPlane, BuiltInCategory,
        BuiltInParameter, SpatialElementBoundaryOptions,
        SpatialElementBoundaryLocation, PlanarFace, View3D, FilteredElementCollector,
        ReferenceIntersector, FindReferenceTarget, ElementMulticategoryFilter
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


def auto_find_room_faces(doc, room, z_height, room_poly):
    """Automatically find floor and ceiling faces for a given room using ray casting."""
    view_3d = get_3d_view_by_name(doc, "RAY_CASTING_VIEW")
    if not view_3d:
        print("3D view 'RAY_CASTING_VIEW' not found.")
        return [], []

    categories = List[BuiltInCategory]()
    categories.Add(BuiltInCategory.OST_Floors)
    categories.Add(BuiltInCategory.OST_Ceilings)
    categories.Add(BuiltInCategory.OST_Roofs)
    cat_filter = ElementMulticategoryFilter(categories)
    intersector = ReferenceIntersector(cat_filter, FindReferenceTarget.Face, view_3d)

    unbounded_height_param = room.get_Parameter(BuiltInParameter.ROOM_HEIGHT)
    unbounded_height = unbounded_height_param.AsDouble() if unbounded_height_param else 10
    max_ray_distance = unbounded_height * 1.5

    grid_spacing = 1.5  # ~0.5m grid
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

    print(f"Casting rays from {len(grid_points)} points to find floor and ceiling faces...")

    floor_refs = []
    ceiling_refs = []
    seen_refs = set()

    for pt in grid_points:
        up_refs = intersector.Find(pt, XYZ(0, 0, 1))
        down_refs = intersector.Find(pt, XYZ(0, 0, -1))

        # Ceiling (must have downward-facing normal)
        c_ref = get_closest_valid_ref(doc, up_refs, max_ray_distance, require_upward_normal=False)
        if c_ref:
            rep = c_ref.ConvertToStableRepresentation(doc)
            if rep not in seen_refs:
                seen_refs.add(rep)
                ceiling_refs.append(c_ref)

        # Floor below (must have upward-facing normal)
        f_ref = get_closest_valid_ref(doc, down_refs, max_ray_distance, require_upward_normal=True)
        if f_ref:
            rep = f_ref.ConvertToStableRepresentation(doc)
            if rep not in seen_refs:
                seen_refs.add(rep)
                floor_refs.append(f_ref)

    return floor_refs, ceiling_refs


def get_face_polygon(face):
    """Convert a Revit Face to a 2D Shapely Polygon ('shadow')."""
    loops = face.GetEdgesAsCurveLoops()
    if not loops:
        return None

    polys = []
    for loop in loops:
        points = []
        for curve in loop:
            pt = curve.GetEndPoint(0)
            points.append((pt.X, pt.Y))  # Drop the Z coordinate entirely

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

    # First loop is the outer boundary, subsequent loops are holes
    combined_poly = polys[0]
    for hole in polys[1:]:
        combined_poly = combined_poly.difference(hole)

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


def evaluate_face_pair(doc, floor_face, floor_poly, ceiling_face, ceiling_poly, room_poly, sketch_plane, z_height):
    """
    Evaluates the 2D intersection of a floor face, ceiling face, and the room boundary.
    Draws the intersection and evaluates clear heights at the vertices.
    """
    if not floor_poly or not ceiling_poly or floor_poly.is_empty or ceiling_poly.is_empty:
        return 0, []

    # Perform the 3-way boolean intersection (Room ∩ Floor ∩ Ceiling)
    intersection = room_poly.intersection(floor_poly).intersection(ceiling_poly)

    if intersection.is_empty:
        return 0, []

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
    else:
        return 0, []

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

            if p1.DistanceTo(p2) > 0.005:
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

                if p1.DistanceTo(p2) > 0.005:
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

    if room_area > 0:
        print(f"-> Found intersection overlapping {area_pct:.2f}% of the room area. Clear height(s): {height_str}")

    # Introduce threshold: exclude areas representing < 8.0% of the room from the final evaluation
    # (they will still be drawn as curves for debugging, but not return heights)
    if area_pct < 8.0:
        print(f"   (Excluded from final evaluation as {area_pct:.2f}% < 8.0%)")
        return lines_created, []

    return lines_created, clear_heights


@revit
def main():
    doc = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument

    # 1. Get the currently selected room
    selection = uidoc.Selection.GetElementIds()
    room = None
    for eid in selection:
        elem = doc.GetElement(eid)
        if elem.Category and elem.Category.Id.Value == int(BuiltInCategory.OST_Rooms):
            room = elem
            break

    if not room:
        print("Please select a Room before running the script.")
        return

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
        print("Could not extract valid boundaries from the room.")
        return

    # 4. Prompt user to pick MULTIPLE faces for floors, then ceilings
    try:
        floor_refs, ceiling_refs = auto_find_room_faces(doc, room, z_height, room_poly)
        if not floor_refs:
            print("No floor faces found.")
            return

        if not ceiling_refs:
            print("No ceiling/roof faces found.")
            return
    except Exception:
        print("Face selection cancelled.")
        return

    # 5. Evaluate intersections and draw
    t = Transaction(doc, "Draw Multi-Face Intersections & Evaluate Heights")
    t.Start()

    try:
        # Create a flat SketchPlane at the exact calculated z_height
        origin = XYZ(0, 0, z_height)
        plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, origin)
        sketch_plane = SketchPlane.Create(doc, plane)

        total_lines_created = 0
        all_clear_heights = []

        # Pre-process floors: sort by Z descending (highest first) and subtract upper from lower
        floors_data = []
        for ref in floor_refs:
            face = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
            poly = get_face_polygon(face)
            if poly:
                z = face.Origin.Z if hasattr(face, "Origin") else 0
                floors_data.append({'face': face, 'poly': poly, 'z': z})

        floors_data.sort(key=lambda x: x['z'], reverse=True)
        accumulated_floors = None
        for data in floors_data:
            if accumulated_floors is not None:
                data['poly'] = data['poly'].difference(accumulated_floors)
                accumulated_floors = accumulated_floors.union(data['poly'])
            else:
                accumulated_floors = data['poly']

        # Pre-process ceilings: sort by Z ascending (lowest first) and subtract lower from upper
        ceilings_data = []
        for ref in ceiling_refs:
            face = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
            poly = get_face_polygon(face)
            if poly:
                z = face.Origin.Z if hasattr(face, "Origin") else 0
                ceilings_data.append({'face': face, 'poly': poly, 'z': z})

        ceilings_data.sort(key=lambda x: x['z'])
        accumulated_ceilings = None
        for data in ceilings_data:
            if accumulated_ceilings is not None:
                data['poly'] = data['poly'].difference(accumulated_ceilings)
                accumulated_ceilings = accumulated_ceilings.union(data['poly'])
            else:
                accumulated_ceilings = data['poly']

        # Iterate through every combination of (floor face) and (ceiling face)
        for floor_data in floors_data:
            for ceiling_data in ceilings_data:
                lines_created, heights = evaluate_face_pair(
                    doc,
                    floor_data['face'], floor_data['poly'],
                    ceiling_data['face'], ceiling_data['poly'],
                    room_poly, sketch_plane, z_height
                )

                total_lines_created += lines_created
                all_clear_heights.extend(heights)

        t.Commit()

        print(f"Success! Created {total_lines_created} model lines at Z={z_height * 0.3048:.3f}m showing the overlaps.")

        # 6. Print the global minimum and maximum
        if all_clear_heights:
            min_ch = min(all_clear_heights)
            max_ch = max(all_clear_heights)
            print("\n--- Clear Heights Summary ---")
            print(f"Evaluated {len(all_clear_heights)} valid points across all intersecting floor-ceiling pairs.")
            print(f"Minimum Clear Height: {min_ch:.3f} m")
            print(f"Maximum Clear Height: {max_ch:.3f} m")
        else:
            print("\nNo valid clear heights could be evaluated at the intersection vertices.")

    except Exception as e:
        t.RollBack()
        print(f"Error processing intersections: {e}")


if __name__ == "__main__":
    main()