"""The add-on's user-facing surface: preferences, import, export, a panel.

Nothing here decides anything about the format. The operators locate the shared
library, hand it bytes, and report what it says -- including, and especially,
when it refuses. An import that would draw wrong is stopped here with the
reason, because the alternative is finding out on the console.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import library, naming as N

ARCHIVE_SUFFIXES = {".70", ".dat", ".exe", ""}
_ENTRY_CACHE: dict[str, list[tuple[str, str, str]]] = {}


def _preferences(context) -> str:
    try:
        return context.preferences.addons[__package__].preferences.library_path
    except (KeyError, AttributeError):
        return ""


def _require_library(operator, context) -> bool:
    root, why = library.locate(_preferences(context))
    if root is not None:
        return True
    # With the reason. A library that is present and one module short fails the
    # same way as one that is absent, and the two want different fixes.
    operator.report({"ERROR"}, (
        "the shared crashbash library was not found -- set its path in the "
        "add-on preferences, or install the packaged add-on which bundles it"
        + (f". {why}" if why else "")))
    return False


def _archive(path: str):
    from crashbash.archive import BashArchive

    return BashArchive(path)


def _entry_items(self, context):
    """Every model in the archive at the chosen path, cheaply and once."""
    path = self.filepath
    if not path or Path(path).suffix.lower() == ".mdl":
        return [("", "", "")]
    if path not in _ENTRY_CACHE:
        try:
            archive = _archive(path)
            _ENTRY_CACHE[path] = [
                (entry.name, entry.name.replace("models/", ""),
                 f"{entry.size:,} bytes")
                for entry in archive if entry.name.endswith(".mdl")
            ] or [("", "no models found", "")]
        except Exception as exc:  # noqa: BLE001
            _ENTRY_CACHE[path] = [("", f"cannot read: {exc}", "")]
    return _ENTRY_CACHE[path]


class CRASHBASH_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    library_path: StringProperty(
        name="crash-bash-editor",
        description=("Path to a checkout of crash-bash-editor. Only needed when "
                     "the add-on was not packaged with the library inside it"),
        subtype="DIR_PATH",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "library_path")
        layout.label(text=library.describe(self.library_path))


class CRASHBASH_OT_import(bpy.types.Operator, ImportHelper):
    """Bring a Crash Bash model into the scene, geometry, textures and clips"""

    bl_idname = "crashbash.import_model"
    bl_label = "Import Crash Bash Model"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ""
    filter_glob: StringProperty(default="*.70;*.dat;*.mdl;*", options={"HIDDEN"})
    entry: EnumProperty(name="Model", items=_entry_items,
                        description="Which model in the archive to open")
    # The enum's items come from whichever file is selected, so it holds no
    # valid value until one is -- which makes it unusable from a script, where
    # the path and the choice arrive in the same call and Blender validates the
    # assignment against an empty list. A plain name has no such order to it.
    entry_name: StringProperty(
        name="Model name", options={"HIDDEN"},
        description="Entry to open, by name; overrides the list when set")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        if Path(self.filepath).suffix.lower() == ".mdl":
            layout.label(text="Loose model; its sibling .tex is used if present",
                         icon="INFO")
        else:
            layout.prop(self, "entry")

    def execute(self, context):
        if not _require_library(self, context):
            return {"CANCELLED"}
        from . import build_scene

        path = Path(self.filepath)
        try:
            if path.suffix.lower() == ".mdl":
                model_data = path.read_bytes()
                sibling = path.with_suffix(".tex")
                pack_data = sibling.read_bytes() if sibling.is_file() else None
                entry, source, pack_entry = path.name, str(path), str(sibling)
            else:
                wanted = self.entry_name or self.entry
                if not wanted:
                    self.report({"ERROR"}, "choose a model from the archive")
                    return {"CANCELLED"}
                archive = _archive(self.filepath)
                by_name = {e.name: e for e in archive}
                if wanted not in by_name:
                    self.report({"ERROR"}, f"'{wanted}' is not in this archive")
                    return {"CANCELLED"}
                model_data = archive.read(by_name[wanted])
                pack_entry = wanted[:-4] + ".tex"
                pack_data = (archive.read(by_name[pack_entry])
                             if pack_entry in by_name else None)
                entry, source = wanted, self.filepath
            collection, notes = build_scene.build_model(
                entry, model_data, pack_data, source, pack_entry)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"{type(exc).__name__}: {exc}")
            return {"CANCELLED"}

        for note in notes[:6]:
            self.report({"WARNING"}, note)
        self.report({"INFO"}, f"{entry}: {len(collection.all_objects)} objects"
                              + (f", {len(notes)} notes" if notes else ""))
        return {"FINISHED"}


class CRASHBASH_OT_export(bpy.types.Operator, ExportHelper):
    """Write the active collection back out as a model entry the game loads"""

    bl_idname = "crashbash.export_model"
    bl_label = "Export Crash Bash Model"
    bl_options = {"REGISTER"}

    filename_ext = ".mdl"
    filter_glob: StringProperty(default="*.mdl", options={"HIDDEN"})
    pin_tables: BoolProperty(
        name="Keep the shared tables in place",
        description=("Do not rewrite the colour and UV tables: map each colour "
                     "onto an entry the model already has and each UV onto a "
                     "triple it already holds. Only the seven hub models need "
                     "this and they turn it on themselves -- the exporter now "
                     "lays a model out again rather than appending to it, so a "
                     "table grows where it stands and nothing is stranded. "
                     "Forcing it on costs colour: a borrowed model came back "
                     "91 of 255 off at worst against 0 without it"),
        default=False,
    )
    rebuild_tables: BoolProperty(
        name="Rebuild the shared tables",
        description=("Build the colour and UV tables from nothing but the "
                     "meshes in the scene, so the file carries no entry that "
                     "is not read by one of them. This RENUMBERS every entry, "
                     "and nothing in the file can see who else holds an index: "
                     "the menu came back from hardware drawing flat bands of "
                     "the wrong colour when its table was rebuilt this way. It "
                     "also costs a little -- median 8 colour and 25 UV entries "
                     "more than shipped, because re-striping deduplicates the "
                     "three-consecutive overlap slightly worse"),
        default=True,
    )
    animation_only: BoolProperty(
        name="Animation only",
        description=("Rebuild the clips and leave every mesh byte-identical. "
                     "Use it whenever the edit is to the timeline rather than "
                     "the shape -- installing geometry that did not change "
                     "costs a copy of the shared tables each time"),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return _target(context) is not None

    def invoke(self, context, event):
        collection = _target(context)
        if collection is not None:
            entry = collection.get(N.PROP_ENTRY, "model.mdl")
            self.filepath = entry.rsplit("/", 1)[-1]
        return super().invoke(context, event)

    def execute(self, context):
        if not _require_library(self, context):
            return {"CANCELLED"}
        from crashbash.formats import modelimport as MI
        from crashbash.formats.anim import read_animations
        from crashbash.formats.mdl import read_model
        from crashbash.formats.tex import read_pack

        from . import read_scene

        collection = _target(context)
        if collection is None:
            self.report({"ERROR"}, "no imported collection is active")
            return {"CANCELLED"}
        try:
            model_data, pack_data = _source_bytes(collection)
            model = read_model(model_data)
            clips = read_animations(model_data, model)
            pack = read_pack(pack_data) if pack_data else None
            request = read_scene.build_request(collection, model, clips, pack,
                                               model_data=model_data)
            report = MI.import_payload(
                model_data, pack_data, request,
                animation_only=self.animation_only,
                rebuild_tables=self.rebuild_tables and not self.animation_only,
                pin_tables=True if self.pin_tables else None)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"nothing was written. {exc}")
            return {"CANCELLED"}

        target = Path(self.filepath)
        target.write_bytes(report.model)
        written = [target.name]
        if report.pack:
            beside = target.with_suffix(".tex")
            beside.write_bytes(report.pack)
            written.append(beside.name)
        for note in report.warnings[:6]:
            self.report({"WARNING"}, note)
        self.report({"INFO"}, (
            f"{', '.join(written)}: {len(report.meshes_rebuilt)} meshes rebuilt, "
            f"{len(report.meshes_unchanged)} untouched, "
            f"{len(report.clips_rebuilt)} clips rebuilt, "
            f"{len(report.placements_written)} placements moved, "
            f"{len(report.placements_added)} added, "
            f"{len(report.textures_written)} textures written"))
        return {"FINISHED"}


class CRASHBASH_OT_borrow_mesh(bpy.types.Operator):
    """Put the selected mesh into the active object's slot, painted for its pack

    Select the mesh you are borrowing, then shift-select the model mesh it is
    to replace, and run this. It does the three things a borrowed mesh needs
    and cannot do for itself.
    """

    bl_idname = "crashbash.borrow_mesh"
    bl_label = "Borrow Selected Mesh"
    bl_options = {"REGISTER", "UNDO"}

    bring_textures: bpy.props.BoolProperty(
        name="Bring its own textures",
        description=("Add the borrowed model's pictures to this pack as new "
                     "slots, taking none of the ones already there. Not "
                     "possible in a model whose tables are pinned, where the "
                     "art is baked into the vertex colours instead"),
        default=True,
    )
    stand_on_origin: bpy.props.BoolProperty(
        name="Stand on its own origin",
        description=("Move the borrowed geometry so it is centred in plan with "
                     "its feet at zero. A placement's translation is an offset "
                     "from where the mesh is authored, so without this the "
                     "borrowed model lands wherever its source model put it"),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            return False
        if obj.get(N.PROP_MESH) is None and obj.get(N.PROP_OBJECT) is None:
            return False
        return any(o is not obj and o.type == "MESH"
                   for o in context.selected_objects)

    def _carry_textures(self, borrowed, collection, pack, notes):
        """Give every borrowed material a slot of its own, appended to the pack.

        The numbers have to be decided here, because they go into the faces:
        appending puts a record before the last one, so the first newcomer
        takes the swatch's old number and each one after it the next. The
        export checks that against where the append actually lands.

        `_carry_textures` is called from `execute`, which imports the library
        itself; the shader builder is the add-on's own.

        A swatch face travels too. It reads one texel of the source pack's
        swatch image through a palette the *model* names, and all 87 of the
        penguin's read one palette -- so a copy of that image, decoded through
        that palette, is an ordinary picture and the face an ordinary textured
        face reading the same cell.
        """
        from . import build_scene

        base = len(pack.textures) - 1
        renumber = {}
        for index, material in enumerate(borrowed.materials):
            image = _material_image(material)
            if image is None or tuple(image.size) == (0, 0):
                self.report({"ERROR"}, (
                    f"'{material.name if material else index}' carries no "
                    f"picture to add; import the source through this add-on"))
                return None
            slot = base + len(renumber)
            fresh = bpy.data.materials.new(
                N.MATERIAL_SLOT.format(slot=slot, width=image.size[0],
                                       height=image.size[1],
                                       depth=4) + "_added")
            fresh[N.PROP_SLOT] = slot
            fresh[N.PROP_NEW_SLOT] = True
            fresh[N.PROP_BLEND] = int(material.get(N.PROP_BLEND, 0) or 0)
            build_scene._shader(fresh, image, fresh[N.PROP_BLEND])
            renumber[index] = fresh
        # `materials.clear()` sets every polygon's index back to 0, so which
        # material each face wore has to be taken first -- otherwise all 116 of
        # the penguin's faces came back on one slot and read one picture.
        worn = [poly.material_index for poly in borrowed.polygons]
        borrowed.materials.clear()
        order = sorted(renumber)
        for index in order:
            borrowed.materials.append(renumber[index])
        remap = {old: n for n, old in enumerate(order)}
        for poly, was in zip(borrowed.polygons, worn):
            poly.material_index = remap.get(was, 0)
        notes.append(f"added {len(renumber)} picture(s) to the pack as slots "
                     f"{base}..{base + len(renumber) - 1}, replacing none; the "
                     f"swatch moves to {base + len(renumber)}")
        return renumber

    def _snap_uvs(self, borrowed, model_data, model, uv, notes) -> bool:
        """Move every face's UVs onto a triple the pinned table already holds."""
        import numpy as np
        from crashbash.formats import modelimport as MI

        from . import build_scene

        sizes = {tuple(image.size) for image in
                 (_material_image(m) for m in borrowed.materials) if image}
        if len(sizes) != 1:
            self.report({"ERROR"}, (
                f"the borrowed pictures are {len(sizes)} different sizes, and a "
                f"pinned table's triples have to fit one of them"))
            return False
        width, height = sizes.pop()
        available = MI.pinned_uv_triples(model_data, model, width, height)
        if not len(available):
            self.report({"ERROR"}, (
                f"this model's pinned UV table holds no triple inside a "
                f"{width}x{height} picture, so a textured face cannot address "
                f"one here"))
            return False

        # Out of Blender's V and into texel space, snap, and back again -- the
        # same conversion the importer uses, flip included.
        faces = len(borrowed.polygons)
        texels = np.empty((faces, 3, 2), dtype=np.int32)
        for n, poly in enumerate(borrowed.polygons):
            for k, loop in enumerate(poly.loop_indices):
                u, v = uv.data[loop].uv
                texels[n, k, 0] = min(max(int(round(u * (width - 1))), 0), width - 1)
                texels[n, k, 1] = min(max(int(round((1.0 - v) * (height - 1))), 0),
                                      height - 1)
        snapped = MI.snap_to_triples(texels, available)
        moved = int(np.abs(snapped - texels).sum(axis=(1, 2)).max())
        for n, poly in enumerate(borrowed.polygons):
            for k, loop in enumerate(poly.loop_indices):
                uv.data[loop].uv = (
                    (snapped[n, k, 0] + 0.5) / width,
                    1.0 - (snapped[n, k, 1] + 0.5) / height)
        notes.append(f"snapped every face onto one of the {len(available)} UV "
                     f"triples this pinned table holds, the worst moving "
                     f"{moved} texels over its six coordinates")
        return True

    def _finish(self, context, target, borrowed, mesh, source, notes, np):
        """Stand it on its origin, hand it to the target, and say what happened."""
        if self.stand_on_origin:
            co = np.empty(len(borrowed.vertices) * 3, dtype=np.float32)
            borrowed.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3)
            co -= np.array([(co[:, 0].min() + co[:, 0].max()) / 2,
                            (co[:, 1].min() + co[:, 1].max()) / 2,
                            co[:, 2].min()], dtype=np.float32)
            borrowed.vertices.foreach_set("co", co.ravel())
            borrowed.update()
            notes.append("stood it on its own origin, feet at z=0")

        was = target.data
        old_faces = len(was.polygons)
        for obj in list(bpy.data.objects):
            if obj.data is was:
                obj.data = borrowed
        borrowed.name = f"{target.name}_borrowed"
        # How much of the level is going away. `crate_jungle/arena`'s 0x5001 is
        # the biggest mesh in its pool and also 642 of the level's 1108
        # triangles, so borrowing over it built a disc that loads with half the
        # map missing -- which is the trade working exactly as stated, and worth
        # saying before it is seen.
        share = old_faces / max(self._level_faces, 1)
        if share > 0.25:
            notes.append(f"this mesh is {share * 100:.0f}% of everything the "
                         f"level draws, so that much of it goes away")

        room = int(mesh.ptr_end - mesh.header_offset)
        per_face = room / max(old_faces, 1)
        wanted = per_face * len(borrowed.polygons)
        budget = (f"{room} bytes owned and roughly {wanted:.0f} wanted"
                  if wanted <= room * 0.9 else
                  f"{room} bytes owned against roughly {wanted:.0f} wanted, "
                  f"which this writer may not fit -- the export measures and "
                  f"refuses rather than break the pool")
        self.report({"INFO"}, (
            f"{source.name} -> {target.name}: {len(borrowed.polygons)} faces "
            f"replacing {old_faces}; {budget}. " + "; ".join(notes)))

    def execute(self, context):
        if not _require_library(self, context):
            return {"CANCELLED"}
        import numpy as np
        from crashbash.formats import modelimport as MI
        from crashbash.formats.mdl import read_model
        from crashbash.formats.tex import read_pack

        from . import build_scene

        target = context.active_object
        source = next(o for o in context.selected_objects
                      if o is not target and o.type == "MESH")
        collection = _target(context)
        try:
            model_data, pack_data = _source_bytes(collection)
            model = read_model(model_data)
            pack = read_pack(pack_data) if pack_data else None
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"{exc}")
            return {"CANCELLED"}
        if pack is None:
            self.report({"ERROR"}, (
                "this model has no texture pack beside it, and a borrowed mesh "
                "has to be painted through the destination's own palette"))
            return {"CANCELLED"}

        # A level's meshes are not all in `model.meshes`: the ones it actually
        # draws are object-pool meshes (§8.3), reached through the object table
        # and numbered on from where the plain ones stop. `warp_room1` has 42 of
        # the first kind and 72 of the second, so looking in one list finds
        # nothing for the very meshes a level edit is about.
        index = target.get(N.PROP_MESH)
        mesh = None
        if index is not None:
            index = int(index)
            mesh = next((m for m in model.meshes if m.index == index), None)
            if mesh is None:
                mesh = next((o.mesh for o in model.objects
                             if o.mesh is not None and o.mesh.index == index), None)
        if mesh is None:
            self.report({"ERROR"}, "the active object is not a mesh of this model")
            return {"CANCELLED"}

        self._level_faces = sum(
            len(o.mesh.face_colour_index) for o in model.objects
            if o.mesh is not None) or 1
        borrowed = source.data.copy()
        notes = []

        # 1. The clips it brought. Blender sums every shape key that is on, and
        #    a borrowed mesh is wanted for its shape, not its animation -- the
        #    penguin's 34 keys drew a fan of shards over the whole room while
        #    the exported file was already right.
        if borrowed.shape_keys:
            notes.append(f"cleared {len(borrowed.shape_keys.key_blocks)} shape keys")
            holder = bpy.data.objects.new("_crashbash_borrow", borrowed)
            holder.shape_key_clear()
            bpy.data.objects.remove(holder)

        uv = borrowed.uv_layers.get(N.UV_LAYER)
        colour = borrowed.color_attributes.get(N.COLOUR_ATTRIBUTE)
        if uv is None or colour is None:
            self.report({"ERROR"}, (
                f"'{source.name}' has no {N.UV_LAYER} or {N.COLOUR_ATTRIBUTE}; "
                f"import it through this add-on so it carries both"))
            return {"CANCELLED"}

        # A carrier's UV table cannot grow (§2.1), so a textured face has
        # nowhere to put its texels and the art has to be flattened into the
        # colour. Everywhere else the pictures can simply be *added* to the
        # pack, which takes nothing from anybody (§10.3 never comes up).
        carrier = int.from_bytes(model_data[0x38:0x3C], "little") != 0
        if self.bring_textures:
            added = self._carry_textures(borrowed, collection, pack, notes)
            if added is None:
                return {"CANCELLED"}
            if carrier:
                # The table cannot grow, so the faces have to address the new
                # slot through triples it already holds. That is a real loss and
                # it is stated: `warp_room1` offers 48 usable ones.
                kept = self._snap_uvs(borrowed, model_data, model, uv, notes)
                if not kept:
                    return {"CANCELLED"}
            self._finish(context, target, borrowed, mesh, source, notes, np)
            return {"FINISHED"}

        # 2. Its art, which does not travel here: the slots it names mean other
        #    pictures in this pack. Fold the texel each corner reads into its
        #    colour so the shape keeps its look without naming a slot (§6.2).
        pages = {}
        for slot, material in enumerate(borrowed.materials):
            image = _material_image(material)
            if image is None:
                continue
            buffer = np.empty(len(image.pixels), dtype=np.float32)
            image.pixels.foreach_get(buffer)
            pages[slot] = buffer.reshape(image.size[1], image.size[0], 4)
        baked = 0
        for poly in borrowed.polygons:
            page = pages.get(poly.material_index)
            if page is None:
                continue
            height, width = page.shape[:2]
            for loop in poly.loop_indices:
                u, v = uv.data[loop].uv
                x = min(max(int(round(u * (width - 1))), 0), width - 1)
                y = min(max(int(round(v * (height - 1))), 0), height - 1)
                texel = page[y, x]
                have = colour.data[loop].color
                # The hardware draws texel * colour / 128, so a stored 128 is
                # neutral and the flat result is the product doubled.
                colour.data[loop].color = tuple(
                    min(texel[i] * have[i] * 2.0, 1.0) for i in range(3)
                ) + (have[3],)
            baked += 1
        # Say why it was flattened rather than leaving it to be noticed. Two
        # separate walls decide it and they are worth naming apart: a pinned
        # model cannot take a new UV at all, and §10.3 says which slots could be
        # overwritten even where it can.
        textured = sum(1 for poly in borrowed.polygons
                       if borrowed.materials[poly.material_index] is not None
                       and borrowed.materials[poly.material_index].get(N.PROP_SLOT)
                       is not None)
        free = MI.sole_sampler_slots(model, mesh)
        # A carrier announces itself by a non-zero i32@0x38, 7 of the 400
        # shipped models. There the UV table cannot grow at all, so a textured
        # face has nowhere to put its texels and the question of slots does not
        # arise; everywhere else it is §10.3 that decides.
        carrier = int.from_bytes(model_data[0x38:0x3C], "little") != 0
        why = ("this model's tables are pinned, so no face can name a UV the "
               "table does not already hold"
               if carrier else
               f"the mesh being replaced is the sole reader of {len(free)} "
               f"slot(s), which is all §10.3 allows taking")
        notes.append(f"baked the texture into the colour on {baked} faces "
                     f"({textured} of them named a slot of their own; {why})")

        # 3. The cell every face now reads. In a pinned model the UV table
        #    cannot grow, so it has to be one the table already holds three in a
        #    row of -- otherwise the export refuses and does not say which.
        cell = MI.pinned_swatch_cell(model_data, model, mesh, pack)
        loose = MI.neutral_swatch_cell(model_data, mesh, pack)
        pinned = cell is not None and cell != loose
        cell = cell or loose
        if cell is None:
            self.report({"ERROR"}, "this pack has no swatch texture to paint through")
            return {"CANCELLED"}
        swatch = next((t for t in pack.textures if t.is_swatch), None)
        # The same conversion the importer uses, V flipped and all: Blender's
        # V runs the other way from a texel row, and setting it unflipped puts
        # every face on a cell the pinned table does not hold -- the export then
        # refuses all 116 of them, which is right and unhelpful.
        u = (cell[0] + 0.5) / swatch.width
        v = 1.0 - (cell[1] + 0.5) / swatch.height
        # The palette is the destination mesh's own -- `_swatch_entry` takes the
        # one its texture list uses most, which is what a rebuild writes for
        # every triangle anyway. Reuse the material the import already built for
        # it rather than making a second one that means the same thing.
        from crashbash.formats import mdlwrite as MW
        palette = MW._swatch_entry(model_data, mesh) & 0x1FF
        material = _collection_swatch(collection, palette)
        if material is None:
            self.report({"ERROR"}, (
                f"no swatch material for palette {palette} in "
                f"'{collection.name}'; re-import the entry"))
            return {"CANCELLED"}
        borrowed.materials.clear()
        borrowed.materials.append(material)
        for poly in borrowed.polygons:
            poly.material_index = 0
            for loop in poly.loop_indices:
                uv.data[loop].uv = (u, v)
        notes.append(f"put all {len(borrowed.polygons)} faces on {material.name} "
                     f"cell {cell}"
                     + (" (one the pinned table holds)" if pinned and carrier else ""))

        # The colour has to have the cell's own value divided back out, or the
        # baked look comes back multiplied by it a second time.
        texel = _texel_at(material, u, v)
        if texel is not None:
            for loop in range(len(colour.data)):
                have = colour.data[loop].color
                colour.data[loop].color = tuple(
                    min(max(have[i] / (texel[i] * 2.0), 0.0), 1.0)
                    if texel[i] > 1 / 255 else 1.0 for i in range(3)
                ) + (have[3],)

        # 4. Where it stands, and the handover -- shared with the textured path.
        self._finish(context, target, borrowed, mesh, source, notes, np)
        return {"FINISHED"}


