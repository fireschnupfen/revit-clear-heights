"""
Command to apply colors from a color book (.abc / .acb) file to the current view's color fill scheme.
Expands the base palette by interpolating middle colors and uses a multi-directional HSV shift
for procedural generation to ensure uniqueness and high contrast.
"""

from revit_rpc import revit

try:
    from Autodesk.Revit.DB import (
        Transaction, ElementId, BuiltInCategory, Color
    )
    from System.Windows.Forms import OpenFileDialog, DialogResult
except ImportError:
    pass

__revit__: str


@revit
def main():
    import re
    import colorsys
    from Autodesk.Revit.DB import Transaction, ElementId, BuiltInCategory, Color
    from System.Windows.Forms import OpenFileDialog, DialogResult

    uidoc = __revit__.ActiveUIDocument
    doc = uidoc.Document
    active_view = doc.ActiveView

    # 1. Ask user to pick a color book file
    dialog = OpenFileDialog()
    dialog.Filter = "Color Book Files (*.abc;*.acb)|*.abc;*.acb|All Files (*.*)|*.*"
    dialog.Title = "Select a Color Book File"

    if dialog.ShowDialog() != DialogResult.OK:
        print("File selection cancelled.")
        return

    filepath = dialog.FileName

    # 2. Parse the file (skipping page colors)
    raw_colors = []
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            entry_pattern = re.compile(r'<colorEntry>.*?</colorEntry>', re.DOTALL)
            rgb_pattern = re.compile(
                r'<red>\s*(\d+)\s*</red>.*?<green>\s*(\d+)\s*</green>.*?<blue>\s*(\d+)\s*</blue>',
                re.IGNORECASE | re.DOTALL
            )
            for entry in entry_pattern.finditer(content):
                rgb_match = rgb_pattern.search(entry.group(0))
                if rgb_match:
                    r, g, b = int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3))
                    raw_colors.append((r, g, b))
    except Exception as e:
        print(f"Error parsing file: {e}")
        return

    if not raw_colors:
        print("No colors found.")
        return

    # 3. Expand base colors by interpolating "middle" colors
    expanded_base = []
    for i in range(len(raw_colors)):
        expanded_base.append(raw_colors[i])
        # Interpolate between current and next (looping back at the end)
        c1 = raw_colors[i]
        c2 = raw_colors[(i + 1) % len(raw_colors)]
        mid = (int((c1[0]+c2[0])/2), int((c1[1]+c2[1])/2), int((c1[2]+c2[2])/2))
        expanded_base.append(mid)

    # 4. Find the target scheme
    target_view = active_view
    if active_view.ViewTemplateId != ElementId.InvalidElementId:
        target_view = doc.GetElement(active_view.ViewTemplateId)

    active_scheme_id = ElementId.InvalidElementId
    for cat in [BuiltInCategory.OST_Rooms, BuiltInCategory.OST_Areas]:
        try:
            sid = target_view.GetColorFillSchemeId(ElementId(cat))
            if sid != ElementId.InvalidElementId:
                active_scheme_id = sid
                break
        except: continue

    if active_scheme_id == ElementId.InvalidElementId:
        print("No active color fill scheme found.")
        return

    scheme = doc.GetElement(active_scheme_id)

    # 5. Apply with multi-directional HSV adjustment
    t = Transaction(doc, "Apply Expanded Color Book")
    t.Start()
    try:
        entries = scheme.GetEntries()
        total_entries = entries.Count
        base_count = len(expanded_base)

        for i in range(total_entries):
            r, g, b = expanded_base[i % base_count]
            cycle = i // base_count

            if cycle == 0:
                entries[i].Color = Color(r, g, b)
            else:
                h, s, v = colorsys.rgb_to_hsv(r/255.0, g/255.0, b/255.0)

                # Multi-directional shift based on cycle
                # Cycle 1: desaturate/brighten, Cycle 2: saturate/darken, etc.
                if cycle % 2 == 1:
                    s = max(0.1, s * (0.7 ** ((cycle + 1) / 2)))
                    v = min(1.0, v + (1.0 - v) * 0.3)
                else:
                    s = min(1.0, s + (1.0 - s) * 0.3)
                    v = max(0.2, v * (0.7 ** (cycle / 2)))

                nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
                entries[i].Color = Color(int(nr*255), int(ng*255), int(nb*255))

        scheme.SetEntries(entries)
        t.Commit()
        print(f"Applied {total_entries} unique colors using expanded {base_count} base colors.")
    except Exception as e:
        t.RollBack()
        print(f"Error: {e}")

if __name__ == "__main__":
    main()