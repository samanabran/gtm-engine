"use client";

import { useParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { notFound } from "next/navigation";
import { Loader2, Sparkles, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import { fetchJson, submitJson } from "@/lib/api";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

type SequenceItem = {
  id: string;
  lead_id: string | null;
  subject: string;
  body: string;
  status: string;
  confidence: number;
};

type Campaign = {
  id: string;
  name: string;
  tone: string;
  product_value_prop?: string;
  brand_voice?: string;
  target_icp?: Record<string, unknown>;
  active: boolean;
  sequences: SequenceItem[];
  created_at: string;
  updated_at: string;
};

type LeadRaw = {
  id: string;
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  title: string | null;
  email: string;
  icp_score: number | null;
  status: string;
};

function leadDisplayName(lead: LeadRaw) {
  const parts = [lead.first_name, lead.last_name].filter(Boolean);
  return parts.length > 0 ? parts.join(" ") : lead.email;
}

export default function CampaignDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();

  const { data: campaign, isLoading: campaignLoading, isError } = useQuery({
    queryKey: ["campaign", id],
    queryFn: () => fetchJson<Campaign | null>(`/campaigns/${id}`, null),
    enabled: Boolean(id),
  });

  const { data: leadsData, isLoading: leadsLoading } = useQuery({
    queryKey: ["leads"],
    queryFn: () =>
      fetchJson<{ items: LeadRaw[]; total: number }>("/leads?page_size=200", {
        items: [],
        total: 0,
      }),
    enabled: Boolean(campaign),
  });

  const generateMutation = useMutation({
    mutationFn: async (leadId: string) => {
      const result = await submitJson(`/campaigns/${id}/generate/${leadId}`, {});
      if (!result.ok) throw new Error("Generation failed");
      return result.data;
    },
    onSuccess: () => {
      toast.success("Sequence generated successfully");
      queryClient.invalidateQueries({ queryKey: ["campaign", id] });
    },
    onError: () => {
      toast.error("Failed to generate sequence. Please try again.");
    },
  });

  if (campaignLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-6 w-64" />
        <Skeleton className="h-64 w-full rounded-2xl" />
      </div>
    );
  }

  if (isError || campaign === null) {
    notFound();
  }

  if (!campaign) return null;

  const threshold = (campaign.target_icp?.threshold as number) ?? 0;
  const generatedLeadIds = new Set(
    campaign.sequences.map((s) => s.lead_id).filter(Boolean)
  );
  const allLeads = leadsData?.items ?? [];
  const eligibleLeads =
    threshold > 0
      ? allLeads.filter((l) => (l.icp_score ?? 0) >= threshold)
      : allLeads;

  return (
    <div className="space-y-4">
      <Breadcrumbs
        items={[
          { label: "Dashboard", href: "/" },
          { label: "Campaigns", href: "/campaigns" },
          { label: campaign.name },
        ]}
      />
      <div className="grid gap-4 xl:grid-cols-[1fr_0.8fr]">
        <div className="space-y-4">
          <Card>
            <CardContent className="space-y-4 p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h1 className="font-display text-3xl font-semibold">{campaign.name}</h1>
                  {campaign.product_value_prop && (
                    <p className="text-sm text-slate-500">{campaign.product_value_prop}</p>
                  )}
                </div>
                <Badge tone={campaign.active ? "success" : "warning"}>
                  {campaign.active ? "active" : "inactive"}
                </Badge>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-2xl border border-border bg-white p-4">
                  <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Tone</p>
                  <p className="mt-1 font-semibold capitalize">{campaign.tone}</p>
                </div>
                {threshold > 0 && (
                  <div className="rounded-2xl border border-border bg-white p-4">
                    <p className="text-xs uppercase tracking-[0.18em] text-slate-500">ICP threshold</p>
                    <p className="mt-1 font-semibold">{threshold}</p>
                  </div>
                )}
                {campaign.brand_voice && (
                  <div className="rounded-2xl border border-border bg-white p-4 md:col-span-2">
                    <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Brand voice</p>
                    <p className="mt-1 text-sm">{campaign.brand_voice}</p>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3 p-5">
              <p className="text-xs uppercase tracking-[0.18em] text-slate-500">
                Eligible leads
                {eligibleLeads.length > 0 && (
                  <span className="ml-2 normal-case text-slate-400">({eligibleLeads.length})</span>
                )}
              </p>
              {leadsLoading ? (
                <div className="space-y-2">
                  <Skeleton className="h-14 w-full rounded-2xl" />
                  <Skeleton className="h-14 w-full rounded-2xl" />
                </div>
              ) : eligibleLeads.length === 0 ? (
                <p className="text-sm text-slate-400">
                  {threshold > 0
                    ? `No leads meet the ICP threshold of ${threshold}. Lower the threshold or score more leads first.`
                    : "No leads found. Import leads first, then score them with AI."}
                </p>
              ) : (
                <div className="space-y-2">
                  {eligibleLeads.map((lead) => {
                    const hasSequence = generatedLeadIds.has(lead.id);
                    const isPending =
                      generateMutation.isPending &&
                      generateMutation.variables === lead.id;
                    return (
                      <div
                        key={lead.id}
                        className="flex items-center justify-between rounded-2xl border border-border bg-white p-4"
                      >
                        <div>
                          <p className="text-sm font-semibold">{leadDisplayName(lead)}</p>
                          <p className="text-xs text-slate-500">
                            {lead.company_name ?? lead.email}
                            {lead.icp_score != null && (
                              <span className="ml-2">
                                · ICP {Math.round(lead.icp_score * 100)}%
                              </span>
                            )}
                          </p>
                        </div>
                        {hasSequence ? (
                          <div className="flex items-center gap-1.5 text-xs font-medium text-emerald-600">
                            <CheckCircle2 className="h-4 w-4" />
                            Generated
                          </div>
                        ) : (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={isPending || generateMutation.isPending}
                            onClick={() => generateMutation.mutate(lead.id)}
                          >
                            {isPending ? (
                              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                            )}
                            {isPending ? "Generating..." : "Generate sequence"}
                          </Button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardContent className="space-y-3 p-5">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Generated sequences</p>
            {campaign.sequences.length === 0 ? (
              <>
                <p className="font-display text-2xl font-semibold">
                  The agent drafts when ICP fit meets the threshold.
                </p>
                <p className="text-sm text-slate-600">
                  Click the Generate sequence button next to any eligible lead to trigger AI outbound drafting.
                </p>
              </>
            ) : (
              <div className="space-y-3">
                {campaign.sequences.map((seq) => (
                  <div key={seq.id} className="rounded-2xl border border-border bg-white p-4">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-semibold">{seq.subject}</p>
                      <Badge tone={seq.status === "approved" ? "success" : "warning"}>
                        {seq.status.replace(/_/g, " ")}
                      </Badge>
                    </div>
                    <p className="mt-1.5 text-xs text-slate-500 line-clamp-3">{seq.body}</p>
                    {seq.confidence > 0 && (
                      <p className="mt-1 text-xs text-slate-400">
                        Confidence: {Math.round(seq.confidence * 100)}%
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
