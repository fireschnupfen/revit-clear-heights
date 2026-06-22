# Project: Revit Room Clear Height Calculation - Python Programming

This file contains custom instructions, preferences, and important context for the AI assistant to follow throughout this project.

## Project Stack & Environment
* **Language:** Python 3
* **Revit API Version:** Revit 2026
* **Target Environment:** Revit API (running inside a live Revit environment)
* **OS:** Windows
* **Revit Integration:** Remote execution via the `revit-rpc` package

## Active Project Goal
* **Goal:** The current goal is to implement a tool that calculates the clear heights of all selected rooms in the active Revit model and writes them into a parameter for each room called "Lichte_Raumhöhe". The parameter should display the calculated clear heights in meters with a precision of 2 decimal points. Some rooms may have multiple clear heights, in that case they should be written as a range from the minimum to the maximum, like **2,42m - 3,21m** for example. 
* **Calculation methodology:** I will outline the proposed method for calculating the clear height for a room: We take the 2D room boundary, that is also visible in plans, make a surface from that on the height of the room's level 'Height for Calculation', propulate that surface with a dense grid of points, and then calculate the clear height on each point by casting a ray upwards and downwards using the ReferenceIntersector class with the 3D view called 'RAY_CASTING_VIEW' as argument for the constructor. We will calculate the clear height at any given point by summing up the distance both the upward and downward ray traveled until they hit a Floor, Ceiling, or Roofs with built-in parameter "Room Bounding" set to "True". When we calculated the clear height at all points, we want to find out how much of the room area roughly is that heigh. We can easily do that by calculating the number of points with any given evaluated clear height and dividing them by the total number of points. We need this value to filter out clear heights, with a threshold of 0.08 percent of the total surface. Another thing to consider is the length of the rays we cast. The length should be larger then, but proportional to each room's built-in clear height parameter. Meaning a room with twice the clear height should have a twice as long ray length.

## Expected Command Architecture & Output
* **Executable Entry Point:** The command must be defined in the file main.py. The file needs to contain a function called main, which is wrapped in the `@revit` decorator and serves as the main entry point. The function cannot accept any arguments to guarantee compatibility with the integration test which is already in place. The file must contain `if __name__ == "__main__":` block at the bottom where to main function is called. See an example below. You can overwrite the content of the file, it only serves as a placeholder.
* **Example Command Structure:**
  ```python
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
  ```

## Debugging, Testing and Validation
* **Integration Tests:** I already set up the necessary integration tests to validate the command's functionality. Run `python -m pytest -x` to execute the tests and validate your work. Keep working until all tests pass. When one of the parametrized tests fails, run the test only with that parameter like `python -m pytest -x -k "test_parameter_name"`, where `parameter_name` is the name of the parameter you want to test, until this specific test succeeds. As soon as this is the case go back to calling `python -m pytest -x` until you hit another parameter that fails. Then test that specific parameter explicitly again. Repeat the procedure until `python -m pytest -x` succeeds without failures.
* **Debugging:** If you encounter any errors during development, use the `print()` function to output relevant information to the console for debugging purposes. You can also raise exceptions with informative messages to help identify issues.

## Revit RPC 
The `revit_rpc` package facilitates remote execution of Python code within the active Revit session. The core concept is that the `@revit` decorator sends the decorated function to be executed inside Revit and returns the results. .NET native objects are wrapped in a proxy client-side, but can be passed as arguments to other `@revit`-decorated functions to be unwrapped on the server side. The `revit_rpc` module's docstring contains comprehensive instructions on how to properly use the package and interact with Revit.

## Additional Rules and Guidelines:
1. **Revit Context:** Inside any `@revit` decorated function, the active Revit session is exposed via the global `__revit__` object, which represents the active `UIApplication`.
   * Active UI Document: `__revit__.ActiveUIDocument`
   * Active Document: `__revit__.ActiveUIDocument.Document`
2. **__revit__:** the variable `__revit__` serves as the main entry point to interact with Revit from within `@revit` decorated functions, but doesn't exist outside the Revit API context and therefore will trigger unresolved reference errors. To avoid that, **typehint the variable in every file you create after the imports like that: `__revit__: str`.**
2. **Imports:** Standard Revit namespaces (`Autodesk.Revit.DB`, etc.) are only available within the Revit environment. To avoid local import errors, **always import them within a try-except block that catches an ImportError**.
3. **No clr module imports:** You don't need to import the `clr` module or use `clr.AddReference` before importing `Autodesk` and `System` modules.
4. **Collection Arguments (.NET Lists):** 
   * Do NOT pass Python lists directly to Revit API methods that require collections as input arguments (due to the `pythonnet` environment, not IronPython).
   * Use a `System.Collections.Generic.List` instead.
   * You cannot initialize a .NET `List` directly with a Python list. You must instantiate an empty generic list first, then iterate over your Python list to `.Add()` each item.
   * *Example:*
     ```python
     from System.Collections.Generic import List
     from Autodesk.Revit.DB import ElementId
    
     ids_list = List[ElementId]()
     for id in python_list:
         ids_list.Add(id)
     ```
