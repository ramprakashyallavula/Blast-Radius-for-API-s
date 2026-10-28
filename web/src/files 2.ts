import type { Consumer } from "./types";

// Keep in sync with SOURCE_SUFFIXES, SKIP_DIRS and MAX_FILE_CHARS in the Python package.
export const SOURCE_EXTENSIONS = [".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"];
const SKIP_DIRS = new Set([
  ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next",
  "coverage", ".pytest_cache",
]);
export const MAX_FILE_BYTES = 200_000;

export function isSourcePath(path: string): boolean {
  const parts = path.split("/");
  if (parts.some((p) => SKIP_DIRS.has(p))) return false;
  return SOURCE_EXTENSIONS.some((ext) => path.endsWith(ext));
}

export interface PickedFile {
  /** Path including the chosen folder, e.g. "billing-service/billing/invoices.py". */
  relativePath: string;
  size: number;
  read: () => Promise<string>;
}

export interface FolderResult {
  consumer: Consumer | null;
  skippedLarge: string[];
}

/**
 * Turn the files from a folder picker into one consumer named after the folder.
 * Non-source files and dependency folders (node_modules, .venv, ...) are left out.
 */
export async function consumerFromFolder(files: PickedFile[]): Promise<FolderResult> {
  const skippedLarge: string[] = [];
  const sourceFiles = [];
  let name = "";
  for (const file of files) {
    const [folder, ...rest] = file.relativePath.split("/");
    const path = rest.join("/");
    name ||= folder;
    if (!path || !isSourcePath(path)) continue;
    if (file.size > MAX_FILE_BYTES) {
      skippedLarge.push(path);
      continue;
    }
    sourceFiles.push({ path, content: await file.read() });
  }
  sourceFiles.sort((a, b) => a.path.localeCompare(b.path));
  return {
    consumer: sourceFiles.length ? { name, files: sourceFiles } : null,
    skippedLarge,
  };
}

export function fromBrowserFile(file: File): PickedFile {
  return {
    relativePath: file.webkitRelativePath || file.name,
    size: file.size,
    read: () => file.text(),
  };
}
