"""
Command to create Generic Model DirectShape elements from selected rooms' geometry.

This creates a solid DirectShape representing the 3D volume of each selected room,
which can be useful for visualization and analysis purposes.
"""

from revit_rpc import revit

try:
    from Autodesk.Revit.DB import (
        FilteredElementCollector, Transaction, DirectShape, ElementId,
        BuiltInCategory, SpatialElementGeometryCalculator,
        SpatialElementBoundaryOptions, SpatialElementBoundaryLocation,
        XYZ, CurveLoop, GeometryCreationUtilities, SolidOptions
    )
    from System.Collections.Generic import List
except ImportError:
    pass

__revit__: str


@revit
def main():
    from Autodesk.Revit.DB import (
        Transaction, DirectShape, ElementId, BuiltInCategory,
        SpatialElementGeometryCalculator
    )
    from System.Collections.Generic import List
    import Autodesk.Revit.DB as DB
    
    doc = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument
    
    # Get selected elements
    selection = uidoc.Selection.GetElementIds()
    if selection.Count == 0:
        print("No elements selected. Please select one or more rooms.")
        return
    
    # Get the Generic Models category
    category_id = ElementId(BuiltInCategory.OST_GenericModel)
    
    # Start transaction
    t = Transaction(doc, "Create DirectShape from Rooms")
    t.Start()
    
    try:
        created_count = 0
        calculator = SpatialElementGeometryCalculator(doc)
        
        for element_id in selection:
            room = doc.GetElement(element_id)
            
            # Check if it's a room
            if room.Category is None or room.Category.Id.Value != int(BuiltInCategory.OST_Rooms):
                print(f"Element {element_id.Value} is not a room, skipping.")
                continue
            
            # Get room name and number for naming the DirectShape
            room_name = room.get_Parameter(DB.BuiltInParameter.ROOM_NAME).AsString() or "Unnamed"
            room_number = room.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER).AsString() or ""
            ds_name = f"Room {room_number} - {room_name}" if room_number else f"Room - {room_name}"
            
            # Get the room's solid geometry
            try:
                results = calculator.CalculateSpatialElementGeometry(room)
                if results:
                    solid = results.GetGeometry()
                    if solid:
                        # Create the DirectShape
                        ds = DirectShape.CreateElement(doc, category_id)
                        
                        # Set the geometry
                        geometry_list = List[DB.GeometryObject]()
                        geometry_list.Add(solid)
                        ds.SetShape(geometry_list)
                        
                        # Set name
                        try:
                            ds.Name = ds_name
                        except:
                            pass
                        
                        created_count += 1
                        print(f"Created DirectShape for Room {room.Id}: {ds_name}")
                    else:
                        print(f"Room {room.Id} has no solid geometry.")
                else:
                    print(f"Could not calculate geometry for Room {room.Id}.")
            except Exception as e:
                print(f"Error processing Room {room.Id}: {e}")
        
        t.Commit()
        print(f"\nSuccessfully created {created_count} DirectShape element(s).")
        
    except Exception as e:
        t.RollBack()
        print(f"Error during execution: {e}")
        raise


if __name__ == "__main__":
    main()
