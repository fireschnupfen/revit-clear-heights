"""
Evaluation module for computing clear heights across intersecting face pairs and aggregating results.
"""

try:
    from Autodesk.Revit.DB import XYZ, Line, ElementId
except ImportError:
    pass

from .geometry import get_intersection_polygon, get_z_on_planar_face
from .ray_casting import get_room_face_pairs

__revit__: str


def round_to_half_cm(value):
    """Rounds the value to the nearest 0.005 (0.5 cm)."""
    return round(value * 200) / 200


def format_height_value(value):
    """Formats a value to 2 decimal places, with 5 as superscript if present."""
    rounded = round_to_half_cm(value)
    str_val = f"{rounded:.3f}".replace(".", ",")
    if str_val.endswith("5"):
        return str_val[:-1] + "⁵"
    else:
        return str_val[:-1]


def evaluate_face_pair(doc, floor_face, floor_poly, ceiling_face, ceiling_poly, room_poly,
                       sketch_plane, z_height, create_debug_lines=False, debug=False,
                       range_delimiter="-"):
    """
    Evaluates the 2D intersection of a floor face, ceiling face, and the room boundary.
    Draws the intersection and evaluates clear heights at the vertices.
    """
    if not floor_poly or not ceiling_poly or floor_poly.is_empty or ceiling_poly.is_empty:
        return 0, [], 0.0, [], []

    # Perform the 3-way boolean intersection (Room ∩ Floor ∩ Ceiling)
    intersection = get_intersection_polygon([room_poly, floor_poly, ceiling_poly])

    if not intersection or intersection.is_empty:
        return 0, [], 0.0, [], []

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
        return 0, [], area_pct, [], []

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
    floor_elevations = []
    ceiling_elevations = []
    for x, y in evaluated_points:
        z1 = get_z_on_planar_face(floor_face, x, y)
        z2 = get_z_on_planar_face(ceiling_face, x, y)

        if z1 is not None and z2 is not None:
            clear_height_ft = abs(z1 - z2)
            clear_height_m = clear_height_ft * 0.3048
            clear_heights.append(clear_height_m)
            floor_elevations.append(z1 * 0.3048)
            ceiling_elevations.append(z2 * 0.3048)

    if clear_heights:
        min_h = min(clear_heights)
        max_h = max(clear_heights)
        if abs(max_h - min_h) > 0.005:
            height_str = f"{format_height_value(min_h)}m {range_delimiter} {format_height_value(max_h)}m"
        else:
            height_str = f"{format_height_value(min_h)}m"
    else:
        height_str = "None"

    if debug and room_area > 0:
        intersection_area_m2 = intersection_area * 0.09290304
        print(f"-> Found intersection overlapping {area_pct:.2f}% of the room area (Intersection Area: {intersection_area_m2:.2f} m²). Clear height(s): {height_str}")

    return lines_created, clear_heights, area_pct, floor_elevations, ceiling_elevations


def evaluate_room_clear_heights(doc, room, z_height, room_poly, grid_spacing,
                                create_ray_lines=False, create_debug_lines=False,
                                debug=False, sketch_plane=None, element_filter=None,
                                ray_length_multiplicator=1.5,
                                ray_casting_view_name="RAY_CASTING_VIEW",
                                range_delimiter="-"):
    """
    Evaluates all clear heights for a room by finding faces, pairing them, and evaluating each pair.
    Returns (total_lines_created, results), where results is a list of dicts:
        {'heights': [...], 'area_pct': float, 'is_ignored': bool, 'f_elevs': [...], 'c_elevs': [...]}.
    """
    floor_refs, ceiling_refs, pairs = get_room_face_pairs(
        doc, room, z_height, room_poly, grid_spacing,
        create_ray_lines=create_ray_lines, debug=debug,
        ray_length_multiplicator=ray_length_multiplicator,
        ray_casting_view_name=ray_casting_view_name
    )

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

    for floor_data, ceiling_data in pairs:
        lines_created, heights, area_pct, f_elevs, c_elevs = evaluate_face_pair(
            doc,
            floor_data['face'], floor_data['poly'],
            ceiling_data['face'], ceiling_data['poly'],
            room_poly, sketch_plane, z_height,
            create_debug_lines=create_debug_lines,
            debug=debug,
            range_delimiter=range_delimiter
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
            results.append({
                'heights': heights,
                'area_pct': area_pct,
                'is_ignored': is_ignored,
                'f_elevs': f_elevs,
                'c_elevs': c_elevs
            })

    return total_lines_created, results

