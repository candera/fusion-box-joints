# Parametric Box Joint handoff

**Status on November 20, 2026.** Version 0.3.0 is current.

- The user's toolbar run of v0.2.0 on the original Oiler Shelf built four of five seams. Back / Side(Mirror) silently did nothing, because its sketch plane landed at the wrong end of the seam. v0.3.0 sketches every seam on its midplane and adds a guard that fails when a seam's panels overlap after cutting.
- v0.3.0 also adds finger-width and tab-pitch sizing. Their fractional layout keeps the receiving panel at both seam ends and spreads the excess across the end fingers, which stay between 20 % and 120 % of nominal.
- Live tests passed in all three modes. v0.3.0 results (`results/2026-11-20-v0.3.0/RESULTS.md`) · v0.2.0 results (`results/2026-11-20-v0.2.0/RESULTS.md`)

**Not yet verified:**
- the v0.3.0 dialog (the sizing dropdown and its show/hide behavior), since the tests call `create_joints()` directly
- a toolbar run of v0.3.0 on the original Oiler Shelf; the document still holds the v0.2.0 group `BoxJoint3 — 5 seams` with the broken seam, which should be deleted or undone first
- physical fit

## How the joint is built

All of the modeling code is in [`ParametricBoxJoint.py`](ParametricBoxJoint/ParametricBoxJoint.py). The section from `profile()` through `model_seam()` is new in v0.2.0. The parts that select seams and faces (`inspect_joint`, `selected_joints`, `placed_face`) and the rollback in `create_joints()` are unchanged from v0.1.14.

1. **`prepare_seam()`, for every seam before any panel changes.** It creates:
   - the seam-length sketch, which provides the pattern axis and the length parameter
   - the seam midplane (`seam_midplane()`)
   - offset construction planes from the four panel faces
   - a corner section with a driven dimension of the receiver's thickness
   - four quadrilateral section sketches, a block and a space section for each cutter; `profile()` pins each corner point to its two side planes and to the sketch plane with `addCoincidentToSurface`
   - a linked sketch of the butt face's outline in the tabbed panel's own component (`butt_outline()`)

   `seam_parameters()` then adds `<prefix>_SeamN_Width`, `_Tongues`, and `_EndFinger`, and the user parameters reproduce `finger_layout()`.
2. **`extend_tabbed()`.** Each butt outline is extruded by the receiver's thickness and joined to the tabbed panel. The extrude lives in the panel's own component and works in the mirrored component.
3. **`model_seam()`.** For each seam, `cutter()` creates a solid block in the working component, which is root for separate occurrences, symmetric about the midplane. It cuts one space between teeth and patterns that cut, with the block as the cut's only participant. The spaces stop short of a spine outside the receiver, so the cutter stays one body. Tongue k is centered at `(2k − Tongues + 1)·Width` from the midplane.
   - The slot cutter's spaces are every receiving finger, end fingers included, so there are always at least three. The block stops just short of the outer edge of the end spaces.
   - The tongue-gap cutter's spaces are the tongues.

   `cut_panel()` applies each cutter with a native Combine Cut that consumes the tool.
4. **`check_panels_intact()`.** Before grouping, the command checks three conditions. Each touched panel must be one lump. The body count of every involved component must be unchanged. The two panels of each seam must not overlap. If any check fails, the command raises an error and rolls back.

These Fusion behaviors were measured during development, and they drive the design:

- A construction plane at path distance 0 follows the edge's internal direction, which can differ between documents. Use the midpoint.
- A pattern needs at least one intersecting instance besides its seed.
- A Combine Cut works with a root tool body and a proxy target in another occurrence, including the mirrored Side.
- Patterned Joins and Joins with frozen participants are unreliable after parameter edits.
- A root pattern of a cut on a proxy body fails.
- An extrude cut that misses its participant fails with "No target body."

Details (`results/2026-11-20-v0.2.0/RESULTS.md`)

## Driving Fusion from the terminal

Fusion 2706 runs a local MCP server at `http://127.0.0.1:27182/mcp`. [`tools/fusion_mcp.py`](tools/fusion_mcp.py) sends a Python file to it with `fusion_mcp_execute`/`script`. The file must define `run(_context)`, and the client prints whatever the script prints. Pass `--read-only` for inspection scripts; these can also run while a command dialog is open. `--tool fusion_mcp_read '{"queryType":"screenshot","direction":"iso-top-right"}' --save-images DIR` saves a viewport PNG. The test scripts in `results/2026-11-20-v0.2.0/` import fresh scratch documents. Close each one with `close(False)` after the run, checking its `creationId` first so you never close the user's documents. This replaces the v0.1.x QA script that polled a trigger file.

The mock tests (`results/2026-11-20-oiler-shelf/mocks/`) target v0.1.x internals (`cut_slots`, `add_tongues`, `trim_butt_shoulder`), so they no longer apply to the modeling code. The registration, dialog, and timeline mocks may still be useful after their imports are updated.

## Next steps

1. Reload the add-in by turning Run off and on, so Fusion loads 0.3.0. On the original Oiler Shelf, delete the v0.2.0 `BoxJoint3` group, put the marker before `Arrange3`, and run the toolbar command in each sizing mode. Expect `BoxJoint4 — 5 seams`, because `BoxJoint3_*` parameters remain until the old group and its parameters are removed. Check the dialog's tongue-count report.
2. Run an angled-corner case live. The midplane section design should handle it.
3. Cut a short joint coupon in the intended stock and tune the clearance.

The previous handoff, which centers on the v0.1.14 failure guard, is archived at `results/2026-11-20-oiler-shelf/HANDOFF-v0.1.14.md`.
