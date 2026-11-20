# Parametric Box Joint for Autodesk Fusion

Create finger joints between solid panels that already meet at straight butt seams. The command places native sketches, construction geometry, extrudes, and patterns in one named timeline group per run. Fusion recomputes the features using the source faces and seam length. After creation, the joint regenerates even with the add-in stopped. [Fusion timeline groups](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_TimelineGroups_add.htm)

## Install and use

1. In Fusion, open **Design > Utilities > Add-Ins > Scripts and Add-Ins**.
2. Choose **+ > Script or add-in from device**, select the [ParametricBoxJoint](ParametricBoxJoint/) folder, and turn on **Run** for the add-in.
3. Open a parametric Part or Hybrid design. Put the timeline marker where the joint should be inserted, after the source panels exist. To update an existing Arrange, put the marker immediately before Arrange. Under **Design > Solid > Modify**, click the blue-and-gold finger joint icon or choose **Parametric Box Joint** from the Modify menu. The same command is available in **Design > Utilities > Add-Ins**.
4. In **Outside faces**, select one broad outside face from each panel you want to join, in any order. For a box with a bottom, a back, and two sides, pick those four outside faces. The command finds the butt seams among your selections. The panels remain separate bodies.
5. Choose how to size the tabs under **Size tabs by**:
   - **Tab count** sets the number of tongues on each seam. Every finger is the same width.
   - **Finger width** sets a nominal finger width.
   - **Tab pitch** sets the center-to-center distance between neighboring tongues. The nominal finger width is half the pitch.
6. Set **Clearance**. The dialog reports the seams it found and any selected faces with no matching seam. For finger width and tab pitch, it also lists each seam's tongue count and end-finger width. Click **OK** to create the group. Roll forward from the new group to recompute later features, including Arrange.

With a finger width or a pitch, a seam rarely holds a whole number of fingers. Tongues and interior fingers keep the nominal width and are centered on the seam. The excess is split equally between the two end fingers, which always belong to the receiving panel. When an end finger would be narrower than 20 % of the nominal width, the layout drops one tongue, so end fingers always stay between 20 % and 120 % of nominal. A box joint needs at least two tongues per seam. Keeping the receiving panel at both ends matters where three panels meet: if partial tongues sat at the ends, two seams sharing a corner could cut the same corner block, which can leave a tongue detached.

Expand the `BoxJoint1` timeline group to see its individual operations. To change the joint, open **Modify > Change Parameters** and edit `BoxJoint1_Clearance` and whichever of `BoxJoint1_Tabs`, `BoxJoint1_FingerWidth`, or `BoxJoint1_Pitch` the run created. The numeric suffix matches the group. Use a whole number from 2 to 99 for Tabs. Each seam also gets `BoxJoint1_SeamN_Width`, `_Tongues`, and `_EndFinger` parameters. These derived values show the layout; edit the top-level parameters, not these. Seams made in one run share the same sizing and clearance. Run the command separately for seams that need different settings or a different sizing mode.

If you already linked an earlier version from this folder, switch **Run** off and back on in Scripts and Add-Ins to reload the updated command. If Run is off, turn it on.

## Fit and source geometry

The slots are wider than the tongues by the entered clearance. A clearance of 0.15 mm gives a nominal 0.075 mm gap at each flank along the seam and 0.15 mm at the slot back and the bottom of each tongue gap. Keep clearance nonnegative and below the finger width and half the thinner panel's thickness. Below 0.01 mm, the cutters use a 0.01 mm modeling minimum so that their faces never coincide with panel faces.

The joint is built in three phases inside one timeline group. First, the add-in creates references for every seam from the untouched faces:
- section sketches on a plane through the seam's midpoint
- offset construction planes
- a driven dimension of the receiver's thickness
- a sketch of each tabbed panel's butt-face outline

Second, each outline is extruded through the receiving panel and joined to the tabbed panel. This adds tongue stock exactly the size of the seam. Third, the add-in builds two cutter bodies per seam in the root component, or in the shared component when both panels live in one occurrence. Each cutter is a solid block, centered on the midplane, with the spaces between its teeth removed by a patterned extrude cut. Native Combine Cuts then apply the cutters and consume them: one cuts slots in the receiver, the other cuts the gaps between tongues in the tabbed panel. The command fails and rolls back in three cases: a panel splits, a cutter body is left behind, or the two panels of any seam overlap after cutting.

