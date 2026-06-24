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
    from Autodesk.Revit.DB import BuiltInCategory, ElementId, Plane, SketchPlane, XYZ, BuiltInParameter, Transaction, \
    Reference
    from Autodesk.Revit.UI.Selection import ObjectType
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


@revit
def func_get_face_polygon(room_id, floor_ref_string):
    room = get_room(room_id)
    doc = room.Document
    sketch_plane, z_height = room_sketch_plane(room)
    ref = Reference.ParseFromStableRepresentation(doc, floor_ref_string)
    face = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
    polys = get_face_polygon(face, doc=doc, sketch_plane=sketch_plane, z_height=z_height, create_model_lines=False)
    return polys.area, doc.GetElement(ref.ElementId).LookupParameter("Fläche").AsDouble()

@pytest.mark.parametrize("room_id,floor_ref_string", [ # Correct
    (5139071, "adc6acde-15c5-4e63-9a28-26ff87808d74-01708d10:0:SURFACE")
])
def test_get_face_polygon(transaction, room_id, floor_ref_string):
    value, excpeted = func_get_face_polygon(room_id, floor_ref_string)
    assert round(value) == round(excpeted)


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


@pytest.mark.parametrize("room_id,len_floor_faces,len_ceiling_faces", [
    (5139071, 3, 2)
])
@revit
def test_auto_find_room_faces(transaction, room_id, len_floor_faces, len_ceiling_faces):
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
        floor_faces, ceiling_faces = auto_find_room_faces(
            doc=doc, 
            room=room,
            z_height=z_height, 
            room_poly=room_poly, 
            grid_spacing=grid_spacing, 
            create_ray_lines=False, 
            debug=False
        )
        
        # Assert number of faces (User will replace the expected number)
        assert len(floor_faces) == len_floor_faces, f"Expected {len_floor_faces} faces, but found {len(floor_faces)}"
        assert len(ceiling_faces) == len_ceiling_faces, f"Expected {len_ceiling_faces} faces, but found {len(ceiling_faces)}"
    except Exception as e:
        pytest.fail(f"test_auto_find_room_faces raised {e}")


@pytest.mark.parametrize("room_id,floor_ref_string,ceiling_ref_string,expected_min,expected_max", [
    (5139071, "98c13167-7fd2-4c36-9595-69e41fbc742c-013f4e25:1:SURFACE",
     "3393eaa1-a0ba-4a41-ac18-e76317201435-0153784f:0:SURFACE", 2.630, 2.630), # Correct
    (5139071, "adc6acde-15c5-4e63-9a28-26ff87808d74-01708d10:0:SURFACE",
     "9993fd19-1dfb-40a2-a38b-8c58b34923e9-01a2ac84:1:SURFACE", 2.600, 2.600)
])
@revit
def test_evaluate_clear_height_manual(transaction, room_id, floor_ref_string, ceiling_ref_string, expected_min, expected_max):
    room = get_room(room_id)
    doc = room.Document
    try:
        sketch_plane, z_height = room_sketch_plane(room)

        room_poly = get_room_polygon(room, doc=doc, sketch_plane=sketch_plane, z_height=z_height, create_model_lines=False)
        
        all_clear_heights = []

        floor_ref = Reference.ParseFromStableRepresentation(doc, floor_ref_string)
        ceil_ref = Reference.ParseFromStableRepresentation(doc, ceiling_ref_string)

        floor_face = doc.GetElement(floor_ref.ElementId).GetGeometryObjectFromReference(floor_ref)
        floor_poly = get_face_polygon(floor_face)

        ceil_face = doc.GetElement(ceil_ref.ElementId).GetGeometryObjectFromReference(ceil_ref)
        ceil_poly = get_face_polygon(ceil_face)

        _, heights = evaluate_face_pair(
            doc=doc,
            floor_face=floor_face,
            floor_poly=floor_poly,
            ceiling_face=ceil_face,
            ceiling_poly=ceil_poly,
            room_poly=room_poly,
            sketch_plane=sketch_plane,
            z_height=z_height,
            create_debug_lines=False,
            debug=True
        )
        if heights:
            all_clear_heights.extend(heights)

        if all_clear_heights:
            min_h = min(all_clear_heights)
            max_h = max(all_clear_heights)
            print(f"Evaluated heights: min={min_h:.3f}m, max={max_h:.3f}m")
            
            # Allow a small tolerance (e.g. 0.01m)
            assert expected_min - 0.01 <= min_h <= expected_min + 0.01, f"Min height {min_h:.3f} does not match expected {expected_min}"
            assert expected_max - 0.01 <= max_h <= expected_max + 0.01, f"Max height {max_h:.3f} does not match expected {expected_max}"
        else:
            pytest.fail("No clear heights evaluated from the selected faces.")
            
    except Exception as e:
        pytest.fail(f"test_evaluate_clear_height_manual raised {e}")
