export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, detail: string) {
    super(status ? `请求失败 (${status})：${detail}` : `网络错误：${detail}`);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface Connection {
  base_url: string;
  bearer: string;
  generation: string;
  workspace_id?: string;
  state?: string;
}

export async function request<T>(connection: Connection, path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${connection.base_url}${path}`, {
      ...init,
      signal: init?.signal ?? AbortSignal.timeout(120000),
      headers: {
        Authorization: `Bearer ${connection.bearer}`,
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch (error) {
    throw new ApiError(0, error instanceof Error ? error.message : "无法连接 Core");
  }
  if (!response.ok) {
    let detail = response.statusText || "Core 未接受请求";
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body) {
        const value = (body as { detail: unknown }).detail;
        detail = typeof value === "string" ? value : JSON.stringify(value);
      }
    } catch {
      // The status remains meaningful even when the server returned no JSON.
    }
    throw new ApiError(response.status, detail);
  }
  try {
    return (await response.json()) as T;
  } catch {
    // Headers are not a receipt. A committed write can lose its response body.
    throw new ApiError(0, "Core 响应不完整或无法读取");
  }
}

export async function browserConnection(baseUrl: string, secret: string): Promise<Connection> {
  if (!/^[\da-f]{64}$/i.test(secret)) throw new Error("请输入 64 位十六进制 Core 密钥。");
  const health = await fetch(`${baseUrl}/health`);
  if (!health.ok) throw new Error("无法读取 Core 状态。");
  const { session } = (await health.json()) as { session: string };
  const bytes = Uint8Array.from(secret.match(/../g)!, (part) => parseInt(part, 16));
  const key = await crypto.subtle.importKey("raw", bytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  bytes.fill(0);
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(session));
  const bearer = Array.from(new Uint8Array(signature), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const connection = { base_url: baseUrl, bearer, generation: session };
  await request(connection, "/api/conversation?limit=1");
  return connection;
}
