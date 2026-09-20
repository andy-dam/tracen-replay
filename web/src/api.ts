// Typed client for the service API (api/openapi.yaml). Every shape here
// mirrors the Go JSON exactly; nothing is derived or re-computed on the
// client. The session lives in an HttpOnly cookie the browser sends itself.

export interface User {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
}

export interface Recording {
  id: string;
  user_id: string;
  name: string;
  size: number;
  sha256: string;
  created_at: string;
}

export interface Failure {
  code: string;
  message: string;
}

export interface Job {
  id: string;
  user_id?: string;
  source_id: string;
  source_name: string;
  status: string;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  stage?: string;
  ocr_processed?: number;
  ocr_total?: number;
  error?: Failure;
  stage_failures?: { stage: string; error: string }[];
  report_id?: string;
}

export interface Report {
  id: string;
  user_id?: string;
  job_id?: string;
  origin: string;
  source_name: string;
  source_sha256: string;
  created_at: string;
  duration_ms: number;
  turns: number;
  entries: number;
}

export interface FieldAccounting {
  before: number | null;
  after: number | null;
  status: string;
  direct: number | null;
  derived: number | null;
  unresolved: number | null;
  turn_difference?: number | null;
  turn_difference_owner?: string;
  window_start_ms?: number | null;
  window_end_ms?: number | null;
}

export interface Opening {
  stats: Record<string, number | null> | null;
  performance: Record<string, number | null> | null;
}

export interface TurnSummary {
  id: string;
  label: string;
  phase: string;
  calendar_value: unknown;
  start_ms: number | null;
  end_ms: number | null;
  window_kind: string;
  action_status: string;
  action_count: number;
  expects_one_action: boolean;
  scheduled_race?: string;
  differences?: Difference[];
  entry_count: number;
  opening_observed: boolean;
  accounting_status_counts: Record<string, number>;
  action_kind?: string;
  training_option?: string;
  /** What stood in for a commit the report never saw: result_card_only, outing_menu_and_receipt or hub_exit_and_receipt. */
  action_basis?: string;
  /** The action shown is the viewer's own answer, not the report's reading. */
  action_filled_in?: boolean;
  opening: Opening;
  /** The previous turn's opening plus what its entries explain, when this turn's opening was never on screen. */
  opening_estimate?: Opening;
  opening_estimate_basis?: string;
}

export interface Turn extends Omit<TurnSummary, "entry_count" | "opening_observed" | "accounting_status_counts" | "action_kind" | "training_option"> {
  accounting: Record<string, Record<string, FieldAccounting>>;
}

export interface Change {
  amount: number | null;
  basis?: string;
  /** the digits the panel showed when a clipped badge was completed from the turn difference */
  read_amount?: number | null;
  /** gains a reader saw on the training's own card while this amount was worked out from the turn difference */
  contradicted_by?: number[];
  /** the other gains the card was read as when the stat bars settled the field at this amount, itself one of the card's reads */
  disagreeing_reads?: number[];
}

export interface Entry {
  id: string;
  kind: string;
  turn_id: string;
  first_seen_ms: number | null;
  last_seen_ms: number | null;
  assignment_basis?: string;
  context_title?: string;
  training_option?: string;
  action_kind?: string;
  raw_text?: string;
  accounting_role?: string;
  transaction_id?: string;
  reward_link_status?: string;
  effect?: Record<string, unknown>;
  accepted_award?: boolean;
  conflicts_present?: boolean;
  changes?: Record<string, Record<string, Change>>;
  detail?: Record<string, unknown>;
}

export interface Summary {
  report: Report;
  source: { name: string; sha256: string; duration_ms: number; width: number; height: number };
  recognition: { model: string; device: string };
  summary: {
    field_status_counts: Record<string, number>;
    action_statuses: Record<string, number>;
    observed_turn_windows: number;
    entry_counts: Record<string, number>;
    stage_failures: { stage: string; error: string }[];
  };
  turns: number;
  entries: number;
  unassigned: number;
  video_available: boolean;
  // Which analyzer made this report and which one is installed now. Each side
  // is present only when it is known, and `stale` only when both are, so an
  // absent field means unknown rather than same.
  analyzer?: {
    report?: AnalyzerVersion;
    installed?: AnalyzerVersion;
    stale?: boolean;
  } | null;
}

export interface AnalyzerVersion {
  package: string;
  code_digest: string;
}

