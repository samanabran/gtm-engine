"use client";

import { useQuery } from "@tanstack/react-query";
import { notFound } from "next/navigation";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { fetchJson } from "@/lib/api";
import { backendCompanyToFrontend } from "@/lib/transforms";
import { formatDate } from "@/lib/utils";

export default function CompanyDetailPage({ params }: { params: { id: string } }) {
  const { data: company, isLoading, isError } = useQuery({
    queryKey: ["company", params.id],
    queryFn: async () => {
      const raw = await fetchJson<any>(`/companies/${params.id}`, null);
      if (!raw) return null;
      return backendCompanyToFrontend(raw);
    },
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Breadcrumbs items={[{ label: "Dashboard", href: "/" }, { label: "Companies", href: "/companies" }, { label: "Loading..." }]} />
        <div className="h-48 animate-pulse rounded-2xl bg-slate-100" />
      </div>
    );
  }

  if (isError || !company) {
    return (
      <div className="space-y-4">
        <Breadcrumbs items={[{ label: "Dashboard", href: "/" }, { label: "Companies", href: "/companies" }, { label: "Not found" }]} />
        <Card>
          <CardContent className="p-5">
            <p className="text-sm text-slate-500">Company not found.</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Breadcrumbs
        items={[
          { label: "Dashboard", href: "/" },
          { label: "Companies", href: "/companies" },
          { label: company.name },
        ]}
      />
      <div className="grid gap-4 xl:grid-cols-[1fr_0.8fr]">
        <Card>
          <CardContent className="space-y-4 p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h1 className="font-display text-3xl font-semibold">{company.name}</h1>
                <p className="text-sm text-slate-500">{company.domain}</p>
              </div>
              <Badge tone="success">{Math.round(company.healthScore * 100)}% health</Badge>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-2xl border border-border bg-white p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Industry</p>
                <p className="mt-1 font-semibold">{company.industry || "—"}</p>
              </div>
              <div className="rounded-2xl border border-border bg-white p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Revenue</p>
                <p className="mt-1 font-semibold">{company.revenue || "—"}</p>
              </div>
              <div className="rounded-2xl border border-border bg-white p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Employees</p>
                <p className="mt-1 font-semibold">{company.employees || "—"}</p>
              </div>
              <div className="rounded-2xl border border-border bg-white p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Last sync</p>
                <p className="mt-1 font-semibold">{company.lastSyncAt ? formatDate(company.lastSyncAt) : "—"}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="space-y-3 p-5">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Account status</p>
            <p className="font-display text-2xl font-semibold">
              {company.healthScore >= 0.7 ? "A healthy account is ready for multi-threaded outreach." : "This account may need further qualification."}
            </p>
            <p className="text-sm text-slate-600">Stage: <span className="font-medium capitalize">{company.stage}</span></p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
