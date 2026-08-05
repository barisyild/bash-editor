"""Make and read fcurves across Blender's two action layouts.

Blender 4.4 introduced slotted actions and 5.0 removed the flat `action.fcurves`
list that every add-on used before it. Both shapes hold the same thing -- one
curve per animated property -- so the add-on works through these four functions
and never names either layout anywhere else.

An action is bound to an ID through a *slot* in the new layout, and a bound
action with no slot selected evaluates to nothing at all: the curves are stored,
the timeline is full, and every clip previews as the rest pose. That is the one
difference worth knowing about.
"""

from __future__ import annotations

import bpy

LAYERED = not hasattr(bpy.types.Action, "fcurves")


def make(name: str) -> bpy.types.Action:
    """An empty action ready to take shape key curves, in either layout."""
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    if not LAYERED:
        return action
    action.slots.new(id_type="KEY", name="Key")
    action.layers.new("Layer").strips.new(type="KEYFRAME")
    return action


def _channelbag(action: bpy.types.Action, ensure: bool = False):
    if not action.layers or not action.layers[0].strips:
        return None
    strip = action.layers[0].strips[0]
    if not action.slots:
        return None
    return strip.channelbag(action.slots[0], ensure=ensure)


def curves(action: bpy.types.Action):
    """Every fcurve the action holds, whichever layout it is in."""
    if not LAYERED:
        return list(action.fcurves)
    bag = _channelbag(action)
    return list(bag.fcurves) if bag else []


def new_curve(action: bpy.types.Action, data_path: str):
    if not LAYERED:
        return action.fcurves.new(data_path=data_path)
    bag = _channelbag(action, ensure=True)
    return bag.fcurves.new(data_path=data_path, index=0)


def assign(owner, action: bpy.types.Action) -> None:
    """Bind the action to `owner`'s animation data, slot and all.

    Without the slot the new layout stores the curves and evaluates none of
    them, so this is not an optional tidying step.
    """
    owner.animation_data_create()
    owner.animation_data.action = action
    if LAYERED and action.slots:
        owner.animation_data.action_slot = action.slots[0]
