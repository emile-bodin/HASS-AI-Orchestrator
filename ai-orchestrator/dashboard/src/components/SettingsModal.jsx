import React, { useEffect, useRef, useState } from 'react';
import { Cpu, ExternalLink, Key, Loader2, Settings, ShieldCheck, Wand2, X } from 'lucide-react';
import { useDialogFocus } from './useDialogFocus';

const DEFAULT_GEMINI_DASHBOARD_MODEL = 'gemini-3.5-flash';

export const SettingsModal = ({ onClose, currentConfig, onUpdate }) => {
    const [config, setConfig] = useState(currentConfig || { dry_run_mode: true });
    const [loading, setLoading] = useState(null);
    const [error, setError] = useState(null);
    const [oauthCallbackUrl, setOauthCallbackUrl] = useState('');
    const closeRef = useRef(null);
    const dialogRef = useDialogFocus(onClose, closeRef);

    useEffect(() => {
        if (currentConfig) setConfig(currentConfig);
    }, [currentConfig]);

    const updateConfigField = async (field, value) => {
        setLoading(field);
        setError(null);
        try {
            const response = await fetch('api/config', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ [field]: value }),
            });
            if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
            const data = await response.json();
            const next = { ...config, ...data, [field]: data[field] ?? value };
            setConfig(next);
            onUpdate?.(next);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(null);
        }
    };

    const startOpenAIOAuth = async () => {
        setLoading('openai_oauth');
        setError(null);
        try {
            const response = await fetch('api/openai/oauth/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `${response.status}: OAuth start failed`);
            window.open(data.authorization_url, '_blank', 'noopener,noreferrer');
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(null);
        }
    };

    const completeOpenAIOAuth = async () => {
        setLoading('openai_oauth_complete');
        setError(null);
        try {
            const response = await fetch('api/openai/oauth/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ callback_url: oauthCallbackUrl }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `${response.status}: OAuth completion failed`);
            setConfig(prev => ({ ...prev, openai_oauth_status: data }));
            onUpdate?.({ ...config, openai_oauth_status: data });
            setOauthCallbackUrl('');
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(null);
        }
    };

    const refreshOpenAIOAuth = async () => {
        try {
            const response = await fetch('api/openai/oauth/status');
            const data = await response.json();
            setConfig(prev => ({ ...prev, openai_oauth_status: data }));
            onUpdate?.({ ...config, openai_oauth_status: data });
        } catch (err) {
            setError(err.message);
        }
    };

    return (
        <div className="cp-modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
            <section ref={dialogRef} className="cp-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title" tabIndex={-1}>
                <header className="cp-modal-header">
                    <span className="cp-icon-tile"><Settings size={18} /></span>
                    <div><h2 id="settings-title">Runtime settings</h2><p>Immediate controls for this session. Persistent defaults remain in the add-on configuration.</p></div>
                    <button ref={closeRef} className="cp-icon-button" type="button" onClick={onClose} aria-label="Close settings"><X size={19} /></button>
                </header>

                <div className="cp-modal-body">
                    {error && <div className="cp-alert is-danger" role="alert">{error}</div>}
                    <article className="cp-setting-card">
                        <span className={`cp-setting-icon ${config.dry_run_mode ? 'is-warning' : 'is-success'}`}><ShieldCheck size={19} /></span>
                        <div><strong>Global simulation mode</strong><p>When on, Home Assistant mutations are simulated. Per-run plan approval remains active regardless of this setting.</p></div>
                        <button
                            className={`cp-switch ${config.dry_run_mode ? 'is-on' : ''}`}
                            type="button"
                            role="switch"
                            aria-checked={!!config.dry_run_mode}
                            onClick={() => updateConfigField('dry_run_mode', !config.dry_run_mode)}
                            disabled={!!loading}
                        >
                            <span />
                            <em>{config.dry_run_mode ? 'Simulation on' : 'Live tools'}</em>
                        </button>
                    </article>

                    <article className="cp-setting-card">
                        <span className="cp-setting-icon"><Wand2 size={19} /></span>
                        <div><strong>Gemini dashboard generation</strong><p>Optional provider for visual dashboard generation only. It does not replace the deterministic home action kernel.</p></div>
                        <button
                            className={`cp-switch ${config.use_gemini_for_dashboard ? 'is-on' : ''}`}
                            type="button"
                            role="switch"
                            aria-checked={!!config.use_gemini_for_dashboard}
                            onClick={() => updateConfigField('use_gemini_for_dashboard', !config.use_gemini_for_dashboard)}
                            disabled={!!loading}
                        >
                            <span />
                            <em>{config.use_gemini_for_dashboard ? 'Enabled' : 'Disabled'}</em>
                        </button>
                    </article>

                    <div className="cp-settings-grid">
                        <label className="cp-field">
                            <span><Key size={13} /> Gemini API key</span>
                            <input type="password" defaultValue="" onBlur={event => { if (event.target.value) updateConfigField('gemini_api_key', event.target.value); }} placeholder="Leave blank to keep current key" />
                        </label>
                        <label className="cp-field">
                            <span><Wand2 size={13} /> Dashboard model</span>
                            <input defaultValue={config.gemini_model_name || DEFAULT_GEMINI_DASHBOARD_MODEL} onBlur={event => updateConfigField('gemini_model_name', event.target.value)} />
                        </label>
                    </div>

                    <article className="cp-setting-card">
                        <span className="cp-setting-icon"><ExternalLink size={19} /></span>
                        <div>
                            <strong>OpenAI via ChatGPT OAuth</strong>
                            <p>{config.openai_oauth_status?.authenticated ? `Connected${config.openai_oauth_status.account?.email ? ` as ${config.openai_oauth_status.account.email}` : ''}.` : 'Sign in with ChatGPT to use eligible Responses API models without an API key.'}</p>
                            <p className="cp-text-muted">Open Sign in, finish login, copy the full 127.0.0.1 callback URL from the browser address bar, then paste it below.</p>
                            <input
                                className="w-full bg-slate-950 border border-slate-700 rounded-lg text-xs text-slate-300 px-2 py-1.5 mt-2"
                                value={oauthCallbackUrl}
                                onChange={event => setOauthCallbackUrl(event.target.value)}
                                placeholder="http://127.0.0.1:1455/auth/callback?code=...&state=..."
                                aria-label="OpenAI OAuth callback URL"
                            />
                            <button className="cp-button mt-2" type="button" onClick={completeOpenAIOAuth} disabled={!!loading || !oauthCallbackUrl.trim()}>
                                {loading === 'openai_oauth_complete' ? <Loader2 size={14} className="cp-spin" /> : <Key size={14} />} Complete sign-in
                            </button>
                        </div>
                        <div className="cp-settings-actions">
                            <button className="cp-button cp-button--primary" type="button" onClick={startOpenAIOAuth} disabled={!!loading}>
                                {loading === 'openai_oauth' ? <Loader2 size={14} className="cp-spin" /> : <ExternalLink size={14} />} Sign in
                            </button>
                            <button className="cp-button" type="button" onClick={refreshOpenAIOAuth} disabled={!!loading}>Refresh</button>
                        </div>
                    </article>

                    <section className="cp-runtime-facts" aria-label="Runtime information">
                        <h3>Runtime information</h3>
                        <dl>
                            <div><dt><Cpu size={14} /> Reasoning model</dt><dd>{config.deep_reasoning_model || config.orchestrator_model || 'gemma4:e4b'}</dd></div>
                            <div><dt>Default profile</dt><dd>{config.reasoning_default_profile || 'balanced'}</dd></div>
                            <div><dt>Ollama host</dt><dd>{config.ollama_host || 'http://localhost:11434'}</dd></div>
                            <div><dt>Release</dt><dd>{config.version || 'unknown'}</dd></div>
                        </dl>
                    </section>
                </div>

                <footer className="cp-modal-footer">
                    {loading && <span><Loader2 size={14} className="cp-spin" /> Saving runtime change…</span>}
                    <button className="cp-button cp-button--primary" type="button" onClick={onClose}>Done</button>
                </footer>
            </section>
        </div>
    );
};