def _draw_budgets(layout, context, collection) -> None:
    """What this model can run out of, and how much of it is spent.

    Every one of these was a disc that did not work, so the panel says the
    number rather than leaving it to be found: the colour index's thirteen
    bits, the pool span an object mesh may not leave, the padding a placement
    list grows into, the strip count no shipped mesh exceeds, and the size the
    model has to stay inside.

    Reading the entry back on every redraw would be absurd, so it is cached on
    the collection and only re-read when the entry changes.
    """
    from crashbash.formats import modelimport as MI
    from crashbash.formats.anim import read_animations  # noqa: F401
    from crashbash.formats.mdl import read_model
    from crashbash.formats.tex import read_pack

    obj = context.active_object
    try:
        model_data, pack_data = _source_bytes(collection)
        model = read_model(model_data)
        pack = read_pack(pack_data) if pack_data else None
    except Exception:  # noqa: BLE001
        return

    mesh = None
    faces = None
    index = obj.get(N.PROP_MESH) if obj is not None else None
    if index is not None:
        index = int(index)
        mesh = next((m for m in model.meshes if m.index == index), None)
        if mesh is None:
            mesh = next((o.mesh for o in model.objects
                         if o.mesh is not None and o.mesh.index == index), None)
        if obj.type == "MESH" and obj.data is not None:
            faces = len(obj.data.polygons)

    box = layout.box()
    box.label(text="Budgets", icon="MOD_BUILD")
    for budget in MI.budgets(model_data, model, pack, mesh, faces):
        row = box.row(align=True)
        if budget.limit is None:
            row.label(text=f"{budget.label}: {budget.used} {budget.unit}".rstrip(),
                      icon="CHECKMARK")
            continue
        icon = ("ERROR" if budget.over else
                "SEQUENCE_COLOR_03" if budget.fraction > 0.9 else "DOT")
        row.label(text=f"{budget.label}  {budget.used} / {budget.limit} "
                       f"{budget.unit}".rstrip(), icon=icon)
        # Blender has no bar widget in a panel, so a progress-shaped label is
        # what there is; twenty cells reads at a glance and costs nothing.
        filled = int(round(budget.fraction * 20))
        box.label(text="  [" + "█" * filled + "·" * (20 - filled) + "]")


