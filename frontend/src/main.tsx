import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
  Outlet,
  redirect,
} from "@tanstack/react-router";
import {
  AppShell,
  Login,
  AuthCallback,
  EnquiriesPage,
  OutboxPage,
  SettingsPage,
} from "./App";
import "./index.css";
const root = createRootRoute({
  component: Outlet,
  notFoundComponent: () => (
    <main className="session-screen">
      <h1>Page not found</h1>
      <a href="/enquiries">Return to enquiries</a>
    </main>
  ),
});
const loginRoute = createRoute({
  getParentRoute: () => root,
  path: "/login",
  component: Login,
});
const callback = createRoute({
  getParentRoute: () => root,
  path: "/auth/callback",
  component: AuthCallback,
});
const shell = createRoute({
  getParentRoute: () => root,
  id: "workspace",
  component: AppShell,
});
const validateSearch = (search: Record<string, unknown>) =>
  Object.fromEntries(
    Object.entries(search).filter(
      ([k, v]) =>
        [
          "q",
          "status",
          "priority",
          "category",
          "safety_verdict",
          "sort",
          "cursor",
        ].includes(k) && typeof v === "string",
    ),
  ) as Record<string, string>;
const index = createRoute({
  getParentRoute: () => shell,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/enquiries" });
  },
});
const enquiries = createRoute({
  getParentRoute: () => shell,
  path: "/enquiries",
  validateSearch,
  component: () => <EnquiriesPage />,
});
const detail = createRoute({
  getParentRoute: () => shell,
  path: "/enquiries/$id",
  validateSearch,
  component: () => <EnquiriesPage />,
});
const quarantine = createRoute({
  getParentRoute: () => shell,
  path: "/quarantine",
  validateSearch,
  component: () => <EnquiriesPage quarantine />,
});
const outbox = createRoute({
  getParentRoute: () => shell,
  path: "/outbox",
  component: OutboxPage,
});
const settings = createRoute({
  getParentRoute: () => shell,
  path: "/settings",
  component: SettingsPage,
});
const router = createRouter({
  routeTree: root.addChildren([
    loginRoute,
    callback,
    shell.addChildren([index, enquiries, detail, quarantine, outbox, settings]),
  ]),
});
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 10000, retry: 1, refetchOnWindowFocus: true },
  },
});
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
