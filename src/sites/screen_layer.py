"""The site-map screen layer: the UI prefab the runtime loads from the APK
player data, not from a downloadable bundle.

The screen-layer loading law (``ScreenLayerData.LoadScreenLayerPrefab``) is
``Resources.Load<GameObject>("Screen/Prefabs/" + layerData.name)``, so the
world-map screen's whole layout -- where the six site icons sit, how big the
map ground is, where the weather button hangs -- lives in the player data's
Resources and in no bundle the router can name.  This module reads that
prefab and writes the layout truth out as one document.

Two player-data shapes are read:

* a single ``resources.assets`` holding every Resources object
  (:func:`extract_screen_layer`);
* APK members only (:func:`extract_screen_layer_jp`): the ResourceManager
  in ``globalgamemanagers`` maps the lower-cased Resources path to one
  serialized file named by a GUID, which is loaded together with every file
  its externals reference, transitively (the MonoScripts live in
  ``globalgamemanagers.assets``).

Two of the three values that matter are not plain fields on the root
behaviour; they are serialized object lists whose order *is* the law:

* ``_siteMapIcons`` (List<SiteMapIconBase>) and ``_siteMapIconPositions``
  (List<Transform>) are consumed index-locked: the runtime positions icon *i*
  at position *i* before the icons animate in.  Any join that is not by list
  index scrambles the map.
* The festival-garden icon is repositioned once more, onto
  ``_secretSiteMapIconPosition`` -- which in this prefab *is* the ``Delivery``
  entry of the position list (the same object, serialized twice).

The root behaviour and the icon behaviours ship without typetrees, so their
fields are hand-decoded along the declaration order the managed types fix:
base-class fields first, then derived.  Every hand parse must land exactly on
the end of its object; a leftover byte means the field chain was wrong and
the read aborts instead of writing a half-truth.  The field chains are a
property of the client version, not of the byte count: the APK-member reader
only accepts the version its chains were derived for.
"""
from __future__ import annotations

import struct
from pathlib import Path

import UnityPy
from core.unity import configure_fallback_unity_version


configure_fallback_unity_version()

SCREEN_LAYER_NAME = "ScreenLayerMysekaiSiteMap"
SCREEN_LAYER_CLASS = "Sekai.ScreenLayerMysekaiSiteMap"
# The Resources index stores its keys lower-cased; the load path is
# "Screen/Prefabs/" + the screen layer's name.
RESOURCES_KEY = ("Screen/Prefabs/" + SCREEN_LAYER_NAME).lower()
LOADING = "Resources.Load(\"Screen/Prefabs/\" + layerData.name)"

JP_REGION = "jp"
# The client version the APK-member field chains below were derived for.
JP_CLIENT_VERSION = "6.8.1"

# siteType enum values (managed MysekaiSiteType), by declaration order.
SITE_TYPES = ("home_site", "first_floor", "second_floor", "third_floor",
              "grassland", "shore", "flower_garden", "memorial_place",
              "festival_garden")

# Icon prefab internals, from the home instance and one outdoor instance
# (the home button ships a different size; the rest are the same tree).
ICON_PREFAB_NODES = ("HomeIconButton", "site_line", "site_shadow",
                     "DefaultWorldNameBase", "NameText", "HereRoot",
                     "WorldMapHere", "UIPartsReleaseSiteBalloon")

# Nodes whose image tint is consumed (a tinted shadow is a visible truth).
# The single-file reader below takes the four floats at a fixed offset after
# the material pointer of the first behaviour where all four fall inside
# [0, 1], whatever that behaviour's class; the APK-member reader instead reads
# ``m_Color`` of the node's Graphic through that component's declared chain
# (see graphic_color), since the offset only holds for Graphic components.
COLOR_NODES = ("site_shadow", "HomeIconButton", "site_line", "WorldMapHere",
               "bg_background_image", "img_map_shadow",
               "PhenometaBackgroundIcon", "PhenometaIcon", "weatherButton",
               "open_direction_background")

_COLOR_OFFSET = 32 + 12  # behaviour header + m_Material PPtr


def node_color(go_pid, tt, read_raw, components_of, obj_type):
    import struct as _struct
    for cpid in components_of(go_pid):
        if obj_type(cpid) != "MonoBehaviour":
            continue
        raw = read_raw(cpid)
        if len(raw) < _COLOR_OFFSET + 16:
            continue
        r, g, b, a = _struct.unpack_from("<4f", raw, _COLOR_OFFSET)
        if all(0.0 <= value <= 1.0 for value in (r, g, b, a)):
            return [round(r, 4), round(g, 4), round(b, 4), round(a, 4)]
    return None


def collect_colors(root_path, nodes, go_by_path, tt, read_raw,
                   components_of, obj_type, color_of=node_color):
    colors = {}
    for path, frame in nodes.items():
        if not path.startswith(root_path + "/"):
            continue
        leaf = path.rsplit("/", 1)[-1]
        if leaf not in COLOR_NODES or leaf in colors:
            continue
        go_pid = go_by_path.get(path)
        if go_pid is None:
            continue
        color = color_of(go_pid, tt, read_raw, components_of, obj_type)
        if color is not None:
            colors[path] = color
    return colors