Use constant-thickness panels with a straight, uninterrupted seam covered by the receiving face. The sketch plane for each seam is a native construction plane perpendicular to the seam at its midpoint, so the seam ends can be square or angled. [Fusion plane along a path](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_ConstructionPlaneInput_setByDistanceOnPath.htm) The two panels can be bodies in one component or in separate, independent local components. For separate components, use the root assembly context. Use panel dimension expressions that keep the faces in contact when resizing the source geometry. Edits that remove referenced faces or the seam edge can require repairing references or recreating that group.

Copies created by **Arrange** follow the geometry of the source panels. Select the source faces while the panels still form the butt seam. The shared-panel check skips rolled-back occurrences and copies identified as Arrange results. [Autodesk’s Arrange feature guide](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ArrangeFeature_UM.htm)

A regular component **Paste** creates another occurrence of the same component. Editing the component would change every occurrence. If the dialog names a shared panel, make an independent copy with **Copy > Paste New**, place it at the seam, and remove the original occurrence from that position. If the old occurrence has assembly joints, reattach them to the replacement. An external link is reported separately. [Autodesk’s component-copy workflow](https://help.autodesk.com/cloudhelp/ENU/Fusion-Assemble/files/GUID-9EDE876B-6A97-47E9-AB87-F27ADFFDCEE9.htm)

The joint uses square slot profiles in the panel face. A flat CNC router workflow requires its own cutter-corner relief. Add roundovers that run through the new fingers after creating the joint so they act on its final edges.

## Status

Test logs, scripts, and design archives under `results/` live in the author's local workspace and are not part of this repository.

**Version 0.3.0 (2026-11-20) adds finger-width and tab-pitch sizing and fixes a missed seam.** The user's toolbar run of v0.2.0 on the original Oiler Shelf built four of five seams. The Back / Side(Mirror) seam silently produced nothing. Its tabbed panel has no square end, so v0.2.0 placed the sketch plane "on the path at distance 0". That plane follows the seam edge's internal direction, which Fusion assigned differently in the original document than in the export, so the cutters were built off the end of the seam. v0.3.0 places every seam's sketches on the seam midplane, and it adds a guard that fails if a seam's panels overlap after cutting. Live tests passed in all three sizing modes, on the shelf and on synthetic boxes, through edits of each parameter. v0.3.0 results (`results/2026-11-20-v0.3.0/RESULTS.md`)

**Version 0.2.0 (2026-11-20) replaced the modeling approach.** Live tests through Fusion's local MCP server passed on fresh imports of the Oiler Shelf export and on synthetic boxes. All five shelf seams built in either face order and survived eight parameter edits. Each panel stayed a single body, with no leftover tools, no interference, and `Arrange3` healthy. Synthetic boxes passed with panels in separate components and with panels in one component. Recompute after edits gave the same volumes as a fresh build. The remaining acceptance step is a toolbar run on the original Oiler Shelf document. v0.2.0 results (`results/2026-11-20-v0.2.0/RESULTS.md`) · [Handoff](HANDOFF.md)

The paragraphs below describe the earlier v0.1.x approach, which joined a tongue blank into the tabbed panel.

**Superseded open issue on Oiler Shelf (v0.1.14).** Fusion confirmed that the latest toolbar run loaded v0.1.14. It failed the final body-count or volume check on the Back / mirrored Side seam. The same version passed on a fresh export. Capture the individual guard measurements from the real command before changing geometry again. [Handoff and reproduction steps](HANDOFF.md)

Version 0.1.14 passed live verification on a fresh local export of the open Oiler Shelf v7 on 2026-11-20. Fusion built `BoxJoint3` with five seams and 122 native operations at marker 13 before `Arrange3`. The test also passed inside a Fusion command transaction with reversed face-selection order. All joint features were healthy after changing the tab count from 3 to 4 and clearance from 0.15 mm to 0.25 mm. The timeline rolled to its end and `Arrange3` remained healthy.

Version 0.1.13 passed on the recovery copy. In the original design, Fusion reported “No target body” during the extrude that removed the outer rail on the Back / mirrored Side seam. Version 0.1.14 creates a separate rail cutter and subtracts it with a native Combine Cut that explicitly targets the panel. The release version also includes its version number in failure dialogs and logs.

The tongue blank is now patterned in the receiving panel's component alongside the slot cutter. For separate components, a native body-copy feature transfers the tool to the tabbed panel and a Remove feature consumes the original tool. A Combine joins the transferred blank to the intended panel and a targeted cut removes its outer rail. The v0.1.12 trial reached the gap pattern on the mirrored side and failed in Fusion's pattern solver. The v0.1.11 trial failed earlier when Fusion left the seed extrude separate from the temporary tool. Both trials restored marker 13 of 14 and removed the new parameters.

On 2026-11-20, all five v0.1.10 Oiler Shelf trials restored the original timeline marker and count and removed the new parameters after failure. Fusion left the first tongue separate at the Back / mirrored Side seam even though the tongue and panel intersected by 0.00293 cm³ at the original 0.01 mm overlap and by 0.293 cm³ with 1.00 mm overlap. Changing seam order or starting at the opposite endpoint also failed.

The v0.1.9 scratch test traced the mirrored tongue failure to a separate body. The mirrored panel stayed at 151.724 cm³ and its component went from one body to two. The scratch runner reported incomplete cleanup. Version 0.1.10 added checks for timeline deletion and parameter removal.

The Oiler Shelf recovery copy detected all five seams in v0.1.8. Fusion trimmed the shoulders and built four joints before the add-in failed at the Back / mirrored Side tongue. Version 0.1.9 checks the selected native body's volume and component body count, resolves body proxies before comparing them, and reports what Fusion returned if the check fails. Mocked tests passed for native and proxy results, empty results with confirmed volume growth, and rejection when the wrong body is returned or the target volume does not increase.

Oiler Shelf passed slot cutter creation and subtraction in v0.1.7, then failed in **Trim butt shoulder** on the mirrored side with Fusion’s “No target body” error. Version 0.1.8 uses the selected butt face’s native Offset Faces feature before modeling slots and tongues. Mocked tests cover native face resolution, inward offsets, and batch rollback.

The v0.1.6 shelf rerun still failed in **Pattern slots** at the Back / mirrored Side seam. Version 0.1.7 builds the repeating slot profiles on one continuous cutter body outside the panel and uses one native Combine Cut to subtract it. The cut consumes the cutter. The timeline, named parameters, and rollback remain native. Mocked tests passed for multi-seam cutter creation, single-body subtraction, and rollback. The half-box reached shoulder trimming during its v0.1.7 retry.

Fusion logged a failed rectangular pattern on the Back / mirrored Side seam on 2026-11-20. Version 0.1.6 determines the spacing sign from Fusion’s reported pattern direction in the target component’s coordinates. Failures show the seam and failing pattern step in a message box and write the traceback to Fusion’s application log. Pattern direction for a reflected component, failure reporting, and rollback passed in mocked tests. The subsequent shelf retest failed during slot patterning.

Version 0.1.5 accepts straight seams with non-square ends. Detection, endpoint choice, and the construction plane inputs passed in mocked tests. The angled-end workflow still needs a live Fusion test.

Version 0.1.4 accepts more than two faces and groups all discovered seams from one run. Discovery, grouping, and rollback after a failed later seam passed with a mocked Fusion API. The multi-seam workflow still needs a live Fusion test.

Version 0.1.2 uses a single two-face selector, accepts either pick order, and reports the geometry check in the dialog. Shared-panel messages identify the selected occurrence and the number of other instances. Selection order, validation messages, component lookup, and execution error handling were checked against a mocked Fusion API. The user confirmed the toolbar command opened in v0.1.1. Version 0.1.3 supports insertion at the timeline marker and excludes later occurrences and Arrange outputs from the shared-panel check. Insertion, rollback, and the occurrence rules were checked with a mocked Fusion API. A live Arrange recompute still needs verification.

Tested in Autodesk Fusion 2706.1.17 on macOS on 2026-11-20. Native recompute passed after changes to panel height, both stock thicknesses, tab count, and clearance. A 2 mm upstream fillet away from the seam survived. Separate local components, two independently grouped corners on one receiving panel, and a roughly 76° corner also passed. No parts have been cut.

Next step for a shop trial is to cut a short joint coupon in the intended stock and adjust the clearance for the measured fit.
