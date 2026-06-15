/**
 * Purpose: Installation section showcasing commands, dependencies, and easy local onboarding.
 * Dependencies: React, lucide-react (Copy, Check, Terminal)
 * Role: Drives local setup conversion rate.
 */

import React, { useState } from 'react';
import { Copy, Check, Terminal } from 'lucide-react';

export default function InstallSection() {
  const [copiedIndex, setCopiedIndex] = useState(null);

  const steps = [
    {
      label: "Install Dependencies & Local Package",
      code: "pip install -e \".[dev]\""
    },
    {
      label: "Launch with Dry-Run Sandbox Mode",
      code: "python -m voiceuse --dry-run"
    }
  ];

  /**
   * Copies command code snippet to clipboard.
   * 
   * @param {string} text - Code text to copy.
   * @param {number} index - Index of the copied command for checkmark state.
   * @returns {void}
   */
  const handleCopy = (text, index) => {
    navigator.clipboard.writeText(text)
      .then(() => {
        setCopiedIndex(index);
        setTimeout(() => setCopiedIndex(null), 2000);
      })
      .catch(err => console.error("Could not copy text: ", err));
  };

  return (
    <section id="install" style={{ padding: '5rem 0 2rem 0' }}>
      <div style={{
        background: 'linear-gradient(180deg, var(--card-bg) 0%, rgba(138, 43, 226, 0.03) 100%)',
        border: '1px solid var(--card-border)',
        borderRadius: '24px',
        padding: '3rem 2rem',
        textAlign: 'center',
        position: 'relative',
        overflow: 'hidden'
      }}>
        <div style={{
          position: 'absolute',
          bottom: '-10%',
          right: '-10%',
          width: '300px',
          height: '300px',
          background: 'radial-gradient(circle, rgba(138, 43, 226, 0.08) 0%, transparent 70%)',
          pointerEvents: 'none',
          zIndex: -1
        }} />

        <h2 style={{ fontSize: '2.25rem', marginBottom: '1rem' }}>
          Get Started in Seconds
        </h2>
        <p style={{ color: 'var(--text-secondary)', maxWidth: '600px', margin: '0 auto 3rem auto', fontSize: '1rem' }}>
          VoiceUse runs completely on your system. You can spin up the dry-run simulation immediately without setting up API keys.
        </p>

        {/* Steps */}
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '2rem',
          maxWidth: '700px',
          margin: '0 auto',
          textAlign: 'left'
        }}>
          {steps.map((step, i) => (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <div style={{
                  width: '28px',
                  height: '28px',
                  borderRadius: '50%',
                  background: 'var(--accent-color)',
                  color: '#fff',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '0.85rem',
                  fontWeight: 700
                }}>
                  {i + 1}
                </div>
                <h4 style={{ fontSize: '1.05rem', fontWeight: 600 }}>{step.label}</h4>
              </div>

              {/* Terminal Code Block */}
              <div style={{
                background: '#040209',
                borderRadius: '12px',
                border: '1px solid var(--card-border)',
                padding: '1rem 1.25rem',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                fontFamily: 'monospace',
                fontSize: '0.9rem',
                color: 'var(--accent-secondary)'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', overflowX: 'auto' }}>
                  <Terminal size={16} style={{ color: 'var(--text-secondary)', flexShrink: 0 }} />
                  <span>{step.code}</span>
                </div>
                <button
                  onClick={() => handleCopy(step.code, i)}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-secondary)',
                    cursor: 'pointer',
                    padding: '0.25rem',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'color var(--transition-fast)'
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.color = 'var(--text-primary)'}
                  onMouseLeave={(e) => e.currentTarget.style.color = 'var(--text-secondary)'}
                  title="Copy command"
                >
                  {copiedIndex === i ? <Check size={16} style={{ color: 'var(--success-color)' }} /> : <Copy size={16} />}
                </button>
              </div>
            </div>
          ))}
        </div>

        {/* Requirements Footer info */}
        <p style={{
          color: 'var(--text-secondary)',
          fontSize: '0.85rem',
          marginTop: '3rem',
          maxWidth: '500px',
          margin: '3rem auto 0 auto',
          lineHeight: 1.5
        }}>
          * Requires Python 3.10+ and external audio playback packages (e.g. <code>ffplay</code> or <code>mpv</code>) installed on your system PATH for complete text-to-speech rendering.
        </p>
      </div>
    </section>
  );
}
