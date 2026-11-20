"""Create finger joints from butt-jointed panels using native Fusion features."""

from dataclasses import dataclass
from itertools import combinations
from json import loads
import math
from pathlib import Path
import traceback

import adsk.core as core
import adsk.fusion as fusion


COMMAND_ID = "Making_ParametricBoxJoint"
DESIGN_WORKSPACE_ID = "FusionSolidEnvironment"
MODIFY_PANEL_ID = "SolidModifyPanel"
ADD_INS_PANEL_ID = "SolidScriptsAddinsPanel"
ICON_FOLDER = Path(__file__).resolve().parent / "Resources"
VERSION = loads(Path(__file__).with_suffix(".manifest").read_text(encoding="utf-8"))["version"]
TOLERANCE = 1e-5  # Fusion's internal length unit is cm.
handlers = []


class JointError(Exception):
    """A geometry or input error that can be shown in the command dialog."""


class NoButtSeam(JointError):
    """The selected faces do not define a butt seam in this orientation."""


def require(result, message):
    if result is None or result is False:
        raise JointError(message)
    return result


def value(expression):
    return core.ValueInput.createByString(expression)


def outward(face):
    normal = face.geometry.normal
    if face.isParamReversed:
        normal.scaleBy(-1)
    return normal


def along(vector, direction):
    return abs(vector.dotProduct(direction)) > 1 - TOLERANCE


def line_direction(line):
    direction = line.startPoint.vectorTo(line.endPoint)
    direction.normalize()
    return direction


def coord(point, vector):
    return point.asVector().dotProduct(vector)


def planar_faces(body):
    return [face for face in body.faces if core.Plane.cast(face.geometry)]


def opposite_face(outside, faces):
    direction = outward(outside)
    candidates = [
        face for face in faces
        if outward(face).dotProduct(direction) < -1 + TOLERANCE
        and outside.geometry.origin.vectorTo(face.geometry.origin).dotProduct(direction) < -TOLERANCE
    ]
    if not candidates:
        raise JointError("Select the broad outer faces of panels with parallel inner and outer faces.")
    return max(candidates, key=lambda face: face.area)


def point_on_face(face, point):
    evaluator = face.evaluator
    success, parameter = evaluator.getParameterAtPoint(point)
    return success and evaluator.isParameterOnFace(parameter)


@dataclass
class Joint:
    receiver: object
    tabbed: object
    receiver_inside: object
    tabbed_inside: object
    butt: object
    start: object
    seam_edge: object
    direction: object
    length: float
    minimum_thickness: float


def inspect_joint(receiver, tabbed):
    if not core.Plane.cast(receiver.geometry) or not core.Plane.cast(tabbed.geometry):
        raise JointError("Select planar outside faces.")
    if receiver.body == tabbed.body or not (receiver.body.isSolid and tabbed.body.isSolid):
        raise JointError("Select two separate solid panels.")
    for face in (receiver, tabbed):
        occurrence = face.assemblyContext
        if occurrence and occurrence.isReferencedComponent:
            raise JointError("Select panels from editable components in this design.")

    faces_a = planar_faces(receiver.body)
    faces_b = planar_faces(tabbed.body)
    inner_a = opposite_face(receiver, faces_a)
    inner_b = opposite_face(tabbed, faces_b)
    direction = outward(receiver).crossProduct(outward(tabbed))
    if direction.length < TOLERANCE:
        raise NoButtSeam("The two outside faces must meet at an angle.")
    direction.normalize()

    butts = [
        face for face in faces_b
        if outward(face).dotProduct(outward(inner_a)) < -1 + TOLERANCE
        and abs(inner_a.geometry.origin.vectorTo(face.geometry.origin).dotProduct(outward(inner_a)))
        < TOLERANCE
        and point_on_face(inner_a, face.pointOnFace)
    ]
    if len(butts) != 1:
        raise NoButtSeam(
            "Pick one broad outside face on each panel. One panel end must touch "
            "the inner face of the other along a straight butt seam."
        )
    butt = butts[0]

    edges = []
    for edge in butt.edges:
        line = core.Line3D.cast(edge.geometry)
        if line and along(line_direction(line), direction):
            edges.append(edge)
    if not edges:
        raise JointError("The butt face needs a straight edge along the seam.")
    seam_edge = max(edges, key=lambda edge: edge.length)
    points = (seam_edge.startVertex.geometry, seam_edge.endVertex.geometry)
    start_coordinate = min(coord(point, direction) for point in points)
    end_coordinate = max(coord(point, direction) for point in points)
    butt_coordinates = [coord(vertex.geometry, direction) for vertex in butt.vertices]
    if (abs(start_coordinate - min(butt_coordinates)) > TOLERANCE
            or abs(end_coordinate - max(butt_coordinates)) > TOLERANCE):
        raise JointError("The butt face needs an uninterrupted straight edge for the full seam.")

    end_faces = [
        face for face in faces_b
        if outward(face).dotProduct(direction) < -1 + TOLERANCE
        and abs(coord(face.geometry.origin, direction) - start_coordinate) < TOLERANCE
    ]
    center = butt.pointOnFace
    for coordinate in (start_coordinate + 10 * TOLERANCE, end_coordinate - 10 * TOLERANCE):
        probe = center.copy()
        shift = direction.copy()
        shift.scaleBy(coordinate - coord(center, direction))
        probe.translateBy(shift)
        if not point_on_face(inner_a, probe):
            raise JointError("The receiving face must cover the full length of the straight butt seam.")
    start = max(end_faces, key=lambda face: face.area, default=None)
    thickness_a = abs(receiver.geometry.origin.vectorTo(inner_a.geometry.origin).dotProduct(outward(receiver)))
    thickness_b = abs(tabbed.geometry.origin.vectorTo(inner_b.geometry.origin).dotProduct(outward(tabbed)))

    return Joint(receiver, tabbed, inner_a, inner_b, butt, start, seam_edge, direction,
                 seam_edge.length, min(thickness_a, thickness_b))


