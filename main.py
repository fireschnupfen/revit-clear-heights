from revit_rpc import revit

try:
    from Autodesk.Revit.DB import (
        FilteredElementCollector, View3D, ReferenceIntersector,
        FindReferenceTarget, XYZ, SpatialElementBoundaryOptions,
        SpatialElementBoundaryLocation, CurveLoop, Transaction,
        BuiltInParameter, BuiltInCategory, ElementCategoryFilter,
        ElementMulticategoryFilter, Floor, Ceiling, RoofBase
    )
    from System.Collections.Generic import List
except ImportError:
    pass

__revit__: str


def get_room_boundary_points(room, options):
    """Extract boundary curves from a room and return as list of points."""
    boundary_segments = room.GetBoundarySegments(options)
    if not boundary_segments or boundary_segments.Count == 0:
        return []
    
    points = []
    for segment_list in boundary_segments:
        for segment in segment_list:
            curve = segment.GetCurve()
            points.append(curve.GetEndPoint(0))
    return points


def create_grid_points_in_polygon(boundary_points, grid_spacing, z_height):
    """Create a dense grid of points within the polygon boundary at given z height."""
    if not boundary_points:
        return []
    
    # Find bounding box of the polygon
    min_x = min(p.X for p in boundary_points)
    max_x = max(p.X for p in boundary_points)
    min_y = min(p.Y for p in boundary_points)
    max_y = max(p.Y for p in boundary_points)
    
    # Generate grid points
    grid_points = []
    x = min_x + grid_spacing / 2
    while x < max_x:
        y = min_y + grid_spacing / 2
        while y < max_y:
            point = XYZ(x, y, z_height)
            if is_point_in_polygon(point, boundary_points):
                grid_points.append(point)
            y += grid_spacing
        x += grid_spacing
    
    return grid_points


