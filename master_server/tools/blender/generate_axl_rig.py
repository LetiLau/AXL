#!/usr/bin/env python3
"""
A-X-L & Bolt Cybernetic Dog 3D Generator (Blender 4.x / 5.x)
Generates:
1. Highly stylized low-to-mid poly Cyber-Dog Mesh (Bolt expressive anatomy + AXL mechanical armor).
   - Expressive Bolt head: curved cranium, sloped brow, tapered muzzle with cyber-nose.
   - Tall upright alert German Shepherd / White Swiss Shepherd ears with recessed inner canal.
   - Big Disney-style cybernetic ocular rings (expressive eyes).
   - Articulated lower jaw and tongue for speech, panting, growling, and barking.
   - High-tech glowing collar around the neck.
   - Athletic canine body (deep chest, tucked waist, digitigrade cyber legs, segmented sickle tail).
2. Bone Rig / Armature with full articulation (neck, head, jaw, tongue, ear.L, ear.R, eye.L, eye.R, legs, tail).
3. Pre-baked Keyframe Animations:
   - 'Idle_Breath' (Subtle canine breathing, gentle tail sway, alert micro-movements)
   - 'Wake_Alert' (Bolt head-tilt, ears perked high, attentive posture)
   - 'Growl_Angry' (Stranger alert: jaw snarl, ears swept back, forward aggressive hunch)
   - 'Happy_Dance' (Joyful victory dance: paws stepping, tail wagging, mouth open with tongue out)
   - 'Speak_Talk' (Subtle jaw and ear cadence for TTS speech synthesis)
4. Saves editable Blender project: axl_dog_rigged.blend
5. Exports production glTF 2.0 Web asset: axl_dog_demo.glb
"""

import math
from pathlib import Path
import bpy
import bmesh
from mathutils import Vector, Euler, Matrix

def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def create_cylinder(name, location, radius, depth, vertices=12, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices,
        radius=radius,
        depth=depth,
        location=location,
        rotation=rotation
    )
    obj = bpy.context.active_object
    obj.name = name
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return obj

def create_torus(name, location, major_r, minor_r, major_seg=16, minor_seg=8, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(
        major_radius=major_r,
        minor_radius=minor_r,
        major_segments=major_seg,
        minor_segments=minor_seg,
        location=location,
        rotation=rotation
    )
    obj = bpy.context.active_object
    obj.name = name
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return obj

def create_icosphere(name, location, radius, subdivisions=2):
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=radius)
    for v in bm.verts:
        v.co += Vector(location)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    mesh.validate()
    return obj

def create_bolt_cranium():
    """Builds a sculpted canine cranium (rounded back of head, tapering forward to brow stop)."""
    mesh = bpy.data.meshes.new("Cranium_Mesh")
    obj = bpy.data.objects.new("Head_Cranium", mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    # Create base icosphere and deform into canine skull
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.22)
    for v in bm.verts:
        # Scale along Y (length), taper front
        x, y, z = v.co.x, v.co.y, v.co.z
        if y > 0:
            # Front of head: slope down brow, taper cheeks
            v.co.z *= 0.85
            v.co.x *= (1.0 - y * 0.4)
        else:
            # Back of head: rounded occiput
            v.co.z *= 0.95
        # Elongate slightly along Y
        v.co.y *= 1.25
        # Shift to world head position
        v.co.x += 0.0
        v.co.y += 0.70
        v.co.z += 0.98

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    mesh.validate()
    return obj

