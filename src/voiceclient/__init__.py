# Voice interface: wake word -> local STT -> the assistant-web API -> local TTS.
# A fourth client of an already-working system, not a new subsystem: it reaches
# the orchestrator only over HTTP (the same entry point the browser uses), so in
# the target topology it runs on the capture machine with no assistant code or
# secrets beyond the assistant's URL. Import-isolated from the other packages.
