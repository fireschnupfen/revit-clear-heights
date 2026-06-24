from revit_rpc import revit


@revit
def print_selected_face_refs():
    from Autodesk.Revit.UI.Selection import ObjectType
    from Autodesk.Revit.Exceptions import OperationCanceledException

    # Assuming standard Revit Python environment variables are available
    uidoc = __revit__.ActiveUIDocument
    doc = uidoc.Document

    try:
        # Prompt the user to select faces
        references = uidoc.Selection.PickObjects(ObjectType.Face, "Select faces. Click 'Finish' or press Esc to stop.")
        
        print(f"--- Selected {len(references)} face(s) ---")
        for i, ref in enumerate(references):
            # Convert the reference to a stable string
            stable_string = ref.ConvertToStableRepresentation(doc)
            print(f"[{i+1}] Element ID: {ref.ElementId}")
            print(f"    Ref String: {stable_string}")
            
    except OperationCanceledException:
        print("Selection cancelled by user.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    print_selected_face_refs()
