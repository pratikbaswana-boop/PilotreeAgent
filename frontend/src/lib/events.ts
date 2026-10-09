import { useEffect, useState } from "react";
import { fetchEventSource } from "@microsoft/fetch-event-source";
import { useQueryClient } from "@tanstack/react-query";
import { accessToken } from "./auth";
import { API } from "./api";
export function useEvents(topics: string[]) {
  const query = useQueryClient();
  const [connected, setConnected] = useState(false);
  const key = [...new Set(topics)].sort().join(",");
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    let lastId = "",
      retry = 0,
      timer: ReturnType<typeof setTimeout>;
    async function connect() {
      while (!controller.signal.aborted) {
        try {
          const token = await accessToken(retry > 0);
          await fetchEventSource(
            `${API}/sse?topics=${encodeURIComponent(key)}`,
            {
              signal: controller.signal,
              openWhenHidden: true,
              headers: {
                ...(token ? { Authorization: `Bearer ${token}` } : {}),
                ...(lastId ? { "Last-Event-ID": lastId } : {}),
              },
              async onopen(response) {
                if (
                  !response.ok ||
                  !response.headers
                    .get("content-type")
                    ?.includes("text/event-stream")
                )
                  throw new Error("Live updates unavailable");
                setConnected(true);
                retry = 0;
                await query.invalidateQueries({ queryKey: ["enquiries"] });
              },
              onmessage(event) {
                if (event.id) lastId = event.id;
                if (event.event === "ping" || event.event === "connected")
                  return;
                void query.invalidateQueries({ queryKey: ["enquiries"] });
                void query.invalidateQueries({ queryKey: ["enquiry"] });
                void query.invalidateQueries({ queryKey: ["analysis"] });
                void query.invalidateQueries({ queryKey: ["actions"] });
              },
              onclose() {
                throw new Error("Reconnect");
              },
              onerror(error) {
                throw error;
              },
            },
          );
        } catch {
          if (controller.signal.aborted) return;
          setConnected(false);
          await new Promise<void>((resolve) => {
            timer = setTimeout(resolve, Math.min(30000, 1000 * 2 ** retry++));
            controller.signal.addEventListener(
              "abort",
              () => {
                clearTimeout(timer);
                resolve();
              },
              { once: true },
            );
          });
        }
      }
    }
    void connect();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [key, query]);
  return connected;
}
