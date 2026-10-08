"""Bundle-name routing with explicit unsupported-domain results."""
import re
from dataclasses import dataclass

# Environment packages of one phenomenon: a global variant, a shared common
# variant, and one unique variant per site.
PHENOMENA_BUNDLE = re.compile(
    r"^mysekai__effect__site__environment__"
    r"(?P<phenomenon>[0-9]+_[a-z0-9]+)__"
    r"(?P<variant>common|global|unique__[a-z0-9_]+)$")

# Older players use an aggregate thumbnail package; current players also use
# one package per master iconAssetbundleName. This is a package namespace, not
# a list of weather types.
PHENOMENA_THUMBNAIL = "mysekai__thumbnail__phenomena"
PHENOMENA_THUMBNAIL_ITEM = re.compile(
    r"^mysekai__thumbnail__phenomena__[A-Za-z0-9_]+$")

# Every package of the site domain lives under one path prefix: the nine sites'
# scenes, the indoor kit the room sites are assembled from, the room skins, the
# field objects, the travel cannon and the world-map screen.  The domain is claimed
# by the prefix rather than by a list, so a package added to it in a later version
# is extracted and censused instead of being silently ignored.
SITE_PREFIX = "mysekai__site__"

# Music and ambience live under one path prefix.  Which of these packages hold a
# world's sound is stated by master rows, not by the names, so this domain is
# recognized here but is not a download root on its own: see `Manifest.roots`.
SOUND_PREFIX = "mysekai__sound__"

# A music row names the leaf of its own package; an ambience row names only a
# cue, and every ambience cue lives in one shared package, so a cue name is not
# a package name and the shared package has to be stated once.
BGM_PACKAGE_PREFIX = SOUND_PREFIX + "bgm__"
AMBIENCE_PACKAGE = SOUND_PREFIX + "se__se_mysekai"

# The furniture-*interface* family is huge (999 packages) but only two things are
# read out of it — the attach points and the per-fixture grid.  The domain is
# therefore *fixture-interface*, never *fixture*: a domain name that promised the
# whole family would overstate what is really claimed.  It is claimed by the full
# prefix ``mysekai__fixture__`` (the trailing double underscore is exactly what
# stops it from eating the ``mysekai__fixture_timeline__`` family's neighbours),
# so a package added to it in a later version is read and censused instead of
# being silently ignored.
FIXTURE_PREFIX = "mysekai__fixture__"

# Two timeline families exist, and each plays under its own host: the story
# cut-scene player (``mysekai__cut_scene__``) and the furniture fixture-timeline
# views (``mysekai__fixture_timeline__``).  The two are separate domains — never
# one performance domain, which is already the alone-actions family's — because
# the products are split by host, not merged.
CUT_SCENE_PREFIX = "mysekai__cut_scene__"
FIXTURE_TIMELINE_PREFIX = "mysekai__fixture_timeline__"

# The dialogue-camera assets live in exactly one package, so the domain is
# claimed by that exact name and never by a prefix: ``mysekai__camera`` as a
# prefix would also swallow any future ``mysekai__camera_*`` neighbour whose
# contents nobody has read yet, and route it into the camera job silently.  An
# exact name leaves such a neighbour visible as ``unsupported`` instead.
CAMERA_PACKAGE = "mysekai__camera"

# Native furniture details do not infer their character combinations from talk
# scripts. FixtureReactionDescription loads this exact system package. Claim the
# one proven package only; a broad ``mysekai__system__`` prefix would silently
# swallow unrelated future system assets nobody has decoded.
FIXTURE_REACTION_PACKAGE = "mysekai__system__fixture_reaction_data"

# The talk scripts name their arguments through constant tables, and the tables
# are in a package of their own rather than in the talk package.  The domain is
# named after what is read out of it — the constant tables — and not after the
# package, because only one of its four assets is read; a domain called after
# the package would promise the rest.  It has no product directory of its own:
# both talk extractors resolve against the tables, and the report entry carries
# the table sizes so "the constants were read" is a count rather than a claim.
TALK_CONSTANTS_PACKAGE = "mysekai__talk__scenario__lib"

