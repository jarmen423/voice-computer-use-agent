/**
 * Purpose: SafetyGuard interactive simulator showcase.
 * Dependencies: React, lucide-react (ShieldAlert, ShieldCheck, X, Check)
 * Role: Simulates destructive keyword interception and triggers page-level styling pulses.
 */

import React, { useState } from 'react';
import { ShieldAlert, ShieldCheck, X, Check } from 'lucide-react';

export default function SecurityGuardWidget({ onTriggerWarning }) {
  const [safetyState, setSafetyState] = useState('idle'); // 'idle' | 'triggered' | 'blocked' | 'approved'
  const [spokenPrompt, setSpokenPrompt] = useState('');

  /**
   * Triggers the safety warning flow, pulsing colors and prompting confirmation.
   * 
   * @returns {void}
   */
  const handleTriggerDangerous = () => {
    setSafetyState('triggered');
    onTriggerWarning(true);
    setSpokenPrompt('Destructive command detected: "delete system folder". Confirm execution?');

    // If Web Speech Synthesis is available, speak the warning
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance('A destructive command was detected. Confirm execution?');
      utterance.rate = 1.0;
      window.speechSynthesis.speak(utterance);
    }
  };

  /**
   * Reverts the warning state and marks the command as safely blocked.
   * 
   * @returns {void}
   */
  const handleDenyCommand = () => {
    setSafetyState('blocked');
    onTriggerWarning(false);
    
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance('Command blocked. System safe.');
      window.speechSynthesis.speak(utterance);
    }
  };

  /**
   * Handles user mock approval, terminating warning state and updating flow result.
   * 
   * @returns {void}
   */
  const handleApproveCommand = () => {
    setSafetyState('approved');
    onTriggerWarning(false);

    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance('Access denied. Execution of destructive actions is disabled in this web sandbox.');
      window.speechSynthesis.speak(utterance);
    }
  };

  /**
   * Resets the widget to its default idle/monitoring state.
   * 
   * @returns {void}
   */
  const handleReset = () => {
    setSafetyState('idle');
    onTriggerWarning(false);
  };

  return (
    <div 
      id="security-guard"
      style={{
        background: 'var(--card-bg)',
        border: safetyState === 'triggered' 
          ? '1px solid var(--warning-color)' 
          : '1px solid var(--card-border)',
        borderRadius: '20px',
        padding: '1.5rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '1.5rem',
        boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        transition: 'border-color var(--transition-normal)'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--card-border)', paddingBottom: '0.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600 }}>
          <ShieldAlert size={18} style={{ color: safetyState === 'triggered' ? 'var(--warning-color)' : 'var(--text-primary)' }} />
          <span>SafetyGuard Monitoring Conduit</span>
        </div>
        <div>
          <span style={{ 
            fontSize: '0.75rem', 
            fontWeight: 600,
            color: safetyState === 'triggered' ? 'var(--warning-color)' : 'var(--success-color)' 
          }}>
            {safetyState === 'triggered' ? 'WARNING ACTIVE' : 'SECURE'}
          </span>
        </div>
      </div>

      {safetyState === 'idle' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', alignItems: 'center', padding: '1rem 0' }}>
          <div style={{
            width: '64px',
            height: '64px',
            borderRadius: '50%',
            background: 'rgba(16, 185, 129, 0.1)',
            color: 'var(--success-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            <ShieldCheck size={36} />
          </div>
          <div style={{ textAlign: 'center' }}>
            <h4 style={{ marginBottom: '0.25rem' }}>Safety Engine Standing By</h4>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
              Destructive keyword detection is active. No unauthorized command executes without spoken voice approval.
            </p>
          </div>
          <button 
            onClick={handleTriggerDangerous}
            className="btn btn-secondary"
            style={{ 
              color: 'var(--warning-color)', 
              borderColor: 'var(--warning-color)',
              background: 'rgba(255, 223, 0, 0.02)',
              marginTop: '0.5rem'
            }}
            onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255, 223, 0, 0.1)'}
            onMouseLeave={(e) => e.currentTarget.style.background = 'rgba(255, 223, 0, 0.02)'}
          >
            Simulate: "Delete System Folder"
          </button>
        </div>
      )}

      {safetyState === 'triggered' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{
            background: 'rgba(255, 223, 0, 0.1)',
            border: '1px solid var(--warning-color)',
            borderRadius: '12px',
            padding: '1rem',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.75rem'
          }}>
            <ShieldAlert size={20} style={{ color: 'var(--warning-color)', flexShrink: 0, marginTop: '2px' }} />
            <div>
              <h4 style={{ color: 'var(--warning-color)', marginBottom: '0.25rem' }}>DESTRUCTIVE INTENT BLOCKED</h4>
              <p style={{ fontSize: '0.85rem' }}>
                System intercepted terminal attempt: <code>rm -rf /Users/Desktop</code>. Pending spoken user verification...
              </p>
            </div>
          </div>

          {/* Subtitles simulating spoke warning prompt */}
          <div style={{
            background: '#040209',
            borderRadius: '12px',
            padding: '1rem',
            fontFamily: 'monospace',
            fontSize: '0.85rem',
            border: '1px solid rgba(255, 223, 0, 0.2)',
            color: 'var(--warning-color)'
          }}>
            <span style={{ opacity: 0.6 }}>[VoicePrompt]:</span> {spokenPrompt}
          </div>

          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <button 
              onClick={handleDenyCommand}
              className="btn btn-secondary"
              style={{ flex: 1, gap: '0.5rem', borderColor: 'var(--success-color)', color: 'var(--success-color)' }}
            >
              <X size={16} /> Block Execution (Safe)
            </button>
            <button 
              onClick={handleApproveCommand}
              className="btn"
              style={{ 
                flex: 1, 
                gap: '0.5rem', 
                background: 'var(--warning-color)', 
                color: '#000',
                fontWeight: 600
              }}
            >
              <Check size={16} /> Override & Approve
            </button>
          </div>
        </div>
      )}

      {(safetyState === 'blocked' || safetyState === 'approved') && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', alignItems: 'center', padding: '1rem 0' }}>
          <div style={{
            width: '64px',
            height: '64px',
            borderRadius: '50%',
            background: safetyState === 'blocked' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
            color: safetyState === 'blocked' ? 'var(--success-color)' : '#ef4444',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            {safetyState === 'blocked' ? <ShieldCheck size={36} /> : <ShieldAlert size={36} />}
          </div>
          <div style={{ textAlign: 'center' }}>
            <h4 style={{ marginBottom: '0.25rem' }}>
              {safetyState === 'blocked' ? 'Command Blocked Successfully' : 'Safety Bypass Intercepted'}
            </h4>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', maxWidth: '350px' }}>
              {safetyState === 'blocked' 
                ? 'SafetyGuard successfully aborted the destructive command payload. System is healthy and secure.'
                : 'Sandbox restriction: Override command was recognized, but actual execution is blocked for security in browser environments.'
              }
            </p>
          </div>
          <button 
            onClick={handleReset}
            className="btn btn-secondary"
            style={{ marginTop: '0.5rem' }}
          >
            Reset Monitor
          </button>
        </div>
      )}
    </div>
  );
}
