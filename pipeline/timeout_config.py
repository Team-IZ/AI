# D98: single source of truth for benchmark timeouts (user request -- centralize
#   instead of scattering LLAMA_TIMEOUT_S/NEMOTRON_TIMEOUT_S/SONNET_TIMEOUT_S as
#   separate hardcoded constants per script, which is how D94b's timeout tuning
#   ended up inconsistent across files).
#   WHY: llama-3.3-70b-instruct's worker-queue overload (D94, HTTP 503 "153/16")
#        showed real single-call latency well past NVIDIA's default 120s under
#        load, confirmed again by a controlled 7-key concurrent test (4/7 timed
#        out at 120s even with each request on a fully distinct API key --
#        ruling out per-key contention, D98). One generous number avoids
#        re-litigating "what should the timeout be" per script.
#   COST: a single global value can't express "this call is usually fast, time
#        it out aggressively" -- if a script needs a genuinely short timeout
#        for a different reason (e.g. a liveness probe), it should use its own
#        local constant instead of importing this one, not lower this value.
#   EXIT: change DEFAULT_TIMEOUT_S here; every importer picks it up. No other
#        file should hardcode a timeout number for NVIDIA/claude -p calls.
DEFAULT_TIMEOUT_S = 600.0

# D104: same centralization for max_tokens (user request, same mechanism as D98).
#   WHY: max_tokens=512 starvation broke two models in two different ways --
#        nemotron-super-49b-v1.5 burned the budget on internal reasoning and
#        returned content:null (D97), mistral-large-3 wrote tool-call arguments
#        longer than the cap and returned unterminated JSON (D103, 17/17
#        identical `Unterminated string` errors; the same job passed at 2048).
#        "Not a reasoning model, so 512 is fine" was disproven -- the required
#        cap tracks output verbosity, not model type, so per-model guessing is
#        the same trap as per-script timeout guessing was.
#   COST: a uniform generous cap can't express "this call must stay short".
#        Worst-case latency per call rises for chatty models (you only pay
#        tokens actually generated, but a runaway generation now runs longer
#        before the cap cuts it).
#   EXIT: change DEFAULT_MAX_TOKENS here; every importer picks it up. No other
#        file should hardcode a max_tokens number for NVIDIA/claude -p calls
#        (enforced by timeout-config-guard.py, same hook as timeouts).
#   NOTE: this file has outgrown its name (it is now the central LLM-call
#        config, not just timeouts). Renaming to llm_call_config.py was
#        considered and deferred -- 8+ files import `from timeout_config
#        import ...` and a rename would touch all of them for zero behavior
#        change. If a third knob ever lands here, do the rename then.
#   D108: 2048 -> 4096. The 5-axis grading tool's arguments (score + rationale
#        + verbatim quotes per axis) are much longer than ask_question's, and
#        mistral-large-3's verbose style hit finish_reason="length" at 2048
#        mid-JSON (measured: single-call repro, arguments cut at char 805).
#        A cap is a ceiling, not a target -- models that never reach it are
#        byte-identical at temp=0, so raising it only unblocks verbose ones.
DEFAULT_MAX_TOKENS = 4096