# Player-appearance packages live under one path prefix shared by more than the
# three families chara.avatar_parts reads (skin/decoration/penlight): the
# avatar body model and its motion library share the same prefix but not the
# category shape (``chara.avatar_parts.split_flat`` raises on them), so the
# prefix alone is not enough here -- only the three known categories are
# claimed.  The category list is duplicated from ``chara.avatar_parts
# .CATEGORIES`` rather than imported, the same way every other domain's shape
# is stated here without importing its extractor module; the two must be kept
# in step by hand.
AVATAR_PART_PREFIX = "virtual_live__avatar__"
AVATAR_PART_CATEGORIES = ("skin", "decoration", "penlight")

# The two authored harvest-tool model families, including the shipped third
# level even when no current master row offers it. Neighbouring tool-model
# domains are not claimed by this bounded reader.
HARVEST_TOOL_BUNDLE = re.compile(r"^mysekai__tool__(?:ax|pickax)000[1-5]$")

# The player's hand items (the avatar item view's models, one package per
# item, each holding the prefab named after its leaf).  Claimed by the path
# prefix: the item view builds the package path from the item's file name, so a
# model added under it is extracted instead of reported as unregistered.
PLAYER_TOOL_MODEL_PREFIX = "mysekai__player_tool_model__"
PLAYER_TOOL_MODEL_BUNDLE = re.compile(r"^mysekai__player_tool_model__[a-z0-9_]+$")

# The player and harvest effects a site move and a harvest action emit: one
# package per effect prefab under this path (flying, landing, failed landing,
# boarding/takeoff, the harvest boost).  Claimed by the path prefix so a new
# effect under it is extracted instead of being reported as unregistered; the
# prefix is exactly one level below the weather family's
# ``mysekai__effect__site__environment__`` and does not overlap it.
SITE_ACTION_EFFECT_PREFIX = "mysekai__effect__site__harvest__action__"
SITE_ACTION_EFFECT_BUNDLE = re.compile(
    r"^mysekai__effect__site__harvest__action__[a-z0-9_]+$")

# The player's foot effects (the dash dust, the water splash): one package per
# effect under this path, named after the action rather than the prefab
# (``dash`` holds ``fx_act_user_walking``).  Claimed by the path prefix for the
# same reason as the harvest-action family; a sibling of it, not overlapping it
# or the weather family.
SITE_COMMON_ACTION_EFFECT_PREFIX = "mysekai__effect__site__common__action__"
SITE_COMMON_ACTION_EFFECT_BUNDLE = re.compile(
    r"^mysekai__effect__site__common__action__[a-z0-9_]+$")

# The effects a harvest object emits when it is hit, when it disappears, and
# while it rests (the barrel): one package per effect prefab under
# ``.../harvest/object/<hit|delete|stay>/<prefab>``, the prefab named exactly as
# the last path segment.  Claimed by the whole prefix, like its two siblings, so
# a package added under it later is extracted -- and one whose path does not have
# that two-segment shape fails by name in the family reader instead of being
# ignored.  A sibling of the harvest-action prefix; neither contains the other.
SITE_HARVEST_OBJECT_EFFECT_PREFIX = "mysekai__effect__site__harvest__object__"
SITE_HARVEST_OBJECT_EFFECT_BUNDLE = re.compile(
    r"^mysekai__effect__site__harvest__object__[a-z0-9_]+$")

# The home site's effects (the craft effect a workbench emits, the multiplayer
# avatar appearance): one package per effect prefab under this path, the prefab
# named after the package leaf.  Claimed by the path prefix like its siblings;
# a sibling of the harvest prefixes and the weather family, overlapping none.
SITE_HOME_EFFECT_PREFIX = "mysekai__effect__site__home__"
SITE_HOME_EFFECT_BUNDLE = re.compile(r"^mysekai__effect__site__home__[a-z0-9_]+$")

# The house door transition (the wipe drawn while the player enters or leaves a
# house) is one package the door transitioner manager loads by this exact name.
# Claimed by the exact name, not by a prefix: ``mysekai__mysekai_`` neighbours
# are unrelated packages nobody has read.
HOUSE_TRANSITION_PACKAGE = "mysekai__mysekai_house_transition"

