# SIB Studio Mixer — User Guide

*Shadows in the Booth Studio Mixer, by Tevin Dahl (Red Eyes) and Erik Lewis (Blue Eyes).*

## 1. The screen
| Area | What's there |
|---|---|
| **Transport bar** (top) | ⏮ start · ▶ play/stop · ● REC · ⟲ Loop · ♩ Click (metronome) · Count-in · 🎧 Monitor · time / bar.beat · key · BPM · tuning · Snap · A/B Dry · Master preview · ✨ Auto-Match · IN/OUT meters |
| **Timeline** (centre) | Ruler (bars + time, loop, markers) · track headers (name, role, M/S/●, FX bypass, volume, pan) · clips with waveforms, fades, takes, breath markers (orange) |
| **Inspector** (right) | Tabs: **Auto-Match · FX Rack · Lyrics · Ad-libs · Breaths · Pitch / Key · Markers · Take Coach · Spectral · Analyzer** |
| **Mixer** (View > Mixer) | Faders, pan, mute/solo for every track + master with meter |

## 2. Recording over a beat
1. **Tools > Audio settings.** Choose your input (interface/mic), input channel, and output (headphones).
   - Prefer **[Windows WASAPI]** or **[ASIO]** devices. **Buffer size** 256–512 is a good start; lower = less delay, but more CPU.
   - **Recording latency fix:** if takes sound late against the beat, raise this a few ms (raise it more if they still land late).
2. Click **●** on a vocal track to arm it (only one armed at a time). The **IN** meter shows your level. Aim for peaks around
   -18 to -12 dBFS (the meter's yellow zone). After each take SIB warns you if it clipped or was too quiet.
3. Press **R** (or ● REC). The count-in plays (Off / 1 bar / 2 bars), then recording starts at the playhead.
4. **Space** stops. The take appears as a clip named *“Lead Vocal take N”*.
5. **Loop recording:** Shift-drag on the ruler to set a loop (or select a range and press **L**), turn Loop on and record.
   Each pass becomes a new take on top of the last. Right-click a take > **Use this take (bring to front)** to pick
   which one plays, or split the takes and bring different pieces to the front to comp the best lines.
6. **Monitor** lets you hear your dry mic in your headphones (use it only if your interface has no direct monitoring).
7. **Click** plays the metronome while recording (Transport > *Metronome during playback too* for playback).

## 3. Editing
- **Click** an empty spot to move the playhead. **Drag** to select a time range (Shift = add tracks / extend).
- **Drag a clip** to move it (snaps to the grid unless you hold Alt; drag up/down to move to another track).
- **Clip edges** trim; the **white squares** at the top corners drag fades.
- **Double-click** a clip to select its range.
- **Right-click** a clip: split, use take, mute, clip gain, rename, duplicate, quick fades, find breaths, analyse pitch, delete.
- **Edit menu:** cut/copy/paste (paste goes to the selected track at the playhead), delete (leaves a gap), ripple delete
  (closes the gap), split at playhead, trim to selection, duplicate, nudge ±10 ms, select all.
- **Process menu** changes the audio permanently (still undoable). It works on the selected range, or on the selected clip(s):
  amplify, normalize, normalize to LUFS, fade in/out, silence, reverse, invert, noise reduction, de-click, de-plosive,
  de-hum, pitch shift, time stretch (clip), hard auto-tune.
- **Noise print:** select a bit of pure room noise (no voice) > *Process > Capture noise print*, then select the vocal >
  *Noise reduction*. Without a print, SIB learns the noise from the quietest parts automatically.

## 4. Auto-Match (Ctrl+M)
**Listen to beat** detects key (with confidence and alternatives), BPM (half/double shown), grid start, real tuning
(A4 Hz) and loudness. It runs automatically when you import a beat. If the key looks wrong, pick one of the alternatives in
**Manual override** (or tap the tempo) and click *Apply override*.

**Auto-Match vocals** options:
| Option | Meaning |
|---|---|
| Style | *Keep each track's chain* (uses the role's default chain) or load one style for the matched tracks |
| Auto-Tune | **auto** (natural for rapping, tighter for singing), **rap** (45 ms, 70%), **melodic** (18 ms), **hard** (0 ms robotic), **off** |
| Vocal level vs beat | How many LU louder than the beat the lead sits (doubles/ad-libs automatically lower) |
| Carve space in the beat | Enables **Beat Carve** on the beat: a sidechain dynamic EQ dip at the vocal's clarity zone, only while vocals play |
| Nudge timing | Moves a take that is consistently early/late onto the 1/16 grid (only if it clearly helps) |
| Selected track only | Match just the current track |