def _material_image(material):
    """The picture a material samples, if it samples one."""
    if material is None or not material.use_nodes:
        return None
    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image:
            return node.image
    return None


def _collection_swatch(collection, palette: int):
    """The swatch material the import already built for that palette."""
    fallback = None
    for obj in collection.all_objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        for material in obj.data.materials:
            if material is None or material.get(N.PROP_SLOT) is not None:
                continue
            have = material.get(N.PROP_PALETTE)
            if have is None:
                continue
            if int(have) == palette:
                return material
            fallback = fallback or material
    return fallback


def _texel_at(material, u: float, v: float):
    """What one texel of a material's picture is worth, 0..1 per channel."""
    import numpy as np

    image = _material_image(material)
    if image is None:
        return None
    buffer = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(buffer)
    page = buffer.reshape(image.size[1], image.size[0], 4)
    x = min(max(int(round(u * (image.size[0] - 1))), 0), image.size[0] - 1)
    y = min(max(int(round(v * (image.size[1] - 1))), 0), image.size[1] - 1)
    return page[y, x][:3]


class CRASHBASH_OT_add_placement(bpy.types.Operator):
    """Put another copy of the active placement's object in the level"""

    bl_idname = "crashbash.add_placement"
    bl_label = "Add Placement"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.get(N.PROP_PLACEMENT) is not None

    def execute(self, context):
        if not _require_library(self, context):
            return {"CANCELLED"}
        from crashbash.formats.mdl import read_model
        from crashbash.formats import placewrite

        collection = _target(context)
        source = context.active_object
        try:
            model_data, _ = _source_bytes(collection)
            model = read_model(model_data)
            room = placewrite.spare_capacity(model_data, model)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"{exc}")
            return {"CANCELLED"}

        # Count what the collection already spends, so the panel's number and
        # this operator agree: a record the level cannot take is refused here
        # rather than at export, where the whole edit would be lost.
        from . import read_scene
        _, fresh = read_scene._claims(collection, model, [])
        if len(fresh) >= room:
            self.report({"ERROR"}, (
                f"this level has room for {room} more record(s) and "
                f"{len(fresh)} object(s) already stand for new ones"))
            return {"CANCELLED"}

        copy = source.copy()
        copy.name = f"{source.name}_copy"
        for parent in source.users_collection:
            parent.objects.link(copy)
        # It arrives exactly on top of the one it came from, which is where a
        # duplicate belongs -- the record's translation is an offset from where
        # the mesh is authored, so anything else would be a guess at the frame.
        copy.matrix_basis = source.matrix_basis.copy()
        bpy.ops.object.select_all(action="DESELECT")
        copy.select_set(True)
        context.view_layer.objects.active = copy
        self.report({"INFO"}, (
            f"{copy.name} added; move it and export. Room for "
            f"{room - len(fresh) - 1} more after this one"))
        return {"FINISHED"}


