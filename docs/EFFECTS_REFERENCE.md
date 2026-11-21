# SIB Studio Mixer — Effects & Settings Reference

Every effect below can be added to any track from the **FX Rack** (`+ Add effect`), reordered by dragging, toggled with its checkbox,
and applied permanently to a selection with **Apply this effect to selection**. Hover any control in the app for the same tips.

Effects marked *(sidechain)* belong on the **beat** track — they listen to the vocals.

## Repair

### Noise Reduction

Spectral noise reduction that learns the noise print from the quietest parts of the take.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Reduction | 12 dB | 0 – 40 dB | How much background noise (fans, hiss, room) is pulled down. 6-12 dB sounds natural. |
| Sensitivity | 1.5 | 1 – 4 | Higher treats more of the signal as noise. Too high eats vocal detail. |
| Smoothing | 0.5 | 0 – 1 | Reduces watery 'musical noise' artifacts. |

### Hum Remover

Removes electrical hum and its harmonics.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Mains frequency | 60 | 50, 60 | 60 Hz in the US. |
| Harmonics | 6 | 1 – 12 |  |
| Notch narrowness | 30 | 5 – 80 |  |

### De-Click (mouth clicks)

Finds lip smacks, mouth clicks and digital ticks and repairs them.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Threshold | 6 | 2 – 20 | Lower finds more clicks. |
| Max click length | 2 ms | 0.5 – 6 ms |  |

### De-Plosive (pops)

Tames P and B pops from getting close to the mic.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Pop band | 150 Hz | 60 – 300 Hz |  |
| Sensitivity | 10 dB | 0 – 20 dB |  |
| Max reduction | 12 dB | 0 – 30 dB |  |

### Breath Control

Automatically finds breaths between bars and turns them down without changing timing.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Detection sensitivity | 0.5 | 0 – 1 | Higher finds more (and quieter) breaths. |
| Mode | reduce | reduce, target, silence | reduce = lower by a fixed amount, target = bring every breath to the same level, silence = mute. |
| Reduce by | -12 dB | -60 – 0 dB | -8 to -15 dB keeps the vocal human. Full removal can sound robotic. |
| Target level | -45 dBFS | -80 – -20 dBFS |  |
| Fade | 15 ms | 1 – 60 ms |  |
| Shortest breath | 120 ms | 40 – 400 ms |  |
| Longest breath | 1200 ms | 300 – 3000 ms |  |

## Pitch

### SIB Tune (Auto-Tune)

Real-time-style pitch correction locked to the beat's key and tuning (formant-preserving).

| Setting | Default | Range | What it does |
|---|---|---|---|
| Key | Auto (from beat) | 13 options | Auto uses the key detected from the beat (Tools > Analyze Beat). |
| Scale | Auto (from beat) | 13 options |  |
| Tuning ref (A4) | 0 Hz | 0 – 466 Hz | 0 = auto: match the beat's real tuning (many beats are not at A440). |
| Retune speed | 25 ms | 0 – 400 ms | 0-10 ms = hard robotic T-Pain/Travis effect. 15-25 = melodic rap. 25-80 = natural. |
| Humanize | 20 % | 0 – 100 % | Loosens correction on held notes so they breathe while short notes stay tight. |
| Flex-Tune | 15 cents | 0 – 100 cents | Notes already close to pitch are left alone - keeps slides and expression. 0 for hard tune. |
| Correction amount | 100 % | 0 – 100 % |  |
| Note stickiness | 20 cents | 0 – 50 cents | Stops the tuner flipping between two notes on a wobbly pitch. |
| Transpose | 0 st | -12 – 12 st |  |
| Throat / formant | 0 st | -6 – 6 st | Changes voice character without changing pitch. |
| Lowest note | 60 Hz | 40 – 300 Hz |  |
| Highest note | 900 Hz | 300 – 1600 Hz |  |
| Mix | 100 % | 0 – 100 % |  |

### Harmonizer

