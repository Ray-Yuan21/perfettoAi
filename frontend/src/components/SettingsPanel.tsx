import { useCallback, useEffect, useState } from "react";
import { fetchSettings, updateSettings, fetchModels } from "../api/client";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function SettingsPanel({ open, onClose }: Props) {
  const [provider, setProvider] = useState("openai");
  const [endpoint, setEndpoint] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiKeySet, setApiKeySet] = useState(false);
  const [modelName, setModelName] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!open) return;
    fetchSettings().then((s) => {
      setProvider(s.provider);
      setEndpoint(s.api_endpoint);
      setModelName(s.model_name);
      setApiKeySet(s.api_key_set);
      setApiKey("");
    });
  }, [open]);

  const loadModels = useCallback(async () => {
    setModelsLoading(true);
    try {
      const res = await fetchModels();
      setModels(res.models);
      if (res.error) setMessage(`Models: ${res.error}`);
    } catch {
      setMessage("Failed to fetch models");
    } finally {
      setModelsLoading(false);
    }
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setMessage("");
    try {
      await updateSettings({
        provider,
        api_endpoint: endpoint,
        model_name: modelName,
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setMessage("Saved");
      setApiKeySet(!!apiKey || apiKeySet);
      setApiKey("");
      loadModels();
    } catch {
      setMessage("Save failed");
    } finally {
      setSaving(false);
    }
  }, [provider, endpoint, modelName, apiKey, apiKeySet]);

  if (!open) return null;

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="settings-panel" onClick={(e) => e.stopPropagation()}>
        <div className="settings-header">
          <span className="settings-title">LLM Settings</span>
          <button className="settings-close" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="settings-body">
          <label className="settings-label">Provider</label>
          <select
            className="settings-select"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
          </select>

          <label className="settings-label">API Endpoint</label>
          <input
            className="settings-input"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            placeholder="https://api.openai.com/v1"
          />

          <label className="settings-label">
            API Key{apiKeySet && !apiKey ? " (set)" : ""}
          </label>
          <input
            className="settings-input"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={apiKeySet ? "••••••••" : "Enter API key"}
          />

          <label className="settings-label">Model</label>
          <div className="settings-model-row">
            <select
              className="settings-select"
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              onFocus={() => {
                if (models.length === 0) loadModels();
              }}
            >
              {modelName && !models.includes(modelName) && (
                <option value={modelName}>{modelName}</option>
              )}
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <button
              className="settings-refresh-btn"
              onClick={loadModels}
              disabled={modelsLoading}
              title="Refresh model list"
            >
              {modelsLoading ? "..." : "Refresh"}
            </button>
          </div>

          {message && <div className="settings-message">{message}</div>}
        </div>

        <div className="settings-footer">
          <button
            className="settings-save-btn"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
