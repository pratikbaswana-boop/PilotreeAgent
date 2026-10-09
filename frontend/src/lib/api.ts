import createClient from "openapi-fetch";
import type { paths, components } from "./schema";
import { accessToken } from "./auth";
export const API = import.meta.env.VITE_API_URL || "/api";
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(
      typeof detail === "string"
        ? detail
        : status === 409
          ? "Someone else updated this enquiry. Reload or merge your changes."
          : `Request failed (${status})`,
    );
  }
}
export async function apiFetch(input: RequestInfo | URL, init?: RequestInit) {
  const token = await accessToken();
  const request = new Request(
    input instanceof Request ? input : new URL(String(input), location.origin),
    init,
  );
  if (token) request.headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(request);
  if (response.status === 401 && location.pathname !== "/login") {
    sessionStorage.setItem("returnTo", location.pathname + location.search);
    location.assign("/login");
  }
  return response;
}
export const client = createClient<paths>({ baseUrl: API, fetch: apiFetch });
export async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
  headers?: Record<string, string>,
): Promise<T> {
  const response = await apiFetch(API + path, {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new ApiError(response.status, data.detail);
  return data as T;
}
export type Enquiry = components["schemas"]["EnquiryOut"];
export type Analysis = components["schemas"]["AnalysisStatus"];
export type User = {
  id: string;
  display_name: string;
  email: string;
  role: "viewer" | "reviewer" | "admin";
};
export type Action = {
  id: string;
  destination: string;
  status: string;
  attempts: number;
  external_id: string | null;
  created_at: string;
};
export type Tool = {
  key: string;
  label: string;
  enabled: boolean;
  suggested_for: string[];
};
export async function getEnquiries(query: Record<string, string>) {
  const { data, error, response } = await client.GET("/enquiries", {
    params: { query },
  });
  if (!data) throw new ApiError(response.status, error);
  return data;
}
