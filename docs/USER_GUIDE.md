# SIB Studio Mixer — User Guide

*Shadows in the Booth Studio Mixer, by Tevin Dahl (Red Eyes) and Erik Lewis (Blue Eyes).*

## 1. The screen
| Area | What's there |
|---|---|
| **Transport bar** (top) | ⏮ start · ▶ play/stop · ● REC · ⟲ Loop · ♩ Click (metronome) · Count-in · 🎧 Monitor · time / bar.beat · key · BPM · tuning · Snap · A/B Dry · Master preview · ✨ Auto-Match · IN/OUT meters |
| **Timeline** (centre) | Ruler (bars + time, loop, markers) · track headers (name, role, M/S/●, FX bypass, volume, pan) · clips with waveforms, fades, takes, breath markers (orange) |
| **Inspector** (right) | Tabs: **Auto-Match · FX Rack · Breaths · Pitch / Key · Markers** |
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
