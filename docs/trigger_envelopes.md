# Trigger Envelopes (`Trigger`)

A **Trigger envelope** modulates one of an instrument's automation destinations every time a **different instrument** (or the same one) plays a note — on any track. Typical uses:

* **Sidechain-style ducking**: a pad or bass dips in volume every time the kick plays.
* **Cross-track filter pumps**: a hi-hat's cutoff opens on every snare hit.
* **Pitch dips, pan swings, wavetable or grain jumps** locked to another part's rhythm.

Trigger envelopes work on the Original Tracker, Tracker+ and Tracker Mini.

---

## 1. Getting Started

1. Select the sample-based instrument you want to **modulate** (for example, a pad).
2. Press **Instrument Parameters** twice to open **Instrument Automation (2/2)**.
3. In the first column (**Destination**), pick any of the 7 targets:
   **Volume**, **Panning**, **Cutoff**, **Wavetable Position**, **Granular Position**, **Granular Length**, **Finetune**.
4. In the second column (**Type**), choose **`Trigger`** (after `WT LFO`).
5. Set **Source** to the instrument that should **fire** the envelope (for example, your kick) and set **Amount** (negative values pull the destination down).

Now every note played by the Source instrument restarts the envelope on every track that is currently playing the modulated instrument.

---

## 2. Parameter Reference

| Column | Parameter | Range | Description |
| :--- | :--- | :--- | :--- |
| **3** | **Shape** | `Rev Saw`, `Saw`, `Triangle`, `Square`, `Random` | Contour of the one-shot envelope (see below). |
| **4** | **Steps** | `128 steps` – `1/64 step`<br>*(Volume: `24 steps` – `1/64 step`)* | How long the envelope lasts, in sequencer steps at the current tempo. Uses the same list as the LFO `Speed`. |
| **5** | **Source** | `01`–`48`, `M01`–`M16`, `S1`–`S3` | Instrument whose note-ons restart the envelope. Uses the same names as the pattern editor: sample instruments, MIDI instruments, and synth parts (Tracker+ / Mini). |
| **6** | **Amount** | `-100` – `+100` | Depth and direction. Negative values pull the destination **down**, positive values push it **up**. |
| **7** | **Repeat** | `1x` – `64x`, `Inf` | How many times the shape plays back-to-back after each trigger. Total length is **Steps × Repeat**. `Inf` keeps cycling until the Source plays its next note (a tempo-synced LFO whose cycle restarts on every Source note). |

### Shapes

Each cycle lasts **Steps**. With **Repeat** at `1x` the shape plays once and then returns to `0` (no modulation); higher values play it that many times in a row, and `Inf` keeps cycling.

| Shape | Contour over the envelope's duration | Typical use |
| :--- | :--- | :--- |
| **`Rev Saw`** | Starts at full Amount and falls linearly to `0` | Classic sidechain duck (with negative Amount) or a decaying filter blip |
| **`Saw`** | Rises linearly from `0` to full Amount, then snaps back to `0` | Swells and reverse-style pumps |
| **`Triangle`** | Rises to full Amount at the half-way point, then falls back to `0` | Smooth bumps |
| **`Square`** | At `1x`: full Amount for the whole duration (a gate). When repeating: full Amount for the first half of each cycle, `0` for the second half (like the LFO Square) | Hard gates and stutters |
| **`Random`** | A random level (`0` to Amount), held for each cycle; every repeat picks a new level | Per-hit variation, random stepped patterns |

> **Example – trance gate:** `Volume`, `Rev Saw`, `Steps 1 step`, `Source` = kick, `Amount -100`, `Repeat 4x` ducks the pad four times, once per step, after every kick.

### How Amount Applies to Each Destination

| Destination | `+100` / `-100` means |
| :--- | :--- |
| **Volume** | Gain is multiplied by `1 + envelope`: `-100` ducks to silence, `+100` boosts up to `2×` (`+6 dB`). |
| **Panning** | Shifts panning by up to the full pan range, then clamps. |
| **Cutoff** | Shifts cutoff by up to the full cutoff range, then clamps (filter must be enabled). |
| **Wavetable Position** | Shifts the wavetable window by up to the full window count. |
| **Granular Position** | Shifts the grain position by up to the full sample length. |
| **Granular Length** | Shifts grain length by up to the full grain-length range. |
| **Finetune** | Shifts finetune by up to `±100` cents. |

These scalings are identical to the existing Envelope / LFO types for each destination.

---

## 3. Behaviour Notes

* **Volume `Trigger` replaces the volume ADSR.** With `Volume` set to `Trigger`, notes play with a plain gate (exactly like the volume automation set to `Off`) and the trigger envelope scales their level.
* **Per track, in parallel.** If the modulated instrument plays on several tracks, every one of those tracks follows the same trigger. A track that starts a note in the middle of a running trigger joins it at the correct point.
* **Self-triggering.** Setting **Source** to the instrument itself makes the envelope restart on every one of its own notes — a tempo-synced one-shot envelope.
* **Live pads count.** Playing the Source instrument from the pads also fires the trigger, even with the sequencer stopped.
* **MIDI and synth sources.** MIDI instruments (`M01`–`M16`) and synth parts (`S1`–`S3`) fire triggers when they are played by the sequencer.
* **Step FX keep priority.** A pattern LFO Step FX on the same destination (for example `Volume LFO` or `Filter LFO`) or a Performance Mode LFO rate change overrides the Trigger envelope while it is active, the same way it overrides the instrument's own Envelope / LFO.
* **`w` / `y` Step FX** (`WT Volume Env` / `WT Filter Env`) have no effect on a `Trigger` destination.
* **Playback start clears triggers.** Pressing **Play** discards any trigger that was still running, including `Inf` repeats.
* **New Source notes restart the repeats.** A Source note that arrives before the repeats finish starts again from the first cycle.

---

## 4. File Compatibility

Trigger settings live in the instrument's existing automation fields, so `.pti` instrument files and projects keep the same size and layout:

| Field | Meaning for `Trigger` |
| :--- | :--- |
| `envelope[slot].loop` | `4` (`Trigger`) |
| `lfo[slot].shape` | Shape |
| `lfo[slot].speed` | Steps (index into the LFO speed list) |
| `lfo[slot].wtBend` | Source instrument index |
| `lfo[slot].amount` | Amount (`-1.0` – `+1.0`) |
| `lfo[slot].wtSust` | Repeat (`0`/`1` = `1x`, `2`–`64`, `255` = `Inf`) |

Instruments saved with a `Trigger` destination still load in older firmware, but older firmware does not know the `Trigger` type, so that destination will not behave as intended there.

Implementation: [`core/AudioEngine/TriggerEnvelope.cpp`](../core/AudioEngine/TriggerEnvelope.cpp) and `playerEngine::handleUpdateTriggerEnvelope()` in [`core/AudioEngine/mtAudioUpdateHandle.cpp`](../core/AudioEngine/mtAudioUpdateHandle.cpp).
