from revit_rpc import revit
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import Point as ShapelyPoint

try:
    from Autodesk.Revit.DB import (
        Transaction, XYZ, Line, BuiltInCategory,
        BuiltInParameter, SpatialElementBoundaryOptions,
        SpatialElementBoundaryLocation, PlanarFace, View3D, FilteredElementCollector,
        ReferenceIntersector, FindReferenceTarget, ElementMulticategoryFilter,
        ParameterFilterElement, ElementId, GeometryCreationUtilities, CurveLoop,
        DirectShape, GeometryObject
    )
    from System.Collections.Generic import List
except ImportError:
    pass

__revit__: str

from main import (
    get_room_polygon, auto_find_room_faces, get_face_polygon, 
    get_intersection_polygon, get_subtracted_polygon
)

def clean_coords(coords):
    """Cleans coordinates to prevent lines that are too short for Revit."""
    if not coords: return []
    cleaned = [XYZ(coords[0][0], coords[0][1], 0)]
    for i in range(1, len(coords)):
        pt = XYZ(coords[i][0], coords[i][1], 0)
        if cleaned[-1].DistanceTo(pt) > 0.005:
            cleaned.append(pt)
    
    # Ensure closed and no short final segment
    while len(cleaned) > 2 and cleaned[-1].DistanceTo(cleaned[0]) <= 0.005:
        cleaned.pop()
        
    cleaned.append(cleaned[0])
    return cleaned

def create_curve_loops_from_polygon(poly, z_height):
    """Converts a Shapely polygon to Revit CurveLoops at a given Z height."""
    loops = List[CurveLoop]()
    
    # Exterior
    coords = list(poly.exterior.coords)
    cleaned = clean_coords(coords)
    if len(cleaned) > 3:
        exterior_loop = CurveLoop()
        for i in range(len(cleaned) - 1):
            p1 = XYZ(cleaned[i].X, cleaned[i].Y, z_height)
            p2 = XYZ(cleaned[i+1].X, cleaned[i+1].Y, z_height)
            exterior_loop.Append(Line.CreateBound(p1, p2))
        try:
            GeometryCreationUtilities.CreateExtrusionGeometry(List[CurveLoop]([exterior_loop]), XYZ.BasisZ, 1.0)
            loops.Add(exterior_loop)
        except Exception:
            pass # Invalid outer loop
    else:
        return None
        
    # Interiors
    for hole in poly.interiors:
        h_coords = list(hole.coords)
        # Reverse hole coordinates to ensure correct winding direction for inner loops
        h_coords.reverse()
        cleaned_hole = clean_coords(h_coords)
        if len(cleaned_hole) > 3:
            hole_loop = CurveLoop()
            for i in range(len(cleaned_hole) - 1):
                p1 = XYZ(cleaned_hole[i].X, cleaned_hole[i].Y, z_height)
                p2 = XYZ(cleaned_hole[i+1].X, cleaned_hole[i+1].Y, z_height)
                hole_loop.Append(Line.CreateBound(p1, p2))
            
            try:
                # Test the loop before adding it
                GeometryCreationUtilities.CreateExtrusionGeometry(List[CurveLoop]([hole_loop]), XYZ.BasisZ, 1.0)
                loops.Add(hole_loop)
            except Exception:
                pass # Skip invalid inner loops
            
    return loops

@revit
def main(debug=False, grid_spacing_min_m=0.5, grid_spacing_max_m=1.5,
         area_min_m2=10.0, area_max_m2=100.0, ray_casting_view_name="RAY_CASTING_VIEW"):
    doc = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument

    print("Hello world!")

    # Get selected rooms
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

    t = Transaction(doc, "Create Clear Height Shapes")
    t.Start()

    try:
        extrusion_dist = 2.1 / 0.3048  # 2.1m to feet

        for room in rooms:
            if debug:
                room_name_param = room.get_Parameter(BuiltInParameter.ROOM_NAME)
                room_name = room_name_param.AsString() if room_name_param else "Unknown"
                print(f"\n--- Processing Room: {room_name} ---")

            level = doc.GetElement(room.LevelId)
            level_elevation = level.Elevation if level else 0

            computation_height_param = level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT)
            computation_height = computation_height_param.AsDouble() if computation_height_param else 0

            small_offset = 0.01  
            z_height = level_elevation + computation_height + small_offset

            room_poly = get_room_polygon(room)
            if not room_poly:
                continue

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

            grid_spacing = grid_spacing_m * 3.28084 

            floor_refs, _ = auto_find_room_faces(doc, room, z_height, room_poly, grid_spacing,
                                                 ray_casting_view_name=ray_casting_view_name)

            if not floor_refs:
                continue

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
                    data['poly'] = get_subtracted_polygon(data['poly'], accumulated_floors)
                    if data['poly'] and not data['poly'].is_empty:
                        from shapely.validation import make_valid
                        data['poly'] = make_valid(data['poly']).buffer(0)
                        accumulated_floors = accumulated_floors.union(data['poly'])
                else:
                    if data['poly'] and not data['poly'].is_empty:
                        from shapely.validation import make_valid
                        data['poly'] = make_valid(data['poly']).buffer(0)
                        accumulated_floors = data['poly']

            shapes_created = 0
            for data in floors_data:
                if not data['poly'] or data['poly'].is_empty:
                    continue

                intersection = get_intersection_polygon([room_poly, data['poly']])
                if not intersection or intersection.is_empty:
                    continue
                    
                from shapely.validation import make_valid
                intersection = make_valid(intersection).buffer(0).simplify(0.005, preserve_topology=True)
                
                if intersection.is_empty:
                    continue

                geoms = []
                if intersection.geom_type == 'Polygon':
                    geoms = [intersection]
                elif intersection.geom_type == 'MultiPolygon':
                    geoms = intersection.geoms
                elif intersection.geom_type == 'GeometryCollection':
                    geoms = [g for g in intersection.geoms if g.geom_type in ('Polygon', 'MultiPolygon')]

                for poly in geoms:
                    if poly.geom_type != 'Polygon':
                        continue
                    
                    # Ignore tiny slivers (area < 0.01 sq ft)
                    if poly.area < 0.01:
                        continue
                        
                    loops = create_curve_loops_from_polygon(poly, data['z'])
                    if loops and loops.Count > 0:
                        try:
                            solid = GeometryCreationUtilities.CreateExtrusionGeometry(loops, XYZ.BasisZ, extrusion_dist)
                            ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_Mass))
                            shape_list = List[GeometryObject]()
                            shape_list.Add(solid)
                            ds.SetShape(shape_list)
                            ds.Name = f"Clear Height Volume - {room_name}"
                            shapes_created += 1
                        except Exception as e:
                            if debug:
                                print(f"Failed to create extrusion: {e}")

            if debug:
                print(f"Created {shapes_created} DirectShapes for room {room_name}.")

        t.Commit()
    except Exception as e:
        t.RollBack()
        if debug:
            print(f"Error: {e}")

if __name__ == "__main__":
    main(debug=True, ray_casting_view_name="Berechnung Lichte Raumhöhen")