def is_point_in_polygon(point, polygon_points):
    """Check if a point is inside a polygon using ray casting algorithm."""
    n = len(polygon_points)
    if n < 3:
        return False
    
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon_points[i].X, polygon_points[i].Y
        xj, yj = polygon_points[j].X, polygon_points[j].Y
        
        if ((yi > point.Y) != (yj > point.Y)) and \
           (point.X < (xj - xi) * (point.Y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    
    return inside


def get_3d_view_by_name(doc, view_name):
    """Get a 3D view by its name."""
    collector = FilteredElementCollector(doc).OfClass(View3D)
    for view in collector:
        if view.Name == view_name and not view.IsTemplate:
            return view
    return None


def calculate_clear_height_at_point(point, intersector_up, intersector_down, max_distance):
    """Calculate clear height at a single point by casting rays up and down."""
    from Autodesk.Revit.DB import XYZ
    
    up_direction = XYZ(0, 0, 1)
    down_direction = XYZ(0, 0, -1)
    
    # Cast ray upward
    up_refs = intersector_up.Find(point, up_direction)
    up_distance = None
    for ref_with_context in up_refs:
        ref = ref_with_context.GetReference()
        if ref is not None:
            up_distance = ref_with_context.Proximity
            break
    
    # Cast ray downward
    down_refs = intersector_down.Find(point, down_direction)
    down_distance = None
    for ref_with_context in down_refs:
        ref = ref_with_context.GetReference()
        if ref is not None:
            down_distance = ref_with_context.Proximity
            break
    
    # Calculate clear height
    if up_distance is not None and down_distance is not None:
        return up_distance + down_distance
    
    return None


def is_element_room_bounding(doc, element_id):
    """Check if an element has Room Bounding parameter set to True."""
    element = doc.GetElement(element_id)
    if element is None:
        return False
    
    param = element.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
    if param is not None:
        return param.AsInteger() == 1
    return False


def format_clear_height_result(clear_heights):
    """Format clear heights as string with German locale (comma as decimal separator)."""
    if not clear_heights:
        return ""
    
    unique_heights = sorted(set(clear_heights))
    
    if len(unique_heights) == 1:
        height_m = unique_heights[0]
        return f"{height_m:.2f}m".replace(".", ",")
    else:
        min_height = min(unique_heights)
        max_height = max(unique_heights)
        return f"{min_height:.2f}m - {max_height:.2f}m".replace(".", ",")


@revit
def main():
    from Autodesk.Revit.DB import (
        FilteredElementCollector, View3D, ReferenceIntersector,
        FindReferenceTarget, XYZ, SpatialElementBoundaryOptions,
        SpatialElementBoundaryLocation, Transaction,
        BuiltInParameter, BuiltInCategory, ElementMulticategoryFilter,
        UnitUtils, UnitTypeId
    )
    from System.Collections.Generic import List
    
    doc = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument
    
    # Get selected elements
    selection = uidoc.Selection.GetElementIds()
    if selection.Count == 0:
        print("No elements selected.")
        return
    
    # Get the 3D view for ray casting
    view_3d = None
    collector = FilteredElementCollector(doc).OfClass(View3D)
    for view in collector:
        if view.Name == "RAY_CASTING_VIEW" and not view.IsTemplate:
            view_3d = view
            break
    
    if view_3d is None:
        print("3D view 'RAY_CASTING_VIEW' not found.")
        return
    
    # Create category filter for Floor, Ceiling, and Roof
    categories = List[BuiltInCategory]()
    categories.Add(BuiltInCategory.OST_Floors)
    categories.Add(BuiltInCategory.OST_Ceilings)
    categories.Add(BuiltInCategory.OST_Roofs)
    category_filter = ElementMulticategoryFilter(categories)
    
    # Create reference intersectors
    intersector_up = ReferenceIntersector(category_filter, FindReferenceTarget.Face, view_3d)
    intersector_down = ReferenceIntersector(category_filter, FindReferenceTarget.Face, view_3d)
    
    # Boundary options
    boundary_options = SpatialElementBoundaryOptions()
    boundary_options.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish
    
    # Grid spacing in feet (approximately 0.1m = ~0.328 feet)
    grid_spacing = 0.328
    
    # Threshold for filtering clear heights (8% = 0.08)
    # Heights representing less than 8% of the room area are filtered out
    threshold = 0.08
    
    # Start transaction
    t = Transaction(doc, "Calculate Clear Heights")
    t.Start()
    
    try:
        for element_id in selection:
            room = doc.GetElement(element_id)
            
            # Check if it's a room
            if room.Category is None or room.Category.Id.Value != int(BuiltInCategory.OST_Rooms):
                continue
            
            # Get room's level elevation
            level = doc.GetElement(room.LevelId)
            level_elevation = level.Elevation if level else 0
            
            # Get room's base offset
            base_offset_param = room.get_Parameter(BuiltInParameter.ROOM_LOWER_OFFSET)
            base_offset = base_offset_param.AsDouble() if base_offset_param else 0
            
            # Get the room's computation height (this is the "Height for Calculation" parameter)
            computation_height_param = room.get_Parameter(BuiltInParameter.ROOM_COMPUTATION_HEIGHT)
            computation_height = computation_height_param.AsDouble() if computation_height_param else 0
            
            # Calculate z height for grid points
            # Place points slightly above the floor level (level_elevation + base_offset + small offset)
            # This ensures we're inside the room and can cast rays to find floor and ceiling
            small_offset = 0.01  # ~3mm above floor level
            z_height = level_elevation + base_offset + small_offset
            
            # Get room's unbounded height for ray length calculation
            unbounded_height_param = room.get_Parameter(BuiltInParameter.ROOM_HEIGHT)
            unbounded_height = unbounded_height_param.AsDouble() if unbounded_height_param else 10
            
            # Get room's upper offset (limit offset)
            upper_offset_param = room.get_Parameter(BuiltInParameter.ROOM_UPPER_OFFSET)
            upper_offset = upper_offset_param.AsDouble() if upper_offset_param else 0
            
            # Get room's limit (upper level)
            limit_param = room.get_Parameter(BuiltInParameter.ROOM_UPPER_LEVEL)
            
            print(f"Room {room.Id}: level_elevation={level_elevation * 0.3048:.2f}m, base_offset={base_offset * 0.3048:.2f}m, computation_height={computation_height * 0.3048:.2f}m, z_height={z_height * 0.3048:.2f}m, unbounded_height={unbounded_height * 0.3048:.2f}m, upper_offset={upper_offset * 0.3048:.2f}m")
            
            # Ray length proportional to room height (with some margin)
            max_ray_distance_up = unbounded_height * 1.5
            max_ray_distance_down = unbounded_height * 1.5
            
            # Get room boundary points
            boundary_points = []
            boundary_segments = room.GetBoundarySegments(boundary_options)
            if boundary_segments and boundary_segments.Count > 0:
                for segment_list in boundary_segments:
                    for segment in segment_list:
                        curve = segment.GetCurve()
                        boundary_points.append(curve.GetEndPoint(0))
            
            if not boundary_points:
                print(f"Room {room.Id} has no boundary.")
                continue
            
            # Create grid points within the room boundary
            grid_points = []
            
            # Minimum distance from boundary to avoid edge issues (in feet, ~10cm)
            boundary_inset = 0.328
            
            # Find bounding box of the polygon
            min_x = min(p.X for p in boundary_points)
            max_x = max(p.X for p in boundary_points)
            min_y = min(p.Y for p in boundary_points)
            max_y = max(p.Y for p in boundary_points)
            
            # Helper function to calculate distance from point to line segment
            def point_to_segment_distance(px, py, x1, y1, x2, y2):
                dx = x2 - x1
                dy = y2 - y1
                if dx == 0 and dy == 0:
                    return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
                t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
                proj_x = x1 + t * dx
                proj_y = y1 + t * dy
                return ((px - proj_x) ** 2 + (py - proj_y) ** 2) ** 0.5
            
            # Helper function to check minimum distance from all boundary edges
            def min_distance_to_boundary(px, py, boundary_pts):
                min_dist = float('inf')
                n = len(boundary_pts)
                for i in range(n):
                    x1, y1 = boundary_pts[i].X, boundary_pts[i].Y
                    x2, y2 = boundary_pts[(i + 1) % n].X, boundary_pts[(i + 1) % n].Y
                    dist = point_to_segment_distance(px, py, x1, y1, x2, y2)
                    if dist < min_dist:
                        min_dist = dist
                return min_dist
            
            # Generate grid points
            x = min_x + grid_spacing / 2
            while x < max_x:
                y = min_y + grid_spacing / 2
                while y < max_y:
                    point = XYZ(x, y, z_height)
                    # Check if point is inside polygon
                    n = len(boundary_points)
                    inside = False
                    j = n - 1
                    for i in range(n):
                        xi, yi = boundary_points[i].X, boundary_points[i].Y
                        xj, yj = boundary_points[j].X, boundary_points[j].Y
                        if ((yi > point.Y) != (yj > point.Y)) and \
                           (point.X < (xj - xi) * (point.Y - yi) / (yj - yi) + xi):
                            inside = not inside
                        j = i
                    
                    # Only add point if inside AND far enough from boundary
                    if inside:
                        dist_to_boundary = min_distance_to_boundary(x, y, boundary_points)
                        if dist_to_boundary >= boundary_inset:
                            grid_points.append(point)
                    y += grid_spacing
                x += grid_spacing
            
            if not grid_points:
                print(f"Room {room.Id} has no grid points.")
                continue
            
            # Calculate clear heights at each grid point
            clear_heights = []
            up_direction = XYZ(0, 0, 1)
            down_direction = XYZ(0, 0, -1)
            
            debug_count = 0
            for point in grid_points:
                # Cast ray upward - find the closest room-bounding element
                up_refs = intersector_up.Find(point, up_direction)
                up_distance = None
                up_elem_info = None
                
                # Find the closest valid hit (room-bounding element with correct face normal)
                closest_up_distance = None
                for ref_with_context in up_refs:
                    ref = ref_with_context.GetReference()
                    if ref is not None:
                        proximity = ref_with_context.Proximity
                        # Skip if we already found a closer valid hit
                        if closest_up_distance is not None and proximity >= closest_up_distance:
                            continue
                        # Filter by max distance
                        if proximity > max_ray_distance_up:
                            continue
                        # Check if element is room bounding
                        elem_id = ref.ElementId
                        elem = doc.GetElement(elem_id)
                        if elem:
                            param = elem.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
                            if param and param.AsInteger() == 1:
                                # For upward rays, we want to hit the bottom face of ceiling/floor above
                                # Check if the face normal points downward (negative Z)
                                face_ref = ref
                                try:
                                    face = elem.GetGeometryObjectFromReference(face_ref)
                                    if face:
                                        # Get face normal at the hit point
                                        uv = face_ref.UVPoint
                                        if uv:
                                            normal = face.ComputeNormal(uv)
                                        else:
                                            # Use a default UV point
                                            normal = face.ComputeNormal(face.GetBoundingBox().Min)
                                        # For upward ray, we want face with downward normal (Z < 0)
                                        if normal.Z >= 0:
                                            if debug_count < 3:
                                                print(f"  UP: Skipped {elem.Category.Name} (ID: {elem_id.Value}) - wrong face normal Z={normal.Z:.2f}, dist={proximity * 0.3048:.2f}m")
                                            continue
                                except:
                                    pass  # If we can't check normal, accept the hit
                                
                                # This is a valid hit - check if it's the closest
                                if closest_up_distance is None or proximity < closest_up_distance:
                                    closest_up_distance = proximity
                                    up_distance = proximity
                                    up_elem_info = f"{elem.Category.Name} (ID: {elem_id.Value}, RoomBounding=True)"
                
                # Cast ray downward - find the closest room-bounding element
                down_refs = intersector_down.Find(point, down_direction)
                down_distance = None
                down_elem_info = None
                
                # Find the closest valid hit (room-bounding element with correct face normal)
                closest_down_distance = None
                for ref_with_context in down_refs:
                    ref = ref_with_context.GetReference()
                    if ref is not None:
                        proximity = ref_with_context.Proximity
                        # Skip if we already found a closer valid hit
                        if closest_down_distance is not None and proximity >= closest_down_distance:
                            continue
                        # Filter by max distance
                        if proximity > max_ray_distance_down:
                            continue
                        # Check if element is room bounding
                        elem_id = ref.ElementId
                        elem = doc.GetElement(elem_id)
                        if elem:
                            param = elem.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
                            if param and param.AsInteger() == 1:
                                # For downward rays, we want to hit the top face of floor below
                                # Check if the face normal points upward (positive Z)
                                face_ref = ref
                                try:
                                    face = elem.GetGeometryObjectFromReference(face_ref)
                                    if face:
                                        # Get face normal at the hit point
                                        uv = face_ref.UVPoint
                                        if uv:
                                            normal = face.ComputeNormal(uv)
                                        else:
                                            # Use a default UV point
                                            normal = face.ComputeNormal(face.GetBoundingBox().Min)
                                        # For downward ray, we want face with upward normal (Z > 0)
                                        if normal.Z <= 0:
                                            if debug_count < 3:
                                                print(f"  DOWN: Skipped {elem.Category.Name} (ID: {elem_id.Value}) - wrong face normal Z={normal.Z:.2f}, dist={proximity * 0.3048:.2f}m")
                                            continue
                                except:
                                    pass  # If we can't check normal, accept the hit
                                
                                # This is a valid hit - check if it's the closest
                                if closest_down_distance is None or proximity < closest_down_distance:
                                    closest_down_distance = proximity
                                    down_distance = proximity
                                    down_elem_info = f"{elem.Category.Name} (ID: {elem_id.Value}, RoomBounding=True)"
                
                if debug_count < 3:
                    print(f"Point {debug_count}: up_dist={up_distance * 0.3048 if up_distance else None:.2f}m ({up_elem_info}), down_dist={down_distance * 0.3048 if down_distance else None:.2f}m ({down_elem_info})")
                    debug_count += 1
                
                # Calculate clear height
                if up_distance is not None and down_distance is not None:
                    clear_height = up_distance + down_distance
                    # Convert from feet to meters
                    clear_height_m = clear_height * 0.3048
                    # Round to 2 decimal places
                    clear_height_m = round(clear_height_m, 2)
                    clear_heights.append(clear_height_m)
            
            if not clear_heights:
                print(f"Room {room.Id} has no valid clear heights.")
                continue
            
            # Count occurrences of each clear height
            height_counts = {}
            for h in clear_heights:
                height_counts[h] = height_counts.get(h, 0) + 1
            
            total_points = len(clear_heights)
            
            # Debug: show height distribution
            print(f"Height distribution (total {total_points} points, threshold {threshold*100:.2f}%):")
            for h, count in sorted(height_counts.items()):
                pct = count / total_points * 100
                print(f"  {h:.2f}m: {count} points ({pct:.2f}%)")
            
            # Filter heights by threshold (0.08% of total area)
            filtered_heights = []
            for h, count in height_counts.items():
                percentage = count / total_points
                if percentage >= threshold:
                    filtered_heights.append(h)
            
            if not filtered_heights:
                # If all heights are filtered out, use all heights
                filtered_heights = list(height_counts.keys())
            
            print(f"Filtered heights: {sorted(filtered_heights)}")
            
            # Format result
            unique_heights = sorted(set(filtered_heights))
            
            if len(unique_heights) == 1:
                result = f"{unique_heights[0]:.2f}m".replace(".", ",")
            else:
                min_height = min(unique_heights)
                max_height = max(unique_heights)
                result = f"{min_height:.2f}m - {max_height:.2f}m".replace(".", ",")
            
            # Write to parameter
            param = room.LookupParameter("Lichte_Raumhöhe")
            if param:
                param.Set(result)
                print(f"Room {room.Id}: {result}")
            else:
                print(f"Room {room.Id}: Parameter 'Lichte_Raumhöhe' not found.")
        
        t.Commit()
        print("Command executed successfully.")
    except Exception as e:
        t.RollBack()
        print(f"Error during execution: {e}")
        raise


if __name__ == "__main__":
    main()