SEMANTICS = {
    "loading": ("the screen layer is built into the APK player data; "
                "Resources.Load(\"Screen/Prefabs/\" + layerData.name) fetches "
                "it, so no bundle name routes to it"),
    "iconPositions": ("`_siteMapIcons[i]` is placed at `_siteMapIconPositions[i]` "
                      "before the in-animation; the pairing is by list index and "
                      "nothing else"),
    "secretSiteIconPosition": ("the festival-garden icon is placed once more onto "
                               "`_secretSiteMapIconPosition`, which here is the "
                               "same transform as one entry of the position list"),
    "mapGround": ("the world-panorama image ships with an empty sprite; the "
                  "runtime LoadSprite() assigns it from the site-map texture "
                  "set, and the assigned texture's native size matches the "
                  "rect below"),
    "anchors": ("anchoredPosition is Unity UI: relative to the anchor point, "
                "y up.  Edge anchors (top / corner) hang off the canvas edge, "
                "not off the map centre"),
    "canvas": ("the screen canvas follows ScreenManager.SetUpScreenResolution: "
               "a 1920x1080 reference, matched on height when the display is "
               "taller than 16:9 and on width when wider"),
}


class ParseError(Exception):
    """A hand parse did not land exactly on the end of its object."""


class Reader:
    """Sequential little-endian reads with an align-everything-endian rule."""

    def __init__(self, raw: bytes):
        self.raw = raw
        self.off = 0

    def _take(self, size: int) -> bytes:
        if self.off + size > len(self.raw):
            raise ParseError(f"read past end at {self.off}+{size} of {len(self.raw)}")
        chunk = self.raw[self.off:self.off + size]
        self.off += size
        return chunk

    def pptr(self):
        file_id, path_id = struct.unpack("<iq", self._take(12))
        return {"fileId": file_id, "pathId": path_id}

    def i32(self):
        return struct.unpack("<i", self._take(4))[0]

    def f32(self):
        return struct.unpack("<f", self._take(4))[0]

    def u8(self):
        return self._take(1)[0]

    def align(self):
        self.off = (self.off + 3) & ~3

    def string(self):
        length = self.i32()
        if length < 0 or self.off + length > len(self.raw):
            raise ParseError(f"string length {length} out of range at {self.off}")
        text = self.raw[self.off:self.off + length].decode("utf-8", "replace")
        self.off += length
        self.align()
        return text

    def boolean(self):
        value = self.u8()
        self.align()
        return value

    def pptr_list(self):
        count = self.i32()
        if count < 0 or self.off + 12 * count > len(self.raw):
            raise ParseError(f"reference list of {count} at {self.off} over-reads")
        return [self.pptr() for _ in range(count)]

    def vector3(self):
        return [self.f32(), self.f32(), self.f32()]

    def curve(self):
        """An engine AnimationCurve: keyframes, then the two wrap modes and the rotation order.

        Each float keyframe is time, value, in/out slope, weighted mode and
        in/out weight (seven 4-byte words).
        """
        count = self.i32()
        if count < 0 or self.off + 28 * count > len(self.raw):
            raise ParseError(f"curve of {count} keys at {self.off} over-reads")
        keys = []
        for _ in range(count):
            keys.append({"time": self.f32(), "value": self.f32(),
                         "inSlope": self.f32(), "outSlope": self.f32(),
                         "weightedMode": self.i32(), "inWeight": self.f32(),
                         "outWeight": self.f32()})
        return {"keys": keys, "preInfinity": self.i32(),
                "postInfinity": self.i32(), "rotationOrder": self.i32()}

    def leftover(self) -> int:
        return len(self.raw) - self.off


def parse_screen_layer_fields(raw: bytes) -> dict:
    """The serialized fields of ScreenLayerMysekaiSiteMap, in declaration order.

    Base ScreenLayer contributes ``layerCamera`` and the public ``isBootDone``;
    everything serialized on the derived class is a private [SerializeField].
    The parse is only trusted when it consumes the object exactly.
    """
    r = Reader(raw)
    fields = {
        "gameObject": r.pptr(),
        "enabled": r.boolean(),
        "script": r.pptr(),
        "name": r.string(),
        "layerCamera": r.pptr(),
        "isBootDone": r.boolean(),
    }

    def plist():
        count = r.i32()
        return [r.pptr() for _ in range(count)]

    fields["_siteMapIcons"] = plist()
    fields["_secretSiteMapIconPosition"] = r.pptr()
    fields["_siteMapIconPositions"] = plist()
    for name in ("_siteMapOpenBackgroundImage", "_siteMapPhenomenaView",
                 "_weatherButton", "_siteMapBackgroundImage",
                 "_siteMapBackgroundGradientImage", "_siteMapGroundHighlightImage",
                 "_siteMapRightGradientImage", "_siteMapLeftGradientImage",
                 "_siteMapSphereImage"):
        fields[name] = r.pptr()
    if r.leftover() != 0:
        raise ParseError(f"screen-layer behaviour has {r.leftover()} leftover bytes")
    return fields


