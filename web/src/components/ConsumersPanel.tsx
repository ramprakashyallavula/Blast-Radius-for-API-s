import { useEffect, useRef, useState } from "react";

import { consumerFromFolder, fromBrowserFile, isSourcePath, SOURCE_EXTENSIONS } from "../files";
import type { Consumer } from "../types";

interface Props {
  consumers: Consumer[];
  onChange: (consumers: Consumer[]) => void;
}

export function ConsumersPanel({ consumers, onChange }: Props) {
  const folderInput = useRef<HTMLInputElement>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    // Folder picking isn't a standard React prop, so set the attribute directly.
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, []);

  async function onFolder(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []).map(fromBrowserFile);
    event.target.value = "";
    if (!files.length) return;
    const { consumer, skippedLarge } = await consumerFromFolder(files);
    if (!consumer) {
      setNotice(`No ${SOURCE_EXTENSIONS.join(" ")} files found in that folder.`);
      return;
    }
    // Uploading a folder with the same name replaces the old one.
    onChange([...consumers.filter((c) => c.name !== consumer.name), consumer]);
    setNotice(
      `Added ${consumer.name} (${plural(consumer.files.length, "file")})` +
        (skippedLarge.length ? `, skipped ${plural(skippedLarge.length, "file")} over 200 KB.` : "."),
    );
  }

  function remove(name: string) {
    onChange(consumers.filter((c) => c.name !== name));
  }

  function addFile(name: string, path: string, content: string) {
    const existing = consumers.find((c) => c.name === name);
    const files = [...(existing?.files ?? []).filter((f) => f.path !== path), { path, content }];
    const updated = { name, files };
    onChange(existing ? consumers.map((c) => (c.name === name ? updated : c)) : [...consumers, updated]);
    setAdding(false);
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2 className="panel-title">Consumers</h2>
          <p className="panel-hint">
            Services that call this API. BlastRadius scans their Python and JS/TS code for HTTP calls.
          </p>
        </div>
        <div className="button-row">
          <button type="button" className="button small" onClick={() => folderInput.current?.click()}>
            Upload a service folder
          </button>
          <button type="button" className="button small" onClick={() => setAdding(true)}>
            Paste a file
          </button>
        </div>
        <input
          ref={folderInput}
          type="file"
          multiple
          hidden
          onChange={onFolder}
          aria-label="Upload a service folder"
        />
      </div>

      {notice && <p className="notice">{notice}</p>}
      {adding && <AddFileForm onAdd={addFile} onCancel={() => setAdding(false)} />}

      {consumers.length === 0 && !adding ? (
        <p className="empty">
          No consumers yet. Without them you'll see what changed, but not who it breaks.
        </p>
      ) : (
        <ul className="consumer-list">
          {consumers.map((c) => (
            <li key={c.name} className="consumer">
              <details>
                <summary>
                  <span className="consumer-name">{c.name}</span>
                  <span className="muted">{plural(c.files.length, "file")}</span>
                </summary>
                <ul className="file-list">
                  {c.files.map((f) => (
                    <li key={f.path}>
                      <code>{f.path}</code>
                    </li>
                  ))}
                </ul>
              </details>
              <button
                type="button"
                className="icon-button"
                onClick={() => remove(c.name)}
                aria-label={`Remove ${c.name}`}
                title="Remove"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function AddFileForm({
  onAdd,
  onCancel,
}: {
  onAdd: (name: string, path: string, content: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [content, setContent] = useState("");
  const validPath = isSourcePath(path);

  return (
    <form
      className="add-file"
      onSubmit={(e) => {
        e.preventDefault();
        onAdd(name.trim(), path.trim(), content);
      }}
    >
      <div className="field-row">
        <label>
          Service name
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="billing-service" required />
        </label>
        <label>
          File path
          <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="app/client.py" required />
        </label>
      </div>
      {path && !validPath && (
        <p className="error-text">Use a source file: {SOURCE_EXTENSIONS.join(" ")}</p>
      )}
      <label>
        Code
        <textarea
          className="code-input short"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          spellCheck={false}
          placeholder={'resp = requests.get(f"{API}/orders/{order_id}")'}
          required
        />
      </label>
      <div className="button-row">
        <button type="submit" className="button primary small" disabled={!name.trim() || !validPath}>
          Add file
        </button>
        <button type="button" className="button small" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
