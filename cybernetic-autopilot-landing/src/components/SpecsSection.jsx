/**
 * Purpose: Specification cards grid showcasing system architecture.
 * Dependencies: React, lucide-react (Cpu, Shield, Zap, Layers)
 * Role: Communicates technical architecture and platform capabilities.
 */

import React from 'react';
import { Cpu, Shield, Zap, Layers } from 'lucide-react';

export default function SpecsSection() {
  const specs = [
    {
      icon: <Zap size={24} />,
      title: "Groq Whisper STT",
      description: "Ultra-fast local voice capture coupled with Groq Whisper API. Managed asynchronously to prevent main loop blocks."
    },
    {
      icon: <Cpu size={24} />,
      title: "Double-Brain Orchestration",
      description: "Primary orchestrations handled by Groq, with instant failover fallback to OpenAI or Cerebras LLMs."
    },
    {
      icon: <Layers size={24} />,
      title: "OS Conduit Control",
      description: "Platform-agnostic UI coordinates mapping, screen captures via MSS, and automated cursor manipulation."
    },
    {
      icon: <Shield size={24} />,
      title: "SafetyGuard System",
      description: "Destructive keyword filters prevent execution of harmful operations. Prompts spoken confirmation request."
    }
  ];

  return (
    <section id="specs" style={{ padding: '6rem 0 3rem 0' }}>
      <div style={{ textAlign: 'center', marginBottom: '4rem' }}>
        <h2 style={{ fontSize: '2.5rem', marginBottom: '1rem' }}>
          Under The Hood
        </h2>
        <p style={{ color: 'var(--text-secondary)', maxWidth: '600px', margin: '0 auto' }}>
          VoiceUse merges ultra-low-latency models with local platform automation to build a highly responsive and safe conduit.
        </p>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))',
        gap: '2rem'
      }}>
        {specs.map((spec, i) => (
          <div 
            key={i}
            className="spec-card"
            style={{
              background: 'var(--card-bg)',
              border: '1px solid var(--card-border)',
              borderRadius: '20px',
              padding: '2rem',
              display: 'flex',
              flexDirection: 'column',
              gap: '1.25rem',
              transition: 'all var(--transition-normal)',
              cursor: 'default'
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = 'var(--accent-color)';
              e.currentTarget.style.boxShadow = '0 10px 30px var(--accent-glow)';
              e.currentTarget.style.transform = 'translateY(-4px)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = 'var(--card-border)';
              e.currentTarget.style.boxShadow = 'none';
              e.currentTarget.style.transform = 'none';
            }}
          >
            <div style={{
              width: '48px',
              height: '48px',
              borderRadius: '12px',
              background: 'rgba(138, 43, 226, 0.1)',
              color: 'var(--accent-color)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              {spec.icon}
            </div>
            <div>
              <h3 style={{ fontSize: '1.2rem', marginBottom: '0.5rem' }}>{spec.title}</h3>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', lineHeight: '1.5' }}>
                {spec.description}
              </p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