class CRASHBASH_OT_bake_particles(bpy.types.Operator):
    """Play the shot: actors and props on their tracks, the camera, the particles"""

    bl_idname = "crashbash.bake_shot"
    bl_label = "Bake Shot Preview"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        collection = _target(context)
        return collection is not None and bool(collection.get(N.PROP_SCENE))

    def execute(self, context):
        if not _require_library(self, context):
            return {"CANCELLED"}
        from crashbash.formats.anim import read_animations
        from crashbash.formats.mdl import read_model

        from . import build_scene

        collection = _target(context)
        try:
            model_data, _ = _source_bytes(collection)
            model = read_model(model_data)
            clips = read_animations(model_data, model)
            stem = collection.get(N.PROP_ENTRY, "model").rsplit("/", 1)[-1]
            made = build_scene.bake_shot(
                collection, model_data, model, clips, stem.rsplit(".", 1)[0])
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"{type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        if not made:
            self.report({"INFO"}, "this model carries no shot")
            return {"CANCELLED"}
        self.report({"INFO"}, (
            f"{made['actors']} actors, {made['props']} props, "
            f"{made['cameras']} cameras and {made['particles']} particles "
            f"keyframed over {made['ticks']} ticks. It is a preview: the "
            f"export ignores it and it goes stale when the shot is edited"))
        return {"FINISHED"}


