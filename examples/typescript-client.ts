/**
 * Minimal typed client for the public Team Assets REST API.
 * No framework, Python imports, cookies, or user authentication are required.
 */
export type Project = {
  id: string;
  name: string;
  description: string;
  version: number;
};

export type Asset = {
  id: string;
  project_id: string;
  name: string;
  category: string;
  role: string;
  version: number;
};

export type Material = {
  id: string;
  asset_id: string;
  variant_id: string | null;
  file_id: string;
  name: string;
  mime?: string;
  url: string;
  thumbnail_url: string;
};

export type Page<T> = {
  total: number;
  limit: number;
  offset: number;
} & Record<string, T[] | number>;

export type UploadSession = {
  id: string;
  filename: string;
  size: number;
  offset: number;
  upload_token: string;
  upload_url: string;
  chunk_size: number;
};

export type UploadStatus = {
  id: string;
  filename: string;
  size: number;
  offset: number;
  status: string;
  file_id: string | null;
  expires_at: number;
  expired?: boolean;
};

export class TeamAssetsApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: unknown,
    message: string,
  ) {
    super(message);
    this.name = "TeamAssetsApiError";
  }

  get current(): unknown {
    return typeof this.detail === "object" && this.detail !== null && "current" in this.detail
      ? (this.detail as { current?: unknown }).current
      : undefined;
  }
}

function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export class TeamAssetsClient {
  private readonly apiRoot: string;
  private readonly serverRoot: string;

  constructor(serverOrigin: string) {
    this.serverRoot = serverOrigin.replace(/\/+$/, "");
    this.apiRoot = `${this.serverRoot}/api/v1`;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const response = await fetch(`${this.apiRoot}${path}`, {
      ...init,
      headers,
      credentials: "omit",
    });
    if (!response.ok) {
      let body: { detail?: unknown } | undefined;
      try {
        body = await response.json();
      } catch {
        body = undefined;
      }
      const detail = body?.detail ?? body;
      const message = typeof detail === "string"
        ? detail
        : typeof detail === "object" && detail !== null && "message" in detail
          ? String((detail as { message: unknown }).message)
          : response.statusText || `HTTP ${response.status}`;
      throw new TeamAssetsApiError(response.status, detail, message);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  listProjects(limit = 50, offset = 0): Promise<{ projects: Project[]; total: number; limit: number; offset: number }> {
    return this.request(`/projects?limit=${limit}&offset=${offset}`);
  }

  listAssets(projectId: string, limit = 50, offset = 0): Promise<{ assets: Asset[]; total: number; limit: number; offset: number }> {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return this.request(`/projects/${encodeURIComponent(projectId)}/assets?${query}`);
  }

  getAsset(assetId: string): Promise<Asset & { variants: unknown[] }> {
    return this.request(`/assets/${encodeURIComponent(assetId)}`);
  }

  createAsset(input: Pick<Asset, "project_id" | "name" | "category" | "role">): Promise<Asset> {
    return this.request("/assets", { method: "POST", body: JSON.stringify(input) });
  }

  async updateAsset(
    assetId: string,
    changes: Pick<Partial<Asset>, "name" | "category" | "role">,
    version: number,
  ): Promise<Asset> {
    return this.request(`/assets/${encodeURIComponent(assetId)}`, {
      method: "PATCH",
      body: JSON.stringify({ ...changes, version }),
    });
  }

  listMaterials(assetId: string, limit = 50, offset = 0): Promise<{ materials: Material[]; total: number }> {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return this.request(`/assets/${encodeURIComponent(assetId)}/materials?${query}`);
  }

  async uploadAssetImage(assetId: string, file: File, idempotencyKey = newIdempotencyKey()): Promise<{ materials: Material[] }> {
    const form = new FormData();
    form.append("files", file, file.name);
    return this.request(`/assets/${encodeURIComponent(assetId)}/materials`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: form,
    });
  }

  beginUpload(
    filename: string,
    size: number,
    options: { sha256?: string; idempotencyKey?: string } = {},
  ): Promise<UploadSession> {
    return this.request("/uploads", {
      method: "POST",
      body: JSON.stringify({
        filename,
        size,
        sha256: options.sha256,
        idempotency_key: options.idempotencyKey ?? newIdempotencyKey(),
      }),
    });
  }

  getUploadStatus(session: UploadSession): Promise<UploadStatus> {
    return this.request(`/uploads/${encodeURIComponent(session.id)}`, {
      headers: { "X-Upload-Token": session.upload_token },
    });
  }

  async uploadChunk(session: UploadSession, offset: number, bytes: Blob): Promise<UploadStatus> {
    const url = new URL(session.upload_url, this.serverRoot);
    url.searchParams.set("offset", String(offset));
    const response = await fetch(url, {
      method: "PUT",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Upload-Token": session.upload_token,
      },
      body: bytes,
      credentials: "omit",
    });
    if (!response.ok) {
      let detail: unknown;
      try {
        detail = (await response.json())?.detail;
      } catch {
        detail = undefined;
      }
      throw new TeamAssetsApiError(response.status, detail, `上传分块失败：HTTP ${response.status}`);
    }
    return response.json() as Promise<UploadStatus>;
  }

  async uploadFileResumable(file: File, idempotencyKey = newIdempotencyKey()): Promise<UploadStatus> {
    const session = await this.beginUpload(file.name, file.size, { idempotencyKey });
    let offset = session.offset;
    while (offset < file.size) {
      const end = Math.min(offset + session.chunk_size, file.size);
      try {
        const progress = await this.uploadChunk(session, offset, file.slice(offset, end));
        offset = progress.offset;
      } catch (error) {
        if (!(error instanceof TeamAssetsApiError) || error.status !== 409) throw error;
        // The accepted offset is authoritative after a retry or concurrent chunk.
        offset = (await this.getUploadStatus(session)).offset;
      }
    }
    return this.request(`/uploads/${encodeURIComponent(session.id)}/complete`, {
      method: "POST",
      headers: { "X-Upload-Token": session.upload_token },
    });
  }

  commitIntake(input: {
    project_id: string;
    idempotency_key: string;
    asset_id?: string;
    asset?: { name: string; category?: string; role?: string };
    variants: Array<{
      variant_id?: string;
      name?: string;
      episodes?: string;
      description?: string;
      episode_usages?: Array<{ episodes: string; mode: string; note?: string }>;
      file_ids?: string[];
      material_names?: Record<string, string>;
    }>;
  }): Promise<unknown> {
    return this.request("/intake/commit", { method: "POST", body: JSON.stringify(input) });
  }

  watchRevisions(onRevision: (revision: number) => void, onError?: (event: Event) => void): EventSource {
    const source = new EventSource(`${this.apiRoot}/events`);
    source.onmessage = event => {
      try {
        const payload = JSON.parse(event.data) as { revision?: number };
        if (typeof payload.revision === "number") onRevision(payload.revision);
      } catch {
        // Ignore malformed events; a later refresh can restore the view.
      }
    };
    if (onError) source.onerror = onError;
    return source;
  }
}
