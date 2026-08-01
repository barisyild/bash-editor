"""Make Blender display a Bash Editor glTF export the way the game draws it.

Run this once after importing the .glb (paste into Blender's Text Editor and
press Run Script, or `blender --python tools/blender_colours.py` after the
import). It is idempotent; running it again changes nothing.

Two things stand between Blender's default result and the game's picture, and
this script fixes both:

* Blender's glTF importer quantises COLOR_0 into a byte attribute clamped at
  1.0 -- but the PS1 blend is `texel * colour / 128`, a multiplier that runs
  to 2.0, and half the game's colours are above 1. The exporter therefore
  writes the untouched multiplier into a second attribute,
  `_CRASHBASH_COLOR`, which Blender imports as unclamped floats. The script
  rebinds every material's Colour Attribute node to it.

* The game multiplies in gamma space, Blender shades in linear space. The
  multiplier that behaves in linear like `m` does in gamma is `m^2.2`, so a
  Gamma node is inserted after the attribute. Without it, mid colours barely
  darken and the over-brightening barely brightens: everything drifts
  towards the washed-out middle.

The scene's view transform is set to Standard as well: the AgX default is a
filmic look for photographic lighting, and it desaturates flat-shaded PS1
colours on sight.
"""

import bpy

ATTRIBUTE = "_CRASHBASH_COLOR"
GAMMA_NODE = "CB_gamma"
GAMMA = 2.2


def fix_materials() -> int:
    fixed = 0
    for material in bpy.data.materials:
        if not material.use_nodes:
            continue
        tree = material.node_tree
        binder = next(
            (n for n in tree.nodes if n.type == "VERTEX_COLOR"), None
        )
        if binder is None:
            continue
        changed = False
        if binder.layer_name != ATTRIBUTE:
            binder.layer_name = ATTRIBUTE
            changed = True
        if not any(n.name == GAMMA_NODE for n in tree.nodes):
            gamma = tree.nodes.new("ShaderNodeGamma")
            gamma.name = GAMMA_NODE
            gamma.label = "game gamma"
            gamma.inputs["Gamma"].default_value = GAMMA
            gamma.location = (binder.location.x + 160, binder.location.y)
            consumers = [
                (link.to_socket)
                for link in list(tree.links)
                if link.from_node == binder
                and link.from_socket.name == "Color"
            ]
            for socket in consumers:
                tree.links.new(gamma.outputs["Color"], socket)
            tree.links.new(binder.outputs["Color"], gamma.inputs["Color"])
            changed = True
        fixed += changed
    return fixed


def main() -> None:
    count = fix_materials()
    for scene in bpy.data.scenes:
        scene.view_settings.view_transform = "Standard"
    print(f"crash bash colours: {count} material(s) adjusted, "
          f"view transform set to Standard")


if __name__ == "__main__":
    main()
