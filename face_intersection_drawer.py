from revit_rpc import revit
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.validation import make_valid

try:
    from Autodesk.Revit.DB import (
        Transaction, XYZ, Line, Plane, SketchPlane, BuiltInCategory,
        BuiltInParameter, SpatialElementBoundaryOptions,
        SpatialElementBoundaryLocation, PlanarFace
    )
    from Autodesk.Revit.UI.Selection import ObjectType
except ImportError:
    pass

__revit__: str


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


def evaluate_face_pair(doc, floor_face, ceiling_face, room_poly, sketch_plane, z_height):
    """
    Evaluates the 2D intersection of a floor face, ceiling face, and the room boundary.
    Draws the intersection and evaluates clear heights at the vertices.
    """
    poly1 = get_face_polygon(floor_face)
    poly2 = get_face_polygon(ceiling_face)

    if not poly1 or not poly2:
        return 0, []

    # Perform the 3-way boolean intersection (Room ∩ Floor ∩ Ceiling)
    intersection = room_poly.intersection(poly1).intersection(poly2)

    if intersection.is_empty:
        return 0, []

    # Calculate and print the area percentage
    room_area = room_poly.area
    intersection_area = intersection.area
    if room_area > 0:
        area_pct = (intersection_area / room_area) * 100
        print(f"-> Found intersection overlapping {area_pct:.2f}% of the room area.")

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

    z_height = level_elevation + computation_height

    # 3. Prompt user to pick MULTIPLE faces for floors, then ceilings
    try:
        floor_refs = uidoc.Selection.PickObjects(ObjectType.Face, "1. Select all FLOOR faces to evaluate")
        if not floor_refs:
            print("No floor faces selected.")
            return

        ceiling_refs = uidoc.Selection.PickObjects(ObjectType.Face, "2. Select all CEILING/ROOF faces to evaluate")
        if not ceiling_refs:
            print("No ceiling/roof faces selected.")
            return
    except Exception:
        print("Face selection cancelled.")
        return

    # 4. Extract room polygon
    room_poly = get_room_polygon(room)
    if not room_poly:
        print("Could not extract valid boundaries from the room.")
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

        # Iterate through every combination of (floor face) and (ceiling face)
        for floor_ref in floor_refs:
            floor_face = doc.GetElement(floor_ref).GetGeometryObjectFromReference(floor_ref)

            for ceiling_ref in ceiling_refs:
                ceiling_face = doc.GetElement(ceiling_ref).GetGeometryObjectFromReference(ceiling_ref)

                lines_created, heights = evaluate_face_pair(
                    doc, floor_face, ceiling_face, room_poly, sketch_plane, z_height
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