def parse_icon_base_fields(raw: bytes) -> dict:
    """The first SiteMapIconBase fields: button, icon image, site type."""
    r = Reader(raw)
    fields = {
        "gameObject": r.pptr(),
        "enabled": r.boolean(),
        "script": r.pptr(),
        "name": r.string(),
        "_mysekaiCustomButton": r.pptr(),
        "_iconImage": r.pptr(),
        "_mysekaiSiteType": r.i32(),
    }
    # Only the prefix is consumed here: the derived SiteMapIcon fields include
    # inline AnimationCurves whose layout is not needed for the layout truth.
    return fields


# ---------------------------------------------------------------------------
# Field chains of the client read from APK members.
#
# Each chain lists the serialized fields of one class after the MonoBehaviour
# header, in declaration order, base class first.  Only [SerializeField] and
# public serializable fields appear; runtime state (tweens, tokens, cached
# masters, non-serialized references) does not.  A reference field carries
# the classes its target may be: a prefab's components live in the prefab's
# own serialized file, so every non-null reference must resolve there to an
# object of one of those classes.  This catches a chain that lands on the
# object end by accident with fields of equal width in the wrong order.
# ---------------------------------------------------------------------------

TRANSFORMS = ("Transform", "RectTransform")
CUSTOM_IMAGE = ("Sekai.UI.CustomImage",)
# UnityEngine.UI.Image and the classes that derive from it.
IMAGES = ("UnityEngine.UI.Image", "Sekai.AtlasImage", "Sekai.UI.CustomImage")
# The concrete classes deriving from the abstract SiteMapIconBase.
ICON_CLASSES = ("Sekai.Mysekai.SiteMapIcon", "Sekai.Mysekai.HomeSiteMapIcon",
                "Sekai.Mysekai.BirthdayDeliverySiteMapIcon")
ICON_BUTTON_CLASS = "Sekai.Mysekai.MysekaiCustomButton"

# In this client the base ScreenLayer serializes only ``layerCamera``; its
# ``isBootDone`` flag is a private runtime field, so the derived fields start
# right after the camera reference.
JP_ROOT_CHAIN = (
    ("layerCamera", "ref", ("Camera",)),
    ("_siteMapIcons", "refs", ICON_CLASSES),
    ("_secretSiteMapIconPosition", "ref", TRANSFORMS),
    ("_siteMapIconPositions", "refs", TRANSFORMS),
    ("_siteMapOpenBackgroundImage", "ref", CUSTOM_IMAGE),
    ("_siteMapPhenomenaView", "ref", ("Sekai.Mysekai.SiteMapPhenomenaView",)),
    ("_weatherButton", "ref", ("Sekai.UI.CustomButton",)),
    ("_siteMapBackgroundImage", "ref", CUSTOM_IMAGE),
    ("_siteMapBackgroundGradientImage", "ref", CUSTOM_IMAGE),
    ("_siteMapGroundHighlightImage", "ref", CUSTOM_IMAGE),
    ("_siteMapRightGradientImage", "ref", CUSTOM_IMAGE),
    ("_siteMapLeftGradientImage", "ref", CUSTOM_IMAGE),
    ("_siteMapSphereImage", "ref", CUSTOM_IMAGE),
)

# SiteMapIconBase: the button wrapper, the icon image, the site type.
JP_ICON_BASE_CHAIN = (
    ("_mysekaiCustomButton", "ref", (ICON_BUTTON_CLASS,)),
    ("_iconImage", "ref", IMAGES),
    ("_mysekaiSiteType", "enum", None),
)

