#ifndef AUDIOENGINE_TRIGGERENVELOPE_H_
#define AUDIOENGINE_TRIGGERENVELOPE_H_

#include "mtStructs.h"
#include <stdint.h>

// Trigger envelopes ("Trigger" type in Instrument Automation).
//
// A Trigger envelope is a one-shot, tempo-synced modulation that restarts whenever a chosen
// *source* instrument plays a note on any track (sidechain-style ducking, cross-track filter
// pumps, pitch dips, ...).
//
// It reuses the existing per-destination automation storage so the 372-byte strInstrument /
// .pti layout is unchanged:
//   envelope[slot].loop   = envelopeGenerator::strEnv::kTypeTrigger
//   lfo[slot].shape       = lfoShapeType (Rev Saw / Saw / Triangle / Square / Random)
//   lfo[slot].speed       = duration index into the LFO speed table (same table/labels as LFO)
//   lfo[slot].wtBend      = source instrument index (0 .. kTotalInstrumentsCount - 1)
//   lfo[slot].amount      = depth, -1.0 .. +1.0
//   lfo[slot].wtSust      = repeat count: 0/1 = once, 2..kRepeatMax, kRepeatInfinite = until the
//                           next trigger
//
// Timing is stateless: every note-on stores the audio block counter for its instrument, and each
// track evaluates "time since the source last fired" when it updates. A track that starts playing
// in the middle of a trigger envelope therefore joins it at the correct phase.
namespace triggerEnvelope
{
constexpr uint8_t kSourceCount = kTotalInstrumentsCount;
constexpr uint8_t kRepeatMax = 64;
constexpr uint8_t kRepeatInfinite = 255;

// Records a note-on of instrument `instrIdx` (sample, MIDI or synth). Safe to call from the
// audio callback (sequencer) or the UI loop (pads).
void fire(uint8_t instrIdx);

// Advances the trigger clock by one audio block. Must be called once per audioEngine::update(),
// after all tracks have been updated.
void advanceClock();

// Clears all pending triggers (e.g. when a project is loaded).
void reset();

// Duration of the envelope in sequencer steps for the given speed index. The volume slot uses the
// shorter amp-constrained speed table, exactly like the volume LFO.
float getDurationSteps(uint8_t speedIdx, bool isAmp);

// Current modulation value for a Trigger envelope (amount * shape(phase)), or 0 when the source
// has not fired or the envelope has finished all its repeats.
float evaluate(const strInstrument::strEnvBasedLfo& cfg, bool isAmp, float tempo);
} // namespace triggerEnvelope

#endif /* AUDIOENGINE_TRIGGERENVELOPE_H_ */