The report lists every change, so you can see what moved and why.

## 5. FX Rack
- Shows the chain of the selected track (click a track header). **Master bus** button edits the master chain.
- Tick/untick to enable, drag or ▲▼ to reorder, *Duplicate*, *Remove*, **+ Add effect** (by category).
- Select an effect to see **every setting**, with slider + exact value. Hover any setting for advice. *Reset to defaults*.
- **Style > Load** replaces the chain with a style; **Save…** stores your chain as a ★ preset (in `%APPDATA%\SIB Studio Mixer\presets`).
- **Apply this effect to selection** prints the current effect into the selected audio (Waveform-editor style).
- The track header's **FX** button bypasses the whole chain for A/B; the toolbar **A/B Dry** does it for the whole song.
- Changes are re-rendered in the background while playing (heavy effects like SIB Tune take a second or two).

Full list of the 31 effects and their 180 settings: [EFFECTS_REFERENCE.md](EFFECTS_REFERENCE.md).

### Typical rap chain order (what the styles use)
Channel tools → Noise reduction → De-click → De-plosive → Breath control → **SIB Tune** → EQ → Vocal Rider →
Compressor → De-esser → Parallel compressor → Saturation → Delay → Reverb.

## 6. Breaths
1. Select a vocal track > **Detect breaths** (sensitivity and min/max length adjustable).
2. Breaths show as **orange bars** on the timeline and in the list. Click one to jump there and select it.
   Untick any you want to keep (a breath before a big line can add energy).
3. Choose **reduce** (e.g. -12 dB; natural), **target** (every breath to the same quiet level), or **silence**, and a fade.
4. **Apply to ticked breaths.** Timing never changes.

The *Breath Control* effect in each chain also does this automatically on every render. Use the tab when you want to review breaths by hand.

## 7. Pitch / Key
Select a vocal clip > **Analyze pitch**. The graph shows your voice (grey) against the result after SIB Tune (red),
with the notes in the key as blue lines. It also reports your range, average pitch drift, whether you're rapping or singing,
and the key your melody fits. **Use the vocal's key** sets the project key from your melody (useful on beats with
ambiguous harmony).

## 8. Exporting
- **Ctrl+E Export mixdown:** file, format (WAV 16/24/32f, MP3 320, FLAC), range (song/selection/loop), master chain on/off,
  loudness target, stems (wet and/or dry). It reports the final LUFS and true peak.
  - Streaming platforms normalise to about -14 LUFS. Loud rap masters are often -9 to -11 LUFS. The ceiling is -1 dBTP.
- **Ctrl+Shift+A Export to Adobe Audition:** writes `Audition\Song.sesx` + WAVs:
  - *Processed + dry (recommended):* SIB-processed tracks play, and the editable dry clips sit underneath, muted.
  - *Processed only* or *Dry only*.
  - Optional breath markers. Markers also come as a CSV for Audition's Markers panel.
  - Opens Audition automatically if it's installed.