Generates in-key harmony voices from the vocal - instant hook stacks.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Key | Auto (from beat) | 13 options | Auto uses the key detected from the beat (Tools > Analyze Beat). |
| Scale | Auto (from beat) | 13 options |  |
| Tuning ref (A4) | 0 Hz | 0 – 466 Hz | 0 = auto: match the beat's real tuning (many beats are not at A440). |
| Voice 1 interval | 2 deg | -7 – 7 deg | Scale degrees: 2 = a third up, 4 = a fifth up, -3 = a fourth down, 7 = octave. |
| Voice 1 level | -8 dB | -40 – 0 dB |  |
| Voice 1 pan | -0.5 | -1 – 1 |  |
| Voice 2 on | off | on / off |  |
| Voice 2 interval | -3 deg | -7 – 7 deg |  |
| Voice 2 level | -10 dB | -40 – 0 dB |  |
| Voice 2 pan | 0.5 | -1 – 1 |  |
| Formant shift | 0 st | -6 – 6 st |  |
| Retune speed | 10 ms | 0 – 200 ms |  |

### Pitch Shifter

Shifts pitch without changing length.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Semitones | 0 st | -12 – 12 st |  |
| Fine | 0 cents | -100 – 100 cents |  |
| Formant | 0 st | -6 – 6 st |  |
| Mode | vocal | vocal, full mix / beat | Use 'full mix / beat' to transpose a beat to fit your voice. |

### Vocal Doubler

Fakes a double-tracked vocal for a thicker, wider sound.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Voices | 2 | 1 – 6 |  |
| Detune | 8 cents | 0 – 30 cents |  |
| Delay | 18 ms | 2 – 60 ms |  |
| Stereo spread | 0.8 | 0 – 1 |  |
| Double level | 50 % | 0 – 100 % |  |

## EQ & Filter

### Parametric EQ

Studio vocal EQ: rumble filter, mud/nasal cuts, presence and air.

| Setting | Default | Range | What it does |
|---|---|---|---|
| High-pass on | on | on / off |  |
| High-pass | 90 Hz | 20 – 400 Hz | Removes rumble and boom. 80-120 Hz for rap leads, 150-200 for doubles/ad-libs. |
| HP slope | 24 dB/oct | 12 dB/oct, 24 dB/oct, 48 dB/oct |  |
| Low shelf | 150 Hz | 40 – 500 Hz |  |
| Low shelf gain | 0 dB | -12 – 12 dB |  |
| Mud band | 300 Hz | 150 – 600 Hz |  |
| Mud gain | -2.5 dB | -12 – 12 dB | Cut 2-4 dB at 200-400 Hz to remove boxiness. |
| Mud Q | 1 | 0.2 – 8 |  |
| Nasal band | 900 Hz | 500 – 2000 Hz |  |
| Nasal gain | -1 dB | -12 – 12 dB |  |
| Nasal Q | 1.5 | 0.2 – 8 |  |
| Presence | 3500 Hz | 1500 – 8000 Hz | Boost 2-4 dB at 2-5 kHz so the words cut through the beat. |
| Presence gain | 2.5 dB | -12 – 12 dB |  |
| Presence Q | 0.8 | 0.2 – 8 |  |
| Air shelf | 12000 Hz | 6000 – 18000 Hz |  |
| Air gain | 2.5 dB | -12 – 12 dB |  |
| Low-pass on | off | on / off |  |
| Low-pass | 18000 Hz | 2000 – 20000 Hz |  |

### Filter (HP/LP/Band)

Simple filter - good for intros, telephone sections and beat drops.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Type | highpass | highpass, lowpass, bandpass, notch |  |
| Frequency | 200 Hz | 20 – 20000 Hz |  |
| Q | 0.707 | 0.1 – 20 |  |
| Steepness | 2 | 2, 4, 8 |  |

## Dynamics

### Noise Gate / Expander

Quiets the noise between words.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Threshold | -45 dB | -80 – 0 dB |  |
| Ratio | 4 :1 | 1 – 50 :1 | 20+ acts as a hard gate. |
| Range | -30 dB | -80 – 0 dB |  |
| Attack | 1 ms | 0.1 – 50 ms |  |
| Hold | 60 ms | 0 – 500 ms |  |
| Release | 120 ms | 5 – 1000 ms |  |

### Compressor