def _target(context) -> bpy.types.Collection | None:
    """The imported collection the user means.

    Widest to narrowest, because two models open at once is the ordinary case:
    whatever the active object belongs to, then whatever the scene holds, then
    the only one in the file. Stopping at "the only one" left both operators
    unavailable the moment a second model was imported.
    """
    def owner(obj):
        for collection in obj.users_collection:
            if collection.get(N.PROP_ENTRY):
                return collection
            for parent in bpy.data.collections:
                if (parent.get(N.PROP_ENTRY)
                        and collection.name in parent.children):
                    return parent
        return None

    for obj in [context.active_object] + list(context.selected_objects or []):
        if obj is not None and owner(obj) is not None:
            return owner(obj)
    here = [c for c in context.scene.collection.children_recursive
            if c.get(N.PROP_ENTRY)]
    if len(here) == 1:
        return here[0]
    found = [c for c in bpy.data.collections if c.get(N.PROP_ENTRY)]
    return found[0] if len(found) == 1 else None


def _source_bytes(collection) -> tuple[bytes, bytes | None]:
    """The shipped entry this collection came from, model and pack."""
    source = collection.get(N.PROP_SOURCE, "")
    entry = collection.get(N.PROP_ENTRY, "")
    pack_entry = collection.get(N.PROP_PACK, "")
    path = Path(source)
    if path.suffix.lower() == ".mdl":
        pack = Path(pack_entry) if pack_entry else path.with_suffix(".tex")
        return path.read_bytes(), pack.read_bytes() if pack.is_file() else None
    archive = _archive(source)
    by_name = {e.name: e for e in archive}
    if entry not in by_name:
        raise ValueError(f"'{entry}' is not in {source}")
    pack_data = (archive.read(by_name[pack_entry])
                 if pack_entry in by_name else None)
    return archive.read(by_name[entry]), pack_data