## 9. Projects & safety
- Projects live in `Documents\SIB Studio Projects\<Song>\` unless you choose otherwise.
- Auto-save every 3 minutes once a project has been saved. You're asked to save on exit.
- Every recording and edit is kept as its own WAV in `Audio\`, so undo can always go back.

## 10. Troubleshooting
| Problem | Fix |
|---|---|
| No sound / “Could not start audio” | Tools > Audio settings: pick the output device explicitly; try a WASAPI device; close other apps using the interface exclusively |
| Crackles | Raise buffer size (512 → 1024) |
| Takes land late/early | Adjust *Recording latency fix* in Audio settings |
| MP3 beat won't import | Install FFmpeg (`winget install ffmpeg`) or convert to WAV |
| Auto-tune sounds wrong | Check the key in Auto-Match; try an alternative key; check the beat's tuning (A4); raise retune speed / Flex-Tune for natural sound |
| Vocal buried | Raise *Vocal level vs beat*, enable Beat Carve, raise EQ presence |
| Esses too harsh / lispy | De-esser *Threshold offset* down (more) or *Max reduction* down (less) |


---

# New in 1.1

## 11. Lyrics & teleprompter
**Writing.** Open the **Lyrics** tab. Write one line per bar. Section headers like `[Verse 1]` or `Hook:` turn blue.
Start a line with `{2}` if it spans 2 bars.
- Rhyming words are coloured by sound. *Rhymes*: **end** (last word of each line), **all** (internal rhymes too),
  **strict end** (perfect rhymes only).
- The gutter shows each line's **bar** and **syllable count**. The count turns yellow at 13+ and red at 17+, where
  it's hard to fit at most tempos. The status line shows syllables/bar and syllables/second at the beat's BPM.
- **Rhyme finder:** type a word or click one in your lyrics. Tabs show **Perfect** (cat/hat), **Multis**
  (fire/higher/buyer, which match the last two vowel sounds) and **Slant** (time/mind/right). Double-click a word
  to insert it.
- **Templates:** 16-bar verse, 8-bar hook, full song layouts.
- **Loop 4 / 8 / 16:** loops that many bars from the playhead so you can write over the beat.
- *File ▾*: import/export `.txt`, export synced `.lrc` (used by streaming lyrics services), and add section
  markers to the timeline.

**Placing lyrics on the beat.** Set **Start bar** (or click *= playhead*) and **Bars/line**. For exact timing,
turn on **⏱ Tap-sync**, play the beat and press **T** right as each line starts. Synced lines show ⏱ in the gutter.

**Teleprompter** (*Ctrl+L*, or the 🎤 Prompter button):
- The current line is big and fills red→blue as the bar plays. The next lines and the previous line are shown.
- Bar/beat lights, a "starts in" timer, and a big countdown during the count-in.
- Drag the window to a second monitor or tablet. **F11** or double-click = full screen, **+/-** or mouse wheel =
  text size, **M** = mirror (for teleprompter glass), **Esc** = close.
- **Space**, **R** and **T** still control the studio while the teleprompter has focus.

## 12. Punch in / out
Select the range to fix on the vocal track, turn on **Punch** in the transport bar, and press **R**. Playback starts
1 bar early so you catch the flow. Recording only happens inside the selection, then it stops by itself.
The new take goes on top (older takes stay underneath for comping).

## 13. Ad-lib Creator & Library (Ad-libs tab)
1. Drag over an ad-lib on a vocal track (or click its clip) > **Save selection as ad-lib** (or *Vocal > Save
   selection as ad-lib*).
2. Name it, pick a category (Hype, Yeah/Uh/Ay, Laughs, Shouts, Whispers, Signature tag, Producer tag…) and a
   **style** to print in: Ad-lib chain, Phone/radio, Pitch up (chipmunk), Pitch down (demon), Octave stack,
   Reverse, Stutter, Delay throw, Big reverb. Silence is trimmed and the level normalised.
3. Inserting: select it in the library, then **Insert** (or double-click). It goes to the selected vocal track,
   or the Ad-libs track if you're on the beat. Options: *Once at playhead*, *Every bar / 2 bars / 4 bars in the
   selection*, snap to beat, or a new ad-lib track.
4. Use ★ to favourite, ✎ to rename, 🗑 to delete, ▶ to preview (*auto* previews as you scroll).
5. **Export pack / Import pack** zips the library so Red Eyes and Blue Eyes can share ad-libs and producer tags.

The library lives in `%APPDATA%\SIB Studio Mixer\adlibs` and starts with generated producer FX (riser,
downlifter, 808 sub drop, impact, reverse swell, laser).

## 14. Take Coach
Select a track > **Rate takes**. Each clip/take is scored out of 100 and ranked 🥇🥈🥉. Click one to see tips and
details: peak level, clipping, voice vs noise, level swings, timing off the grid (ms), syllables per second,
pitch drift, pops, esses, breaths per minute. **Use best take** brings the winner to the front.

## 15. Vocal editing tools (Vocal menu)
| Tool | What it does |
|---|---|
| Strip silence | Splits clips into phrases and removes the silence between them (non-destructive) |
| Align to lead | Moves each phrase of a double/stack/harmony so its consonants line up with the Lead track |
| Quantize phrases | Pulls the start of each phrase toward the 1/16, 1/8, 1/4 or 1/8-triplet grid. *Strength* keeps some human feel |
| Delay / reverb throw | The selected word(s) echo on the beat, or bloom into a big space. Shows as a coloured bar on the clip |
| Reverse-reverb swell | A reversed reverb that "sucks in" to the selected word |
| Remove throws | Clears throws inside the selection |

**Process menu additions:** stutter edit (1/16 or 1/8), tape stop, tape start, filter sweep down/up.
**Spectral tab:** *Show spectrum* for the selected clip/range. Drag a box around a noise (cough, chair, phone
buzz, harsh S) and choose *Apply to box* to turn just that region down.

## 16. Automation
Click **A** on a track header. Click the lane to add volume points, drag them, and right-click a point to delete
it. Hold Ctrl to edit clips while automation is shown. Use it to ride the beat down for a spoken intro, push a
line up, or fade an outro.

## 17. Send buses & mixing
- **Mixer** (View > Mixer, or Mix > Mixer): each strip has **Rev** and **Dly** send sliders (far left = off).
  The **Reverb bus** and **Delay bus** strips have their own fader, mute and **FX** button, which edits the bus
  effect in the FX Rack.
- Quick setups: *Mix > Send all vocals to Reverb bus* and *Send ad-libs to Delay bus*.
- Using one shared reverb for all the vocals makes lead, doubles and ad-libs sound like one performance in one
  room.

## 18. Reference track, Match EQ & Analyzer
- *Mix > Import reference track* loads a finished song you like (excluded from exports). **REF** in the transport
  flips playback to it, loudness-matched to your mix, so you compare tone and balance rather than volume.
- *Mix > Match selected track's tone to reference* (use an acapella reference for vocals) or *Match master to
  reference track* adds a **Match EQ** at 70%. Adjust *Amount* in the FX Rack.
- **Analyzer tab:** live spectrum with peak hold; LUFS (momentary / short-term / integrated); peak (red near
  0 dB); phase correlation (red when negative, which means mono problems). **Find where beat and vocals clash**
  overlays the beat (red) and vocals (blue) and shades the clash zones, then tells you where to put Beat Carve or
  an EQ boost.

## 19. Beat tools (Mix menu)
- **Transpose beat** ±6 semitones to fit the beat to your voice. The project key follows, so SIB Tune stays correct.
- **Change beat tempo** time-stretches the beat to a new BPM without changing its key.
- **Beat drop on selection** cuts the beat out under a line (the classic drop before the hook).
- **Remove vocals from beat** adds a centre-cancel Vocal Remover for beats that still have a vocal on them.

## 20. Importing Adobe Audition sessions
*File > Import Adobe Audition session (.sesx)*, Ctrl+Shift+I, or drag the `.sesx` onto SIB.

| Comes across | How |
|---|---|
| Tracks, clip positions, source in/out, clip gain, clip mute, fades | Exactly |
| Track volume, pan, mute, solo; tempo, time signature; markers | Exactly |
| Media files | Found by relative path, absolute path, or by searching the session folder |
| Track effect racks, the track EQ, the master rack | Each effect becomes its SIB equivalent, with settings mapped by name. Audition stores settings normalised, so treat them as close starting points |
| Clip effects | The clip moves to its own "(clip FX)" track with those effects plus the track's effects |
| Sends to reverb/delay buses | Mapped onto SIB's shared Reverb/Delay bus with the bus's reverb/delay settings |
| Plug-ins (Auto-Tune, Melodyne, FabFilter Pro-Q/C/L/DS, Waves, Valhalla, EchoBoy, Decapitator, Ozone, RX, Soothe…) | Replaced by the matching SIB effect with default settings (plug-in settings are private to the plug-in) |
| Anything else | Kept as a bypassed "Audition effect (imported)" placeholder so nothing disappears silently |

Afterwards an **import report** lists every track, clip count, effect mapping, missing file and skipped item. The
imported project is saved as a new SIB project. Your original Audition session is never changed.
