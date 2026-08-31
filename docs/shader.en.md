# Shader Extraction

[中文](shader.md)

`moly shader` reports what the shaders in one package **declare and contain**:

```sh
moly --unity-version 2022.3.62f3 shader path/to/decrypted/some_bundle --platform 9
```

```
35 shaders, 2545 program records, 925 distinct programs (platform 9)
   597 distinct /   1194 records  <family>
   210 distinct /    312 records  <family>
   ...
```

`--out <file>` writes the full census as JSON. `--programs <dir>` writes each program body to its
own file, named by **shader, platform and record index**. `--json` prints the counts alone. The
command exits non-zero if any platform's blob fails to parse.

## What this layer decodes

A `Shader` asset keeps its compiled output in `compressedBlob`: **every platform's programs back to
back** in one byte array, sliced by the four parallel tables `platforms`, `offsets`,
`compressedLengths` and `decompressedLengths`, and LZ4-decompressed per platform. The decompressed
region (the *blob*) opens with an index table followed by variable-length records.

**The index table holds two kinds of record, not one.** Since record version `202012090` (the Unity
2021.2 line):

| Kind | Contents |
|---|---|
| program | keyword sets (global and local) and the compiled program body |
| parameter | the reflection table: constant buffers and their members, and texture / sampler / buffer binding slots |

A parameter record is the table **several variants share**. Counting it as a program inflates the
denominator by roughly two fifths, which reads as progress being harder than it is. The two are
counted apart here, and `parameterRecords` has a name of its own.

A ShaderLab-level export (name, properties, pass tags, render state, fallback) stops at the program
code; the bytes of the reflection section are typically skipped. **This is the section this reader
reads.** Which uniforms a shader consumes, and which constant buffer or texture slot each is bound
to, has no second source: the container blocks and the blob are **compressed independently**, so a
raw byte search over the package returns nothing whether or not the name is there.

## Why it can be trusted

The engine's own reader is handed `record start + record length` when it takes a variant. A correct
parse therefore **consumes a record exactly**: a mis-sized field either overruns or leaves a hole,
and the account will not balance. That property is turned into a check here — `account()` reports

```
{'total': 2436, 'header': 28, 'records': 2408, 'gaps': [], 'gapBytes': 0, 'overlaps': [], 'balanced': True}
```

`balanced` means the header plus every record tiles the blob exactly, with no gap and no overlap.
This is not a spot check but a **byte-for-byte account**: missing one 4-byte field necessarily
leaves a 4-byte hole.

## Denominator discipline

Two numbers answer different questions, and a coverage claim needs both:

- **records** — every program record.
- **distinct programs** — records grouped by content hash. Variants that compile to identical code
  are **one** piece of work, not many, and the ratio is large in practice: 2,545 records on platform
  9 of one package are 925 distinct programs. Reviewing by record inflates the work nearly
  threefold and hides that the two heaviest families dominate it.

One further trap costs more than miscounting: **a set filtered by name is not a denominator.**
Naming is an author's habit, not a classification, so what a name filter drops is exactly the
irregularly named part — and that loss looks identical to absence. Pair it with a criterion that is
**blind to names**, such as which shaders the domain's materials actually point at. The denominator
is complete only when the two criteria differ by nothing.

## Boundaries

- **Nothing here interprets a program.** Bodies are addressed by
  `(shader, platform, record index, content hash)` and handed back verbatim. The moment a survey
  starts editing what it reports, it stops being usable as a denominator.
- When a platform's blob fails to parse, **the entry is kept with its error** rather than dropped.
  Dropping it would let a decode failure look like a family that simply has no programs, which is
  the one reading that must never be silent.
- Only record version `202012090` has been run against real data. Earlier versions have
  version-gated branches, but **no sample has exercised them**.
- Every blob observed so far carries a **single** LZ4 segment per platform. The multi-segment join
  order is written from the field layout and is **unverified against a sample**;
  `platform_blobs()` therefore reports the segment count it saw, so a caller can tell which case it
  is in instead of assuming.

## Layers

| Module | Responsibility |
|---|---|
| `shaders.blob` | one decompressed blob's index table and records, with the byte account |
| `shaders.objects` | getting that blob out of a `Shader` asset: declared form, passes and light modes, per-platform slicing and LZ4 join |
| `shaders.census` | what a whole package contains, counted by record and by distinct program |

A `Shader` object's `m_Name` is the **empty string**; the real name lives in `m_ParsedForm.m_Name`.
Anything that reads `m_Name` — including the cheap "peek the name without parsing" helpers — gets
`""` for every shader, so a survey filtered by name returns zero and **looks exactly like a true
negative**. `shaders.objects.shader_name()` is the only name reader that should be used.