# The placement effect a decided furniture placement shows (the field put
# effect loads this package once by this exact name and instantiates its prefab,
# named after the leaf, at every put).  Claimed by the exact name, not by the
# ``mysekai__effect__fixture__`` prefix: the other packages under that prefix are
# the furniture particle packages' material and mesh companions, read by the
# fixture particle pass as pointer targets, not effects of their own.
FIXTURE_PUT_EFFECT_PREFIX = "mysekai__effect__fixture__"
FIXTURE_PUT_EFFECT_PACKAGE = "mysekai__effect__fixture__fx_fixture_put_01"
FIXTURE_PUT_EFFECT_BUNDLE = re.compile(r"^mysekai__effect__fixture__fx_fixture_put_01$")


@dataclass(frozen=True)
class Route:
    domain: str
    extractor: str


def route(name):
    if HARVEST_TOOL_BUNDLE.fullmatch(name):
        return Route("harvest-tool", "harvest-tools")
    if PLAYER_TOOL_MODEL_BUNDLE.fullmatch(name):
        return Route("player-tool-model", "player-tool-models")
    if SITE_ACTION_EFFECT_BUNDLE.fullmatch(name):
        return Route("site-action-effect", "site-action-effects")
    if SITE_COMMON_ACTION_EFFECT_BUNDLE.fullmatch(name):
        return Route("site-common-action-effect", "site-common-action-effects")
    if SITE_HARVEST_OBJECT_EFFECT_BUNDLE.fullmatch(name):
        return Route("site-harvest-object-effect", "site-harvest-object-effects")
    if SITE_HOME_EFFECT_BUNDLE.fullmatch(name):
        return Route("site-home-effect", "site-home-effects")
    if FIXTURE_PUT_EFFECT_BUNDLE.fullmatch(name):
        return Route("fixture-put-effect", "fixture-put-effects")
    if "__character__mdl_sd_" in name:
        return Route("character", "characters")
    if name == "mysekai__character_motion":
        return Route("motion", "motion-library")
    if name == "mysekai__character_settings":
        return Route("facial", "facial-tables")
    if name == "mysekai__character_alone_action":
        return Route("performance", "alone-actions")
    if "__effect__emoticon__" in name:
        return Route("emoticon", "emoticons")
    if name == "mysekai__talk__scenario__talk":
        return Route("talk", "talks")
    if name == TALK_CONSTANTS_PACKAGE:
        return Route("talk-constants", "fixture-talks")
    if name == CAMERA_PACKAGE:
        return Route("camera", "perf")
    if name == HOUSE_TRANSITION_PACKAGE:
        return Route("house-transition", "prefab-package")
    if name == FIXTURE_REACTION_PACKAGE:
        return Route("fixture-reaction", "fixture-reactions")
    if name.startswith(AVATAR_PART_PREFIX):
        rest = name[len(AVATAR_PART_PREFIX):]
        if any(rest.startswith(category + "__") for category in AVATAR_PART_CATEGORIES):
            return Route("avatar-part", "avatar-parts")
    if (PHENOMENA_BUNDLE.match(name) or name == PHENOMENA_THUMBNAIL
            or PHENOMENA_THUMBNAIL_ITEM.fullmatch(name)):
        return Route("phenomena", "phenomena")
    if name.startswith(SITE_PREFIX):
        return Route("site", "sites")
    if name.startswith(SOUND_PREFIX):
        return Route("sound", "phenomena")
    # The fixture-interface check must come before the two timeline families: its
    # prefix ``mysekai__fixture__`` is safe on its own (it does not match the
    # ``mysekai__fixture_timeline__`` family), but the shorter ``mysekai__fixture``
    # would, so this order lets a regression there fail loudly instead of silently
    # merging the two families into one job.
    if name.startswith(FIXTURE_PREFIX):
        return Route("fixture-interface", "fixtures")
    if name.startswith(CUT_SCENE_PREFIX):
        return Route("cutscene-timeline", "perf")
    if name.startswith(FIXTURE_TIMELINE_PREFIX):
        return Route("fixture-timeline", "perf")
    return None
