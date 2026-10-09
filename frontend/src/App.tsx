import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
  useSearch,
} from "@tanstack/react-router";
import {
  useReactTable,
  getCoreRowModel,
  type ColumnDef,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  MagnifyingGlassIcon,
  EnvelopeClosedIcon,
  ExclamationTriangleIcon,
  PaperPlaneIcon,
  GearIcon,
  SunIcon,
  MoonIcon,
  ExitIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
} from "@radix-ui/react-icons";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { NewEnquiry } from "@/components/NewEnquiry";
import { DetailPanel } from "@/components/DetailPanel";
import { getEnquiries, request, type Enquiry, type User } from "@/lib/api";
import { copy, label, categories, priorities } from "@/lib/copy";
import {
  auth,
  login,
  logout,
  localDevelopmentAuth,
  demoAuth,
  localLogin,
} from "@/lib/auth";
import { useEvents } from "@/lib/events";
const UserContext = createContext<User | null>(null);
export function useUser() {
  return useContext(UserContext)!;
}
export function AppShell() {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => request<User>("/me"),
    retry: false,
  });
  const [dark, setDark] = useState(
    () => localStorage.getItem("theme") === "dark",
  );
  const [collapsed, setCollapsed] = useState(false),
    [command, setCommand] = useState(false),
    [term, setTerm] = useState("");
  const [online, setOnline] = useState(navigator.onLine);
  const nav = useNavigate(),
    location = useLocation();
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "k") {
        event.preventDefault();
        setCommand((c) => !c);
      }
    };
    const status = () => setOnline(navigator.onLine);
    window.addEventListener("keydown", key);
    window.addEventListener("online", status);
    window.addEventListener("offline", status);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("online", status);
      window.removeEventListener("offline", status);
    };
  }, []);
  if (me.isPending)
    return (
      <main className="session-screen">
        <Skeleton className="h-10 w-56" />
        <p>Opening your workspace…</p>
      </main>
    );
  if (me.isError)
    return (
      <main className="session-screen">
        <h1>Workspace unavailable</h1>
        <p>{me.error.message}</p>
        <Button onClick={() => me.refetch()}>Try again</Button>
        <Link to="/login">Sign in</Link>
      </main>
    );
  if (!me.data) return null;
  const items = [
    { path: "/enquiries", text: copy.enquiries, icon: EnvelopeClosedIcon },
    {
      path: "/quarantine",
      text: copy.quarantine,
      icon: ExclamationTriangleIcon,
    },
    { path: "/outbox", text: copy.outbox, icon: PaperPlaneIcon },
    ...(me.data.role === "admin"
      ? [{ path: "/settings", text: copy.settings, icon: GearIcon }]
      : []),
  ];
  return (
    <UserContext.Provider value={me.data}>
      <div className={`app-shell ${collapsed ? "collapsed" : ""}`}>
        <aside className="sidebar">
          <Link to="/enquiries" className="brand">
            <span className="brand-mark">p</span>
            <span>
              Pilotree<span className="brand-sub">OPERATIONS</span>
            </span>
          </Link>
          <div className="workspace-label">WORKSPACE</div>
          <nav aria-label="Main navigation">
            {items.map((item) => (
              <Link
                to={item.path}
                key={item.path}
                className={
                  location.pathname.startsWith(item.path)
                    ? "nav-item selected"
                    : "nav-item"
                }
              >
                <item.icon />
                <span>{item.text}</span>
              </Link>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <div className="sidebar-note">
              <span className="live-dot" /> Human decisions.
              <br />
              AI assistance.
            </div>
            <button
              className="collapse-button"
              aria-label="Collapse navigation"
              onClick={() => setCollapsed(!collapsed)}
            >
              {collapsed ? <ChevronRightIcon /> : <ChevronLeftIcon />}
              <span>Collapse</span>
            </button>
          </div>
        </aside>
        <div className="main-shell">
          <header className="topbar">
            <span className="breadcrumb">
              Operations <ChevronRightIcon />{" "}
              <strong>
                {location.pathname.startsWith("/quarantine")
                  ? "Quarantine"
                  : location.pathname.startsWith("/outbox")
                    ? "Outbox"
                    : location.pathname.startsWith("/settings")
                      ? "Settings"
                      : "Enquiries"}
              </strong>
            </span>
            <div className="topbar-actions">
              <button
                className="command-trigger"
                onClick={() => setCommand(true)}
              >
                <MagnifyingGlassIcon />
                <span>Search workspace</span>
                <kbd>⌘ K</kbd>
              </button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Toggle theme"
                onClick={() => setDark(!dark)}
              >
                {dark ? <SunIcon /> : <MoonIcon />}
              </Button>
              <span className="avatar" title={me.data.email}>
                {me.data.display_name.slice(0, 2).toUpperCase()}
              </span>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Sign out"
                onClick={() => logout()}
              >
                <ExitIcon />
              </Button>
            </div>
          </header>
          {!online && (
            <div className="offline-banner" role="status">
              {copy.offline}
            </div>
          )}
          <Outlet />
        </div>
      </div>
      <Dialog open={command} onOpenChange={setCommand}>
        <DialogContent className="triage-dialog">
          <DialogHeader>
            <DialogTitle>Search workspace</DialogTitle>
            <DialogDescription>
              Find an enquiry by name, company, or message.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setCommand(false);
              void nav({ to: "/enquiries", search: { q: term } });
            }}
          >
            <Input
              aria-label="Search workspace"
              autoFocus
              value={term}
              onChange={(e) => setTerm(e.target.value)}
            />
            <Button className="mt-4" type="submit">
              Search enquiries
            </Button>
          </form>
          <Button
            variant="outline"
            onClick={() => {
              setDark(!dark);
              setCommand(false);
            }}
          >
            Toggle theme
          </Button>
        </DialogContent>
      </Dialog>
    </UserContext.Provider>
  );
}
export function Login() {
  const [error, setError] = useState("");
  const [accessCode, setAccessCode] = useState("");
  return (
    <main className="login-screen">
      <div className="login-panel">
        <span className="brand-mark">p</span>
        <span className="eyebrow">PILOTREE OPERATIONS</span>
        <h1>
          A clear next step
          <br />
          for every enquiry.
        </h1>
        <p>
          Sign in to review enquiries, work with AI, and keep every outbound
          action in your hands.
        </p>
        {!auth && !localDevelopmentAuth && !demoAuth && (
          <p className="error-note">
            Configure VITE_OIDC_AUTHORITY and VITE_OIDC_CLIENT_ID to enable your
            organisation's sign-in.
          </p>
        )}
        {error && <p role="alert">{error}</p>}
        {demoAuth && (
          <Input
            type="password"
            aria-label="Demo access code"
            placeholder="Demo access code"
            value={accessCode}
            onChange={(event) => setAccessCode(event.target.value)}
          />
        )}
        <Button
          disabled={(!auth && !localDevelopmentAuth && !demoAuth) || (demoAuth && !accessCode)}
          onClick={() =>
            (localDevelopmentAuth || demoAuth ? localLogin(accessCode) : login())?.catch((e) =>
              setError(e.message),
            )
          }
        >
          {localDevelopmentAuth || demoAuth
            ? demoAuth ? "Enter demo workspace" : "Enter local workspace"
            : "Sign in with your organisation"}
        </Button>
        <small>Access is limited to authorised team members.</small>
      </div>
    </main>
  );
}
export function AuthCallback() {
  const nav = useNavigate();
  const [error, setError] = useState("");
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    auth
      ?.signinCallback()
      .then((user) => {
        if (!user || window.self !== window.top) return;
        const stored =
          (user.state as { returnTo?: string })?.returnTo ||
          sessionStorage.getItem("returnTo") ||
          "/enquiries";
        void nav({
          to:
            stored.startsWith("/") && !stored.startsWith("//")
              ? stored
              : "/enquiries",
        });
      })
      .catch((e) => setError(e.message));
  }, [nav]);
  return (
    <main className="session-screen">{error || "Completing sign-in…"}</main>
  );
}
export function EnquiriesPage({
  quarantine = false,
}: {
  quarantine?: boolean;
}) {
  const user = useUser(),
    params = useParams({ strict: false }) as { id?: string },
    search = useSearch({ strict: false }) as Record<string, string>;
  const nav = useNavigate(),
    [queryText, setQueryText] = useState(search.q || "");
  const filters = useMemo<Record<string, string>>(
    () => ({
      ...search,
      ...(quarantine ? { safety_verdict: "QUARANTINE" } : {}),
    }),
    [search, quarantine],
  );
  const list = useQuery({
    queryKey: ["enquiries", filters],
    queryFn: () => getEnquiries(filters),
    placeholderData: (previous) => previous,
    refetchInterval: 15000,
  });
  const connected = useEvents(
    (list.data?.items ?? [])
      .map((e) => `enquiry:${e.id}`)
      .concat(params.id ? [`enquiry:${params.id}`] : []),
  );
  const update = (key: string, value: string) => {
    const next = { ...search };
    delete next.cursor;
    if (value) next[key] = value;
    else delete next[key];
    void nav({ to: quarantine ? "/quarantine" : "/enquiries", search: next });
  };
  useEffect(() => {
    setQueryText(search.q || "");
  }, [search.q]);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (queryText !== (search.q || "")) update("q", queryText);
    }, 300);
    return () => clearTimeout(timer);
  }, [queryText]);
  const columns = useMemo<ColumnDef<Enquiry>[]>(
    () => [{ accessorKey: "company" }, { accessorKey: "message" }],
    [],
  );
  const table = useReactTable({
    data: list.data?.items ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  const rows = table.getRowModel().rows,
    scroll = useRef<HTMLDivElement>(null);
  const virtual = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scroll.current,
    estimateSize: () => 128,
    overscan: 6,
  });
  const choose = (id: string) =>
    void nav({ to: "/enquiries/$id", params: { id }, search });
  return (
    <main className={`enquiries-page ${params.id ? "has-detail" : ""}`}>
      <div className="page-heading">
        <div>
          <div className="eyebrow">YOUR DAILY WORKSPACE</div>
          <h1>
            {quarantine ? "Quarantine" : "Enquiries"}{" "}
            <span>{list.data?.items.length ?? "–"}</span>
          </h1>
          <p>
            {quarantine
              ? "Enquiries that need a closer look."
              : "Read the context. Review the analysis. Choose the next step."}
          </p>
        </div>
        <div className="page-heading-actions">
          <span className={`live-status ${connected ? "connected" : ""}`}>
            <span />
            {connected ? "Live updates" : "Polling for updates"}
          </span>
          {user.role !== "viewer" && <NewEnquiry />}
        </div>
      </div>
      <div className="filter-bar">
        <div className="search-input">
          <MagnifyingGlassIcon />
          <Input
            aria-label="Search enquiries"
            placeholder="Search enquiries…"
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
          />
        </div>
        {[
          {
            key: "status",
            title: "Status",
            values: ["new", "open", "resolved"],
          },
          { key: "priority", title: "Priority", values: [...priorities] },
          { key: "category", title: "Category", values: [...categories] },
          {
            key: "safety_verdict",
            title: "Safety",
            values: Object.keys(copy.safety),
          },
        ].map((filter) => (
          <select
            key={filter.key}
            aria-label={filter.title}
            value={filters[filter.key] || ""}
            onChange={(e) => update(filter.key, e.target.value)}
            disabled={quarantine && filter.key === "safety_verdict"}
          >
            <option value="">{filter.title}: All</option>
            {filter.values.map((value) => (
              <option key={value} value={value}>
                {label(value)}
              </option>
            ))}
          </select>
        ))}
        <select
          aria-label="Sort"
          value={search.sort || "received_at"}
          onChange={(e) => update("sort", e.target.value)}
        >
          <option value="received_at">Newest first</option>
          <option value="company">Company A–Z</option>
        </select>
      </div>
      <div className="master-detail">
        <section className="enquiry-list" aria-label="Enquiry list">
          <div className="list-heading">
            <span>{rows.length} ENQUIRIES</span>
            <span>RECEIVED</span>
          </div>
          <div className="list-scroll" ref={scroll}>
            {list.isPending ? (
              <div className="skeleton-list">
                {[1, 2, 3, 4].map((i) => (
                  <Skeleton key={i} className="h-24 mb-3" />
                ))}
              </div>
            ) : list.isError ? (
              <div className="empty-state" role="alert">
                <h3>Could not load enquiries</h3>
                <p>{list.error.message}</p>
                <Button onClick={() => list.refetch()}>Try again</Button>
              </div>
            ) : rows.length === 0 ? (
              <div className="empty-state">
                <EnvelopeClosedIcon />
                <h3>{copy.noEnquiries}</h3>
                <p>Try a different search or clear your filters.</p>
                <Button
                  variant="outline"
                  onClick={() =>
                    nav({
                      to: quarantine ? "/quarantine" : "/enquiries",
                      search: {},
                    })
                  }
                >
                  {copy.clear}
                </Button>
              </div>
            ) : (
              <div
                style={{ height: virtual.getTotalSize(), position: "relative" }}
              >
                {virtual.getVirtualItems().map((item) => {
                  const enquiry = rows[item.index].original;
                  return (
                    <button
                      key={enquiry.id}
                      className={`enquiry-row ${params.id === enquiry.id ? "selected" : ""}`}
                      onClick={() => choose(enquiry.id)}
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "100%",
                        height: item.size,
                        transform: `translateY(${item.start}px)`,
                      }}
                      aria-label={`Open ${enquiry.company}`}
                    >
                      <div className="row-top">
                        <strong>{enquiry.company}</strong>
                        <time>
                          {new Date(enquiry.received_at).toLocaleDateString(
                            undefined,
                            { month: "short", day: "numeric" },
                          )}
                        </time>
                      </div>
                      <p>
                        {String(
                          enquiry.analysis_result?.summary || enquiry.message,
                        )}
                      </p>
                      <div className="row-bottom">
                        <span
                          className={`priority priority-${enquiry.analysis_result?.priority || "none"}`}
                        >
                          {label(
                            String(
                              enquiry.analysis_result?.priority ||
                                "Not analysed",
                            ),
                          )}
                        </span>
                        <span>{label(enquiry.analysis_status || "new")}</span>
                        {enquiry.safety_decision &&
                          enquiry.safety_decision !== "ALLOW" && (
                            <span className="row-warning">
                              <ExclamationTriangleIcon />
                              {enquiry.safety_decision === "ALLOW_WITH_WARNING"
                                ? "Warning"
                                : label(enquiry.safety_decision)}
                            </span>
                          )}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <footer className="list-footer">
            <span>
              {list.isFetching && !list.isPending
                ? "Refreshing…"
                : "All times in your timezone"}
            </span>
            {list.data?.next_cursor && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  nav({
                    to: quarantine ? "/quarantine" : "/enquiries",
                    search: { ...search, cursor: list.data!.next_cursor! },
                  })
                }
              >
                Next page <ChevronRightIcon />
              </Button>
            )}
            {search.cursor && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => update("cursor", "")}
              >
                First page
              </Button>
            )}
          </footer>
        </section>
        {params.id ? (
          <DetailPanel
            key={params.id}
            id={params.id}
            user={user}
            onBack={() => nav({ to: "/enquiries", search })}
          />
        ) : (
          <section className="detail-placeholder">
            <div className="placeholder-art">
              <EnvelopeClosedIcon />
              <span>
                <CheckIconMini />
              </span>
            </div>
            <h2>{copy.select}</h2>
            <p>{copy.selectBody}</p>
            <div className="workflow-key">
              <span>01 Read</span>
              <span>02 Review</span>
              <span>03 Send</span>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
function CheckIconMini() {
  return <span>✓</span>;
}
export function OutboxPage() {
  const [id, setId] = useState(""),
    [selected, setSelected] = useState("");
  const query = useQuery({
    queryKey: ["actions", selected],
    queryFn: () =>
      request<
        Array<{
          id: string;
          destination: string;
          status: string;
          attempts: number;
        }>
      >(`/actions?enquiry_id=${encodeURIComponent(selected)}`),
    enabled: !!selected,
  });
  return (
    <main className="utility-page">
      <h1>Actions / Outbox</h1>
      <p>
        Inspect delivery attempts for an enquiry. Open its detail to retry or
        resend.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setSelected(id);
        }}
      >
        <Input
          aria-label="Enquiry ID"
          placeholder="Enquiry ID"
          value={id}
          onChange={(e) => setId(e.target.value)}
        />
        <Button>Find actions</Button>
      </form>
      {query.isError && <p role="alert">{query.error.message}</p>}
      {query.data?.length === 0 && <p>No outbound actions for this enquiry.</p>}
      {query.data?.map((action) => (
        <div className="outbox-row" key={action.id}>
          <strong>{label(action.destination)}</strong>
          <span className={`status-badge status-${action.status}`}>
            {label(action.status)}
          </span>
          <span>{action.attempts} attempts</span>
        </div>
      ))}
      {selected && (
        <Link to="/enquiries/$id" params={{ id: selected }}>
          Open enquiry
        </Link>
      )}
    </main>
  );
}
export function SettingsPage() {
  const user = useUser();
  const queryClient = useQueryClient();
  const [slackWebhook, setSlackWebhook] = useState("");
  const [slackChannel, setSlackChannel] = useState("#triage");
  const [linearKey, setLinearKey] = useState("");
  const [linearTeam, setLinearTeam] = useState("");
  const [settingsNotice, setSettingsNotice] = useState("");
  const query = useQuery({
    queryKey: ["admin-tools"],
    queryFn: () =>
      request<
        Array<{
          key: string;
          enabled: boolean;
          configured: boolean;
          config: Record<string, string>;
        }>
      >("/admin/tools"),
    enabled: user.role === "admin",
  });
  const save = useMutation({
    mutationFn: ({ key, config }: { key: string; config: object }) =>
      request(`/admin/tools/${key}`, "PUT", { enabled: true, config }),
    onSuccess: async () => {
      setSlackWebhook("");
      setLinearKey("");
      setSettingsNotice("Destination saved and enabled.");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["admin-tools"] }),
        queryClient.invalidateQueries({ queryKey: ["tools"] }),
        queryClient.invalidateQueries({ queryKey: ["action-proposals"] }),
      ]);
    },
  });
  const configured = (key: string) => query.data?.find((item) => item.key === key);
  useEffect(() => {
    const slack = query.data?.find((item) => item.key === "slack");
    const linear = query.data?.find((item) => item.key === "linear");
    if (slack?.config.channel) setSlackChannel(slack.config.channel);
    if (linear?.config.team_id) setLinearTeam(linear.config.team_id);
  }, [query.data]);
  return (
    <main className="utility-page">
      <h1>Destination settings</h1>
      {user.role !== "admin" ? (
        <p>Administrator access required.</p>
      ) : (
        <>
          <p>Configure where approved enquiries can be sent. Credentials are write-only.</p>
          {settingsNotice && <p role="status">{settingsNotice}</p>}
          {save.isError && <p role="alert">{save.error.message}</p>}
          <div className="destination-settings-grid">
            <form
              className="destination-setting-card"
              onSubmit={(event) => {
                event.preventDefault();
                save.mutate({
                  key: "slack",
                  config: { webhook_url: slackWebhook, channel: slackChannel },
                });
              }}
            >
              <div>
                <h2>Slack</h2>
                <span className={`configuration-badge ${configured("slack")?.configured ? "is-configured" : ""}`}>
                  {configured("slack")?.configured
                    ? "Configured"
                    : "Not configured"}
                </span>
              </div>
              <label>
                {configured("slack")?.configured ? "Replace webhook URL (optional)" : "Incoming webhook URL"}
                <Input
                  type="password"
                  value={slackWebhook}
                  onChange={(event) => setSlackWebhook(event.target.value)}
                  placeholder={configured("slack")?.configured ? "Leave blank to keep existing" : "https://hooks.slack.com/services/..."}
                  required={!configured("slack")?.configured}
                />
              </label>
              <label>
                Channel label
                <Input value={slackChannel} onChange={(event) => setSlackChannel(event.target.value)} />
              </label>
              <Button disabled={save.isPending}>Save Slack</Button>
            </form>
            <form
              className="destination-setting-card"
              onSubmit={(event) => {
                event.preventDefault();
                save.mutate({
                  key: "linear",
                  config: { api_key: linearKey, team_id: linearTeam },
                });
              }}
            >
              <div>
                <h2>Linear</h2>
                <span className={`configuration-badge ${configured("linear")?.configured ? "is-configured" : ""}`}>
                  {configured("linear")?.configured
                    ? "Configured"
                    : "Not configured"}
                </span>
              </div>
              <label>
                {configured("linear")?.configured ? "Replace API key (optional)" : "API key"}
                <Input
                  type="password"
                  value={linearKey}
                  onChange={(event) => setLinearKey(event.target.value)}
                  placeholder={configured("linear")?.configured ? "Leave blank to keep existing" : "lin_api_..."}
                  required={!configured("linear")?.configured}
                />
              </label>
              <label>
                Team ID
                <Input
                  value={linearTeam}
                  onChange={(event) => setLinearTeam(event.target.value)}
                  placeholder={configured("linear")?.config.team_id || "Team UUID"}
                  required={!configured("linear")?.config.team_id}
                />
              </label>
              <Button disabled={save.isPending}>Save Linear</Button>
            </form>
          </div>
        </>
      )}
    </main>
  );
}
