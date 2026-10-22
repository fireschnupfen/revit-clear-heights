"""
Ray casting module for finding floor and ceiling faces using Revit's ReferenceIntersector.
"""

from shapely.geometry import Point as ShapelyPoint

try:
    from Autodesk.Revit.DB import (
        FilteredElementCollector, View3D, ReferenceIntersector,
        FindReferenceTarget, BuiltInCategory, BuiltInParameter,
        XYZ, Line, Plane, SketchPlane
    )

    BOUNDARY_CATEGORIES = {
        int(BuiltInCategory.OST_Walls),
        int(BuiltInCategory.OST_GenericModel),
        int(BuiltInCategory.OST_StructuralFraming)
    }
except ImportError:
    BOUNDARY_CATEGORIES = set()

from .geometry import get_face_polygon, get_subtracted_polygon, robust_union

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
    sorted_refs = sorted(refs, key=lambda r: r.Proximity)

    for ref_with_context in sorted_refs:
        proximity = ref_with_context.Proximity
        if proximity > max_distance:
            break

        ref = ref_with_context.GetReference()
        if not ref:
            continue

        elem = doc.GetElement(ref.ElementId)
        if not elem:
            continue

        param = elem.LookupParameter("Raumbegrenzung")
        # If the parameter exists and has an integer value, and it is explicitly set to 0 (unchecked), we skip this element.
        if param and param.HasValue and param.AsInteger() == 0:
            continue

        # If the ray hits a boundary category first, it is swallowed.
        cat = elem.Category
        if cat and cat.Id.Value in BOUNDARY_CATEGORIES:
            return None

        try:
            face = elem.GetGeometryObjectFromReference(ref)
            if face:
                uv = ref.UVPoint
                normal = face.ComputeNormal(uv) if uv else face.ComputeNormal(face.GetBoundingBox().Min)

                tolerance = 1e-6
                if require_upward_normal and normal.Z <= tolerance:
                    continue
                if not require_upward_normal and normal.Z >= -tolerance:
                    continue

                return ref
        except Exception:
            pass

    return None


def auto_find_room_faces(doc, room, z_height, room_poly, grid_spacing,
                         create_ray_lines=False, debug=False,
                         ray_length_multiplicator=1.5,
                         ray_casting_view_name="RAY_CASTING_VIEW"):
    """Automatically find floor and ceiling faces for a given room using ray casting."""
    view_3d = get_3d_view_by_name(doc, ray_casting_view_name)
    if not view_3d:
        if debug:
            print(f"3D view '{ray_casting_view_name}' not found.")
        return [], []

    intersector = ReferenceIntersector(view_3d)
    intersector.TargetType = FindReferenceTarget.Face

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

            if create_ray_lines:
                for hit in down_refs:
                    if hit.GetReference().ConvertToStableRepresentation(doc) == rep:
                        f_pt = XYZ(pt.X, pt.Y, pt.Z - hit.Proximity)
                        break

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


def get_room_face_pairs(doc, room, z_height, room_poly, grid_spacing,
                        create_ray_lines=False, debug=False,
                        ray_length_multiplicator=1.5,
                        ray_casting_view_name="RAY_CASTING_VIEW"):
    """
    Runs auto face detection and then pairs floor and ceiling faces together.
    Returns (floor_refs, ceiling_refs, pairs), where pairs is a list of (floor_data, ceiling_data) tuples.
    """
    floor_refs, ceiling_refs = auto_find_room_faces(
        doc, room, z_height, room_poly, grid_spacing,
        create_ray_lines=create_ray_lines, debug=debug,
        ray_length_multiplicator=ray_length_multiplicator,
        ray_casting_view_name=ray_casting_view_name
    )

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
                accumulated_floors = robust_union(accumulated_floors, data['poly'])
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
                accumulated_ceilings = robust_union(accumulated_ceilings, data['poly'])
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