# The hide/open cloud objects and the cloud animator of the outdoor and home
# icons are private runtime references, not serialized; the festival icon
# declares no cloud at all.
JP_ICON_CHAINS = {
    "Sekai.Mysekai.SiteMapIcon": JP_ICON_BASE_CHAIN + (
        ("_uiPartsReleaseSiteBalloon", "ref", ("Sekai.Mysekai.UIPartsReleaseSiteBalloon",)),
        ("_siteMapIconLine", "ref", CUSTOM_IMAGE),
        ("_hereIconImage", "ref", ("GameObject",)),
        ("_siteNameObject", "ref", ("GameObject",)),
        ("_InEase", "curve", None),
        ("_siteIconIdleEase", "curve", None),
        ("_siteIconScaleEase", "curve", None),
        ("_siteIconShadow", "ref", CUSTOM_IMAGE),
        ("_canvasGroup", "ref", ("CanvasGroup",)),
        ("_buttonGroupRoot", "ref", TRANSFORMS),
        ("_buttonAndColoudRoot", "ref", TRANSFORMS),
        ("_siteName", "ref", ("Sekai.UI.CustomTextMesh",)),
        ("_birthdayPlantIconCells", "refs", ("Sekai.Mysekai.SiteMapBirthdayPlantIconCell",)),
        ("_initPosition", "vector3", None),
        ("_siteIconFadeOutEase", "curve", None),
    ),
    "Sekai.Mysekai.HomeSiteMapIcon": JP_ICON_BASE_CHAIN + (
        ("_hereIconImage", "ref", ("GameObject",)),
        ("_siteNameObject", "ref", ("GameObject",)),
        ("_InEase", "curve", None),
        ("_siteIconIdleEase", "curve", None),
        ("_siteIconScaleEase", "curve", None),
        ("_siteIconShadow", "ref", CUSTOM_IMAGE),
        ("_canvasGroup", "ref", ("CanvasGroup",)),
        ("_buttonGroupRoot", "ref", TRANSFORMS),
        ("_buttonAndColoudRoot", "ref", TRANSFORMS),
        ("_initPosition", "vector3", None),
        ("_siteIconFadeOutEase", "curve", None),
    ),
    "Sekai.Mysekai.BirthdayDeliverySiteMapIcon": JP_ICON_BASE_CHAIN + (
        ("_siteMapIconLine", "ref", CUSTOM_IMAGE),
        ("_hereIconImage", "ref", ("GameObject",)),
        ("_siteNameObject", "ref", ("GameObject",)),
        ("_siteName", "ref", ("Sekai.UI.CustomTextMesh",)),
        ("_InEase", "curve", None),
        ("_siteIconIdleEase", "curve", None),
        ("_siteIconScaleEase", "curve", None),
        ("_siteIconFadeOutEase", "curve", None),
        ("_canvasGroup", "ref", ("CanvasGroup",)),
        ("_buttonGroupRoot", "ref", TRANSFORMS),
        ("_initPosition", "vector3", None),
        ("_leftDecorationImage", "ref", CUSTOM_IMAGE),
        ("_rightDecorationImage", "ref", CUSTOM_IMAGE),
    ),
}

# MysekaiCustomButton wraps exactly one CustomButton.
JP_ICON_BUTTON_CHAIN = (("_button", "ref", ("Sekai.UI.CustomButton",)),)

# Behaviours seen on the colour nodes that are not Graphics: a CustomButton
# is a Selectable, the tap effects and the rotation tween derive from plain
# MonoBehaviours.  A colour node carrying a behaviour in neither this set nor
# the Graphic chains stops the read, since it could be the node's Graphic.
NON_GRAPHIC_CLASSES = ("Sekai.UI.CustomButton", ICON_BUTTON_CLASS,
                       "Sekai.UI.CoverButtonTapEffect",
                       "Sekai.UI.GraphicButtonTapEffect", "Sekai.TweenRotation")


def read_chain(raw: bytes, chain) -> tuple[dict, list, int]:
    """Decode the MonoBehaviour header and *chain*; the object must end exactly there.

    Returns the fields, the reference checks ``(field, pointer, classes)`` and
    the byte count consumed.
    """
    r = Reader(raw)
    fields = {"gameObject": r.pptr(), "enabled": r.boolean(), "script": r.pptr(),
              "name": r.string()}
    checks = []
    for name, kind, classes in chain:
        if kind == "ref":
            fields[name] = r.pptr()
            checks.append((name, fields[name], classes))
        elif kind == "refs":
            fields[name] = r.pptr_list()
            checks.extend((name, pointer, classes) for pointer in fields[name])
        elif kind == "enum":
            fields[name] = r.i32()
        elif kind == "curve":
            fields[name] = r.curve()
        elif kind == "vector3":
            fields[name] = r.vector3()
        else:
            raise ValueError(f"unknown field kind {kind!r}")
    if r.leftover() != 0:
        raise ParseError(f"{r.leftover()} leftover bytes after {r.off} of {len(raw)}")
    return fields, checks, r.off


def check_references(owner: int, checks, class_of) -> None:
    """Every non-null reference resolves in the prefab file to one of its declared classes."""
    for field, pointer, classes in checks:
        if pointer["pathId"] == 0 and pointer["fileId"] == 0:
            continue
        if pointer["fileId"] != 0:
            raise ParseError(f"behaviour {owner} {field} points outside the prefab file: {pointer}")
        actual = class_of(pointer["pathId"])
        if actual not in classes:
            raise ParseError(f"behaviour {owner} {field} points at a {actual}, "
                             f"declared {' / '.join(classes)}")


def _graphic_chains():
    """Graphic-derived classes seen on the colour nodes, with their full serialized chains.

    Graphic serializes ``m_Material`` then ``m_Color``; MaskableGraphic,
    Image and AtlasImage append their own fields and CustomImage none.  The
    stock UGUI chains read the same way in this client.
    """
    from ui import talk
    return {"UnityEngine.UI.Image": talk.decode_image,
            "Sekai.AtlasImage": talk.decode_atlasimage,
            "Sekai.UI.CustomImage": talk.decode_customimage}