MODES = {"count": "Tab count", "width": "Finger width", "pitch": "Tab pitch"}
EPSILON = "0.000001"  # Keeps exact fits from rounding the wrong way in floor().


@dataclass
class Sizing:
    """How the seam is divided. Lengths are in cm, Fusion's internal unit."""
    mode: str            # "count", "width" or "pitch"
    amount: float        # Tongues per seam, finger width, or tongue pitch.
    amount_expression: str
    clearance: float
    clearance_expression: str


@dataclass
class Layout:
    width: float         # Nominal finger width.
    tongues: int         # Tongues on the tabbed panel.
    end_finger: float    # Width of each receiving-panel finger at the seam ends.


def finger_layout(length, sizing):
    """The symmetric finger layout that the parametric expressions reproduce.

    Tongues and interior fingers have the nominal width and are centered on
    the seam. The receiving panel owns both ends, which keeps corners where
    three panels meet consistent, and the excess is split equally between
    its two end fingers. When an end finger would be narrower than 20 % of
    the nominal width, the layout drops a tongue so that the end fingers stay
    between 20 % and 120 % of nominal.
    """
    if sizing.mode == "count":
        width = length / (2 * sizing.amount + 1)
    elif sizing.mode == "width":
        width = sizing.amount
    else:
        width = sizing.amount / 2
    if width <= 0:
        raise JointError("The finger width must be positive.")
    tongues = math.floor((length / width + 0.6) / 2 + 1e-6)
    return Layout(width, tongues, (length - (2 * tongues - 1) * width) / 2)


def validate_fit(joint, sizing):
    if sizing.mode == "count" and not 2 <= sizing.amount <= 99:
        raise JointError("Use 2 to 99 tabs.")
    layout = finger_layout(joint.length, sizing)
    if not 2 <= layout.tongues <= 199:
        raise JointError(f"This size gives {layout.tongues} tongue(s) on a "
                         f"{joint.length * 10:.1f} mm seam. Use a size that gives 2 to 199.")
    clearance = sizing.clearance
    if clearance < 0 or clearance >= min(layout.width, joint.minimum_thickness / 2):
        raise JointError("Clearance must be nonnegative and smaller than a finger and half the panel thickness.")
    return layout


def prefix_for(design):
    index = 1
    names = [parameter.name for parameter in design.userParameters]
    while any(name.startswith(f"BoxJoint{index}_") for name in names):
        index += 1
    return f"BoxJoint{index}"


def seam_dimension(component, joint):
    sketch = require(component.sketches.addWithoutEdges(joint.tabbed), "Could not create seam sketch.")
    sketch.name = "Box joint seam length"
    projected = sketch.project2([joint.seam_edge], True)
    if len(projected) != 1:
        raise JointError("Could not link the straight seam to the length sketch.")
    line = require(fusion.SketchLine.cast(projected[0]), "The projected seam must be a line.")
    # Keep the linked line visible to the sketch solver, while excluding it from profiles.
    line.isConstruction = True
    start = line.startSketchPoint.geometry
    end = line.endSketchPoint.geometry
    text_point = core.Point3D.create((start.x + end.x) / 2 + 0.5,
                                     (start.y + end.y) / 2 + 0.5, 0)
    dimension = require(
        sketch.sketchDimensions.addDistanceDimension(
            line.startSketchPoint, line.endSketchPoint,
            fusion.DimensionOrientations.AlignedDimensionOrientation, text_point, False),
        "Could not measure the linked seam.",
    )
    sketch.isVisible = False
    return dimension.parameter.name, line


def offset_plane(component, face, direction, expression, name):
    planes = component.constructionPlanes
    plane_input = planes.createInput()
    require(plane_input.setByOffset(face, value("0.01 mm")), "Could not reference a panel face.")
    plane = require(planes.add(plane_input), "Could not create an offset construction plane.")
    offset = face.geometry.origin.vectorTo(plane.geometry.origin)
    sign = "" if offset.dotProduct(direction) > 0 else "-"
    plane.definition.offset.expression = f"{sign}({expression})"
    plane.name = name
    plane.isLightBulbOn = False
    return plane


def seam_midplane(component, joint):
    """A plane normal to the seam through its midpoint.

    The midpoint does not depend on the edge's internal direction, which
    Fusion can assign differently in different documents. A plane at a path
    parameter of 0 lands at whichever end that direction starts from.
    """
    path = require(fusion.Path.create(joint.seam_edge, fusion.ChainedCurveOptions.noChainedCurves),
                   "Could not use the straight seam edge as a path.")
    planes = component.constructionPlanes
    plane_input = planes.createInput()
    require(plane_input.setByDistanceOnPath(path, core.ValueInput.createByReal(0.5)),
            "Could not position a sketch plane at the seam midpoint.")
    plane = require(planes.add(plane_input), "Could not create the seam midplane.")
    plane.name = "Box joint seam midplane"
    plane.isLightBulbOn = False
    start = joint.seam_edge.startVertex.geometry
    end = joint.seam_edge.endVertex.geometry
    middle = core.Point3D.create((start.x + end.x) / 2, (start.y + end.y) / 2, (start.z + end.z) / 2)
    if abs(plane.geometry.origin.vectorTo(middle).dotProduct(joint.direction)) > 1e-4:
        raise JointError("Fusion placed the seam midplane away from the middle of the seam.")
    return plane


