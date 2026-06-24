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
    get_subtracted_polygon,
    get_intersection_polygon,
    evaluate_face_pair,
    main
)

try:
    from Autodesk.Revit.DB import BuiltInCategory, ElementId, Plane, SketchPlane, XYZ, BuiltInParameter, Transaction
except ImportError:
    ...

__revit__: str


@revit
def get_room(id):
    """Return the first selected Room in the active Revit document."""
    uidoc = __revit__.ActiveUIDocument
    doc = uidoc.Document
    return doc.GetElement(ElementId(id))


@revit
def room_sketch_plane(room):
    doc = room.Document

    level = doc.GetElement(room.LevelId)
    level_elevation = level.Elevation if level else 0
    
    computation_height_param = level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT)
    computation_height = computation_height_param.AsDouble() if computation_height_param else 0
    
    z_height = level_elevation + computation_height
    
    origin = XYZ(0, 0, z_height)
    plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, origin)
    sketch_plane = SketchPlane.Create(doc, plane)
    
    return sketch_plane, z_height


@pytest.mark.parametrize("room_id", [5139071])
@revit
def test_get_room_polygon(transaction, room_id):
    room = get_room(room_id)
    doc = room.Document
    try:
        sketch_plane, z_height = room_sketch_plane(room)

        get_room_polygon(room, doc=doc, sketch_plane=sketch_plane, z_height=z_height, create_model_lines=True)
    except Exception as e:
        pytest.fail(f"get_room_polygon raised {e}")


@pytest.mark.parametrize("room_id", [5139071])
@revit
def test_get_face_polygon(transaction, room_id):
    room = get_room(room_id)
    doc = room.Document
    try:
        sketch_plane, z_height = room_sketch_plane(room)
        
        from face_intersection_drawer import get_face_polygon
        from Autodesk.Revit.UI.Selection import ObjectType
        import sys
        
        # Retrieve ActiveUIDocument from __main__ globals (injected by revit_rpc)
        uidoc = __revit__.ActiveUIDocument
        floor_refs = uidoc.Selection.PickObjects(ObjectType.Face, "Select a face")
        
        if floor_refs:
            ref = floor_refs[0]
            face = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
            get_face_polygon(face, doc=doc, sketch_plane=sketch_plane, z_height=z_height, create_model_lines=True)
    except Exception as e:
        pytest.fail(f"get_face_polygon raised {e}")


@pytest.mark.parametrize("room_id", [5139071])
@revit
def test_get_subtracted_polygon(transaction, room_id):
    room = get_room(room_id)
    doc = room.Document
    try:
        sketch_plane, z_height = room_sketch_plane(room)
        
        from face_intersection_drawer import get_face_polygon, get_subtracted_polygon
        from Autodesk.Revit.UI.Selection import ObjectType
        import sys
        
        # Retrieve ActiveUIDocument from __main__ globals (injected by revit_rpc)
        uidoc = __revit__.ActiveUIDocument
        floor_refs = uidoc.Selection.PickObjects(ObjectType.Face, "Select at least two faces to subtract")
        
        if floor_refs and len(floor_refs) >= 2:
            face1 = doc.GetElement(floor_refs[0].ElementId).GetGeometryObjectFromReference(floor_refs[0])
            face2 = doc.GetElement(floor_refs[1].ElementId).GetGeometryObjectFromReference(floor_refs[1])
            poly1 = get_face_polygon(face1)
            poly2 = get_face_polygon(face2)
            result = get_subtracted_polygon(poly1, poly2, doc=doc, sketch_plane=sketch_plane, z_height=z_height, create_model_lines=True)
            assert result is not None
    except Exception as e:
        pytest.fail(f"get_subtracted_polygon raised {e}")


@pytest.mark.parametrize("room_id", [5139071])
@revit
def test_get_intersection_polygon(transaction, room_id):
    room = get_room(room_id)
    doc = room.Document
    try:
        sp, z_height = room_sketch_plane(room)
        
        from face_intersection_drawer import get_face_polygon, get_intersection_polygon
        from Autodesk.Revit.UI.Selection import ObjectType
        import sys
        
        # Retrieve ActiveUIDocument from __main__ globals (injected by revit_rpc)
        uidoc = __revit__.ActiveUIDocument
        floor_refs = uidoc.Selection.PickObjects(ObjectType.Face, "Select at least two faces to intersect")
        
        if floor_refs and len(floor_refs) == 1:
            face1 = doc.GetElement(floor_refs[0].ElementId).GetGeometryObjectFromReference(floor_refs[0])
            poly1 = get_face_polygon(face1)
            poly2 = get_room_polygon(room, doc=doc, sketch_plane=sp, z_height=z_height, create_model_lines=False)
            result = get_intersection_polygon([poly1, poly2], doc=doc, sketch_plane=sp, z_height=z_height, create_model_lines=True)
            print(result)
            assert result is not None
    except Exception as e:
        pytest.fail(f"get_intersection_polygon raised {e}")


@pytest.mark.parametrize("room_id,expected", [
    (5139071, 4)
])
@revit
def test_auto_find_room_faces(transaction, room_id, expected):
    room = get_room(room_id)
    doc = room.Document
    try:
        sketch_plane, z_height = room_sketch_plane(room)
        
        from face_intersection_drawer import auto_find_room_faces, get_room_polygon
        
        # Get room polygon
        room_poly = get_room_polygon(room, doc=doc, sketch_plane=sketch_plane, z_height=z_height, create_model_lines=False)
        
        # Grid spacing in feet (e.g., 1.0 = ~30cm)
        grid_spacing = 1.0 
        
        # Find faces
        faces = auto_find_room_faces(
            doc=doc, 
            room=room,
            z_height=z_height, 
            room_poly=room_poly, 
            grid_spacing=grid_spacing, 
            create_ray_lines=False, 
            debug=False
        )
        
        # Assert number of faces (User will replace the expected number)
        assert len(faces) == expected, f"Expected {expected} faces, but found {len(faces)}"
    except Exception as e:
        pytest.fail(f"test_auto_find_room_faces raised {e}")
