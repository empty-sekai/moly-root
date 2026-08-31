"""Compiled shader programs: what a package's shaders declare and contain.

Three layers, bottom up:

* :mod:`shaders.blob` -- one decompressed per-platform blob's index table and
  records, transcribed from the engine's own reader and verified by a byte
  account that a mis-sized field cannot balance.
* :mod:`shaders.objects` -- getting that blob out of a ``Shader`` asset:
  declared form, passes and light modes, and the per-platform slicing and LZ4
  join that produce the blob.
* :mod:`shaders.census` -- what a whole package contains, counted by record
  and by distinct program, which is the denominator a coverage claim needs.
"""