def profile(component, support, sides, name):
    """Sketch a quadrilateral section whose edge k lies on sides[k]."""
    sketch = require(component.sketches.addWithoutEdges(support), f"Could not create {name}.")
    sketch.name = name
    points = []
    for first, second in zip(sides[-1:] + sides[:-1], sides):
        intersection = require(first.geometry.intersectWithPlane(second.geometry),
                               "The panel planes do not define a corner.")
        point = require(support.geometry.intersectWithLine(intersection),
                        "The panel planes do not intersect the seam end.")
        points.append(sketch.modelToSketchSpace(point))
    lines = sketch.sketchCurves.sketchLines
    first_line = lines.addByTwoPoints(points[0], points[1])
    outline = [first_line]
    for point in points[2:] + [first_line.startSketchPoint]:
        outline.append(lines.addByTwoPoints(outline[-1].endSketchPoint, point))
    # Pin each corner to its two side planes and to the sketch plane. Line-on-
    # plane constraints alone let corners drift off the sketch plane during
    # recompute, which distorts the extruded section.
    constraints = sketch.geometricConstraints
    for line, before, side in zip(outline, sides[-1:] + sides[:-1], sides):
        for surface in (before, side, support):
            require(constraints.addCoincidentToSurface(line.startSketchPoint, surface),
                    f"Could not constrain the {name} to a panel plane.")
    if sketch.profiles.count != 1:
        raise JointError(f"The {name} did not form a single closed profile.")

    origin = sketch.sketchToModelSpace(core.Point3D.create(0, 0, 0))
    positive = sketch.sketchToModelSpace(core.Point3D.create(0, 0, 1))
    sketch.isVisible = False
    return sketch.profiles.item(0), origin.vectorTo(positive), outline


def ensure_healthy(feature):
    if feature.healthState != fusion.FeatureHealthStates.HealthyFeatureHealthState:
        raise JointError(f"{feature.name}: {feature.errorOrWarningMessage}")
    return feature


# Cutter blocks extend past panel faces that open to air, so that no cutter
# face is coincident with a finished panel face. Space cuts reach past the
# block faces, and the block's spine stays outside the receiving panel.
MARGIN = "0.5 mm"
FAR = "1 mm"
SPINE = "1.5 mm"


def driven_length(sketch_line, name):
    sketch = sketch_line.parentSketch
    start = sketch_line.startSketchPoint.geometry
    end = sketch_line.endSketchPoint.geometry
    text_point = core.Point3D.create((start.x + end.x) / 2 + 0.5, (start.y + end.y) / 2 + 0.5, 0)
    dimension = require(
        sketch.sketchDimensions.addDistanceDimension(
            sketch_line.startSketchPoint, sketch_line.endSketchPoint,
            fusion.DimensionOrientations.AlignedDimensionOrientation, text_point, False),
        f"Could not measure the {name}.",
    )
    return dimension.parameter.name


def extrude(component, section, axis, start, distance, name, cut_from=None, symmetric=False):
    """Extrude a section along the seam from the seam midplane.

    start and distance are measured along the seam direction. A symmetric
    extrude spans distance in total, centered on the midplane.
    """
    sketch_profile, normal = section[:2]
    extrudes = component.features.extrudeFeatures
    operation = (fusion.FeatureOperations.CutFeatureOperation if cut_from
                 else fusion.FeatureOperations.NewBodyFeatureOperation)
    feature_input = require(extrudes.createInput(sketch_profile, operation), f"Could not start {name}.")
    if symmetric:
        require(feature_input.setSymmetricExtent(value(distance), True), f"Could not set the depth for {name}.")
    else:
        forward = normal.dotProduct(axis) > 0
        direction = (fusion.ExtentDirections.PositiveExtentDirection if forward
                     else fusion.ExtentDirections.NegativeExtentDirection)
        if start:
            signed_start = start if forward else f"-({start})"
            feature_input.startExtent = fusion.OffsetStartDefinition.create(value(signed_start))
        extent = fusion.DistanceExtentDefinition.create(value(distance))
        require(feature_input.setOneSideExtent(extent, direction), f"Could not set the depth for {name}.")
    if cut_from:
        feature_input.participantBodies = [cut_from]
    feature = require(extrudes.add(feature_input), f"Could not create {name}.")
    feature.name = name
    return ensure_healthy(feature)


def repeat(component, feature, axis, direction, qty, spacing, name):
    patterns = component.features.rectangularPatternFeatures
    entities = core.ObjectCollection.create()
    entities.add(feature)
    pattern_input = require(
        patterns.createInput(entities, axis, value(qty), value(spacing),
                             fusion.PatternDistanceType.SpacingPatternDistanceType),
        f"Could not define {name}.",
    )
    # Fusion chooses the sense of the pattern axis. Flip the spacing when it
    # runs against the seam direction (both are in this component's space).
    positive = require(pattern_input.directionOne, f"Could not read Fusion's direction for {name}.")
    seam = direction.copy()
    require(seam.normalize() and positive.normalize(), f"Invalid direction for {name}.")
    if not along(positive, seam):
        raise JointError(f"Fusion's direction for {name} is not parallel to the seam.")
    if positive.dotProduct(seam) < 0:
        pattern_input.distanceOne = value(f"-({spacing})")
    pattern_input.patternComputeOption = fusion.PatternComputeOptions.AdjustPatternCompute
    try:
        pattern = require(patterns.add(pattern_input), f"Could not create {name}.")
    except RuntimeError as error:
        raise JointError(f"{name}: {error}") from error
    pattern.name = name
    return ensure_healthy(pattern)