Evens out the performance so every bar sits at the same level.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Threshold | -20 dB | -60 – 0 dB |  |
| Ratio | 4 :1 | 1 – 20 :1 | 4:1 laid back, 6:1 aggressive. |
| Attack | 10 ms | 0.1 – 100 ms | 5-15 ms lets consonants punch through. |
| Release | 80 ms | 5 – 1000 ms |  |
| Knee | 6 dB | 0 – 24 dB |  |
| Makeup gain | 0 dB | -12 – 24 dB |  |
| Auto makeup | on | on / off |  |
| Detector | peak | peak, rms |  |
| Mix (parallel) | 100 % | 0 – 100 % | Below 100% = parallel/New York compression. |

### De-Esser

Tames harsh S, SH and T sounds.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Frequency | 6500 Hz | 3000 – 12000 Hz | 5-8 kHz for most voices. |
| Threshold offset | 0 dB | -20 – 20 dB | Lower = more de-essing. |
| Max reduction | 6 dB | 0 – 20 dB | 3-6 dB. Too much makes the rapper sound lispy. |
| Mode | split | split, wide |  |
| Auto threshold | on | on / off |  |
| Listen (solo the esses) | off | on / off |  |

### Vocal Rider (auto level)

Rides the fader for you - quiet words come up, shouted words come down.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Target level | -18 dB | -40 – -6 dB |  |
| Max boost | 6 dB | 0 – 18 dB |  |
| Max cut | 6 dB | 0 – 18 dB |  |
| Speed | 150 ms | 20 – 1000 ms |  |
| Ignore below | -50 dB | -80 – -20 dB |  |

### Multiband Compressor

Controls lows, mids and highs separately.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Low/Mid split | 200 Hz | 60 – 800 Hz |  |
| Mid/High split | 4000 Hz | 1500 – 10000 Hz |  |
| Low threshold | -24 dB | -60 – 0 dB |  |
| Low ratio | 3 | 1 – 20 |  |
| Low gain | 0 dB | -12 – 12 dB |  |
| Mid threshold | -24 dB | -60 – 0 dB |  |
| Mid ratio | 2 | 1 – 20 |  |
| Mid gain | 0 dB | -12 – 12 dB |  |
| High threshold | -28 dB | -60 – 0 dB |  |
| High ratio | 3 | 1 – 20 |  |
| High gain | 0 dB | -12 – 12 dB |  |

### Hard Limiter

Brick-wall limiter that stops clipping.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Input gain | 0 dB | -12 – 24 dB |  |
| Ceiling | -1 dBFS | -12 – 0 dBFS |  |
| Release | 60 ms | 5 – 500 ms |  |
| Look-ahead | 3 ms | 0.5 – 10 ms |  |

## Color

### Saturation

Harmonic warmth and grit that helps the vocal glue to the beat.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Type | tape | tape, tube, warm, hard | Tape for warmth, tube for grit (trap/drill). |
| Drive | 6 dB | 0 – 30 dB |  |
| Mix | 35 % | 0 – 100 % |  |
| Tone | 0 dB | -6 – 6 dB |  |
| Output | 0 dB | -12 – 12 dB |  |

### Exciter (clarity)

Adds crisp upper harmonics - great for super-clear rap vocals.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Frequency | 3500 Hz | 1500 – 10000 Hz |  |
| Drive | 12 dB | 0 – 30 dB |  |
| Amount | 20 % | 0 – 100 % |  |

### Lo-Fi / Bitcrusher

Crunchy lo-fi texture.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Bits | 10 | 3 – 16 |  |
| Downsample | 2 | 1 – 16 |  |
| Mix | 50 % | 0 – 100 % |  |

### Telephone / Radio

Phone / megaphone vocal for intros and hooks.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Low cut | 400 Hz | 100 – 1500 Hz |  |
| High cut | 3400 Hz | 1500 – 8000 Hz |  |
| Drive | 6 dB | 0 – 24 dB |  |
| Mix | 100 % | 0 – 100 % |  |

## Space

### Delay / Echo

