from revit_rpc import revit

# ... imports and business logic functions ...

__revit__: str


@revit
def main():
    from Autodesk.Revit.DB import Transaction
    doc = __revit__.ActiveUIDocument.Document

    # Create and manage transaction (no 'with' statements)
    t = Transaction(doc, "Command Name")
    t.Start()
    try:
        # Execute command functionality here
        # ...
        t.Commit()
        print("Command executed successfully.")
    except Exception as e:
        t.RollBack()
        print(f"Error during execution: {e}")
        raise


if __name__ == "__main__":
    main()