@dataclass
class Panel:
    token: str
    occurrence: object  # Assembly context in the working component, or None.


@dataclass
class PreparedSeam:
    component: object
    direction: object
    axis: object
    length: str
    thickness: str
    receiver: Panel
    tabbed: Panel
    extension: object    # Profile of the butt face outline, in the tabbed panel's component.
    slot_block: tuple
    slot_space: tuple
    gap_block: tuple
    gap_space: tuple
    width: str = ""      # Layout parameter names, filled in by seam_parameters().
    tongues: str = ""


def panel_ref(component, face):
    body = face.body.nativeObject or face.body
    occurrence = face.assemblyContext if component.parentDesign.rootComponent == component else None
    return Panel(body.entityToken, occurrence)


def butt_outline(joint):
    """A sketch of the butt face's original outline, in the tabbed panel's own component.

    Extruding this outline makes the tongue stock exactly the size of the
    seam. Offset Faces instead drags slanted neighboring faces past the seam
    ends, and a face that an earlier extension enlarged would carry that
    growth along.
    """
    butt = joint.butt.nativeObject or joint.butt
    component = butt.body.parentComponent
    sketch = require(component.sketches.addWithoutEdges(butt), "Could not sketch on the butt face.")
    sketch.name = "Box joint tongue stock outline"
    projected = sketch.project(butt)
    if not projected or sketch.profiles.count != 1:
        raise JointError("The butt face outline did not form a single profile.")
    sketch.isVisible = False
    return sketch.profiles.item(0)


def prepare_seam(design, joint, gap):
    """Create every reference for one seam from the untouched source faces."""
    component = design.rootComponent
    receiver, tabbed = joint.receiver, joint.tabbed
    if receiver.assemblyContext and receiver.assemblyContext == tabbed.assemblyContext:
        joint = inspect_joint(receiver.nativeObject, tabbed.nativeObject)
        component = joint.receiver.body.parentComponent

    length, axis = seam_dimension(component, joint)
    support = seam_midplane(component, joint)
    # Zero clearance still keeps cutter faces off the mating panel faces.
    tool_gap = f"max({gap}; 0.01 mm)"

    def plane(face, expression, name):
        return offset_plane(component, face, outward(face), expression, name)

    # Outward from the receiver's inside face points into the tabbed panel.
    # Outward from the tabbed panel's inside face points along the receiver.
    a_out = plane(joint.receiver, MARGIN, "Cutter entry plane")
    a_spine = plane(joint.receiver, SPINE, "Cutter spine plane")
    a_in = plane(joint.receiver_inside, MARGIN, "Slot through plane")
    a_in_far = plane(joint.receiver_inside, FAR, "Slot space plane")
    a_gap = plane(joint.receiver_inside, tool_gap, "Tongue gap depth")
    a_gap_far = plane(joint.receiver_inside, f"{tool_gap} + {MARGIN}", "Tongue space plane")
    b_out = plane(joint.tabbed, MARGIN, "Cutter edge plane")
    b_out_far = plane(joint.tabbed, FAR, "Cutter space edge plane")
    b_in = plane(joint.tabbed_inside, MARGIN, "Tongue gap through plane")
    b_in_far = plane(joint.tabbed_inside, FAR, "Tongue gap space plane")
    slot_back = plane(joint.tabbed_inside, tool_gap, "Slot back clearance")
    slot_back_far = plane(joint.tabbed_inside, f"{tool_gap} + {MARGIN}", "Slot back space plane")

    corner = profile(component, support,
                     [joint.tabbed, joint.receiver, joint.tabbed_inside, joint.receiver_inside],
                     "Box joint corner section")
    # The first edge lies on the tabbed outer face, between the receiver's faces.
    thickness = driven_length(corner[2][0], "receiving panel thickness")

    slot_block = profile(component, support, [a_spine, b_out, a_in, slot_back], "Slot cutter section")
    slot_space = profile(component, support, [a_out, b_out_far, a_in_far, slot_back_far],
                         "Slot cutter space section")
    gap_block = profile(component, support, [a_spine, b_out, a_gap, b_in], "Tongue gap cutter section")
    gap_space = profile(component, support, [a_out, b_out_far, a_gap_far, b_in_far],
                        "Tongue gap cutter space section")

    return PreparedSeam(component, joint.direction, axis, length, thickness,
                        panel_ref(component, joint.receiver), panel_ref(component, joint.tabbed),
                        butt_outline(joint), slot_block, slot_space, gap_block, gap_space)


def seam_parameters(design, seam, index, prefix, sizing_parameter, mode, parameters):
    """Named per-seam parameters that reproduce finger_layout() in Fusion."""
    length = seam.length
    w, tongues = f"{prefix}_Seam{index}_Width", f"{prefix}_Seam{index}_Tongues"
    width = {"count": f"{length} / (2 * {sizing_parameter} + 1)",
             "width": sizing_parameter,
             "pitch": f"{sizing_parameter} / 2"}[mode]
    units = design.unitsManager.defaultLengthUnits
    definitions = [
        (w, width, units, "Nominal finger width on this seam."),
        (tongues, f"floor(({length} / {w} + 0.6) / 2 + {EPSILON})", "",
         "Tongues on the tabbed panel. End fingers stay between 20 % and 120 % of the width."),
        (f"{prefix}_Seam{index}_EndFinger", f"({length} - (2 * {tongues} - 1) * {w}) / 2", units,
         "Width of the receiving panel's finger at each end of this seam. Informational."),
    ]
    for name, expression, unit, comment in definitions:
        parameters.append(require(design.userParameters.add(name, value(expression), unit, comment),
                                  f"Could not add the parameter {name}."))
    seam.width, seam.tongues = w, tongues


