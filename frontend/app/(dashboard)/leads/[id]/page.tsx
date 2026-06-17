"use client";

import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { notFound } from "next/navigation";
import { Sparkles, Loader2 } from "lucide-react";
import { fetchJson, submitJson } from "@/lib/api";
import { backendLeadToFrontend } from "@/lib/transforms";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { LeadDetailCard } from "@/components/leads/lead-detail-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export default function LeadDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();

  const scoreMutation = useMutation({
    mutationFn: async () => {
      const result = await submitJson("/agents/icp/score", { payload: { lead_id: id } });
      if (!result.ok) throw new Error("Scoring failed");
      return result.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["lead", id] });
      queryClient.invalidateQueries({ queryKey: ["leads"] });
    },
  });

  const { data: lead, isLoading, isError } = useQuery({
    queryKey: ["lead", id],
    queryFn: async () => {
      const raw = await fetchJson<Record<string, unknown> | null>(`/leads/${id}`, null);
      if (!raw) return null;
      return backendLeadToFrontend(raw as Parameters<typeof backendLeadToFrontend>[0]);
    },
    enabled: Boolean(id),
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-6 w-64" />
        <Skeleton className="h-64 w-full rounded-2xl" />
      </div>
    );
  }

  if (isError || lead === null) {
    notFound();
  }

  if (!lead) return null;

  return (
    <div className="space-y-4">
      <Breadcrumbs
        items={[
          { label: "Dashboard", href: "/" },
          { label: "Leads", href: "/leads" },
          { label: lead.name },
        ]}
      />
      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <LeadDetailCard lead={lead} />
        <Card>
          <CardContent className="space-y-4 p-5">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Actions</p>
            <Button
              onClick={() => scoreMutation.mutate()}
              disabled={scoreMutation.isPending}
              className="w-full"
            >
              {scoreMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="mr-2 h-4 w-4" />
              )}
              {scoreMutation.isPending ? "Scoring..." : "Score with AI"}
            </Button>
            <p className="text-xs text-slate-400">
              Runs ICP scoring via the AI agent pipeline to evaluate lead fit.
            </p>
            <div className="space-y-2 pt-2">
              <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Timeline</p>
              {[
                "Enriched from Apollo and Hunter",
                "ICP score crossed threshold",
                "Queued for review",
              ].map((entry) => (
                <div
                  key={entry}
                  className="rounded-2xl border border-border bg-white p-4 text-sm"
                >
                  {entry}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}