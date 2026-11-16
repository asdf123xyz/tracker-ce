#include "TriggerEnvelope.h"
#include "AudioStream.h"
#include "tempoSyncRateValues.h"
#include "util_math.h"

namespace triggerEnvelope
{
namespace
{
#ifdef PLATFORM_IMX
constexpr float kAudioSampleRate = 44100.0f;
#else
constexpr float kAudioSampleRate = 44117.6470588f;
#endif
constexpr float kBlockSeconds = AUDIO_BLOCK_SAMPLES / kAudioSampleRate;

// Block counter starts at 1 so that a stored value of 0 means "never fired".
uint32_t s_blockClock = 1;
uint32_t s_firedBlock[kSourceCount] = {};
uint8_t s_randomLevel[kSourceCount] = {};
uint32_t s_randomState = 0x9E3779B9u;

uint8_t nextRandom()
{
    // xorshift32 - cheap, deterministic and safe to use inside the audio callback
    s_randomState ^= s_randomState << 13;
    s_randomState ^= s_randomState >> 17;
    s_randomState ^= s_randomState << 5;
    return (uint8_t)(s_randomState >> 24);
}

// Random level for repeat `cycle`: cycle 0 uses the level drawn at fire time, later cycles mix in
// the cycle number so each repeat gets its own level without storing any state.
uint8_t cycleRandomLevel(uint8_t baseLevel, uint32_t cycle)
{
    if (cycle == 0)
        return baseLevel;

    uint32_t x = (baseLevel + 1u) * 0x9E3779B1u ^ cycle * 0x85EBCA6Bu;
    x ^= x >> 15;
    x *= 0x2C1B3C6Du;
    x ^= x >> 12;
    return (uint8_t)(x >> 24);
}

float shapeValue(uint8_t shape, float phase, bool repeating, uint8_t randomLevel)
{
    switch (shape)
    {
        case lfoShapeReverseSaw: return 1.0f - phase;
        case lfoShapeSaw: return phase;
        case lfoShapeTriangle: return (phase < 0.5f) ? (2.0f * phase) : (2.0f - 2.0f * phase);
        // A single Square holds for the whole duration (gate); repeated Squares pulse at 50% duty
        // like the LFO Square so the repeats are audible.
        case lfoShapeSquare: return (!repeating || phase < 0.5f) ? 1.0f : 0.0f;
        case lfoShapeRandom: return randomLevel / 255.0f;
        default: return 1.0f - phase;
    }
}
} // namespace

AUDIO_CORE_RAM void fire(uint8_t instrIdx)
{
    if (instrIdx >= kSourceCount)
        return;

    s_randomLevel[instrIdx] = nextRandom();
    s_firedBlock[instrIdx] = s_blockClock;
}

AUDIO_CORE_RAM void advanceClock()
{
    s_blockClock++;
    if (s_blockClock == 0)
    {
        // wraps after ~145 days of uptime; keep "0 = never fired" valid
        reset();
    }
}

void reset()
{
    for (uint8_t i = 0; i < kSourceCount; i++)
    {
        s_firedBlock[i] = 0;
    }
    s_blockClock = 1;
}

float getDurationSteps(uint8_t speedIdx, bool isAmp)
{
    if (isAmp)
    {
        return tempoSyncRatesAmp[(speedIdx < ENVELOPE_SPEED_AMP_COUNT) ? speedIdx : 0];
    }
    return tempoSyncRatesOthers[(speedIdx < ENVELOPE_SPEED_OTHER_COUNT) ? speedIdx : 0];
}

AUDIO_CORE_RAM float evaluate(const strInstrument::strEnvBasedLfo& cfg, bool isAmp, float tempo)
{
    const uint8_t source = cfg.wtBend;
    if (source >= kSourceCount)
        return 0.0f;

    const uint32_t fired = s_firedBlock[source];
    if (fired == 0)
        return 0.0f;

    if (tempo <= 0.0f)
        tempo = 120.0f;

    // One sequencer step is a 1/16 note: 15 / BPM seconds.
    const float durationSeconds = getDurationSteps(cfg.speed, isAmp) * (15.0f / tempo);
    if (durationSeconds <= 0.0f)
        return 0.0f;

    const bool infinite = (cfg.wtSust == kRepeatInfinite);
    const uint32_t repeats =
      infinite ? 0 : ((cfg.wtSust <= 1) ? 1 : ((cfg.wtSust > kRepeatMax) ? 1 : cfg.wtSust));

    const float elapsedSeconds = (float)(s_blockClock - fired) * kBlockSeconds;
    const float cyclePosition = elapsedSeconds / durationSeconds;

    if (!infinite && cyclePosition >= (float)repeats)
        return 0.0f;

    const uint32_t cycle = (uint32_t)cyclePosition;
    const float phase = cyclePosition - (float)cycle;
    const float amount = clamp<float>(cfg.amount, -1.0f, 1.0f);

    return shapeValue(cfg.shape,
                      phase,
                      infinite || repeats > 1,
                      cycleRandomLevel(s_randomLevel[source], cycle)) *
           amount;
}
} // namespace triggerEnvelope