def resolve_body(design, token, occurrence=None):
    bodies = []
    for entity in design.findEntityByToken(token):
        body = fusion.BRepBody.cast(entity)
        if body:
            body = body.nativeObject or body
            if body not in bodies:
                bodies.append(body)
    if len(bodies) != 1:
        raise JointError(f"Could not resolve a body by its token (found {len(bodies)}).")
    body = bodies[0]
    return body.createForAssemblyContext(occurrence) if occurrence else body


def extend_tabbed(design, seam):
    """Extrude the butt face outline through the receiving panel."""
    target = resolve_body(design, seam.tabbed.token)
    component = target.parentComponent
    extrudes = component.features.extrudeFeatures
    feature_input = require(extrudes.createInput(seam.extension, fusion.FeatureOperations.JoinFeatureOperation),
                            "Could not define the tongue stock.")
    # The outline sketch lies on the butt face, so its normal points out of
    # the panel, regardless of how the component is placed or mirrored.
    require(feature_input.setDistanceExtent(False, value(seam.thickness)),
            "Could not set the tongue stock depth.")
    feature_input.participantBodies = [target]
    volume_before = target.volume
    feature = require(extrudes.add(feature_input), "Could not extend the tabbed panel.")
    feature.name = "Tongue stock"
    ensure_healthy(feature)
    target = resolve_body(design, seam.tabbed.token)
    if not target.volume > volume_before:
        raise JointError("The tongue stock did not join the tabbed panel.")


def cutter(design, seam, block, space, block_length, space_start, space_width, spaces, name):
    """A solid block with patterned spaces cut between its teeth.

    The block is symmetric about the seam midplane. The spaces stop short of
    the spine, so the cutter stays one body for any finger count. Each space
    cut has the block as its only participant, and the block keeps its
    identity through recompute, so the pattern follows the parameters.
    """
    component = seam.component
    feature = extrude(component, block, seam.direction, None, block_length, f"{name} block",
                      symmetric=True)
    body = require(feature.bodies.item(0) if feature.bodies.count == 1 else None,
                   f"The {name} block did not make one body.")
    body.name = f"Box joint {name}"
    token = body.entityToken
    seed = extrude(component, space, seam.direction, space_start, space_width, f"{name} first space",
                   cut_from=resolve_body(design, token))
    repeat(component, seed, seam.axis, seam.direction, spaces, f"2 * {seam.width}", f"{name} spaces")
    return token


def cut_panel(design, seam, panel, tool_token, name):
    target = resolve_body(design, panel.token, panel.occurrence)
    volume_before = target.volume
    tools = core.ObjectCollection.create()
    tools.add(resolve_body(design, tool_token))
    combines = seam.component.features.combineFeatures
    feature_input = require(combines.createInput(target, tools), f"Could not define {name}.")
    feature_input.operation = fusion.FeatureOperations.CutFeatureOperation
    feature_input.isKeepToolBodies = False
    feature = require(combines.add(feature_input), f"Could not create {name}.")
    feature.name = name
    ensure_healthy(feature)
    volume_after = resolve_body(design, panel.token, panel.occurrence).volume
    if not volume_after < volume_before:
        raise JointError(f"{name} removed no material ({volume_before:.6g} to {volume_after:.6g} cm³).")
    return feature


def model_seam(design, seam, gap):
    w, tongues = seam.width, seam.tongues
    g = f"max({gap}; 0.01 mm)"
    # Positions are measured along the seam from its midplane. Tongue k of
    # Tongues is centered at (2k - Tongues + 1) * Width, and the receiving
    # panel's fingers fall halfway between.
    # Slots: the spaces are every receiving finger, including one at each end,
    # each narrowed by the clearance. That always gives at least three spaces,
    # and a pattern needs at least one instance besides its seed. The block
    # stops a little short of the outer edge of the end spaces, so they clear
    # it completely without coincident faces. Only the spine, outside the
    # receiving panel, remains beyond the outermost slot.
    overhang = f"min({MARGIN}; ({w} - {g}) / 4)"
    slots = cutter(design, seam, seam.slot_block, seam.slot_space,
                   f"2 * (({tongues} + 0.5) * {w} - {g} / 2 - {overhang})",
                   f"-{tongues} * {w} - ({w} - {g}) / 2", f"{w} - {g}", f"{tongues} + 1",
                   "slot cutter")
    cut_panel(design, seam, seam.receiver, slots, "Cut box joint slots")
    # Tongue gaps: the block spans the seam, and the spaces are the tongues.
    gaps = cutter(design, seam, seam.gap_block, seam.gap_space, f"{seam.length} + 2 * {MARGIN}",
                  f"-({tongues} - 0.5) * {w}", w, tongues, "tongue gap cutter")
    return cut_panel(design, seam, seam.tabbed, gaps, "Cut tongue gaps")