export interface Readiness {
  ready: boolean;
  checks: { name: string; ok: boolean; note?: string }[];
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

/** One stat change between two observations that no captured event covers, or that was worked out onto its only possible owner. */
export interface Difference {
  channel: string;
  field: string;
  amount: number;
  worked_out: boolean;
  /** training, event (a receipt that lost its number) or lesson, when worked out */
  owner?: string;
  window_start_ms?: number | null;
  window_end_ms?: number | null;
}
/** The viewer's edit of one of the report's entries. */
export interface EntryEdit {
  changes?: Record<string, Record<string, number>>;
  deleted?: boolean;
  reviewed?: boolean;
  note?: string;
}
/** An event the viewer saw that the report has no entry for. */
export interface AddedEvent {
  id: string;
  kind: string;
  title?: string;
  time_ms?: number | null;
  changes?: Record<string, Record<string, number>>;
  note?: string;
}
/** What a viewer filled in for one turn; checked by the server against the next observed values. */
export interface CorrectionChange {
  field: string;
  channel?: string;
  amount: number;
  note?: string;
}
export interface CorrectionAction {
  kind: string;
  training_option?: string;
  name?: string;
  gains?: Record<string, number>;
}
export interface Correction {
  report_id: string;
  turn_id: string;
  action?: CorrectionAction | null;
  changes: CorrectionChange[];
  entries?: Record<string, EntryEdit>;
  added?: AddedEvent[];
  note?: string;
  created_at: string;
  updated_at: string;
}
export interface FieldVerification {
  channel: string;
  field: string;
  before: number | null;
  after: number | null;
  recorded: number;
  turn_difference: number;
  residual: number | null;
  supplied: number;
  status: string;
  window_start_ms?: number | null;
  window_end_ms?: number | null;
}
export interface Verification {
  fields: FieldVerification[];
  balanced: boolean;
  verified: boolean;
  summary: string;
}
export interface TurnDetail {
  turn: Turn;
  entries: Entry[];
  correction?: Correction | null;
  verification?: Verification | null;
}

// The API's origin. Empty means the client is served by the service itself;
// otherwise every call, stream and media URL is prefixed and sent with
// credentials, and the service must list this client's origin.
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/+$/, "");
export const crossOrigin = API_BASE ? "use-credentials" : undefined;
const url = (path: string) => API_BASE + path;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...((init?.headers as Record<string, string>) ?? {}) };
  if (init?.body && !(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(url(path), { ...init, headers, credentials: API_BASE ? "include" : "same-origin" });
  if (!response.ok) {
    let code = "http_" + response.status;
    let message = response.statusText;
    try {
      const body = await response.json();
      if (body?.error?.code) {
        code = body.error.code;
        message = body.error.message;
      }
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, code, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  ready: () => request<Readiness>("/readyz").catch((e) => (e instanceof ApiError && e.status === 503 ? ({ ready: false, checks: [] } as Readiness) : Promise.reject(e))),
  me: () => request<{ user: User }>("/api/auth/me").then((r) => r.user),
  login: (email: string, password: string) => request<{ user: User }>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }).then((r) => r.user),
  register: (email: string, password: string, display_name: string) =>
    request<{ user: User }>("/api/auth/register", { method: "POST", body: JSON.stringify({ email, password, display_name }) }).then((r) => r.user),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  recordings: () => request<{ recordings: Recording[] }>("/api/recordings").then((r) => r.recordings),
  deleteRecording: (id: string) => request<void>(`/api/recordings/${enc(id)}`, { method: "DELETE" }),
  deleteReport: (id: string) => request<void>(`/api/reports/${enc(id)}`, { method: "DELETE" }),
  jobs: () => request<{ jobs: Job[] }>("/api/jobs").then((r) => r.jobs),
  job: (id: string) => request<Job>(`/api/jobs/${enc(id)}`),
  submit: (sourceId: string) => request<Job>("/api/jobs", { method: "POST", body: JSON.stringify({ source_id: sourceId }) }),
  cancel: (id: string) => request<Job>(`/api/jobs/${enc(id)}/cancel`, { method: "POST" }),
  reports: () => request<{ reports: Report[] }>("/api/reports").then((r) => r.reports),
  summary: (id: string) => request<Summary>(`/api/reports/${enc(id)}/summary`),
  turns: (id: string) => request<{ turns: TurnSummary[] }>(`/api/reports/${enc(id)}/turns`).then((r) => r.turns),
  turn: (id: string, turnId: string) => request<TurnDetail>(`/api/reports/${enc(id)}/turns/${enc(turnId)}`),
  correction: (id: string, turnId: string) => request<{ correction: Correction | null; verification: Verification | null }>(`/api/reports/${enc(id)}/turns/${enc(turnId)}/correction`),
  saveCorrection: (id: string, turnId: string, body: { action?: CorrectionAction | null; changes: CorrectionChange[]; entries?: Record<string, EntryEdit>; added?: AddedEvent[]; note?: string }) =>
    request<{ correction: Correction; verification: Verification }>(`/api/reports/${enc(id)}/turns/${enc(turnId)}/correction`, { method: "PUT", body: JSON.stringify(body) }),
  deleteCorrection: (id: string, turnId: string) => request<void>(`/api/reports/${enc(id)}/turns/${enc(turnId)}/correction`, { method: "DELETE" }),
  corrections: (id: string) => request<{ corrections: { correction: Correction; verification: Verification }[] }>(`/api/reports/${enc(id)}/corrections`).then((r) => r.corrections),
  unassigned: (id: string) => request<{ entries: Entry[] }>(`/api/reports/${enc(id)}/unassigned`).then((r) => r.entries),
  entries: (id: string) => request<{ entries: Entry[] }>(`/api/reports/${enc(id)}/entries`).then((r) => r.entries),
  frameUrl: (id: string, ms: number) => url(`/api/reports/${enc(id)}/frame?ms=${ms}`),
  videoUrl: (id: string) => url(`/api/reports/${enc(id)}/video`),
  recordingVideoUrl: (id: string) => url(`/api/recordings/${enc(id)}/video`),
  recordingFrameUrl: (id: string, ms: number) => url(`/api/recordings/${enc(id)}/frame?ms=${ms}`),
  logUrl: (id: string) => url(`/api/jobs/${enc(id)}/log`),
  downloadUrl: (id: string) => url(`/api/reports/${enc(id)}/download`),
  events: (id: string) => new EventSource(url(`/api/jobs/${enc(id)}/events`), { withCredentials: !!API_BASE }),
};

export interface UploadTarget {
  mode: "direct" | "multipart";
  id?: string;
  url?: string;
  method?: string;
  headers?: Record<string, string>;
  expires_at?: string;
}

/**
 * Upload a recording with progress; XMLHttpRequest is the only browser API that reports upload progress.
 * The service says where the bytes go: straight into its object store by a signed URL (the API then
 * records the upload once told it is complete), or to the API itself as a multipart post.
 */
export function uploadRecording(file: File, onProgress: (sent: number, total: number) => void): { promise: Promise<Recording>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  let aborted = false;
  const send = (method: string, target: string, headers: Record<string, string>, body: FormData | File, credentials: boolean) =>
    new Promise<string>((resolve, reject) => {
      xhr.open(method, target);
      xhr.withCredentials = credentials;
      for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value);
      xhr.upload.onprogress = (e) => onProgress(e.loaded, e.lengthComputable ? e.total : file.size);
      xhr.onerror = () => reject(new ApiError(0, "network", "the upload was interrupted"));
      xhr.onabort = () => reject(new ApiError(0, "aborted", "the upload was cancelled"));
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.responseText);
        else {
          let error: Failure | undefined;
          try {
            error = (JSON.parse(xhr.responseText) as { error?: Failure }).error;
          } catch {
            // no JSON body: a storage service answers with XML or nothing
          }
          reject(new ApiError(xhr.status, error?.code ?? "http_" + xhr.status, error?.message ?? xhr.statusText));
        }
      };
      xhr.send(body);
    });
  const promise = (async () => {
    const target = await request<UploadTarget>("/api/recordings/uploads", { method: "POST", body: JSON.stringify({ name: file.name, size: file.size }) });
    if (aborted) throw new ApiError(0, "aborted", "the upload was cancelled");
    if (target.mode === "direct" && target.url && target.id) {
      await send(target.method ?? "PUT", target.url, target.headers ?? {}, file, false);
      return request<Recording>(`/api/recordings/uploads/${enc(target.id)}/complete`, { method: "POST", body: JSON.stringify({ name: file.name }) });
    }
    const form = new FormData();
    form.append("file", file, file.name);
    return JSON.parse(await send("POST", url("/api/recordings"), {}, form, !!API_BASE)) as Recording;
  })();
  return {
    promise,
    abort: () => {
      aborted = true;
      xhr.abort();
    },
  };
}
