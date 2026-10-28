import { useId, useRef } from "react";

interface Props {
  label: string;
  hint: string;
  value: string;
  onChange: (value: string) => void;
}

const MAX_SPEC_CHARS = 500_000; // matches the API's limit

export function SpecEditor({ label, hint, value, onChange }: Props) {
  const id = useId();
  const upload = useRef<HTMLInputElement>(null);

  async function onFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) onChange(await file.text());
    event.target.value = ""; // allow picking the same file again
  }

  const lines = value ? value.split("\n").length : 0;
  const tooBig = value.length > MAX_SPEC_CHARS;

  return (
    <section className="panel spec-editor">
      <div className="panel-header">
        <div>
          <label htmlFor={id} className="panel-title">
            {label}
          </label>
          <p className="panel-hint">{hint}</p>
        </div>
        <button type="button" className="button small" onClick={() => upload.current?.click()}>
          Upload file
        </button>
        <input
          ref={upload}
          type="file"
          accept=".yaml,.yml,.json"
          hidden
          onChange={onFile}
          aria-label={`Upload ${label}`}
        />
      </div>
      <textarea
        id={id}
        className="code-input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={"openapi: 3.0.3\ninfo:\n  title: My API\n  version: 1.0.0\npaths: …"}
        spellCheck={false}
      />
      <p className={tooBig ? "panel-footer error-text" : "panel-footer"}>
        {tooBig
          ? `Too large: the limit is ${MAX_SPEC_CHARS.toLocaleString()} characters.`
          : `${lines.toLocaleString()} lines · YAML or JSON`}
      </p>
    </section>
  );
}