def graphic_color(go_pid, components_of, obj_type_of, class_of, read_raw, chains):
    """m_Color of the node's Graphic, read along its declared chain to the object end.

    Graphic forbids a second Graphic on one GameObject, so a node has at most
    one; a node without one has no tint and gets no entry.
    """
    from ui import talk
    graphics = []
    for cpid in components_of(go_pid):
        if obj_type_of(cpid) != "MonoBehaviour":
            # Graphic is a behaviour; engine components are never the tint.
            continue
        cls = class_of(cpid)
        if cls in chains:
            graphics.append((cpid, cls))
        elif cls not in NON_GRAPHIC_CLASSES:
            raise ParseError(f"colour node {go_pid} carries {cls}, which is neither a "
                             "known Graphic chain nor a known non-Graphic")
    if not graphics:
        return None
    if len(graphics) > 1:
        raise ParseError(f"colour node {go_pid} has {len(graphics)} Graphic components")
    cpid, cls = graphics[0]
    reader = talk.Reader(read_raw(cpid))
    talk.unpack_mb_prefix(reader)
    fields = chains[cls](reader)
    if reader.remaining() != 0:
        raise ParseError(f"{cls} {cpid} has {reader.remaining()} leftover bytes")
    return [round(value, 4) for value in fields["m_Color"]]


