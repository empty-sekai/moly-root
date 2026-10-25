# Display text and ordinary multi-character conversations

`moly talks` also exports ordinary conversations between two to five characters
under `commonSomeCharacter`. These talks have no fixture anchor. They retain
participant slots, source association rows, conditions, pre-actions, tweets,
script steps and voice cues. The runtime chooses the initiating character;
association metadata does not fix that conversation's leader.

The audio corpus reader includes this block. Existing solo `units` and
`birthdayParty` blocks keep their own semantics. A consumer must explicitly
support the new family before it can play these conversations.

## Text catalogues

Generate a translation source from caller-provided extracted documents:

```sh
moly text-catalog --corpus extracted/talks.json \
  --corpus extracted/fixture-talks/talks.json \
  --region jp --source-revision SOURCE_REVISION --out text-source.json
```

On the next source update pass `--previous text-source-previous.json`. Keep that
previous catalogue in durable versioned storage: it is the registry that
preserves text identities across updates. The output identifies scripts and
display units, with original text, source hashes, speakers, voice context and
bindings to the current script steps. Step numbers are bindings, not identity.

Unique voice anchors preserve identity when the text changes; unchanged text
can also retain an unambiguous identity. Changes invalidate the old source hash.
Ambiguous repeated lines after a source edit are retired and allocated new IDs
for review. A display-only label does not replace the original control label.
Tweets have their own master identities because multiple talk records can share
a script but reference different tweets.

The catalogue may contain original game text and derived context. It belongs in
the caller's asset/content storage, not in this source repository.

## Official translations

Generate a regional official overlay from two catalogues:

```sh
moly official-text --source jp-text-source.json --donor cn-text-source.json \
  --locale zh-CN --out official-zh-CN.json
```

The matcher requires compatible script identity, participant sets and per-line
speaker/voice context. A same-numbered talk is not enough. Unanchored or
incompatible lines are listed in `issues` for explicit review. A tweet also
needs matching source metadata and script associations. Identical or empty
donor text is not silently labelled a translation.

The overlay contains plain text, source IDs/hashes and donor provenance. It
never changes executable steps, voice cues, timing or region assets. A text
consumer can prefer matching official text, then a published community
translation, then the original. Source region and display locale are separate.

These commands prepare source catalogues and official candidates. They do not
publish a service, grant editorial approval, or verify runtime playback.
