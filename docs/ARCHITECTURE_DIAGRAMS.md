# VoiceUse Architecture Diagrams

This document maps the current VoiceUse architecture as implemented in the
codebase. The diagrams are intentionally split by concern so a future reader can
understand how voice input, LLM planning, local OS control, visual computer use,
plugins, and the Codex MCP adapter fit together without reading every module
first.

## 1. System Context

VoiceUse has two major ways to control the desktop:

- The local voice application started with `voiceuse`.
- The globally installed MCP server started with `voiceuse-computer-control-mcp`.

```mermaid
flowchart LR
    User[User] -->|speech / hotkey / wake word| App[VoiceUse app]
    User -->|desktop task in Codex| Codex[Codex or MCP client]

    App --> Backend[VoiceCommandBackend]
    Backend --> Brain[Native Brain LLM orchestrator]
    Backend --> ExtAgent[External desktop action agent]
    App --> Grok[Grok Voice plugin]
    Brain --> Tools[Shared tool registry]
    Grok --> Tools

    Codex -->|stdio MCP| MCP[voiceuse-computer-control-mcp]
    ExtAgent -->|stdio MCP| MCP

    Tools --> OS[OSController facade]
    Tools --> Vision[VisionBridge]
    MCP --> OS

    OS --> Desktop[Local desktop session]
    Vision --> OS
    Vision --> Providers[Codex CLI or Anthropic computer use]
```

Key files:

- `voiceuse/main.py`
- `voiceuse/agent_backend.py`
- `voiceuse/brain.py`
- `voiceuse/tool_registry.py`
- `voiceuse/os_controller.py`
- `voiceuse/vision_bridge.py`
- `voiceuse/computer_control_mcp.py`

## 2. Voice Pipeline

This is the normal pipeline when no replacement realtime plugin is active.

```mermaid
sequenceDiagram
    participant U as User
    participant IM as InputManager
    participant App as Application
    participant STT as Groq Whisper STT
    participant Backend as VoiceCommandBackend
    participant Agent as Native Brain or external action agent
    participant Safety as SafetyGuard
    participant Tools as ToolRegistry
    participant OS as OSController / VisionBridge
    participant TTS as TTSManager

    U->>IM: Press hotkey or say wake word
    IM->>App: on_hotkey_start()
    App->>TTS: cancel stale speech
    App->>IM: capture audio until release / silence / max duration
    IM->>App: audio bytes
    App->>STT: transcribe_audio()
    STT-->>App: text command
    App->>Backend: process_command(text)
    Backend->>Agent: plan and execute
    Agent->>Safety: pre-check requested action
    Agent->>Tools: dispatch tool calls
    Tools->>OS: execute desktop action
    OS-->>Tools: CommandResult
    Tools-->>Agent: CommandResult
    Agent-->>Backend: final summary
    Backend-->>App: final CommandResult
    App->>TTS: speak result summary
```

Important behavior:

- Audio and STT work is kept off the event loop where it can block.
- `TTSManager.cancel()` is called on new user input so old speech does not keep
  playing over a new command.
- `VoiceCommandBackend` decides whether the command goes to the native Brain or
  an external MCP-capable desktop action agent.

## 3. Application Composition Root

`Application` is the runtime owner for the default app. It wires dependencies
once, keeps references as instance attributes, and owns startup/shutdown.

```mermaid
flowchart TB
    CLI[voiceuse CLI] --> App[Application]
    App --> Config[Config]
    App --> Audio[AudioDevice]
    App --> Input[InputManager]
    App --> TTS[TTSManager]
    App --> OS[OSController]
    App --> Vision[VisionBridge]
    App --> Safety[SafetyGuard]
    App --> Brain[Brain]
    App --> Backend[VoiceCommandBackend]
    App --> PluginLookup[get_plugin]

    Backend --> Brain
    Backend --> External[ExternalAgentBackend]
    External --> Runner[ExternalAgentRunner]
    Runner --> CLI[codex exec runner]

    Brain --> Config
    Brain --> OS
    Brain --> Vision
    Brain --> Safety

    Vision --> OS
    Input --> Audio

    PluginLookup -->|if enabled| Plugin[PluginBase implementation]
    Plugin --> Audio
    Plugin --> OS
    Plugin --> Vision
    Plugin --> Safety
```

