# Codex Plugin Setup  

Add this to: 
`C:\Users\jfrie\.codex\config.toml`
```toml
[marketplaces.voiceuse-local]
source_type = "local"
source = 'D:\code\voice-computer-use-agent'

[plugins."voiceuse-computer-control@voiceuse-local"]
enabled = true
```
Then run:

`C:\Users\jfrie\bin\sync-codex-config.cmd`

Then fully restart Codex from:

cd D:\code\voice-computer-use-agent
codex

**Why That Works**

Codex marketplace sources expect a folder containing:

.agents/plugins/marketplace.json
plugins/<plugin-name>/

Your repo now has exactly that:

  D:\code\voice-computer-use-agent\.agents\plugins\marketplace.json
  D:\code\voice-computer-use-agent\plugins\voiceuse-computer-control

After Restart

Try:

> Use VoiceUse Computer Control to list my open windows.

or:

> Use VoiceUse Computer Control to observe my screen and describe what you see.

You should see tools available like:

  voiceuse_observe_screen
  voiceuse_list_windows
  voiceuse_open_app
  voiceuse_focus_window
  voiceuse_click
  voiceuse_type_text
  voiceuse_press_key
  voiceuse_wait

  The safest first real action test is:

`Use VoiceUse Computer Control to focus Calculator.`