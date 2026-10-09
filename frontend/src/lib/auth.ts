import { UserManager, WebStorageStateStore } from "oidc-client-ts";
const authority = import.meta.env.VITE_OIDC_AUTHORITY;
export const auth = authority
  ? new UserManager({
      authority,
      client_id: import.meta.env.VITE_OIDC_CLIENT_ID,
      redirect_uri: `${location.origin}/auth/callback`,
      silent_redirect_uri: `${location.origin}/auth/callback`,
      post_logout_redirect_uri: `${location.origin}/login`,
      response_type: "code",
      scope: import.meta.env.VITE_OIDC_SCOPE || "openid profile email",
      automaticSilentRenew: true,
      userStore: new WebStorageStateStore({ store: sessionStorage }),
    })
  : null;
export async function accessToken(refresh = false): Promise<string | null> {
  if (localDevelopmentAuth)
    return sessionStorage.getItem("pilotree.local.token");
  if (!auth) return null;
  let user = await auth.getUser();
  if (user && (refresh || user.expired || (user.expires_in ?? 0) < 30)) {
    user = await auth.signinSilent();
  }
  return user?.access_token ?? null;
}
export function login() {
  if (auth)
    return auth.signinRedirect({
      state: { returnTo: sessionStorage.getItem("returnTo") || "/enquiries" },
    });
}
export async function logout() {
  if (localDevelopmentAuth) {
    sessionStorage.removeItem("pilotree.local.token");
    location.assign("/login");
    return;
  }
  if (auth) await auth.signoutRedirect();
}

export const localDevelopmentAuth =
  import.meta.env.DEV &&
  import.meta.env.VITE_LOCAL_DEVELOPMENT_AUTH === "true" &&
  ["127.0.0.1", "localhost"].includes(location.hostname);
export const demoAuth = import.meta.env.VITE_DEMO_AUTH === "true";
export async function localLogin(accessCode = "") {
  const response = await fetch(`${import.meta.env.VITE_API_URL || "/api"}/auth/local-session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_code: accessCode }),
  });
  if (!response.ok)
    throw new Error(
      "Local sign-in is unavailable. Check the API configuration.",
    );
  const session = await response.json();
  sessionStorage.setItem("pilotree.local.token", session.access_token);
  const target = sessionStorage.getItem("returnTo") || "/enquiries";
  location.assign(
    target.startsWith("/") && !target.startsWith("//") ? target : "/enquiries",
  );
}
