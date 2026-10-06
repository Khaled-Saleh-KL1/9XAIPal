import type { ModelCatalog, ModelInfo } from '../api';

const MODEL_LABELS: Record<string, string> = {
  'meta/muse-glimmer-30b': 'Muse Glimmer 30B (NVIDIA)',
  'gemma4:31b': 'Gemma 4 31B',
  'gpt-oss:120b': 'GPT OSS 120B',
  'gpt-oss:20b': 'GPT OSS 20B',
  'nemotron-3-super': 'Nemotron 3 Super',
  'nemotron-3-nano:30b': 'Nemotron 3 Nano 30B',
  'nemotron-3-ultra': 'Nemotron 3 Ultra',
  'glm-5.3-flash': 'GLM 5.3 Flash',
};

export function modelLabel(model: ModelInfo): string {
  return MODEL_LABELS[model.name] ?? model.name;
}

export function resolveAvailableModel(catalog: ModelCatalog, current: string): string {
  const isAvailable = (name: string) =>
    catalog.models.some((model) => model.name === name && model.available !== false);
  if (current && isAvailable(current)) return current;
  if (isAvailable(catalog.default)) return catalog.default;
  return catalog.models.find((model) => model.available !== false)?.name ?? '';
}

export function ModelPicker({
  catalog,
  model,
  onChange,
  title,
}: {
  catalog: ModelCatalog | null;
  model: string;
  onChange: (name: string) => void;
  title: string;
}) {
  if (!catalog || catalog.models.length === 0) return null;

  const available = catalog.models.filter((item) => item.available !== false);
  const unavailable = catalog.models.filter((item) => item.available === false);
  const local = available.filter((item) => !item.is_cloud);
  const cloud = available.filter((item) => item.is_cloud);

  return (
    <label className="model-picker" title={title}>
      <select
        aria-label={title}
        value={model}
        onChange={(event) => onChange(event.target.value)}
      >
        {local.length > 0 && (
          <optgroup label="Local">
            {local.map((item) => (
              <option key={item.name} value={item.name}>{modelLabel(item)}</option>
            ))}
          </optgroup>
        )}
        {cloud.length > 0 && (
          <optgroup label="Cloud">
            {cloud.map((item) => (
              <option key={item.name} value={item.name}>{modelLabel(item)}</option>
            ))}
          </optgroup>
        )}
        {unavailable.length > 0 && (
          <optgroup label="Unavailable">
            {unavailable.map((item) => {
              const reason = item.unavailable_reason || 'model unavailable';
              return (
                <option key={item.name} value={item.name} disabled title={reason}>
                  {modelLabel(item)} ({reason})
                </option>
              );
            })}
          </optgroup>
        )}
      </select>
    </label>
  );
}
