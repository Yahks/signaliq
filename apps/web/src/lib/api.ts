/**
 * Typed API client.
 *
 * All requests go through /api/* which Next.js rewrites to the backend.
 * Auth tokens live in memory + localStorage; refresh is automatic.
 */
import { useAuthStore } from "@/stores/auth";

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  auth?: boolean;
};

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  /** FastAPI {detail: {reason, message}} → readable string */
  get userMessage(): string {
    const d = this.detail as { reason?: string; message?: string } | undefined;
    if (d && typeof d === "object" && d.message) return d.message;
    if (typeof this.detail === "string") return this.detail;
    return this.message;
  }
}

/**
 * S.6.4.1d.1 — extract a human-readable message from any thrown value.
 *
 * Uses duck-typing rather than `instanceof ApiError`. In Next.js dev
 * mode the same module can resolve to two distinct class objects (one
 * in the page bundle, one in the lib bundle), so `instanceof` returns
 * false even for our own errors. Reading the properties we care about
 * (userMessage, message) works regardless of which class object the
 * error is an instance of.
 */
export function getErrorMessage(e: unknown, fallback = "Something went wrong"): string {
  if (!e) return fallback;
  if (typeof e === "string") return e;
  const maybe = e as { userMessage?: string; message?: string };
  if (typeof maybe.userMessage === "string" && maybe.userMessage) return maybe.userMessage;
  if (typeof maybe.message === "string" && maybe.message) return maybe.message;
  return fallback;
}


async function rawRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, headers = {}, auth = true } = opts;
  const finalHeaders: Record<string, string> = { ...headers };
  if (body !== undefined) finalHeaders["Content-Type"] = "application/json";

  if (auth) {
    const token = useAuthStore.getState().accessToken;
    if (token) finalHeaders["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(path, {
    method,
    headers: finalHeaders,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  const data = text ? safeJson(text) : {};

  if (!res.ok) {
    // Two error shapes arrive at this client:
    //   1. Our own endpoints:  { error: { code, message, context } }
    //   2. FastAPI validation: { detail: "..." }
    //      our verify error:   { detail: { reason, message, provider } }
    //
    // Prefer FastAPI's structured detail when it carries a message.
    // This is what surfaces "The provider rejected this key" instead of
    // a bare "HTTP 400".
    type OwnError = { error?: { code?: string; message?: string; context?: Record<string, unknown> } };
    type FastApiDetail = string | { message?: string; reason?: string; provider?: string } | undefined;

    const own = (data as OwnError).error;
    const detail = (data as { detail?: FastApiDetail }).detail;

    let message = `HTTP ${res.status}`;
    let code = "unknown";
    let extra: unknown = undefined;

    if (detail !== undefined) {
      if (typeof detail === "string") {
        message = detail;
      } else if (detail && typeof detail === "object") {
        message = detail.message ?? detail.reason ?? message;
        code = detail.reason ?? code;
        extra = detail;
      }
    } else if (own?.message) {
      message = own.message;
      code = own.code ?? "unknown";
      extra = own.context;
    }

    throw new ApiError(res.status, message, { code, extra });
  }
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

/**
 * Request with automatic refresh-token flow.
 * If a 401 comes back and we have a refresh token, refresh and retry once.
 */
async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  try {
    return await rawRequest<T>(path, opts);
  } catch (err) {
    const errStatus = (err as { status?: number })?.status;
    if (errStatus === 401 && opts.auth !== false) {
      const refreshed = await useAuthStore.getState().tryRefresh();
      if (refreshed) {
        return await rawRequest<T>(path, opts);
      }
    }
    throw err;
  }
}

// ---------- typed endpoints ----------
export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
};

export type UserResponse = {
  id: string;
  email: string;
  full_name: string | null;
  email_verified: boolean;
  is_superadmin: boolean;
};

export type MeResponse = {
  user: UserResponse;
  org_id: string | null;
  org_slug: string | null;
  role: string | null;
  // S.7.5a — true when the org has not finished onboarding.
  onboarding_required: boolean;
};

export type SourceResponse = {
  id: string;
  name: string;
  source_type: string;
  description: string | null;
  collection_method: string | null;
  retention_policy: string | null;
  config: Record<string, unknown>;
  last_ingested_at: string | null;
  created_at: string;
};

export type JobResponse = {
  id: string;
  source_id: string;
  status: "pending" | "running" | "succeeded" | "failed";
  file_format: string | null;
  total_records: number | null;
  processed_records: number;
  failed_records: number;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type NormalizedRecord = {
  id: string;
  raw_record_id: string;
  normalized_text: string;
  detected_language: string;
  language_confidence: number | null;
  is_duplicate: boolean;
  is_spam: boolean;
  is_bot_suspected: boolean;
  urls: string[];
  hashtags: string[];
  mentions: string[];
  tokens_count: number | null;
  created_at: string;
};

export type RecordListResponse = {
  items: NormalizedRecord[];
  total: number;
  limit: number;
  offset: number;
};

export type RecordDetail = {
  raw: {
    id: string;
    source_id: string;
    ingestion_job_id: string | null;
    source_record_id: string | null;
    author_identifier: string | null;
    original_timestamp: string | null;
    text: string;
    language: string | null;
    metadata: Record<string, unknown>;
    content_hash: string;
    created_at: string;
  };
  normalized: NormalizedRecord | null;
};


// ---------- Source reliability (Phase 10.9b) ----------

export type SourceReliabilityItem = {
  slug: string;
  name: string;
  kind: string;
  enabled: boolean;
  predictions_scored: number | null;
  result_accuracy: number | null;
  avg_brier_score: number | null;
  reliability_weight: number | null;
  computed_at: string | null;
  low_sample: boolean;
};

export type SourceReliabilityList = {
  window_days: number;
  items: SourceReliabilityItem[];
};



// ---------- Trading block (Phase T) ----------

export type TradingSubblockResponse = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  category: string | null;
  cadence: string;
  source_kind: string | null;
  enabled: boolean;
  sort_order: number;
  theme: { color?: string; icon?: string; layout?: string } | null;
  post_count: number;
  latest_post_at: string | null;
};