def _screen_layer_document(objects: dict, root_pid: int, root_mb: int, fields: dict, *,
                           game_object_names: dict, parse_icon, button_game_object,
                           colors_of, source: dict, semantics: dict) -> tuple[dict, dict]:
    """The layout document of one screen-layer prefab whose root behaviour is decoded.

    *objects* maps path ids of the prefab's serialized file to its objects.
    *parse_icon* decodes one icon behaviour (by path id) into at least
    ``gameObject``, ``_mysekaiCustomButton`` and ``_mysekaiSiteType``;
    *button_game_object* maps the icon's button reference to the GameObject
    path id it sits on (or None); *colors_of* receives the node table and
    the tree accessors and returns the colour table.
    """
    typetrees: dict[int, dict] = {}

    def tt(pid):
        if pid not in typetrees:
            typetrees[pid] = objects[pid].read_typetree()
        return typetrees[pid]

    def read_raw(pid):
        return objects[pid].get_raw_data()

    def go_name(pid):
        if pid not in game_object_names:
            game_object_names[pid] = tt(pid).get("m_Name", f"go:{pid}")
        return game_object_names[pid]

    def obj_type(pid):
        obj = objects.get(pid)
        return obj.type.name if obj is not None else None

    def components_of(go_pid):
        out = []
        for entry in tt(go_pid)["m_Component"]:
            out.append(entry["component"]["m_PathID"])
        return out

    # -- transform tree over the whole file (rects are engine-typed) --
    transform_of_go: dict[int, int] = {}
    children_of: dict[int, list[int]] = {}
    rect_by_pid: dict[int, dict] = {}
    for pid, obj in objects.items():
        if obj.type.name not in ("Transform", "RectTransform"):
            continue
        value = tt(pid)
        rect_by_pid[pid] = value
        transform_of_go[value["m_GameObject"]["m_PathID"]] = pid
        father = value["m_Father"]["m_PathID"]
        if father:
            children_of.setdefault(father, []).append(pid)

    def rect_of(go_pid):
        tpid = transform_of_go.get(go_pid)
        return None if tpid is None else rect_of_gameobject_frame(rect_by_pid[tpid])

    def walk(go_pid, path, table):
        name = go_name(go_pid)
        child_path = f"{path}/{name}" if path else name
        remember(go_pid, child_path, table)
        for kid_go in (tt(kid)["m_GameObject"]["m_PathID"]
                       for kid in sorted(children_of.get(transform_of_go[go_pid], []),
                                         key=lambda t: t)):
            walk(kid_go, child_path, table)

    def remember(go_pid, path, table):
        table[path] = rect_of(go_pid)
        go_by_path[path] = go_pid

    nodes: dict[str, dict] = {}
    go_by_path: dict[str, int] = {}
    root_transform = tt(transform_of_go[root_pid])
    nodes[SCREEN_LAYER_NAME] = rect_of_gameobject_frame(root_transform)
    go_by_path[SCREEN_LAYER_NAME] = root_pid
    for kid in sorted(children_of.get(transform_of_go[root_pid], [])):
        walk(tt(kid)["m_GameObject"]["m_PathID"], SCREEN_LAYER_NAME, nodes)

    def child_path_of(go_pid):
        # walk up to the root building the path (roots have no father)
        parts = []
        current = go_pid
        while True:
            tpid = transform_of_go[current]
            father = rect_by_pid[tpid]["m_Father"]["m_PathID"]
            parts.append(go_name(current))
            if not father:
                break
            current = rect_by_pid[father]["m_GameObject"]["m_PathID"]
        return "/".join(reversed(parts))

    # -- icon list: behaviour per entry --
    icons = []
    for ref in fields["_siteMapIcons"]:
        pid = ref["pathId"]
        icon = parse_icon(pid)
        site_type = icon["_mysekaiSiteType"]
        if not 0 <= site_type < len(SITE_TYPES):
            raise ParseError(f"icon behaviour {pid} names siteType {site_type}")
        button_go = button_game_object(icon["_mysekaiCustomButton"]["pathId"])
        icons.append({
            "node": go_name(icon["gameObject"]["pathId"]),
            "path": child_path_of(icon["gameObject"]["pathId"]),
            "siteType": SITE_TYPES[site_type],
            "siteTypeValue": site_type,
            "behaviourPathId": pid,
            # The runtime only assigns the position; the authored root scale
            # stays effective (it is not reset per site).
            "rootRect": rect_of(icon["gameObject"]["pathId"]),
            "buttonNode": go_name(button_go) if button_go else None,
            "buttonPath": child_path_of(button_go) if button_go else None,
            "buttonRect": rect_of(button_go) if button_go else None,
        })

    positions = []
    for ref in fields["_siteMapIconPositions"]:
        pid = ref["pathId"]
        if obj_type(pid) not in ("Transform", "RectTransform"):
            raise ParseError(f"icon position {pid} is a {obj_type(pid)}")
        go_pid = tt(pid)["m_GameObject"]["m_PathID"]
        positions.append({
            "node": go_name(go_pid),
            "path": child_path_of(go_pid),
            "rect": rect_of(go_pid),
        })

    if len(icons) != len(positions):
        raise ParseError(f"{len(icons)} icons against {len(positions)} positions")
    secret_ref = fields["_secretSiteMapIconPosition"]["pathId"]
    secret_entry = next((entry for entry, ref in zip(positions, fields["_siteMapIconPositions"])
                         if ref["pathId"] == secret_ref), None)

    def named_ref(field):
        pid = fields[field]["pathId"]
        obj = objects.get(pid)
        if obj is None:
            raise ParseError(f"{field} references missing object {pid}")
        if obj.type.name in ("Transform", "RectTransform"):
            go_pid = tt(pid)["m_GameObject"]["m_PathID"]
        else:
            file_id, go_pid = struct.unpack_from("<iq", obj.get_raw_data(), 0)
        if go_pid not in objects:
            raise ParseError(f"{field} references missing GameObject {go_pid}")
        return {"field": field, "node": go_name(go_pid),
                "path": child_path_of(go_pid), "rect": rect_of(go_pid)}

    curated = {
        "mapGround": named_ref("_siteMapSphereImage"),
        "groundHighlight": named_ref("_siteMapGroundHighlightImage"),
        "backgroundImage": named_ref("_siteMapBackgroundImage"),
        "backgroundGradient": named_ref("_siteMapBackgroundGradientImage"),
        "groundRightGradient": named_ref("_siteMapRightGradientImage"),
        "groundLeftGradient": named_ref("_siteMapLeftGradientImage"),
        "openBackground": named_ref("_siteMapOpenBackgroundImage"),
        "phenomenaView": named_ref("_siteMapPhenomenaView"),
        "weatherButtonRoot": named_ref("_weatherButton"),
    }

    # Icon prefab internals, from the home instance and one outdoor instance
    # (the home button ships a different size; the rest are the same tree).
    def icon_prefab_nodes(instance_path, names):
        prefix = instance_path + "/"
        picked = {}
        for path, frame in nodes.items():
            if not path.startswith(prefix):
                continue
            leaf = path.rsplit("/", 1)[-1]
            if leaf in names and leaf not in picked:
                picked[leaf] = frame
        return picked

    home_icon = next(icon for icon in icons if icon["siteType"] == "home_site")
    outdoor_icon = next(icon for icon in icons if icon["siteType"] != "home_site")
    icon_prefab = {
        "home": {"instance": home_icon["path"],
                 "nodes": icon_prefab_nodes(home_icon["path"], ICON_PREFAB_NODES)},
        "outdoor": {"instance": outdoor_icon["path"],
                    "nodes": icon_prefab_nodes(outdoor_icon["path"], ICON_PREFAB_NODES)},
    }
    colors = colors_of(nodes, go_by_path, tt, read_raw, components_of, obj_type)

    counts = {
        "nodes": len(nodes),
        "icons": len(icons),
        "iconPositions": len(positions),
        "siteTypes": sorted({icon["siteType"] for icon in icons}),
        "prefabNodes": len(icon_prefab["outdoor"]["nodes"]),
        "colors": len(colors),
    }
    document = {
        "version": 1,
        "semantics": semantics,
        "source": {"gameObject": SCREEN_LAYER_NAME, **source,
                   "rootPathId": root_pid, "rootBehaviourPathId": root_mb,
                   "loading": LOADING},
        "canvas": {
            "referenceResolution": [1920, 1080],
            "matchLaw": ("ScreenManager.SetUpScreenResolution: 1080/1920 <= H/W "
                         "matches height, else width"),
        },
        "iconPositions": positions,
        "icons": icons,
        "secretSiteIconPosition": (None if secret_entry is None
                                   else {"path": secret_entry["path"],
                                         "node": secret_entry["node"]}),
        "screen": curated,
        "iconPrefab": icon_prefab,
        "colors": colors,
        "nodes": nodes,
        "summary": counts,
    }
    return document, counts


