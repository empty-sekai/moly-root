"""Bundle-name routing with explicit unsupported-domain results."""
import re
from dataclasses import dataclass

# Environment packages of one phenomenon: a global variant, a shared common
# variant, and one unique variant per site.
PHENOMENA_BUNDLE = re.compile(
    r"^mysekai__effect__site__environment__"
    r"(?P<phenomenon>[0-9]+_[a-z0-9]+)__"
    r"(?P<variant>common|global|unique__[a-z0-9_]+)$")

# Every phenomenon icon lives in this one shared package.
PHENOMENA_THUMBNAIL = "mysekai__thumbnail__phenomena"

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


@dataclass(frozen=True)
class Route:
    domain: str
    extractor: str


def route(name):
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
    if PHENOMENA_BUNDLE.match(name) or name == PHENOMENA_THUMBNAIL:
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