export type TradingSubblockListResponse = {
  items: TradingSubblockResponse[];
  total: number;
};

export type TradingPostResponse = {
  id: string;
  subblock_id: string;
  subblock_slug: string | null;
  title: string;
  body: string;
  format: string;
  source_kind: string;
  source_urls: string[];
  symbols: string[];
  status: string;
  word_count: number | null;
  published_at: string | null;
  created_at: string;
};

export type TradingPostListResponse = {
  items: TradingPostResponse[];
  total: number;
  limit: number;
  offset: number;
};

export type TradingGenerateResponse = {
  task_id: string;
  status: string;
  slug: string;
};



// ---------- Trading health (T.8) ----------

export type TradingSubblockHealth = {
  slug: string;
  name: string;
  cadence: string;
  source_kind: string | null;
  posts_24h: number;
  drafts_24h: number;
  last_published_at: string | null;
  last_created_at: string | null;
  avg_word_count_24h: number | null;
  minutes_since_last: number | null;
  expected_cadence_minutes: number;
  status: "healthy" | "delayed" | "stale" | "never_run";
};

export type TradingPerformanceTrade = {
  traded_at: string;
  direction: string;
  contracts: number;
  entry: number;
  stop: number | null;
  exit: number;
  result: number;
  equity: number;
};

export type TradingPerformanceSummary = {
  trades: number;
  winners: number;
  losers: number;
  scratches: number;
  net: number;
  ending: number;
  peak: number;
  trough: number;
  max_dd: number;
  max_dd_pct: number;
  account_name: string;
  starting_balance: number;
};

export type TradingPerformanceMonthly = {
  month: string;
  trades: number;
  winners: number;
  losers: number;
  net: number;
  ending_equity: number;
};

export type GenerationRun = {
  id: string;
  subblock_slug: string;
  status: string;
  error: string | null;
  post_id: string | null;
  started_at: string;
  finished_at: string | null;
};

export type TradingPerformanceResponse = {
  summary: TradingPerformanceSummary;
  trades: TradingPerformanceTrade[];
  monthly: TradingPerformanceMonthly[];
};

export type TradingHealthResponse = {
  items: TradingSubblockHealth[];
  summary: {
    total: number;
    healthy: number;
    delayed: number;
    stale: number;
    never_run: number;
  };
  generated_at: string;
};



// ---------- Cross-subblock symbol timeline (T.9) ----------

export type SymbolPostMention = {
  post_id: string;
  subblock_slug: string;
  subblock_name: string;
  title: string;
  published_at: string | null;
  word_count: number | null;
};

export type SymbolTimelineResponse = {
  ticker: string;
  total_mentions: number;
  subblocks: string[];
  latest_mention: string | null;
  mentions: SymbolPostMention[];
  related_tickers: Array<{ ticker: string; co_occurrences: number }>;
};



// ---------- Symbol index (T.9.2) ----------

export type SymbolIndexItem = {
  ticker: string;
  mentions: number;
};

export type SymbolIndexResponse = {
  items: SymbolIndexItem[];
  total: number;
};



// ---------- Draft review (T.11) ----------

export type DraftPostResponse = {
  id: string;
  subblock_slug: string;
  subblock_name: string;
  title: string;
  body: string;
  source_kind: string;
  word_count: number | null;
  created_at: string;
  unsupported_claims: string[];
  regenerated: boolean;
};

export type DraftListResponse = {
  items: DraftPostResponse[];
  total: number;
};

export type DraftActionResult = {
  post_id: string;
  new_status: string;
};



// ---------- Token balance (S.3) ----------

export type TokenBalanceResponse = {
  balance: number;
  lifetime_granted: number;
  lifetime_consumed: number;
  consumed_today: number;
  calls_today: number;
  tokens_per_usd: number;
  balance_usd: number;
  consumed_today_usd: number;
};



// ---------- Block settings (S.4) ----------

export type BlockSettingsItem = {
  slug: string;
  name: string;
  auto_enabled: boolean;
  updated_at: string | null;
};

export type BlockSettingsListResponse = {
  items: BlockSettingsItem[];
};

// ---------- Editorial voice (S.7.8b) ----------
export type EditorialVoice = {
  name: string;
  directive: string;
};

export type EditorialSettingsResponse = {
  voice: string;
  default: string;
  available: EditorialVoice[];
};

export type EditorialUpdateResponse = {
  voice: string;
};

// ---------- Studio chat (S.8) ----------
export type StudioMessageItem = {
  id: string;
  role: "user" | "assistant";
  content: string;
  prompt_kind: string | null;
  trading_post_id: string | null;
  track: string | null;
  created_at: string;
  is_system?: boolean;
};

export type StudioMessageListResponse = {
  items: StudioMessageItem[];
};

export type StudioMessageResponse = {
  message_id: string;
  kind: string;              // "article" | "question"
  content: string;
  trading_post_id: string | null;
  track: string | null;
};

export type StudioPostRequest = {
  prompt: string;
  form?: string | null;
  tone?: string | null;
  track?: string | null;
};

export type StudioDeleteResponse = {
  id: string;
  action: string;
};



// ---------- Credentials (BYOK, S.5.4b) ----------

