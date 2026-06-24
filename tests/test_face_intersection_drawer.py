import pytest
from unittest.mock import MagicMock
import sys

from revit_rpc import revit

# Mock imports if needed, or just let them fail and be mocked
# Let's mock the Revit elements and call the functions

from face_intersection_drawer import (
    get_3d_view_by_name,
    get_closest_valid_ref,
    auto_find_room_faces,
    get_face_polygon,
    get_room_polygon,
    get_z_on_planar_face,
    evaluate_face_pair,
    main
)


@revit
def test_get_room_polygon(transaction, selected_room):
    try:
        from Autodesk.Revit.DB import Plane, SketchPlane, XYZ, BuiltInParameter
        doc = selected_room.Document
        
        level = doc.GetElement(selected_room.LevelId)
        level_elevation = level.Elevation if level else 0
        
        computation_height_param = level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT)
        computation_height = computation_height_param.AsDouble() if computation_height_param else 0
        
        z_height = level_elevation + computation_height
        
        origin = XYZ(0, 0, z_height)
        plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, origin)
        sketch_plane = SketchPlane.Create(doc, plane)
        
        get_room_polygon(selected_room, doc=doc, sketch_plane=sketch_plane, z_height=z_height, create_model_lines=True)
    except Exception as e:
        pytest.fail(f"get_room_polygon raised {e}")

@revit
def test_get_face_polygon(transaction, selected_room):
    try:
        from Autodesk.Revit.DB import Plane, SketchPlane, XYZ, BuiltInParameter
        doc = selected_room.Document
        
        level = doc.GetElement(selected_room.LevelId)
        level_elevation = level.Elevation if level else 0
        
        computation_height_param = level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT)
        computation_height = computation_height_param.AsDouble() if computation_height_param else 0
        
        z_height = level_elevation + computation_height
        
        origin = XYZ(0, 0, z_height)
        plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, origin)
        sketch_plane = SketchPlane.Create(doc, plane)
        
        # We need a face to test this. We can use auto_find_room_faces
        from face_intersection_drawer import auto_find_room_faces, get_room_polygon
        room_poly = get_room_polygon(selected_room)
        floor_refs, ceiling_refs = auto_find_room_faces(doc, selected_room, z_height, room_poly, 1.0)
        
        if floor_refs:
            ref = floor_refs[0]
            face = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
            get_face_polygon(face, doc=doc, sketch_plane=sketch_plane, z_height=z_height, create_model_lines=True)
            
    except Exception as e:
        pytest.fail(f"get_face_polygon raised {e}")