class VIEW3D_PT_crashbash(bpy.types.Panel):
    bl_label = "Crash Bash"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Crash Bash"

    def draw(self, context):
        layout = self.layout
        collection = _target(context)
        if collection is None:
            layout.label(text="Nothing imported in this scene", icon="INFO")
            layout.operator(CRASHBASH_OT_import.bl_idname, icon="IMPORT")
            return
        box = layout.box()
        box.label(text=collection.get(N.PROP_ENTRY, "?"), icon="MESH_DATA")
        box.label(text=f"{len(collection.all_objects)} objects")
        _draw_budgets(layout, context, collection)

        obj = context.active_object
        if obj is not None and obj.get(N.PROP_MESH) is not None:
            box = layout.box()
            box.label(text=f"mesh {obj[N.PROP_MESH]}", icon="OUTLINER_OB_MESH")
            box.label(text=f"{len(obj.data.polygons)} faces, "
                           f"{len(obj.data.materials)} materials")
            clips = obj.get(N.PROP_CLIPS) or {}
            if clips:
                box.label(text=f"{len(clips)} clips")
                for label in list(clips)[:8]:
                    box.label(text=f"  {label}")
            if obj.get(N.PROP_VOLUMES):
                box.label(text=f"{len(obj[N.PROP_VOLUMES])} collision volumes "
                               f"(carried through, not edited)")
            box.operator(CRASHBASH_OT_borrow_mesh.bl_idname, icon="LINK_BLEND")
            box.label(text="select the mesh to borrow, then this one")
        if obj is not None and obj.get(N.PROP_OBJECT) is not None:
            layout.box().label(
                text=f"object pool id {obj[N.PROP_OBJECT]:04X}; a rebuild has "
                     f"to fit the span it owns",
                icon="MESH_CUBE")
        if obj is not None and obj.get(N.PROP_PLACEMENT) is not None:
            box = layout.box()
            box.label(text=f"placement {obj[N.PROP_PLACEMENT]} places "
                           f"{obj.get(N.PROP_PLACES, 0):04X}", icon="EMPTY_AXIS")
            box.label(text="move it and export; one record changes")
            box.operator(CRASHBASH_OT_add_placement.bl_idname, icon="DUPLICATE")
            box.label(text="or duplicate it: the copy becomes a new record")
            if obj.get(N.PROP_SPARE):
                box.label(text="spare: another record already places this "
                               "object", icon="CHECKMARK")
                box.label(text="re-aim it to put something new in the level")
                box.prop(obj, f'["{N.PROP_PLACES}"]', text="places")
                box.label(text="then move it: the record translates the new "
                               "object from where its own mesh is authored")
            else:
                box.label(text="the only record placing this object; "
                               "re-aiming it takes it out of the level",
                          icon="ERROR")
        if obj is not None and obj.get(N.PROP_EMITTER) is not None:
            box = layout.box()
            box.label(text=f"particle emitter at node "
                           f"{int(obj[N.PROP_EMITTER]):#x}", icon="PARTICLES")
            for field_name in N.EMITTER_FIELDS:
                if obj.get(field_name) is not None:
                    box.prop(obj, f'["{field_name}"]', text=field_name)
        if collection.get(N.PROP_SCENE):
            layout.operator(CRASHBASH_OT_bake_particles.bl_idname,
                            icon="PARTICLES")
        layout.operator(CRASHBASH_OT_export.bl_idname, icon="EXPORT")


def _menu_import(self, context):
    self.layout.operator(CRASHBASH_OT_import.bl_idname, text="Crash Bash Model")


def _menu_export(self, context):
    self.layout.operator(CRASHBASH_OT_export.bl_idname, text="Crash Bash Model")


CLASSES = (
    CRASHBASH_AddonPreferences,
    CRASHBASH_OT_import,
    CRASHBASH_OT_export,
    CRASHBASH_OT_borrow_mesh,
    CRASHBASH_OT_add_placement,
    CRASHBASH_OT_bake_particles,
    VIEW3D_PT_crashbash,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)


def unregister() -> None:
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
