/**
 * Purpose: Interactive console widget simulating browser voice command execution.
 * Dependencies: React, lucide-react (Mic, MicOff, Terminal, Play, Check)
 * Role: Shows page-control integration via speech (Web Speech API) or keyboard.
 */

import React, { useState, useEffect, useRef } from 'react';
import { Mic, MicOff, Terminal, Play, Check } from 'lucide-react';

export default function ConsoleWidget() {
  const [logs, setLogs] = useState([
    { type: 'system', text: 'VoiceUse console initialized. Ready for command.' }
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isListening, setIsListening] = useState(false);
  const [hasSpeechSupport, setHasSpeechSupport] = useState(false);
  const recognitionRef = useRef(null);
  const logContainerRef = useRef(null);

  useEffect(() => {
    // Check Speech Recognition support in window
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
      setHasSpeechSupport(true);
      const recognition = new SpeechRecognition();
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.lang = 'en-US';

      recognition.onstart = () => {
        setIsListening(true);
        addLog('system', 'Microphone active. Listening for "toggle dark mode", "show config", etc...');
      };

      recognition.onresult = (event) => {
        const speechToText = event.results[0][0].transcript;
        setInputValue(speechToText);
        addLog('user', speechToText);
        executeCommand(speechToText);
      };

      recognition.onerror = (event) => {
        console.error("Speech recognition error:", event.error);
        addLog('system', `Speech error detected: ${event.error}`);
        setIsListening(false);
      };

      recognition.onend = () => {
        setIsListening(false);
      };

      recognitionRef.current = recognition;
    }
  }, []);

  useEffect(() => {
    // Auto-scroll logs to bottom
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs]);

  /**
   * Appends a log line to the console interface.
   * 
   * @param {string} type - Log type ('user', 'system', 'agent', 'success').
   * @param {string} text - Log text content.
   * @returns {void}
   */
  const addLog = (type, text) => {
    setLogs((prev) => [...prev, { type, text }]);
  };

  /**
   * Simulates the backend brain analyzing intent and executing the browser control.
   * 
   * @param {string} rawCommand - Raw speech or typed command.
   * @returns {void}
   */
  const executeCommand = (rawCommand) => {
    const cmd = rawCommand.toLowerCase().trim();
    addLog('agent', 'Processing linguistic command intent...');

    setTimeout(() => {
      if (cmd.includes('dark') || cmd.includes('light') || cmd.includes('theme')) {
        addLog('agent', 'Command maps to OS action: toggleTheme()');
        setTimeout(() => {
          const isDark = document.documentElement.classList.contains('dark-theme');
          if (isDark) {
            document.documentElement.classList.remove('dark-theme');
            document.documentElement.classList.add('light-theme');
            localStorage.setItem('theme', 'light');
          } else {
            document.documentElement.classList.remove('light-theme');
            document.documentElement.classList.add('dark-theme');
            localStorage.setItem('theme', 'dark');
          }
          addLog('success', 'CSS Variables updated. Theme transitioned successfully.');
        }, 400);
      } else if (cmd.includes('config') || cmd.includes('structure') || cmd.includes('yaml')) {
        addLog('agent', 'Command maps to FS lookup: read("config.yaml")');
        setTimeout(() => {
          addLog('success', 'File loaded:\n---\nvoiceuse:\n  stt:\n    engine: groq_whisper\n  brain:\n    model: groq-llama3-70b\n  safety:\n    destructive_guard: true');
        }, 500);
      } else if (cmd.includes('scroll') || cmd.includes('security') || cmd.includes('guard')) {
        addLog('agent', 'Command maps to screen action: scrollTo("#security")');
        setTimeout(() => {
          const el = document.getElementById('security-guard');
          if (el) {
            el.scrollIntoView({ behavior: 'smooth' });
            addLog('success', 'Viewport shifted to Security Guard validation widget.');
          } else {
            addLog('system', 'Error: target selector "#security-guard" not found on page.');
          }
        }, 400);
      } else {
        addLog('system', `Unknown command intent: "${rawCommand}". Try clickable shortcuts below.`);
      }
    }, 450);
  };

  /**
   * Handles text input submissions.
   * 
   * @param {React.FormEvent} e - Form event.
   * @returns {void}
   */
  const handleSubmit = (e) => {
    e.preventDefault();
    if (!inputValue.trim()) return;
    addLog('user', inputValue);
    executeCommand(inputValue);
    setInputValue('');
  };

  /**
   * Starts Web Speech API listener if available.
   * 
   * @returns {void}
   */
  const handleMicToggle = () => {
    if (!hasSpeechSupport) {
      addLog('system', 'Web Speech API is not supported in this browser. Please type commands.');
      return;
    }
    if (isListening) {
      recognitionRef.current.stop();
    } else {
      recognitionRef.current.start();
    }
  };

  return (
    <div style={{
      background: 'var(--card-bg)',
      border: '1px solid var(--card-border)',
      borderRadius: '20px',
      padding: '1.5rem',
      display: 'flex',
      flexDirection: 'column',
      gap: '1rem',
      boxShadow: '0 10px 30px rgba(0,0,0,0.2)'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--card-border)', paddingBottom: '0.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600 }}>
          <Terminal size={18} style={{ color: 'var(--accent-color)' }} />
          <span>Linguistic Control Terminal</span>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <span style={{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%', backgroundColor: 'var(--success-color)' }} />
          <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Ready</span>
        </div>
      </div>

      {/* Logs output window */}
      <div 
        ref={logContainerRef}
        style={{
          background: '#040209',
          borderRadius: '12px',
          padding: '1rem',
          height: '220px',
          overflowY: 'auto',
          fontFamily: 'monospace',
          fontSize: '0.85rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.5rem',
          whiteSpace: 'pre-wrap',
          border: '1px solid rgba(255,255,255,0.05)'
        }}
      >
        {logs.map((log, i) => {
          let color = '#fff';
          let prefix = '> ';
          if (log.type === 'system') { color = 'var(--text-secondary)'; prefix = '⚙ '; }
          if (log.type === 'agent') { color = 'var(--accent-secondary)'; prefix = '🧠 '; }
          if (log.type === 'success') { color = 'var(--success-color)'; prefix = '✓ '; }
          if (log.type === 'user') { color = '#f3f0fa'; prefix = '🗣 '; }
          
          return (
            <div key={i} style={{ color }}>
              <span style={{ opacity: 0.6 }}>{prefix}</span>
              {log.text}
            </div>
          );
        })}
      </div>

      {/* Command input form */}
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '0.5rem' }}>
        <input 
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="Speak or type: 'toggle theme'..."
          style={{
            flex: 1,
            background: 'rgba(0,0,0,0.2)',
            border: '1px solid var(--card-border)',
            borderRadius: '9999px',
            padding: '0.75rem 1.25rem',
            color: 'var(--text-primary)',
            outline: 'none',
            fontSize: '0.9rem'
          }}
        />

        {/* Speech Trigger */}
        <button
          type="button"
          onClick={handleMicToggle}
          style={{
            background: isListening ? 'rgba(239, 68, 68, 0.2)' : 'rgba(138, 43, 226, 0.15)',
            border: `1px solid ${isListening ? '#ef4444' : 'var(--card-border)'}`,
            borderRadius: '50%',
            width: '44px',
            height: '44px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            color: isListening ? '#ef4444' : 'var(--text-primary)',
            transition: 'all var(--transition-fast)'
          }}
          title={isListening ? "Stop listening" : "Speak command"}
        >
          {isListening ? <MicOff size={18} className="mic-pulsing" /> : <Mic size={18} />}
        </button>

        {/* Enter/Submit Trigger */}
        <button
          type="submit"
          className="btn btn-primary"
          style={{
            width: '44px',
            height: '44px',
            borderRadius: '50%',
            padding: 0
          }}
        >
          <Play size={16} />
        </button>
      </form>

      {/* Shortcuts pills */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginTop: '0.5rem' }}>
        <button 
          onClick={() => { setInputValue('toggle theme'); executeCommand('toggle theme'); }} 
          style={{ fontSize: '0.8rem', padding: '0.4rem 0.8rem', borderRadius: '8px', border: '1px solid var(--card-border)', background: 'rgba(255,255,255,0.02)', color: 'var(--text-primary)', cursor: 'pointer' }}
        >
          "Toggle Theme"
        </button>
        <button 
          onClick={() => { setInputValue('show config'); executeCommand('show config'); }} 
          style={{ fontSize: '0.8rem', padding: '0.4rem 0.8rem', borderRadius: '8px', border: '1px solid var(--card-border)', background: 'rgba(255,255,255,0.02)', color: 'var(--text-primary)', cursor: 'pointer' }}
        >
          "Show config.yaml"
        </button>
        <button 
          onClick={() => { setInputValue('scroll to security'); executeCommand('scroll to security'); }} 
          style={{ fontSize: '0.8rem', padding: '0.4rem 0.8rem', borderRadius: '8px', border: '1px solid var(--card-border)', background: 'rgba(255,255,255,0.02)', color: 'var(--text-primary)', cursor: 'pointer' }}
        >
          "Scroll to Security"
        </button>
      </div>

      <style>{`
        @keyframes micPulse {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.15); box-shadow: 0 0 10px rgba(239, 68, 68, 0.4); }
        }
        .mic-pulsing {
          animation: micPulse 1.2s infinite ease-in-out;
        }
      `}</style>
    </div>
  );
}
