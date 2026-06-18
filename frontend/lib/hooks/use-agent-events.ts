"use client";

import { useEffect, useState } from "react";
import { useAppStore } from "@/lib/store";

export type AgentEvent = {
  type: "agent_started" | "agent_progress" | "agent_completed" | "agent_error";
  message: string;
  agentName: string;
  progress?: number;
};

export function useAgentEvents() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const accessToken = useAppStore((state) => state.accessToken);

  useEffect(() => {
    if (!accessToken) return;

    const url = `/api/events/agent-status?token=${encodeURIComponent(accessToken)}`;
    const source = new EventSource(url);

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as AgentEvent;
        setEvents((current) => [payload, ...current].slice(0, 12));
      } catch {
        // Ignore non-JSON heartbeat messages.
      }
    };

    return () => source.close();
  }, [accessToken]);

  return { events, connected };
}
