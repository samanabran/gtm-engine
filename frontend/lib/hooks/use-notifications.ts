import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { AppNotification } from "@/lib/types";

export function useNotifications() {
  return useQuery({
    queryKey: ["notifications"],
    queryFn: async (): Promise<AppNotification[]> => {
      const response = await apiFetch("/notifications?limit=10");
      if (!response.ok) {
        throw new Error(`Failed to load notifications (${response.status})`);
      }
      return response.json();
    },
    refetchInterval: 30000,
  });
}