def occurrence_origin(occurrence):
    # Nested panels inherit the history of the containing occurrence.
    while occurrence:
        creation = occurrence.timelineObject
        if creation:
            if creation.isRolledBack:
                return "rolled back"
            entity = creation.entity
            if entity and entity.objectType == "adsk::fusion::ArrangeFeature":
                return "arranged"
        occurrence = occurrence.assemblyContext
    return "source"


def placed_face(design, face):
    root = design.rootComponent
    owner = face.body.parentComponent
    if owner == root:
        return face
    occurrence = face.assemblyContext
    if occurrence and occurrence.isReferencedComponent:
        raise JointError(f"{occurrence.fullPathName} is linked from another design. "
                         "Select a local copy of the panel.")
    if occurrence and occurrence_origin(occurrence) == "arranged":
        raise JointError("This face is on an Arrange result. Select the source panel, "
                         "or move the timeline marker before Arrange to see it in the box.")
    # Later occurrences do not exist yet at this insertion point. Copies
    # created by Arrange are outputs of the source definition.
    sources = [placement for placement in root.allOccurrencesByComponent(owner)
               if occurrence_origin(placement) == "source"]
    if not sources or (occurrence and occurrence not in sources):
        raise JointError(f"Could not locate {owner.name} at the current timeline marker. "
                         "Select the source panel after its creation.")
    if len(sources) > 1:
        label = occurrence.fullPathName if occurrence else owner.name
        raise JointError(f"{label} shares a component with {len(sources) - 1} other source instance(s) "
                         "at the current timeline marker. "
                         "Use Copy > Paste New to make an independent panel.")
    return face if occurrence else face.createForAssemblyContext(sources[0])


def inspect_pair(first, second):
    try:
        return inspect_joint(first, second)
    except NoButtSeam:
        return inspect_joint(second, first)


def panel_label(face):
    occurrence = face.assemblyContext
    return f"{occurrence.fullPathName} › {face.body.name}" if occurrence else face.body.name


def seam_label(joint):
    return f"{panel_label(joint.receiver)} / {panel_label(joint.tabbed)}"


def selected_joints(design, faces, sizing):
    faces = [placed_face(design, face) for face in faces]
    if len(faces) == 2:
        joint = inspect_pair(*faces)
        validate_fit(joint, sizing)
        return [joint], 0

    joints = []
    matched = set()
    for i, j in combinations(range(len(faces)), 2):
        first, second = faces[i], faces[j]
        if first.body == second.body:
            continue
        try:
            joint = inspect_pair(first, second)
        except NoButtSeam:
            continue
        except JointError as error:
            raise JointError(f"{panel_label(first)} / {panel_label(second)}: {error}") from error
        matched.update((i, j))
        if any(existing.butt == joint.butt and existing.receiver.body == joint.receiver.body
               for existing in joints):
            continue
        try:
            validate_fit(joint, sizing)
        except JointError as error:
            raise JointError(f"{seam_label(joint)}: {error}") from error
        joints.append(joint)

    if not joints:
        raise JointError("No butt seams found among the selected faces. "
                         "Pick broad outside faces on panels that meet end to face.")
    return joints, len(faces) - len(matched)


def insertion_point(timeline):
    index = timeline.markerPosition
    next_item = timeline.item(index) if index < timeline.count else None
    if index and next_item:
        previous_group = timeline.item(index - 1).parentGroup
        if previous_group and previous_group == next_item.parentGroup:
            raise JointError("Place the timeline marker between groups or ungrouped features "
                             "so the box joint can have its own group.")
    return index, next_item


def panel_body_counts(design, seams):
    tokens = dict.fromkeys(panel.token for seam in seams for panel in (seam.receiver, seam.tabbed))
    components = {}
    for component in [resolve_body(design, token).parentComponent for token in tokens] + [
            seam.component for seam in seams]:
        components[component.entityToken] = (component, component.bRepBodies.count)
    return tokens, components


def check_panels_intact(design, seams, before):
    """Each panel must remain one connected solid, with no pieces or tools left over."""
    tokens, components = before
    for token in tokens:
        body = resolve_body(design, token)
        if body.lumps.count != 1:
            raise JointError(f"{body.parentComponent.name} › {body.name} split into "
                             f"{body.lumps.count} pieces.")
    for component, count in components.values():
        if component.bRepBodies.count != count:
            raise JointError(f"{component.name} went from {count} to "
                             f"{component.bRepBodies.count} bodies. A panel split or "
                             "a cutter tooth was left behind.")
    # A cutter that missed its seam still removes material somewhere, so
    # check the result: mating panels must interlock without overlapping.
    temporary = fusion.TemporaryBRepManager.get()
    for seam in seams:
        receiver = temporary.copy(resolve_body(design, seam.receiver.token, seam.receiver.occurrence))
        tabbed = temporary.copy(resolve_body(design, seam.tabbed.token, seam.tabbed.occurrence))
        temporary.booleanOperation(receiver, tabbed, fusion.BooleanTypes.IntersectionBooleanType)
        if receiver.volume > 1e-6:
            raise JointError(f"The finished panels overlap by {receiver.volume * 1000:.3g} mm³. "
                             "A cutter did not line up with its seam.")