def extract_screen_layer(player_data: str, out_dir) -> dict:
    """Write the screen-layer layout truth into *out_dir*; return counts.

    Reads a player data whose Resources objects sit in one ``resources.assets``.
    """
    env = UnityPy.load(player_data)
    objects = {}
    for obj in env.objects:
        if obj.assets_file.name == "resources.assets":
            objects[obj.path_id] = obj
    if not objects:
        raise ParseError("resources.assets not found in the player data")

    game_object_names: dict[int, str] = {}
    for pid, obj in objects.items():
        if obj.type.name == "GameObject":
            try:
                game_object_names[pid] = obj.read_typetree().get("m_Name", "")
            except Exception:
                pass

    # -- locate the screen layer root by name (no id is hardcoded) --
    roots = [pid for pid, name in game_object_names.items()
             if name == SCREEN_LAYER_NAME]
    if len(roots) != 1:
        raise ParseError(
            f"expected exactly one {SCREEN_LAYER_NAME} GameObject, found {len(roots)}")
    root_pid = roots[0]

    def obj_type(pid):
        obj = objects.get(pid)
        return obj.type.name if obj is not None else None

    root_components = [entry["component"]["m_PathID"]
                       for entry in objects[root_pid].read_typetree()["m_Component"]]
    root_mb = next(cpid for cpid in root_components if obj_type(cpid) == "MonoBehaviour")
    fields = parse_screen_layer_fields(objects[root_mb].get_raw_data())

    def parse_icon(pid):
        # Only the base-class prefix is read on this shape.
        return parse_icon_base_fields(objects[pid].get_raw_data())

    def button_game_object(button_pid):
        if button_pid in objects and obj_type(button_pid) == "MonoBehaviour":
            # The button behaviour has no typetree either; its first field is
            # the m_GameObject PPtr, which is all the tree walk needs.
            file_id, go_pid = struct.unpack_from("<iq", objects[button_pid].get_raw_data(), 0)
            return go_pid if go_pid in objects else None
        return None

    def colors_of(nodes, go_by_path, tt, read_raw, components_of, obj_type):
        return collect_colors(SCREEN_LAYER_NAME, nodes, go_by_path, tt, read_raw,
                              components_of, obj_type)

    document, counts = _screen_layer_document(
        objects, root_pid, root_mb, fields, game_object_names=game_object_names,
        parse_icon=parse_icon, button_game_object=button_game_object,
        colors_of=colors_of, source={"serializedFile": "resources.assets"},
        semantics=SEMANTICS)
    from core.jsonio import write_json
    path = write_json(Path(out_dir) / "screen_layer.json", document)
    return {"counts": counts, "path": str(path)}


