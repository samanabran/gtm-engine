"use client";

import { useEffect, useRef, useState } from "react";
import { Bell } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useNotifications } from "@/lib/hooks/use-notifications";

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const { data, isLoading, isError } = useNotifications();
  const notifications = data ?? [];

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="relative" ref={containerRef}>
      <Button variant="outline" size="sm" onClick={() => setOpen((value) => !value)}>
        <Bell className="h-4 w-4" />
        {notifications.length > 0 && (
          <Badge tone="primary" className="ml-1 border-0 px-1.5 py-0 text-[10px]">
            {notifications.length}
          </Badge>
        )}
      </Button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-80 rounded-2xl border border-border bg-white p-2 shadow-soft">
          <p className="px-2 py-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Recent activity
          </p>
          {isLoading && <p className="px-2 py-3 text-sm text-slate-500">Loading...</p>}
          {isError && (
            <p className="px-2 py-3 text-sm text-rose-500">Couldn&apos;t load notifications.</p>
          )}
          {!isLoading && !isError && notifications.length === 0 && (
            <p className="px-2 py-3 text-sm text-slate-500">No recent activity.</p>
          )}
          {!isLoading && !isError && notifications.length > 0 && (
            <ul className="max-h-80 space-y-1 overflow-y-auto">
              {notifications.map((item) => (
                <li key={item.id} className="rounded-xl px-2 py-2 text-sm hover:bg-slate-50">
                  <p className="font-medium text-slate-800">{item.message}</p>
                  <p className="text-xs text-slate-400">
                    {item.agent_name ?? "System"}
                    {item.created_at ? ` · ${new Date(item.created_at).toLocaleString()}` : ""}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
