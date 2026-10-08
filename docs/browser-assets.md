# Browser character portraits

The browser catalog can use the original SD character portraits from the APK's
`CharacterSDIconAtlas`. Export them into an existing extracted runtime root:

```sh
moly browser-assets --player-data /path/to/data.unity3d \
  --asset-root /path/to/runtime \
  --source-region cn --source-game-version 6.0.0
```

Use the actual region and version of the supplied player data. The command reads
`characters.json` from the runtime root and writes
`ui/character-portraits/character-portraits.json` plus PNG files. The manifest's
`characters` array contains `unitId`, `gameCharacterId`, `name`, and an `image`
path relative to the runtime root. Image filenames include their content hash;
publish the manifest and its referenced images together when hosting a root.

The atlas uses `chr_sp_{unitId}`. Separate Virtual Singer incarnations retain
their own source portrait even when they share a `gameCharacterId`. Missing
sprites are reported in `coverage.missing` and omitted from the image list;
the browser can then display its normal text fallback. Pixels retain the
original size, packing mask and transparency.

This is an explicit UI export. Reusing a player-data file as a UI donor for
another region records its original region, game version and SHA-256 under
`source`, with `kind: "shared-ui"`. It does not replace that region's gameplay
tables or character identities. No game pixels are committed to this repository.
