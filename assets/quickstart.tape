# The README quickstart: install, connect, ask, get located evidence.
#   vhs assets/quickstart.tape
#
# Records against ~/silica-demo — the nine LSM papers of the two static shots —
# with CLAUDE_CONFIG_DIR pointed at a throwaway ~/.claude-demo so the take
# carries no personal plugins, hooks or CLAUDE.md. That directory holds the
# credentials, a settings.json allowing the three silica tools, and
# .claude.json.seed: onboarding and folder trust pre-accepted, nothing else.
#
# Two knobs this tape has to carry:
#   - SILICA_PDF_PROVIDER=pdfium. The machine provider OCRs, ~33 s per PDF.
#   - the Sleep around the model's turn. Wait+Screen cannot help there: VHS
#     reads the terminal buffer from the top, so once the TUI has scrolled one
#     screenful no pattern in the live view is ever reachable. Retime if slow.
#
# Needs a vhs built past v0.12.0's Render bug: teardown() cancels the context
# it then hands to Render, so every ffmpeg call refuses to start and the
# release binary writes nothing, silently, with exit 0.

Output assets/quickstart.gif
Output assets/quickstart.mp4

Require uv
Require claude

Set Shell bash
Set FontFamily "Ubuntu Sans Mono"
Set FontSize 15
Set LineHeight 1.35
Set Width 1280
Set Height 720
Set Padding 20
Set Margin 26
Set MarginFill "#0b0e13"
Set BorderRadius 12
Set WindowBar Rings
Set WindowBarSize 34
Set Framerate 15
Set TypingSpeed 38ms
Set Theme { "background": "#0f141b", "foreground": "#e7edf4", "cursor": "#5ec27b", "selection": "#222c39", "black": "#0f141b", "red": "#d1707a", "green": "#5ec27b", "yellow": "#f2b544", "blue": "#6f93c4", "magenta": "#a98bc4", "cyan": "#6fb6b6", "white": "#8d9aab", "brightBlack": "#414e5d", "brightRed": "#e08a92", "brightGreen": "#7ed197", "brightYellow": "#f6c86a", "brightBlue": "#8dabd6", "brightMagenta": "#bfa5d6", "brightCyan": "#8cc9c9", "brightWhite": "#eef3f8" }

Hide
Type "cd ~/silica-demo" Enter
Type "export SILICA_PDF_PROVIDER=pdfium CLAUDE_CONFIG_DIR=~/.claude-demo" Enter
Type "unset CLAUDE_CODE_CHILD_SESSION CLAUDECODE" Enter
Type `PS1='\[\e[38;2;121;135;154m\]silica-demo\[\e[0m\] \[\e[38;2;94;194;123m\]$\[\e[0m\] '` Enter
Type "rm -rf .silica; rm -f ~/.claude-demo/CLAUDE.md; cp ~/.claude-demo/.claude.json.seed ~/.claude-demo/.claude.json" Enter
Type "uv tool uninstall silica-core >/dev/null 2>&1; clear" Enter
Show

Sleep 700ms
Type "uv tool install -q 'silica-core[mcp,dense]' && silica --version" Sleep 400ms Enter
Wait+Screen@3m /silica \d+\.\d+\.\d+/
Sleep 1000ms

Type "silica init" Sleep 300ms Enter
Wait+Screen@60s /"docs": 9/
Sleep 1400ms

Type "silica setup claude" Sleep 300ms Enter
Wait+Screen@60s /guidance block/
Sleep 1400ms

# Claude Code owns the screen from here; clear so its TUI starts clean.
Hide
Type "clear" Enter
Show

Type "claude" Sleep 300ms Enter
# The TUI takes ~5 s to paint; an empty screen is not worth five seconds of GIF.
Hide
Wait+Screen@60s /Claude Code v/
Show
Sleep 900ms

Type "Which paper maps the compaction design space, and on what page?"
Sleep 500ms
Enter
Sleep 11s

# No Screenshot here: with Hide/Show in the tape VHS indexes the wrong frame and
# leaves the previous poster in place. The last frame of the video is the poster:
#   ffmpeg -y -sseof -1 -i assets/quickstart.mp4 -frames:v 1 assets/quickstart-poster.png
