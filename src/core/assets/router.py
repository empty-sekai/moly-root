"""Bundle-name routing with explicit unsupported-domain results."""
from dataclasses import dataclass


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
    return None