This is the most important lifecycle boundary. New subsystems should generally
be constructed here instead of inside unrelated modules.

## 4. Voice Front-End To External Agent Backend

This is the new bridge between the original voice assistant and the stronger
computer-use agent path. VoiceUse keeps the real-time voice UX. The external
agent owns the observe, reason, act, verify loop through the MCP tools.

```mermaid
flowchart TD
    UserVoice[User speaks] --> Hotkey[Hotkey / wake word]
    Hotkey --> STT[InputManager STT]
    STT --> Text[Transcribed command]
    Text --> Backend{agent.backend}

    Backend -->|native| Native[NativeBrainBackend]
    Native --> Brain[Brain.process_command]
    Brain --> ToolRegistry[tool_registry]

    Backend -->|external_agent| External[ExternalAgentBackend]
    External --> Prompt[Generic desktop action agent prompt]
    Prompt --> Runner[ExternalAgentRunner]
    Runner --> CodexRunner[CodexCliRunner]
    CodexRunner --> AgentCLI[codex exec]
    AgentCLI --> MCP[voiceuse-computer-control-mcp]

    MCP --> OS[OSController]
    ToolRegistry --> OS
    ToolRegistry --> Vision[VisionBridge]
    Vision --> OS
    OS --> Desktop[Local desktop]

    AgentCLI --> Summary[Short final summary]
    Brain --> Summary
    Summary --> TTS[TTSManager speaks aloud]
```

Config switch:

```yaml
agent:
  backend: external_agent
  runner: codex_cli
```

The prompt in `ExternalAgentBackend` does not mention Codex. It describes a
generic desktop action agent contract so another MCP-capable agent runner can be
added behind the same interface.

## 5. Application State Machine

VoiceUse has multiple asynchronous triggers. The state machine in `main.py`
keeps lifecycle transitions explicit.

```mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> INITIALISING
    CREATED --> SHUTTING_DOWN
    INITIALISING --> IDLE
    INITIALISING --> SHUTTING_DOWN

    IDLE --> LISTENING
    IDLE --> THINKING
    IDLE --> SPEAKING
    IDLE --> CONFIRMING
    IDLE --> SHUTTING_DOWN

    LISTENING --> THINKING
    LISTENING --> SPEAKING
    LISTENING --> CONFIRMING
    LISTENING --> IDLE
    LISTENING --> SHUTTING_DOWN

    THINKING --> ACTING
    THINKING --> SPEAKING
    THINKING --> CONFIRMING
    THINKING --> IDLE
    THINKING --> SHUTTING_DOWN

    ACTING --> THINKING
    ACTING --> SPEAKING
    ACTING --> IDLE
    ACTING --> SHUTTING_DOWN

    SPEAKING --> IDLE
    SPEAKING --> LISTENING
    SPEAKING --> SHUTTING_DOWN

    CONFIRMING --> THINKING
    CONFIRMING --> IDLE
    CONFIRMING --> SPEAKING
    CONFIRMING --> SHUTTING_DOWN

    SHUTTING_DOWN --> STOPPED
    STOPPED --> [*]
```

The state machine is intentionally coarse. It protects the application from
invalid high-level transitions; it does not model every low-level audio or tool
operation.

## 6. Brain Agent Loop

The Brain is the top-level LLM orchestrator for the default voice pipeline.
It owns conversation history, desktop context, provider fallback, safety checks,
tool dispatch, and result recording.

