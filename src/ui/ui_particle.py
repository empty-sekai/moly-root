"""Serialized fields of the UI particle host (``Coffee.UIExtensions.UIParticle``).

A UIParticle is a MaskableGraphic that draws the ParticleSystems it lists into
the canvas.  After the Graphic and MaskableGraphic chains it serializes, in
declaration order: ``m_IsTrail`` and ``m_IgnoreCanvasScaler`` (bools),
``m_Scale`` (float) and ``m_Scale3D`` (Vector3), ``m_AnimatableProperties``
(each a shader property name and its property type), ``m_Particles`` (the
ParticleSystems it draws) and ``m_ShrinkByMaterial`` (bool).  Its baked mesh,
material lists and cached position are runtime state.  JP 6.8.1 and CN 6.0.0
declare the same fields.
"""
from __future__ import annotations

from ui import talk


def decode_ui_particle(r: talk.Reader) -> dict:
    d = talk.decode_graphic_base(r)
    d.update(talk.decode_maskable_graphic_base(r))
    d["m_IsTrail"] = r.bool4()
    d["m_IgnoreCanvasScaler"] = r.bool4()
    d["m_Scale"] = r.f32()
    d["m_Scale3D"] = r.vec3()
    count = r.i32()
    d["m_AnimatableProperties"] = [{"m_Name": r.string(), "m_Type": r.i32()}
                                   for _ in range(count)]
    d["m_Particles"] = talk.decode_pptr_list(r)
    d["m_ShrinkByMaterial"] = r.bool4()
    return d


DECODERS = {
    "Coffee.UIExtensions.UIParticle": decode_ui_particle,
}
