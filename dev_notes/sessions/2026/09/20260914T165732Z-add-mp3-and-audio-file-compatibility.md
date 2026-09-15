<!-- dev-notes: {"id":"20260914T165732Z-add-mp3-and-audio-file-compatibility","created_at":"2026-09-14T16:57:32Z","title":"Add MP3 and audio-file compatibility","status":"completed","tags":["audio","frontend","mp3","realtime"],"files":["README.md","dist/app.js","dist/index.html"],"branch":"main","commit":"6b56efd"} -->

# Add MP3 and audio-file compatibility

## Outcome
- Expanded the local file picker from video-only to browser-supported video and audio files, including explicit MP3 selection.
- Added media-type routing so audio files play through the existing audio element and visualization while video files continue to use the video element.
- Both paths reuse the same Web Audio capture, 16 kHz PCM chunking, local Qwen3-ASR inference, transcript, seeking, restart, and copy behavior.
- Clearing the file input after selection allows choosing the same source file again.

## Decisions and rationale
- Reuse the existing demo audio element for uploaded audio instead of adding a second player, preserving one active-media state machine and avoiding duplicate inference logic.
- Detect audio by MIME type with a filename-extension fallback for files whose browser-provided MIME type is empty.

## Verification
- `node --check dist/app.js` passed.
- `git diff --check` passed.
- The running local page served the updated `video/*,audio/*,.mp3` picker and audio-aware UI copy.

## Open questions / next step
- Actual codec playback remains browser-dependent even when a file is selectable; MP3 is the primary supported audio format for this phase.