```mermaid
flowchart TD
    Start[process_command text] --> Context[Build messages from system prompt, desktop context, and rolling history]
    Context --> LLM[LLM chat with tool schemas]
    LLM --> HasTools{Tool calls?}
    HasTools -->|no| FinishText[Return conversational response]
    HasTools -->|yes| Safety[Safety check each tool]
    Safety --> Confirm{Needs confirmation?}
    Confirm -->|yes| AskUser[Ask user confirmation through app callback]
    AskUser --> Confirmed{Confirmed?}
    Confirmed -->|no| Blocked[Return blocked result]
    Confirmed -->|yes| Dispatch
    Confirm -->|no| Dispatch[Dispatch via tool_registry]
    Dispatch --> Audit[Write action audit event]
    Audit --> Results[Append tool results to conversation]
    Results --> Done{Terminal or step budget used?}
    Done -->|no| LLM
    Done -->|yes| Summarize[Record turn and return CommandResult]
```

Key implementation details:

- `_LLMClient.chat()` retries transient provider failures and then falls back.
- Desktop context is cached briefly so repeated agent steps do not enumerate
  windows more than necessary.
- Tool results become context for later planning rounds in the same user command.

## 7. Shared Tool Dispatch

Both the default Brain and the Grok Voice plugin use the same tool schema and
dispatcher so their capabilities do not drift.

```mermaid
flowchart LR
    Brain[Brain] --> Registry[tool_registry.TOOL_SCHEMAS]
    Grok[GrokVoicePlugin] --> Registry

    Brain --> Dispatch[dispatch_tool_call]
    Grok --> Dispatch

    Dispatch --> OSBranch{Tool type}
    OSBranch -->|open/focus/type/search/system| OS[OSController]
    OSBranch -->|click_element| Vision[VisionBridge]

    OS --> Result[CommandResult]
    Vision --> Result
```

High-value files:

- `voiceuse/tool_registry.py`
- `voiceuse/models.py`
- `voiceuse/action_audit.py`

## 8. OS Control Facade And Focused Services

`OSController` is still the public facade, but it now delegates high-risk or
focused behavior into smaller services.

```mermaid
flowchart TB
    OS[OSController facade] --> Cache[Window cache 500 ms]
    OS --> Platform[Platform window APIs]
    OS --> Input[InputSimulator]
    OS --> Shot[ScreenshotService]
    OS --> Command[SystemCommandExecutor]
    OS --> Resolve[WindowResolver]
    OS --> Browser[BrowserWorkflow]

    Platform --> Windows[pywin32 / pygetwindow]
    Platform --> Linux[xdotool / wmctrl]
    Platform --> Mac[osascript]

    Input --> PyAutoGUI[pyautogui]
    Input --> Clipboard[pyperclip for Unicode paste]
    Shot --> MSS[mss screenshots]
    Command --> InspectOnly[inspect-only command policy]
    Browser --> Input
    Browser --> Resolve
```

Architectural point:

- `OSController` remains the compatibility surface.
- New behavior should usually land in a focused service under
  `voiceuse/os_services.py` first, then be exposed by `OSController`.

## 9. Visual Computer-Use Loop

`VisionBridge.find_and_click()` is the closed-loop computer-use path for visual
UI tasks.

```mermaid
sequenceDiagram
    participant Brain as Brain / ToolRegistry
    participant Vision as VisionBridge
    participant OS as OSController
    participant Provider as Codex CLI or Anthropic
    participant Desktop as Desktop

    Brain->>Vision: click_element(description, app_name)
    Vision->>OS: focus target and capture screenshot
    OS->>Desktop: screenshot monitor/window
    Desktop-->>Vision: PNG target

    loop up to 5 steps
        Vision->>Provider: task, screenshot, target metadata, step history
        Provider-->>Vision: action JSON
        alt done / failed
            Vision-->>Brain: CommandResult
        else mouse/key/type/scroll action
            Vision->>OS: execute action
            OS->>Desktop: click/type/key/scroll
            Vision->>Vision: wait
            Vision->>OS: capture fresh screenshot
        end
    end
```

