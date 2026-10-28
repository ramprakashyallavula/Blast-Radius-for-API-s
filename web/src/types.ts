// Mirrors the request/response models in src/blastradius/server.py.

export interface SourceFile {
  path: string;
  content: string;
}

export interface Consumer {
  name: string;
  files: SourceFile[];
}

export interface AnalyzeRequest {
  base_spec: string;
  head_spec: string;
  consumers: Consumer[];
}

export type Severity = "breaking" | "warning" | "safe";
export type ImpactLevel = "will_break" | "may_break" | "unaffected";

export interface Change {
  kind: string;
  severity: Severity;
  method: string;
  path: string;
  field: string | null;
  message: string;
  before?: unknown;
  after?: unknown;
  /** False for a change inside a oneOf/anyOf branch: impact is at most "may break". */
  certain: boolean;
}

export interface Impact {
  consumer: string;
  file: string;
  line: number;
  impact: ImpactLevel;
  reason: string;
  code: string;
}

export interface ChangeImpact extends Change {
  worst_impact: ImpactLevel;
  impacts: Impact[];
}

export interface UnmatchedCall {
  consumer: string;
  file: string;
  line: number;
  method: string;
  url: string;
}

export interface AnalyzeResponse {
  version: string;
  summary: {
    breaking_changes: number;
    consumers: string[];
    will_break: string[];
    may_break: string[];
    call_sites: number;
    unmatched_call_sites: number;
  };
  changes: ChangeImpact[];
  safe_changes: Change[];
  unmatched: UnmatchedCall[];
  markdown: string;
}