def create_joints(design, faces, sizing):
    if design.designType != fusion.DesignTypes.ParametricDesignType:
        raise JointError("Enable the parametric timeline before creating a box joint.")
    timeline = design.timeline
    first_index, next_item = insertion_point(timeline)
    initial_count = timeline.count
    joints, _ = selected_joints(design, faces, sizing)
    labels = [seam_label(joint) for joint in joints]
    prefix = prefix_for(design)
    gap = f"{prefix}_Clearance"
    units = design.unitsManager.defaultLengthUnits
    sizing_parameter, sizing_units, sizing_comment = {
        "count": (f"{prefix}_Tabs", "", "Tongues per seam. Use an integer from 2 to 99."),
        "width": (f"{prefix}_FingerWidth", units,
                  "Nominal finger width. Excess is split between partial tongues at both ends."),
        "pitch": (f"{prefix}_Pitch", units,
                  "Center-to-center distance between tongues. Fingers are half this wide."),
    }[sizing.mode]
    parameters = []
    group = None

    try:
        parameters.append(require(design.userParameters.add(
            gap, value(sizing.clearance_expression), units, "Total gap across each finger and slot."),
            "Could not add the clearance parameter."))
        parameters.append(require(design.userParameters.add(
            sizing_parameter, value(sizing.amount_expression), sizing_units, sizing_comment),
            f"Could not add the parameter {sizing_parameter}."))

        # Capture native references for every seam while the selected panels
        # still have their original faces. One seam may change a face the next
        # seam uses as a reference.
        seams = [prepare_seam(design, joint, gap) for joint in joints]
        for index, seam in enumerate(seams, 1):
            seam_parameters(design, seam, index, prefix, sizing_parameter, sizing.mode, parameters)
        body_counts = panel_body_counts(design, seams)
        for seam, label in zip(seams, labels):
            try:
                extend_tabbed(design, seam)
            except (JointError, RuntimeError) as error:
                raise JointError(f"{label}: {error}") from error
        for seam, label in zip(seams, labels):
            try:
                last = model_seam(design, seam, gap)
            except (JointError, RuntimeError) as error:
                raise JointError(f"{label}: {error}") from error
        check_panels_intact(design, seams, body_counts)

        last_index = last.timelineObject.index
        if (seams[0].axis.parentSketch.timelineObject.index != first_index
                or last_index < first_index
                or last_index + 1 != (next_item.index if next_item else timeline.count)):
            raise JointError("Fusion did not insert the joints as a contiguous set of features.")
        group = require(timeline.timelineGroups.add(first_index, last_index),
                        "Could not group the box joint features.")
        description = labels[0] if len(labels) == 1 else f"{len(labels)} seams"
        group.name = f"{prefix} — {description}"
        group.isCollapsed = True
        if timeline.markerPosition != group.index + 1:
            require(group.rollTo(False), "Could not position the timeline after the box joint.")
        return group, prefix
    except Exception as error:
        # Delete only this command's newly created operations, newest first.
        try:
            if group:
                require(group.deleteMe(True), "Could not delete the new joint group.")
            else:
                stop_index = next_item.index if next_item else timeline.count
                if stop_index - first_index != timeline.count - initial_count:
                    raise JointError("Could not identify the new timeline entries for cleanup.")
                for index in range(stop_index - 1, first_index - 1, -1):
                    item = timeline.item(index)
                    message = f"Could not delete the new timeline entry {item.name}."
                    if hasattr(item, "deleteObject"):
                        require(item.isDeletable and item.deleteObject(), message)
                    else:
                        entity = require(item.entity, message)
                        require(entity.deleteMe(), message)
            for parameter in reversed(parameters):
                if parameter.isValid:
                    name = parameter.name
                    parameter.deleteMe()
                    if design.userParameters.itemByName(name):
                        raise JointError(f"Could not delete the new parameter {name}.")
            if timeline.markerPosition != first_index:
                timeline.markerPosition = first_index
            if timeline.count != initial_count or timeline.markerPosition != first_index:
                raise JointError("The timeline did not return to its original size and position.")
        except Exception as cleanup_error:
            error.add_note(f"Additional error while rolling back: {cleanup_error}")
        raise


def selected_faces(command_inputs):
    field = command_inputs.itemById("faces")
    if field.selectionCount < 2:
        raise JointError("Select at least two broad outside faces.")
    return [field.selection(index).entity for index in range(field.selectionCount)]


def active_design():
    design = fusion.Design.cast(core.Application.get().activeProduct)
    if not design or design.designType != fusion.DesignTypes.ParametricDesignType:
        raise JointError("Open a parametric design to create a box joint.")
    return design


def settings(command_inputs):
    clearance = command_inputs.itemById("clearance")
    if not clearance.isValidExpression:
        raise JointError("Enter a valid clearance expression.")
    mode = selected_mode(command_inputs)
    if mode == "count":
        tabs = command_inputs.itemById("tabs").value
        amount, expression = tabs, str(tabs)
    else:
        field = command_inputs.itemById(mode)
        if not field.isValidExpression or field.value <= 0:
            raise JointError(f"Enter a positive {MODES[mode].lower()}.")
        amount, expression = field.value, field.expression
    return Sizing(mode, amount, expression, clearance.value, clearance.expression)


def selected_mode(command_inputs):
    name = command_inputs.itemById("sizing").selectedItem.name
    return next(key for key, label in MODES.items() if label == name)


def show_sizing_inputs(command_inputs):
    mode = selected_mode(command_inputs)
    for key in MODES:
        command_inputs.itemById("tabs" if key == "count" else key).isVisible = key == mode