def create_bolt_snout():
    """Builds the upper muzzle with defined nasal bridge and cyber-nose."""
    mesh = bpy.data.meshes.new("Snout_Mesh")
    obj = bpy.data.objects.new("Snout_Muzzle", mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    # Loft rings for the muzzle: Base (near eyes), Mid, and Nose Tip
    # Ring 0 (Base near brow)
    r0 = [
        Vector((-0.10, 0.80, 0.96)),
        Vector((0.10, 0.80, 0.96)),
        Vector((0.11, 0.80, 0.89)),
        Vector((-0.11, 0.80, 0.89)),
    ]
    # Ring 1 (Mid snout)
    r1 = [
        Vector((-0.075, 0.95, 0.94)),
        Vector((0.075, 0.95, 0.94)),
        Vector((0.08, 0.95, 0.88)),
        Vector((-0.08, 0.95, 0.88)),
    ]
    # Ring 2 (Nose tip)
    r2 = [
        Vector((-0.045, 1.07, 0.93)),
        Vector((0.045, 1.07, 0.93)),
        Vector((0.05, 1.07, 0.89)),
        Vector((-0.05, 1.07, 0.89)),
    ]

    v0 = [bm.verts.new(p) for p in r0]
    v1 = [bm.verts.new(p) for p in r1]
    v2 = [bm.verts.new(p) for p in r2]

    # Skin between ring 0 and 1
    for i in range(4):
        ni = (i + 1) % 4
        bm.faces.new([v0[i], v0[ni], v1[ni], v1[i]])
    # Skin between ring 1 and 2
    for i in range(4):
        ni = (i + 1) % 4
        bm.faces.new([v1[i], v1[ni], v2[ni], v2[i]])
    # Front cap (nose tip)
    bm.faces.new(v2)

    # Add Cyber Nose block (tartufo) on top of tip
    nose_verts = [
        bm.verts.new(Vector((-0.04, 1.05, 0.95))),
        bm.verts.new(Vector((0.04, 1.05, 0.95))),
        bm.verts.new(Vector((0.045, 1.09, 0.94))),
        bm.verts.new(Vector((-0.045, 1.09, 0.94))),
        bm.verts.new(Vector((0.0, 1.08, 0.90))),
    ]
    bm.faces.new([nose_verts[0], nose_verts[1], nose_verts[2], nose_verts[3]])
    bm.faces.new([nose_verts[0], nose_verts[3], nose_verts[4]])
    bm.faces.new([nose_verts[1], nose_verts[2], nose_verts[4]])

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    mesh.validate()
    return obj

def create_bolt_jaw():
    """Builds the articulated lower jaw (mandible)."""
    mesh = bpy.data.meshes.new("Jaw_Mesh")
    obj = bpy.data.objects.new("Lower_Jaw", mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    # Hinged at (0, 0.72, 0.86), extends forward to chin at (0, 1.04, 0.86)
    r_base = [
        Vector((-0.09, 0.76, 0.87)),
        Vector((0.09, 0.76, 0.87)),
        Vector((0.07, 0.76, 0.82)),
        Vector((-0.07, 0.76, 0.82)),
    ]
    r_mid = [
        Vector((-0.065, 0.92, 0.86)),
        Vector((0.065, 0.92, 0.86)),
        Vector((0.055, 0.92, 0.82)),
        Vector((-0.055, 0.92, 0.82)),
    ]
    r_chin = [
        Vector((-0.04, 1.04, 0.86)),
        Vector((0.04, 1.04, 0.86)),
        Vector((0.03, 1.03, 0.83)),
        Vector((-0.03, 1.03, 0.83)),
    ]

    vb = [bm.verts.new(p) for p in r_base]
    vm = [bm.verts.new(p) for p in r_mid]
    vc = [bm.verts.new(p) for p in r_chin]

    for i in range(4):
        ni = (i + 1) % 4
        bm.faces.new([vb[i], vb[ni], vm[ni], vm[i]])
        bm.faces.new([vm[i], vm[ni], vc[ni], vc[i]])
    bm.faces.new(vc)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    mesh.validate()
    return obj

def create_bolt_tongue():
    """Builds an expressive canine tongue for happy panting and speech."""
    mesh = bpy.data.meshes.new("Tongue_Mesh")
    obj = bpy.data.objects.new("Tongue", mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    # Ribbon strip curving over lower lip
    pts = [
        Vector((-0.032, 0.85, 0.865)),
        Vector((0.032, 0.85, 0.865)),
        Vector((-0.035, 0.96, 0.865)),
        Vector((0.035, 0.96, 0.865)),
        Vector((-0.033, 1.05, 0.855)),
        Vector((0.033, 1.05, 0.855)),
        Vector((-0.025, 1.10, 0.825)),
        Vector((0.025, 1.10, 0.825)),
    ]
    v = [bm.verts.new(p) for p in pts]
    bm.faces.new([v[0], v[1], v[3], v[2]])
    bm.faces.new([v[2], v[3], v[5], v[4]])
    bm.faces.new([v[4], v[5], v[7], v[6]])

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    mesh.validate()
    return obj

def create_bolt_ear(is_left=True):
    """
    Builds Bolt's tall upright alert triangular ear.
    Has an outer convex back shell and an inner recessed contour (canal)
    matching the user's sketch!
    """
    mesh = bpy.data.meshes.new(f"Ear_Mesh_{'L' if is_left else 'R'}")
    obj = bpy.data.objects.new(f"Ear.{'L' if is_left else 'R'}", mesh)
    bpy.context.collection.objects.link(obj)

    side = 1.0 if is_left else -1.0
    bm = bmesh.new()

    # Outer shell perimeter points
    # Base
    b_inner = Vector((side * 0.08, 0.65, 1.12))
    b_outer = Vector((side * 0.20, 0.61, 1.08))
    b_back  = Vector((side * 0.14, 0.54, 1.10))

    # Mid height
    m_inner = Vector((side * 0.10, 0.67, 1.28))
    m_outer = Vector((side * 0.23, 0.63, 1.25))
    m_back  = Vector((side * 0.16, 0.56, 1.27))

    # Tip
    tip = Vector((side * 0.15, 0.66, 1.48))

    # Inner ear recessed ridge points (concave front face)
    cav_base = Vector((side * 0.13, 0.63, 1.14))
    cav_mid  = Vector((side * 0.15, 0.65, 1.28))

    # Vertices
    v_b_in  = bm.verts.new(b_inner)
    v_b_out = bm.verts.new(b_outer)
    v_b_bk  = bm.verts.new(b_back)

    v_m_in  = bm.verts.new(m_inner)
    v_m_out = bm.verts.new(m_outer)
    v_m_bk  = bm.verts.new(m_back)

    v_tip   = bm.verts.new(tip)

    v_c_base = bm.verts.new(cav_base)
    v_c_mid  = bm.verts.new(cav_mid)

    # Faces: Back convex shell
    bm.faces.new([v_b_out, v_b_bk, v_m_bk, v_m_out])
    bm.faces.new([v_b_bk, v_b_in, v_m_in, v_m_bk])
    bm.faces.new([v_m_out, v_m_bk, v_tip])
    bm.faces.new([v_m_bk, v_m_in, v_tip])

    # Faces: Front inner ear contour (creating the sketch canal effect)
    bm.faces.new([v_b_in, v_b_out, v_c_base])
    bm.faces.new([v_b_out, v_m_out, v_c_mid, v_c_base])
    bm.faces.new([v_b_in, v_c_base, v_c_mid, v_m_in])
    bm.faces.new([v_m_out, v_tip, v_c_mid])
    bm.faces.new([v_m_in, v_c_mid, v_tip])

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    mesh.validate()
    return obj

def create_bolt_eye(is_left=True):
    """
    Builds large Disney/Bolt cybernetic ocular sensors:
    Concentric rings with pupil center that give an unmistakable lively gaze.
    """
    side = 1.0 if is_left else -1.0
    rot = (0, math.radians(-side * 22), math.radians(side * 12))
    loc = (side * 0.13, 0.82, 1.02)

    # Outer eye ring
    eye_ring = create_torus(
        f"EyeOuter.{'L' if is_left else 'R'}",
        location=loc,
        major_r=0.048,
        minor_r=0.008,
        major_seg=14,
        minor_seg=6,
        rotation=rot
    )

    # Inner pupil sphere
    pupil = create_icosphere(
        f"Pupil.{'L' if is_left else 'R'}",
        location=(side * 0.13, 0.83, 1.02),
        radius=0.024,
        subdivisions=1
    )

    # Join outer and inner ocular components
    bpy.ops.object.select_all(action='DESELECT')
    eye_ring.select_set(True)
    pupil.select_set(True)
    bpy.context.view_layer.objects.active = eye_ring
    bpy.ops.object.join()
    eye_obj = bpy.context.active_object
    eye_obj.name = f"Eye.{'L' if is_left else 'R'}"
    return eye_obj

def create_bolt_collar():
    """Builds AXL's glowing cybernetic collar around the base of the neck."""
    loc = (0.0, 0.44, 0.72)
    rot = (math.radians(35), 0, 0)
    collar = create_torus("Cyber_Collar", loc, major_r=0.18, minor_r=0.028, major_seg=16, minor_seg=8, rotation=rot)

    # Add 4 cyber power emitter nodes along collar
    nodes = []
    for angle_deg in [-50, 0, 50, 180]:
        ang = math.radians(angle_deg)
        nx = math.sin(ang) * 0.18
        ny = 0.44 + math.cos(ang) * 0.18 * math.cos(math.radians(35))
        nz = 0.72 - math.cos(ang) * 0.18 * math.sin(math.radians(35))
        node = create_cylinder(f"CollarNode_{angle_deg}", (nx, ny, nz), radius=0.02, depth=0.03, vertices=8, rotation=rot)
        nodes.append(node)

    bpy.ops.object.select_all(action='DESELECT')
    collar.select_set(True)
    for n in nodes:
        n.select_set(True)
    bpy.context.view_layer.objects.active = collar
    bpy.ops.object.join()
    collar_res = bpy.context.active_object
    collar_res.name = "Collar"
    return collar_res

def create_athletic_body():
    """
    Builds the athletic canine torso and neck (Bolt's sleek deep ribcage + AXL armor).
    """
    # 1. Neck (tapered forward cylinder)
    neck = create_cylinder("Neck", (0.0, 0.48, 0.80), radius=0.14, depth=0.26, vertices=12, rotation=(math.radians(35), 0, 0))

    # 2. Chest (Deep canine ribcage)
    mesh_c = bpy.data.meshes.new("Chest_Mesh")
    chest = bpy.data.objects.new("Chest", mesh_c)
    bpy.context.collection.objects.link(chest)
    bm_c = bmesh.new()
    bmesh.ops.create_icosphere(bm_c, subdivisions=2, radius=0.28)
    for v in bm_c.verts:
        v.co.y *= 1.45   # Long chest
        v.co.z *= 1.25   # Deep ribcage
        v.co.x *= 0.90   # Athletic slim width
        v.co.x += 0.0
        v.co.y += 0.22
        v.co.z += 0.65
    bm_c.to_mesh(mesh_c)
    bm_c.free()

    # 3. Pelvis / Hind Torso (Tucked athletic canine waist)
    mesh_p = bpy.data.meshes.new("Pelvis_Mesh")
    pelvis = bpy.data.objects.new("Pelvis", mesh_p)
    bpy.context.collection.objects.link(pelvis)
    bm_p = bmesh.new()
    bmesh.ops.create_icosphere(bm_p, subdivisions=2, radius=0.22)
    for v in bm_p.verts:
        v.co.y *= 1.30
        v.co.z *= 0.95
        v.co.x *= 0.80
        v.co.x += 0.0
        v.co.y -= 0.24
        v.co.z += 0.60
    bm_p.to_mesh(mesh_p)
    bm_p.free()

    return neck, chest, pelvis

def create_digitigrade_leg(is_front=True, is_left=True):
    """
    Builds a canine digitigrade cybernetic leg (scapula/thigh, knee/hock joint, lower leg, and paw with toes).
    """
    side = 1.0 if is_left else -1.0
    label = f"{'F' if is_front else 'B'}{'L' if is_left else 'R'}"
    parts = []

    if is_front:
        # Front Leg (Upright athletic canine stance)
        # Upper Arm / Shoulder
        up = create_cylinder(f"LegUp.{label}", (side * 0.18, 0.28, 0.48), radius=0.065, depth=0.28, vertices=8, rotation=(math.radians(8), 0, 0))
        # Elbow joint node
        joint = create_icosphere(f"JointElbow.{label}", (side * 0.18, 0.30, 0.34), radius=0.045, subdivisions=1)
        # Forearm
        lo = create_cylinder(f"LegLo.{label}", (side * 0.18, 0.31, 0.20), radius=0.05, depth=0.26, vertices=8, rotation=(math.radians(-6), 0, 0))
        # Paw with 3 cyber toes
        paw = create_cylinder(f"Paw.{label}", (side * 0.18, 0.36, 0.04), radius=0.075, depth=0.07, vertices=8, rotation=(0, 0, 0))
        parts.extend([up, joint, lo, paw])
    else:
        # Hind Leg (Classic digitigrade Z-curve: Thigh forward, hock backward, paw grounded)
        # Thigh
        up = create_cylinder(f"LegUp.{label}", (side * 0.17, -0.22, 0.46), radius=0.08, depth=0.28, vertices=8, rotation=(math.radians(-26), 0, 0))
        # Hock / Knee joint node
        joint = create_icosphere(f"JointHock.{label}", (side * 0.17, -0.29, 0.33), radius=0.05, subdivisions=1)
        # Lower leg / Cannon bone (slanted forward)
        lo = create_cylinder(f"LegLo.{label}", (side * 0.17, -0.24, 0.19), radius=0.05, depth=0.26, vertices=8, rotation=(math.radians(22), 0, 0))
        # Rear paw
        paw = create_cylinder(f"Paw.{label}", (side * 0.17, -0.19, 0.04), radius=0.075, depth=0.07, vertices=8, rotation=(0, 0, 0))
        parts.extend([up, joint, lo, paw])

    bpy.ops.object.select_all(action='DESELECT')
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    res = bpy.context.active_object
    res.name = f"Leg.{label}"
    return res

def create_bolt_tail():
    """Builds Bolt's alert sickle-curved tail meeting AXL's segmented carbon/metal spine."""
    parts = []
    # 3 curved segments
    seg1 = create_cylinder("TailSeg1", (0.0, -0.44, 0.68), radius=0.040, depth=0.18, vertices=8, rotation=(math.radians(-38), 0, 0))
    seg2 = create_cylinder("TailSeg2", (0.0, -0.57, 0.82), radius=0.032, depth=0.20, vertices=8, rotation=(math.radians(-62), 0, 0))
    seg3 = create_cylinder("TailSeg3", (0.0, -0.68, 0.99), radius=0.024, depth=0.20, vertices=8, rotation=(math.radians(-84), 0, 0))
    parts.extend([seg1, seg2, seg3])

    bpy.ops.object.select_all(action='DESELECT')
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    tail = bpy.context.active_object
    tail.name = "Tail"
    return tail

def build_complete_cyber_dog():
    """Assembles all anatomical components into a unified articulated cyber-dog."""
    parts = []

    # 1. Head & Facial Expressions
    cranium = create_bolt_cranium()
    snout = create_bolt_snout()
    jaw = create_bolt_jaw()
    tongue = create_bolt_tongue()
    ear_l = create_bolt_ear(is_left=True)
    ear_r = create_bolt_ear(is_left=False)
    eye_l = create_bolt_eye(is_left=True)
    eye_r = create_bolt_eye(is_left=False)
    collar = create_bolt_collar()
    parts.extend([cranium, snout, jaw, tongue, ear_l, ear_r, eye_l, eye_r, collar])

    # 2. Torso
    neck, chest, pelvis = create_athletic_body()
    parts.extend([neck, chest, pelvis])

    # 3. Four Digitigrade Legs
    leg_fl = create_digitigrade_leg(is_front=True, is_left=True)
    leg_fr = create_digitigrade_leg(is_front=True, is_left=False)
    leg_bl = create_digitigrade_leg(is_front=False, is_left=True)
    leg_br = create_digitigrade_leg(is_front=False, is_left=False)
    parts.extend([leg_fl, leg_fr, leg_bl, leg_br])

    # 4. Tail
    tail = create_bolt_tail()
    parts.append(tail)

    # Join into coherent mesh
    bpy.ops.object.select_all(action='DESELECT')
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = cranium
    bpy.ops.object.join()
    body = bpy.context.active_object
    body.name = "AXL_Bolt_Body"

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    body.data.update()
    body.data.validate()
    return body

def build_expressive_armature():
    """
    Constructs the bone hierarchy tailored for expressive Disney dog animation:
    Head tilt, ear perking/drooping, jaw speaking/growling, panting tongue,
    dancing legs, and wagging tail.
    """
    arm_data = bpy.data.armatures.new("AXL_Armature_Data")
    arm_obj = bpy.data.objects.new("AXL_Rig", arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)

    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm_data.edit_bones

    # 1. Root
    root = edit_bones.new("root")
    root.head = (0.0, 0.0, 0.0)
    root.tail = (0.0, 0.1, 0.0)

    # 2. Pelvis / Spine
    pelvis = edit_bones.new("spine_pelvis")
    pelvis.head = (0.0, -0.32, 0.60)
    pelvis.tail = (0.0, 0.02, 0.62)
    pelvis.parent = root

    # 3. Chest
    chest = edit_bones.new("spine_chest")
    chest.head = (0.0, 0.02, 0.62)
    chest.tail = (0.0, 0.40, 0.68)
    chest.parent = pelvis

    # 4. Neck
    neck = edit_bones.new("neck")
    neck.head = (0.0, 0.40, 0.68)
    neck.tail = (0.0, 0.58, 0.88)
    neck.parent = chest

    # 5. Head (Master for cranium and eyes)
    head = edit_bones.new("head")
    head.head = (0.0, 0.58, 0.88)
    head.tail = (0.0, 0.82, 0.98)
    head.parent = neck

    # 6. Jaw (Hinged lower jaw for barking, growling, speaking)
    jaw = edit_bones.new("jaw")
    jaw.head = (0.0, 0.74, 0.86)
    jaw.tail = (0.0, 1.02, 0.84)
    jaw.parent = head

    # 7. Tongue (Inside jaw, extends for happy panting)
    tongue = edit_bones.new("tongue")
    tongue.head = (0.0, 0.85, 0.865)
    tongue.tail = (0.0, 1.08, 0.84)
    tongue.parent = jaw

    # 8. Ears (Bolt hallmark: alert perking, curious tilt, angry back-flattening)
    ear_l = edit_bones.new("ear.L")
    ear_l.head = (0.13, 0.62, 1.10)
    ear_l.tail = (0.16, 0.65, 1.45)
    ear_l.parent = head

    ear_r = edit_bones.new("ear.R")
    ear_r.head = (-0.13, 0.62, 1.10)
    ear_r.tail = (-0.16, 0.65, 1.45)
    ear_r.parent = head

    # 9. Eyes
    eye_l = edit_bones.new("eye.L")
    eye_l.head = (0.13, 0.82, 1.02)
    eye_l.tail = (0.14, 0.88, 1.03)
    eye_l.parent = head

    eye_r = edit_bones.new("eye.R")
    eye_r.head = (-0.13, 0.82, 1.02)
    eye_r.tail = (-0.14, 0.88, 1.03)
    eye_r.parent = head

    # 10. Front Leg Left
    fl_up = edit_bones.new("leg_up.FL")
    fl_up.head = (0.18, 0.28, 0.60)
    fl_up.tail = (0.18, 0.30, 0.34)
    fl_up.parent = chest

    fl_lo = edit_bones.new("leg_lo.FL")
    fl_lo.head = (0.18, 0.30, 0.34)
    fl_lo.tail = (0.18, 0.32, 0.08)
    fl_lo.parent = fl_up

    fl_foot = edit_bones.new("foot.FL")
    fl_foot.head = (0.18, 0.32, 0.08)
    fl_foot.tail = (0.18, 0.44, 0.0)
    fl_foot.parent = fl_lo

    # 11. Front Leg Right
    fr_up = edit_bones.new("leg_up.FR")
    fr_up.head = (-0.18, 0.28, 0.60)
    fr_up.tail = (-0.18, 0.30, 0.34)
    fr_up.parent = chest

    fr_lo = edit_bones.new("leg_lo.FR")
    fr_lo.head = (-0.18, 0.30, 0.34)
    fr_lo.tail = (-0.18, 0.32, 0.08)
    fr_lo.parent = fr_up

    fr_foot = edit_bones.new("foot.FR")
    fr_foot.head = (-0.18, 0.32, 0.08)
    fr_foot.tail = (-0.18, 0.44, 0.0)
    fr_foot.parent = fr_lo

    # 12. Hind Leg Left
    bl_up = edit_bones.new("leg_up.BL")
    bl_up.head = (0.17, -0.22, 0.58)
    bl_up.tail = (0.17, -0.29, 0.33)
    bl_up.parent = pelvis

    bl_lo = edit_bones.new("leg_lo.BL")
    bl_lo.head = (0.17, -0.29, 0.33)
    bl_lo.tail = (0.17, -0.24, 0.08)
    bl_lo.parent = bl_up

    bl_foot = edit_bones.new("foot.BL")
    bl_foot.head = (0.17, -0.24, 0.08)
    bl_foot.tail = (0.17, -0.16, 0.0)
    bl_foot.parent = bl_lo

    # 13. Hind Leg Right
    br_up = edit_bones.new("leg_up.BR")
    br_up.head = (-0.17, -0.22, 0.58)
    br_up.tail = (-0.17, -0.29, 0.33)
    br_up.parent = pelvis

    br_lo = edit_bones.new("leg_lo.BR")
    br_lo.head = (-0.17, -0.29, 0.33)
    br_lo.tail = (-0.17, -0.24, 0.08)
    br_lo.parent = br_up

    br_foot = edit_bones.new("foot.BR")
    br_foot.head = (-0.17, -0.24, 0.08)
    br_foot.tail = (-0.17, -0.16, 0.0)
    br_foot.parent = br_lo

    # 14. Tail (3 bones for sickle curve)
    tail1 = edit_bones.new("tail.01")
    tail1.head = (0.0, -0.38, 0.62)
    tail1.tail = (0.0, -0.52, 0.76)
    tail1.parent = pelvis

    tail2 = edit_bones.new("tail.02")
    tail2.head = (0.0, -0.52, 0.76)
    tail2.tail = (0.0, -0.66, 0.94)
    tail2.parent = tail1

    tail3 = edit_bones.new("tail.03")
    tail3.head = (0.0, -0.66, 0.94)
    tail3.tail = (0.0, -0.76, 1.10)
    tail3.parent = tail2

    bpy.ops.object.mode_set(mode='OBJECT')
    return arm_obj

def bind_mesh_to_armature(mesh_obj, arm_obj):
    """Binds the articulated mesh to the skeleton with automatic weighting."""
    bpy.ops.object.select_all(action='DESELECT')
    mesh_obj.select_set(True)
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.parent_set(type='ARMATURE_AUTO')

    # Normalize vertex groups so no weight exceeds 1.0 due to floating point rounding
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=False)
    # Clamp any weights slightly > 1.0 to 1.0
    for vg in mesh_obj.vertex_groups:
        for v in mesh_obj.data.vertices:
            try:
                w = vg.weight(v.index)
                if w > 1.0:
                    vg.add([v.index], 1.0, 'REPLACE')
            except RuntimeError:
                pass
    mesh_obj.data.validate()

    # Reset active object to armature
    bpy.context.view_layer.objects.active = arm_obj

def build_actions(arm_obj):
    """
    Creates rich canine keyframe animations:
    1. Idle_Breath (60 frames)
    2. Wake_Alert (40 frames)
    3. Growl_Angry (40 frames - Stranger Snarl)
    4. Happy_Dance (60 frames - Victory Ballet)
    5. Speak_Talk (30 frames - TTS Talking)
    """
    bpy.context.view_layer.objects.active = arm_obj
    arm_obj.animation_data_create()
    pose_bones = arm_obj.pose.bones
    for b in pose_bones:
        b.rotation_mode = 'XYZ'

    # Helper to push action to NLA
    def push_nla(action, track_name):
        track = arm_obj.animation_data.nla_tracks.new()
        track.name = track_name
        track.strips.new(action.name, 1, action)

    # -------------------------------------------------------------
    # 1. ACTION: Idle_Breath (Calm rhythmic respiration & alert ear twitch)
    # -------------------------------------------------------------
    act_idle = bpy.data.actions.new("Idle_Breath")
    arm_obj.animation_data.action = act_idle

    chest = pose_bones.get("spine_chest")
    head = pose_bones.get("head")
    ear_l = pose_bones.get("ear.L")
    ear_r = pose_bones.get("ear.R")
    tail2 = pose_bones.get("tail.02")

    if chest:
        chest.location = (0, 0, 0); chest.keyframe_insert("location", frame=1)
        chest.location = (0, 0, 0.018); chest.keyframe_insert("location", frame=30)
        chest.location = (0, 0, 0); chest.keyframe_insert("location", frame=60)

    if head:
        head.rotation_euler = (0, 0, 0); head.keyframe_insert("rotation_euler", frame=1)
        head.rotation_euler = (math.radians(-2), 0, 0); head.keyframe_insert("rotation_euler", frame=30)
        head.rotation_euler = (0, 0, 0); head.keyframe_insert("rotation_euler", frame=60)

    if ear_l and ear_r:
        ear_l.rotation_euler = (0, 0, 0); ear_l.keyframe_insert("rotation_euler", frame=1)
        ear_r.rotation_euler = (0, 0, 0); ear_r.keyframe_insert("rotation_euler", frame=1)
        ear_l.rotation_euler = (0, 0, math.radians(4)); ear_l.keyframe_insert("rotation_euler", frame=25)
        ear_r.rotation_euler = (0, 0, math.radians(-3)); ear_r.keyframe_insert("rotation_euler", frame=35)
        ear_l.rotation_euler = (0, 0, 0); ear_l.keyframe_insert("rotation_euler", frame=60)
        ear_r.rotation_euler = (0, 0, 0); ear_r.keyframe_insert("rotation_euler", frame=60)

    if tail2:
        tail2.rotation_euler = (0, 0, 0); tail2.keyframe_insert("rotation_euler", frame=1)
        tail2.rotation_euler = (0, 0, math.radians(12)); tail2.keyframe_insert("rotation_euler", frame=30)
        tail2.rotation_euler = (0, 0, 0); tail2.keyframe_insert("rotation_euler", frame=60)

    push_nla(act_idle, "NLA_Idle")

    # -------------------------------------------------------------
    # 2. ACTION: Wake_Alert (Bolt curious head-tilt, ears straight up)
    # -------------------------------------------------------------
    act_wake = bpy.data.actions.new("Wake_Alert")
    arm_obj.animation_data.action = act_wake

    if head:
        head.rotation_euler = (0, 0, 0); head.keyframe_insert("rotation_euler", frame=1)
        # Classic Disney inquisitive canine head tilt (15 deg roll, 8 deg yaw)
        head.rotation_euler = (math.radians(-6), math.radians(18), math.radians(10))
        head.keyframe_insert("rotation_euler", frame=16)
        head.keyframe_insert("rotation_euler", frame=40)

    if ear_l and ear_r:
        ear_l.rotation_euler = (0, 0, 0); ear_l.keyframe_insert("rotation_euler", frame=1)
        ear_r.rotation_euler = (0, 0, 0); ear_r.keyframe_insert("rotation_euler", frame=1)
        # Perk ears high and forward
        ear_l.rotation_euler = (math.radians(18), math.radians(4), math.radians(12))
        ear_r.rotation_euler = (math.radians(18), math.radians(-4), math.radians(-12))
        ear_l.keyframe_insert("rotation_euler", frame=14)
        ear_r.keyframe_insert("rotation_euler", frame=14)
        ear_l.keyframe_insert("rotation_euler", frame=40)
        ear_r.keyframe_insert("rotation_euler", frame=40)

    push_nla(act_wake, "NLA_Wake")

    # -------------------------------------------------------------
    # 3. ACTION: Growl_Angry (Threat posture: snarl jaw, ears flat back)
    # -------------------------------------------------------------
    act_growl = bpy.data.actions.new("Growl_Angry")
    arm_obj.animation_data.action = act_growl

    jaw = pose_bones.get("jaw")
    if jaw:
        jaw.rotation_euler = (0, 0, 0); jaw.keyframe_insert("rotation_euler", frame=1)
        # Snarl jaw opened
        jaw.rotation_euler = (math.radians(-22), 0, 0); jaw.keyframe_insert("rotation_euler", frame=12)
        # Rapid predatory tremor
        jaw.rotation_euler = (math.radians(-18), 0, 0); jaw.keyframe_insert("rotation_euler", frame=22)
        jaw.rotation_euler = (math.radians(-24), 0, 0); jaw.keyframe_insert("rotation_euler", frame=32)
        jaw.rotation_euler = (math.radians(-20), 0, 0); jaw.keyframe_insert("rotation_euler", frame=40)

    if ear_l and ear_r:
        ear_l.rotation_euler = (0, 0, 0); ear_l.keyframe_insert("rotation_euler", frame=1)
        ear_r.rotation_euler = (0, 0, 0); ear_r.keyframe_insert("rotation_euler", frame=1)
        # Flatten ears back aggressively
        ear_l.rotation_euler = (math.radians(-32), math.radians(15), math.radians(-20))
        ear_r.rotation_euler = (math.radians(-32), math.radians(-15), math.radians(20))
        ear_l.keyframe_insert("rotation_euler", frame=12)
        ear_r.keyframe_insert("rotation_euler", frame=12)
        ear_l.keyframe_insert("rotation_euler", frame=40)
        ear_r.keyframe_insert("rotation_euler", frame=40)

    if head:
        head.rotation_euler = (0, 0, 0); head.keyframe_insert("rotation_euler", frame=1)
        # Lower head aggressively
        head.rotation_euler = (math.radians(12), 0, 0); head.keyframe_insert("rotation_euler", frame=12)
        head.rotation_euler = (math.radians(10), 0, 0); head.keyframe_insert("rotation_euler", frame=40)

    push_nla(act_growl, "NLA_Growl")

    # -------------------------------------------------------------
    # 4. ACTION: Happy_Dance (Victory ballet: stepping paws, happy pant, wagging tail)
    # -------------------------------------------------------------
    act_dance = bpy.data.actions.new("Happy_Dance")
    arm_obj.animation_data.action = act_dance

    # Wagging tail
    tail1 = pose_bones.get("tail.01")
    tail3 = pose_bones.get("tail.03")
    if tail1 and tail3:
        for f, ang in [(1, 0), (15, 25), (30, -25), (45, 25), (60, 0)]:
            tail1.rotation_euler = (0, 0, math.radians(ang * 0.7)); tail1.keyframe_insert("rotation_euler", frame=f)
            tail3.rotation_euler = (0, 0, math.radians(ang * 1.3)); tail3.keyframe_insert("rotation_euler", frame=f)

    # Stepping front paws
    fl = pose_bones.get("leg_up.FL")
    fr = pose_bones.get("leg_up.FR")
    if fl and fr:
        # Alternating joyful tap
        fl.location = (0, 0, 0); fl.keyframe_insert("location", frame=1)
        fl.location = (0, 0, 0.04); fl.keyframe_insert("location", frame=15)
        fl.location = (0, 0, 0); fl.keyframe_insert("location", frame=30)
        fl.location = (0, 0, 0.04); fl.keyframe_insert("location", frame=45)
        fl.location = (0, 0, 0); fl.keyframe_insert("location", frame=60)

        fr.location = (0, 0, 0.04); fr.keyframe_insert("location", frame=1)
        fr.location = (0, 0, 0); fr.keyframe_insert("location", frame=15)
        fr.location = (0, 0, 0.04); fr.keyframe_insert("location", frame=30)
        fr.location = (0, 0, 0); fr.keyframe_insert("location", frame=45)
        fr.location = (0, 0, 0); fr.keyframe_insert("location", frame=60)

    # Open happy panting jaw and tongue
    tongue = pose_bones.get("tongue")
    if jaw:
        jaw.rotation_euler = (math.radians(-16), 0, 0); jaw.keyframe_insert("rotation_euler", frame=1)
        jaw.rotation_euler = (math.radians(-24), 0, 0); jaw.keyframe_insert("rotation_euler", frame=30)
        jaw.rotation_euler = (math.radians(-16), 0, 0); jaw.keyframe_insert("rotation_euler", frame=60)
    if tongue:
        tongue.location = (0, 0.02, -0.01); tongue.keyframe_insert("location", frame=1)
        tongue.location = (0, 0.04, -0.015); tongue.keyframe_insert("location", frame=30)
        tongue.location = (0, 0.02, -0.01); tongue.keyframe_insert("location", frame=60)

    push_nla(act_dance, "NLA_Dance")

    # -------------------------------------------------------------
    # 5. ACTION: Speak_Talk (Cadenced jaw movement synced with vocal synthesis)
    # -------------------------------------------------------------
    act_speak = bpy.data.actions.new("Speak_Talk")
    arm_obj.animation_data.action = act_speak

    if jaw:
        frames = [1, 5, 10, 15, 20, 25, 30]
        angles = [0, -14, -4, -18, -6, -15, 0]
        for f, ang in zip(frames, angles):
            jaw.rotation_euler = (math.radians(ang), 0, 0); jaw.keyframe_insert("rotation_euler", frame=f)

    if ear_l and ear_r:
        for f, ang in [(1, 0), (15, 6), (30, 0)]:
            ear_l.rotation_euler = (math.radians(ang), 0, math.radians(ang)); ear_l.keyframe_insert("rotation_euler", frame=f)
            ear_r.rotation_euler = (math.radians(ang), 0, math.radians(-ang)); ear_r.keyframe_insert("rotation_euler", frame=f)

    push_nla(act_speak, "NLA_Speak")

    # Reset pose to rest
    bpy.ops.object.mode_set(mode='POSE')
    for b in pose_bones:
        b.location = (0, 0, 0)
        b.rotation_euler = (0, 0, 0)
    bpy.ops.object.mode_set(mode='OBJECT')

def main():
    print(">>> [AXL-BOLT-GEN] Generating expressive Bolt + AXL cybernetic dog...")
    clear_scene()

    mesh_obj = build_complete_cyber_dog()
    arm_obj = build_expressive_armature()
    bind_mesh_to_armature(mesh_obj, arm_obj)
    build_actions(arm_obj)

    out_dir = Path(__file__).resolve().parent
    out_blend = out_dir / "axl_dog_rigged.blend"
    out_glb = out_dir / "axl_dog_demo.glb"

    # 1. Save .blend Project
    bpy.ops.wm.save_as_mainfile(filepath=str(out_blend))
    print(f">>> [AXL-BOLT-GEN] Successfully saved Blender project: {out_blend}")

    # 2. Export glTF 2.0 Web asset
    bpy.ops.export_scene.gltf(
        filepath=str(out_glb),
        export_format='GLB',
        export_animations=True,
        export_skins=True,
        export_all_influences=True
    )
    print(f">>> [AXL-BOLT-GEN] Successfully exported glTF 2.0 web asset: {out_glb}")
    print(">>> [AXL-BOLT-GEN] Generation finished successfully!")

if __name__ == "__main__":
    main()