def extract_screen_layer_jp(apks, out_dir, *, client_version: str) -> dict:
    """Write the screen-layer layout truth from APK members into *out_dir*.

    *apks* are the base APK and the install-time asset pack.  The Resources
    index names the prefab's serialized file; that file is loaded with every
    file its externals reference, the root and icon behaviours are decoded along this
    client's declarations (each to its last byte, each reference checked
    against its declared class), and the colour table reads the Graphic
    colour of each colour node through that component's own chain.  Returns
    the counts, the written path and the per-object decode accounting.
    """
    from ui import talk
    from ui.player_source import (ApkPlayerData, container_target, resource_container,
                                  unity_version)

    if client_version != JP_CLIENT_VERSION:
        raise ValueError(f"no screen-layer field chains registered for JP {client_version}; "
                         f"they were derived for {JP_CLIENT_VERSION}")
    store = ApkPlayerData(apks)
    player = store.environment()
    player.add(["globalgamemanagers"], follow=False)
    env = player.env
    manager, container = resource_container(env)
    entries = container.get(RESOURCES_KEY)
    if not entries:
        raise ParseError(f"Resources index has no entry {RESOURCES_KEY!r}")
    if len(entries) != 1:
        raise ParseError(f"Resources entry {RESOURCES_KEY!r} has {len(entries)} pointers")
    pointer = entries[0]
    file_name, root_pid = container_target(env, manager, pointer)
    player.add([file_name])
    if player.missing:
        raise FileNotFoundError(f"externals no APK member supplies: {player.missing}")
    objects = dict(env.files[file_name].objects)
    mono_index = talk.build_monoscript_index(env)

    def class_of(pid):
        obj = objects.get(pid)
        if obj is None:
            raise ParseError(f"object {pid} is not in the prefab file")
        if obj.type.name == "MonoBehaviour":
            return talk.resolve_script_class(obj, mono_index)
        return obj.type.name

    game_object_names = {pid: obj.read_typetree().get("m_Name", "")
                         for pid, obj in objects.items() if obj.type.name == "GameObject"}
    if class_of(root_pid) != "GameObject" or game_object_names.get(root_pid) != SCREEN_LAYER_NAME:
        raise ParseError(f"{RESOURCES_KEY!r} points at {class_of(root_pid)} "
                         f"{game_object_names.get(root_pid)!r}, not the {SCREEN_LAYER_NAME} root")
    same_name = [pid for pid, name in game_object_names.items() if name == SCREEN_LAYER_NAME]
    if same_name != [root_pid]:
        raise ParseError(f"expected one {SCREEN_LAYER_NAME} GameObject, found {same_name}")

    decoded = []

    def decode(pid, chain, expected):
        cls = class_of(pid)
        if cls not in expected:
            raise ParseError(f"behaviour {pid} is a {cls}, expected {' / '.join(expected)}")
        raw = objects[pid].get_raw_data()
        try:
            fields, checks, consumed = read_chain(raw, chain)
        except ParseError as exc:
            raise ParseError(f"{cls} {pid}: {exc}") from None
        check_references(pid, checks, class_of)
        decoded.append({"class": cls, "pathId": pid, "consumed": consumed, "size": len(raw)})
        return fields

    root_components = [entry["component"]["m_PathID"]
                       for entry in objects[root_pid].read_typetree()["m_Component"]]
    root_behaviours = [cpid for cpid in root_components if class_of(cpid) == SCREEN_LAYER_CLASS]
    if len(root_behaviours) != 1:
        raise ParseError(f"expected one {SCREEN_LAYER_CLASS} on the root, found {len(root_behaviours)}")
    root_mb = root_behaviours[0]
    fields = decode(root_mb, JP_ROOT_CHAIN, (SCREEN_LAYER_CLASS,))

    def parse_icon(pid):
        cls = class_of(pid)
        if cls not in JP_ICON_CHAINS:
            raise ParseError(f"icon behaviour {pid} is a {cls}, which has no field chain")
        return decode(pid, JP_ICON_CHAINS[cls], ICON_CLASSES)

    def button_game_object(button_pid):
        button = decode(button_pid, JP_ICON_BUTTON_CHAIN, (ICON_BUTTON_CLASS,))
        go = button["gameObject"]
        if go["fileId"] != 0 or class_of(go["pathId"]) != "GameObject":
            raise ParseError(f"icon button {button_pid} sits on {go}, not a GameObject of the prefab")
        return go["pathId"]

    chains = _graphic_chains()

    def colors_of(nodes, go_by_path, tt, read_raw, components_of, obj_type):
        def color_of(go_pid, tt, read_raw, components_of, obj_type):
            return graphic_color(go_pid, components_of, obj_type, class_of, read_raw, chains)
        return collect_colors(SCREEN_LAYER_NAME, nodes, go_by_path, tt, read_raw,
                              components_of, obj_type, color_of=color_of)

    member = player.member(file_name)
    source = {
        "region": JP_REGION,
        "clientVersion": client_version,
        "unityVersion": unity_version(env),
        "resourcesKey": RESOURCES_KEY,
        "containerPointer": {"fileId": pointer[0], "pathId": pointer[1]},
        "serializedFile": file_name,
        "serializedFileSha256": store.sha256(member),
        "apkMembers": store.members_of(member),
        "colorRule": ("m_Color of the node's single Graphic component, read along "
                      "that component's declared chain to its last byte; a node "
                      "without a Graphic has no entry"),
    }
    semantics = dict(SEMANTICS)
    semantics["loading"] = (SEMANTICS["loading"] + "; this client's Resources index "
                            "maps the lower-cased load path to one serialized file of "
                            "the install-time asset pack")
    document, counts = _screen_layer_document(
        objects, root_pid, root_mb, fields, game_object_names=game_object_names,
        parse_icon=parse_icon, button_game_object=button_game_object,
        colors_of=colors_of, source=source, semantics=semantics)
    document["source"]["handDecoded"] = decoded
    from core.jsonio import write_json
    path = write_json(Path(out_dir) / "screen_layer.json", document)
    return {"counts": counts, "path": str(path), "decoded": decoded,
            "source": document["source"]}


def rect_of_gameobject_frame(transform: dict) -> dict:
    """The RectTransform truth of one node, with plain-Transform fallback."""
    is_rect = "m_AnchoredPosition" in transform
    frame = {
        "anchorsMin": _vec2(transform["m_AnchorMin"]) if is_rect else None,
        "anchorsMax": _vec2(transform["m_AnchorMax"]) if is_rect else None,
        "pivot": _vec2(transform["m_Pivot"]) if is_rect else None,
        "anchoredPosition": (_vec2(transform["m_AnchoredPosition"])
                             if is_rect else None),
        "sizeDelta": _vec2(transform["m_SizeDelta"]) if is_rect else None,
        "localScale": _vec3(transform["m_LocalScale"]),
        "localPosition": _vec3(transform["m_LocalPosition"]),
    }
    return frame


def _vec2(value):
    return [round(value["x"], 4), round(value["y"], 4)]


def _vec3(value):
    return [round(value["x"], 4), round(value["y"], 4), round(value["z"], 4)]
