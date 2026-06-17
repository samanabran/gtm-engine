"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { submitJson } from "@/lib/api";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { EmptyState } from "@/components/common/empty-state";
import { ApprovalList } from "@/components/approvals/approval-list";
import { ApprovalReview } from "@/components/approvals/approval-review";
import { ApprovalStatsBar } from "@/components/approvals/approval-stats-bar";
import { useApprovals } from "@/lib/hooks/use-approvals";

export default function ApprovalsPage() {
  const approvals = useApprovals();
  const queryClient = useQueryClient();

  const approved = approvals.data?.filter((a) => a.status === "approved").length ?? 0;
  const rejected = approvals.data?.filter((a) => a.status === "rejected").length ?? 0;
  const pending = approvals.data?.filter((a) => a.status === "pending_approval").length ?? 0;

  function moveToNext() {
    const items = approvals.data ?? [];
    const currentId = approvals.currentItem?.id;
    const idx = items.findIndex((a) => a.id === currentId);
    const next = items.find((a, i) => i > idx && a.status === "pending_approval");
    if (next) approvals.selectApproval(next.id);
  }

  const approveMutation = useMutation({
    mutationFn: async ({ approvalId, body }: { approvalId: string; body?: string }) => {
      const result = await submitJson(`/approvals/${approvalId}/approve`, { note: null, body: body ?? null });
      if (!result.ok) throw new Error("Approve failed");
      return result.data;
    },
    onSuccess: () => {
      toast.success("Sequence approved");
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      moveToNext();
    },
    onError: () => toast.error("Failed to approve. Please try again."),
  });

  const rejectMutation = useMutation({
    mutationFn: async ({ approvalId, note }: { approvalId: string; note: string }) => {
      const result = await submitJson(`/approvals/${approvalId}/reject`, { note: note || null });
      if (!result.ok) throw new Error("Reject failed");
      return result.data;
    },
    onSuccess: () => {
      toast.success("Sequence rejected");
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      moveToNext();
    },
    onError: () => toast.error("Failed to reject. Please try again."),
  });

  const currentId = approvals.currentItem?.id ?? null;

  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "Dashboard", href: "/" }, { label: "Approvals" }]} />
      <div className="space-y-3">
        <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Human-in-the-loop</p>
        <h1 className="font-display text-3xl font-semibold">Approve, edit, reject, or skip drafts</h1>
      </div>
      <ApprovalStatsBar pending={pending} approved={approved} rejected={rejected} />
      {approvals.data?.length ? (
        <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
          <ApprovalList
            items={approvals.data}
            selectedId={currentId}
            onSelect={approvals.selectApproval}
          />
          <ApprovalReview
            item={approvals.currentItem}
            onApprove={(_variantId, body) => {
              if (currentId) approveMutation.mutate({ approvalId: currentId, body });
            }}
            onReject={(reason) => {
              if (currentId) rejectMutation.mutate({ approvalId: currentId, note: reason });
            }}
            onSkip={moveToNext}
          />
        </div>
      ) : (
        <EmptyState
          title="No approvals queued"
          description="Once the outbound agent drafts a review-ready sequence, it will appear here for human approval."
        />
      )}
    </div>
  );
}