Why this exists:

- A single screenshot-and-click cannot recover from popups, loading delays,
  wrong focus, stale UI, or misclicks.
- The loop gives the model a chance to observe the result of each action and
  adapt before continuing.

## 10. Codex MCP / Plugin Architecture

Codex gets desktop-control tools through a globally registered MCP server. The
Codex plugin and skill provide a convenience layer and usage guidance.

```mermaid
flowchart TB
    Codex[Codex session] --> ToolSearch[Tool discovery]
    ToolSearch --> MCPTools[mcp__voiceuse_computer_control__ tools]
    Codex --> Skill[VoiceUse Computer Control skill]

    MCPTools --> Stdio[stdio JSON-RPC]
    Stdio --> CLI[voiceuse-computer-control-mcp]
    CLI --> Server[voiceuse.computer_control_mcp]
    Server --> Tools[VoiceUseComputerTools]
    Tools --> Config[Config from VOICEUSE_CONFIG]
    Tools --> OS[OSController]
    OS --> Desktop[Local desktop]

    PluginManifest[.codex-plugin/plugin.json] --> Skill
    PluginManifest --> PluginMCP[plugin .mcp.json]
    GlobalConfig[Codex user config] --> CLI
```

Important paths:

- MCP server module:
  - `D:\code\voice-computer-use-agent\voiceuse\computer_control_mcp.py`
- Global launcher:
  - `C:\Users\jfrie\bin\voiceuse-computer-control-mcp.cmd`
- Codex skill:
  - `D:\code\voice-computer-use-agent\plugins\voiceuse-computer-control\skills\voiceuse-computer-control\SKILL.md`
- Plugin manifest:
  - `D:\code\voice-computer-use-agent\plugins\voiceuse-computer-control\.codex-plugin\plugin.json`
- MCP declaration:
  - `D:\code\voice-computer-use-agent\plugins\voiceuse-computer-control\.mcp.json`

## 11. MCP Tool Call Lifecycle

This is what happens when Codex calls a loaded VoiceUse MCP tool.

```mermaid
sequenceDiagram
    participant C as Codex
    participant MCP as voiceuse-computer-control-mcp
    participant Tools as VoiceUseComputerTools
    participant OS as OSController
    participant Desktop as Desktop

    C->>MCP: initialize
    MCP-->>C: protocolVersion and capabilities
    C->>MCP: tools/list
    MCP-->>C: voiceuse_* tool schemas
    C->>MCP: tools/call voiceuse_open_app
    MCP->>Tools: call_tool(name, arguments)
    Tools->>OS: open_app(app_name)
    OS->>Desktop: focus or launch app
    Desktop-->>OS: result
    OS-->>Tools: CommandResult
    Tools-->>MCP: MCP content
    MCP-->>C: JSON-RPC result
```

The direct MCP registration is intentionally independent of the plugin cache.
That makes the tool server globally available from any Codex working directory
as long as the launcher remains on PATH.

## 12. Grok Voice Realtime Plugin

The Grok plugin is a replacement pipeline. It does not use the default
InputManager, Brain, or TTSManager as the active voice loop. It still shares the
same OS, vision, safety, audit, audio-device, and tool-dispatch layers.

```mermaid
flowchart TB
    App[Application] --> PluginLookup[get_plugin]
    PluginLookup --> Grok[GrokVoicePlugin]

    Grok --> Streamer[GrokAudioStreamer]
    Grok --> Client[XAIRealtimeClient]
    Grok --> Tools[tool_registry]
    Grok --> Safety[SafetyGuard]
    Grok --> Audit[ActionAuditLog]
    Grok --> Audio[AudioDevice]

    Streamer --> Mic[Microphone 24 kHz PCM]
    Streamer --> Speaker[Playback audio]
    Client --> XAI[xAI Realtime WebSocket]
    XAI --> Client
    Client -->|function calls| Grok
    Tools --> OS[OSController]
    Tools --> Vision[VisionBridge]
```