export type Credential = {
  id: string;
  provider: string;
  model_name: string;
  roles: string[];
  label: string;
  key_preview: string;
  is_active: boolean;
  priority: number;
  exhausted_until: string | null;
  last_success_at: string | null;
  created_at: string;
  // S.6.4.3
  tokens_used: number;
  calls_made: number;
  last_remaining_tokens: number | null;
  last_rate_at: string | null;
};

export type CredentialListResponse = {
  items: Credential[];
};

export type CreateCredentialInput = {
  provider: string;
  api_key: string;
  label?: string;
};

// S.7.6d.2 — trash
export type TrashActionResult = {
  id: string;
  kind: "trading" | "news" | "brief";
  action: "deleted" | "restored" | "purged";
  purge_at: string | null;
};

export type TrashItem = {
  id: string;
  kind: "trading" | "news" | "brief";
  title: string;
  word_count: number | null;
  deleted_at: string;
  purge_at: string;
};

export type TrashListResponse = {
  items: TrashItem[];
  total: number;
};

export const api = {
  // auth
  register: (body: {
    email: string;
    username?: string;
    password: string;
    full_name?: string;
    country?: string;
    accepted_terms: boolean;
    accepted_age: boolean;
    accepted_risk: boolean;
  }) => request<TokenResponse>("/api/auth/register", { method: "POST", body, auth: false }),

  login: (body: { identifier: string; password: string }) =>
    request<TokenResponse>("/api/auth/login", { method: "POST", body, auth: false }),

  me: () => request<MeResponse>("/api/auth/me"),

  // sources
  listSources: () => request<SourceResponse[]>("/api/sources"),
  createSource: (body: { name: string; source_type: string; description?: string }) =>
    request<SourceResponse>("/api/sources", { method: "POST", body }),

  // ingestion
  uploadFile: async (sourceId: string, file: File, mapping?: Record<string, string>) => {
    const form = new FormData();
    form.append("source_id", sourceId);
    form.append("file", file);
    if (mapping) form.append("mapping", JSON.stringify(mapping));
    const token = useAuthStore.getState().accessToken;
    const res = await fetch("/api/ingestion/upload", {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new ApiError(res.status, "upload_failed", text || `HTTP ${res.status}`);
    }
    return (await res.json()) as { job_id: string; status: string; message: string };
  },

  getJob: (jobId: string) => request<JobResponse>(`/api/ingestion/jobs/${jobId}`),
  listJobs: () => request<JobResponse[]>("/api/ingestion/jobs"),

  // records
  listRecords: (params?: { limit?: number; offset?: number; language?: string }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    if (params?.language) q.set("language", params.language);
    const qs = q.toString();
    return request<RecordListResponse>(`/api/records${qs ? `?${qs}` : ""}`);
  },
  getRecord: (id: string) => request<RecordDetail>(`/api/records/${id}`),

  // enrichment
  getEnrichment: (recordId: string) => request<EnrichmentResponse>(`/api/enrichments/${recordId}`),

  // entities
  listEntities: (params?: { limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    const qs = q.toString();
    return request<EntityResponse[]>(`/api/entities${qs ? `?${qs}` : ""}`);
  },
  getEntity: (id: string) => request<EntityDetail>(`/api/entities/${id}`),

  // topics
  listTopics: (params?: { limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<TopicResponse[]>(`/api/topics${qs ? `?${qs}` : ""}`);
  },

  // search
  semanticSearch: (query: string, limit: number = 20) =>
    request<SemanticSearchResponse>("/api/search/semantic", {
      method: "POST",
      body: { query, limit },
    }),
  similarRecords: (recordId: string, limit: number = 10) =>
    request<SemanticSearchResponse>(`/api/search/similar/${recordId}?limit=${limit}`),

  // clusters
  listClusters: (limit: number = 100) =>
    request<ClusterResponse[]>(`/api/clusters?limit=${limit}`),
  getCluster: (id: string) => request<ClusterDetail>(`/api/clusters/${id}`),

  // pain points
  listPainPoints: (limit: number = 50) =>
    request<PainPointResponse[]>(`/api/pain-points?limit=${limit}`),
  getPainPoint: (id: string) => request<PainPointDetail>(`/api/pain-points/${id}`),

  // dashboard
  dashboardSummary: () => request<DashboardSummary>("/api/dashboard/summary"),

  // trading block (Phase T)
  listTradingSubblocks: () =>
    request<TradingSubblockListResponse>("/api/trading/subblocks"),
  getTradingSubblock: (slug: string) =>
    request<TradingSubblockResponse>(`/api/trading/subblocks/${slug}`),
  listTradingPosts: (
    slug: string,
    limit: number = 20,
    offset: number = 0,
    status?: string | null,
  ) => {
    const qs = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    // status=null means "all statuses" (empty string param → the API
    // skips the status filter). undefined means the API default
    // (published only).
    if (status === null) {
      qs.set("status", "");
    } else if (status !== undefined) {
      qs.set("status", status);
    }
    return request<TradingPostListResponse>(
      `/api/trading/subblocks/${slug}/posts?${qs}`,
    );
  },
  getTradingPost: (postId: string) =>
    request<TradingPostResponse>(`/api/trading/posts/${postId}`),

  getTokenBalance: () =>
    request<TokenBalanceResponse>("/api/auth/tokens/balance"),

  // block settings (S.4 — per-block auto toggle)
  listBlockSettings: () =>
    request<BlockSettingsListResponse>("/api/settings/blocks"),
  updateBlockSettings: (slug: string, autoEnabled: boolean) =>
    request<BlockSettingsItem>(`/api/settings/blocks/${slug}`, {
      method: "PATCH",
      body: { auto_enabled: autoEnabled },
    }),

  // editorial voice (S.7.8b)
  getEditorialSettings: () =>
    request<EditorialSettingsResponse>("/api/settings/editorial"),
  setEditorialVoice: (voice: string) =>
    request<EditorialUpdateResponse>("/api/settings/editorial", {
      method: "PUT",
      body: { voice },
    }),

  // studio chat (S.8)
  // filter: "user" = manual chat, "system" = auto-generated
  // (movers), undefined = both interleaved.
  getStudioMessages: (filter?: "user" | "system", limit = 100) => {
    const qs = filter ? `&filter=${filter}` : "";
    return request<StudioMessageListResponse>(
      `/api/studio/messages?limit=${limit}${qs}`,
    );
  },
  getStudioMessageCounts: () =>
    request<{ user: number; system: number }>(
      "/api/studio/messages/counts",
    ),
  postStudioMessage: (body: StudioPostRequest) =>
    request<StudioMessageResponse>("/api/studio/message", {
      method: "POST",
      body,
    }),
  deleteStudioMessage: (id: string) =>
    request<StudioDeleteResponse>(`/api/studio/messages/${id}`, {
      method: "DELETE",
    }),

  // S.7.5a — onboarding
  getOnboardingState: () =>
    request<OnboardingState>("/api/onboarding/state"),
  acknowledgeOnboarding: () =>
    request<{ ok: boolean; completed: boolean }>(
      "/api/onboarding/acknowledge",
      { method: "POST" },
    ),
  selectBlocks: (blocks: string[]) =>
    request<{ ok: boolean; selected_blocks: string[] }>(
      "/api/onboarding/select-blocks",
      { method: "POST", body: { blocks } },
    ),

  getTradingHealth: () =>
    request<TradingHealthResponse>("/api/trading/health"),

  tradingPerformance: () =>
    request<TradingPerformanceResponse>("/api/trading/performance"),

  generatePerformanceChart: () =>
    request<{ url: string }>(
      "/api/trading/performance/chart",
      { method: "POST" },
    ),

  getAccountName: () =>
    request<{ account_name: string }>("/api/trading/account-name"),

  listGenerationRuns: (limit: number = 20, slug?: string) =>
    request<{ items: GenerationRun[] }>(
      `/api/trading/runs?limit=${limit}` +
        (slug ? `&slug=${encodeURIComponent(slug)}` : ""),
    ),

  setAccountName: (account_name: string) =>
    request<{ ok: boolean; account_name: string }>(
      "/api/trading/account-name",
      { method: "POST", body: { account_name } },
    ),

  // S.9.21 — per-subblock schedule.
  getSchedule: () =>
    request<{
      subblocks: Record<
        string,
        { enabled: boolean; interval_hours: number }
      >;
      next_fire: Record<string, string | null>;
    }>("/api/trading/schedule"),

  setSchedule: (
    subblocks: Record<
      string,
      { enabled: boolean; interval_hours: number }
    >,
  ) =>
    request<{
      ok: boolean;
      subblocks: Record<
        string,
        { enabled: boolean; interval_hours: number }
      >;
    }>("/api/trading/schedule", {
      method: "POST",
      body: { subblocks },
    }),

  listAvailableSubblocks: () =>
    request<
      Array<{
        slug: string;
        name: string;
        description: string | null;
        cadence: string;
        source_kind: string | null;
        ready: boolean;
      }>
    >("/api/trading/subblocks-available"),

  listDrafts: (limit: number = 100) =>
    request<DraftListResponse>(`/api/trading/drafts?limit=${limit}`),
  approveDraft: (postId: string) =>
    request<DraftActionResult>(`/api/trading/drafts/${postId}/approve`, { method: "POST" }),
  rejectDraft: (postId: string, reason: string = "") =>
    request<DraftActionResult>(
      `/api/trading/drafts/${postId}/reject?reason=${encodeURIComponent(reason)}`,
      { method: "POST" },
    ),

  listSymbols: (limit: number = 60) =>
    request<SymbolIndexResponse>(`/api/trading/symbols?limit=${limit}`),

  getSymbolTimeline: (ticker: string, limit: number = 50) =>
    request<SymbolTimelineResponse>(
      `/api/trading/symbols/${ticker}/posts?limit=${limit}`,
    ),
// credentials (BYOK, S.5.4b)
  listCredentials: () =>
    request<CredentialListResponse>("/api/settings/credentials"),
  addCredential: (input: CreateCredentialInput) =>
    request<Credential>("/api/settings/credentials", {
      method: "POST",
      body: input,
    }),
  toggleCredential: (id: string, is_active: boolean) =>
    request<Credential>(`/api/settings/credentials/${id}`, {
      method: "PATCH",
      body: { is_active },
    }),
  requestSupport: (message: string) =>
    request<{ status: string }>("/api/settings/credentials/support-request", {
      method: "POST",
      body: { message },
    }),

  // Paper Account (S.8.90)
  broker: {
    status: () => request<BrokerStatus>("/api/broker/status"),
    connect: (input: BrokerConnectInput) =>
      request<BrokerCredential>("/api/broker/connect", {
        method: "POST",
        body: input,
      }),
    disconnect: () =>
      request<{ removed: boolean }>("/api/broker/disconnect", {
        method: "DELETE",
      }),
    account: () => request<BrokerAccount>("/api/broker/account"),
    positions: () => request<BrokerPosition[]>("/api/broker/positions"),
    orders: (limit: number = 50) =>
      request<BrokerOrder[]>(`/api/broker/orders?limit=${limit}`),
    fills: (day?: string) =>
      request<BrokerFill[]>(
        day ? `/api/broker/fills?day=${day}` : "/api/broker/fills",
      ),
    settings: (monitor_enabled: boolean) =>
      request<BrokerStatus>("/api/broker/settings", {
        method: "PATCH",
        body: { monitor_enabled },
      }),
    placeOrder: (input: {
      symbol: string;
      side: "buy" | "sell";
      qty: number;
      type?: "market" | "limit";
      limit_price?: number;
      time_in_force?: string;
    }) =>
      request<BrokerOrder>("/api/broker/orders", {
        method: "POST",
        body: input,
      }),
  },
  traderProcess: {
    get: () => request<ProcessResponse>("/api/trader-process"),
    list: () =>
      request<SavedProcessListItem[]>("/api/trader-process/list"),
    getById: (id: string) =>
      request<ProcessResponse>(`/api/trader-process/by-id/${id}`),
    save: (input: ProcessDocument) =>
      request<ProcessResponse>("/api/trader-process", {
        method: "PUT",
        body: input,
      }),
    remove: () =>
      request<{ removed: boolean }>("/api/trader-process", {
        method: "DELETE",
      }),
    listDrafts: (status: "pending" | "accepted" = "pending") =>
      request<DraftListItem[]>(`/api/trader-process/drafts?status=${status}`),
    generate: () =>
      request<ProcessResponse>("/api/trader-process/generate", {
        method: "POST",
      }),
    listHistory: () =>
      request<SavedProcessListItem[]>("/api/trader-process/history"),
    getHistory: (id: string) =>
      request<ProcessResponse>(`/api/trader-process/history/${id}`),
    deleteHistory: (id: string) =>
      request<{ removed: boolean }>(`/api/trader-process/history/${id}`, {
        method: "DELETE",
      }),
    getDraft: (id: string) =>
      request<DraftResponse>(`/api/trader-process/drafts/${id}`),
    acceptDraft: (id: string) =>
      request<ProcessResponse>(`/api/trader-process/drafts/${id}/accept`, {
        method: "POST",
      }),
    deleteDraft: (id: string) =>
      request<{ removed: boolean }>(`/api/trader-process/drafts/${id}`, {
        method: "DELETE",
      }),
  },
  marketConditions: {
    get: () => request<MarketConditionsResponse>("/api/market-conditions"),
    list: () =>
      request<Array<{
        id: string;
        updated_at: string | null;
        preview: string;
      }>>("/api/market-conditions/list"),
    getById: (id: string) =>
      request<MarketConditionsResponse>(
        `/api/market-conditions/by-id/${id}`,
      ),
    remove: (id: string) =>
      request<{ removed: boolean }>(
        `/api/market-conditions/by-id/${id}`,
        { method: "DELETE" },
      ),
    save: (input: MarketConditions) =>
      request<MarketConditionsResponse>("/api/market-conditions", {
        method: "PUT",
        body: input,
      }),
    generate: () =>
      request<MarketConditionsResponse>("/api/market-conditions/generate", {
        method: "POST",
      }),
    listDrafts: () =>
      request<Array<{
        id: string;
        created_at: string;
        reviewed_at: string | null;
        preview: string;
      }>>("/api/market-conditions/drafts"),
    getDraft: (id: string) =>
      request<MarketConditionsResponse>(`/api/market-conditions/drafts/${id}`),
    acceptDraft: (id: string) =>
      request<MarketConditionsResponse>(
        `/api/market-conditions/drafts/${id}/accept`,
        { method: "POST" },
      ),
    deleteDraft: (id: string) =>
      request<{ removed: boolean }>(
        `/api/market-conditions/drafts/${id}`,
        { method: "DELETE" },
      ),
  },
  resetCredential: (id: string) =>
    request<Credential>(`/api/settings/credentials/${id}/reset`, {
      method: "POST",
    }),
  deleteCredential: (id: string) =>
    request<void>(`/api/settings/credentials/${id}`, { method: "DELETE" }),

  // S.7.6d.2 — trash (delete / restore / purge per kind)
  deleteTradingPost: (id: string) =>
    request<TrashActionResult>(`/api/trading/posts/${id}`, { method: "DELETE" }),
  restoreTradingPost: (id: string) =>
    request<TrashActionResult>(`/api/trading/posts/${id}/restore`, { method: "POST" }),
  purgeTradingPost: (id: string) =>
    request<TrashActionResult>(`/api/trading/posts/${id}/purge`, { method: "DELETE" }),

  deleteNewsPost: (id: string) =>
    request<TrashActionResult>(`/api/chains/posts/${id}`, { method: "DELETE" }),
  restoreNewsPost: (id: string) =>
    request<TrashActionResult>(`/api/chains/posts/${id}/restore`, { method: "POST" }),
  purgeNewsPost: (id: string) =>
    request<TrashActionResult>(`/api/chains/posts/${id}/purge`, { method: "DELETE" }),

  deleteBrief: (id: string) =>
    request<TrashActionResult>(`/api/briefs/${id}`, { method: "DELETE" }),
  restoreBrief: (id: string) =>
    request<TrashActionResult>(`/api/briefs/${id}/restore`, { method: "POST" }),
  purgeBrief: (id: string) =>
    request<TrashActionResult>(`/api/briefs/${id}/purge`, { method: "DELETE" }),

  listTrash: () => request<TrashListResponse>("/api/trash"),

  generateTradingPost: (slug: string) =>
    request<TradingGenerateResponse>(
      `/api/trading/subblocks/${slug}/generate`,
      { method: "POST" },
    ),

  // source reliability (Phase 10.9b)
  listSourceReliability: (windowDays: number = 30) =>
    request<SourceReliabilityList>(
      `/api/predictions/sources?window_days=${windowDays}`,
    ),

  // ai cost
  aiCostSummary: () => request<AiCostSummary>("/api/metrics/ai-cost"),

  // football predictions
  listUpcomingPredictions: (competition?: string, limit: number = 50) => {
    const q = new URLSearchParams();
    if (competition) q.set("competition", competition);
    q.set("limit", String(limit));
    return request<UpcomingPredictionsList>(`/api/predictions/upcoming?${q.toString()}`);
  },
  performanceSummary: () => request<PerformanceSummary>("/api/predictions/performance"),

  // health
  fullHealth: () => request<HealthStatus>("/api/health/full", { auth: false }),

  // daily briefs
  listBriefs: (limit: number = 30) =>
    request<DailyBriefListResponse>(`/api/briefs?limit=${limit}`),
  getBrief: (id: string) => request<DailyBriefResponse>(`/api/briefs/${id}`),
  generateBrief: (windowHours: number = 24) =>
    request<{ task_id: string; status: string }>(
      `/api/briefs/generate?window_hours=${windowHours}&force=true`,
      { method: "POST" },
    ),

  // follows
  followChain: (chainId: string) =>
    request<FollowStatus>(`/api/chains/${chainId}/follow`, { method: "POST" }),
  unfollowChain: (chainId: string) =>
    request<FollowStatus>(`/api/chains/${chainId}/follow`, { method: "DELETE" }),
  getFollowStatus: (chainId: string) =>
    request<FollowStatus>(`/api/chains/${chainId}/follow`),

  // notifications
  listNotifications: (unreadOnly: boolean = false) =>
    request<NotificationsListResponse>(
      `/api/notifications?limit=50${unreadOnly ? "&unread_only=true" : ""}`,
    ),
  markNotificationRead: (id: string) =>
    request<NotificationResponse>(`/api/notifications/${id}/read`, { method: "POST" }),
  markAllNotificationsRead: () =>
    request<{ marked_read: number }>("/api/notifications/read-all", { method: "POST" }),

  // blocks
  listBlocks: () => request<BlockResponse[]>("/api/blocks"),

  // stock news fetcher
  fetchStockNews: (opts: {
    tickers?: string[] | null;
    autoBuild?: boolean;
    subtopicSlug?: string;
    maxEvents?: number;
  }) => request<StockNewsFetchResponse>("/api/sources/fetch-stock-news", {
    method: "POST",
    body: {
      tickers: opts.tickers ?? null,
      auto_build: opts.autoBuild ?? false,
      subtopic_slug: opts.subtopicSlug ?? "us_stocks",
      max_events: opts.maxEvents ?? 30,
    },
  }),
  getBlock: (slug: string) => request<BlockResponse>(`/api/blocks/${slug}`),

  // chains
  listChains: (limit: number = 50) =>
    request<NewsChainResponse[]>(`/api/chains?limit=${limit}`),
  getChain: (id: string) => request<NewsChainDetail>(`/api/chains/${id}`),
  buildChainFromContent: (
    content: string,
    opts: { blockSlug: string; subtopicSlug?: string | null; targetWords?: number } = { blockSlug: "news" },
  ) =>
    request<ChainBuildResponse>("/api/chains/build", {
      method: "POST",
      body: {
        content,
        block_slug: opts.blockSlug,
        subtopic_slug: opts.subtopicSlug ?? null,
        target_words: opts.targetWords ?? null,
      },
    }),
  getChainBuildStatus: (taskId: string) =>
    request<ChainBuildStatusResponse>(`/api/chains/build/${taskId}`),
  _oldBuildChain: (events: any[], opts: { blockSlug: string; subtopicSlug?: string | null; targetWords?: number } = { blockSlug: "news" }) =>
    request<ChainBuildResponse>("/api/chains/build", {
      method: "POST",
      body: {
        events,
        block_slug: opts.blockSlug,
        subtopic_slug: opts.subtopicSlug ?? null,
        target_words: opts.targetWords,
      },
    }),
};

// ---------- Phase 2: enrichment, entities, topics ----------

export type EnrichmentResponse = {
  id: string;
  normalized_record_id: string;
  sentiment_label: string | null;
  sentiment_score: number | null;
  sentiment_confidence: number | null;
  emotions: Record<string, number>;
  intent: string | null;
  intent_confidence: number | null;
  raw_topics: string[];
  raw_entities: Array<{ text: string; type: string; confidence: number }>;
  pain_point_signals: string[];
  feature_request_signals: string[];
  created_at: string;
};

export type EntityResponse = {
  id: string;
  canonical_name: string;
  type: string;
  mention_count: number;
  created_at: string;
};

export type EntityDetail = {
  id: string;
  canonical_name: string;
  type: string;
  mention_count: number;
  avg_sentiment: number | null;
  sample_mentions: Array<{
    surface_form: string;
    confidence: number;
    sentiment_score: number | null;
    record_id: string;
  }>;
  created_at: string;
};

export type TopicResponse = {
  id: string;
  name: string;
  record_count: number;
  created_at: string;
};


// ---------- Search ----------

export type SearchHit = {
  record_id: string;
  normalized_text: string;
  detected_language: string;
  similarity: number;
  created_at: string;
};

export type SemanticSearchResponse = {
  query: string;
  hits: SearchHit[];
  total: number;
};


// ---------- Clusters ----------

export type ClusterResponse = {
  id: string;
  title: string;
  summary: string | null;
  volume: number;
  sentiment_avg: number | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  created_at: string;
};

export type ClusterDetail = {
  id: string;
  title: string;
  summary: string | null;
  volume: number;
  sentiment_avg: number | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  sample_records: Array<{ record_id: string; normalized_text: string }>;
  created_at: string;
};


// ---------- Pain points ----------

export type PainPointResponse = {
  id: string;
  title: string;
  description: string | null;
  severity: number | null;
  first_seen_at: string;
  last_seen_at: string;
};

export type PainPointDetail = PainPointResponse & {
  sample_records: Array<{ record_id: string; normalized_text: string }>;
};


// ---------- Dashboard ----------

export type DashboardSummary = {
  counts: {
    records: number;
    enriched: number;
    entities: number;
    topics: number;
    clusters: number;
    pain_points: number;
    sources: number;
  };
  sentiment: Array<{ label: string; count: number }>;
  top_entities: Array<{ name: string; type: string; mentions: number }>;
  top_topics: Array<{ name: string; records: number }>;
  top_clusters: Array<{ id: string; title: string; volume: number; sentiment_avg: number | null }>;
  top_pain_points: Array<{ id: string; title: string; severity: number | null }>;
  recent_jobs: Array<{ id: string; status: string; processed: number; created_at: string | null }>;
};


// ---------- News chains ----------

export type ChainBuildStatusResponse = {
  task_id: string;
  status: "pending" | "started" | "success" | "failure";
  chain_id?: string | null;
  post_id?: string | null;
  verification_status?: string | null;
  error?: string | null;
  reason?: string | null;
  // S.6.2.3 — structured, user-facing
  error_kind?: string | null;
  error_message?: string | null;
  error_action?: string | null;
  error_retry_after_seconds?: number | null;
};

export type ChainBuildResponse = {
  chain_id: string;
  status: string;
  event_count: number;
};

export type NewsEventResponse = {
  id: string;
  headline: string;
  summary: string;
  occurred_at: string | null;
  reported_at: string | null;
  location: string | null;
  actors: string[];
  event_type: string | null;
  status: string;
  confidence: number;
  created_at: string;
};

export type ChainLinkResponse = {
  from_event_id: string;
  to_event_id: string;
  link_type: string;
  rationale: string;
  confidence: number;
  sequence: number;
};

export type PostEvidenceResponse = {
  claim_text: string;
  claim_type: string;
  source_kind: string;
  source_id: string | null;
  confidence: number | null;
};

export type GeneratedPostResponse = {
  id: string;
  chain_id: string;
  title: string;
  body: string;
  format: string;
  tone: string;
  word_count: number | null;
  status: string;
  created_at: string;
};

export type NewsChainResponse = {
  id: string;
  title: string;
  thesis: string | null;
  status: string;
  primary_entities: string[];
  primary_topics: string[];
  confidence: number | null;
  block_id?: string | null;
  subtopic_slug?: string | null;
  update_count?: number;
  last_updated_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type ImpactResponse = {
  id: string;
  label: string;
  category: string;
  direction: string | null;
  strength: string | null;
  reasoning: string | null;
  position: number;
};

export type QuantifiedScenario = {
  primary_symbol: string | null;
  current_price: number | null;
  vol_30d_pct: number | null;
  expected_move_low_pct: number | null;
  expected_move_high_pct: number | null;
  confidence: string;
  basis: string;
  notes: string[];
};

export type ScenarioResponse = {
  id: string;
  name: string;
  condition: string;
  reasoning: string;
  position: number;
  candidate_symbols: string[];
  quantified: Partial<QuantifiedScenario>;
};

export type LiveQuote = {
  symbol?: string | null;
  current_price?: number | null;
  daily_change_abs?: number | null;
  daily_change_pct?: number | null;
  vol_30d_pct?: number | null;
  as_of?: string | null;
};

export type WatchlistItemResponse = {
  id: string;
  label: string;
  symbol: string | null;
  reasoning: string | null;
  position: number;
  live_quote: LiveQuote;
};

export type ChainUpdateResponse = {
  id: string;
  event_id: string;
  event_headline: string;
  event_summary: string;
  similarity: number;
  reasoning: string | null;
  attached_by: string;
  created_at: string;
};

export type NewsChainDetail = {
  chain: NewsChainResponse & { update_count?: number; last_updated_at?: string | null };
  events: NewsEventResponse[];
  links: ChainLinkResponse[];
  post: GeneratedPostResponse | null;
  evidence: PostEvidenceResponse[];
  impacts: ImpactResponse[];
  scenarios: ScenarioResponse[];
  watchlist: WatchlistItemResponse[];
  updates: ChainUpdateResponse[];
};


// ---------- Blocks ----------

export type Subtopic = {
  slug: string;
  name: string;
  description?: string | null;
};

export type BlockResponse = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  style_guide: string | null;
  config: Record<string, unknown>;
  subtopics: Subtopic[];
  is_system: boolean;
};


// ---------- Stock news fetch ----------

export type StockNewsFetchResponse = {
  task_id: string;
  status: string;
  tickers: string[] | null;
  auto_build: boolean;
};


// ---------- Notifications & follows ----------

export type FollowStatus = {
  chain_id: string;
  following: boolean;
};

export type NotificationResponse = {
  id: string;
  kind: string;
  chain_id: string | null;
  event_id: string | null;
  title: string;
  body: string | null;
  read_at: string | null;
  created_at: string;
};

export type NotificationsListResponse = {
  items: NotificationResponse[];
  total: number;
  unread: number;
};


// ---------- AI cost ----------

export type AiCostSummary = {
  date: string;
  cost_usd: number;
  cost_cap_usd: number;
  cost_pct: number;
  tokens_total: number;
  token_cap: number;
  token_pct: number;
  calls: number;
  enabled: boolean;
  alert_at_percent: number;
};


// ---------- Daily briefs ----------

export type DailyBriefResponse = {
  id: string;
  brief_date: string;
  title: string;
  body: string;
  word_count: number | null;
  status: string;
  block_slug: string;
  created_at: string;
};

export type DailyBriefListResponse = {
  items: DailyBriefResponse[];
  total: number;
};


// ---------- Health ----------

export type HealthComponent = {
  status: string;
  latency_ms?: number;
  error?: string;
  [key: string]: unknown;
};

export type HealthStatus = {
  status: "healthy" | "degraded" | "down";
  checked_at: string;
  components: {
    postgres: HealthComponent;
    redis: HealthComponent;
    celery: HealthComponent;
    providers: Record<string, HealthComponent>;
  };
  activity_1h: {
    total_calls_1h?: number;
    ok_calls_1h?: number;
    error_calls_1h?: number;
    success_rate?: number | null;
    avg_latency_ms?: number | null;
    error?: string;
  };
};


// ---------- Football predictions ----------

export type ModelProbs = {
  home: number | null;
  draw: number | null;
  away: number | null;
};

export type MarketProbs = ModelProbs & {
  bookmakers: number | null;
};

export type PredictionResponse = {
  id: string;
  match_id: string;
  home_team: string;
  away_team: string;
  competition: string;
  kickoff_at: string;
  match_status: string;
  prob_home_win: number;
  prob_draw: number;
  prob_away_win: number;
  expected_home_goals: number | null;
  expected_away_goals: number | null;
  prob_over_2_5: number;
  prob_under_2_5: number;
  // Extended markets (Phase 10.9a)
  prob_over_1_5: number | null;
  prob_under_1_5: number | null;
  prob_over_3_5: number | null;
  prob_under_3_5: number | null;
  prob_btts_yes: number | null;
  prob_btts_no: number | null;
  home_goals_dist: number[] | null;
  away_goals_dist: number[] | null;
  top_scorelines: Array<[number, number, number]> | null;
  // Corners (may be low-confidence; see corners_confidence)
  expected_home_corners: number | null;
  expected_away_corners: number | null;
  expected_total_corners: number | null;
  corners_confidence: string | null;
  confidence: string;
  data_quality: number | null;
  fair_odds_home: number | null;
  fair_odds_draw: number | null;
  fair_odds_away: number | null;
  model_version: string;
  published_at: string | null;
  blend_mode: string | null;
  model_probs: ModelProbs | null;
  market_probs: MarketProbs | null;
  actual_home_goals: number | null;
  actual_away_goals: number | null;
  result_correct: boolean | null;
  brier_score: number | null;
};

// S.7.5a — onboarding
export type BlockOption = {
  slug: string;
  name: string;
  description: string | null;
};

export type OnboardingState = {
  guide_seen: boolean;
  completed: boolean;
  selected_blocks: string[];
  available_blocks: BlockOption[];
};

export type UpcomingPredictionsList = {
  items: PredictionResponse[];
  total: number;
};

export type PerformanceWindow = {
  scored: number;
  result_accuracy: number | null;
  avg_brier_score: number | null;
};

export type PerformanceSummary = {
  model_version: string;
  predictions_total: number;
  predictions_scored: number;
  result_accuracy: number | null;
  over_under_accuracy: number | null;
  avg_brier_score: number | null;
  avg_log_loss: number | null;
  baseline_random: number;
  baseline_always_home: number | null;
  last_7_days: PerformanceWindow | null;
  last_30_days: PerformanceWindow | null;
};


// ─────────────────────────────────────────────────────────────────────
// Paper Account — broker credentials (S.8.90)
// ─────────────────────────────────────────────────────────────────────

export type BrokerStatus = {
  connected: boolean;
  broker: string | null;
  label: string | null;
  paper: boolean | null;
  monitor_enabled: boolean | null;
  key_preview: string | null;
  last_sync_at: string | null;
  last_sync_error: string | null;
};

export type BrokerCredential = {
  id: string;
  broker: string;
  label: string;
  paper: boolean;
  monitor_enabled: boolean;
  key_preview: string;
  last_sync_at: string | null;
  last_sync_error: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type BrokerConnectInput = {
  broker: string;
  label: string;
  api_key: string;
  api_secret: string;
  paper: boolean;
};

export type BrokerAccount = {
  equity: number;
  buying_power: number;
  cash: number;
  portfolio_value: number;
  currency: string;
  status: string;
  pattern_day_trader: boolean;
};

export type BrokerPosition = {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
};

export type BrokerOrder = {
  id: string;
  symbol: string;
  side: string;
  qty: string;
  filled_qty: string;
  type: string;
  status: string;
  submitted_at: string | null;
  filled_avg_price: number | null;
};

export type BrokerFill = {
  id: string;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  transaction_time: string | null;
};

// S.8.97 — trader process (one document per org, drafts as documents)
export type ProcessDocument = {
  daily_scan: string;
  entry_logic: string;
  position_sizing: string;
  exit_logic: string;
  holding_period: string;
  research_to_rules: string;
  chart_overlay: string;
  no_backtest_rule: string;
  multi_strategy_blend: string;
  rolling: string;
  stocks_vs_options: string;
  universes: string;
  emotion_removal: string;
  fundamental_metrics: string;
  trade_history_summary: string;
};

export type ProcessResponse = ProcessDocument & {
  updated_at?: string | null;
};

export type MarketConditions = {
  what_i_track: string;
  baselines: string;
  regimes: string;
  thresholds: string;
  adjustments: string;
  mechanisms: string;
};

export type MarketConditionsResponse = MarketConditions & {
  updated_at?: string | null;
};

export type DraftListItem = {
  id: string;
  created_at: string;
  reviewed_at: string | null;
  preview: string;
};

export type DraftResponse = ProcessDocument & {
  id: string;
  created_at: string;
  reviewed_at: string | null;
};

// S.8.97 — saved process list
export type SavedProcessListItem = {
  id: string;
  updated_at: string | null;
  preview: string;
};