class InputsChanged(core.InputChangedEventHandler):
    def notify(self, args):
        inputs = args.inputs
        if args.input.id == "sizing":
            show_sizing_inputs(inputs)
        if args.input.id not in ("faces", "sizing", "tabs", "width", "pitch", "clearance"):
            return
        try:
            faces = selected_faces(inputs)
            design = active_design()
            insertion_point(design.timeline)
            sizing = settings(inputs)
            joints, unmatched = selected_joints(design, faces, sizing)
            count = len(joints)
            message = "1 butt seam found." if count == 1 else f"{count} butt seams found."
            if sizing.mode != "count":
                layouts = [finger_layout(joint.length, sizing) for joint in joints]
                message += " Tongues per seam: " + ", ".join(
                    f"{layout.tongues} (end fingers {layout.end_finger * 10:.1f} mm)" for layout in layouts) + "."
            if unmatched:
                message += f" Selected faces without a seam: {unmatched}."
            message += " Click OK to create the joint group."
        except (JointError, RuntimeError) as error:
            message = str(error)
        inputs.itemById("status").text = message


class Execute(core.CommandEventHandler):
    def notify(self, args):
        try:
            inputs = args.command.commandInputs
            create_joints(active_design(), selected_faces(inputs), settings(inputs))
        except Exception as error:
            args.executeFailed = True
            args.executeFailedMessage = str(error)
            app = core.Application.get()
            app.log(f"Parametric Box Joint v{VERSION} failed\n{traceback.format_exc()}",
                    core.LogLevels.ErrorLogLevel, core.LogTypes.FileLogType)
            app.userInterface.messageBox(
                f"{error}\n\nA copy of this error is in the Fusion application log.",
                f"Parametric Box Joint v{VERSION}",
            )


class Destroy(core.CommandEventHandler):
    def __init__(self, local_handlers):
        super().__init__()
        self.local_handlers = local_handlers

    def notify(self, args):
        for handler in self.local_handlers:
            if handler in handlers:
                handlers.remove(handler)
        self.local_handlers.clear()


class Created(core.CommandCreatedEventHandler):
    def notify(self, args):
        command = args.command
        inputs = command.commandInputs
        faces = inputs.addSelectionInput("faces", "Outside faces",
                                        "Pick the broad outside faces of the panels to join, in any order.")
        faces.addSelectionFilter("PlanarFaces")
        faces.setSelectionLimits(2, 0)
        design = fusion.Design.cast(core.Application.get().activeProduct)
        units = design.unitsManager.defaultLengthUnits if design else "mm"
        sizing = inputs.addDropDownCommandInput("sizing", "Size tabs by",
                                                core.DropDownStyles.TextListDropDownStyle)
        for key, label in MODES.items():
            sizing.listItems.add(label, key == "count")
        inputs.addIntegerSpinnerCommandInput("tabs", "Tabs", 2, 99, 1, 3)
        inputs.addValueInput("width", "Finger width", units, value("20 mm"))
        inputs.addValueInput("pitch", "Tab pitch", units, value("40 mm"))
        show_sizing_inputs(inputs)
        inputs.addValueInput("clearance", "Clearance", units,
                             value("0.15 mm"))
        inputs.addTextBoxCommandInput("status", "", "Select at least two broad outside faces.", 4, True)
        local_handlers = [InputsChanged(), Execute()]
        command.inputChanged.add(local_handlers[0])
        command.execute.add(local_handlers[1])
        destroy = Destroy(local_handlers)
        command.destroy.add(destroy)
        local_handlers.append(destroy)
        handlers.extend(local_handlers)


def command_panels(ui):
    workspace = ui.workspaces.itemById(DESIGN_WORKSPACE_ID)
    if not workspace:
        return None, None
    solid_tab = workspace.toolbarTabs.itemById("SolidTab")
    modify = solid_tab.toolbarPanels.itemById(MODIFY_PANEL_ID) if solid_tab else None
    if not modify:
        modify = workspace.toolbarPanels.itemById(MODIFY_PANEL_ID)
    return modify, workspace.toolbarPanels.itemById(ADD_INS_PANEL_ID)


def clear_registration(ui):
    # The initial release could put the command on a different Modify panel
    # with the same ID. Remove that registration as well when reloading.
    for panel in (*command_panels(ui), ui.allToolbarPanels.itemById(MODIFY_PANEL_ID)):
        if panel:
            control = panel.controls.itemById(COMMAND_ID)
            if control:
                control.deleteMe()
    definition = ui.commandDefinitions.itemById(COMMAND_ID)
    if definition:
        definition.deleteMe()
    handlers.clear()


def run(_context):
    app = core.Application.get()
    ui = app.userInterface
    try:
        clear_registration(ui)
        modify, add_ins = command_panels(ui)
        if not (modify or add_ins):
            raise JointError("Could not find the Design workspace's Modify or Add-Ins panel.")
        definition = require(ui.commandDefinitions.addButtonDefinition(
            COMMAND_ID, "Parametric Box Joint",
            "Create grouped finger joints between butted panels.", str(ICON_FOLDER)),
            "Could not register the Parametric Box Joint command.")
        created = Created()
        require(definition.commandCreated.add(created), "Could not connect the box joint command.")
        handlers.append(created)
        if modify:
            button = require(modify.controls.addCommand(definition),
                             "Could not add the box joint command to Solid > Modify.")
            button.isPromotedByDefault = True
            button.isPromoted = True
        if add_ins:
            require(add_ins.controls.addCommand(definition),
                    "Could not add the box joint command to Utilities > Add-Ins.")
        app.log(f"Parametric Box Joint v{VERSION} registered in the Design workspace.")
    except Exception:
        app.log(traceback.format_exc())
        clear_registration(ui)
        raise


def stop(_context):
    clear_registration(core.Application.get().userInterface)