The main architectural weakness that remains is that this plugin is still a
replacement mode rather than a fully compositional audio pipeline. The good part
is that tool schemas and dispatch are now shared, so desktop-control behavior is
not duplicated.

## 13. Safety, Permissions, And Audit Path

Safety is not only a prompt concern. The code has explicit checks before tool
execution and audit records after attempted actions.

```mermaid
flowchart TD
    Request[User or realtime model request] --> ToolCall[ToolCall]
    ToolCall --> Safety[SafetyGuard]
    Safety --> Allowed{Allowed by policy?}
    Allowed -->|no| Block[Return blocked CommandResult]
    Allowed -->|yes| Confirm{Needs confirmation?}
    Confirm -->|yes| UserConfirm[Ask user confirmation]
    UserConfirm --> YesNo{Confirmed?}
    YesNo -->|no| Block
    YesNo -->|yes| Execute[Execute tool]
    Confirm -->|no| Execute
    Execute --> Audit[ActionAuditLog JSONL]
    Audit --> Result[CommandResult]
```

Main files:

- `voiceuse/safety.py`
- `voiceuse/action_audit.py`
- `voiceuse/tool_registry.py`

## 14. Observability Flow

Latency timing is deliberately close to the paths that users feel: end-to-end
pipeline time and tool dispatch time.

```mermaid
flowchart LR
    Pipeline[Application pipeline] --> Timer1[LatencyTimer pipeline]
    Dispatch[tool_registry.dispatch_tool_call] --> Timer2[LatencyTimer tool.dispatch]
    Timer1 --> Logs[structured log records]
    Timer2 --> Logs
    Logs --> Operator[Developer/operator reads latency and failure patterns]
```

This is still lightweight observability. A production version should add
durable metrics for STT duration, LLM provider latency, token usage, TTS
duration, tool failure rates, and confirmation frequency.

## 15. Configuration And Secrets

Configuration is loaded from YAML with environment-variable resolution.
Serialized config intentionally excludes resolved secrets.

```mermaid
flowchart TB
    YAML[config.yaml] --> Config[Config.from_yaml]
    Env[Environment variables] --> Config
    Config --> App[Application]
    Config --> Brain[LLM/STT/TTS providers]
    Config --> Agent[Voice command backend selection]
    Config --> OS[OS aliases and browser preferences]
    Config --> Safety[allowed tools and confirmation policy]
    Config --> Plugins[plugin configuration]

    Config --> Serialize[Config.to_yaml]
    Serialize --> Redacted[secrets excluded from output]
```

Common environment variables:

- `GROQ_API_KEY`
- `OPENAI_API_KEY`
- `CEREBRAS_API_KEY`
- `ANTHROPIC_API_KEY`
- `XAI_API_KEY`
- `PORCUPINE_ACCESS_KEY`

## 16. Current Architectural Boundaries

```mermaid
flowchart LR
    subgraph VoiceApp[Default voice app]
        InputManager
        Brain
        VoiceCommandBackend
        TTSManager
    end

    subgraph SharedCore[Shared local-control core]
        ToolRegistry
        SafetyGuard
        ActionAuditLog
        OSController
        VisionBridge
        AudioDevice
    end

    subgraph RealtimePlugin[Realtime plugin mode]
        GrokVoicePlugin
        XAIRealtimeClient
        GrokAudioStreamer
    end

    subgraph CodexAdapter[Codex and MCP adapter]
        CodexSkill
        ComputerControlMCP
        GlobalLauncher
        ExternalAgentBackend
    end

    VoiceApp --> SharedCore
    RealtimePlugin --> SharedCore
    CodexAdapter --> SharedCore
```

The long-term target architecture should keep moving code into `SharedCore`.
Voice, realtime plugins, and external MCP clients should become different
front-ends over the same local-control engine rather than parallel
implementations.