Tempo-synced echoes, locked to the beat's BPM.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Sync to beat BPM | on | on / off |  |
| Note | 1/4 | 12 options |  |
| Time (unsynced) | 250 ms | 10 – 2000 ms | 80-120 ms with 0 feedback = slapback. |
| Feedback | 25 % | 0 – 95 % |  |
| Mix | 12 % | 0 – 100 % |  |
| Ping-pong | on | on / off |  |
| Low cut | 300 Hz | 20 – 2000 Hz |  |
| High cut | 6000 Hz | 1000 – 20000 Hz |  |

### Reverb

Plate/room/hall space for the vocal.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Decay | 1.1 s | 0.1 – 8 s | 0.8-1.5 s plate for rap. |
| Pre-delay | 30 ms | 0 – 200 ms | 20-40 ms keeps the words up front. |
| Sync pre-delay to BPM (1/64) | off | on / off |  |
| Damping | 0.5 | 0 – 1 |  |
| Size | 0.6 | 0 – 1 |  |
| Width | 1 | 0 – 1.5 |  |
| Early reflections | 0.3 | 0 – 1 |  |
| Mix | 12 % | 0 – 100 % |  |
| Low cut | 250 Hz | 20 – 1000 Hz |  |
| High cut | 9000 Hz | 2000 – 20000 Hz |  |

### Chorus

Shimmering width.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Rate | 0.8 Hz | 0.05 – 8 Hz |  |
| Depth | 3 ms | 0 – 15 ms |  |
| Delay | 12 ms | 1 – 40 ms |  |
| Mix | 30 % | 0 – 100 % |  |

### Stereo Width

Narrows or widens the stereo image.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Width | 1 | 0 – 2 | 0 = mono, 1 = unchanged, 2 = extra wide. |

## Mix

### Beat Carve (make room for vocals) *(sidechain)*

Put this on the BEAT. It dips the beat's vocal range only while vocals play (sidechain dynamic EQ).

| Setting | Default | Range | What it does |
|---|---|---|---|
| Carve frequency | 3000 Hz | 500 – 8000 Hz | The vocal's clarity zone. 2-4 kHz for most rappers. |
| Carve depth | -4 dB | -15 – 0 dB |  |
| Width (Q) | 0.8 | 0.2 – 4 |  |
| Overall duck | -1 dB | -12 – 0 dB | Turns the whole beat down a little while someone is rapping. |
| Vocal threshold | -40 dB | -70 – -10 dB |  |
| Attack | 15 ms | 1 – 200 ms |  |
| Release | 250 ms | 20 – 2000 ms |  |

### Sidechain Duck (pump) *(sidechain)*

Ducks the whole track under the vocals.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Duck amount | -6 dB | -24 – 0 dB |  |
| Vocal threshold | -35 dB | -70 – -5 dB |  |
| Attack | 5 ms | 0.5 – 100 ms |  |
| Release | 200 ms | 10 – 2000 ms |  |

## Utility

### Gain / Amplify

Turns it up or down.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Gain | 0 dB | -48 – 24 dB |  |

### Normalize / Match Loudness

Brings the level to an exact peak or loudness target.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Mode | peak | peak, loudness (LUFS) |  |
| Peak target | -1 dBFS | -24 – 0 dBFS |  |
| Loudness target | -16 LUFS | -36 – -5 LUFS |  |

### Channel Tools

Polarity, mono, channel swap, DC removal.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Invert polarity | off | on / off |  |
| Sum to mono | off | on / off |  |
| Swap L/R | off | on / off |  |
| Remove DC offset | on | on / off |  |

## Master

### Mastering (glue + loudness)

Final polish: glue compression, tone, width and loud-but-clean limiting.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Glue compressor | on | on / off |  |
| Glue threshold | -14 dB | -40 – 0 dB |  |
| Glue ratio | 2 | 1 – 6 |  |
| Low end | 0 dB | -6 – 6 dB |  |
| Top end | 0.5 dB | -6 – 6 dB |  |
| Width | 1 | 0.5 – 1.5 |  |
| Loudness target | -10 LUFS | -20 – -6 LUFS | -14 = streaming normalised, -9 to -11 = loud rap master. |
| Ceiling | -1 dBTP | -3 – 0 dBTP |  |
